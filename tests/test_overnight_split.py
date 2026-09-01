"""번 돈이 밤사이에 났나 낮에 났나. 나누기 검증.

이 표 하나로 "실시간 매매 구조를 만들 것인가"를 판단할 참이다. 그래서
나누기가 조용히 틀리면 큰 결정을 틀린 근거로 내리게 된다."""

from datetime import date

import pandas as pd
import pytest

from muwon.analysis.overnight_split import Split, format_split
from muwon.analysis.overnight_split import split_overnight as split
from muwon.backtest.engine import ClosedTrade


def _bars(rows: list[tuple[int, float, float]]) -> pd.DataFrame:
    """(일, 시가, 종가)로 일봉을 만든다. 고가/저가는 여기서 안 쓴다."""
    return pd.DataFrame(
        [
            {
                "trade_date": date(2024, 1, day),
                "open": o,
                "high": max(o, c),
                "low": min(o, c),
                "close": c,
                "volume": 1000,
            }
            for day, o, c in rows
        ]
    )


def _trade(entry_day: int, exit_day: int) -> ClosedTrade:
    return ClosedTrade(
        symbol="A",
        entry_date=date(2024, 1, entry_day),
        exit_date=date(2024, 1, exit_day),
        entry_price=100.0,
        exit_price=100.0,
        quantity=1,
        pnl_pct=0.0,
        pnl_amount=0.0,
        exit_reason="테스트",
    )


def test_night_and_day_multiply_back_to_the_whole():
    """이 항등식이 이 분석의 전부다. 깨지면 두 조각을 따로 말할 근거가 없다."""
    df = _bars([(1, 99.0, 100.0), (2, 104.0, 102.0), (3, 105.0, 110.0)])
    (나눈것,) = split([_trade(1, 3)], {"A": df})

    밤 = 1 + 나눈것.오버나이트 / 100
    낮 = 1 + 나눈것.장중 / 100
    assert 밤 * 낮 == pytest.approx(110.0 / 100.0)
    assert 나눈것.전체 == pytest.approx(10.0)


def test_the_entry_day_intraday_move_is_not_ours():
    """종가에 샀으므로 산 날 낮에 오른 것은 우리 수익이 아니다.
    여기에 넣으면 없는 수익이 장중 몫으로 잡힌다."""
    # 산 날 낮에 +50% 폭등했지만 그건 우리가 사기 전 일이다.
    df = _bars([(1, 66.7, 100.0), (2, 100.0, 100.0)])
    (나눈것,) = split([_trade(1, 2)], {"A": df})
    assert 나눈것.장중 == pytest.approx(0.0)
    assert 나눈것.오버나이트 == pytest.approx(0.0)


def test_a_gain_made_only_overnight_shows_up_only_at_night():
    df = _bars([(1, 100.0, 100.0), (2, 110.0, 110.0), (3, 121.0, 121.0)])
    (나눈것,) = split([_trade(1, 3)], {"A": df})
    assert 나눈것.오버나이트 == pytest.approx(21.0)
    assert 나눈것.장중 == pytest.approx(0.0)


def test_a_gain_made_only_during_the_day_shows_up_only_in_the_day():
    df = _bars([(1, 100.0, 100.0), (2, 100.0, 110.0), (3, 110.0, 121.0)])
    (나눈것,) = split([_trade(1, 3)], {"A": df})
    assert 나눈것.오버나이트 == pytest.approx(0.0)
    assert 나눈것.장중 == pytest.approx(21.0)


def test_a_same_day_trade_has_nothing_to_split():
    """산 날과 판 날이 같으면 밤이 없다. 억지로 0을 넣으면 표본이 오염된다."""
    df = _bars([(1, 100.0, 100.0)])
    assert split([_trade(1, 1)], {"A": df}) == []


def test_a_zero_price_drops_the_trade_instead_of_poisoning_the_product():
    """0이 하나 섞이면 곱이 통째로 망가진다. 반쯤 계산된 숫자가 가장 위험하다."""
    df = _bars([(1, 100.0, 100.0), (2, 0.0, 110.0), (3, 110.0, 120.0)])
    assert split([_trade(1, 3)], {"A": df}) == []


def test_a_missing_symbol_is_skipped_not_crashed():
    assert split([_trade(1, 3)], {}) == []


def _만든것(밤: float, 낮: float) -> Split:
    return Split(symbol="A", 보유일=5, 오버나이트=밤, 장중=낮, 전체=밤 + 낮)


def test_the_report_refuses_to_split_a_share_when_the_signs_disagree():
    """부호가 갈리면 '몇 %가 밤에서 나왔나'라는 말 자체가 성립하지 않는다.
    100%를 넘는 기여도를 태연히 찍는 것이 이런 표의 흔한 거짓말이다."""
    글 = format_split([_만든것(10.0, -4.0)])
    assert "번 것은 전부 밤사이" in 글
    assert "%" in 글
    assert "기여도: 밤" not in 글


def test_the_report_splits_the_share_when_both_are_positive():
    글 = format_split([_만든것(6.0, 2.0)])
    assert "기여도: 밤 75% · 낮 25%" in 글


def test_the_report_says_so_when_there_is_nothing_to_share():
    글 = format_split([_만든것(-3.0, -2.0)])
    assert "나눌 것이 없습니다" in 글


def test_the_report_warns_that_this_is_a_diagnosis_not_a_strategy():
    """'밤에만 들고 있기'는 매일 왕복 매매다. 그 비용이 안 들어 있다는 말이
    빠지면, 이 표를 매매 지시로 읽게 된다."""
    글 = format_split([_만든것(6.0, 2.0)])
    assert "비용이 여기 하나도 안 들어 있습니다" in 글


def test_an_empty_result_says_so_instead_of_dividing_by_zero():
    assert "나눌 수 있는 매매가 없습니다" in format_split([])


def test_the_report_never_compounds_overlapping_trades():
    """매매를 곱해서 이어 붙이면 +550만% 같은 숫자가 나온다. 포트폴리오는
    여러 종목을 동시에 들고 있어서 매매들이 겹치기 때문이다. 실제로 한 번
    그렇게 찍었고, 그대로 뒀다면 표 전체를 못 믿게 됐다."""
    글 = format_split([_만든것(6.0, 2.0), _만든것(6.0, 2.0)])
    assert "이어붙임" not in 글
    assert "계좌 수익률이" in 글
    # 더하기라면 두 건의 밤 몫은 정확히 12%p다.
    assert "+12%p" in 글
