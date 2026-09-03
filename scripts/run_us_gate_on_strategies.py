"""기존 전략 27개 위에 미국 섹터 신호를 얹으면 나아지나.

## 어디서 나왔나

§48~§49에서 "미국 섹터가 강하면 국내 같은 섹터 종목을 산다"가 대조군을
이겼다. 그때 국내 쪽 규칙은 단순한 추세·모멘텀이었다. 이 저장소에는 이미
종목을 고르는 규칙이 27개 있다. 그 위에 미국 신호를 얹으면 어떤가.

## 어떻게 얹나

기존 전략이 낸 매수 신호 중에서 **미국이 강하다고 본 섹터의 종목만
통과시킨다.** 매도는 기존 전략 규칙 그대로다. 미국 신호는 "어느 종목을
살까"에만 관여한다. 매도까지 바꾸면 무엇이 결과를 바꿨는지 모른다(§49).

미국 신호는 §48과 같다. 섹터 ETF의 60일 상대강도 상위 k개 중 60일 이동평균
위인 섹터. 짝과 매도 기준을 바꿔도 덜 흔들렸던 N60 두 벌(k 2, 3)만 쓴다.
미국 시세는 하루 미룬다.

## 비교 상대

- **그냥.** 원래 전략 그대로.
- **밀어놓기.** 미국 신호 배열을 통째로 민 것. 같은 빈도로 같은 섹터를
  막되 실제와 짝만 끊는다. 이것을 못 이기면 신호가 아니라 "덜 산 것"의
  효과다. §47에서 덜 사는 것 자체가 원래 잃던 전략을 좋아 보이게 했다.

## 채점 기준은 계산하기 전에 정한다

1. **1순위는 가장 나빴던 해다.** 거래 20건 이상인 해만 센다.
2. **미국 신호를 얹은 것이 그냥을 가장 나빴던 해와 최대낙폭 둘 다에서
   이기고, 밀어놓기 중앙값을 가장 나빴던 해에서 이겨야 그 설정에서 이긴 것이다.**
3. **두 벌 다 이겨야 그 전략에서 통과다.**
4. **전략 27개 중 절반 넘게 통과해야 살펴볼것이다.**

## 한계

§48과 같다. 살아남은 종목만 보고 슬리피지 0이다. 섹터가 없는 종목(원자재
ETF)은 신호가 없어 항상 막힌다.

사용 예:
    python scripts/run_us_gate_on_strategies.py --조각 1/3 --저장 결과.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_inverse_hedge import 한번
from run_switch_check import 섹터표만들기
from run_universe_compare import 실거래종목, 전략이름
from run_us_sector_follow import 기준지수, 미국강한섹터, 밀어놓기, 섹터짝

from muwon.analysis.market_data import load_histories
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.domain.types import Signal, SignalType
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
)
from muwon.strategy.registry import build_strategy, list_definitions

설정벌 = [(60, 2), (60, 3)]


class 미국섹터문(PortfolioStrategy):
    """기존 전략의 매수 신호를 미국 섹터 신호로 거른다. 매도는 그대로 통과한다."""

    def __init__(self, 원래, 섹터표: dict[str, str], 강한섹터: pd.Series, 이름: str):
        원본 = 원래
        self._원래 = as_portfolio_strategy(원래)
        self._섹터표 = 섹터표
        self._강한섹터 = 강한섹터
        self.name = 이름
        self.max_holding_days = getattr(원본, "max_holding_days", None)
        self.take_profit_pct = float(getattr(원본, "take_profit_pct", 0.0) or 0.0)

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._원래.prepare(histories)

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        신호 = self._원래.evaluate(ctx)
        오늘 = self._강한섹터.get(pd.Timestamp(ctx.as_of))
        강한 = 오늘["강한"] if 오늘 else frozenset()
        return [s for s in 신호
                if s.signal_type != SignalType.BUY or self._섹터표.get(s.symbol) in 강한]


def 전략판정(그냥: dict, 벌들: dict) -> dict:
    이긴 = []
    for 이름, ㅂ in 벌들.items():
        m, 밀 = ㅂ["미국문"], ㅂ["밀어놓기중앙값"]
        ㅁ, ㄱ = m["요약"]["최악"], 그냥["요약"]["최악"]
        if (ㅁ is not None and ㄱ is not None and ㅁ > ㄱ and m["낙폭"] > 그냥["낙폭"]
                and 밀["최악"] is not None and ㅁ > 밀["최악"]):
            이긴.append(이름)
    return {"통과": len(이긴) == len(벌들) and bool(벌들), "이긴벌": 이긴}


def main() -> int:
    ㅍ = argparse.ArgumentParser(description=__doc__)
    ㅍ.add_argument("--시작", default="2021-01-04")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--전략", default="", help="쉼표로. 비우면 등록된 전략 전부")
    ㅍ.add_argument("--조각", default="1/1")
    ㅍ.add_argument("--씨앗", default="1,2,3")
    ㅍ.add_argument("--미국지연", type=int, default=1)
    ㅍ.add_argument("--설정", default="", help="'N:k'를 쉼표로. 예 20:2,60:3. 비우면 N60 두 벌")
    ㅍ.add_argument("--슬리피지", type=float, default=0.0)
    ㅍ.add_argument("--비중", type=float, default=0.15)
    ㅍ.add_argument("--동시보유", type=int, default=6)
    ㅍ.add_argument("--섹터당", type=int, default=3)
    ㅍ.add_argument("--손절", type=float, default=-0.05)
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0)
    ㅍ.add_argument("--저장", default="")
    인자 = ㅍ.parse_args()

    시작, 끝 = date.fromisoformat(인자.시작), date.fromisoformat(인자.끝)
    정책 = RiskPolicy(max_position_weight=인자.비중, max_concurrent_positions=인자.동시보유,
                    stop_loss_pct=인자.손절, take_profit_pct=0.0, daily_loss_limit_pct=-0.03)
    섹터표 = 섹터표만들기()
    제약 = {"섹터표": 섹터표, "섹터상한": 인자.섹터당, "섹터상한셈": "하루후보",
           "점수순": True, "결제일수": 0, "예수금": 인자.예수금}
    씨앗들 = [int(x) for x in 인자.씨앗.split(",") if x]
    전략키들 = ([k.strip() for k in 인자.전략.split(",") if k.strip()]
             or [ㅈ.key for ㅈ in list_definitions()])
    i, n = (int(x) for x in 인자.조각.split("/"))
    전략키들 = 전략키들[i - 1::n]

    source, cache = YahooFinanceDataSource(), PriceCache(".cache/prices.sqlite")
    종목들, 읽은날 = 실거래종목()
    histories = load_histories(source, 종목들, 시작 - timedelta(days=400), 끝, cache=cache)
    미국 = {심볼: source.get_daily_ohlcv(심볼, 시작 - timedelta(days=400), 끝)
          for 심볼 in [기준지수, *섹터짝.values()]}
    국내날들 = pd.DatetimeIndex(sorted({pd.Timestamp(d) for df in histories.values()
                                       for d in df["trade_date"]}))
    고른설정 = ([tuple(int(x) for x in 항.split(":")) for 항 in 인자.설정.split(",") if 항.strip()]
             or 설정벌)
    신호들 = {f"N{N} k{k}": 미국강한섹터(미국, 국내날들, N, k, 인자.미국지연) for N, k in 고른설정}
    print(f"매매 대상 {len(histories)}종목(시트 사본 {읽은날}) · {시작} ~ {끝} · "
          f"전략 {len(전략키들)}개 (조각 {인자.조각})", file=sys.stderr)

    낸것: dict = {
        "설명": "기존 전략의 매수 신호를 미국 섹터 신호로 거른 것입니다. 매도는 원래 규칙 그대로입니다.",
        "잰날": str(datetime.now(UTC).date()),
        "기간": f"{시작} ~ {끝}", "섹터짝": 섹터짝, "미국지연": 인자.미국지연, "슬리피지": 인자.슬리피지,
        "매매대상": f"실거래 시트 사본 {읽은날} 기준 {len(histories)}종목",
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · 섹터당 {인자.섹터당}종목 · "
               f"손절 {인자.손절:.0%} · 예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결 · 슬리피지 0"),
        "채점기준": ("1순위는 가장 나빴던 해(거래 20건 이상). 미국 신호를 얹은 것이 그냥을 최악 해와 "
                 "낙폭 둘 다에서 이기고 밀어놓기 중앙값을 최악 해에서 이겨야 그 설정에서 이긴 것. "
                 "두 벌 다 이겨야 전략 통과. 27개 중 절반 넘게 통과해야 살펴볼것."),
        "전략": {},
    }
    시작때 = time.time()
    for 키 in 전략키들:
        그냥 = 한번(histories, build_strategy(키), 시작, 끝, 정책, 제약, "없음", 인자.슬리피지)
        if 그냥 is None:
            print(f"■ {전략이름(키)} 못 돌림", file=sys.stderr)
            continue
        print(f"\n■ {전략이름(키)}  그냥 최악 {그냥['요약']['최악']} 낙폭 {그냥['낙폭']:+.1f} "
              f"{그냥['거래']}건", file=sys.stderr)
        벌들: dict = {}
        for 벌이름, 강한 in 신호들.items():
            문 = 한번(histories, 미국섹터문(build_strategy(키), 섹터표, 강한, f"{키}+미국문"),
                    시작, 끝, 정책, 제약, "없음", 인자.슬리피지)
            밀린것 = [r for 씨 in 씨앗들
                   if (r := 한번(histories, 미국섹터문(build_strategy(키), 섹터표,
                                                 밀어놓기(강한, 씨), f"{키}+밀어놓기"),
                               시작, 끝, 정책, 제약, "없음", 인자.슬리피지))]
            최악들 = [m["요약"]["최악"] for m in 밀린것 if m["요약"]["최악"] is not None]
            벌들[벌이름] = {
                "미국문": 문,
                "밀어놓기중앙값": {
                    "최악": round(statistics.median(최악들), 2) if 최악들 else None,
                    "낙폭": round(statistics.median(m["낙폭"] for m in 밀린것), 2) if 밀린것 else None,
                    "반복": len(밀린것)},
            }
            print(f"  {벌이름}  미국문 최악 {문['요약']['최악']} 낙폭 {문['낙폭']:+.1f} {문['거래']}건  "
                  f"밀어놓기 최악 {벌들[벌이름]['밀어놓기중앙값']['최악']}  ({time.time()-시작때:.0f}초)",
                  file=sys.stderr)
        낸것["전략"][키] = {"이름": 전략이름(키), "그냥": 그냥, "벌": 벌들,
                        "판정": 전략판정(그냥, 벌들)}
        print(f"  → {'통과' if 낸것['전략'][키]['판정']['통과'] else '기각'}", file=sys.stderr)
        if 인자.저장:
            Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")

    통과 = [v["이름"] for v in 낸것["전략"].values() if v["판정"]["통과"]]
    낸것["판정"] = {"통과전략": 통과, "통과수": len(통과), "전체": len(낸것["전략"]),
                "결과": "살펴볼것" if len(통과) * 2 > len(낸것["전략"]) else "기각"}
    if 인자.저장:
        Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
    print(json.dumps(낸것["판정"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
