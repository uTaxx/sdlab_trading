"""추세추종(trend following) 계열 전략. "오르는 놈이 더 오른다"에 베팅한다.

공통 성질: 상승 추세가 확인된 뒤에 들어가므로 바닥에서 사지는 못하고,
추세가 꺾인 뒤에 나오므로 꼭지에서 팔지도 못한다. 대신 큰 추세 한 번을
크게 먹는 걸 노린다. 그래서 승률은 낮고(자주 틀림) 손익비는 높은
(맞을 때 크게 먹는) 패턴이 정상이다. 횡보장에서 잦은 헛신호(휩쏘)로
조금씩 잃는 게 이 계열의 전형적인 약점이다."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal, SignalType
from muwon.indicators.technical import (
    add_adx,
    add_donchian,
    add_ema_pair,
    add_indicators,
    add_macd,
)
from muwon.strategy.common import (
    crossed_above,
    crossed_below,
    has_nan,
    make_signal,
    pct_above,
    volume_ratio,
)


@dataclass(frozen=True)
class GoldenCrossParams:
    sma_short: int = 20
    sma_long: int = 60


class GoldenCrossStrategy(Strategy):
    """골든크로스/데드크로스: 가장 고전적인 추세추종.

    단기 이동평균선이 장기 이동평균선을 위로 뚫으면(골든크로스) 매수,
    아래로 뚫으면(데드크로스) 매도. 가격이 이동평균선을 넘는지가 아니라
    "선끼리" 교차하는 걸 보기 때문에 신호가 드물고 느리지만 그만큼
    헛신호도 적다."""

    def __init__(self, params: GoldenCrossParams | None = None, name: str = "golden_cross"):
        self.params = params or GoldenCrossParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_indicators(price_history, sma_short=p.sma_short, sma_long=p.sma_long)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["sma_short", "sma_long"]) or has_nan(prev, ["sma_short", "sma_long"]):
                continue
            if crossed_above(prev["sma_short"], cur["sma_short"], prev["sma_long"], cur["sma_long"]):
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        "골든크로스(단기선이 장기선 상향돌파)",
                        score=volume_ratio(cur),
                    )
                )
            elif crossed_below(
                prev["sma_short"], cur["sma_short"], prev["sma_long"], cur["sma_long"]
            ):
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, "데드크로스(단기선이 장기선 하향이탈)")
                )
        return signals


@dataclass(frozen=True)
class MacdCrossParams:
    fast: int = 12
    slow: int = 26
    signal: int = 9
    require_positive_macd: bool = False  # True면 0선 위(=상승 국면)에서의 교차만 매수


class MacdCrossStrategy(Strategy):
    """MACD 신호선 교차: 추세 전환을 이동평균보다 조금 빨리 잡으려는 전략.

    MACD선이 신호선을 위로 뚫으면 매수, 아래로 뚫으면 매도.
    require_positive_macd를 켜면 MACD가 0보다 클 때(이미 상승 국면일 때)의
    교차만 인정해서, 하락장 중의 반짝 반등을 걸러낸다."""

    def __init__(self, params: MacdCrossParams | None = None, name: str = "macd_cross"):
        self.params = params or MacdCrossParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_macd(price_history, fast=p.fast, slow=p.slow, signal=p.signal)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["macd", "macd_signal"]) or has_nan(prev, ["macd", "macd_signal"]):
                continue
            if crossed_above(prev["macd"], cur["macd"], prev["macd_signal"], cur["macd_signal"]):
                if p.require_positive_macd and cur["macd"] <= 0:
                    continue
                # 신호선과 벌어진 폭이 클수록 강한 전환. 주가로 나눠야
                # 5만원짜리와 50만원짜리를 같은 자로 잴 수 있다.
                gap_pct = (cur["macd"] - cur["macd_signal"]) / cur["close"] * 100 if cur["close"] > 0 else 0.0
                signals.append(
                    make_signal(symbol, cur, SignalType.BUY, self.name, "MACD 신호선 상향돌파", score=float(gap_pct))
                )
            elif crossed_below(prev["macd"], cur["macd"], prev["macd_signal"], cur["macd_signal"]):
                signals.append(make_signal(symbol, cur, SignalType.SELL, self.name, "MACD 신호선 하향이탈"))
        return signals


@dataclass(frozen=True)
class EmaCrossParams:
    ema_short: int = 12
    ema_long: int = 26


class EmaCrossStrategy(Strategy):
    """지수이동평균 교차: 골든크로스와 같은 아이디어지만, 최근 가격에 더
    큰 가중치를 주는 지수이동평균을 써서 더 빨리 반응한다(그만큼 헛신호도
    늘어난다)."""

    def __init__(self, params: EmaCrossParams | None = None, name: str = "ema_cross"):
        self.params = params or EmaCrossParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_ema_pair(price_history, ema_short=p.ema_short, ema_long=p.ema_long)
        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["ema_short", "ema_long"]) or has_nan(prev, ["ema_short", "ema_long"]):
                continue
            if crossed_above(prev["ema_short"], cur["ema_short"], prev["ema_long"], cur["ema_long"]):
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        "EMA 골든크로스",
                        score=pct_above(cur["ema_short"], cur["ema_long"]),
                    )
                )
            elif crossed_below(
                prev["ema_short"], cur["ema_short"], prev["ema_long"], cur["ema_long"]
            ):
                signals.append(make_signal(symbol, cur, SignalType.SELL, self.name, "EMA 데드크로스"))
        return signals


@dataclass(frozen=True)
class DonchianBreakoutParams:
    entry_window: int = 20  # N일 신고가 돌파 시 매수
    exit_window: int = 10  # M일 신저가 이탈 시 매도
    adx_filter: float = 0.0  # 0보다 크면 ADX가 이 값 이상일 때만 매수(추세장 필터)


class DonchianBreakoutStrategy(Strategy):
    """돈치안 채널 돌파: 이른바 "터틀 트레이딩"의 핵심 규칙.

    최근 N일 최고가를 넘으면 매수하고, 최근 M일 최저가를 깨면 매도한다.
    진입 창(20일)보다 청산 창(10일)을 짧게 두는 게 정석인데, 수익은 길게
    끌고 손실은 빨리 끊기 위해서다.

    adx_filter를 켜면 추세 강도(ADX)가 일정 이상일 때만 진입해서, 횡보장
    돌파(대개 가짜 돌파)를 걸러낸다."""

    def __init__(
        self, params: DonchianBreakoutParams | None = None, name: str = "donchian_breakout"
    ):
        self.params = params or DonchianBreakoutParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_donchian(price_history, window=p.entry_window)
        exit_df = add_donchian(price_history, window=p.exit_window)
        df["dc_exit_lower"] = exit_df["dc_lower"]
        if p.adx_filter > 0:
            df["adx"] = add_adx(price_history)["adx"]

        signals: list[Signal] = []
        for i in range(1, len(df)):
            cur = df.iloc[i]
            if has_nan(cur, ["dc_upper", "dc_exit_lower"]):
                continue
            if cur["close"] > cur["dc_upper"]:
                if p.adx_filter > 0 and (pd.isna(cur["adx"]) or cur["adx"] < p.adx_filter):
                    continue
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        f"{p.entry_window}일 신고가 돌파",
                        score=pct_above(cur["close"], cur["dc_upper"]),
                    )
                )
            elif cur["close"] < cur["dc_exit_lower"]:
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, f"{p.exit_window}일 신저가 이탈")
                )
        return signals
