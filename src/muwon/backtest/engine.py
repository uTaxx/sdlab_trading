"""포트폴리오 단위 백테스트 엔진.

여러 종목을 하나의 계좌(현금+포지션)로 묶어서 하루 단위로 시뮬레이션한다.
종목별로 따로 백테스트하지 않는 이유는, RiskManager가 검증하는 규칙(종목당
비중/동시보유종목수/일일손실한도)이 애초에 포트폴리오 전체를 보는 값이라
개별 종목 단위로는 의미가 없기 때문이다. 실거래 실행기(execution/)가 훗날
따라야 할 흐름(신호 생성 → 리스크 매니저 승인 → 주문)과 최대한 같은 순서로
짜서, 백테스트와 실거래 로직이 어긋나지 않게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from muwon.backtest.costs import TransactionCosts
from muwon.domain.interfaces import Strategy
from muwon.domain.types import SignalType
from muwon.indicators.technical import add_indicators
from muwon.risk.exits import atr_series, evaluate_exit
from muwon.risk.manager import RiskManager
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
    bars_since,
)


@dataclass
class OpenPosition:
    symbol: str
    quantity: int
    entry_price: float
    entry_date: date
    entry_reason: str = ""


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl_pct: float
    pnl_amount: float
    exit_reason: str
    entry_reason: str = ""


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame  # columns: trade_date, equity
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    final_positions: dict[str, OpenPosition] = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve["equity"].iloc[-1]) if len(self.equity_curve) else 0.0

    @property
    def total_return_pct(self) -> float:
        if len(self.equity_curve) < 1:
            return 0.0
        start = float(self.equity_curve["equity"].iloc[0])
        end = float(self.equity_curve["equity"].iloc[-1])
        return (end / start - 1) * 100 if start > 0 else 0.0

    @property
    def max_drawdown_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        equity = self.equity_curve["equity"]
        running_peak = equity.cummax()
        drawdown = (equity - running_peak) / running_peak
        return float(drawdown.min() * 100)

    @property
    def win_rate_pct(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl_amount > 0)
        return wins / len(self.closed_trades) * 100

    @property
    def num_trades(self) -> int:
        return len(self.closed_trades)


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy | PortfolioStrategy,
        risk_manager: RiskManager,
        costs: TransactionCosts | None = None,
        initial_cash: float = 10_000_000.0,
        exit_at_open: bool = False,
        entry_at_open: bool = False,
    ):
        self._strategy = as_portfolio_strategy(strategy)
        self._risk_manager = risk_manager
        self._costs = costs or TransactionCosts()
        self._initial_cash = initial_cash
        # 청산을 **다음 날 시가**에 체결할 것인가.
        #
        # 기본값(False)은 판단한 그날 종가에 판다. 그런데 판단에 그날 종가가
        # 필요하므로, 종가를 보고 그 종가에 판다는 것은 실제로는 하기 어려운
        # 일이다. 실거래는 장 마감 뒤에 정하고 다음 날 아침에 주문을 낸다.
        #
        # 게다가 수익의 70~92%가 밤사이(종가→시가)에 났다는 것을 쟀다
        # (설계안 §26). 종가에 파는 지금 방식은 **마지막 밤을 버리고 있다.**
        # 이 옵션은 그 둘을 한 번에 확인하기 위한 것이다.
        self._exit_at_open = exit_at_open
        # 매수를 **다음 날 시가**에 체결할 것인가.
        #
        # 실거래 엔진은 어제까지의 완성된 일봉으로 판단하고 개장 직후에
        # 시장가 주문을 낸다. 즉 **실거래는 이미 이렇게 하고 있다.** 기본값
        # (False)은 신호 난 그날 종가에 사는데, 그건 실거래가 하는 일이
        # 아니다 — 지금까지의 5년 성적이 실거래와 다른 규칙의 성적이었다.
        #
        # 그리고 수익의 70~92%가 밤사이에 났으므로(설계안 §26), 매수를 하루
        # 늦추면 **밤 하나를 잃는다.** 청산 쪽과 부호가 반대다.
        self._entry_at_open = entry_at_open

    def run(
        self, price_histories: dict[str, pd.DataFrame], trade_from: date | None = None
    ) -> BacktestResult:
        """price_histories: {symbol: DataFrame[trade_date, open, high, low, close, volume]}

        trade_from을 주면 그 날짜부터만 매매하고, 그 이전 구간은 지표 예열에만
        쓴다(신호는 전체 히스토리로 계산되므로 이동평균·RSI가 충분히 채워진
        상태로 매매를 시작한다). 기간을 잘라 여러 구간에서 검증할 때 필요하다 —
        예열 없이 잘라 넣으면 각 구간 초반의 지표가 NaN이라 신호가 안 나와
        짧은 구간일수록 결과가 과소평가된다."""
        enriched = {
            symbol: add_indicators(df).set_index("trade_date")
            for symbol, df in price_histories.items()
            if len(df) > 0
        }
        self._strategy.prepare(price_histories)
        trade_dates_by_symbol = {symbol: list(df.index) for symbol, df in enriched.items()}
        policy0 = self._risk_manager.get_policy()
        atr_by_symbol = (
            {s: atr_series(df, policy0.atr_window) for s, df in price_histories.items()}
            if (policy0.atr_stop_enabled or policy0.trailing_stop_enabled)
            else {}
        )
        max_holding_days = self._strategy.max_holding_days

        all_dates = sorted({d for df in enriched.values() for d in df.index})

        cash = self._initial_cash
        positions: dict[str, OpenPosition] = {}
        closed_trades: list[ClosedTrade] = []
        equity_curve_rows: list[dict] = []
        day_start_equity = self._initial_cash

        # 어제 정했지만 아직 체결 안 된 주문. *_at_open일 때만 채워진다.
        pending_exits: dict[str, str] = {}
        pending_entries: dict[str, str] = {}

        # **마지막으로 본 종가.** 들고 있는 종목의 그날 시세가 없을 때 쓴다.
        #
        # 전에는 시세가 없으면 평가금액 계산에서 그 종목을 통째로 뺐다.
        # 그러면 그 종목이 0원이 된다. 종목마다 상장일이 다르면 어떤 날은
        # 한 종목만 거래일이라, 그날 들고 있던 나머지가 전부 0원이 되고
        # 계좌가 하루 만에 90% 넘게 줄어든 것으로 찍혔다. 다음 날 되돌아와서
        # 총수익률은 멀쩡해 보이고 최대 하락폭만 말이 안 되게 나왔다
        # (2026-08-31에 63종목으로 재다가 드러남).
        마지막종가: dict[str, float] = {}

        def 값(symbol: str, 오늘시세: dict) -> float:
            """평가에 쓸 값. 오늘 시세가 없으면 마지막으로 본 값을 쓴다."""
            if symbol in 오늘시세:
                return float(오늘시세[symbol])
            return 마지막종가.get(symbol, positions[symbol].entry_price)

        for current_date in all_dates:
            if trade_from is not None and current_date < trade_from:
                continue  # 지표 예열 구간 — 매매도 평가금액 기록도 하지 않는다

            opens_today = {
                symbol: df.loc[current_date, "open"]
                for symbol, df in enriched.items()
                if current_date in df.index
            }
            closes_today = {
                symbol: df.loc[current_date, "close"]
                for symbol, df in enriched.items()
                if current_date in df.index
            }

            signals_today: dict[str, list] = {}
            for signal in self._strategy.evaluate(
                MarketContext(
                    as_of=current_date,
                    histories=price_histories,
                    held=frozenset(positions),
                )
            ):
                signals_today.setdefault(signal.symbol, []).append(signal)

            # 0) 어제 정한 주문을 오늘 **시가**에 체결한다. 청산이 먼저다 —
            # 판 돈으로 사야 하기 때문이다(실거래에서도 같은 순서).
            # 판단(어제 종가)과 체결(오늘 시가)을 하루 벌려 두는 것이 이
            # 옵션의 전부다. 오늘 거래가 없는 종목은 그대로 두고 다음 날 다시
            # 시도한다 — 임의로 종가에 팔아 버리면 옵션의 뜻이 사라진다.
            for symbol, reason in list(pending_exits.items()):
                if symbol not in positions:
                    del pending_exits[symbol]
                    continue
                if symbol not in opens_today:
                    continue
                cash += self._close_position(
                    positions[symbol], float(opens_today[symbol]), current_date, reason, closed_trades
                )
                del positions[symbol]
                del pending_exits[symbol]

            # 0-b) 어제 정한 매수를 오늘 시가에 체결한다.
            #
            # 수량은 **체결 시점의** 평가금액으로 정한다. 어제 정해 두면
            # 밤사이 값이 변한 뒤에 옛 금액으로 사게 된다. 실거래에서도
            # 아침에 계좌를 보고 수량을 정한다.
            if pending_entries:
                시가평가금액 = cash + sum(
                    positions[s].quantity * 값(s, opens_today)
                    for s in positions
                )
                # 개장 시점에 알 수 있는 손익은 밤사이 움직임뿐이다.
                밤사이손익 = (
                    (시가평가금액 - day_start_equity) / day_start_equity
                    if day_start_equity > 0
                    else 0.0
                )
                for symbol, reason in list(pending_entries.items()):
                    if symbol in opens_today and symbol not in positions:
                        cash -= self._open_position(
                            symbol,
                            float(opens_today[symbol]),
                            current_date,
                            reason,
                            시가평가금액,
                            밤사이손익,
                            len(positions),
                            cash,
                            positions,
                        )
                # 어제 신호는 어제 것이다. 오늘 못 산 것을 계속 들고 있으면
                # 며칠 묵은 신호로 사게 된다 — 실거래에서도 당일 주문이다.
                pending_entries.clear()

            # 1) 청산: 손절 → 보유기간 초과 → 전략 매도 신호
            for symbol in list(positions.keys()):
                if symbol not in closes_today or symbol in pending_exits:
                    continue
                price = float(closes_today[symbol])
                position = positions[symbol]
                exit_reason = None

                stop = evaluate_exit(
                    entry_price=position.entry_price,
                    entry_date=position.entry_date,
                    current_price=price,
                    as_of=current_date,
                    policy=self._risk_manager.get_policy(),
                    atr=atr_by_symbol.get(symbol),
                    history=price_histories.get(symbol),
                )
                if stop.should_exit:
                    exit_reason = stop.reason
                elif max_holding_days is not None and bars_since(
                    trade_dates_by_symbol.get(symbol, []), position.entry_date, current_date
                ) >= max_holding_days:
                    exit_reason = f"보유 {max_holding_days}일 경과 청산"
                else:
                    for signal in signals_today.get(symbol, []):
                        if signal.signal_type == SignalType.SELL:
                            exit_reason = signal.reason
                            break

                if exit_reason is not None:
                    if self._exit_at_open:
                        # 오늘은 정하기만 한다. 체결은 내일 아침이다.
                        pending_exits[symbol] = exit_reason
                    else:
                        cash += self._close_position(
                            position, price, current_date, exit_reason, closed_trades
                        )
                        del positions[symbol]

            # 2) 이 시점 평가금액 → 오늘 손익률 계산
            equity_after_exits = cash + sum(
                positions[s].quantity * 값(s, closes_today)
                for s in positions
            )
            daily_pnl_pct = (
                (equity_after_exits - day_start_equity) / day_start_equity
                if day_start_equity > 0
                else 0.0
            )

            # 3) 진입: 리스크 매니저 승인을 받은 매수 신호만 실행
            for symbol, price in closes_today.items():
                if symbol in positions or symbol in pending_entries:
                    continue
                buy_signals = [
                    s for s in signals_today.get(symbol, []) if s.signal_type == SignalType.BUY
                ]
                if not buy_signals:
                    continue

                if self._entry_at_open:
                    # 오늘은 정하기만 한다. 체결도 수량 결정도 내일 아침이다.
                    pending_entries[symbol] = buy_signals[0].reason
                    continue

                cash -= self._open_position(
                    symbol,
                    float(price),
                    current_date,
                    buy_signals[0].reason,
                    equity_after_exits,
                    daily_pnl_pct,
                    len(positions),
                    cash,
                    positions,
                )

            마지막종가.update({ㅅ: float(ㄱ) for ㅅ, ㄱ in closes_today.items()})
            equity = cash + sum(
                positions[s].quantity * 값(s, closes_today)
                for s in positions
            )
            # 보유 종목 수와 현금도 남긴다 — 노출도(자금을 얼마나 굴렸나)와
            # 회전율을 나중에 계산하려면 이 두 값이 있어야 한다. 수익률만
            # 남기면 "적게 굴려서 적게 벌었는지"를 구분할 수 없다.
            equity_curve_rows.append(
                {
                    "trade_date": current_date,
                    "equity": equity,
                    "cash": cash,
                    "positions": len(positions),
                }
            )
            day_start_equity = equity

        equity_curve = pd.DataFrame(equity_curve_rows)
        return BacktestResult(
            equity_curve=equity_curve, closed_trades=closed_trades, final_positions=positions
        )

    def _open_position(
        self,
        symbol: str,
        market_price: float,
        entry_date: date,
        reason: str,
        equity: float,
        daily_pnl_pct: float,
        open_positions: int,
        cash: float,
        positions: dict[str, OpenPosition],
    ) -> float:
        """리스크 승인 → 수량 결정 → 매수. 쓴 현금을 돌려준다(못 사면 0).

        종가에 사든 시가에 사든 여기서 하는 일은 같다. 한 군데로 모으는
        이유는, 두 벌로 두면 리스크 규칙이 한쪽에만 반영되는 일이 생기기
        때문이다 — 그런 어긋남은 화면에 아무 표시도 남기지 않는다."""
        policy = self._risk_manager.get_policy()
        decision = self._risk_manager.check_new_position(
            proposed_weight=policy.max_position_weight,
            current_open_positions=open_positions,
            daily_pnl_pct=daily_pnl_pct,
        )
        if not decision.approved:
            return 0.0

        # 체결가는 기준가가 아니다 — 호가가 벌어져 있고 시장가로 치면
        # 반대 호가를 먹고 들어간다. 사는 쪽은 기준가보다 비싸게 잡힌다.
        fill = self._costs.buy_price(market_price)
        target_value = equity * policy.max_position_weight
        quantity = int(target_value / (fill * (1 + self._costs.buy_fee_pct)))
        cost = quantity * fill * (1 + self._costs.buy_fee_pct)
        if quantity <= 0 or cost > cash:
            return 0.0

        positions[symbol] = OpenPosition(
            symbol=symbol,
            quantity=quantity,
            entry_price=fill,
            entry_date=entry_date,
            entry_reason=reason,
        )
        return cost

    def _close_position(
        self,
        position: OpenPosition,
        market_price: float,
        exit_date: date,
        exit_reason: str,
        closed_trades: list[ClosedTrade],
    ) -> float:
        """market_price는 체결 기준가다 — 지금 방식이면 그날 **종가**,
        exit_at_open이면 그날 **시가**. 실제 체결가는 그보다 불리하다 —
        파는 쪽은 더 싸게 잡힌다. 손익도 체결가 기준으로 계산해야 실제로
        계좌에 남는 돈과 맞는다."""
        exit_price = self._costs.sell_price(market_price)
        proceeds = position.quantity * exit_price * (1 - self._costs.total_sell_cost_pct)
        cost_basis = position.quantity * position.entry_price
        pnl_amount = proceeds - cost_basis
        pnl_pct = (exit_price / position.entry_price - 1) * 100 if position.entry_price > 0 else 0.0
        closed_trades.append(
            ClosedTrade(
                symbol=position.symbol,
                entry_date=position.entry_date,
                exit_date=exit_date,
                entry_price=position.entry_price,
                exit_price=exit_price,
                quantity=position.quantity,
                pnl_pct=pnl_pct,
                pnl_amount=pnl_amount,
                exit_reason=exit_reason,
                entry_reason=position.entry_reason,
            )
        )
        return proceeds
