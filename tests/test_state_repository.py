from datetime import datetime

from muwon.db.models import PositionRow, TradeRow
from muwon.db.session import make_session_factory
from muwon.domain.types import OrderResult, OrderSide
from muwon.execution import state_repository


def test_record_trade_computes_pnl_correctly():
    session_factory = make_session_factory("sqlite:///:memory:")
    position = PositionRow(
        symbol="005930",
        quantity=10,
        entry_price=100.0,
        entry_date=datetime(2024, 1, 2).date(),  # noqa: DTZ001 (테스트용, tz 무관)
        entered_at=datetime(2024, 1, 2, 9, 30),  # noqa: DTZ001 (테스트용, tz 무관)
        entry_reason="단기선 상향돌파 + 거래량 급증",
        strategy_key="ma_rsi_v1",
    )
    exit_order = OrderResult(
        symbol="005930", side=OrderSide.SELL, quantity=10, price=110.0, order_id="X1", is_paper=True
    )

    state_repository.record_trade(session_factory, position, exit_order, "단기선 하향이탈")

    with session_factory() as session:
        trades = session.query(TradeRow).all()
    assert len(trades) == 1
    trade = trades[0]
    assert trade.symbol == "005930"
    assert trade.strategy_key == "ma_rsi_v1"
    assert trade.quantity == 10
    assert trade.entry_price == 100.0
    assert trade.exit_price == 110.0
    assert trade.pnl_amount == 100.0  # (110-100) * 10
    assert round(trade.pnl_pct, 4) == 10.0  # +10%
    assert trade.exit_reason == "단기선 하향이탈"
    assert trade.entered_at == datetime(2024, 1, 2, 9, 30)  # noqa: DTZ001 (테스트용, tz 무관)
def test_record_trade_handles_loss():
    session_factory = make_session_factory("sqlite:///:memory:")
    position = PositionRow(
        symbol="000660",
        quantity=5,
        entry_price=200.0,
        entry_date=datetime(2024, 1, 2).date(),  # noqa: DTZ001 (테스트용, tz 무관)
        entered_at=datetime(2024, 1, 2, 10, 0),  # noqa: DTZ001 (테스트용, tz 무관)
        entry_reason="RSI 과매도 반등",
        strategy_key="ma_rsi_fast5_20",
    )
    exit_order = OrderResult(
        symbol="000660", side=OrderSide.SELL, quantity=5, price=180.0, order_id="X2", is_paper=True
    )

    state_repository.record_trade(session_factory, position, exit_order, "손절")

    with session_factory() as session:
        trade = session.query(TradeRow).one()
    assert trade.pnl_amount == -100.0  # (180-200) * 5
    assert round(trade.pnl_pct, 4) == -10.0
