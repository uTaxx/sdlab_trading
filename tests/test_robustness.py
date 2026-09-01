"""여러 기간 검증(과최적화 탐지) 로직 확인.

핵심은 "한 구간 대박 + 다른 구간 폭망" 전략이 "꾸준히 조금 버는" 전략보다
위로 올라오지 않는가다. 평균만 보면 정확히 그 반대가 된다."""

import math
from datetime import date

import pandas as pd

from muwon.analysis.robustness import (
    PeriodOutcome,
    PeriodWindow,
    RobustnessResult,
    _slice_histories,
    evaluate_robustness,
    format_robustness_table,
    half_year_windows,
    rank_by_robustness,
    yearly_windows,
)
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import get_definition
from tests.price_series import make_price_df


def make_result(key: str, returns: list[float], trades: int = 10) -> RobustnessResult:
    return RobustnessResult(
        key=key,
        display_name=key,
        category="추세추종",
        outcomes=[
            PeriodOutcome(label=f"P{i}", return_pct=r, mdd_pct=-10.0, win_rate_pct=50.0, num_trades=trades)
            for i, r in enumerate(returns)
        ],
    )


def test_yearly_windows_cover_each_year():
    windows = yearly_windows(2022, 2024)
    assert [w.label for w in windows] == ["2022", "2023", "2024"]
    assert windows[0].trade_from == date(2022, 1, 1)
    assert windows[-1].end == date(2024, 12, 31)


def test_half_year_windows_split_each_year_in_two():
    windows = half_year_windows(2023, 2023)
    assert [w.label for w in windows] == ["2023H1", "2023H2"]
    assert windows[0].end == date(2023, 6, 30)
    assert windows[1].trade_from == date(2023, 7, 1)


def test_window_data_range_includes_warmup_before_trading_starts():
    """예열 없이 구간을 자르면 초반 지표가 NaN이라 신호가 안 나온다.
    짧은 구간일수록 결과가 과소평가되는 왜곡이 생긴다."""
    window = yearly_windows(2024, 2024)[0]
    assert window.data_from < window.trade_from


def test_worst_period_beats_mean_when_ranking():
    """평균으로 줄 세우면 '한 번 대박, 한 번 폭망'이 위로 온다. 실전에서
    중요한 건 못 버티는 구간이 있느냐이므로 최악 구간이 1순위여야 한다."""
    volatile = make_result("volatile", [100.0, -60.0])  # 평균 +20%
    steady = make_result("steady", [6.0, 4.0])  # 평균 +5%

    ranked = rank_by_robustness([volatile, steady])

    assert [r.key for r in ranked] == ["steady", "volatile"]
    assert volatile.mean_return_pct > steady.mean_return_pct  # 평균은 오히려 높다


def test_metrics_summarize_spread_and_consistency():
    result = make_result("mixed", [30.0, -10.0, 5.0])

    assert round(result.mean_return_pct, 2) == 8.33
    assert result.worst_return_pct == -10.0
    assert result.best_return_pct == 30.0
    assert result.spread_pct == 40.0
    assert round(result.positive_ratio, 2) == 0.67
    assert result.total_trades == 30


def test_verdict_flags_all_positive():
    assert "전 구간 플러스" in make_result("good", [5.0, 3.0, 8.0]).verdict


def test_verdict_flags_overfitting_suspicion():
    """한 구간에서 크게 벌고 다른 구간에서 크게 잃으면 과최적화를 의심해야 한다."""
    assert "과최적화 의심" in make_result("lucky", [80.0, -35.0]).verdict


def test_verdict_handles_strategy_that_never_traded():
    assert make_result("idle", [0.0, 0.0], trades=0).verdict == "거래없음"


def test_slice_histories_keeps_only_window_range():
    df = make_price_df([100.0] * 400, start=date(2023, 1, 1))
    window = PeriodWindow("2024", date(2024, 1, 1), date(2024, 3, 31))

    sliced = _slice_histories({"TEST": df}, window)["TEST"]

    assert sliced["trade_date"].min() >= window.data_from
    assert sliced["trade_date"].max() <= window.end
    # 예열 구간이 실제로 포함돼 있어야 지표가 채워진다
    assert sliced["trade_date"].min() < window.trade_from


def test_slice_drops_symbols_with_no_data_in_trading_window():
    """예열 구간에만 데이터가 있고 매매 구간엔 없는 종목은 제외해야 한다
    (상장폐지 등): 넣어봐야 거래가 발생할 수 없다."""
    df = make_price_df([100.0] * 30, start=date(2023, 9, 1))  # 2023년에만 존재
    window = PeriodWindow("2024", date(2024, 1, 1), date(2024, 12, 31))

    assert _slice_histories({"GONE": df}, window) == {}


def test_evaluate_runs_each_window_independently():
    """구간마다 초기 자금부터 다시 시작해야 앞 구간 성과가 뒤에 섞이지 않는다.

    단조 상승/하락 시계열을 쓰면 이동평균선이 교차하는 사건 자체가 없어
    거래가 0건이 되므로, 파동이 있는 시계열로 실제 교차를 만든다."""
    closes = [100 + 25 * math.sin(i / 25) + i * 0.05 for i in range(760)]
    df = make_price_df(closes, start=date(2023, 1, 2))

    windows = yearly_windows(2023, 2024)
    results = evaluate_robustness(
        [get_definition("golden_cross_5_20")],
        {"TEST": df},
        windows,
        RiskManager(policy_provider=lambda: RiskPolicy()),
    )

    assert len(results) == 1
    assert [o.label for o in results[0].outcomes] == ["2023", "2024"]
    assert all(o.num_trades > 0 for o in results[0].outcomes)
    # 구간마다 따로 돌았다면 성과가 서로 다르게 나온다
    assert results[0].spread_pct > 0


def test_format_table_lists_every_window_column():
    windows = yearly_windows(2023, 2024)
    table = format_robustness_table([make_result("k", [10.0, -5.0])], windows)

    assert "2023" in table and "2024" in table
    assert "평균" in table and "최악" in table
    assert "k" in table


def test_backtest_engine_ignores_trades_before_trade_from():
    """trade_from 이전 구간은 지표 예열에만 쓰고 매매·평가금액 기록을
    하지 않아야 한다. 안 그러면 예열 구간의 손익이 성과에 섞인다."""
    from muwon.backtest.engine import BacktestEngine

    closes = [100.0 + i for i in range(200)]
    df = make_price_df(closes, start=date(2023, 1, 2))
    engine = BacktestEngine(
        strategy=get_definition("golden_cross_5_20").factory(),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    )

    trade_from = df["trade_date"].iloc[150]
    result = engine.run({"TEST": df}, trade_from=trade_from)

    assert len(result.equity_curve) > 0
    assert result.equity_curve["trade_date"].min() >= trade_from
    for trade in result.closed_trades:
        assert trade.entry_date >= trade_from


def test_backtest_engine_without_trade_from_is_unchanged():
    """trade_from을 안 주면 기존과 똑같이 전체 구간을 매매해야 한다."""
    from muwon.backtest.engine import BacktestEngine

    df = make_price_df([100.0 + i for i in range(200)], start=date(2023, 1, 2))
    engine = BacktestEngine(
        strategy=get_definition("golden_cross_5_20").factory(),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    )

    result = engine.run({"TEST": df})
    assert len(result.equity_curve) == len(df)
    assert isinstance(result.equity_curve, pd.DataFrame)
