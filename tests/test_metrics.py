"""백테스트 지표 검증.

숫자가 조금 틀려도 눈에 안 띄기 때문에, 손으로 계산할 수 있는 값으로
확인한다. 특히 CAGR과 Profit Factor는 정의가 여러 가지라 어느 정의를
쓰는지가 테스트에 드러나야 한다."""

import math
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from muwon.backtest.engine import ClosedTrade
from muwon.backtest.metrics import (
    cagr_pct,
    compute_metrics,
    expectancy_pct,
    exposure_pct,
    profit_factor,
    sharpe,
    sortino,
    turnover,
)


def curve(values, positions=None, start=date(2024, 1, 2)):
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(len(values))],
            "equity": values,
            "cash": [0.0] * len(values),
            "positions": positions if positions is not None else [1] * len(values),
        }
    )


def trade(pnl_amount, pnl_pct=0.0, days=5, quantity=10, entry_price=100.0):
    entry = date(2024, 1, 2)
    return ClosedTrade(
        symbol="A",
        entry_date=entry,
        exit_date=entry + timedelta(days=days),
        entry_price=entry_price,
        exit_price=entry_price * (1 + pnl_pct / 100),
        quantity=quantity,
        pnl_pct=pnl_pct,
        pnl_amount=pnl_amount,
        exit_reason="테스트",
    )


# ── 기간 보정 ────────────────────────────────────────────────────


def test_cagr_annualises_so_periods_are_comparable():
    """1년에 +20%와 3년에 +20%는 전혀 다르다. 총수익률로는 구분이 안 된다."""
    one_year = curve([100.0, 120.0], start=date(2023, 1, 1))
    one_year.loc[1, "trade_date"] = date(2024, 1, 1)

    three_years = curve([100.0, 120.0], start=date(2021, 1, 1))
    three_years.loc[1, "trade_date"] = date(2024, 1, 1)

    assert cagr_pct(one_year) == pytest.approx(20.0, abs=0.2)
    assert cagr_pct(three_years) == pytest.approx(6.3, abs=0.3)


def test_cagr_handles_degenerate_input():
    assert cagr_pct(curve([100.0])) == 0.0
    assert cagr_pct(pd.DataFrame(columns=["trade_date", "equity"])) == 0.0


# ── 위험 대비 ────────────────────────────────────────────────────


def test_sharpe_prefers_the_smoother_path_to_the_same_place():
    """같은 곳에 도착했다면 덜 흔들린 쪽이 나은 전략이다."""
    smooth = curve([100 + i for i in range(60)])
    jagged = curve([100 + i + (8 if i % 2 else -8) for i in range(60)])

    assert sharpe(smooth) > sharpe(jagged)


def test_sortino_does_not_punish_upside_spikes():
    """위로 튀는 건 벌하지 않아야 한다. 투자자가 싫어하는 건 손실 쪽 변동이다."""
    def equity_from(returns):
        value, values = 100.0, [100.0]
        for r in returns:
            value *= 1 + r
            values.append(value)
        return curve(values)

    # 오르내림이 섞인 기준선에, 딱 하루만 위로 크게 튄 경우를 비교한다
    base_returns = [0.01 if i % 2 == 0 else -0.005 for i in range(60)]
    spiked = list(base_returns)
    spiked[30] = 0.15

    # 튄 날은 Sharpe의 분모(전체 변동성)는 키우지만 Sortino의 분모(하락
    # 변동성)는 안 키운다. 그래서 같은 곡선에서 Sortino가 더 높아야 한다.
    assert sortino(equity_from(spiked)) > sharpe(equity_from(spiked))
    assert sortino(equity_from(base_returns)) > 0


def test_sortino_is_never_nan_with_a_single_down_day():
    """하락일이 하나뿐이면 표본 표준편차가 NaN이 된다. 그대로 흘리면
    비교표에 nan이 섞여 전략 하나가 통째로 판독 불가가 된다."""
    values = [100 + i for i in range(30)]
    values[15] -= 3  # 하락 구간이 딱 한 번

    result = sortino(curve(values))

    assert not math.isnan(result), "NaN이 새어 나오면 안 된다"
    assert result == 0.0


def test_metrics_are_zero_when_there_is_no_variation():
    flat = curve([100.0] * 30)
    assert sharpe(flat) == 0.0
    assert sortino(flat) == 0.0


# ── 거래 기반 ────────────────────────────────────────────────────


def test_profit_factor_compares_totals_not_averages():
    """분석 리포트의 '손익비'(평균 비교)와 다른 값이다.

    이긴 거래가 한 건뿐이어도 그 금액이 크면 Profit Factor는 1을 넘는다."""
    trades = [trade(300.0), trade(-50.0), trade(-50.0), trade(-50.0)]

    assert profit_factor(trades) == pytest.approx(2.0)


def test_profit_factor_without_losses_is_infinite_not_zero():
    """손실이 없는데 0을 돌려주면 '최악의 전략'으로 잘못 읽힌다."""
    assert profit_factor([trade(100.0)]) == float("inf")
    assert profit_factor([]) == 0.0


def test_expectancy_exposes_high_win_rate_with_bad_payoff():
    """승률 80%인데 장기적으로 잃는 경우를 잡아내는 게 이 지표의 목적이다
    (인수인계서 25항)."""
    trades = [trade(10.0, pnl_pct=1.0)] * 8 + [trade(-60.0, pnl_pct=-6.0)] * 2

    win_rate = 80.0
    assert win_rate > 50
    assert expectancy_pct(trades) < 0, "승률이 높아도 기대값은 음수일 수 있다"


# ── 자금을 얼마나 굴렸는가 ───────────────────────────────────────


def test_exposure_measures_days_holding_something():
    """수익률이 낮을 때 '기회가 없어서'인지 '판단이 틀려서'인지 구분하려면
    자금을 얼마나 굴렸는지를 알아야 한다."""
    half = curve([100.0] * 10, positions=[0, 0, 0, 0, 0, 1, 1, 1, 1, 1])

    assert exposure_pct(half) == pytest.approx(50.0)
    assert exposure_pct(curve([100.0] * 5, positions=[0] * 5)) == 0.0


def test_turnover_counts_both_sides_of_each_trade():
    """회전율이 높을수록 수수료·세금이 성과를 갉아먹는다."""
    trades = [trade(0.0, quantity=10, entry_price=100.0)]  # 매수 1000 + 매도 1000
    result = turnover(trades, curve([1000.0] * 10))

    assert result == pytest.approx(2.0)


def test_turnover_is_zero_without_trades():
    assert turnover([], curve([1000.0] * 5)) == 0.0


# ── 통합 ─────────────────────────────────────────────────────────


def test_compute_metrics_fills_every_field():
    """지표 하나가 조용히 0으로 남으면 비교표에서 잘못 읽힌다."""

    fake = SimpleNamespace(
        equity_curve=curve([100 + i for i in range(40)]),
        closed_trades=[trade(50.0, pnl_pct=5.0), trade(-20.0, pnl_pct=-2.0)],
        total_return_pct=39.0,
        max_drawdown_pct=-3.0,
        win_rate_pct=50.0,
        num_trades=2,
    )

    metrics = compute_metrics(fake)

    assert metrics.cagr_pct > 0
    assert metrics.sharpe > 0
    assert metrics.profit_factor == pytest.approx(2.5)
    assert metrics.expectancy_pct == pytest.approx(1.5)
    assert metrics.exposure_pct == 100.0
    assert metrics.avg_holding_days == 5.0
    assert set(metrics.as_row()) == {
        "CAGR",
        "MDD",
        "Sharpe",
        "Sortino",
        "PF",
        "기대값%",
        "승률",
        "거래",
        "보유일",
        "노출%",
    }


# ── 체결 가정 ────────────────────────────────────────────────────


def test_slippage_makes_you_buy_higher_and_sell_lower():
    """체결가는 종가보다 항상 불리한 쪽으로 잡혀야 한다.
    부호가 하나만 뒤집혀도 백테스트가 슬리피지를 이익으로 계산한다."""
    from muwon.backtest.costs import TransactionCosts

    costs = TransactionCosts(slippage_pct=0.001)

    assert costs.buy_price(10_000) == pytest.approx(10_010)
    assert costs.sell_price(10_000) == pytest.approx(9_990)


def test_default_costs_keep_the_close_price_assumption():
    """기본값을 바꾸면 지금까지 낸 모든 숫자와 비교가 안 된다.
    얼마가 맞는지는 실측으로 정할 문제라 기본은 0으로 둔다."""
    from muwon.backtest.costs import TransactionCosts

    costs = TransactionCosts()

    assert costs.slippage_pct == 0.0
    assert costs.buy_price(10_000) == 10_000
    assert costs.sell_price(10_000) == 10_000
