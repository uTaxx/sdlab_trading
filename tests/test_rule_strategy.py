from muwon.domain.types import SignalType
from muwon.strategy.rule_based import MovingAverageRsiStrategy
from tests.price_series import (
    flat_then_breakout,
    range_bound,
    sharp_uptrend_overbought,
    uptrend_then_dead_cross,
    uptrend_with_oversold_dip_and_bounce,
)


def test_golden_cross_with_volume_triggers_buy():
    strategy = MovingAverageRsiStrategy()
    signals = strategy.generate_signals("TEST", flat_then_breakout())
    buys = [s for s in signals if s.signal_type == SignalType.BUY]
    assert any("거래량 급증" in s.reason for s in buys)


def test_dead_cross_triggers_sell():
    strategy = MovingAverageRsiStrategy()
    signals = strategy.generate_signals("TEST", uptrend_then_dead_cross())
    sells = [s for s in signals if s.signal_type == SignalType.SELL]
    assert any("하향이탈" in s.reason for s in sells)


def test_rsi_oversold_bounce_triggers_buy():
    strategy = MovingAverageRsiStrategy()
    df = uptrend_with_oversold_dip_and_bounce()
    signals = strategy.generate_signals("TEST", df)
    buys = [s for s in signals if s.signal_type == SignalType.BUY]
    assert any("과매도 반등" in s.reason for s in buys)


def test_rsi_overbought_triggers_sell():
    strategy = MovingAverageRsiStrategy()
    signals = strategy.generate_signals("TEST", sharp_uptrend_overbought())
    sells = [s for s in signals if s.signal_type == SignalType.SELL]
    assert any("과매수" in s.reason for s in sells)


def test_volume_confirmed_buy_never_fires_without_volume_surge():
    """횡보장에서는 이동평균을 오르내리는 잔가지 크로스가 생길 수 있어도
    (추세추종 전략의 잘 알려진 한계), 거래량 급증이 없으면 고신뢰
    '골든크로스+거래량' 매수는 절대 나오면 안 된다. 거래량이 항상 일정한
    합성 데이터로 이를 확인한다."""
    strategy = MovingAverageRsiStrategy()
    signals = strategy.generate_signals("TEST", range_bound())
    assert not any("거래량 급증" in s.reason for s in signals)
