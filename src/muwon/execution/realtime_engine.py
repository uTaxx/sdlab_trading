"""장중 실시간(체결 틱 단위) 매매 엔진.

TradingEngine(engine.py)이 "하루 한 번, 이미 확정된 종가로" 판단한다면,
이건 "장이 열려 있는 동안 계속 떠서, 분봉이 하나 마감될 때마다" 판단한다.
신호 판정 로직(Strategy)과 리스크 검증(RiskManager)은 완전히 동일하게
재사용한다 — 다른 건 "언제 판단하느냐"뿐이다. 틱 하나하나에 반응하지
않는 이유는 tick_aggregator.py 문서 참고.

TradingEngine은 프로세스가 매번 새로 뜨는 걸 전제로 매번 DB에서 상태를
읽고 쓰지만, 이건 장중 내내 살아있는 프로세스라 현금/당일기준평가금액을
메모리에 들고 있다가 거래가 있을 때만 DB에 반영한다(재시작 시 복구용).
포지션은 거래 즉시 DB에 반영해 다른 프로세스(대시보드)와 항상 최신 상태로
맞춘다.

engine.py와 이게 같은 DB를 공유하는 건 코드 재사용 때문이지, 배치
모드(GitHub Actions)와 장중 상시 모드(VPS)를 동시에 같은 계좌에 돌리라는
뜻이 아니다 — 운영 모드는 한 번에 하나만 고를 것."""

from __future__ import annotations

from collections import deque
from datetime import date, datetime

import pandas as pd
from sqlalchemy.orm import sessionmaker

from muwon.backtest.costs import TransactionCosts
from muwon.data.tick_aggregator import Bar, BarAggregator, Tick
from muwon.data.universe import Ticker
from muwon.db.models import PositionRow
from muwon.domain.interfaces import OrderExecutor, Strategy
from muwon.domain.types import OrderSide, SignalType
from muwon.execution import state_repository
from muwon.execution.engine import 매도알림, 매수알림
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.manager import RiskManager

BAR_HISTORY_LENGTH = 120  # sma60까지 계산하려면 최소 60개 봉 필요 — 여유있게 보관
MIN_BARS_FOR_SIGNAL = 20  # sma20조차 안 채워졌으면 신호 자체가 항상 NaN


class RealtimeTradingEngine:
    def __init__(
        self,
        strategy: Strategy,
        risk_manager: RiskManager,
        order_executor: OrderExecutor,
        notifier: TelegramNotifier,
        session_factory: sessionmaker,
        universe: list[Ticker],
        bar_seconds: int = 60,
        costs: TransactionCosts | None = None,
        initial_cash: float = 10_000_000.0,
    ):
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._order_executor = order_executor
        self._notifier = notifier
        self._session_factory = session_factory
        self._universe = {t.symbol: t for t in universe}
        self._aggregator = BarAggregator(bar_seconds=bar_seconds)
        self._bar_history: dict[str, deque[Bar]] = {
            symbol: deque(maxlen=BAR_HISTORY_LENGTH) for symbol in self._universe
        }
        self._costs = costs or TransactionCosts()
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._day_start_equity = initial_cash
        self._started = False

    def start(self) -> None:
        """장 시작 시 한 번 호출 — DB에서 현금/기준평가금액을 읽어와
        메모리에 올린다. 이후 거래가 있을 때마다 DB에도 즉시 반영되므로,
        중간에 프로세스가 죽어도 다음 start()에서 이어받는다."""
        self._cash, self._day_start_equity = state_repository.load_engine_state(
            self._session_factory, self._initial_cash
        )
        self._started = True

    def on_tick(self, tick: Tick) -> None:
        if not self._started:
            raise RuntimeError("start()를 먼저 호출해야 합니다.")
        if tick.symbol not in self._universe:
            return
        closed_bar = self._aggregator.add_tick(tick)
        if closed_bar is not None:
            self._on_bar_closed(closed_bar)

    def _on_bar_closed(self, bar: Bar) -> None:
        history = self._bar_history[bar.symbol]
        history.append(bar)
        if len(history) < MIN_BARS_FOR_SIGNAL:
            return

        df = self._bars_to_df(history)
        latest_bar_time = df["trade_date"].iloc[-1]
        signals = self._strategy.generate_signals(bar.symbol, df)
        latest_signals = [s for s in signals if s.trade_date == latest_bar_time]
        if not latest_signals:
            return

        self._act_on_signals(bar.symbol, bar.close, latest_signals)

    def _act_on_signals(self, symbol: str, price: float, signals: list) -> None:
        ticker = self._universe[symbol]
        positions = state_repository.load_positions(self._session_factory)
        position = positions.get(symbol)

        if position is not None:
            exit_reason = None
            if self._risk_manager.should_stop_loss(position.entry_price, price):
                exit_reason = "손절"
            else:
                for signal in signals:
                    if signal.signal_type == SignalType.SELL:
                        exit_reason = signal.reason
                        break
            if exit_reason is not None:
                self._sell(ticker, position, price, exit_reason)
            return

        buy_signals = [s for s in signals if s.signal_type == SignalType.BUY]
        if not buy_signals:
            return

        policy = self._risk_manager.get_policy()
        decision = self._risk_manager.check_new_position(
            proposed_weight=policy.max_position_weight,
            current_open_positions=len(positions),
            daily_pnl_pct=self._daily_pnl_pct(positions),
        )
        if not decision.approved:
            return  # 장중엔 거부될 때마다 알림을 보내면 스팸이 되므로 조용히 건너뜀

        equity = self._current_equity(positions)
        target_value = equity * policy.max_position_weight
        quantity = int(target_value / (price * (1 + self._costs.buy_fee_pct)))
        cost = quantity * price * (1 + self._costs.buy_fee_pct)
        if quantity <= 0 or cost > self._cash:
            return

        order = self._order_executor.submit_order(symbol, OrderSide.BUY, quantity, price)
        self._cash -= cost
        new_position = PositionRow(
            symbol=symbol,
            quantity=order.quantity,
            entry_price=order.price,
            entry_date=date.today(),  # noqa: DTZ011 — 기록용, tz 무관
            entered_at=datetime.utcnow(),  # noqa: DTZ003 — 기록용, tz 무관
            entry_reason=buy_signals[0].reason,
            strategy_key=self._strategy.name,
        )
        state_repository.save_position(self._session_factory, new_position)
        state_repository.record_order(self._session_factory, order, buy_signals[0].reason)
        state_repository.save_engine_state(self._session_factory, self._cash, self._day_start_equity)
        # 알림 모양은 하루 한 번 도는 엔진과 같은 것을 쓴다. 각자 쓰면 같은
        # 일을 두 말투로 알리게 되고, 하나를 고칠 때 나머지를 빠뜨린다.
        self._notifier.send(
            매수알림(
                ticker.name, symbol, order, buy_signals[0].reason,
                전략=self._strategy, 정책=self._risk_manager.get_policy(), 장중=True,
            )
        )

    def _sell(self, ticker: Ticker, position: PositionRow, price: float, reason: str) -> None:
        order = self._order_executor.submit_order(position.symbol, OrderSide.SELL, position.quantity, price)
        proceeds = order.quantity * order.price * (1 - self._costs.total_sell_cost_pct)
        self._cash += proceeds
        state_repository.delete_position(self._session_factory, position.symbol)
        state_repository.record_order(self._session_factory, order, reason)
        state_repository.record_trade(self._session_factory, position, order, reason)
        state_repository.save_engine_state(self._session_factory, self._cash, self._day_start_equity)
        self._notifier.send(
            매도알림(
                ticker.name, position.symbol, order, reason,
                진입가=position.entry_price, 진입일=position.entry_date,
                판날=date.today(),  # noqa: DTZ011 — 알림 표시용, tz 무관
                장중=True,
            )
        )

    def _current_equity(self, positions: dict[str, PositionRow]) -> float:
        # 장중 각 포지션의 실시간가를 다 들고 있어야 정확하지만, 리스크
        # 판단의 참고용 근사치라 진입가로 어림잡는 것으로 충분하다.
        return self._cash + sum(p.quantity * p.entry_price for p in positions.values())

    def _daily_pnl_pct(self, positions: dict[str, PositionRow]) -> float:
        equity = self._current_equity(positions)
        if self._day_start_equity <= 0:
            return 0.0
        return (equity - self._day_start_equity) / self._day_start_equity

    @staticmethod
    def _bars_to_df(history: deque[Bar]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "trade_date": [b.bar_start for b in history],
                "open": [b.open for b in history],
                "high": [b.high for b in history],
                "low": [b.low for b in history],
                "close": [b.close for b in history],
                "volume": [b.volume for b in history],
            }
        )
