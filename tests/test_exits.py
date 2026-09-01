"""변동성 기반 청산(ATR 손절·트레일링) 검증.

기본값은 꺼져 있다. 실측에서 고정 -5%보다 나쁘게 나왔기 때문이다(커밋 메시지
참고). 그래도 코드를 남긴 이유는 종목 구성이나 시장이 달라지면 다시 후보가
되기 때문이고, 남기는 이상 동작은 검증돼 있어야 한다."""

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.risk.exits import atr_series, evaluate_exit, highest_close_since
from muwon.settings.schema import RiskPolicy


def frame(closes, highs=None, lows=None, start=date(2024, 1, 2)):
    n = len(closes)
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(n)],
            "open": closes,
            "high": highs or [c * 1.02 for c in closes],
            "low": lows or [c * 0.98 for c in closes],
            "close": closes,
            "volume": [100_000] * n,
        }
    )


def flat_atr(days=40, price=100.0, daily_range=4.0):
    """하루에 daily_range만큼 움직이는 종목: ATR이 대략 그 값이 된다."""
    closes = [price] * days
    highs = [price + daily_range / 2] * days
    lows = [price - daily_range / 2] * days
    return frame(closes, highs, lows)


def test_atr_stop_fires_at_volatility_scaled_level():
    """손절선이 진입가 - (ATR × 배수)여야 한다."""
    df = flat_atr()
    atr = atr_series(df)
    entry_date = df["trade_date"].iloc[-2]
    as_of = df["trade_date"].iloc[-1]
    atr_value = float(atr.loc[entry_date])
    policy = RiskPolicy(atr_stop_enabled=True, atr_stop_multiple=2.0)

    common = {
        "entry_price": 100.0,
        "entry_date": entry_date,
        "as_of": as_of,
        "policy": policy,
        "atr": atr,
    }

    just_above = evaluate_exit(current_price=100.0 - atr_value * 2 + 0.5, **common)
    just_below = evaluate_exit(current_price=100.0 - atr_value * 2 - 0.5, **common)

    assert not just_above.should_exit
    assert just_below.should_exit
    assert "ATR 손절" in just_below.reason


def test_volatile_stock_gets_wider_stop_than_quiet_one():
    """같은 -4% 하락이 조용한 종목에선 손절, 출렁이는 종목에선 아직 아니어야 한다.
    이게 ATR 손절을 쓰는 유일한 이유다."""
    policy = RiskPolicy(atr_stop_enabled=True, atr_stop_multiple=2.0)
    quiet, volatile = flat_atr(daily_range=1.0), flat_atr(daily_range=6.0)

    def decide(df):
        atr = atr_series(df)
        return evaluate_exit(
            entry_price=100.0,
            entry_date=df["trade_date"].iloc[-2],
            current_price=96.0,
            as_of=df["trade_date"].iloc[-1],
            policy=policy,
            atr=atr,
        ).should_exit

    assert decide(quiet) is True
    assert decide(volatile) is False


def test_falls_back_to_fixed_stop_when_atr_missing():
    """변동성 정보가 없다고 손절 자체가 사라지면 안 된다."""
    policy = RiskPolicy(atr_stop_enabled=True, stop_loss_pct=-0.05)
    result = evaluate_exit(
        entry_price=100.0,
        entry_date=date(2024, 1, 2),
        current_price=94.0,
        as_of=date(2024, 1, 3),
        policy=policy,
        atr=None,
    )
    assert result.should_exit
    assert result.reason == "손절"


def test_disabled_policy_uses_fixed_stop_only():
    policy = RiskPolicy()  # 기본값: ATR 꺼짐
    df = flat_atr()
    common = {
        "entry_price": 100.0,
        "entry_date": df["trade_date"].iloc[-2],
        "as_of": df["trade_date"].iloc[-1],
        "policy": policy,
        "atr": atr_series(df),
        "history": df,
    }

    assert not evaluate_exit(current_price=96.0, **common).should_exit  # -4%
    assert evaluate_exit(current_price=94.0, **common).should_exit  # -6%


def test_trailing_stop_measures_from_peak_not_entry():
    """트레일링은 고점 대비로 재야 한다. 진입가 기준이면 이익을 못 지킨다."""
    closes = [100.0] * 20 + [130.0] * 5 + [120.0]
    df = frame(closes, [c * 1.02 for c in closes], [c * 0.98 for c in closes])
    policy = RiskPolicy(trailing_stop_enabled=True, trailing_stop_multiple=1.0)

    result = evaluate_exit(
        entry_price=100.0,
        entry_date=df["trade_date"].iloc[0],
        current_price=120.0,
        as_of=df["trade_date"].iloc[-1],
        policy=policy,
        atr=atr_series(df),
        history=df,
    )

    assert result.should_exit, "고점 130에서 크게 밀렸으면 이익을 지켜야 한다"
    assert "트레일링" in result.reason
    assert "+20.0%" in result.reason, "아직 이익 구간임이 사유에 드러나야 한다"


def test_trailing_does_not_fire_below_entry():
    """고점이 진입가 아래면 트레일링은 손절과 같은 일을 두 번 하는 셈이다.

    고정 손절(-5%)에 걸리지 않는 -3% 지점을 써야 트레일링만 떼어 볼 수 있다."""
    closes = [100.0] * 20 + [98.0] * 5
    df = frame(closes, [c * 1.02 for c in closes], [c * 0.98 for c in closes])
    policy = RiskPolicy(trailing_stop_enabled=True, trailing_stop_multiple=0.1)

    result = evaluate_exit(
        entry_price=100.0,
        entry_date=df["trade_date"].iloc[-3],
        current_price=97.0,
        as_of=df["trade_date"].iloc[-1],
        policy=policy,
        atr=atr_series(df),
        history=df,
    )
    assert not result.should_exit


def test_highest_close_since_ignores_outside_window():
    df = frame([100.0, 200.0, 110.0, 120.0, 90.0])
    dates = list(df["trade_date"])

    assert highest_close_since(df, dates[2], dates[3]) == pytest.approx(120.0)
    assert highest_close_since(df, dates[0], dates[1]) == pytest.approx(200.0)


def test_take_profit_sells_when_the_target_is_reached():
    """익절이 이 시스템에 아예 없었다. 넣되 기본값은 끔이다.
    유리한지 아직 재지 않았기 때문이다."""
    from datetime import date

    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    policy = RiskPolicy(take_profit_pct=0.10)
    decision = evaluate_exit(
        entry_price=10_000,
        entry_date=date(2024, 1, 2),
        current_price=11_000,
        as_of=date(2024, 1, 5),
        policy=policy,
    )
    assert decision.should_exit
    assert "익절" in decision.reason


def test_take_profit_is_off_by_default():
    """기본값이 켜져 있으면 지금까지의 모든 결과와 비교가 안 된다."""
    from datetime import date

    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    decision = evaluate_exit(
        entry_price=10_000,
        entry_date=date(2024, 1, 2),
        current_price=20_000,  # +100%인데도
        as_of=date(2024, 1, 5),
        policy=RiskPolicy(),
    )
    assert not decision.should_exit


def test_the_stop_loss_still_wins_over_take_profit():
    """손실을 막는 쪽이 언제나 먼저여야 한다."""
    from datetime import date

    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    decision = evaluate_exit(
        entry_price=10_000,
        entry_date=date(2024, 1, 2),
        current_price=9_000,
        as_of=date(2024, 1, 5),
        policy=RiskPolicy(stop_loss_pct=-0.05, take_profit_pct=0.10),
    )
    assert decision.should_exit
    assert decision.reason == "손절"
