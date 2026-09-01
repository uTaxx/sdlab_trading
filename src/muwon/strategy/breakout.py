"""돌파(breakout)·모멘텀 계열 전략. "박스를 뚫고 나가면 그 방향으로 간다"에
베팅한다. 단타에서 가장 흔히 쓰이는 계열이다.

추세추종과 비슷하지만 진입 근거가 "선의 교차"가 아니라 "특정 가격대·
거래량의 돌파"라는 점이 다르다. 가짜 돌파(뚫는 척하고 되돌아옴)가 최대
약점이라, 대부분 거래량 급증 같은 확인 조건을 함께 건다."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from muwon.domain.interfaces import Strategy
from muwon.domain.types import Signal, SignalType
from muwon.indicators.technical import add_bollinger, add_indicators
from muwon.strategy.common import (
    crossed_above,
    crossed_below,
    has_nan,
    make_signal,
    pct_above,
    volume_ratio,
)


@dataclass(frozen=True)
class BollingerBreakoutParams:
    window: int = 20
    num_std: float = 2.0
    volume_ma_window: int = 20
    volume_surge_ratio: float = 1.5  # 1.0이면 거래량 조건 없음


class BollingerBreakoutStrategy(Strategy):
    """볼린저밴드 상단 돌파: 평균회귀 버전과 정반대로 해석한다.

    같은 지표를 놓고 "상단을 뚫었으니 과열이라 곧 떨어진다"(평균회귀)로
    볼 수도, "상단을 뚫을 만큼 힘이 강하니 더 간다"(돌파)로 볼 수도 있다.
    어느 쪽이 맞는지는 시장 국면에 따라 다르므로, 두 해석을 각각 가설로
    등록해 실제 데이터로 비교하려고 둘 다 구현했다."""

    def __init__(
        self, params: BollingerBreakoutParams | None = None, name: str = "bollinger_breakout"
    ):
        self.params = params or BollingerBreakoutParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_bollinger(price_history, window=p.window, num_std=p.num_std)
        df["volume_ma"] = df["volume"].rolling(window=p.volume_ma_window).mean()

        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["bb_upper", "bb_mid"]) or has_nan(prev, ["bb_upper", "bb_mid"]):
                continue

            if crossed_above(prev["close"], cur["close"], prev["bb_upper"], cur["bb_upper"]):
                if p.volume_surge_ratio > 1.0 and (
                    pd.isna(cur["volume_ma"])
                    or cur["volume_ma"] <= 0
                    or cur["volume"] < cur["volume_ma"] * p.volume_surge_ratio
                ):
                    continue
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        "볼린저 상단 돌파",
                        score=pct_above(cur["close"], cur["bb_upper"]),
                    )
                )
            # 청산은 상태 판정: 갭 하락으로 중심선을 훌쩍 밑돈 채 머무르면
            # 교차 사건이 안 생겨 청산 신호를 놓친다(엔진은 보유 중일 때만 매도).
            elif cur["close"] < cur["bb_mid"]:
                signals.append(make_signal(symbol, cur, SignalType.SELL, self.name, "볼린저 중심선 이탈"))
        return signals


@dataclass(frozen=True)
class VolumeSurgeParams:
    volume_ma_window: int = 20
    volume_surge_ratio: float = 2.0
    min_price_change_pct: float = 2.0  # 거래량만 늘고 가격은 그대로인 경우 제외
    sma_short: int = 20
    holding_days: int = 5  # 진입 후 이 일수가 지나면 청산(시간 기반 청산)
    #: 종가가 이 이동평균 아래로 내려오면 매도 신호를 낸다. None이면 안 낸다.
    #:
    #: 기본값을 None으로 둔 이유는, 지금 쓰는 volume_surge_5d가 "시간이 되면
    #: 판다"는 가설이고 그 성적이 이미 기록돼 있기 때문이다. 여기에 매도 신호를
    #: 끼워 넣으면 같은 이름의 전략이 다른 것이 되어 과거 성적과 비교할 수 없다.
    #: 매도 신호를 쓰는 쪽은 별도 이름으로 등록해 나란히 놓고 비교한다.
    exit_sma: int | None = None


class VolumeSurgeStrategy(Strategy):
    """거래량 급증 + 가격 상승: 세력이 들어왔다고 보는 단타의 기본 패턴.

    청산이 지표가 아니라 시간 기준이다(holding_days). "재료가 터진 날 들어가서
    며칠 안에 나온다"는 단타 습성을 그대로 옮긴 것이다.

    `exit_sma`를 주면 매도 신호도 낸다. 종가가 그 이동평균 아래로 내려온 날
    파는 것으로, "정해진 날짜가 아니라 추세가 깨질 때 판다"는 가설이다.
    둘 중 어느 쪽이 나은지는 백테스트로 비교한다."""

    def __init__(self, params: VolumeSurgeParams | None = None, name: str = "volume_surge"):
        self.params = params or VolumeSurgeParams()
        self.name = name
        # 청산은 엔진이 '실제 보유일'로 집행한다. 전에는 이 전략이 스스로
        # entry_index로 보유 상태를 기억했는데, 그건 엔진이 정말 샀는지와
        # 무관한 값이었다. 리스크 한도로 매수가 거부된 종목도 전략은 샀다고
        # 믿고 그 뒤 며칠간 신호를 막았다. 자리 경쟁이 생기는 넓은 유니버스에서
        # 특히 해롭다. 전략은 "지금 이 종목이 매력적인가"만 답한다.
        self.max_holding_days = self.params.holding_days

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        # 매도선을 따로 주면 그 창으로 이동평균을 낸다. sma_short를 그대로
        # 쓰면 exit_sma=10을 줘도 20일선을 보게 되어, 화면에는 10일선이라고
        # 적히고 실제로는 20일선으로 파는 상태가 된다.
        평균창 = p.exit_sma if p.exit_sma is not None else p.sma_short
        df = add_indicators(
            price_history, sma_short=평균창, volume_ma_window=p.volume_ma_window
        )
        signals: list[Signal] = []

        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]

            if has_nan(cur, ["volume_ma"]):
                continue

            # 매도 신호를 먼저 본다. 같은 날 두 신호가 다 서면 파는 쪽이
            # 먼저다. 이 저장소는 언제나 위험을 줄이는 쪽을 앞에 둔다.
            if p.exit_sma is not None and not has_nan(cur, ["sma_short"]):
                # 어제는 위에 있었는데 오늘 아래로 내려온 날에만 낸다.
                # 아래에 머무는 동안 매일 신호를 내면 이미 판 종목에 대고
                # 같은 신호가 쌓이고, 기록에서 "왜 팔았나"가 흐려진다.
                내려왔나 = (
                    not has_nan(prev, ["sma_short"])
                    and prev["close"] >= prev["sma_short"]
                    and cur["close"] < cur["sma_short"]
                )
                if 내려왔나:
                    signals.append(
                        make_signal(
                            symbol, cur, SignalType.SELL, self.name,
                            f"{p.exit_sma}일 평균선 아래로 내려왔습니다",
                        )
                    )
                    continue

            price_change_pct = (cur["close"] / prev["close"] - 1) * 100 if prev["close"] > 0 else 0.0
            volume_surged = cur["volume_ma"] > 0 and cur["volume"] >= cur["volume_ma"] * p.volume_surge_ratio
            if volume_surged and price_change_pct >= p.min_price_change_pct:
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        f"거래량 {p.volume_surge_ratio:g}배 급증 + {price_change_pct:.1f}% 상승",
                        # 이 전략의 진입 근거가 거래량이므로 배수가 곧 강도다
                        score=volume_ratio(cur),
                    )
                )
        return signals


@dataclass(frozen=True)
class PriceChannelParams:
    lookback: int = 20  # 최근 N일 종가 기준 채널
    breakout_pct: float = 0.0  # 신고가 대비 몇 % 더 올라야 인정할지 (가짜 돌파 필터)
    exit_sma: int = 20  # 이 이동평균 아래로 내려오면 청산


class PriceChannelBreakoutStrategy(Strategy):
    """종가 기준 N일 신고가 돌파: 돈치안(고가 기준)의 종가 버전이다.

    장중 잠깐 찍은 고가가 아니라 종가로만 판정하기 때문에 장중 흔들림에
    덜 반응한다. breakout_pct를 주면 "신고가를 살짝 넘긴 정도"는 무시하고
    확실히 뚫었을 때만 진입한다."""

    def __init__(
        self, params: PriceChannelParams | None = None, name: str = "price_channel_breakout"
    ):
        self.params = params or PriceChannelParams()
        self.name = name

    def generate_signals(self, symbol: str, price_history: pd.DataFrame) -> list[Signal]:
        p = self.params
        df = add_indicators(price_history, sma_short=p.exit_sma)
        # 직전 N일(오늘 제외) 종가 최고치: 오늘을 포함하면 항상 참이 된다
        df["channel_high"] = df["close"].rolling(window=p.lookback).max().shift(1)

        signals: list[Signal] = []
        for i in range(1, len(df)):
            prev, cur = df.iloc[i - 1], df.iloc[i]
            if has_nan(cur, ["channel_high"]):
                continue

            threshold = cur["channel_high"] * (1 + p.breakout_pct / 100)
            if cur["close"] > threshold >= prev["close"]:
                signals.append(
                    make_signal(
                        symbol,
                        cur,
                        SignalType.BUY,
                        self.name,
                        f"{p.lookback}일 종가 신고가 돌파",
                        score=pct_above(cur["close"], threshold),
                    )
                )
            elif not has_nan(cur, ["sma_short"]) and not has_nan(prev, ["sma_short"]) and crossed_below(
                prev["close"], cur["close"], prev["sma_short"], cur["sma_short"]
            ):
                signals.append(
                    make_signal(symbol, cur, SignalType.SELL, self.name, f"{p.exit_sma}일선 하향이탈")
                )
        return signals
