"""장중 손절 비교 검증.

이 계산이 낙관적으로 틀리면 "실시간으로 바꾸자"는 결론이 근거 없이 나온다."""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.analysis.intraday_stop import compare, format_comparison


@dataclass
class FakeTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    pnl_pct: float


def bars(start: date, rows):
    """rows = [(open, high, low, close), ...]"""
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(rows))],
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1000] * len(rows),
        }
    )


def test_a_dip_that_recovers_by_close_would_have_been_stopped_out():
    """장중에 -8%까지 빠졌다가 -1%로 마감한 매매. 지금 구조는 안 팔지만
    장중 스톱은 팔린다. 그리고 그건 **나빠지는** 쪽이다."""
    df = bars(date(2024, 1, 1), [(100, 100, 100, 100), (99, 100, 92, 99)])
    결과 = compare(
        [FakeTrade("A", date(2024, 1, 1), date(2024, 1, 2), 100.0, -1.0)],
        {"A": df},
        stop_loss_pct=-0.05,
    )
    assert 결과[0].장중발동
    assert 결과[0].장중손익 == pytest.approx(-5.0)
    assert 결과[0].차이 < 0, "회복한 매매를 팔았으니 나빠져야 한다"


def test_a_gap_down_fills_at_the_open_not_the_stop_price():
    """밤사이 -12% 갭이면 스톱은 손절선(-5%)이 아니라 시가에 발동한다.

    손절선에 팔렸다고 계산하면 스톱이 갭도 막아 주는 것처럼 보인다."""
    df = bars(date(2024, 1, 1), [(100, 100, 100, 100), (88, 90, 86, 89)])
    결과 = compare(
        [FakeTrade("A", date(2024, 1, 1), date(2024, 1, 2), 100.0, -11.0)],
        {"A": df},
        stop_loss_pct=-0.05,
    )
    assert 결과[0].장중손익 == pytest.approx(-12.0), "시가 -12%에 체결되어야 한다"


def test_a_trade_that_never_touches_the_stop_is_unchanged():
    df = bars(date(2024, 1, 1), [(100, 100, 100, 100), (101, 105, 99, 104)])
    결과 = compare(
        [FakeTrade("A", date(2024, 1, 1), date(2024, 1, 2), 100.0, 4.0)],
        {"A": df},
        stop_loss_pct=-0.05,
    )
    assert not 결과[0].장중발동
    assert 결과[0].차이 == pytest.approx(0.0)


def test_it_reports_how_many_days_earlier_the_exit_was():
    df = bars(
        date(2024, 1, 1),
        [(100, 100, 100, 100), (99, 100, 94, 96), (96, 97, 95, 96), (96, 97, 95, 96)],
    )
    결과 = compare(
        [FakeTrade("A", date(2024, 1, 1), date(2024, 1, 4), 100.0, -4.0)],
        {"A": df},
        stop_loss_pct=-0.05,
    )
    assert 결과[0].며칠빨리 == 2


def test_the_report_warns_that_the_estimate_is_optimistic():
    """이 경고가 빠지면 '이만큼 좋아진다'로 읽힌다."""
    df = bars(date(2024, 1, 1), [(100, 100, 100, 100), (99, 100, 92, 99)])
    글 = format_comparison(
        compare([FakeTrade("A", date(2024, 1, 1), date(2024, 1, 2), 100.0, -1.0)], {"A": df})
    )
    assert "낙관적" in 글
    assert "좋을 수는 없다" in 글


def test_empty_input_says_so():
    assert "완결된 매매가 없습니다" in format_comparison([])
