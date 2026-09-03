"""미국 섹터가 강하면 국내 같은 섹터 종목을 산다.

설계안 §48~§49에서 대조군을 이긴 규칙을 등록용으로 옮긴 것이다. 등록해야
매일 17:50 검토가 계산하고, 순위에 들면 그림자 추적(§10)에 남는다.

## 규칙

미국 섹터 ETF의 N일 수익률에서 S&P 500(SPY)의 N일 수익률을 뺀 것이
상대강도다. 상대강도 상위 k개이면서 ETF 종가가 자기 60일 이동평균 위에
있으면 강한 섹터다. 그 섹터의 국내 종목 중 종가가 20일 이동평균 위이고 N일
수익률이 플러스인 것을 산다. 섹터가 강한 섹터에서 빠지거나 종가가 20일
이동평균 아래로 내려오면 판다.

미국 시세는 하루 미룬다. 한국 저녁에 판단할 때 그날 미국 장은 아직 열리지
않았다.

## 미국 시세를 못 받으면 아무것도 안 산다

이 전략은 다른 전략과 달리 국내 시세 밖의 자료가 필요하다. 엔진은 국내
시세만 넘겨주므로 `prepare()`에서 직접 받는다. 못 받으면 매수 신호를 내지
않고 경고를 남긴다. 조용히 국내 신호만으로 사면 "미국을 보고 산 것"이
아닌데 그렇게 기록된다.

## 매매 대상에 섹터 종목이 없으면 미국 시세를 안 받는다

테스트가 등록된 전략 전부를 가짜 종목 하나로 돌린다. 그 종목은 어느 섹터에도
없으므로 살 것이 없고, 그럴 때 미국 시세를 받으러 나가면 테스트가 네트워크에
걸린다. 섹터 종목이 하나도 없으면 받지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from loguru import logger

from muwon.domain.types import Signal, SignalType
from muwon.sector.catalog import CATALOG
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy

#: 국내 섹터 코드 → 미국 섹터 ETF. 설계안 §48의 기본 짝이다.
섹터짝: dict[str, str] = {
    "SEMI": "SOXX", "BATT": "LIT", "BIO": "XBI", "AUTO": "CARZ",
    "DEF": "ITA", "POWER": "GRID", "ROBO": "BOTZ",
}
기준지수 = "SPY"
#: 미국 지표 예열. 60일 이동평균과 N일 수익률이 나오려면 이만큼 앞이 있어야 한다.
예열날수 = 400

@dataclass(frozen=True)
class USSectorParams:
    """화면 설명(strategy_rules)이 이 이름으로 찾는다."""

    N: int = 60
    k: int = 2
    지연: int = 1
    보유상한: int = 20


@dataclass(frozen=True)
class USSectorGateParams:
    """기존 전략 위에 미국 섹터 신호를 얹은 것(설계안 §50)의 설정."""

    원래키: str
    N: int = 60
    k: int = 2
    지연: int = 0


#: (심볼, 시작, 끝) → trade_date·close가 있는 DataFrame. 테스트는 가짜를 넣는다.
시세가져오기 = Callable[[str, date, date], pd.DataFrame]


def 섹터표만들기() -> dict[str, str]:
    """종목코드 → 섹터코드."""
    return {m.symbol: s.코드 for s in CATALOG for m in s.종목}


def 야후에서가져오기(심볼: str, 시작: date, 끝: date) -> pd.DataFrame:
    from muwon.data.price_cache import PriceCache
    from muwon.data.yahoo_client import YahooFinanceDataSource

    return PriceCache(".cache/prices.sqlite").fetch(
        YahooFinanceDataSource(), 심볼, 심볼, 시작, 끝,
    )


def 강한섹터계산(
    미국: dict[str, pd.DataFrame], 국내날들: pd.DatetimeIndex,
    N: int, k: int, 지연: int, 추세창: int = 60,
) -> pd.Series:
    """국내 거래일마다 강한 섹터 코드 집합. 미국 휴장일은 앞 값으로 채우고 `지연`만큼 민다."""
    def 정렬(심볼: str) -> pd.Series:
        s = 미국[심볼].set_index("trade_date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        return s.reindex(국내날들, method="ffill").shift(지연)

    기준수익 = 정렬(기준지수).pct_change(N)
    상대, 추세 = {}, {}
    for 코드, 심볼 in 섹터짝.items():
        s = 정렬(심볼)
        상대[코드] = s.pct_change(N) - 기준수익
        추세[코드] = s > s.rolling(추세창).mean()
    상대표, 추세표 = pd.DataFrame(상대), pd.DataFrame(추세)
    나온것 = {}
    for d in 국내날들:
        줄 = 상대표.loc[d].dropna()
        if 줄.empty:
            나온것[d] = frozenset()
            continue
        상위 = 줄.sort_values(ascending=False).index[:k]
        나온것[d] = frozenset(c for c in 상위 if bool(추세표.at[d, c]))
    return pd.Series(나온것, dtype=object)


class USSectorFollowStrategy(PortfolioStrategy):
    def __init__(
        self, N: int = 60, k: int = 2, 지연: int = 1, 보유상한: int = 20,
        name: str = "us_sector_follow",
        가져오기: 시세가져오기 | None = None,
        섹터표: dict[str, str] | None = None,
    ):
        self.name = name
        self.max_holding_days = 보유상한
        self.take_profit_pct = 0.0
        self._N, self._k, self._지연 = N, k, 지연
        self._가져오기 = 가져오기 or 야후에서가져오기
        self._섹터표 = 섹터표 if 섹터표 is not None else 섹터표만들기()
        self._종가: dict[str, pd.Series] = {}
        self._이평20: dict[str, pd.Series] = {}
        self._수익N: dict[str, pd.Series] = {}
        self._강한섹터: pd.Series = pd.Series(dtype=object)
        #: 미국 시세를 못 받았으면 True. 화면과 로그가 "안 산 까닭"을 알 수 있게.
        self.미국시세없음 = False

    @property
    def params(self) -> USSectorParams:
        return USSectorParams(N=self._N, k=self._k, 지연=self._지연,
                              보유상한=int(self.max_holding_days or 0))

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._종가.clear(); self._이평20.clear(); self._수익N.clear()
        for 심볼, df in histories.items():
            s = df.set_index("trade_date")["close"].astype(float)
            s.index = pd.to_datetime(s.index)
            self._종가[심볼] = s
            self._이평20[심볼] = s.rolling(20).mean()
            self._수익N[심볼] = s.pct_change(self._N)

        섹터종목 = [심 for 심 in histories if self._섹터표.get(심) in 섹터짝]
        if not 섹터종목 or not self._종가:
            self._강한섹터 = pd.Series(dtype=object)
            return

        국내날들 = pd.DatetimeIndex(sorted({d for s in self._종가.values() for d in s.index}))
        시작 = 국내날들[0].date() - timedelta(days=예열날수)
        끝 = 국내날들[-1].date()
        try:
            미국 = {심볼: self._가져오기(심볼, 시작, 끝) for 심볼 in [기준지수, *섹터짝.values()]}
            빈것 = [심 for 심, df in 미국.items() if df is None or len(df) == 0]
            if 빈것:
                raise ValueError(f"시세가 비었습니다: {', '.join(빈것)}")
            self._강한섹터 = 강한섹터계산(미국, 국내날들, self._N, self._k, self._지연)
            self.미국시세없음 = False
        except Exception as 탈:  # noqa: BLE001 (못 받으면 안 사는 쪽으로 간다)
            logger.warning(f"{self.name}: 미국 섹터 시세를 못 받아 매수 신호를 내지 않습니다 ({탈})")
            self._강한섹터 = pd.Series(dtype=object)
            self.미국시세없음 = True

    def _강한가(self, 섹터: str, d: pd.Timestamp) -> bool:
        집합 = self._강한섹터.get(d)
        return bool(집합) and 섹터 in 집합

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        d = pd.Timestamp(ctx.as_of)
        신호: list[Signal] = []
        for 심볼 in ctx.histories:
            섹터 = self._섹터표.get(심볼)
            if 섹터 not in 섹터짝:
                continue
            s = self._종가.get(심볼)
            if s is None or d not in s.index:
                continue
            종가, 이평, 수익 = s.at[d], self._이평20[심볼].at[d], self._수익N[심볼].at[d]
            if np.isnan(이평) or np.isnan(수익):
                continue
            강함 = self._강한가(섹터, d)
            if 심볼 in ctx.held:
                if not 강함 or 종가 < 이평:
                    까닭 = "미국 섹터 약해짐" if not 강함 else "20일 이동평균 아래"
                    신호.append(Signal(심볼, ctx.as_of, SignalType.SELL, self.name, reason=까닭))
            elif 강함 and 종가 > 이평 and 수익 > 0:
                신호.append(Signal(
                    심볼, ctx.as_of, SignalType.BUY, self.name, score=float(수익),
                    reason=f"미국 {섹터짝[섹터]} 강함, {self._N}일 {수익:+.1%}",
                ))
        return 신호


class USSectorGateStrategy(PortfolioStrategy):
    """기존 전략의 매수 신호 중 미국이 강하다고 본 섹터의 종목만 통과시킨다.

    설계안 §50이다. 매도는 원래 전략 규칙 그대로다. 미국 신호는 "어느 종목을
    살까"에만 관여한다. 미국 시세를 못 받으면 매수 신호를 전부 막고 경고를
    남긴다. 보유 상한과 익절선은 원래 전략 것을 그대로 쓴다.
    """

    def __init__(
        self, 원래, 원래키: str, N: int = 60, k: int = 2, 지연: int = 0,
        name: str = "us_gate",
        가져오기: 시세가져오기 | None = None,
        섹터표: dict[str, str] | None = None,
    ):
        from muwon.strategy.portfolio import as_portfolio_strategy

        원본 = 원래
        self._원래 = as_portfolio_strategy(원래)
        self._원래키 = 원래키
        self.name = name
        self.max_holding_days = getattr(원본, "max_holding_days", None)
        self.take_profit_pct = float(getattr(원본, "take_profit_pct", 0.0) or 0.0)
        self._N, self._k, self._지연 = N, k, 지연
        self._가져오기 = 가져오기 or 야후에서가져오기
        self._섹터표 = 섹터표 if 섹터표 is not None else 섹터표만들기()
        self._강한섹터: pd.Series = pd.Series(dtype=object)
        self.미국시세없음 = False

    @property
    def params(self) -> USSectorGateParams:
        return USSectorGateParams(원래키=self._원래키, N=self._N, k=self._k, 지연=self._지연)

    @property
    def 원래전략(self):
        return self._원래

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._원래.prepare(histories)
        섹터종목 = [심 for 심 in histories if self._섹터표.get(심) in 섹터짝]
        if not 섹터종목:
            self._강한섹터 = pd.Series(dtype=object)
            return
        국내날들 = pd.DatetimeIndex(sorted({
            pd.Timestamp(d) for df in histories.values() for d in df["trade_date"]
        }))
        시작 = 국내날들[0].date() - timedelta(days=예열날수)
        끝 = 국내날들[-1].date()
        try:
            미국 = {심볼: self._가져오기(심볼, 시작, 끝) for 심볼 in [기준지수, *섹터짝.values()]}
            빈것 = [심 for 심, df in 미국.items() if df is None or len(df) == 0]
            if 빈것:
                raise ValueError(f"시세가 비었습니다: {', '.join(빈것)}")
            self._강한섹터 = 강한섹터계산(미국, 국내날들, self._N, self._k, self._지연)
            self.미국시세없음 = False
        except Exception as 탈:  # noqa: BLE001 (못 받으면 안 사는 쪽으로 간다)
            logger.warning(f"{self.name}: 미국 섹터 시세를 못 받아 매수 신호를 전부 막습니다 ({탈})")
            self._강한섹터 = pd.Series(dtype=object)
            self.미국시세없음 = True

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        신호 = self._원래.evaluate(ctx)
        강한 = self._강한섹터.get(pd.Timestamp(ctx.as_of)) or frozenset()
        return [s for s in 신호
                if s.signal_type != SignalType.BUY or self._섹터표.get(s.symbol) in 강한]
