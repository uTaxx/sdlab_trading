"""RealtimeTradingEngine을 '봉 하나당 틱 하나'로 흘려보내며 검증한다.
기존 일봉 합성 시나리오(tests/price_series.py)의 종가·거래량 시퀀스를
그대로 재사용한다 — MovingAverageRsiStrategy는 close/volume만 보고
open/high/low는 안 보므로, 봉 하나에 틱 하나씩만 흘려도 동일한 신호가
나온다."""

from datetime import UTC, datetime, timedelta

from muwon.data.tick_aggregator import Tick
from muwon.data.universe import Ticker
from muwon.db.models import OrderRow, PositionRow, TradeRow
from muwon.db.session import make_session_factory
from muwon.execution.realtime_engine import RealtimeTradingEngine
from muwon.execution.simulated_executor import SimulatedOrderExecutor
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.rule_based import MovingAverageRsiStrategy
from tests.price_series import breakout_entry_then_dead_cross_exit, flat_then_breakout

TEST_TICKER = Ticker("005930", "삼성전자", "KOSPI", "005930.KS")
BAR_SECONDS = 60
T0 = datetime(2024, 1, 2, 9, 0, 0, tzinfo=UTC)


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


def make_engine(policy: RiskPolicy | None = None, notifier=None):
    policy = policy or RiskPolicy()
    session_factory = make_session_factory("sqlite:///:memory:")
    notifier = notifier or FakeNotifier()
    engine = RealtimeTradingEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=RiskManager(policy_provider=lambda: policy),
        order_executor=SimulatedOrderExecutor(),
        notifier=notifier,
        session_factory=session_factory,
        universe=[TEST_TICKER],
        bar_seconds=BAR_SECONDS,
    )
    engine.start()
    return engine, session_factory, notifier


def feed_closes_as_ticks(engine, closes: list[float], volumes: list[int], start_offset: int = 0):
    """봉 하나당 틱 하나를 흘려보낸다. 각 봉은 '다음 틱이 새 시간창을 열 때'
    비로소 마감·평가되므로, 마지막(가장 중요한) 봉을 실제로 마감시키려면
    뒤에 트리거용 틱을 하나 더 보내야 한다."""
    n = len(closes)
    for i, (price, volume) in enumerate(zip(closes, volumes, strict=True)):
        ts = T0 + timedelta(seconds=start_offset + i * BAR_SECONDS)
        engine.on_tick(Tick(symbol=TEST_TICKER.symbol, price=price, volume=volume, timestamp=ts))
    flush_ts = T0 + timedelta(seconds=start_offset + n * BAR_SECONDS)
    engine.on_tick(Tick(symbol=TEST_TICKER.symbol, price=closes[-1], volume=0, timestamp=flush_ts))


def _closes_and_volumes(df):
    return list(df["close"]), list(df["volume"])


def test_breakout_bar_sequence_triggers_buy_and_notifies():
    engine, session_factory, notifier = make_engine()
    closes, volumes = _closes_and_volumes(flat_then_breakout(tail_days=0))

    feed_closes_as_ticks(engine, closes, volumes)

    with session_factory() as session:
        positions = session.query(PositionRow).all()
        orders = session.query(OrderRow).all()
    assert len(positions) == 1
    assert len(orders) == 1
    assert orders[0].side == "buy"
    assert any("🟢 매수체결 (장중)" in m for m in notifier.messages)


def test_dead_cross_bar_sells_existing_position():
    """진입→보유→청산이 하나로 이어진 fixture를 그대로 흘려보낸다 —
    일봉 엔진 테스트(test_execution_engine.py)와 같은 시나리오를 봉 단위
    틱 스트림으로 재현."""
    engine, session_factory, notifier = make_engine()
    closes, volumes = _closes_and_volumes(breakout_entry_then_dead_cross_exit(tail_days=0))

    feed_closes_as_ticks(engine, closes, volumes)

    with session_factory() as session:
        positions = session.query(PositionRow).all()
        orders = session.query(OrderRow).all()
        trades = session.query(TradeRow).all()
    assert len(positions) == 0
    assert len(orders) == 2
    assert orders[0].side == "buy"
    assert orders[1].side == "sell"
    assert orders[1].reason == "단기선 하향이탈"

    assert len(trades) == 1
    assert trades[0].strategy_key == "ma_rsi_v1"
    assert trades[0].exit_reason == "단기선 하향이탈"

    assert any("🔴 매도체결 (장중)" in m for m in notifier.messages)


def test_trading_disabled_blocks_realtime_entry():
    policy = RiskPolicy(trading_enabled=False)
    engine, session_factory, notifier = make_engine(policy=policy)
    closes, volumes = _closes_and_volumes(flat_then_breakout(tail_days=0))

    feed_closes_as_ticks(engine, closes, volumes)

    with session_factory() as session:
        assert session.query(PositionRow).count() == 0
    assert notifier.messages == []


def test_on_tick_before_start_raises():
    session_factory = make_session_factory("sqlite:///:memory:")
    engine = RealtimeTradingEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=RiskManager(policy_provider=RiskPolicy),
        order_executor=SimulatedOrderExecutor(),
        notifier=FakeNotifier(),
        session_factory=session_factory,
        universe=[TEST_TICKER],
    )
    try:
        engine.on_tick(Tick(symbol="005930", price=100.0, volume=1, timestamp=T0))
        raise AssertionError("RuntimeError가 발생해야 한다")
    except RuntimeError:
        pass
