"""평균회귀(mean reversion) 계열 전략. "많이 떨어졌으면 되돌아온다"에 베팅한다.

추세추종과 정반대 성격이다: 떨어질 때 사고 오를 때 팔기 때문에 승률은
높은 편이지만(자주 조금씩 맞음), 한 번 크게 무너지는 추세장에서 계속
"싸다"고 사다가 크게 잃는 게 전형적인 약점이다. 그래서 대부분 장기
이동평균 위(=큰 추세는 살아있음) 같은 안전장치를 함께 건다."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal, SignalType
from muwon.indicators.technical import add_bollinger, add_indicators, add_stochastic
from muwon.strategy.common import crossed_above, crossed_below, has_nan, make_signal


def _recovered_from_below(prev_value: float, cur_value: float, prev_ref: float, cur_ref: float) -> bool:
    """직전엔 기준선 "밖"(엄격히 아래)에 있다가 지금은 안으로 들어왔는가.

    일반 교차 판정(crossed_above)은 prev <= ref를 허용하는데, 밴드 이탈/복귀
    판정에 그걸 쓰면 변동성이 0에 가까워 종가와 밴드가 같아지는 구간에서
    헛신호가 난다(급등한 날이 "바닥 반등"으로 잡히는 정반대 오판). 밴드
    밖에 실제로 나가 있었는지는 엄격 부등호로 봐야 한다."""
    if pd.isna(prev_value) or pd.isna(cur_value) or pd.isna(prev_ref) or pd.isna(cur_ref):
        return False
    return prev_value < prev_ref and cur_value >= cur_ref


@dataclass(frozen=True)
class RsiReversionParams:
    rsi_period: int = 14
    oversold: float = 30  # 이 아래로 갔다가 회복하면 매수
    overbought: float = 70  # 이 위로 올라가면 매도
    sma_long: int = 60
    require_above_long_ma: bool = True  # 장기 이동평균 위에서만 매수(하락장 회피)


class RsiReversionStrategy(Strategy):
    """순수 RSI 평균회귀: 과매도에서 반등하면 사고, 과매수면 판다.

    require_above_long_ma를 켜면 장기 이동평균 위에 있을 때만 매수해서,
    끝없이 흘러내리는 종목을 계속 받아내는 상황을 막는다."""

    def __init__(self, params: RsiReversionParams | None = None, name: str = "rsi_reversion"):
        self.params = params or RsiReversionParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_indicators(price_history, rsi_period=p.rsi_period, sma_long=p.sma_long)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["rsi"]) or has_nan(prev, ["rsi"]):
                continue

            bounced = prev["rsi"] < p.oversold <= cur["rsi"]
            if bounced:
                if p.require_above_long_ma and (
                    pd.isna(cur["sma_long"]) or cur["close"] <= cur["sma_long"]
                ):
                    continue
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        f"RSI 과매도({p.oversold:g}) 반등",
                        # 더 깊이 빠졌다 올라올수록 되돌림 여력이 크다고 본다
                        score=float(p.oversold - prev["rsi"]) if not pd.isna(prev["rsi"]) else 0.0,
                    )
                )
            elif cur["rsi"] > p.overbought:
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, f"RSI 과매수({p.overbought:g})")
                )
        return signals


@dataclass(frozen=True)
class BollingerReversionParams:
    window: int = 20
    num_std: float = 2.0
    exit_at_middle: bool = True  # True면 중심선 복귀 시 청산, False면 상단 도달 시 청산


class BollingerReversionStrategy(Strategy):
    """볼린저밴드 하단 반등: 가격이 아래 띠 밖으로 벗어났다가 다시
    안으로 들어오면 "과하게 떨어진 게 되돌아온다"고 보고 매수한다.

    청산은 중심선(이동평균) 복귀 시점이 기본이다. 평균으로 돌아오는 걸
    노린 거래이므로 평균에 닿으면 목적을 달성한 것으로 본다."""

    def __init__(
        self, params: BollingerReversionParams | None = None, name: str = "bollinger_reversion"
    ):
        self.params = params or BollingerReversionParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_bollinger(price_history, window=p.window, num_std=p.num_std)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["bb_upper", "bb_mid", "bb_lower"]) or has_nan(prev, ["bb_lower"]):
                continue

            # 하단 밴드 밖에 있다가 안으로 복귀 = 반등 시작
            if _recovered_from_below(prev["close"], cur["close"], prev["bb_lower"], cur["bb_lower"]):
                # 직전에 밴드 밖으로 더 많이 벗어나 있었을수록 되돌림 폭이 크다
                depth_pct = (
                    (1 - prev["close"] / prev["bb_lower"]) * 100 if prev["bb_lower"] > 0 else 0.0
                )
                signals.append(
                    make_signal(
                        symbol, cur, SignalType.BUY, self.name, "볼린저 하단 이탈 후 복귀", score=float(depth_pct)
                    )
                )
                continue

            # 청산은 "교차한 순간"이 아니라 "지금 그 위에 있는가"라는 상태로 본다.
            # 갭 상승으로 기준선을 훌쩍 뛰어넘어 계속 머무르면 교차 사건이
            # 발생하지 않아 청산 신호가 영영 안 나오기 때문이다. 엔진은 포지션을
            # 들고 있을 때만 매도하므로 신호가 여러 날 반복돼도 문제되지 않는다.
            exit_ref = "bb_mid" if p.exit_at_middle else "bb_upper"
            exit_label = "중심선" if p.exit_at_middle else "상단"
            if cur["close"] >= cur[exit_ref]:
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, f"볼린저 {exit_label} 도달")
                )
        return signals


@dataclass(frozen=True)
class StochasticParams:
    window: int = 14
    smooth_window: int = 3
    oversold: float = 20
    overbought: float = 80


class StochasticStrategy(Strategy):
    """스토캐스틱 교차: 과매도 구간에서 %K가 %D를 위로 뚫으면 매수,
    과매수 구간에서 아래로 뚫으면 매도.

    RSI와 비슷한 과매수/과매도 지표지만, 최근 고가~저가 범위 안에서
    종가의 위치를 보기 때문에 박스권(횡보장)에서 더 잘 맞는 편이다."""

    def __init__(self, params: StochasticParams | None = None, name: str = "stochastic"):
        self.params = params or StochasticParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_stochastic(price_history, window=p.window, smooth_window=p.smooth_window)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["stoch_k", "stoch_d"]) or has_nan(prev, ["stoch_k", "stoch_d"]):
                continue
            if (
                crossed_above(prev["stoch_k"], cur["stoch_k"], prev["stoch_d"], cur["stoch_d"])
                and cur["stoch_k"] <= p.oversold
            ):
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        f"스토캐스틱 과매도({p.oversold:g}) 교차",
                        score=float(p.oversold - cur["stoch_k"]),
                    )
                )
            elif (
                crossed_below(prev["stoch_k"], cur["stoch_k"], prev["stoch_d"], cur["stoch_d"])
                and cur["stoch_k"] >= p.overbought
            ):
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, f"스토캐스틱 과매수({p.overbought:g}) 교차")
                )
        return signals
