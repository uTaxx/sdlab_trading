"""장이 나쁠 때 인버스 ETF를 사면 현금으로 있는 것보다 나은가.

## 무엇을 묻나

2026-09-02까지의 계산으로 알게 된 것은 이것이다. 지금 시스템은 장이 나쁠 때
막을 방법이 없다. 손절선을 넓혀도, 종목 수를 줄여도, 전략을 갈아타도
안 됐다(설계안 §41, §43). 전부 "국내 주식을 사는" 틀 안에서 값만 바꾼 것이다.

인버스 ETF는 그 틀 밖의 것이다. 장이 내리면 오른다. 그래서 "장이 나쁘다고
판단한 날에는 인버스 ETF를 산다"는 규칙을 시험한다.

## 비교 상대는 현금이다

"장이 나쁘면 인버스를 산다"는 결국 장을 미리 맞히는 규칙이다. 이 저장소가
그런 규칙을 세 번 기각했다. 그래서 인버스가 좋아 보여도 그것이 인버스 덕인지
"장이 나쁠 때 덜 샀기" 때문인지 갈라야 한다.

같은 판단(나쁜 날)을 놓고 세 가지를 비교한다.

- **그냥.** 판단을 무시하고 원래 전략대로 산다.
- **현금.** 나쁜 날에는 새로 사지 않는다. 들고 있던 것은 원래 규칙대로 판다.
- **인버스.** 나쁜 날에는 인버스 ETF를 산다. 좋아지면 판다. 원래 전략은
  그대로 산다.
- **인버스+현금.** 둘 다.

인버스가 현금보다 못하면 인버스는 넣을 이유가 없다. 판단이 맞았을 때의
이득은 현금으로도 얻는데, 판단이 틀렸을 때의 손실은 인버스가 더 크다.
2배 인버스는 장이 오르락내리락만 해도 값이 조금씩 녹는다.

## 채점 기준은 계산하기 전에 정한다

1. **1순위는 가장 나빴던 해다.** 거래 20건 이상인 해만 센다.
2. **인버스가 현금을 가장 나빴던 해와 최대낙폭 둘 다에서 못 이기면 기각한다.**
3. **무작위 대조군을 못 이기면 기각한다.** 같은 날수만큼 무작위로 고른 날에
   인버스를 산 것이다. 이것을 못 이기면 지표가 준 정보가 없다는 뜻이다.
4. **설정값 여러 벌에서 답이 갈리면 기각한다.** 절반 넘게 이겨야 한다.

## 미래를 보지 않게 지킨 것

나쁨 판단은 `run_regime_switch_sim.시장지표`를 그대로 쓴다. D일 종가까지의
코스피 변동성, 하루 미룬 공포지수(VIX), 그 시점까지의 과거로만 표준화한
z점수다. 판단은 D일 저녁에 하고 체결은 D+1 시가다.

## 인버스 ETF도 원래 규칙을 다 받는다

한 종목 15% 비중, 손절 -5%, 보유 상한, 동시 보유 상한이 전부 걸린다. 장이
오르는 날 인버스는 손절에 걸려 팔리고, 보유 상한이 되면 팔렸다가 다음 날
다시 산다. 그 왕복 비용이 곧 이 규칙의 실제 비용이라 일부러 빼지 않는다.

## 한계

절대 수익률은 못 믿는다. 매매 대상이 지금 살아 있는 종목이라 살아남은
회사만 본다. 슬리피지는 0이다. 인버스 ETF는 호가가 촘촘해서 주식보다는
덜 하지만 0은 아니다. 같은 편향이 네 방식에 똑같이 걸리므로 방식끼리의
비교에만 쓴다.

사용 예:
    python scripts/run_inverse_hedge.py --기본전략 volume_surge_3d,gap_up_go
    python scripts/run_inverse_hedge.py --인버스 252670 --저장 결과.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import statistics
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_regime_switch_sim import 시장지표
from run_switch_check import 섹터표만들기
from run_universe_compare import 손익분포, 실거래종목, 요약, 전략이름, 해마다

from muwon.analysis.market_data import load_histories
from muwon.analysis.switching import 굴리기
from muwon.backtest.costs import TransactionCosts
from muwon.data.price_cache import PriceCache
from muwon.data.universe import Ticker
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.domain.types import Signal, SignalType
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
)
from muwon.strategy.registry import build_strategy

#: 국내 상장 인버스 ETF. 시세 시작일은 야후에서 실제로 받아 본 값이다.
인버스표 = {
    "114800": Ticker("114800", "KODEX 인버스", "KOSPI", "114800.KS"),
    "252670": Ticker("252670", "KODEX 200선물인버스2X", "KOSPI", "252670.KS"),
}

#: 나쁨 판단 설정. (지표, z점수 문턱). 여섯 벌에서 답이 모이는지 본다.
판단벌 = [
    ("변동성", 0.5), ("변동성", 1.0), ("변동성", 1.5),
    ("공포", 0.5), ("공포", 1.0), ("공포", 1.5),
]


class 인버스덧대기(PortfolioStrategy):
    """원래 전략 위에 "나쁜 날" 규칙을 얹는다.

    원래 전략은 인버스 ETF를 모른다. 시세 목록에서 빼고 넘기고, 혹시 그
    종목에 낸 신호가 있으면 버린다. 거래량 급증 같은 규칙이 인버스 ETF에
    걸리면 "장이 나쁘다"와 무관한 매수가 섞인다.
    """

    def __init__(self, 원래, 인버스: str, 나쁨: pd.Series,
                 인버스켬: bool, 현금켬: bool):
        # 등록된 전략은 PortfolioStrategy가 아니라 어댑터가 필요하다. 익절선은
        # 어댑터가 안 옮기므로 감싸기 전의 원본에서 읽는다.
        원본 = 원래
        self._원래 = as_portfolio_strategy(원래)
        self._인버스 = 인버스
        self._나쁨 = 나쁨
        self._인버스켬 = 인버스켬
        self._현금켬 = 현금켬
        꼬리 = {(True, True): "+인버스+현금", (True, False): "+인버스",
              (False, True): "+현금", (False, False): ""}[(인버스켬, 현금켬)]
        self.name = f"{원본.name}{꼬리}"
        # 보유 상한과 익절선을 원래 전략에서 그대로 가져온다. 엔진이 이 객체에서
        # 읽기 때문에 안 옮기면 원래 전략의 청산 규칙이 사라진다.
        self.max_holding_days = getattr(원본, "max_holding_days", None)
        self.take_profit_pct = float(getattr(원본, "take_profit_pct", 0.0) or 0.0)

    def _나쁜날인가(self, 날: date) -> bool:
        값 = self._나쁨.get(pd.Timestamp(날))
        return bool(값) if 값 is not None and not pd.isna(값) else False

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        self._원래.prepare({k: v for k, v in histories.items() if k != self._인버스})

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        주식만 = {k: v for k, v in ctx.histories.items() if k != self._인버스}
        ctx2 = dataclasses.replace(ctx, histories=주식만,
                                   held=frozenset(ctx.held - {self._인버스}))
        신호 = [s for s in self._원래.evaluate(ctx2) if s.symbol != self._인버스]
        나쁨 = self._나쁜날인가(ctx.as_of)

        if self._현금켬 and 나쁨:
            신호 = [s for s in 신호 if s.signal_type != SignalType.BUY]

        if self._인버스켬 and self._인버스 in ctx.histories:
            들고있음 = self._인버스 in ctx.held
            if 나쁨 and not 들고있음:
                # 점수를 크게 줘서 자리가 모자랄 때 먼저 산다.
                신호.append(Signal(self._인버스, ctx.as_of, SignalType.BUY, self.name,
                                 score=1e9, reason="시장 지표 나쁨. 인버스 매수"))
            elif not 나쁨 and 들고있음:
                신호.append(Signal(self._인버스, ctx.as_of, SignalType.SELL, self.name,
                                 reason="시장 지표 회복. 인버스 매도"))
        return 신호


def 나쁨만들기(지표: pd.DataFrame, 무엇: str, 문턱: float) -> pd.Series:
    return (지표[무엇] >= 문턱).astype(bool)


def 무작위나쁨(본보기: pd.Series, 씨앗: int) -> pd.Series:
    """같은 날수를 무작위 날짜에 놓는다. 지표가 준 정보가 있는지 가르는 대조군."""
    rng = random.Random(씨앗)
    날들 = list(본보기.index)
    고른것 = set(rng.sample(날들, int(본보기.sum())))
    return pd.Series([d in 고른것 for d in 날들], index=본보기.index, dtype=bool)


def 한번(histories, 전략, 시작, 끝, 정책, 제약, 인버스: str,
       슬리피지: float = 0.0) -> dict | None:
    나온것 = 굴리기(histories, 전략, 시작, 끝, 정책,
                costs=TransactionCosts(slippage_pct=슬리피지), **제약)
    if 나온것 is None:
        return None
    결과, 지표 = 나온것
    해별 = 해마다(결과)
    인버스거래 = [t for t in 결과.closed_trades if t.symbol == 인버스]
    return {
        "이름": 전략.name,
        "수익률": round(지표.total_return_pct, 2),
        "낙폭": round(지표.max_drawdown_pct, 2),
        "거래": len(결과.closed_trades),
        "인버스거래": len(인버스거래),
        "인버스손익": round(float(sum(t.pnl_amount for t in 인버스거래))),
        "해별": 해별,
        "요약": 요약(해별),
        "손익분포": 손익분포(결과),
    }


def 판정(벌들: dict) -> dict:
    """채점 기준 2~4를 그대로 적용한다. 계산이 끝난 뒤 기준을 옮기지 않는다."""
    이긴벌, 진벌, 무작위이긴벌 = [], [], []
    for 이름, ㅂ in 벌들.items():
        인, 현, 무 = ㅂ.get("인버스"), ㅂ.get("현금"), ㅂ.get("무작위중앙값")
        if not 인 or not 현:
            continue
        인최악, 현최악 = 인["요약"]["최악"], 현["요약"]["최악"]
        둘다 = (인최악 is not None and 현최악 is not None
              and 인최악 > 현최악 and 인["낙폭"] > 현["낙폭"])
        (이긴벌 if 둘다 else 진벌).append(이름)
        if 무 and 인최악 is not None and 무["최악"] is not None and 인최악 > 무["최악"]:
            무작위이긴벌.append(이름)
    전체 = len(이긴벌) + len(진벌)
    결과 = "기각"
    if 전체 and len(이긴벌) * 2 > 전체 and len(무작위이긴벌) * 2 > 전체:
        결과 = "살펴볼것"
    return {
        "결과": 결과,
        "현금을이긴벌": 이긴벌, "현금에진벌": 진벌,
        "무작위를이긴벌": 무작위이긴벌,
        "읽는법": ("살펴볼것은 채택이 아닙니다. 현금과 무작위 대조군을 절반 넘게 이겼다는 "
                "뜻이고, 그다음은 다른 기본 전략과 다른 인버스 ETF에서도 같은지 보는 "
                "것입니다."),
    }


def main() -> int:
    ㅍ = argparse.ArgumentParser(description=__doc__)
    ㅍ.add_argument("--시작", default="2021-01-04")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--기본전략", default="volume_surge_3d,gap_up_go",
                   help="쉼표로 잇는다. 이 전략 위에 인버스 규칙을 얹는다")
    ㅍ.add_argument("--인버스", default="114800", choices=sorted(인버스표),
                   help="114800=KODEX 인버스(1배) / 252670=KODEX 200선물인버스2X")
    ㅍ.add_argument("--무작위씨앗", default="1,2,3", help="대조군 반복 수만큼 쉼표로")
    ㅍ.add_argument("--비중", type=float, default=0.15)
    ㅍ.add_argument("--동시보유", type=int, default=6)
    ㅍ.add_argument("--섹터당", type=int, default=3)
    ㅍ.add_argument("--손절", type=float, default=-0.05)
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0)
    ㅍ.add_argument("--벌수", type=int, default=len(판단벌),
                   help="판단 설정을 앞에서 몇 벌만 쓸지. 짧게 확인할 때 쓴다")
    ㅍ.add_argument("--저장", default="")
    인자 = ㅍ.parse_args()

    시작, 끝 = date.fromisoformat(인자.시작), date.fromisoformat(인자.끝)
    인버스 = 인자.인버스
    정책 = RiskPolicy(max_position_weight=인자.비중, max_concurrent_positions=인자.동시보유,
                    stop_loss_pct=인자.손절, take_profit_pct=0.0, daily_loss_limit_pct=-0.03)
    제약 = {"섹터표": 섹터표만들기(), "섹터상한": 인자.섹터당, "섹터상한셈": "하루후보",
           "점수순": True, "결제일수": 0, "예수금": 인자.예수금}
    씨앗들 = [int(x) for x in 인자.무작위씨앗.split(",") if x]

    source, cache = YahooFinanceDataSource(), PriceCache(".cache/prices.sqlite")
    종목들, 읽은날 = 실거래종목()
    histories = load_histories(source, 종목들 + [인버스표[인버스]],
                               시작 - timedelta(days=400), 끝, cache=cache)
    if 인버스 not in histories:
        raise SystemExit(f"인버스 ETF {인버스} 시세를 못 받았습니다. 계산을 멈춥니다.")
    주식시세 = {k: v for k, v in histories.items() if k != 인버스}
    지수 = source.get_daily_ohlcv("^KS11", 시작 - timedelta(days=800), 끝)
    공포 = source.get_daily_ohlcv("^VIX", 시작 - timedelta(days=800), 끝)
    지표 = 시장지표(주식시세, 지수, 공포)
    지표 = 지표[지표.index >= pd.Timestamp(시작)]
    print(f"매매 대상 {len(주식시세)}종목(시트 사본 {읽은날}) + {인버스표[인버스].name} · "
          f"{시작} ~ {끝} · 판단 가능한 날 {len(지표)}일", file=sys.stderr)

    낸것: dict = {
        "설명": "장이 나쁘다고 판단한 날 인버스 ETF를 사는 규칙을 현금으로 있는 것과 비교한 것입니다.",
        "잰날": str(datetime.now(UTC).date()),
        "기간": f"{시작} ~ {끝}",
        "인버스": 인버스표[인버스].name,
        "매매대상": f"실거래 시트 사본 {읽은날} 기준 {len(주식시세)}종목",
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · 섹터당 {인자.섹터당}종목 · "
               f"손절 {인자.손절:.0%} · 예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결 · 슬리피지 0"),
        "채점기준": ("1순위는 가장 나빴던 해(거래 20건 이상). 인버스가 현금을 최악 해와 "
                 "최대낙폭 둘 다에서 못 이기면 기각. 무작위 대조군을 못 이기면 기각. "
                 "여섯 벌 중 절반 넘게 이겨야 함."),
        "기본전략": {},
    }
    시작때 = time.time()
    for 키 in [k.strip() for k in 인자.기본전략.split(",") if k.strip()]:
        print(f"\n■ 기본 전략 {전략이름(키)}", file=sys.stderr)
        원래 = build_strategy(키)
        그냥 = 한번(주식시세, 원래, 시작, 끝, 정책, 제약, 인버스)
        if 그냥 is None:
            print("  못 돌림", file=sys.stderr)
            continue
        print(f"  그냥          {그냥['수익률']:+8.1f}%  낙폭 {그냥['낙폭']:+7.2f}%  "
              f"최악 {그냥['요약']['최악']}  {그냥['거래']}건  ({time.time()-시작때:.0f}초)",
              file=sys.stderr)
        벌들: dict = {}
        for 무엇, 문턱 in 판단벌[:인자.벌수]:
            나쁨 = 나쁨만들기(지표, 무엇, 문턱)
            벌이름 = f"{무엇} z≥{문턱}"
            ㅂ: dict = {"나쁜날비율": round(float(나쁨.mean()) * 100, 1)}
            for 방식, 인켬, 현켬 in (("현금", False, True), ("인버스", True, False),
                                  ("인버스+현금", True, True)):
                전략 = 인버스덧대기(build_strategy(키), 인버스, 나쁨, 인켬, 현켬)
                ㅂ[방식] = 한번(histories, 전략, 시작, 끝, 정책, 제약, 인버스)
            무작위들 = []
            for 씨 in 씨앗들:
                전략 = 인버스덧대기(build_strategy(키), 인버스, 무작위나쁨(나쁨, 씨), True, False)
                ㄱ = 한번(histories, 전략, 시작, 끝, 정책, 제약, 인버스)
                if ㄱ:
                    무작위들.append(ㄱ)
            최악들 = [m["요약"]["최악"] for m in 무작위들 if m["요약"]["최악"] is not None]
            ㅂ["무작위중앙값"] = {
                "최악": round(statistics.median(최악들), 2) if 최악들 else None,
                "낙폭": round(statistics.median(m["낙폭"] for m in 무작위들), 2) if 무작위들 else None,
                "반복": len(무작위들),
            }
            벌들[벌이름] = ㅂ
            인, 현 = ㅂ["인버스"], ㅂ["현금"]
            print(f"  {벌이름:<10s} 나쁜날 {ㅂ['나쁜날비율']:4.1f}%  "
                  f"현금 최악 {현['요약']['최악']} 낙폭 {현['낙폭']:+.1f}  "
                  f"인버스 최악 {인['요약']['최악']} 낙폭 {인['낙폭']:+.1f} "
                  f"인버스거래 {인['인버스거래']}건 손익 {인['인버스손익']:+,}원  "
                  f"무작위 최악 {ㅂ['무작위중앙값']['최악']}  ({time.time()-시작때:.0f}초)",
                  file=sys.stderr)
        낸것["기본전략"][키] = {"이름": 전략이름(키), "그냥": 그냥, "벌": 벌들, "판정": 판정(벌들)}
        print(f"  판정: {낸것['기본전략'][키]['판정']['결과']}", file=sys.stderr)
        # 기본 전략 하나가 끝날 때마다 남긴다. 중간에 죽어도 앞의 것은 건진다.
        if 인자.저장:
            Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")

    print(json.dumps({k: v["판정"] for k, v in 낸것["기본전략"].items()},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
