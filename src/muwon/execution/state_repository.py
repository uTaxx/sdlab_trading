"""TradingEngine(일 1회)과 RealtimeTradingEngine(장중 상시)이 공유하는
포지션/주문/가상현금 저장소.

두 엔진이 같은 DB 상태를 쓰는 건, 이 함수들이 둘 다 재사용되기 때문이지
두 엔진을 동시에 같은 계좌에 돌리라는 뜻이 아니다. 배치(GitHub Actions)와
장중 상시(VPS)는 서로 다른 운영 모드로, 한 번에 하나만 실제 계좌에 붙여
쓸 것을 전제로 한다."""

from __future__ import annotations

from datetime import datetime

from muwon.db.models import (
    EngineStateRow,
    OrderRow,
    PositionRow,
    RunLogRow,
    SignalRow,
    TradeRow,
)
from muwon.domain.types import OrderResult


def load_positions(session_factory) -> dict[str, PositionRow]:
    with session_factory() as session:
        rows = session.query(PositionRow).all()
        session.expunge_all()
        return {row.symbol: row for row in rows}


def save_position(session_factory, position: PositionRow) -> None:
    with session_factory() as session:
        session.merge(position)
        session.commit()


def delete_position(session_factory, symbol: str) -> None:
    with session_factory() as session:
        row = session.get(PositionRow, symbol)
        if row is not None:
            session.delete(row)
            session.commit()


def record_order(session_factory, order: OrderResult, reason: str) -> None:
    with session_factory() as session:
        session.add(
            OrderRow(
                symbol=order.symbol,
                side=order.side.value,
                quantity=order.quantity,
                price=order.price,
                is_paper=order.is_paper,
                kis_order_id=order.order_id,
                reason=reason,
                reference_price=order.reference_price or None,
                fill_confirmed=order.fill_confirmed,
            )
        )
        session.commit()


def record_signals(session_factory, signals) -> int:
    """전략이 낸 신호를 그대로 남긴다.

    signals 테이블은 스키마에만 있고 아무도 쓰지 않았다. 그래서 "0건"이
    "신호가 없었다"인지 "기록을 안 했다"인지 알 수 없었다. 실제로 오늘
    그 구분이 안 돼서 한참 헤맸다. 여기서 남기는 건 매수/매도 신호가 실제로
    떴는지이고, 그게 떴는데도 안 샀다면 이유는 run_logs.rejections에 있다.

    같은 날 두 번 실행하면 같은 신호가 두 줄 남는다. 합치지 않는다.
    실행 자체가 두 번 있었다는 것도 사실이기 때문이다."""
    if not signals:
        return 0
    with session_factory() as session:
        for signal in signals:
            session.add(
                SignalRow(
                    symbol=signal.symbol,
                    trade_date=signal.trade_date,
                    strategy_name=signal.strategy_name,
                    signal_type=signal.signal_type.value,
                    score=signal.score,
                )
            )
        session.commit()
    return len(signals)


def record_run(
    session_factory,
    *,
    run_date,
    strategy_key: str,
    universe_size: int,
    checked_symbols: int,
    buy_signals: int,
    sell_signals: int,
    orders: int,
    rejections: list[str],
    cash: float,
    equity: float,
) -> None:
    """한 회차가 무엇을 보고 무엇을 했는지 한 줄로 남긴다.

    체결이 없어도 남긴다. 그래야 "살 게 없었다"와 "안 돌았다"가 갈린다."""
    with session_factory() as session:
        session.add(
            RunLogRow(
                run_date=run_date,
                strategy_key=strategy_key,
                universe_size=universe_size,
                checked_symbols=checked_symbols,
                buy_signals=buy_signals,
                sell_signals=sell_signals,
                orders=orders,
                rejections="\n".join(rejections),
                cash=cash,
                equity=equity,
            )
        )
        session.commit()


def record_trade(session_factory, position: PositionRow, exit_order: OrderResult, exit_reason: str) -> None:
    """진입(position)~청산(exit_order)을 하나로 묶어 손익까지 계산해
    trades 테이블에 남긴다. 이 테이블이 "이 가설이 실전에서 어떻게
    됐는지"를 판단하는 근거 데이터가 된다. 사람이 보든, 나중에 AI가
    보든 마찬가지다."""
    entry_value = position.quantity * position.entry_price
    exit_value = exit_order.quantity * exit_order.price
    pnl_amount = exit_value - entry_value
    pnl_pct = (exit_order.price / position.entry_price - 1) * 100 if position.entry_price > 0 else 0.0

    with session_factory() as session:
        session.add(
            TradeRow(
                symbol=position.symbol,
                strategy_key=position.strategy_key,
                quantity=position.quantity,
                entry_price=position.entry_price,
                exit_price=exit_order.price,
                entry_reason=position.entry_reason,
                exit_reason=exit_reason,
                pnl_amount=pnl_amount,
                pnl_pct=pnl_pct,
                is_paper=exit_order.is_paper,
                entered_at=position.entered_at,
                exited_at=datetime.utcnow(),  # noqa: DTZ003 (기록용, tz 무관)
            )
        )
        session.commit()


def load_engine_state(session_factory, initial_cash: float) -> tuple[float, float]:
    """(cash, day_start_equity)를 돌려준다. day_start_equity는 '직전 실행이
    끝난 시점의 평가금액' 기준점: 상태가 아예 없는 첫 실행이면 남아 있는
    포지션을 진입가로 어림잡아 기준을 만든다."""
    with session_factory() as session:
        cash_row = session.get(EngineStateRow, "cash")
        equity_row = session.get(EngineStateRow, "day_start_equity")

        cash = float(cash_row.value) if cash_row else initial_cash
        if equity_row is not None:
            day_start_equity = float(equity_row.value)
        else:
            positions_value = sum(p.quantity * p.entry_price for p in session.query(PositionRow).all())
            day_start_equity = cash + positions_value
        return cash, day_start_equity


def save_engine_state(session_factory, cash: float, day_start_equity: float) -> None:
    with session_factory() as session:
        for key, value in (("cash", str(cash)), ("day_start_equity", str(day_start_equity))):
            row = session.get(EngineStateRow, key)
            if row is None:
                session.add(EngineStateRow(key=key, value=value))
            else:
                row.value = value
        session.commit()
