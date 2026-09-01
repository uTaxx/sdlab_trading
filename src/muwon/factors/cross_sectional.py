"""전 종목을 한꺼번에 봐야 계산되는 Factor: 상대강도와 시장 국면.

이 둘은 예전 인터페이스에서는 **만들 수가 없었다.** generate_signals(symbol, df)
에는 다른 종목도 지수도 들어오지 않기 때문이다. Phase 1에서 판단 단위를
'종목'에서 '하루'로 올린 이유가 정확히 이것이다.

계산은 두 단계로 나뉜다. warmup()은 종목별 시계열을 한 번만 만들고,
prepare()는 날짜마다 그 표에서 그날 값을 꺼내 횡단면(순위·비율)을 구한다.
"""

from __future__ import annotations

import pandas as pd

from muwon.factors.base import Factor, percentile_scores
from muwon.strategy.portfolio import FactorResult, MarketContext


def _closes(df: pd.DataFrame) -> pd.Series:
    return df.set_index("trade_date")["close"]


class RelativeStrengthFactor(Factor):
    """남들보다 잘하고 있는가. 유니버스 안에서의 수익률 순위.

    절대 수익률(Momentum)과 다르다. 시장 전체가 20% 오른 구간에서 10% 오른
    종목은 절대로는 좋아 보여도 상대로는 하위권이다. 추세추종에서 오래
    검증된 변수라 인수인계서도 9항에서 따로 떼어 놓았다.

    기준지수를 넘겨받으면 초과수익(종목-지수)으로, 없으면 종목 수익률 자체로
    순위를 매긴다. 지수 조회가 막혀도 Factor가 통째로 죽지 않게 하려는 것이다."""

    key = "relative_strength"

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        self._period = int(self.params.get("period", 60))
        self._returns: dict[str, pd.Series] = {}
        for symbol, df in histories.items():
            if len(df) == 0:
                continue
            closes = _closes(df)
            self._returns[symbol] = (closes / closes.shift(self._period) - 1) * 100
        self._index_returns: pd.Series | None = None

    def prepare(self, ctx: MarketContext) -> None:
        if self._index_returns is None and ctx.index_history is not None and len(ctx.index_history):
            closes = _closes(ctx.index_history)
            self._index_returns = (closes / closes.shift(self._period) - 1) * 100

        index_return = None
        if self._index_returns is not None and ctx.as_of in self._index_returns.index:
            value = self._index_returns.loc[ctx.as_of]
            if pd.notna(value):
                index_return = float(value)

        self._raw = {}
        for symbol, series in self._returns.items():
            if ctx.as_of not in series.index:
                continue
            value = series.loc[ctx.as_of]
            if pd.isna(value):
                continue
            self._raw[symbol] = float(value) - index_return if index_return is not None else float(value)

        self._ranked = percentile_scores(self._raw)
        self._vs_index = index_return is not None

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        if symbol not in self._ranked:
            return FactorResult(self.key, None, "데이터 부족")
        basis = "지수 대비" if self._vs_index else "유니버스 내"
        return FactorResult(
            self.key,
            self._ranked[symbol],
            f"{self._period}일 상대강도 상위 {100 - self._ranked[symbol]:.0f}% "
            f"({basis} {self._raw[symbol]:+.1f}%)",
        )


#: Breadth(20일선 위 종목 비율)로 국면을 나눈다. 지수 데이터 없이도 판정할 수
#: 있어야 해서 우리 유니버스 자체를 시장의 대리 지표로 쓴다. 시총 상위
#: 60종목이면 시장 방향을 읽는 표본으로는 충분하다.
REGIME_SCORES = {"STRONG_BULL": 100.0, "BULL": 75.0, "NEUTRAL": 50.0, "BEAR": 20.0}


class MarketRegimeFactor(Factor):
    """시장 자체가 살 만한 환경인가. 개별 종목보다 상위 판단.

    아무리 좋은 종목도 시장이 무너지는 구간에서는 같이 빠진다. 그래서 이
    Factor는 점수에 들어갈 뿐 아니라, 국면에 따라 매수 기준선 자체를 올린다
    (약세장에서는 웬만한 점수로는 못 사게 한다).

    **평활화와 확정 지연이 반드시 필요하다.** 하루치 Breadth를 그대로 쓰면
    국면이 며칠마다 뒤집힌다. 2022년 실측에서 245거래일 동안 67번 바뀌었다
    (평균 3.7일마다). 하락장 한복판의 2~3일 반등에 STRONG_BULL이 선언되고
    바로 그때 사서 손절당하는 일이 실제로 일어났다(STRONG_BULL 진입 7건 중
    6건 손실). 3.7일마다 바뀌는 건 국면이 아니라 잡음이다.

    두 장치 모두 과거만 본다. 평활화는 뒤를 보는 이동평균이고, 확정 지연은
    '최근 N일이 같은 판정이었는가'를 묻는다. 앞을 보면 그건 미래참조다.
    """

    key = "market_regime"

    def warmup(self, histories: dict[str, pd.DataFrame]) -> None:
        short_ma = int(self.params.get("short_ma", 20))
        long_ma = int(self.params.get("long_ma", 60))
        smoothing = int(self.params.get("breadth_smoothing", 10))
        confirm_days = int(self.params.get("confirm_days", 5))
        uptrend_ma = int(self.params.get("uptrend_ma", 0))
        uptrend_slope = int(self.params.get("uptrend_slope", 0))

        above_short, above_long, daily_returns = [], [], []
        for df in histories.values():
            if len(df) == 0:
                continue
            closes = _closes(df)
            long_line = closes.rolling(long_ma).mean()
            # 이동평균이 아직 안 만들어진 구간은 NaN으로 남겨 '집계 대상 아님'이
            # 되게 한다. 0으로 채우면 상장 초기 종목이 전부 '선 아래'로 잡혀
            # Breadth가 실제보다 낮게 나온다.
            valid = long_line.notna()
            above_short.append((closes > closes.rolling(short_ma).mean()).where(valid))
            above_long.append((closes > long_line).where(valid))
            daily_returns.append(closes.pct_change())

        self.regime: str | None = None
        self.breadth_short = self.breadth_long = 0.0
        self._by_date: dict = {}
        self.uptrend_days = self.total_days = 0
        if not above_short:
            return

        short_pct = pd.concat(above_short, axis=1).mean(axis=1) * 100
        long_pct = pd.concat(above_long, axis=1).mean(axis=1) * 100
        # 평활화: 그날 하루가 아니라 최근 며칠의 평균으로 본다
        short_pct = short_pct.rolling(smoothing, min_periods=1).mean()
        long_pct = long_pct.rolling(smoothing, min_periods=1).mean()

        uptrend = self._market_uptrend(daily_returns, uptrend_ma, uptrend_slope, short_pct.index)
        # 필터가 실제로 몇 날이나 강세 선언을 막았는지. 이걸 안 남기면
        # '필터가 효과 없었다'와 '필터가 켜지지도 않았다'를 구분할 수 없다.
        # 58종목 스윕에서 네 설정이 완전히 같은 결과를 내서 실제로 막혔다.
        self.uptrend_days = int(uptrend.sum())
        self.total_days = len(uptrend)

        raw = [
            self._classify(s, long, up) if pd.notna(s) and pd.notna(long) else None
            for s, long, up in zip(short_pct, long_pct, uptrend, strict=True)
        ]
        confirmed = self._confirm(raw, confirm_days)

        self._by_date = {
            day: (regime, float(s), float(long))
            for day, regime, s, long in zip(
                short_pct.index, confirmed, short_pct, long_pct, strict=True
            )
            if regime is not None and pd.notna(s) and pd.notna(long)
        }

    @staticmethod
    def _confirm(raw: list, confirm_days: int) -> list:
        """같은 판정이 confirm_days일 연속돼야 국면 전환으로 인정한다.

        직전 확정값을 유지하는 방식이라 앞을 보지 않는다. 오늘의 국면은
        오늘까지의 판정만으로 정해진다."""
        confirmed, current, run_label, run_len = [], None, None, 0
        for label in raw:
            if label == run_label:
                run_len += 1
            else:
                run_label, run_len = label, 1
            if label is not None and run_len >= confirm_days:
                current = label
            confirmed.append(current)
        return confirmed

    def prepare(self, ctx: MarketContext) -> None:
        found = self._by_date.get(ctx.as_of)
        if found is None:
            self.regime = None
            self.breadth_short = self.breadth_long = 0.0
            return
        self.regime, self.breadth_short, self.breadth_long = found

    @staticmethod
    def _market_uptrend(daily_returns: list, window: int, slope_days: int, index) -> pd.Series:
        """유니버스 동일가중 지수가 자기 장기 이동평균 위에 있는가.

        Breadth만으로는 '하락 추세 안의 반등'과 '추세 전환'이 구분되지 않는다.
        Breadth는 수준 지표라 가격이 이미 오른 뒤에야 올라가고, 그래서 반등
        꼭지에서 최고값을 낸다. 2022년에 04-04·05-04·08-22를 BULL로,
        11-25를 STRONG_BULL로 선언한 게 전부 그 자리였다. 수준 외에 방향이
        필요하다.

        지수는 종목별 일간 수익률의 평균을 누적해서 만든다. 종가를 그냥
        평균하면 주가가 비싼 종목이 지수를 지배하고, 첫날 종가로 나눠 맞추면
        중간에 상장한 종목이 지수를 튀게 한다. 수익률 평균은 둘 다 피한다.

        slope_days를 주면 '평균선 자체가 오르고 있는가'까지 함께 본다.
        '지수가 평균 위' 하나만으로는 부족했다. 58종목 실측에서 이 조건이
        529일 중 152일만 통과했는데도 2022년 강세 오판이 그대로 남았다.
        하락 추세에서도 반등이 크면 가격이 잠시 평균선을 넘고, 그 자리가
        정확히 반등 꼭지다. 평균선의 기울기는 그때도 아직 아래를 향한다.

        window가 0이면 이 조건을 쓰지 않는다(전부 True): 검증 전에는 켜지
        않는다."""
        if window <= 0 or not daily_returns:
            return pd.Series(True, index=index)
        market = pd.concat(daily_returns, axis=1).mean(axis=1).fillna(0.0)
        proxy = (1 + market).cumprod()
        line = proxy.rolling(window, min_periods=window).mean()
        # min_periods를 채우기 전에는 판단을 보류하고 False로 둔다. 지수가
        # 자기 평균 위인지 모르는 상태에서 강세를 선언할 이유가 없다.
        above = proxy > line
        if slope_days <= 0:
            return above
        return above & (line > line.shift(slope_days))

    def _classify(self, short_pct: float, long_pct: float, uptrend: bool = True) -> str:
        strong = float(self.params.get("strong_bull_breadth", 65))
        bull = float(self.params.get("bull_breadth", 50))
        bear = float(self.params.get("bear_breadth", 40))
        if short_pct >= strong and long_pct >= strong and uptrend:
            return "STRONG_BULL"
        if short_pct >= bull and long_pct >= bull and uptrend:
            return "BULL"
        if long_pct < bear:
            return "BEAR"
        return "NEUTRAL"

    def score(self, symbol: str, ctx: MarketContext) -> FactorResult:
        """국면 점수는 전 종목에 같은 값이 들어간다.

        종목을 고르는 데는 기여하지 않지만(다 같은 점수라 순위가 안 바뀐다),
        총점을 끌어내려 '약세장에서는 아무것도 안 사게' 만드는 역할을 한다."""
        if self.regime is None:
            return FactorResult(self.key, None, "국면 판정 불가")
        return FactorResult(
            self.key,
            REGIME_SCORES[self.regime],
            f"{self.regime} (20일선 위 {self.breadth_short:.0f}%, "
            f"60일선 위 {self.breadth_long:.0f}%)",
        )
