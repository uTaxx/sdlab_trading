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
from muwon.risk.exits import (
    atr_series,
    evaluate_exit,
    보유만료글,
    보유상한,
    익절기준,
    최소보유일,
)
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
    #: 이 종목을 산 전략. **청산은 이것을 따른다**(2026-09-02).
    #:
    #: 전에는 청산이 지금 걸린 전략을 봤다. 그래서 전략을 바꾸면 이미 들고
    #: 있던 종목의 보유 기간과 익절선이 발밑에서 바뀌었다. 살 때 이미
    #: "며칠 들고 언제 판다"가 정해져 있었으므로 그것을 따르는 것이 맞다.
    #:
    #: None이면 실행 내내 전략이 하나였다는 뜻이라 엔진의 전략을 쓴다.
    전략: object | None = None


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
        섹터표: dict[str, str] | None = None,
        섹터상한: int = 0,
        섹터상한셈: str = "하루후보",
        점수순: bool = False,
        결제일수: int = 0,
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
        # 아니다. 지금까지의 5년 성적이 실거래와 다른 규칙의 성적이었다.
        #
        # 그리고 수익의 70~92%가 밤사이에 났으므로(설계안 §26), 매수를 하루
        # 늦추면 **밤 하나를 잃는다.** 청산 쪽과 부호가 반대다.
        self._entry_at_open = entry_at_open

        # 아래 넷은 **실거래가 하는 일인데 백테스트가 안 하던 것**이다.
        # 기본값은 옛 동작 그대로다. 켜면 지금까지 낸 전략 평가 결과·기간
        # 검증 숫자와 비교가 안 되므로, 켜는 쪽에서 그 사실을 적어야 한다.
        #
        # 종목코드 → 섹터코드. 없으면 섹터 상한을 안 건다.
        self._섹터표 = 섹터표 or {}
        #: 한 섹터에서 몇 종목까지. 0 이하면 제한 없음(시트의 max_per_sector와
        #: 같은 뜻이다).
        #:
        #: 음수를 그대로 두면 상한이 -1인 셈이 되어 **한 종목도 안 산다.**
        #: 수익률 0%가 나오는데 화면에는 그것이 "이 전략은 아무것도 못
        #: 번다"로 보인다. 조용히 틀리는 쪽이라 여기서 막는다.
        self._섹터상한 = max(0, int(섹터상한 or 0))
        #: "하루후보"는 그날 새로 사는 것만 센다. 실거래가 그렇게 한다.
        #: "보유전체"는 이미 들고 있는 것까지 세는 진짜 보유 한도다.
        self._섹터상한셈 = 섹터상한셈
        #: 자리가 모자랄 때 신호 점수가 높은 것부터 살 것인가.
        self._점수순 = bool(점수순)
        #: 판 돈이 며칠 뒤에 쓸 수 있게 되나. **한국 주식은 2거래일이다(T+2).**
        #:
        #: ## 왜 기본값이 0인가
        #:
        #: 0은 판 즉시 그 돈으로 살 수 있다는 뜻이고, 지금까지의 모든 백테스트
        #: 숫자가 그 가정 위에 있다. 기본값을 2로 바꾸면 전략 평가 결과와
        #: 기간 검증 숫자가 전부 다른 조건의 값이 된다. 그래서 켜는 쪽에서
        #: 그 사실을 적도록 두고, 기본값은 옛 동작으로 남긴다.
        #:
        #: ## 왜 필요한가
        #:
        #: 판 돈을 즉시 다시 굴리는 것은 실제로 못 하는 일이다. 회전이 빠른
        #: 전략일수록 백테스트가 유리하게 나온다. 갭 상승 따라가기처럼 매일
        #: 사고파는 전략은 매일 매도 대금을 그날 재투자하는 것으로 계산된다.
        #:
        #: **평가금액에는 들어간다.** 돈이 사라진 것이 아니라 아직 못 쓸 뿐이다.
        #: 빼고 세면 파는 날마다 자산이 뚝 떨어졌다가 이틀 뒤 돌아온다.
        self._결제일수 = max(0, int(결제일수 or 0))

    def run(
        self, price_histories: dict[str, pd.DataFrame], trade_from: date | None = None
    ) -> BacktestResult:
        """price_histories: {symbol: DataFrame[trade_date, open, high, low, close, volume]}

        trade_from을 주면 그 날짜부터만 매매하고, 그 이전 구간은 지표 예열에만
        쓴다(신호는 전체 히스토리로 계산되므로 이동평균·RSI가 충분히 채워진
        상태로 매매를 시작한다). 기간을 잘라 여러 구간에서 검증할 때 필요하다.
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
        all_dates = sorted({d for df in enriched.values() for d in df.index})

        cash = self._initial_cash
        positions: dict[str, OpenPosition] = {}
        closed_trades: list[ClosedTrade] = []
        equity_curve_rows: list[dict] = []
        day_start_equity = self._initial_cash

        # 어제 정했지만 아직 체결 안 된 주문. *_at_open일 때만 채워진다.
        pending_exits: dict[str, str] = {}
        pending_entries: dict[str, str] = {}

        # 판 돈이 언제 풀리나. {풀리는 날의 순번: 금액}
        # 거래일 순번으로 센다. 달력 날짜로 세면 연휴에 하루씩 어긋난다.
        날짜순번 = {d: i for i, d in enumerate(all_dates)}
        결제대기: dict[int, float] = {}

        def 입금(금액: float, 판날) -> float:
            """매도 대금. 결제일수가 0이면 그대로 현금이 되고, 아니면 잠긴다.

            돌려주는 값은 **오늘 현금에 더할 몫**이다."""
            if self._결제일수 <= 0:
                return 금액
            푸는날 = 날짜순번[판날] + self._결제일수
            결제대기[푸는날] = 결제대기.get(푸는날, 0.0) + 금액
            return 0.0

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
                continue  # 지표 예열 구간: 매매도 평가금액 기록도 하지 않는다

            # 결제가 끝난 돈을 오늘 현금에 넣는다. 파는 것보다 먼저다.
            # 오늘 푼 돈으로 오늘 살 수 있어야 실제와 같다.
            풀린것 = 결제대기.pop(날짜순번[current_date], 0.0)
            cash += 풀린것

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

            ctx = MarketContext(
                as_of=current_date,
                histories=price_histories,
                held=frozenset(positions),
            )
            signals_today: dict[str, list] = {}
            for signal in self._strategy.evaluate(ctx):
                signals_today.setdefault(signal.symbol, []).append(signal)
            # 산 전략이 지금 전략과 다를 때만 따로 계산한다. 하루에 한 번씩
            # 캐시를 비운다. 안 비우면 어제 신호로 오늘 판다.
            self._청산신호모음 = {}

            # 며칠까지 들고 있을 것인가는 **종목마다 다시 묻는다.**
            #
            # 답은 `risk/exits.보유상한()`이 낸다. 전에는 여기서 전략의 값을
            # 곧장 읽어서 기초설정의 보유기간을 통째로 무시했다. 실거래
            # 엔진도 보유상한()을 쓰므로, 기초설정에 보유기간을 넣어 두면
            # 백테스트와 실거래가 서로 다른 규칙으로 돌고 아무것도 빨개지지
            # 않았다.
            #
            # 그리고 **산 전략에게 묻는다**(2026-09-02에 바꿈). 전에는 실행
            # 중간에 전략이 바뀌면 이미 들고 있던 종목의 보유기간도 같이
            # 바뀌었다. 실거래가 그렇게 돌고 있어서 그대로 흉내 낸 것인데,
            # 살 때 이미 정해져 있던 것이 나중에 바뀌는 것이 잘못이었다.
            # 양쪽을 같이 고쳤다. 그래서 이 값은 청산 루프 안에서 종목마다
            # 구한다.

            # 0) 어제 정한 주문을 오늘 **시가**에 체결한다. 청산이 먼저다.
            # 판 돈으로 사야 하기 때문이다(실거래에서도 같은 순서).
            # 판단(어제 종가)과 체결(오늘 시가)을 하루 벌려 두는 것이 이
            # 옵션의 전부다. 오늘 거래가 없는 종목은 그대로 두고 다음 날 다시
            # 시도한다. 임의로 종가에 팔아 버리면 옵션의 뜻이 사라진다.
            for symbol, reason in list(pending_exits.items()):
                if symbol not in positions:
                    del pending_exits[symbol]
                    continue
                if symbol not in opens_today:
                    continue
                cash += 입금(self._close_position(
                    positions[symbol], float(opens_today[symbol]), current_date,
                    reason, closed_trades
                ), current_date)
                del positions[symbol]
                del pending_exits[symbol]

            # 0-b) 어제 정한 매수를 오늘 시가에 체결한다.
            #
            # 수량은 **체결 시점의** 평가금액으로 정한다. 어제 정해 두면
            # 밤사이 값이 변한 뒤에 옛 금액으로 사게 된다. 실거래에서도
            # 아침에 계좌를 보고 수량을 정한다.
            if pending_entries:
                시가평가금액 = cash + sum(결제대기.values()) + sum(
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
                # 며칠 묵은 신호로 사게 된다. 실거래에서도 당일 주문이다.
                pending_entries.clear()

            # 1) 청산: 손절 → 보유기간 초과 → 전략 매도 신호
            for symbol in list(positions.keys()):
                if symbol not in closes_today or symbol in pending_exits:
                    continue
                price = float(closes_today[symbol])
                position = positions[symbol]
                exit_reason = None

                # 청산은 **산 전략**을 따른다. 실행 내내 전략이 하나면
                # 지금 전략과 같은 것이라 값이 달라지지 않는다.
                산전략 = position.전략 or self._strategy
                정책 = self._risk_manager.get_policy()
                max_holding_days = 보유상한(산전략, 정책)
                최소일 = 최소보유일(산전략, 정책)
                들고있던일 = bars_since(
                    trade_dates_by_symbol.get(symbol, []),
                    position.entry_date, current_date,
                )
                stop = evaluate_exit(
                    entry_price=position.entry_price,
                    entry_date=position.entry_date,
                    current_price=price,
                    as_of=current_date,
                    policy=정책,
                    atr=atr_by_symbol.get(symbol),
                    history=price_histories.get(symbol),
                    익절=익절기준(산전략, 정책),
                    최소보유일=최소일,
                    들고있던일=들고있던일,
                )
                if stop.should_exit:
                    exit_reason = stop.reason
                elif max_holding_days is not None and 들고있던일 >= max_holding_days:
                    # 상한을 최소 보유기간보다 먼저 본다. 둘이 어긋나게
                    # 적혀도 상한을 넘겨 들고 있지 않게 하려는 것이다.
                    exit_reason = 보유만료글(max_holding_days, 들고있던일)
                elif 최소일 > 0 and 들고있던일 < 최소일:
                    # 최소 보유기간 안이다. 손절은 위에서 봤고 여기서부터는
                    # 안 판다. 전략의 매도 신호도 이 기간에는 안 본다.
                    pass
                else:
                    # 매도 신호도 산 전략이 낸 것만 본다. 지금 전략의 신호를
                    # 섞으면 한 종목에 두 전략이 걸려 왜 팔렸는지 설명이 안 된다.
                    묶음 = self._청산신호(산전략, ctx, signals_today)
                    for signal in 묶음.get(symbol, []):
                        if signal.signal_type == SignalType.SELL:
                            exit_reason = signal.reason
                            break

                if exit_reason is not None:
                    if self._exit_at_open:
                        # 오늘은 정하기만 한다. 체결은 내일 아침이다.
                        pending_exits[symbol] = exit_reason
                    else:
                        cash += 입금(self._close_position(
                            position, price, current_date, exit_reason, closed_trades
                        ), current_date)
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
            오늘후보 = []
            for symbol, price in closes_today.items():
                if symbol in positions or symbol in pending_entries:
                    continue
                buy_signals = [
                    s for s in signals_today.get(symbol, []) if s.signal_type == SignalType.BUY
                ]
                if not buy_signals:
                    continue
                # 실거래는 한 종목에 신호가 여럿이면 점수가 제일 높은 것을
                # 고른다(`propose_buys.py`). 여기서 첫 번째를 고르면 이유가
                # 다른 신호가 기록에 남는다.
                고른신호 = max(buy_signals, key=lambda s: s.score)
                오늘후보.append((symbol, float(price), 고른신호))

            오늘후보 = self._후보줄세우기(오늘후보, positions, pending_entries)

            for symbol, price, 신호 in 오늘후보:
                if self._entry_at_open:
                    # 오늘은 정하기만 한다. 체결도 수량 결정도 내일 아침이다.
                    pending_entries[symbol] = 신호.reason
                    continue

                cash -= self._open_position(
                    symbol,
                    price,
                    current_date,
                    신호.reason,
                    equity_after_exits,
                    daily_pnl_pct,
                    len(positions),
                    cash,
                    positions,
                )

            마지막종가.update({ㅅ: float(ㄱ) for ㅅ, ㄱ in closes_today.items()})
            # 아직 결제 안 된 매도 대금도 내 돈이다. 못 쓸 뿐이다. 빼고 세면
            # 파는 날마다 자산이 뚝 떨어졌다가 이틀 뒤 돌아온다.
            잠긴돈 = sum(결제대기.values())
            equity = cash + 잠긴돈 + sum(
                positions[s].quantity * 값(s, closes_today)
                for s in positions
            )
            # 보유 종목 수와 현금도 남긴다. 노출도(자금을 얼마나 굴렸나)와
            # 회전율을 나중에 계산하려면 이 두 값이 있어야 한다. 수익률만
            # 남기면 "적게 굴려서 적게 벌었는지"를 구분할 수 없다.
            equity_curve_rows.append(
                {
                    "trade_date": current_date,
                    "equity": equity,
                    "cash": cash,
                    # 판 돈 중 아직 결제가 안 끝난 몫. 평가금액에는 들어
                    # 있지만 현금은 아니다. 이 칸이 없으면 부르는 쪽이
                    # `equity - cash`를 보유 평가액으로 읽는데, 그러면 잠긴
                    # 돈이 통째로 "아직 안 판 수익"으로 잡힌다.
                    "결제대기": 잠긴돈,
                    "positions": len(positions),
                }
            )
            day_start_equity = equity

        equity_curve = pd.DataFrame(equity_curve_rows)
        return BacktestResult(
            equity_curve=equity_curve, closed_trades=closed_trades, final_positions=positions
        )

    def _후보줄세우기(self, 후보들, positions, pending_entries):
        """오늘 살 것을 실거래와 같은 순서로 줄 세우고, 섹터 상한으로 자른다.

        ## 왜 순서가 결과를 바꾸나

        자리는 여덟인데 신호가 열 개면 둘은 못 산다. 실거래는 신호 점수가
        높은 순으로 세워서 위에서부터 사고(`propose_buys.py`), 여기서는
        시세를 받은 순서대로 샀다. **같은 전략인데 다른 종목을 사고 있었다.**

        기본값은 옛 동작 그대로다. 지금까지 낸 전략 평가 결과와 기간 검증
        숫자가 전부 그 위에 있어서, 기본값을 바꾸면 과거 결과와 비교가
        안 된다. 실거래와 같은 규칙으로 재려면 켜서 쓴다."""
        if self._점수순:
            후보들 = sorted(후보들, key=lambda ㅌ: -ㅌ[2].score)

        if not self._섹터상한 or not self._섹터표:
            return 후보들

        # **실거래는 그날 새로 사는 것만 센다.** 이미 들고 있는 것은 안
        # 센다(`sector/selection.cap_per_sector`가 0부터 센다). 그래서 반도체
        # 셋을 들고 있어도 오늘 반도체 셋을 더 살 수 있다. `보유전체`는 그
        # 규칙이 아니라 진짜 보유 한도로 쟀을 때를 보는 쪽이다.
        센것: dict[str, int] = {}
        if self._섹터상한셈 == "보유전체":
            for ㅅ in list(positions) + list(pending_entries):
                키 = self._섹터표.get(ㅅ, "")
                센것[키] = 센것.get(키, 0) + 1

        남김 = []
        for ㅌ in 후보들:
            키 = self._섹터표.get(ㅌ[0], "")
            if 센것.get(키, 0) >= self._섹터상한:
                continue
            센것[키] = 센것.get(키, 0) + 1
            남김.append(ㅌ)
        return 남김

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
        때문이다. 그런 어긋남은 화면에 아무 표시도 남기지 않는다."""
        policy = self._risk_manager.get_policy()
        decision = self._risk_manager.check_new_position(
            proposed_weight=policy.max_position_weight,
            current_open_positions=open_positions,
            daily_pnl_pct=daily_pnl_pct,
        )
        if not decision.approved:
            return 0.0

        # 체결가는 기준가가 아니다. 호가가 벌어져 있고 시장가로 치면
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
            전략=self._지금전략(),
        )
        return cost

    def _지금전략(self):
        """지금 신호를 내고 있는 속 전략.

        갈아타기 시험에서는 껍데기 하나가 날마다 다른 전략에 넘긴다. 껍데기를
        보유 종목에 적어 두면 날마다 답이 바뀌어서, 청산이 산 전략을 따르게
        한 뜻이 사라진다."""
        return getattr(self._strategy, "오늘전략", None) or self._strategy

    def _청산신호(self, 산전략, ctx, 오늘신호: dict) -> dict:
        """산 전략이 오늘 낸 신호. 지금 전략과 같으면 이미 계산한 것을 쓴다."""
        if 산전략 is self._strategy or 산전략 is self._지금전략():
            return 오늘신호
        열쇠 = id(산전략)
        묶음 = self._청산신호모음.get(열쇠)
        if 묶음 is None:
            묶음 = {}
            for ㅅ in 산전략.evaluate(ctx):
                묶음.setdefault(ㅅ.symbol, []).append(ㅅ)
            self._청산신호모음[열쇠] = 묶음
        return 묶음

    def _close_position(
        self,
        position: OpenPosition,
        market_price: float,
        exit_date: date,
        exit_reason: str,
        closed_trades: list[ClosedTrade],
    ) -> float:
        """market_price는 체결 기준가다. 지금 방식이면 그날 **종가**,
        exit_at_open이면 그날 **시가**. 실제 체결가는 그보다 불리하다.
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
