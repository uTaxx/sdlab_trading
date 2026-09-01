"""유니버스 전체를 보고 하루치 판단을 내리는 전략 계층.

왜 필요한가. 기존 Strategy 인터페이스는 종목 하나의 가격 히스토리만 받는다.

    def generate_signals(self, symbol: str, price_history: pd.DataFrame)

이 시그니처에는 다른 종목도, 지수도, 그날의 순위도 들어올 수 없다. 그래서
상대강도(같은 날 전 종목 중 몇 등인가)와 시장 국면(지수·Breadth) 같은
'혼자서는 계산할 수 없는' Factor를 원리적으로 만들 수 없었다. 전략 하나하나에
전역 데이터를 밀어 넣는 방식으로 우회하면 결국 조건문 덩어리로 되돌아간다.

그래서 판단의 단위를 '종목'이 아니라 '하루'로 올린다. PortfolioStrategy는
그날의 전체 상황(MarketContext)을 받아 종목별 신호를 돌려준다.

기존 전략 21종은 버리지 않는다. SingleSymbolAdapter로 감싸 같은 경로에 태운다.
다기간 검증의 기준선이자, 새 엔진이 정말 더 나은지 비교할 대조군이기 때문이다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal


@dataclass(frozen=True)
class MarketContext:
    """어느 하루의 판단에 필요한 모든 것.

    histories는 as_of까지만 잘라서 넣는 게 원칙이지만, 지표 예열 때문에
    전체 프레임을 그대로 넘기는 구현도 있다(백테스트). 그래서 미래를 보지
    않을 책임은 Factor/전략 쪽에 있다. as_of 이후 행을 참조하지 말 것.
    """

    as_of: date
    histories: dict[str, pd.DataFrame]
    held: frozenset[str] = frozenset()
    index_history: pd.DataFrame | None = None


@dataclass(frozen=True)
class FactorResult:
    """Factor 하나가 한 종목에 매긴 점수.

    score는 반드시 0~100으로 정규화한다. 척도가 제각각이면 가중치를 아무리
    조정해도 의미가 없어진다. 이 규칙이 점수 합산 방식의 전제다.
    데이터가 모자라 평가할 수 없으면 score=None으로 두고, 그 Factor의
    가중치는 합계에서 빼고 나머지를 재정규화한다.
    """

    key: str
    score: float | None
    reason: str = ""


@dataclass(frozen=True)
class Evaluation:
    """한 종목에 대한 최종 평가. 점수와 '왜'가 항상 함께 다닌다.

    사람이 판단 근거를 못 보면 전략을 고칠 수가 없다(인수인계서 35항)."""

    symbol: str
    score: float
    factor_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


class PortfolioStrategy(ABC):
    """하루 단위로 유니버스 전체를 평가하는 전략."""

    name: str

    #: 진입 후 이 거래일 수가 지나면 청산한다(시간 기반 청산). None이면 없음.
    #: 전략이 아니라 엔진이 실제 보유일 기준으로 집행한다. 전략이 스스로
    #: "내가 보유 중"을 기억하면 엔진의 실제 보유와 어긋나기 때문이다.
    max_holding_days: int | None = None

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        """반복 호출 전 한 번만 하는 무거운 계산을 여기 둔다.

        백테스트는 evaluate()를 날짜 수만큼 부르기 때문에, 매번 지표를 다시
        계산하면 감당이 안 된다. 여기서 미리 계산해 두고 evaluate()는 조회만
        하게 만든다. 미리 계산하더라도 각 날짜의 값이 그 날짜까지의 데이터로만
        결정되어야 한다는 원칙은 그대로다."""

    @abstractmethod
    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        """ctx.as_of 하루치 신호. 다른 날짜의 신호를 섞어 돌려주면 안 된다."""
        raise NotImplementedError


class SingleSymbolAdapter(PortfolioStrategy):
    """기존 Strategy 하나를 PortfolioStrategy로 변환한다.

    두 엔진이 PortfolioStrategy 한 가지만 알면 되도록 만드는 다리다. 기존
    전략은 종목별로 독립 계산되므로 prepare()에서 전 종목·전 기간 신호를 한
    번에 만들어 날짜별로 색인해 둔다. 기존 엔진들이 하던 것과 똑같은 계산
    순서라, 결과가 달라지지 않는다."""

    def __init__(self, strategy: Strategy):
        self._strategy = strategy
        self.name = strategy.name
        self.max_holding_days = getattr(strategy, "max_holding_days", None)

    @property
    def inner(self) -> Strategy:
        return self._strategy

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._by_date: dict[date, list[Signal]] = {}
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            for signal in self._strategy.generate_signals(symbol, df):
                self._by_date.setdefault(signal.trade_date, []).append(signal)

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        if not hasattr(self, "_by_date"):
            self.prepare(ctx.histories)
        return self._by_date.get(ctx.as_of, [])


def as_portfolio_strategy(strategy: Strategy | PortfolioStrategy) -> PortfolioStrategy:
    """무엇이 들어오든 PortfolioStrategy로 통일한다."""
    if isinstance(strategy, PortfolioStrategy):
        return strategy
    return SingleSymbolAdapter(strategy)


def bars_since(trade_dates, entry_date: date, as_of: date) -> int:
    """진입일 다음 거래일부터 as_of까지의 거래일 수.

    달력 일수가 아니라 거래일로 세는 이유: '보유 5일'은 주말·공휴일을 뺀
    5거래일을 뜻한다. 달력으로 세면 연휴가 낀 주에 하루 일찍 팔게 된다."""
    return sum(1 for d in trade_dates if entry_date < d <= as_of)
