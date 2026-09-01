"""이동평균+거래량 추세추종과 RSI 평균회귀를 결합한 규칙기반 전략.

매수 트리거 두 가지 (서로 독립적):
  - trend: 종가가 단기 이동평균을 상향 돌파 + 거래량이 평균의
    volume_surge_ratio배 이상 + RSI가 rsi_buy_ceiling 미만(과매수 아님)
  - reversion: RSI가 rsi_oversold 밑에서 위로 반등 + 장기 이동평균 위
    (추세 자체는 살아있는 구간)에서만: 하락 추세 중 반짝 반등을 잡는 걸
    피하기 위한 필터

매도 트리거:
  - 단기 이동평균 하향 이탈, 또는 RSI가 rsi_overbought 초과(과매수 청산)

손절/포지션 크기 같은 자금관리는 이 전략이 아니라 RiskManager가 책임진다.
여기서는 방향 신호만 낸다.

파라미터(MovingAverageRsiParams)를 인스턴스마다 다르게 줄 수 있게 만든
이유: "가설을 검증하고 진화시킨다"는 게 코드를 고쳐가며 하는 게 아니라,
같은 규칙 구조에 다른 파라미터 세트를 꽂아서 백테스트로 비교하는 방식이
되어야 하기 때문이다. 등록된 가설들은 strategy/registry.py에서 관리한다."""

from dataclasses import dataclass

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal, SignalType
from muwon.indicators.technical import add_indicators
from muwon.strategy.common import volume_ratio


@dataclass(frozen=True)
class MovingAverageRsiParams:
    sma_short: int = 20
    sma_long: int = 60
    rsi_period: int = 14
    volume_ma_window: int = 20
    volume_surge_ratio: float = 1.5
    rsi_oversold: float = 30
    rsi_overbought: float = 80
    rsi_buy_ceiling: float = 70


class MovingAverageRsiStrategy(Strategy):
    def __init__(self, params: MovingAverageRsiParams | None = None, name: str = "ma_rsi_v1"):
        self.params = params or MovingAverageRsiParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_indicators(
            price_history,
            sma_short=p.sma_short,
            sma_long=p.sma_long,
            rsi_period=p.rsi_period,
            volume_ma_window=p.volume_ma_window,
        )
        signals: list[Signal] = []

        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if pd.isna(cur["sma_short"]) or pd.isna(prev["sma_short"]) or pd.isna(cur["rsi"]):
                continue

            golden_cross = prev["close"] <= prev["sma_short"] and cur["close"] > cur["sma_short"]
            volume_surge = (
                not pd.isna(cur["volume_ma"])
                and cur["volume_ma"] > 0
                and cur["volume"] >= cur["volume_ma"] * p.volume_surge_ratio
            )
            if golden_cross and volume_surge and cur["rsi"] < p.rsi_buy_ceiling:
                signals.append(
                    Signal(
                        symbol=symbol,
                        trade_date=cur["trade_date"],
                        signal_type=SignalType.BUY,
                        strategy_name=self.name,
                        reason="단기선 상향돌파 + 거래량 급증",
                        score=volume_ratio(cur),
                    )
                )
                continue

            rsi_bounce = (
                not pd.isna(prev["rsi"])
                and prev["rsi"] < p.rsi_oversold <= cur["rsi"]
                and not pd.isna(cur["sma_long"])
                and cur["close"] > cur["sma_long"]
            )
            if rsi_bounce:
                signals.append(
                    Signal(
                        symbol=symbol,
                        trade_date=cur["trade_date"],
                        signal_type=SignalType.BUY,
                        strategy_name=self.name,
                        reason="RSI 과매도 반등",
                        # 진입 사유가 둘인 전략이라 강도도 같은 자로 재야
                        # 서로 비교가 된다. 거래량 배수로 통일한다.
                        score=volume_ratio(cur),
                    )
                )
                continue

            dead_cross = prev["close"] >= prev["sma_short"] and cur["close"] < cur["sma_short"]
            if dead_cross:
                signals.append(
                    Signal(
                        symbol=symbol,
                        trade_date=cur["trade_date"],
                        signal_type=SignalType.SELL,
                        strategy_name=self.name,
                        reason="단기선 하향이탈",
                    )
                )
                continue

            if cur["rsi"] > p.rsi_overbought:
                signals.append(
                    Signal(
                        symbol=symbol,
                        trade_date=cur["trade_date"],
                        signal_type=SignalType.SELL,
                        strategy_name=self.name,
                        reason="RSI 과매수",
                    )
                )

        return signals
