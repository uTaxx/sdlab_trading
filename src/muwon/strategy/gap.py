"""갭과 변동성 돌파: 유명하지만 근거가 얇은 둘.

`docs/단타전략조사.md`에서 **일봉만으로 싸게 재 볼 수 있는** 후보로 꼽은
것들이다. 기대는 낮다. 기각하려고 만든다.

## 왜 기대가 낮은가

- **변동성 돌파**(래리 윌리엄스)는 한국 개인 투자자에게 가장 널리 알려진
  단타 규칙인데, 조사에서 **동료심사 논문을 하나도 찾지 못했다.** 나온 것은
  트레이딩 플랫폼 문서와 백테스트 블로그뿐이다. 유명하다는 것과 검증됐다는
  것은 다르다.
- **갭**은 학술 문헌이 있는데 결론이 **반반**이다. "메워진다"와 "이어진다"가
  둘 다 보고돼 있다. 반반이면 그건 신호가 아니다.

그래도 만드는 이유는, 이 저장소가 **기각된 가설을 자산으로 취급**하기
때문이다. 싸게 재서 기각하면 같은 걸 두 번 시험하지 않게 된다.

## 근사라는 것을 반드시 같이 읽어야 한다

**변동성 돌파는 원래 장중에 체결되는 규칙이다.** "오늘 시가 + 어제 폭 × K"를
가격이 뚫는 **그 순간** 사는 것이다. 그런데 우리는 일봉밖에 없다.

그래서 여기서는 "그날 **고가**가 돌파선을 넘었나"로만 판정한다. 넘었다는
사실은 알 수 있지만 **돌파선 가격에 살 수는 없다**. 엔진은 종가(또는 다음 날
시가)에 산다. 돌파 후 더 오른 날은 실제보다 비싸게 사는 것이고, 뚫고 도로
밀린 날은 오히려 싸게 사는 것이 된다.

즉 이 결과는 원래 규칙의 성적이 아니라 **"돌파가 일어난 날에 종가로
따라 산 것"의 성적**이다. 방향이 어느 쪽으로 치우치는지도 모른다.
잘 나와도 "그러니 변동성 돌파가 통한다"고 읽으면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal, SignalType
from muwon.strategy.common import make_signal


@dataclass(frozen=True)
class VolatilityBreakoutParams:
    #: 어제 폭(고가−저가)의 몇 배를 오늘 시가에 더할 것인가. 통상 0.5.
    k: float = 0.5
    #: 며칠 들고 있을 것인가. 원 규칙은 당일 청산이라 1이 가장 가깝다.
    holding_days: int = 1


class VolatilityBreakoutStrategy(Strategy):
    """오늘 시가 + (어제 고가 − 어제 저가) × K 를 넘으면 산다.

    **일봉 근사다.** 돌파선 가격에 사는 게 아니라, 돌파가 일어난 날에
    종가로 따라 산다. 모듈 문서의 경고를 반드시 같이 읽을 것."""

    def __init__(self, params: VolatilityBreakoutParams | None = None, name: str = "volatility_breakout"):
        self.params = params or VolatilityBreakoutParams()
        self.name = name
        self.max_holding_days = self.params.holding_days

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = price_history.sort_values("trade_date").reset_index(drop=True)
        signals: list[Signal] = []

        for i in range(1, len(df)):
            어제, 오늘 = df.iloc[i - 1], df.iloc[i]
            폭 = float(어제["high"]) - float(어제["low"])
            if 폭 <= 0 or float(오늘["open"]) <= 0:
                continue
            돌파선 = float(오늘["open"]) + 폭 * p.k
            if float(오늘["high"]) < 돌파선:
                continue
            signals.append(
                make_signal(
                    symbol,
                    오늘,
                    SignalType.BUY,
                    self.name,
                    f"시가+어제폭×{p.k:g} 돌파 (일봉 근사)",
                    # 돌파선을 얼마나 크게 넘었나. 같은 날 후보가 많으면
                    # 이 값으로 줄을 세운다.
                    score=(float(오늘["high"]) / 돌파선 - 1) * 100,
                )
            )
        return signals


@dataclass(frozen=True)
class GapParams:
    #: "up"이면 갭 상승을 따라 사고(이어간다는 쪽), "down"이면 갭 하락을 산다(메운다는 쪽).
    direction: str = "up"
    #: 몇 % 이상 벌어진 갭만 볼 것인가.
    min_gap_pct: float = 2.0
    holding_days: int = 1


class GapStrategy(Strategy):
    """어제 종가와 오늘 시가의 차이를 보고 산다.

    문헌 결론이 반반이라 **두 방향을 다 만들어 둔다**. 한쪽만 만들면
    "이쪽이 맞을 것 같아서" 고른 셈이 되고, 그건 결과를 미리 정해 놓고
    재는 것이다."""

    def __init__(self, params: GapParams | None = None, name: str = "gap"):
        self.params = params or GapParams()
        if self.params.direction not in ("up", "down"):
            raise ValueError(f"direction은 'up' 또는 'down'이어야 합니다: {self.params.direction}")
        self.name = name
        self.max_holding_days = self.params.holding_days

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = price_history.sort_values("trade_date").reset_index(drop=True)
        signals: list[Signal] = []

        for i in range(1, len(df)):
            어제종가 = float(df.iloc[i - 1]["close"])
            오늘 = df.iloc[i]
            if 어제종가 <= 0:
                continue
            갭 = (float(오늘["open"]) / 어제종가 - 1) * 100
            맞는가 = 갭 >= p.min_gap_pct if p.direction == "up" else 갭 <= -p.min_gap_pct
            if not 맞는가:
                continue
            어느쪽 = "상승" if p.direction == "up" else "하락"
            signals.append(
                make_signal(
                    symbol,
                    오늘,
                    SignalType.BUY,
                    self.name,
                    f"갭 {어느쪽} {abs(갭):.1f}%",
                    score=abs(갭),
                )
            )
        return signals
