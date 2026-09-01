"""Factor 공통 인터페이스와 정규화 도우미.

Factor는 "이 종목이 이 관점에서 얼마나 좋은가"를 **0~100**으로 답한다.
척도를 통일하는 게 이 구조 전체의 전제다. 어떤 Factor는 배수(1.5배),
어떤 Factor는 퍼센트(-6%)로 답하면 가중치를 아무리 조정해도 의미가 없다.

평가할 수 없으면 None을 돌려준다. 데이터가 모자란 초반 구간이나 소스가
없는 Factor가 여기 해당하며, 점수 엔진이 그 Factor의 가중치를 빼고 나머지를
재정규화한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from itertools import pairwise
from typing import Any

import pandas as pd

from muwon.strategy.portfolio import FactorResult, MarketContext


class Factor(ABC):
    """설정에서 key로 참조되고, 종목 하나에 점수를 매긴다."""

    key: str

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = params or {}
        # 빈 데이터로 한 번 예열해 내부 표를 만들어 둔다. warmup을 안 부르고
        # score부터 호출해도 알 수 없는 AttributeError 대신 "데이터 부족"으로
        # 떨어지게 하려는 것: 잘못 쓴 쪽이 원인을 바로 알 수 있어야 한다.
        self.warmup({})

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        """실행당 한 번. 종목별 지표 시계열을 통째로 계산해 둔다.

        백테스트는 날짜 수만큼 평가를 반복하므로, 여기서 미리 만들지 않으면
        같은 이동평균을 수백 번 다시 계산하게 된다(실측 25배 차이)."""

    def prepare(self, ctx: MarketContext) -> None:
        """그날 하루치 계산. 횡단면 순위·비율처럼 '그날 전 종목을 한꺼번에
        봐야' 나오는 값을 여기서 만든다. 종목별 계산만 하는 Factor는 비워 둔다."""

    @abstractmethod
    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        raise NotImplementedError


# ── 정규화 도우미 ────────────────────────────────────────────────


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def ratio_score(satisfied: int, total: int) -> float:
    """조건 몇 개 중 몇 개를 만족했는가 → 0~100.

    임의의 상수 없이 만들 수 있는 가장 정직한 점수다. 정배열 판정처럼
    '단계적으로 좋아지는' 조건에 쓴다."""
    if total <= 0:
        return 0.0
    return satisfied / total * 100


def piecewise(value: float, points: list[tuple[float, float]]) -> float:
    """구간별 선형 보간. points는 (입력, 점수)를 입력 오름차순으로 준다.

    예: [(-10, 20), (-6, 100), (-4, 60), (-2, 20)] 처럼 '너무 깊으면 오히려
    감점'인 비단조 관계도 표현할 수 있다. 단순히 많이 떨어졌다고 좋은 게
    아니라는 인수인계서 10항의 요구가 이 형태를 필요로 한다."""
    if not points:
        return 0.0
    if value <= points[0][0]:
        return points[0][1]
    if value >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in pairwise(points):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            t = (value - x0) / (x1 - x0)
            return y0 + (y1 - y0) * t
    return points[-1][1]


def percentile_scores(values: dict[str, float]) -> dict[str, float]:
    """종목별 값 → 유니버스 안에서의 백분위(0~100).

    절대값을 점수로 바꿀 때 임계값을 손으로 정하면(예: 수익률 20% 이상이면
    100점) 시장 국면에 따라 전 종목이 100점이거나 0점이 된다. 순위로 바꾸면
    그 문제가 사라진다. 대신 '다 같이 나쁜 날'에도 1등은 100점이 되므로,
    시장 자체의 좋고 나쁨은 Market Regime이 따로 본다."""
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 50.0}
    # 같은 값이면 같은 점수여야 한다. 정렬 순서로 등수를 매기면 완전히
    # 동일한 두 종목이 다른 점수를 받아 선택이 임의로 갈린다.
    ranked = pd.Series(values).rank(pct=True, method="average") * 100
    return {symbol: float(score) for symbol, score in ranked.items()}


def history_up_to(ctx: MarketContext, symbol: str) -> pd.DataFrame | None:
    """ctx.as_of까지로 자른 히스토리. 미래를 보지 않기 위한 공통 관문이다.

    백테스트는 성능 때문에 전체 프레임을 그대로 넘기므로, 자르는 책임이
    Factor 쪽에 있다. 모든 Factor는 반드시 이 함수를 거쳐 데이터를 읽는다."""
    df = ctx.histories.get(symbol)
    if df is None or len(df) == 0:
        return None
    trimmed = df[df["trade_date"] <= ctx.as_of]
    return trimmed if len(trimmed) else None


def pct_return(closes: pd.Series, periods: int) -> float | None:
    """N거래일 전 대비 수익률(%). 데이터가 모자라면 None."""
    if len(closes) <= periods:
        return None
    past = float(closes.iloc[-1 - periods])
    if past <= 0:
        return None
    return (float(closes.iloc[-1]) / past - 1) * 100
