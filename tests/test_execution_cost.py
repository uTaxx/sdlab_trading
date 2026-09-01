"""결정가 대비 체결가 측정 검증.

이 숫자로 백테스트의 슬리피지 값을 정하게 되므로, 부호가 뒤집히거나
가짜 표본이 섞이면 전략 판단이 통째로 틀어진다."""

from datetime import UTC, datetime, timedelta

import pytest

from muwon.analysis.execution_cost import CostSample, collect, format_report
from muwon.db.models import OrderRow
from muwon.db.session import make_session_factory


def make_factory(tmp_path, rows):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'orders.db'}")
    with factory() as session:
        session.add_all(rows)
        session.commit()
    return factory


def order(side, reference, fill, confirmed=True, symbol="005930", minutes=0):
    return OrderRow(
        symbol=symbol,
        side=side,
        quantity=1,
        price=fill,
        is_paper=True,
        kis_order_id=f"X{minutes}",
        reason="테스트",
        reference_price=reference,
        fill_confirmed=confirmed,
        created_at=datetime(2026, 8, 18, tzinfo=UTC) + timedelta(minutes=minutes),
    )


def test_unfavourable_fills_are_positive_for_both_sides():
    """부호를 맞추지 않으면 매수·매도가 상쇄돼 '비용 0'처럼 보인다.
    비싸게 사도 손해, 싸게 팔아도 손해다."""
    bought_higher = CostSample("005930", "BUY", 10_000, 10_100)
    sold_lower = CostSample("005930", "SELL", 10_000, 9_900)

    assert bought_higher.cost_pct == pytest.approx(1.0)
    assert sold_lower.cost_pct == pytest.approx(1.0)


def test_favourable_fills_are_negative():
    """유리하게 체결되는 경우도 있다. 그걸 0으로 깎으면 값이 부풀려진다."""
    assert CostSample("005930", "BUY", 10_000, 9_900).cost_pct == pytest.approx(-1.0)


def test_unconfirmed_fills_are_excluded(tmp_path):
    """체결 조회가 실패하면 기준가를 그대로 기록한다. 그 행을 세면
    '차이 0'인 가짜 표본이 섞여 실제보다 작게 나온다."""
    factory = make_factory(
        tmp_path,
        [
            order("BUY", 10_000, 10_100, confirmed=True, minutes=1),
            order("BUY", 10_000, 10_000, confirmed=False, minutes=2),
            order("BUY", 10_000, 10_000, confirmed=None, minutes=3),
        ],
    )

    report = collect(factory)

    assert report.count == 1
    assert report.skipped_unconfirmed == 2
    assert report.median_pct == pytest.approx(1.0)


def test_rows_without_reference_price_are_excluded(tmp_path):
    """컬럼이 생기기 전에 쌓인 주문은 기준가가 없다."""
    factory = make_factory(tmp_path, [order("BUY", None, 10_100, minutes=1)])

    report = collect(factory)

    assert report.count == 0
    assert report.skipped_no_reference == 1


def test_empty_report_says_what_is_missing(tmp_path):
    """표본이 없을 때 0%를 답으로 내놓으면 '비용이 없다'로 읽힌다."""
    factory = make_factory(tmp_path, [])

    text = format_report(collect(factory))

    assert "아직 잴 수 있는 표본이 없습니다" in text
    assert "추정값으로만" in text


def test_small_sample_is_flagged(tmp_path):
    """표본 몇 건으로 백테스트 값을 정하면 안 된다."""
    factory = make_factory(
        tmp_path, [order("BUY", 10_000, 10_050, minutes=i) for i in range(3)]
    )

    text = format_report(collect(factory))

    assert "20건 미만" in text
