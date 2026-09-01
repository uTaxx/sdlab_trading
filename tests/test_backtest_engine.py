import pandas as pd

from muwon.backtest.engine import BacktestEngine
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.rule_based import MovingAverageRsiStrategy
from tests.price_series import breakout_entry_then_dead_cross_exit, flat_then_breakout


def make_engine(policy: RiskPolicy | None = None, initial_cash: float = 10_000_000.0) -> BacktestEngine:
    policy = policy or RiskPolicy()
    return BacktestEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=RiskManager(policy_provider=lambda: policy),
        initial_cash=initial_cash,
    )


def test_buy_and_sell_round_trip_produces_closed_trade():
    df = breakout_entry_then_dead_cross_exit()
    engine = make_engine()
    result = engine.run({"TEST": df})

    assert result.num_trades == 1
    trade = result.closed_trades[0]
    assert trade.symbol == "TEST"
    assert trade.exit_reason == "단기선 하향이탈"
    assert trade.exit_price < trade.entry_price


def test_equity_curve_covers_all_trading_days():
    df = flat_then_breakout()
    engine = make_engine()
    result = engine.run({"TEST": df})
    assert len(result.equity_curve) == len(df)
    assert result.equity_curve["equity"].iloc[0] > 0


def test_stop_loss_closes_position_before_dead_cross():
    """진입 직후 급락하면 데드크로스 신호를 기다리지 않고 손절선에서 먼저
    청산되어야 한다."""
    df = flat_then_breakout(breakout_price=102.0, tail_price=103.0)
    crash_row = df.iloc[[-1]].copy()
    crash_row["close"] = 90.0  # 진입가(102) 대비 -11.8%, 손절선(-5%) 큰 폭 이탈
    crash_row["trade_date"] = df["trade_date"].iloc[-1] + (df["trade_date"].iloc[-1] - df["trade_date"].iloc[-2])
    df_with_crash = pd.concat([df, crash_row], ignore_index=True)

    engine = make_engine(RiskPolicy(stop_loss_pct=-0.05))
    result = engine.run({"TEST": df_with_crash})

    assert result.num_trades == 1
    assert result.closed_trades[0].exit_reason == "손절"


def test_max_concurrent_positions_limits_entries():
    df_a = flat_then_breakout()
    df_b = flat_then_breakout()  # 동일 패턴 → 같은 날 동시에 매수 신호 발생

    policy = RiskPolicy(max_concurrent_positions=1)
    engine = make_engine(policy)
    result = engine.run({"A": df_a, "B": df_b})

    # 데드크로스가 없는 패턴이라 포지션이 끝까지 열려 있다. 동시 보유 한도(1)를
    # 넘겨 두 종목이 동시에 열리면 안 된다.
    assert len(result.final_positions) == 1


def test_시세가_없는_날에도_들고_있는_종목을_0원으로_치지_않는다():
    """종목마다 거래일이 다르면 평가금액이 무너지던 결함.

    전에는 그날 시세가 없는 종목을 평가금액 계산에서 통째로 뺐다. 그러면
    그 종목이 0원이 된다. 어떤 날 한 종목만 거래일이면 그날 들고 있던
    나머지가 전부 0원이 되어 계좌가 하루 만에 무너진 것으로 찍혔고, 다음
    날 되돌아와서 총수익률은 멀쩡해 보이고 최대 하락폭만 말이 안 되게
    나왔다. 63종목으로 재다가 12개월 최대 하락폭이 -98.37%로 나와서
    드러났다(2026-08-31).

    30종목일 때는 거래일이 모두 같아서 한 번도 안 드러났다."""
    산것 = flat_then_breakout()
    # 다른 종목 하나가 하루 늦게 상장한다. 그날은 이 종목만 거래일이다.
    늦은것 = 산것.iloc[[-1]].copy()
    하루 = 산것["trade_date"].iloc[-1] - 산것["trade_date"].iloc[-2]
    늦은것["trade_date"] = 산것["trade_date"].iloc[-1] + 하루

    result = make_engine().run({"TEST": 산것, "LATE": 늦은것})

    assert "TEST" in result.final_positions, "TEST를 들고 있어야 시험이 성립한다"
    곡선 = result.equity_curve
    assert 곡선["trade_date"].iloc[-1] == 늦은것["trade_date"].iloc[0]

    # TEST 값은 그대로다. LATE는 안 샀다. 그러니 평가금액이 변할 이유가 없다.
    어제, 오늘 = float(곡선["equity"].iloc[-2]), float(곡선["equity"].iloc[-1])
    assert abs(오늘 - 어제) < 1.0, (
        f"평가금액이 {어제:,.0f}에서 {오늘:,.0f}로 바뀌었습니다. "
        "시세 없는 날 보유 종목을 0원으로 친 것입니다."
    )
