"""실시간/모의투자용 신호→리스크→체결→알림→기록 1회 실행 엔진.

BacktestEngine과 최대한 같은 판단 로직(진입 조건, 손절, 비중 계산, 당일
손익 기준 서킷브레이커)을 쓰지만, 여긴 프로세스가 매번 새로 떠도 상태
(포지션·가상현금)가 이어져야 하므로 DB(positions/engine_state 테이블)에
둔다. run_once()는 하루에 한 번, **개장 직후** 호출하는 걸 전제로 한다.
전략이 일봉 기준이라 더 자주 실행할 이유가 없고, 판단은 어제까지의 완성된
일봉으로 하되 주문은 장이 열려 있을 때 넣어야 체결되기 때문이다. (장 마감
시각에 실행하면 판단할 데이터는 완전하지만 주문을 넣을 시장이 없다.)

가상현금(engine_state.cash)은 KIS 실계좌 잔고를 조회하는 대신 이 엔진이
자체적으로 기록하는 값이다. KIS 잔고조회 API 연동은 이 MVP 범위 밖이라,
KISOrderExecutor로 실제 주문을 넣더라도 리스크 계산(종목당 비중, 일일
손실한도)은 이 가상현금 기준으로 이뤄진다는 점에 주의할 것."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger
from sqlalchemy.orm import sessionmaker

from muwon.backtest.costs import TransactionCosts
from muwon.data.universe import Ticker
from muwon.db.models import PositionRow
from muwon.domain.interfaces import MarketDataSource, OrderExecutor, Strategy
from muwon.domain.types import OrderSide, SignalType
from muwon.execution import state_repository
from muwon.notify import notice_format as 모양
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.exits import (
    atr_series,
    evaluate_exit,
    보유만료글,
    보유상한,
    익절기준,
)
from muwon.risk.manager import RiskManager
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
    bars_since,
)

HISTORY_LOOKBACK_DAYS = 120  # 60일선 등 지표 계산에 필요한 최소 여유
KST = ZoneInfo("Asia/Seoul")  # 실행 서버가 UTC라도 '오늘'은 한국 장 기준이어야 한다


def today_kst() -> date:
    return datetime.now(KST).date()


def _돈(값: float) -> str:
    return f"{값:,.0f}원"


def _손절설명(사유: str) -> str:
    """청산 사유를 처음 보는 사람도 알 수 있게 풀어 준다.

    "손절" 한 마디로는 무슨 일이 일어난 것인지 알 수 없다. 이 시스템을
    처음 쓰는 사람에게는 **왜 내 주식이 저절로 팔렸나**가 제일 큰 질문이다."""
    풀이 = {
        "손절": "미리 정해 둔 한계보다 더 떨어져서 자동으로 팔았습니다",
        "보유기간 만료": "전략이 정한 보유 기간이 끝나 팔았습니다",
        "매도 신호": "전략이 팔 때가 됐다고 판단했습니다",
        "청산": "보유를 정리했습니다",
    }
    for 열쇠, 말 in 풀이.items():
        if 사유.startswith(열쇠):
            return f"{사유}. {말}"
    if 사유.startswith("ATR 손절"):
        return f"{사유}. 그 종목이 하루에 보통 움직이는 폭보다 더 떨어져서 자동으로 팔았습니다"
    if 사유.startswith("익절"):
        return f"{사유}. 목표한 만큼 올라서 이익을 확정했습니다"
    if 사유.startswith("트레일링"):
        return f"{사유}. 고점에서 밀려나 이익을 지키려고 팔았습니다"
    return 사유


def 전략이름(전략, *, 파는쪽: bool = False) -> str:
    """전략 객체에서 사람이 읽을 한글 이름을 꺼낸다.

    기록에 남는 `name`은 영문 키다. 알림에 그대로 쓰면 화면에 영문이 뜬다.
    등록 안 된 이름(묶은 것, `adopted` 같은 것)이면 그대로 돌려준다.

    매수와 매도를 따로 걸었으면 **어느 쪽 이름인지가 중요하다.** 사는
    알림에 파는 쪽 이름이 뜨면 왜 샀는지 설명이 안 된다."""
    쪽 = getattr(전략, "매도쪽" if 파는쪽 else "매수쪽", 전략)
    이름 = getattr(쪽, "name", "") or ""
    try:
        from muwon.strategy.registry import get_definition

        return get_definition(이름).화면이름
    except Exception:  # noqa: BLE001 (이름을 못 찾는다고 알림이 안 가면 안 된다)
        return 이름


def 매도규칙(전략, 정책, 산값: float) -> list[str]:
    """이 종목이 어떤 조건에서 팔리는지 전부.

    **파는 조건은 어디에 설정돼 있든 한자리에 모여 있어야 한다.** 손절은
    리스크 정책에, 보유 기간과 매도 신호는 전략에 있다. 흩어 두면 "매도
    전략은 손절밖에 없냐"는 오해가 생긴다. 실제로 받은 질문이다.

    엔진이 검사하는 순서대로 적는다."""
    줄들: list[str] = []

    손절비율 = getattr(정책, "stop_loss_pct", None)
    if getattr(정책, "atr_stop_enabled", False):
        줄들.append(
            f"변동성 손절 (하루 평균 변동폭 ATR {정책.atr_window}일의 "
            f"{정책.atr_stop_multiple:g}배만큼 빠지면 매도)"
        )
    elif 손절비율:
        손절가 = 산값 * (1 + 손절비율)
        줄들.append(f"{손절비율:.0%} 손절 ({_돈(손절가)}에서 매도)")
    # 익절도 여기 적는다. 켜 뒀는데 알림에 안 적히면 "왜 갑자기 팔렸지"가 된다.
    익절 = getattr(정책, "take_profit_pct", 0.0) or 0.0
    if 익절 > 0:
        줄들.append(f"+{익절:.0%} 익절 ({_돈(산값 * (1 + 익절))}에서 매도)")
    if getattr(정책, "trailing_stop_enabled", False):
        줄들.append(
            f"트레일링 스톱 (산 뒤 최고가에서 ATR의 "
            f"{정책.trailing_stop_multiple:g}배만큼 밀리면 매도)"
        )

    상한 = 보유상한(전략, 정책)
    if 상한:
        덮었나 = int(getattr(정책, "max_holding_days", 0) or 0) > 0
        줄들.append(
            f"{상한}거래일이 지나면 오르든 내리든 매도"
            + (" (기준에서 정한 값)" if 덮었나 else "")
        )

    # 전략 자신의 매도 신호. 설명을 만드는 쪽은 화면용이라 **굵게** 표시가
    # 섞여 있다. 텔레그램은 그것을 별표 그대로 보여 준다.
    try:
        from muwon.dashboard.strategy_rules import describe

        파는쪽 = getattr(전략, "매도쪽", 전략)
        줄들 += [ㄱ.replace("**", "") for ㄱ in describe(파는쪽).판다]
    except Exception as e:  # noqa: BLE001 (설명 하나 때문에 알림이 안 가면 안 된다)
        # 조용히 넘기지는 않는다. 매도 조건 한 줄이 빠진 알림은 "이게 전부"로
        # 읽히는데, 그 사실이 로그에도 없으면 영영 안 고쳐진다.
        logger.warning(f"매도 규칙 설명을 못 만들었습니다: {type(e).__name__}: {e}")

    if not 줄들:
        줄들.append("정해진 매도 조건이 없습니다. 손으로 팔아야 합니다")
    return 줄들


def 매수알림(이름: str, symbol: str, order, 사유: str, 손절비율: float | None = None,
          atr손절: bool = False, 전략=None, 정책=None, 장중: bool = False) -> str:
    """산 뒤에 보내는 글.

    라벨과 값을 콜론으로 가른다. 문장으로 풀어 쓰면 찾는 숫자가 매번 다른
    자리에 있어서, 훑어보는 글로는 못 쓴다.

    손절비율·atr손절은 정책을 통째로 못 넘기는 부르는 쪽을 위해 남겨 둔다.
    정책을 주면 그쪽이 이긴다. 손절 말고 다른 청산 조건까지 적을 수 있다."""
    줄 = 모양.머리("🟢", "매수체결" + (" (장중)" if 장중 else ""), 이름, symbol)
    줄 += [
        모양.수량칸(order),
        모양.칸("단가", 모양.단가(order.price)),
        모양.칸("매수총액", 모양.돈(order.quantity * order.price)),
        "",
    ]

    전략글 = 전략이름(전략) if 전략 is not None else ""
    줄.append(모양.칸("적용전략", f"{전략글} ({사유})" if 전략글 else 사유))

    if 정책 is not None:
        줄 += 모양.여러값("매도전략", 매도규칙(전략, 정책, order.price))
    elif 손절비율 and not atr손절:
        손절가 = order.price * (1 + 손절비율)
        줄.append(모양.칸("매도전략", f"{손절비율:.0%} 손절 ({_돈(손절가)}에서 매도)"))
    elif atr손절:
        줄.append(모양.칸("매도전략", "변동성 손절 (하루 평균 변동폭을 넘어 떨어지면 매도)"))

    if getattr(order, "잔여", 0):
        줄 += ["", 모양.잔여안내]
    return 모양.글(줄)


def 매도알림(이름: str, symbol: str, order, 사유: str, 진입가: float = 0.0,
          진입일=None, 판날=None, 장중: bool = False) -> str:
    """판 뒤에 보내는 글.

    예전 글에는 **손익이 아예 없었다.** 팔았다는 사실만 있고 벌었는지
    잃었는지가 없으면, 받아 보는 사람이 제일 먼저 묻는 것에 답을 못 한다."""
    줄 = 모양.머리("🔴", "매도체결" + (" (장중)" if 장중 else ""), 이름, symbol)
    줄 += [
        모양.수량칸(order),
        모양.칸("단가", 모양.단가(order.price)),
        모양.칸("매도총액", 모양.돈(order.quantity * order.price)),
        "",
        모양.칸("매도사유", _손절설명(사유)),
    ]

    if 진입가 > 0:
        손익 = (order.price - 진입가) * order.quantity
        비율 = order.price / 진입가 - 1
        줄 += [
            모양.칸("매수단가", 모양.단가(진입가)),
            모양.칸("실현손익", f"{손익:+,.0f}원 ({비율:+.1%})"),
        ]
        if 진입일 is not None and 판날 is not None:
            with suppress(TypeError, AttributeError):
                줄.append(모양.칸("보유기간", f"{(판날 - 진입일).days}일"))
        줄 += ["", "수수료와 세금은 빼기 전 값입니다."]

    if getattr(order, "잔여", 0):
        줄 += ["", 모양.잔여안내]
    return 모양.글(줄)


def _수량글(order) -> str:
    """체결 알림의 수량 줄.

    **부분 체결은 사고가 아니라 흔한 일이다.** 그래서 경보를 걸지 않고
    사실만 적는다. 다 채워졌으면 예전처럼 한 줄이고, 남았으면 주문·체결·
    잔여를 나란히 보여 준다.

        수량: 51주
        수량: 12주 중 4주 체결 · 잔여 8주

    잔여는 대개 그날 안에 마저 채워지거나 장 마감에 취소된다. 어느 쪽이든
    다음 실행이 계좌를 다시 읽으므로 사람이 손댈 것은 없다. 그 사실까지
    알림에 적어야 "뭘 해야 하나" 하고 멈추지 않는다.
    """
    남은것 = getattr(order, "잔여", 0)
    if not 남은것:
        return f"수량: {order.quantity}주"
    return (
        f"수량: {order.ordered_quantity}주 중 {order.quantity}주 체결 · 잔여 {남은것}주\n"
        "      (잔여는 마저 체결되거나 장 마감에 취소됩니다. 손댈 것 없습니다)"
    )


@dataclass
class ExecutedAction:
    symbol: str
    name: str
    side: OrderSide
    quantity: int
    price: float
    reason: str


@dataclass
class RunSummary:
    run_date: date | None
    checked_symbols: int
    actions: list[ExecutedAction] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    #: 종목코드 → 안 산(또는 못 산) 이유. rejections는 사람이 읽는 한 줄이라
    #: 종목명이 앞에 붙어 있어서 짝을 지으려면 문자열을 다시 뜯어야 한다.
    #: 알림에서 "승인했는데 왜 안 샀지"에 답하려면 이 짝이 필요하다.
    #: 2026-08-26에 이유가 바로 옆에 있는데도 알림이 버리고 있었다.
    거부사유: dict[str, str] = field(default_factory=dict)


class TradingEngine:
    def __init__(
        self,
        strategy: Strategy | PortfolioStrategy,
        risk_manager: RiskManager,
        data_source: MarketDataSource,
        order_executor: OrderExecutor,
        notifier: TelegramNotifier,
        session_factory: sessionmaker,
        universe: list[Ticker],
        source_symbol: Callable[[Ticker], str],
        costs: TransactionCosts | None = None,
        initial_cash: float = 10_000_000.0,
        orderable_provider: Callable[[str, float], int] | None = None,
        현재가공급자: Callable[[], dict[str, float]] | None = None,
        섹터표: dict[str, str] | None = None,
        섹터상한: int = 0,
    ):
        self._strategy = as_portfolio_strategy(strategy)
        self._risk_manager = risk_manager
        self._data_source = data_source
        self._order_executor = order_executor
        self._notifier = notifier
        self._session_factory = session_factory
        self._universe = universe
        self._source_symbol = source_symbol
        self._costs = costs or TransactionCosts()
        self._initial_cash = initial_cash
        #: (종목, 값) → 미수 없이 살 수 있는 수량. 못 물어보면 -1.
        #: 없으면 예전처럼 우리 현금 계산만으로 간다. 백테스트와 흉내 실행은
        #: 증권사가 없으니 물어볼 곳도 없다.
        self._orderable_provider = orderable_provider
        #: 종목코드 → 지금 값. **손절 판단에만 쓴다.**
        #:
        #: 사는 판단은 어제까지의 완성된 일봉으로 한다. 오늘 봉은 거래량이
        #: 아직 다 안 쌓여서 "거래량 2배 급증" 같은 조건이 성립할 수가 없다.
        #: 그런데 손절은 다르다. 손절은 "지금 이 값에 팔아야 하나"를 묻는
        #: 것이라 어제 종가로 재면 하루 늦게 판다.
        #:
        #: 없으면 예전처럼 어제 종가로 잰다. 백테스트와 흉내 실행은
        #: 증권사가 없으니 물어볼 곳도 없다.
        self._현재가공급자 = 현재가공급자

        #: 종목코드 → 섹터 이름. 섹터 상한을 걸려면 이것이 있어야 한다.
        #:
        #: ## 여기서도 세는 이유 (2026-09-02에 더함)
        #:
        #: 전에는 08:30 후보를 뽑을 때만 섹터 상한을 봤다. 그 계산은 후보
        #: 목록을 0부터 세기 때문에 **이미 들고 있는 종목을 모른다.** 반도체를
        #: 이미 두 종목 들고 있어도 그날 후보에 반도체 세 종목이 들어올 수
        #: 있었고, 다 승인하면 반도체 다섯 종목이 된다. 분산한 줄 알았는데
        #: 사실상 반도체 하나에 다섯 배로 건 것이다.
        #:
        #: 여기서는 보유 종목을 알고 있으므로 진짜 보유 한도로 셀 수 있다.
        #: 화면의 설명문도 "동일 섹터에서 동시에 보유할 수 있는 최대 종목
        #: 수"라고 적혀 있어서, 이쪽이 적힌 대로의 동작이다.
        self._섹터표 = dict(섹터표 or {})
        #: 0이면 제한 없음. 음수를 그대로 두면 첫 종목부터 상한에 걸려
        #: 매수가 통째로 막힌다. 백테스트 엔진에서 실제로 겪은 자리다.
        self._섹터상한 = max(0, int(섹터상한 or 0))

    @staticmethod
    def _산전략키(position) -> str:
        return (getattr(position, "strategy_key", "") or "").strip()

    def _청산전략표(self, positions) -> dict:
        """보유 종목을 산 전략들. 키가 없거나 못 만들면 지금 전략으로 간다.

        ## 왜 산 전략인가 (2026-09-02)

        살 때 이미 "며칠 들고 언제 판다"가 정해져 있었다. 그런데 청산 판단이
        지금 걸린 전략을 보고 있어서, 전략을 바꾸는 순간 규칙이 발밑에서
        바뀌었다. 5거래일 계획으로 들어간 종목이 상한 1거래일짜리 전략으로
        바뀌자마자 다음 실행에서 전부 정리됐다.

        ## 못 만드는 키는 조용히 넘기지 않는다

        묶은 전략(`a+b`)이나 옛 기록(`adopted`)은 등록된 키가 아니라 만들 수
        없다. 그때는 지금 전략으로 가되 로그를 남긴다. 조용히 넘어가면 어떤
        규칙으로 팔렸는지 나중에 알 수 없다."""
        from muwon.strategy.registry import build_strategy

        표: dict = {}
        for position in positions.values():
            키 = self._산전략키(position)
            if 키 in 표:
                continue
            if not 키 or 키 == getattr(self._strategy, "name", ""):
                표[키] = self._strategy
                continue
            try:
                표[키] = as_portfolio_strategy(build_strategy(키))
            except Exception as e:  # noqa: BLE001 (전략 하나 때문에 청산이 멈추면 안 된다)
                logger.warning(
                    f"산 전략 '{키}'를 만들지 못해 지금 전략으로 청산합니다: "
                    f"{type(e).__name__}: {e}"
                )
                표[키] = self._strategy
        return 표

    def _섹터센것(self, 보유심볼) -> dict[str, int]:
        """섹터별로 몇 종목 들고 있나. 섹터를 모르는 종목은 세지 않는다.

        모르는 종목을 빈 이름 하나로 묶으면 서로 관계없는 종목들이 한 섹터로
        묶여서, 실제로는 분산돼 있는데 상한에 걸린다."""
        센것: dict[str, int] = {}
        for ㅅ in 보유심볼:
            섹터 = self._섹터표.get(ㅅ, "")
            if 섹터:
                센것[섹터] = 센것.get(섹터, 0) + 1
        return 센것

    def run_once(self, as_of: date | None = None) -> RunSummary:
        """as_of: '오늘'로 볼 날짜(기본은 한국시간 오늘). 테스트용 주입구다."""
        trade_date = as_of or today_kst()
        end = trade_date
        start = end - timedelta(days=HISTORY_LOOKBACK_DAYS)

        latest_prices: dict[str, float] = {}
        latest_signals: dict[str, list] = {}
        histories: dict[str, pd.DataFrame] = {}
        # 보유일 계산에는 오늘까지 포함한 날짜가 필요하다. 판단용 histories는
        # 미완성인 오늘 봉을 빼지만, "며칠 들고 있었나"는 오늘을 세야 맞다.
        all_trade_dates: dict[str, list] = {}
        run_date: date | None = None

        for ticker in self._universe:
            df = self._data_source.get_daily_ohlcv(self._source_symbol(ticker), start, end)
            # 오늘 봉은 장이 끝나기 전이면 미완성이다(거래량이 아직 다 안
            # 쌓여 "거래량 2배 급증" 같은 조건이 성립할 수 없고, 종가도
            # 확정이 아니다). 개장 직후 실행을 전제로 하므로, 판단은 항상
            # 마지막으로 '완성된' 일봉으로만 한다.
            all_trade_dates[ticker.symbol] = list(df["trade_date"])
            df = df[df["trade_date"] < trade_date]
            if len(df) == 0:
                continue
            last_row = df.iloc[-1]
            latest_prices[ticker.symbol] = float(last_row["close"])
            run_date = last_row["trade_date"]
            histories[ticker.symbol] = df

        # 손절은 지금 값으로 잰다. 못 물어보면 어제 종가로 재고, **그
        # 사실을 회차 기록에 남긴다**. 조용히 어제 값으로 재면 왜 늦게
        # 팔렸는지 나중에 알 방법이 없다.
        현재가: dict[str, float] = {}
        if self._현재가공급자 is not None:
            try:
                현재가 = {ㄱ: float(ㄴ) for ㄱ, ㄴ in (self._현재가공급자() or {}).items() if ㄴ}
            except Exception as e:  # noqa: BLE001 (시세를 못 물어봐도 회차는 돌아야 한다)
                logger.warning(f"지금 값을 못 물어봤습니다. 어제 종가로 손절을 계산합니다: {e}")

        summary = RunSummary(run_date=run_date, checked_symbols=len(latest_prices))
        cash, day_start_equity = state_repository.load_engine_state(
            self._session_factory, self._initial_cash
        )
        if run_date is None:
            # 시세를 하나도 못 받은 회차다. 조용히 돌아가면 대시보드에는
            # "아무 일도 없었음"으로 보이는데, 실제로는 데이터 공급이 끊긴
            # 것이다. 그 구분이 남도록 한 줄은 반드시 남긴다.
            self._record_run(summary, cash, cash, buy=0, sell=0)
            return summary

        positions = state_repository.load_positions(self._session_factory)

        # 전략은 유니버스 전체와 보유 현황을 함께 보고 하루치를 판단한다.
        self._strategy.prepare(histories)
        signals = list(
            self._strategy.evaluate(
                MarketContext(as_of=run_date, histories=histories, held=frozenset(positions))
            )
        )
        for signal in signals:
            latest_signals.setdefault(signal.symbol, []).append(signal)
        # 신호는 주문이 나가든 안 나가든 남긴다. "안 산 이유"를 나중에
        # 되짚으려면 무엇을 봤는지가 먼저 있어야 한다.
        state_repository.record_signals(self._session_factory, signals)

        # 청산 규칙은 **그 종목을 산 전략**에서 나온다 (2026-09-02에 바꿈).
        #
        # 전에는 지금 걸린 전략 하나가 보유 종목 전부의 청산을 정했다. 그래서
        # 전략을 바꾸면 그 순간 규칙이 발밑에서 바뀌었다. 09-02 아침에 5거래일
        # 계획으로 들어간 종목들이, 보유 상한 1거래일짜리 전략으로 바뀌면서
        # 26분 뒤에 한꺼번에 정리됐다. 손절이 걸린 것도 매도 신호가 난 것도
        # 아니고 규칙이 바뀐 것이다.
        #
        # 살 때 이미 "며칠 들고 언제 판다"가 정해져 있었으므로 그것을 따른다.
        # 보유 기간, 익절선, 매도 신호를 한 전략에서 뽑아야 "왜 팔렸나"에
        # 답할 때 종목 하나에 전략 하나만 보면 된다.
        청산전략표 = self._청산전략표(positions)
        청산신호: dict[str, dict[str, list]] = {}
        for 키, 전략 in 청산전략표.items():
            if 전략 is self._strategy:
                청산신호[키] = latest_signals
                continue
            전략.prepare(histories)
            묶음: dict[str, list] = {}
            for ㅅ in 전략.evaluate(
                MarketContext(as_of=run_date, histories=histories,
                              held=frozenset(positions))
            ):
                묶음.setdefault(ㅅ.symbol, []).append(ㅅ)
            청산신호[키] = 묶음

        # 1) 청산: 손절 우선, 그다음 전략 매도 신호
        #
        # **매도 스위치가 꺼져 있으면 이 구간을 통째로 건너뛴다.** 손절도
        # 익절도 보유일수 청산도 안 걸린다. 값이 반토막 나도 아무 일이
        # 일어나지 않는다는 뜻이라, 조용히 지나가면 안 된다.
        # 들고 있는 것이 있으면 화면과 알림에 그 사실을 남긴다.
        매도켬 = self._risk_manager.get_policy().sell_enabled
        if not 매도켬 and positions:
            summary.rejections.append(
                f"🛑 매도 스위치가 꺼져 있어 보유 {len(positions)}종목의 "
                "손절·익절·청산이 전부 멈춰 있습니다"
            )
            들고있는것 = [
                f"  · {_find_ticker(self._universe, ㅅ).name}({ㅅ})" for ㅅ in sorted(positions)
            ]
            self._notify(
                "\n".join(
                    [
                        "🛑 지금 들고 있는 종목이 아무 보호도 못 받고 있습니다",
                        "",
                        "매도 스위치가 꺼져 있어서, 값이 얼마나 떨어져도 자동으로",
                        f"팔리지 않습니다. 지금 {len(positions)}종목을 들고 있습니다.",
                        "",
                        *들고있는것,
                        "",
                        "일부러 끄신 것이면 그대로 두셔도 됩니다.",
                        "다시 켜려면 대시보드 맨 위의 '자동 매도' 스위치를 켜세요.",
                        "다음 실행부터 손절이 다시 걸립니다.",
                    ]
                )
            )

        for symbol, position in list(positions.items()) if 매도켬 else []:
            if symbol not in latest_prices:
                continue
            # 손절 판단과 매도 주문은 지금 값으로. 없으면 어제 종가로.
            price = 현재가.get(symbol) or latest_prices[symbol]
            exit_reason = None
            policy = self._risk_manager.get_policy()
            산전략 = 청산전략표.get(self._산전략키(position), self._strategy)
            # 기준에서 덮어썼으면 그것이, 아니면 **산 전략**이 정한 대로.
            max_holding_days = 보유상한(산전략, policy)
            stop = evaluate_exit(
                entry_price=position.entry_price,
                entry_date=position.entry_date,
                current_price=price,
                as_of=run_date,
                policy=policy,
                atr=atr_series(histories[symbol], policy.atr_window)
                if (policy.atr_stop_enabled or policy.trailing_stop_enabled)
                else None,
                history=histories.get(symbol),
                익절=익절기준(산전략, policy),
            )
            if stop.should_exit:
                exit_reason = stop.reason
            elif max_holding_days is not None and (
                들고있던일 := bars_since(
                    all_trade_dates.get(symbol, []), position.entry_date, trade_date
                )
            ) >= max_holding_days:
                exit_reason = 보유만료글(max_holding_days, 들고있던일)
            else:
                # 매도 신호도 산 전략이 낸 것만 본다. 지금 전략이 낸 신호를
                # 섞으면 한 종목에 두 전략이 걸려서 왜 팔렸는지 설명할 수 없다.
                묶음 = 청산신호.get(self._산전략키(position), latest_signals)
                for signal in 묶음.get(symbol, []):
                    if signal.signal_type == SignalType.SELL:
                        exit_reason = signal.reason
                        break

            if exit_reason is None:
                continue

            ticker = _find_ticker(self._universe, symbol)
            order = self._order_executor.submit_order(symbol, OrderSide.SELL, position.quantity, price)
            proceeds = order.quantity * order.price * (1 - self._costs.total_sell_cost_pct)
            cash += proceeds
            del positions[symbol]
            state_repository.record_order(self._session_factory, order, exit_reason)
            state_repository.record_trade(self._session_factory, position, order, exit_reason)
            state_repository.delete_position(self._session_factory, symbol)
            summary.actions.append(
                ExecutedAction(symbol, ticker.name, OrderSide.SELL, order.quantity, order.price, exit_reason)
            )
            self._notify(
                매도알림(
                    ticker.name, symbol, order, exit_reason,
                    진입가=position.entry_price,
                    진입일=position.entry_date,
                    판날=trade_date,
                )
            )

        equity_after_exits = cash + sum(
            positions[s].quantity * latest_prices[s] for s in positions if s in latest_prices
        )
        daily_pnl_pct = (
            (equity_after_exits - day_start_equity) / day_start_equity if day_start_equity > 0 else 0.0
        )

        # 2) 진입: 신호가 남은 자리보다 많을 수 있으므로 강도 순으로 줄을 세운다.
        # 정렬 없이 dict 순서대로 사면 그건 곧 유니버스 순서(=시가총액 순)라,
        # 뒤쪽 종목은 신호가 떠도 자리가 차서 영영 못 산다. 점수가 같으면
        # 파이썬 정렬이 안정적이라 기존 순서가 그대로 유지된다.
        candidates: list[tuple[str, float, object]] = []
        for symbol, price in latest_prices.items():
            if symbol in positions:
                continue
            buy_signals = [s for s in latest_signals.get(symbol, []) if s.signal_type == SignalType.BUY]
            if buy_signals:
                candidates.append((symbol, price, buy_signals[0]))
        candidates.sort(key=lambda c: c[2].score, reverse=True)

        # 섹터별 보유 수는 **들고 있는 것부터 센다.** 사는 동안 늘어나므로
        # 이 루프 안에서 같이 갱신한다.
        섹터센것 = self._섹터센것(positions)

        for symbol, price, buy_signal in candidates:
            policy = self._risk_manager.get_policy()
            섹터 = self._섹터표.get(symbol, "")
            if self._섹터상한 and 섹터 and 섹터센것.get(섹터, 0) >= self._섹터상한:
                # 조용히 건너뛰면 "승인했는데 왜 안 샀지"가 남는다.
                사유 = (
                    f"{섹터} 섹터를 이미 {섹터센것[섹터]}종목 들고 있습니다. "
                    f"섹터당 보유 상한 {self._섹터상한}종목입니다"
                )
                summary.rejections.append(
                    f"{_find_ticker(self._universe, symbol).name}({symbol}): {사유}"
                )
                summary.거부사유[symbol] = 사유
                continue
            decision = self._risk_manager.check_new_position(
                proposed_weight=policy.max_position_weight,
                current_open_positions=len(positions),
                daily_pnl_pct=daily_pnl_pct,
            )
            ticker = _find_ticker(self._universe, symbol)
            if not decision.approved:
                summary.rejections.append(f"{ticker.name}({symbol}): {decision.reason}")
                summary.거부사유[symbol] = decision.reason
                continue

            target_value = equity_after_exits * policy.max_position_weight
            quantity = int(target_value / (price * (1 + self._costs.buy_fee_pct)))

            # ── 증권사가 "살 수 있는 수량"을 정한다 ──────────────────
            #
            # 우리 기준은 **얼마나 사고 싶은가**(비중 상한)를 정하고,
            # 증권사는 **얼마나 살 수 있는가**(증거금율까지 반영)를 정한다.
            # 둘 중 작은 쪽을 산다.
            #
            # 이걸 안 물어보면 우리가 스스로 센 현금 위에서만 판단하게 되는데,
            # 그 값은 부분 체결·거부·손매매로 조용히 어긋난다. 2026-08-25에
            # 294만원이 벌어진 채로 돌았다.
            #
            # 못 물어봤을 때(-1)는 예전처럼 우리 현금으로 간다. 조회 한 번
            # 실패한 것이 그날 매수를 통째로 막으면 안 된다.
            살수있는수량 = self._orderable(symbol, price)
            if 살수있는수량 == 0:
                사유 = "증권사가 살 수 있는 수량을 0주로 알려 줬습니다. 주문가능현금이 모자랍니다"
                summary.rejections.append(
                    f"{_find_ticker(self._universe, symbol).name}({symbol}): {사유}"
                )
                summary.거부사유[symbol] = 사유
                continue
            if 0 < 살수있는수량 < quantity:
                summary.rejections.append(
                    f"{_find_ticker(self._universe, symbol).name}({symbol}): "
                    f"{quantity}주를 사려 했지만 증권사 매수가능수량이 {살수있는수량}주라 "
                    "그만큼만 삽니다"
                )
                quantity = 살수있는수량

            cost = quantity * price * (1 + self._costs.buy_fee_pct)
            if quantity <= 0 or cost > cash:
                # 여기서 조용히 넘어가면 "승인했는데 왜 안 샀지"가 남는다.
                사유 = (
                    "한 종목에 넣을 수 있는 금액 안에서 살 수 있는 수량이 0주였습니다"
                    if quantity <= 0
                    else f"살 돈이 모자랍니다. {cost:,.0f}원이 필요한데 {cash:,.0f}원이 남아 있습니다"
                )
                summary.rejections.append(
                    f"{_find_ticker(self._universe, symbol).name}({symbol}): {사유}"
                )
                summary.거부사유[symbol] = 사유
                continue

            order = self._order_executor.submit_order(symbol, OrderSide.BUY, quantity, price)
            cash -= cost
            if 섹터:
                섹터센것[섹터] = 섹터센것.get(섹터, 0) + 1
            positions[symbol] = PositionRow(
                symbol=symbol,
                quantity=order.quantity,
                entry_price=order.price,
                entry_date=trade_date,
                entered_at=datetime.utcnow(),  # noqa: DTZ003 (기록용, tz 무관)
                entry_reason=buy_signal.reason,
                strategy_key=self._strategy.name,
            )
            state_repository.record_order(self._session_factory, order, buy_signal.reason)
            state_repository.save_position(self._session_factory, positions[symbol])
            summary.actions.append(
                ExecutedAction(symbol, ticker.name, OrderSide.BUY, order.quantity, order.price, buy_signal.reason)
            )
            정책 = self._risk_manager.get_policy()
            self._notify(
                매수알림(
                    ticker.name, symbol, order, buy_signal.reason,
                    전략=self._strategy, 정책=정책,
                )
            )

        final_equity = cash + sum(
            positions[s].quantity * latest_prices[s] for s in positions if s in latest_prices
        )
        # day_start_equity는 "직전 실행 종료 시점 평가금액"이다. 하루 한 번만
        # 도는 엔진이라 이번 실행의 최종 평가금액이 곧 다음 실행의 기준점이 된다.
        state_repository.save_engine_state(self._session_factory, cash, final_equity)
        self._record_run(
            summary,
            cash,
            final_equity,
            buy=sum(1 for s in signals if s.signal_type == SignalType.BUY),
            sell=sum(1 for s in signals if s.signal_type == SignalType.SELL),
        )
        return summary

    def _record_run(
        self, summary: RunSummary, cash: float, equity: float, *, buy: int, sell: int
    ) -> None:
        state_repository.record_run(
            self._session_factory,
            run_date=summary.run_date,
            strategy_key=self._strategy.name,
            universe_size=len(self._universe),
            checked_symbols=summary.checked_symbols,
            buy_signals=buy,
            sell_signals=sell,
            orders=len(summary.actions),
            rejections=summary.rejections,
            cash=cash,
            equity=equity,
        )

    def _orderable(self, symbol: str, price: float) -> int:
        """증권사가 말하는 매수가능수량. 물어볼 곳이 없으면 -1(=모름)."""
        if self._orderable_provider is None:
            return -1
        try:
            return self._orderable_provider(symbol, price)
        except Exception:  # noqa: BLE001 (조회 실패가 매매를 멈춰선 안 된다)
            return -1

    def _notify(self, message: str) -> None:
        self._notifier.send(message)


def _find_ticker(universe: list[Ticker], symbol: str) -> Ticker:
    for ticker in universe:
        if ticker.symbol == symbol:
            return ticker
    return Ticker(symbol=symbol, name=symbol, market="", yahoo_symbol="")
