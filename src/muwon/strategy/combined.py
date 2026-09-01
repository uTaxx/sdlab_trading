"""전략 여러 개를 한 번에 굴린다. AND / OR로 묶어서.

왜 필요한가. 지금까지는 실거래에 전략을 **하나만** 걸 수 있었다. 그런데
전략마다 잘 맞는 장이 다르다. 어떤 건 자주 사고(놓치는 게 적지만 잡음도
많고), 어떤 건 까다롭다(잘 맞지만 기회가 드물다). 하나만 고르면 그 성격을
통째로 받아들이는 수밖에 없다.

묶는 방식은 두 가지다.

- **OR (하나라도)**. 어느 하나가 사라고 하면 산다. 기회가 늘고 신호가
  잦아진다. 서로 다른 때에 발화하는 전략을 모을 때 쓴다.
- **AND (모두)**. 같은 날 같은 종목에 **전부** 사라고 해야 산다. 기회가
  크게 줄고 조건이 까다로워진다. 잡음을 걷어내고 싶을 때 쓴다.

개수 제한은 없다. 둘이든 다섯이든 같은 규칙으로 묶인다.

## 파는 쪽은 언제나 OR다

이건 설정으로 두지 않았다. AND로 팔면 **하나라도 반대하면 못 파는** 구조가
되어, 나머지가 전부 "팔라"고 해도 한 전략이 침묵하는 동안 계속 들고 있게
된다. 손실을 키우는 방향의 실수는 되돌리기 어렵다. 그래서 **살 때는 까다롭게,
팔 때는 관대하게**. 어느 하나라도 팔라고 하면 판다.

손절·보유 기간은 여기서 다루지 않는다. 엔진이 리스크 정책으로 따로 집행한다.

## 점수는 가장 약한 근거를 따른다 (AND)

자리가 모자라면 엔진이 점수 순으로 줄을 세운다. AND로 묶은 신호의 점수를
가장 높은 것으로 잡으면, 실제로는 간신히 통과한 종목이 앞줄에 서게 된다.
그래서 AND는 **최솟값**을, OR은 **최댓값**을 쓴다. 각각 "가장 약한 근거"와
"가장 강한 근거"다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from muwon.domain.types import Signal, SignalType
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
)

COMBINE_OR = "OR"
COMBINE_AND = "AND"
COMBINE_MODES = (COMBINE_OR, COMBINE_AND)

MODE_LABELS = {
    COMBINE_OR: "하나라도 (OR)",
    COMBINE_AND: "모두 (AND)",
}


class CombinedStrategy(PortfolioStrategy):
    """전략 여러 개를 AND/OR로 묶어 하나처럼 쓴다."""

    def __init__(self, strategies, mode: str = COMBINE_OR, name: str = ""):
        members = [as_portfolio_strategy(s) for s in strategies]
        if not members:
            raise ValueError("전략을 하나 이상 넣어야 합니다")
        if mode not in COMBINE_MODES:
            raise ValueError(f"묶는 방식은 {COMBINE_MODES} 중 하나여야 합니다: {mode}")

        self._members = members
        self.mode = mode
        self.name = name or f"{mode.lower()}({'+'.join(m.name for m in members)})"

        # 보유 기간은 **가장 짧은 것**을 따른다. 하나가 "5일 지나면 무조건
        # 판다"고 정해 뒀는데 다른 전략 때문에 더 들고 있으면, 그 전략은
        # 자기가 검증된 조건 밖에서 돌게 된다.
        limits = [m.max_holding_days for m in members if m.max_holding_days]
        self.max_holding_days = min(limits) if limits else None

    @property
    def members(self) -> list[PortfolioStrategy]:
        return list(self._members)

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        for member in self._members:
            member.prepare(histories)

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        # 전략별로 그날 낸 신호를 종목별로 모은다. 같은 전략이 한 종목에
        # 매수·매도를 동시에 낼 일은 없지만, 방어적으로 종류별로 나눠 담는다.
        buys: dict[str, list[Signal]] = {}
        sells: dict[str, list[Signal]] = {}
        for member in self._members:
            for signal in member.evaluate(ctx):
                bucket = buys if signal.signal_type == SignalType.BUY else sells
                if signal.signal_type in (SignalType.BUY, SignalType.SELL):
                    bucket.setdefault(signal.symbol, []).append(signal)

        combined: list[Signal] = []
        # 매도는 언제나 OR: 하나라도 팔라고 하면 판다.
        for symbol, signals in sells.items():
            combined.append(_merge(symbol, signals, ctx.as_of, self.name, COMBINE_OR))

        needed = len(self._members) if self.mode == COMBINE_AND else 1
        for symbol, signals in buys.items():
            # AND는 **서로 다른 전략** 수로 센다. 한 전략이 같은 종목에 신호를
            # 두 번 내도 그건 한 표다.
            if len({s.strategy_name for s in signals}) < needed:
                continue
            combined.append(_merge(symbol, signals, ctx.as_of, self.name, self.mode))
        return combined


def _merge(symbol: str, signals: list[Signal], as_of: date, name: str, mode: str) -> Signal:
    """같은 종목에 모인 신호를 한 줄로 합친다.

    사유를 전부 이어 붙인다. 나중에 매매 기록을 볼 때 "무엇 때문에 샀나"가
    한 전략만 남아 있으면 묶은 뜻이 없다."""
    점수들 = [s.score for s in signals]
    점수 = min(점수들) if mode == COMBINE_AND else max(점수들)
    잇기 = " + " if mode == COMBINE_AND else " 또는 "
    사유 = 잇기.join(f"[{s.strategy_name}] {s.reason}" for s in signals)
    return Signal(
        symbol=symbol,
        trade_date=as_of,
        signal_type=signals[0].signal_type,
        strategy_name=name,
        score=점수,
        # 기록 칸이 100자라 넘치면 잘린다. 잘릴 바에는 몇 개가 겹쳤는지라도
        # 남기는 편이 낫다.
        reason=사유 if len(사유) <= 100 else f"{len(signals)}개 전략 동시 신호",
    )
