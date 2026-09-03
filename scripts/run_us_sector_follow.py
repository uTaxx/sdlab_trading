"""미국 섹터가 강하면 국내 같은 섹터 종목을 산다. 이 전략이 되나.

## 어디서 나왔나

2026-09-03에 주인이 낸 생각이다. 미국 시장에서 섹터별 강도와 상승 모멘텀을
보고, 강한 섹터의 국내 종목을 사되 그 종목의 추세와 모멘텀도 같이 본다.

지금까지 시험한 시장 지표(변동성, 공포지수)는 "장이 나쁜가"만 말했고 그것으로
매매를 켜고 끄는 규칙은 세 번 다 기각됐다(§43, §46, §47). 이 생각은 다르다.
"어느 섹터를 살까"를 미국에서 하루 먼저 읽는 것이다.

## 전략을 이렇게 구체화했다

**섹터 짝.** 국내 섹터마다 미국 섹터 ETF를 하나 붙인다.

    반도체 SEMI  → SOXX     2차전지 BATT → LIT      바이오 BIO → XBI
    자동차 AUTO  → CARZ     방산 DEF     → ITA      전력 POWER → GRID
    로봇 ROBO    → BOTZ

원자재 ETF(COMM)와 화장품(COSM)은 뺀다. 화장품은 실거래 목록에 종목이 없다.

**미국 쪽 판단.** 섹터 ETF의 N일 수익률에서 S&P 500(SPY)의 N일 수익률을 뺀
것이 상대강도다. 상대강도 상위 k개이면서 ETF 종가가 자기 60일 이동평균 위에
있으면 강한 섹터다. 상대강도만 보면 다 같이 빠지는 장에서 덜 빠진 섹터를
강하다고 하게 되므로 절대 추세 조건을 같이 건다.

**미국 시세는 하루 미룬다.** 한국 저녁에 판단할 때 그날 미국 장은 아직
열리지 않았다. 공포지수를 미룬 것과 같은 까닭이다(§43). 08:30 실행이면
전날 미국 종가를 쓸 수 있으므로 `--미국지연 0`도 같이 잰다.

**국내 쪽 판단.** 강한 섹터에 속한 종목 중 종가가 20일 이동평균 위이고 N일
수익률이 플러스인 것만 산다. 점수는 N일 수익률이라 섹터 안에서 오른 순으로
고른다. 섹터가 강한 섹터에서 빠지거나 종가가 20일 이동평균 아래로 내려오면
판다. 손절과 보유 상한은 엔진이 원래 규칙대로 건다.

## 비교 상대 셋. 각각 다른 것을 가른다

- **국내만.** 미국 신호를 끈다. 모든 섹터를 강한 섹터로 본다. 이것을 못
  이기면 미국 신호가 준 값이 없다.
- **미국만.** 국내 필터를 끈다. 강한 섹터 종목이면 추세와 무관하게 산다.
  미국+국내가 이것을 이기는 폭이 국내 필터의 값이다.
- **밀어놓기.** 미국 신호 배열을 통째로 무작위 날수만큼 민다. 뭉침은 같고
  실제와의 짝만 끊긴다. 이것을 못 이기면 신호가 아니라 "섹터를 돌아가며
  사는 것" 자체의 효과다.

## 채점 기준은 계산하기 전에 정한다

1. **1순위는 가장 나빴던 해다.** 거래 20건 이상인 해만 센다.
2. **미국+국내가 국내만을 가장 나빴던 해와 최대낙폭 둘 다에서 이겨야 그
   설정에서 이긴 것이다.**
3. **밀어놓기 대조군 중앙값도 가장 나빴던 해에서 이겨야 한다.**
4. **설정 네 벌(N 20·60 × k 2·3) 중 절반 넘게 이겨야 살펴볼것이다.**

## 한계

살아남은 종목만 본다. 특히 2차전지는 2020~21 폭등이 과거 지수에 그대로
들어간다(카탈로그 메모). 슬리피지 0이다. 섹터 짝은 내가 고른 것이라 다른
ETF를 붙이면 답이 달라질 수 있다. 미국 ETF 시세가 2019년부터라 2021년
시작이면 예열은 충분하다.

사용 예:
    python scripts/run_us_sector_follow.py --저장 docs/자료/미국섹터_따라가기.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_inverse_hedge import 한번
from run_switch_check import 대상종목, 섹터표만들기
from run_universe_compare import 실거래종목

from muwon.analysis.market_data import load_histories
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.domain.types import Signal, SignalType
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy

#: 국내 섹터 코드 → 미국 섹터 ETF. 내가 고른 짝이다. 다른 ETF면 답이 달라질 수 있어서
#: 두 벌을 두고 둘 다 돌린다.
섹터짝벌 = {
    "기본": {"SEMI": "SOXX", "BATT": "LIT", "BIO": "XBI", "AUTO": "CARZ",
           "DEF": "ITA", "POWER": "GRID", "ROBO": "BOTZ"},
    "대안": {"SEMI": "SMH", "BATT": "BATT", "BIO": "IBB", "AUTO": "DRIV",
           "DEF": "PPA", "POWER": "PAVE", "ROBO": "ROBO"},
}
섹터짝 = 섹터짝벌["기본"]   # main()에서 --짝으로 바꾼다
기준지수 = "SPY"

#: 설정 네 벌. (N일, 상위 k개)
설정벌 = [(20, 2), (20, 3), (60, 2), (60, 3)]


def 미국강한섹터(
    미국: dict[str, pd.DataFrame], 국내날들: pd.DatetimeIndex, N: int, k: int,
    지연: int, 추세창: int = 60,
) -> pd.Series:
    """국내 거래일마다 강한 섹터 코드 집합을 낸다.

    미국 시세를 국내 거래일에 맞춰 앞으로 채우고(휴장일이 다르다), 그다음
    `지연` 거래일만큼 민다. 지연 1이면 국내 D일 판단에 미국 D-1 종가까지 쓴다."""
    def 정렬(심볼):
        s = 미국[심볼].set_index("trade_date")["close"].astype(float)
        s.index = pd.to_datetime(s.index)
        return s.reindex(국내날들, method="ffill").shift(지연)

    기준 = 정렬(기준지수)
    기준수익 = 기준.pct_change(N)
    시장나쁨 = 기준 < 기준.rolling(20).mean()
    상대, 추세, 흐름나쁨 = {}, {}, {}
    for 코드, 심볼 in 섹터짝.items():
        s = 정렬(심볼)
        상대[코드] = s.pct_change(N) - 기준수익
        추세[코드] = s > s.rolling(추세창).mean()
        흐름나쁨[코드] = s < s.rolling(20).mean()   # 매도 판단용. 짧은 흐름이 꺾였나
    상대표 = pd.DataFrame(상대)
    추세표 = pd.DataFrame(추세)
    흐름표 = pd.DataFrame(흐름나쁨)

    나온것 = {}
    for d in 국내날들:
        줄 = 상대표.loc[d].dropna()
        if 줄.empty:
            나온것[d] = {"강한": frozenset(), "약한": frozenset(), "시장나쁨": False}
            continue
        상위 = 줄.sort_values(ascending=False).index[:k]
        나온것[d] = {
            "강한": frozenset(c for c in 상위 if bool(추세표.at[d, c])),
            "약한": frozenset(c for c in 섹터짝 if bool(흐름표.at[d, c])),
            "시장나쁨": bool(시장나쁨.at[d]),
        }
    return pd.Series(나온것)


def 밀어놓기(신호: pd.Series, 씨앗: int) -> pd.Series:
    n = len(신호)
    폭 = n // 10 + (씨앗 * 7919) % max(1, n * 8 // 10)
    값 = list(신호.to_numpy())
    return pd.Series(값[-폭:] + 값[:-폭], index=신호.index)


class 미국섹터따라가기(PortfolioStrategy):
    """강한 미국 섹터의 국내 종목을, 그 종목의 추세와 모멘텀을 보고 산다."""

    def __init__(self, 섹터표: dict[str, str], 강한섹터: pd.Series | None, N: int,
                 국내필터: bool, 보유상한: int, 이름: str, 매도기준: str = "국내"):
        self._섹터표 = 섹터표
        self._강한섹터 = 강한섹터        # None이면 모든 섹터를 강하다고 본다(국내만)
        self._매도기준 = 매도기준        # 국내 / 미국 / 둘다
        self._N = N
        self._국내필터 = 국내필터
        self.name = 이름
        self.max_holding_days = 보유상한
        self.take_profit_pct = 0.0
        self._종가: dict[str, pd.Series] = {}
        self._이평20: dict[str, pd.Series] = {}
        self._수익N: dict[str, pd.Series] = {}

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        for 심볼, df in histories.items():
            s = df.set_index("trade_date")["close"].astype(float)
            s.index = pd.to_datetime(s.index)
            self._종가[심볼] = s
            self._이평20[심볼] = s.rolling(20).mean()
            self._수익N[심볼] = s.pct_change(self._N)

    def _신호(self, d) -> dict | None:
        if self._강한섹터 is None:
            return None
        return self._강한섹터.get(d)

    def _강한가(self, 섹터: str, d) -> bool:
        신호 = self._신호(d)
        if 신호 is None:
            return 섹터 in 섹터짝
        return 섹터 in 신호["강한"]

    def _미국흐름나쁜가(self, 섹터: str, d) -> bool:
        """섹터 ETF가 자기 20일선 아래거나 S&P 500이 20일선 아래면 흐름이 나쁘다."""
        신호 = self._신호(d)
        if 신호 is None:
            return False
        return 섹터 in 신호["약한"] or 신호["시장나쁨"]

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        d = pd.Timestamp(ctx.as_of)
        신호 = []
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
            추세좋음 = (종가 > 이평) and (수익 > 0)
            강함 = self._강한가(섹터, d)
            if 심볼 in ctx.held:
                국내팔기 = (not 강함) or (종가 < 이평)
                미국팔기 = self._미국흐름나쁜가(섹터, d)
                # 국내만(미국 신호 없음)은 매도도 국내 기준이다. 미국 정보를 전혀
                # 안 쓰는 것이 비교 상대의 뜻이라, 미국 매도 기준을 주면 팔 조건이
                # 사라져서 비교가 안 된다.
                기준 = self._매도기준 if self._강한섹터 is not None else "국내"
                팔기 = {"국내": 국내팔기, "미국": 미국팔기,
                      "둘다": 국내팔기 or 미국팔기}[기준]
                if 팔기:
                    까닭 = ("미국 흐름 나쁨" if 미국팔기 and 기준 != "국내"
                          else "미국 섹터 약해짐" if not 강함 else "20일 이동평균 아래")
                    신호.append(Signal(심볼, ctx.as_of, SignalType.SELL, self.name, reason=까닭))
            elif 강함 and (추세좋음 or not self._국내필터):
                신호.append(Signal(심볼, ctx.as_of, SignalType.BUY, self.name,
                                 score=float(수익) if self._국내필터 else 1.0,
                                 reason=f"미국 {섹터짝[섹터]} 강함, {self._N}일 {수익:+.1%}"))
        return 신호


def 벌판정(미국국내: dict, 국내만: dict, 밀린중앙: dict) -> dict:
    ㅁ, ㄱ = 미국국내["요약"]["최악"], 국내만["요약"]["최악"]
    국내이김 = (ㅁ is not None and ㄱ is not None and ㅁ > ㄱ
             and 미국국내["낙폭"] > 국내만["낙폭"])
    밀이김 = ㅁ is not None and 밀린중앙["최악"] is not None and ㅁ > 밀린중앙["최악"]
    return {"국내만을이김": 국내이김, "밀어놓기를이김": 밀이김, "이김": 국내이김 and 밀이김}


def main() -> int:
    ㅍ = argparse.ArgumentParser(description=__doc__)
    ㅍ.add_argument("--시작", default="2021-01-04")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--미국지연", type=int, default=1, help="0이면 그날 미국 종가까지 쓴다(08:30 실행 가정)")
    ㅍ.add_argument("--보유상한", type=int, default=20)
    ㅍ.add_argument("--짝", default="기본", choices=sorted(섹터짝벌), help="섹터 짝 벌")
    ㅍ.add_argument("--매도기준", default="국내", choices=["국내", "미국", "둘다"],
                   help="국내=20일선 아래로 내려오면 / 미국=섹터 ETF나 SPY가 20일선 아래면 / 둘다")
    ㅍ.add_argument("--씨앗", default="1,2,3")
    ㅍ.add_argument("--벌수", type=int, default=len(설정벌))
    ㅍ.add_argument("--벌", default="", help="쉼표로 고른 설정만. 예 'N20 k3,N60 k2'")
    ㅍ.add_argument("--슬리피지", type=float, default=0.0, help="편도. 0.001이면 0.1%%")
    ㅍ.add_argument("--비중", type=float, default=0.15)
    ㅍ.add_argument("--동시보유", type=int, default=6)
    ㅍ.add_argument("--섹터당", type=int, default=3)
    ㅍ.add_argument("--손절", type=float, default=-0.05)
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0)
    ㅍ.add_argument("--목록", default="실거래", choices=["실거래", "섹터전체"],
                   help="실거래=구글 시트 사본 63종목 / 섹터전체=섹터 초안 71종목")
    ㅍ.add_argument("--종목빼기", type=float, default=0.0,
                   help="섹터마다 이 비율만큼 종목을 무작위로 뺀다. 목록에 얼마나 민감한지 본다")
    ㅍ.add_argument("--종목씨앗", type=int, default=0)
    ㅍ.add_argument("--저장", default="")
    인자 = ㅍ.parse_args()

    시작, 끝 = date.fromisoformat(인자.시작), date.fromisoformat(인자.끝)
    global 섹터짝
    섹터짝 = 섹터짝벌[인자.짝]
    정책 = RiskPolicy(max_position_weight=인자.비중, max_concurrent_positions=인자.동시보유,
                    stop_loss_pct=인자.손절, take_profit_pct=0.0, daily_loss_limit_pct=-0.03)
    섹터표 = 섹터표만들기()
    제약 = {"섹터표": 섹터표, "섹터상한": 인자.섹터당, "섹터상한셈": "하루후보",
           "점수순": True, "결제일수": 0, "예수금": 인자.예수금}
    씨앗들 = [int(x) for x in 인자.씨앗.split(",") if x]

    source, cache = YahooFinanceDataSource(), PriceCache(".cache/prices.sqlite")
    if 인자.목록 == "섹터전체":
        종목들, 읽은날 = 대상종목(), "catalog.py"
    else:
        종목들, 읽은날 = 실거래종목()
    histories = load_histories(source, 종목들, 시작 - timedelta(days=400), 끝, cache=cache)
    if 인자.종목빼기 > 0:
        import random
        rng = random.Random(인자.종목씨앗)
        섹터모음: dict[str, list[str]] = {}
        for 심볼 in histories:
            섹터모음.setdefault(섹터표.get(심볼, '?'), []).append(심볼)
        뺄것 = set()
        for 목록 in 섹터모음.values():
            뺄것 |= set(rng.sample(sorted(목록), round(len(목록) * 인자.종목빼기)))
        histories = {k: v for k, v in histories.items() if k not in 뺄것}
        print(f"종목 {len(뺄것)}개를 뺐습니다 (씨앗 {인자.종목씨앗})", file=sys.stderr)
    미국 = {심볼: source.get_daily_ohlcv(심볼, 시작 - timedelta(days=400), 끝)
          for 심볼 in [기준지수, *섹터짝.values()]}
    국내날들 = pd.DatetimeIndex(sorted({pd.Timestamp(d) for df in histories.values()
                                       for d in df["trade_date"]}))
    섹터별 = {}
    for 심볼 in histories:
        섹터별.setdefault(섹터표.get(심볼, "?"), []).append(심볼)
    print(f"매매 대상 {len(histories)}종목(시트 사본 {읽은날}) · {시작} ~ {끝} · "
          f"미국 지연 {인자.미국지연}일 · 짝 {인자.짝} · 매도 {인자.매도기준} · 섹터별 종목 "
          + ", ".join(f"{k} {len(v)}" for k, v in sorted(섹터별.items())), file=sys.stderr)

    낸것: dict = {
        "설명": "미국 섹터 ETF의 상대강도로 강한 섹터를 고르고, 그 섹터의 국내 종목을 추세와 모멘텀을 보고 사는 전략입니다.",
        "잰날": str(datetime.now(UTC).date()),
        "기간": f"{시작} ~ {끝}",
        "섹터짝": 섹터짝, "짝벌": 인자.짝, "매도기준": 인자.매도기준, "기준지수": 기준지수,
        "미국지연": 인자.미국지연, "보유상한": 인자.보유상한, "슬리피지": 인자.슬리피지,
        "매매대상": f"{인자.목록} ({읽은날}) {len(histories)}종목 (원자재 ETF 제외)",
        "종목빼기": 인자.종목빼기, "종목씨앗": 인자.종목씨앗,
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · 섹터당 {인자.섹터당}종목 · "
               f"손절 {인자.손절:.0%} · 예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결 · 슬리피지 {인자.슬리피지:.2%}"),
        "채점기준": ("1순위는 가장 나빴던 해(거래 20건 이상). 미국+국내가 국내만을 최악 해와 "
                 "최대낙폭 둘 다에서 이기고 밀어놓기 대조군 중앙값을 최악 해에서 이겨야 그 "
                 "설정에서 이긴 것. 네 벌 중 절반 넘게 이겨야 살펴볼것."),
        "벌": {},
    }
    시작때 = time.time()
    고른벌 = {x.strip() for x in 인자.벌.split(",") if x.strip()}
    for N, k in 설정벌[:인자.벌수]:
        if 고른벌 and f"N{N} k{k}" not in 고른벌:
            continue
        강한 = 미국강한섹터(미국, 국내날들, N, k, 인자.미국지연)
        강한날비율 = float(np.mean([len(v["강한"]) > 0 for v in 강한.values])) * 100
        만들기 = lambda 신호, 필터, 이름, N=N: 미국섹터따라가기(
            섹터표, 신호, N, 필터, 인자.보유상한, 이름, 인자.매도기준)
        ㅂ = {"강한섹터있는날비율": round(강한날비율, 1)}
        ㅂ["미국+국내"] = 한번(histories, 만들기(강한, True, f"미국+국내 N{N} k{k}"),
                          시작, 끝, 정책, 제약, "없음", 인자.슬리피지)
        ㅂ["국내만"] = 한번(histories, 만들기(None, True, f"국내만 N{N}"),
                        시작, 끝, 정책, 제약, "없음", 인자.슬리피지)
        ㅂ["미국만"] = 한번(histories, 만들기(강한, False, f"미국만 N{N} k{k}"),
                        시작, 끝, 정책, 제약, "없음", 인자.슬리피지)
        밀린것 = [r for 씨 in 씨앗들
               if (r := 한번(histories, 만들기(밀어놓기(강한, 씨), True, f"밀어놓기 {씨}"),
                           시작, 끝, 정책, 제약, "없음", 인자.슬리피지))]
        최악들 = [m["요약"]["최악"] for m in 밀린것 if m["요약"]["최악"] is not None]
        ㅂ["밀어놓기중앙값"] = {
            "최악": round(statistics.median(최악들), 2) if 최악들 else None,
            "낙폭": round(statistics.median(m["낙폭"] for m in 밀린것), 2) if 밀린것 else None,
            "반복": len(밀린것),
        }
        ㅂ["판정"] = 벌판정(ㅂ["미국+국내"], ㅂ["국내만"], ㅂ["밀어놓기중앙값"])
        벌이름 = f"N{N} k{k}"
        낸것["벌"][벌이름] = ㅂ
        def 줄(r):
            return (f"수익 {r['수익률']:+7.1f}% 최악 {r['요약']['최악']!s:>7s} "
                    f"낙폭 {r['낙폭']:+6.1f} {r['거래']:>4d}건")
        print(f"\n■ {벌이름} (강한 섹터가 있는 날 {강한날비율:.0f}%)", file=sys.stderr)
        for 이름 in ("미국+국내", "국내만", "미국만"):
            print(f"  {이름:<8s} {줄(ㅂ[이름])}", file=sys.stderr)
        print(f"  밀어놓기  최악 {ㅂ['밀어놓기중앙값']['최악']} 낙폭 {ㅂ['밀어놓기중앙값']['낙폭']}  "
              f"→ {'이김' if ㅂ['판정']['이김'] else '짐'}  ({time.time()-시작때:.0f}초)", file=sys.stderr)
        if 인자.저장:
            Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")

    이긴수 = sum(1 for b in 낸것["벌"].values() if b["판정"]["이김"])
    전체 = len(낸것["벌"])
    낸것["판정"] = {"이긴벌": 이긴수, "전체": 전체,
                "결과": "살펴볼것" if 전체 and 이긴수 * 2 > 전체 else "기각"}
    if 인자.저장:
        Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
    print(json.dumps(낸것["판정"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
