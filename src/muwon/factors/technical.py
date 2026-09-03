"""종목 하나만 보고 계산하는 Factor들: Trend / Momentum / Pullback / Volume.

이 넷은 예전 구조에서도 만들 수 있었던 것들이다. 달라진 건 참/거짓이 아니라
0~100 점수를 돌려준다는 점, 그리고 왜 그 점수인지 문장을 함께 남긴다는 점이다.

성능에 관해: 백테스트는 evaluate()를 거래일 수만큼(수백~수천 번) 호출한다.
날짜마다 이동평균을 처음부터 다시 계산하면 같은 값을 수백 번 구하게 되고,
실제로 그 방식이 다른 전략보다 25배 느렸다(18종목 730일 기준 30초 vs 1.2초).
그래서 지표는 warmup()에서 **종목별 시계열로 한 번만** 계산하고, 날짜별
호출은 그 표에서 값을 꺼내 오기만 한다.

rolling 계산을 미리 해 두는 게 미래를 보는 게 아닌 이유: rolling(w).mean()의
i번째 값은 i 이전 데이터로만 만들어진다. 특정 날짜의 값을 꺼내 쓰는 한
그 시점 이후 데이터는 섞이지 않는다.
"""

from __future__ import annotations

from itertools import pairwise
from typing import ClassVar

import pandas as pd

from muwon.factors.base import Factor, percentile_scores, piecewise, ratio_score
from muwon.strategy.portfolio import FactorResult, MarketContext

MISSING = FactorResult  # 가독성용 별칭 (반환 타입이 길어 읽기 어려워서)


def _indexed(df: pd.DataFrame) -> pd.DataFrame:
    return df.set_index("trade_date")


class TrendFactor(Factor):
    """정배열이 얼마나 완성됐는가.

    '20일선 위에 있다'를 참/거짓으로 보면 40일선 위에 겨우 걸친 종목과
    모든 선 위에서 완만히 오르는 종목이 같은 취급을 받는다. 사다리를 여러
    칸으로 나눠 몇 칸을 올라섰는지로 본다."""

    key = "trend"

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        self.windows = [int(w) for w in self.params.get("mas", [20, 60, 120])]
        self._table: dict[str, pd.DataFrame] = {}
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            indexed = _indexed(df)
            table = pd.DataFrame(index=indexed.index)
            table["close"] = indexed["close"]
            for w in self.windows:
                table[f"ma{w}"] = indexed["close"].rolling(w).mean()
            self._table[symbol] = table

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        row = self._row(symbol, ctx.as_of)
        if row is None:
            return FactorResult(self.key, None, "데이터 부족")

        price = float(row["close"])
        mas = {w: float(row[f"ma{w}"]) for w in self.windows}
        checks: list[tuple[bool, str]] = [(price > mas[w], f"종가>{w}일선") for w in self.windows]
        # 선끼리의 순서(정배열)도 본다. 가격만 보면 급등 하루로 만점이 나온다
        for short, long in pairwise(self.windows):
            checks.append((mas[short] > mas[long], f"{short}>{long}일선"))

        passed = [label for ok, label in checks if ok]
        detail = ", ".join(passed) if passed else "역배열"
        return FactorResult(
            self.key,
            ratio_score(len(passed), len(checks)),
            f"정배열 {len(passed)}/{len(checks)} ({detail})",
        )

    def _row(self, symbol, as_of):
        table = self._table.get(symbol)
        if table is None or as_of not in table.index:
            return None
        row = table.loc[as_of]
        return None if row.isna().any() else row


class MomentumFactor(Factor):
    """이 종목 자체가 오르고 있는가. 여러 기간을 섞어 본다.

    최근 수익률만 보면 하루 급등에 속는다. 인수인계서 8.2항대로 장기
    모멘텀에 더 큰 비중을 둔다. 절대 수익률을 점수로 바꿀 때 임계값을 손으로
    정하면 시장 국면에 따라 전 종목이 0점이나 100점이 되므로, 혼합 수익률을
    유니버스 안에서의 백분위로 바꾼다."""

    key = "momentum"
    DEFAULT_WEIGHTS: ClassVar[dict[int, float]] = {5: 0.15, 20: 0.20, 60: 0.30, 120: 0.35}

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        raw = self.params.get("weights") or self.DEFAULT_WEIGHTS
        self._weights = {int(k): float(v) for k, v in raw.items()}
        self._returns: dict[str, pd.DataFrame] = {}
        self._blend: dict[str, pd.Series] = {}

        weight_series = pd.Series(self._weights, dtype=float)
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            closes = _indexed(df)["close"]
            returns = pd.DataFrame(
                {p: (closes / closes.shift(p) - 1) * 100 for p in self._weights}
            )
            # 계산된 기간의 가중치만으로 다시 나눈다. 상장한 지 얼마 안 된
            # 종목이 '장기 수익률 없음' 때문에 무조건 낮은 값을 받으면 안 된다
            used = returns.notna().mul(weight_series, axis=1).sum(axis=1)
            total = returns.fillna(0).mul(weight_series, axis=1).sum(axis=1)
            self._returns[symbol] = returns
            self._blend[symbol] = (total / used).where(used > 0)

    def prepare(self, ctx: MarketContext) -> None:
        values = {}
        for symbol, series in self._blend.items():
            if ctx.as_of in series.index:
                value = series.loc[ctx.as_of]
                if pd.notna(value):
                    values[symbol] = float(value)
        self._today = values
        self._ranked = percentile_scores(values)

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        if symbol not in self._ranked:
            return FactorResult(self.key, None, "데이터 부족")
        row = self._returns[symbol].loc[ctx.as_of]
        detail = ", ".join(f"{p}일 {row[p]:+.1f}%" for p in self._weights if pd.notna(row[p]))
        return FactorResult(
            self.key, self._ranked[symbol], f"모멘텀 {self._today[symbol]:+.1f}% ({detail})"
        )


class PullbackFactor(Factor):
    """상승 추세 중의 눌림인가.

    많이 떨어졌다고 좋은 게 아니다(인수인계서 10항). 추세가 살아 있는 상태의
    -4~-6% 조정이 가장 높고, 그보다 조정 폭이 작으면 기회가 아니며, 너무 깊으면 추세
    훼손으로 본다. 장기선 아래로 내려간 종목은 아예 눌림으로 치지 않는다."""

    key = "pullback"
    #: (조정폭 %, 점수): 오름차순
    DEFAULT_CURVE: ClassVar[list[tuple[float, float]]] = [
        (-12.0, 10.0),
        (-8.0, 70.0),
        (-6.0, 100.0),
        (-4.0, 70.0),
        (-2.0, 30.0),
        (0.0, 10.0),
    ]

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        self.lookback = int(self.params.get("lookback", 5))
        self.trend_ma = int(self.params.get("trend_ma", 60))
        self._curve = [(float(x), float(y)) for x, y in (self.params.get("curve") or self.DEFAULT_CURVE)]
        self._table = {}
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            closes = _indexed(df)["close"]
            self._table[symbol] = pd.DataFrame(
                {
                    "close": closes,
                    "long_ma": closes.rolling(self.trend_ma).mean(),
                    "recent_high": closes.rolling(self.lookback + 1).max(),
                }
            )

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        table = self._table.get(symbol)
        if table is None or ctx.as_of not in table.index:
            return FactorResult(self.key, None, "데이터 부족")
        row = table.loc[ctx.as_of]
        if row.isna().any() or row["recent_high"] <= 0:
            return FactorResult(self.key, None, "데이터 부족")

        if row["close"] <= row["long_ma"]:
            return FactorResult(self.key, 0.0, f"{self.trend_ma}일선 아래: 눌림이 아니라 하락")

        dip_pct = (row["close"] / row["recent_high"] - 1) * 100
        return FactorResult(
            self.key,
            piecewise(dip_pct, self._curve),
            f"최근 {self.lookback}일 고점 대비 {dip_pct:+.1f}% (추세 유지)",
        )


class VolumeFactor(Factor):
    """관심이 몰렸는가. 평균 거래량 대비 배수.

    유동성 하한도 여기서 본다. 거래대금이 너무 작은 종목은 신호가 맞아도
    실제로는 원하는 가격에 못 산다(인수인계서 12항)."""

    key = "volume"
    DEFAULT_CURVE: ClassVar[list[tuple[float, float]]] = [
        (0.5, 0.0),
        (1.0, 30.0),
        (1.5, 60.0),
        (2.0, 80.0),
        (3.0, 100.0),
    ]

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        window = int(self.params.get("ma_window", 20))
        self._min_turnover = float(self.params.get("min_turnover_krw", 0))
        self._curve = [(float(x), float(y)) for x, y in (self.params.get("curve") or self.DEFAULT_CURVE)]
        self._table = {}
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            indexed = _indexed(df)
            self._table[symbol] = pd.DataFrame(
                {
                    "close": indexed["close"],
                    "volume": indexed["volume"],
                    "volume_ma": indexed["volume"].rolling(window).mean(),
                }
            )

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        table = self._table.get(symbol)
        if table is None or ctx.as_of not in table.index:
            return FactorResult(self.key, None, "데이터 부족")
        row = table.loc[ctx.as_of]
        if pd.isna(row["volume_ma"]) or row["volume_ma"] <= 0:
            return FactorResult(self.key, None, "거래량 데이터 없음")

        if self._min_turnover > 0:
            turnover = float(row["volume_ma"]) * float(row["close"])
            if turnover < self._min_turnover:
                return FactorResult(
                    self.key, 0.0, f"유동성 미달 (평균 거래대금 {turnover / 1e8:.1f}억)"
                )

        ratio = float(row["volume"]) / float(row["volume_ma"])
        return FactorResult(self.key, piecewise(ratio, self._curve), f"거래량 평균 대비 {ratio:.1f}배")
