"""장이 나쁜 날에는 새로 사지 않는다. 이 규칙이 실제로 도움이 되나.

## 어디서 나왔나

인버스 ETF 검증(설계안 §46)에서 인버스는 기각됐는데, 비교 상대로 둔
"나쁜 날에는 새로 안 산다"가 뜻밖에 좋았다. 갭 상승 따라가기의 가장 나빴던
해가 -19.48%에서 -4.11%로 올라갔다. 그때는 그 규칙을 검증한 것이 아니라
비교 상대로 썼을 뿐이라 무작위 대조군과 비교하지 않았다. 여기서 한다.

## 무엇을 비교하나

전략 27개 각각에 대해 셋을 굴린다.

- **그냥.** 원래 전략대로 산다.
- **현금.** 나쁘다고 판단한 날에는 새로 사지 않는다. 들고 있던 것은 원래
  규칙대로 판다.
- **밀어놓기.** 나쁜 날 배열을 통째로 무작위 날수만큼 밀어서, 뭉친 모양은
  그대로 두고 실제 장과의 짝만 끊은 것. 이것을 못 이기면 지표가 준 정보는
  없고 "가끔 안 사는 것" 자체의 효과일 뿐이다.

## 대조군을 흩뿌리지 않고 미는 이유

나쁜 날은 뭉쳐서 온다. 변동성이 20일 평균이라 한 번 높아지면 며칠씩
이어진다. 같은 날수를 흩뿌린 대조군은 "며칠 연속 안 사는 것"과 "하루씩
띄엄띄엄 안 사는 것"을 비교하는 셈이라 공정하지 않다. 배열을 밀면 날수와
뭉침이 정확히 같고 실제 장과의 정렬만 사라진다.

## 채점 기준은 계산하기 전에 정한다

1. **1순위는 가장 나빴던 해다.** 거래 20건 이상인 해만 센다.
2. **현금이 그냥을 가장 나빴던 해와 최대낙폭 둘 다에서 못 이기면 그 설정은
   진 것이다.**
3. **현금이 밀어놓기 대조군의 중앙값을 가장 나빴던 해에서 못 이기면 진 것이다.**
4. **판단 설정 여섯 벌 중 절반 넘게 이겨야 그 전략에서 통과다.**
5. **전략 27개 중 절반 넘게 통과해야 '살펴볼것'이다.** 특정 전략 몇 개에서만
   좋은 것은 그 전략에 맞춘 것이다.

## 미래를 보지 않게 지킨 것

나쁨 판단은 §43의 시장 지표를 그대로 쓴다. D일 저녁에 아는 값만 쓰고
체결은 D+1 시가다.

## 한계

절대 수익률은 못 믿는다. 살아남은 종목만 보고 슬리피지도 0이다. 같은
편향이 세 방식에 똑같이 걸리므로 방식끼리의 비교에만 쓴다. 판단 지표가
둘(변동성, 공포지수)뿐이고 문턱이 셋뿐이다. 다른 지표에서는 다를 수 있다.

사용 예:
    python scripts/run_cash_rule.py --저장 docs/자료/현금규칙_검증.json
    python scripts/run_cash_rule.py --조각 1/2   # 전략을 반으로 갈라 동시에 돌릴 때
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

from run_inverse_hedge import 나쁨만들기, 인버스덧대기, 판단벌, 한번
from run_regime_switch_sim import 시장지표
from run_switch_check import 섹터표만들기
from run_universe_compare import 실거래종목, 전략이름

from muwon.analysis.market_data import load_histories
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, list_definitions

#: 인버스 없이 쓴다. 시세 목록에 없는 이름이라 껍데기가 아무것도 안 산다.
없는종목 = "없음"


def 밀어놓기(나쁨: pd.Series, 씨앗: int) -> pd.Series:
    """나쁜 날 배열을 통째로 민다. 날수와 뭉침은 같고 실제 장과의 짝만 끊긴다.

    미는 폭은 전체의 1/10에서 9/10 사이로 잡는다. 너무 조금 밀면 원래와
    거의 겹쳐서 대조군 구실을 못 한다."""
    n = len(나쁨)
    폭 = n // 10 + (씨앗 * 7919) % max(1, n * 8 // 10)
    값 = 나쁨.to_numpy()
    밀린 = list(값[-폭:]) + list(값[:-폭])
    return pd.Series(밀린, index=나쁨.index, dtype=bool)


def 전략판정(그냥: dict, 벌들: dict) -> dict:
    """채점 기준 2~4."""
    이긴, 진, 대조이긴 = [], [], []
    for 이름, ㅂ in 벌들.items():
        현, 밀 = ㅂ.get("현금"), ㅂ.get("밀어놓기중앙값")
        if not 현:
            continue
        현최악, 그냥최악 = 현["요약"]["최악"], 그냥["요약"]["최악"]
        둘다 = (현최악 is not None and 그냥최악 is not None
              and 현최악 > 그냥최악 and 현["낙폭"] > 그냥["낙폭"])
        (이긴 if 둘다 else 진).append(이름)
        if 밀 and 현최악 is not None and 밀["최악"] is not None and 현최악 > 밀["최악"]:
            대조이긴.append(이름)
    전체 = len(이긴) + len(진)
    통과 = bool(전체 and len(이긴) * 2 > 전체 and len(대조이긴) * 2 > 전체)
    return {"통과": 통과, "그냥을이긴벌": 이긴, "그냥에진벌": 진, "대조군을이긴벌": 대조이긴}


def main() -> int:
    ㅍ = argparse.ArgumentParser(description=__doc__)
    ㅍ.add_argument("--시작", default="2021-01-04")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--전략", default="", help="쉼표로. 비우면 등록된 전략 전부")
    ㅍ.add_argument("--조각", default="1/1", help="전략 목록을 n조각으로 갈라 i번째만. 예 2/3")
    ㅍ.add_argument("--씨앗", default="1,2,3", help="밀어놓기 대조군 반복")
    ㅍ.add_argument("--벌수", type=int, default=len(판단벌))
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
    제약 = {"섹터표": 섹터표만들기(), "섹터상한": 인자.섹터당, "섹터상한셈": "하루후보",
           "점수순": True, "결제일수": 0, "예수금": 인자.예수금}
    씨앗들 = [int(x) for x in 인자.씨앗.split(",") if x]

    전략키들 = ([k.strip() for k in 인자.전략.split(",") if k.strip()]
             or [ㅈ.key for ㅈ in list_definitions()])
    i, n = (int(x) for x in 인자.조각.split("/"))
    전략키들 = 전략키들[i - 1::n]

    source, cache = YahooFinanceDataSource(), PriceCache(".cache/prices.sqlite")
    종목들, 읽은날 = 실거래종목()
    histories = load_histories(source, 종목들, 시작 - timedelta(days=400), 끝, cache=cache)
    지수 = source.get_daily_ohlcv("^KS11", 시작 - timedelta(days=800), 끝)
    공포 = source.get_daily_ohlcv("^VIX", 시작 - timedelta(days=800), 끝)
    지표 = 시장지표(histories, 지수, 공포)
    지표 = 지표[지표.index >= pd.Timestamp(시작)]
    print(f"매매 대상 {len(histories)}종목(시트 사본 {읽은날}) · {시작} ~ {끝} · "
          f"판단 가능한 날 {len(지표)}일 · 전략 {len(전략키들)}개 (조각 {인자.조각})",
          file=sys.stderr)

    낸것: dict = {
        "설명": "장이 나쁘다고 판단한 날 새로 사지 않는 규칙을 그냥 두기와 밀어놓기 대조군에 비교한 것입니다.",
        "잰날": str(datetime.now(UTC).date()),
        "기간": f"{시작} ~ {끝}",
        "매매대상": f"실거래 시트 사본 {읽은날} 기준 {len(histories)}종목",
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · 섹터당 {인자.섹터당}종목 · "
               f"손절 {인자.손절:.0%} · 예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결 · 슬리피지 0"),
        "채점기준": ("1순위는 가장 나빴던 해(거래 20건 이상). 현금이 그냥을 최악 해와 최대낙폭 "
                 "둘 다에서 이기고, 밀어놓기 대조군 중앙값을 최악 해에서 이겨야 그 설정에서 "
                 "이긴 것. 여섯 벌 중 절반 넘게 이겨야 그 전략 통과. 전략 절반 넘게 통과해야 살펴볼것."),
        "전략": {},
    }
    시작때 = time.time()
    for 키 in 전략키들:
        원래 = build_strategy(키)
        그냥 = 한번(histories, 원래, 시작, 끝, 정책, 제약, 없는종목)
        if 그냥 is None:
            print(f"■ {전략이름(키)} 못 돌림", file=sys.stderr)
            continue
        print(f"\n■ {전략이름(키)}  그냥 {그냥['수익률']:+7.1f}%  낙폭 {그냥['낙폭']:+6.1f}%  "
              f"최악 {그냥['요약']['최악']}  {그냥['거래']}건", file=sys.stderr)
        벌들: dict = {}
        for 무엇, 문턱 in 판단벌[:인자.벌수]:
            나쁨 = 나쁨만들기(지표, 무엇, 문턱)
            현금 = 한번(histories, 인버스덧대기(build_strategy(키), 없는종목, 나쁨, False, True),
                     시작, 끝, 정책, 제약, 없는종목)
            밀린것 = [
                r for 씨 in 씨앗들
                if (r := 한번(histories, 인버스덧대기(build_strategy(키), 없는종목,
                                                밀어놓기(나쁨, 씨), False, True),
                            시작, 끝, 정책, 제약, 없는종목))
            ]
            최악들 = [m["요약"]["최악"] for m in 밀린것 if m["요약"]["최악"] is not None]
            벌이름 = f"{무엇} z≥{문턱}"
            벌들[벌이름] = {
                "나쁜날비율": round(float(나쁨.mean()) * 100, 1),
                "현금": 현금,
                "밀어놓기중앙값": {
                    "최악": round(statistics.median(최악들), 2) if 최악들 else None,
                    "낙폭": round(statistics.median(m["낙폭"] for m in 밀린것), 2) if 밀린것 else None,
                    "반복": len(밀린것),
                },
            }
            print(f"  {벌이름:<10s} 나쁜날 {벌들[벌이름]['나쁜날비율']:4.1f}%  "
                  f"현금 최악 {현금['요약']['최악']} 낙폭 {현금['낙폭']:+.1f} {현금['거래']}건  "
                  f"밀어놓기 최악 {벌들[벌이름]['밀어놓기중앙값']['최악']}  "
                  f"({time.time()-시작때:.0f}초)", file=sys.stderr)
        낸것["전략"][키] = {"이름": 전략이름(키), "그냥": 그냥, "벌": 벌들,
                        "판정": 전략판정(그냥, 벌들)}
        print(f"  → {'통과' if 낸것['전략'][키]['판정']['통과'] else '기각'}", file=sys.stderr)
        if 인자.저장:
            Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                     encoding="utf-8")

    통과수 = sum(1 for v in 낸것["전략"].values() if v["판정"]["통과"])
    전체 = len(낸것["전략"])
    낸것["판정"] = {
        "통과전략": [v["이름"] for v in 낸것["전략"].values() if v["판정"]["통과"]],
        "통과수": 통과수, "전체": 전체,
        "결과": "살펴볼것" if 전체 and 통과수 * 2 > 전체 else "기각",
    }
    if 인자.저장:
        Path(인자.저장).write_text(json.dumps(낸것, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
    print(json.dumps(낸것["판정"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
