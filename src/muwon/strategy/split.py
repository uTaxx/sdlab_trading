"""사는 쪽과 파는 쪽을 **다른 전략으로** 굴린다.

## 왜 필요한가

지금까지 전략 하나에는 사는 규칙과 파는 규칙이 같이 들어 있었다. 파는 규칙만
바꾸려면 이름을 새로 붙인 전략을 등록해야 했다 — `volume_surge_5d`의 매도만
20일선으로 바꾸려고 `volume_surge_5d_ma20`을 따로 만든 것이 그 예다.

이러면 이름이 곱으로 늘어난다. 매수 후보 22개에 매도 방식 4가지를 붙이면
88개를 등록해야 하고, 그중 실제로 재 본 것은 몇 개뿐인데 목록에는 88개가
있는 상태가 된다.

`CombinedStrategy`는 이걸 못 푼다. 거기 넣은 전략들은 **양쪽에 다** 적용된다.
매수는 AND/OR로 묶고 매도는 언제나 OR인데, 어느 쪽이든 같은 전략 묶음이다.

여기서는 매수 쪽과 매도 쪽을 아예 갈라 받는다.

    SplitStrategy(매수=volume_surge_5d, 매도=ma_rsi_v1)

매수 신호는 매수 쪽에서만, 매도 신호는 매도 쪽에서만 나온다.

## 파는 것에 관한 모든 것은 매도 쪽이 정한다

보유 기간 상한(`max_holding_days`)도 매도 쪽 것을 쓴다. 그것도 청산 규칙이기
때문이다. 매수 쪽이 "5일 지나면 판다"를 갖고 있어도 여기서는 안 본다 —
파는 자리를 두 군데로 나누면 왜 팔렸는지 설명할 수 없게 된다.

**손절과 일일 손실 한도는 여기서도 다루지 않는다.** 엔진이 리스크 정책으로
따로 집행하고, 그건 전략을 무엇으로 바꾸든 그대로 걸린다.

## 매도 쪽에 청산 수단이 하나도 없으면 경고한다

어떤 전략은 매도 신호를 아예 안 낸다(`volume_surge_5d`가 그렇다 — 시간
청산만 있다). 그런 전략을 매도 쪽에 놓고 보유 기간 상한마저 없으면,
**손절 말고는 나가는 길이 없다.** 값이 안 빠지면 영영 들고 있게 된다.

그 상태를 막지는 않는다(그게 맞는 조합도 있다). 대신 `왜조심해야하나`가
빈 문자열이 아니게 만들어서, 고르는 화면이 그걸 그대로 보여 주게 한다.
조용히 두면 "왜 안 팔리지"가 몇 주 뒤에 온다.

## 이 조합의 성적은 없다

성적표는 등록된 이름 단위로 잰다. 매수 A + 매도 B는 이름이 없으므로 재 본
숫자도 없다. 고르는 화면이 그 사실을 말해야 한다 — 안 재 본 조합을 숫자
없이 내놓으면 재 본 것처럼 읽힌다.
"""

from __future__ import annotations

import pandas as pd

from muwon.domain.types import Signal, SignalType
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
)


class SplitStrategy(PortfolioStrategy):
    """매수는 한 전략에서, 매도는 다른 전략에서."""

    def __init__(self, 매수, 매도, name: str = ""):
        self._매수 = as_portfolio_strategy(매수)
        self._매도 = as_portfolio_strategy(매도)
        self.name = name or f"매수:{self._매수.name}+매도:{self._매도.name}"
        # 보유 기간 상한도 청산 규칙이라 매도 쪽 것을 쓴다.
        self.max_holding_days = self._매도.max_holding_days

    @property
    def 매수쪽(self) -> PortfolioStrategy:
        return self._매수

    @property
    def 매도쪽(self) -> PortfolioStrategy:
        return self._매도

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._매수.prepare(histories)
        # 같은 전략을 양쪽에 놓으면 두 번 준비할 필요가 없다. 무거운 계산이라
        # 두 번 하면 회차 시간이 그만큼 늘어난다.
        if self._매도 is not self._매수:
            self._매도.prepare(histories)

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        낸것 = [
            s for s in self._매수.evaluate(ctx) if s.signal_type == SignalType.BUY
        ]
        if self._매도 is self._매수:
            판것 = [
                s for s in self._매수.evaluate(ctx) if s.signal_type == SignalType.SELL
            ]
        else:
            판것 = [
                s for s in self._매도.evaluate(ctx) if s.signal_type == SignalType.SELL
            ]

        # 전략 이름은 묶은 이름으로 바꾼다. 기록에 매수 쪽 이름만 남으면
        # 나중에 "이 매매가 어떤 조합에서 나왔나"를 알 수 없다. 다만 사유에는
        # 원래 전략 이름을 남겨서 어느 쪽이 낸 신호인지 보이게 한다.
        return [_이름붙이기(s, self.name) for s in 낸것 + 판것]

    @property
    def 왜조심해야하나(self) -> str:
        """이 조합에서 나가는 길이 좁으면 그 이유를 한 줄로. 없으면 빈 문자열."""
        if self.max_holding_days is not None:
            return ""
        return (
            f"매도 쪽({self._매도.name})에 보유 기간 상한이 없습니다. "
            "매도 신호가 안 나면 손절 말고는 파는 길이 없습니다."
        )


def _이름붙이기(신호: Signal, 이름: str) -> Signal:
    from dataclasses import replace

    사유 = f"[{신호.strategy_name}] {신호.reason}"
    return replace(
        신호,
        strategy_name=이름,
        # 기록 칸이 100자라 넘치면 잘린다. 잘릴 바에는 원래 사유를 살린다.
        reason=사유 if len(사유) <= 100 else 신호.reason,
    )
