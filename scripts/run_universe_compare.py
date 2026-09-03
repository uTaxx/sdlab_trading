"""매매 대상 목록을 바꿔 가며 전략 27개를 계산하고, 손익이 몇 종목에서
나왔는지 함께 센다.

## 왜 만들었나

2026-09-02에 두 가지를 알았다.

**전략 평가 결과와 실거래가 서로 다른 종목을 본다.** 실거래는 구글 시트
종목 탭의 활성 63종목을 보고(`propose_buys.py`), 전략 평가 결과와 기간
검증은 상태 DB의 시가총액 상위 30종목으로 계산한다. 두 목록이 겹치는 것은
19종목뿐이다.

**목록이 바뀌면 순위가 통째로 뒤집힌다.** 같은 전략 26개를 같은 기간, 같은
설정으로 두 목록에 계산했더니 가장 나빴던 해의 순위 상관이 +0.12였다. 30종목
상위 다섯이 71종목에서 9위, 14위, 15위, 16위, 19위였다.

까닭을 찾다가 손익을 종목별로 세어 봤다. 전략 다섯 중 셋에서 **가장 많이 번
다섯 종목이 전체 손익의 100%를 넘었다.** 100%를 넘는다는 것은 그 다섯을
빼면 나머지 종목 전체가 순손실이라는 뜻이다. 그런 전략은 그 몇 종목이
목록에 있느냐 없느냐로 성적이 정해진다. 전략의 성적이 아니라 종목의 성적이다.

지금 평가표의 여섯 칸(수익률, 가장 나빴던 해, 샤프, 최대낙폭, 손익비, 거래
수) 중 어느 것도 이것을 잡아내지 못한다. 그래서 칸을 더한다.

## 무엇을 세나

전체 손익을 종목별로 나누고 큰 것부터 더해서, 가장 많이 번 1종목과 3종목과
5종목이 전체의 몇 퍼센트인지 적는다. 100%를 넘으면 나머지가 순손실이다.

## 목록 셋

- `실거래`: 구글 시트 종목 탭의 활성 63종목. **실거래가 실제로 보는 것이다.**
- `평가`: 상태 DB의 시가총액 상위 30종목. 지금 전략 평가 결과가 쓰는 것이다.
- `섹터전체`: `sector/catalog.py`의 활성 71종목. 시트에 아직 안 올린 화장품
  8종목이 더 들어 있다.

## 이 계산으로 하지 않는 것

**전략을 바꾸지 않는다.** 실제 주문에 직접 영향을 주는 결정이라 사람이 정한다.

사용 예:
    python scripts/run_universe_compare.py
    python scripts/run_universe_compare.py --목록 실거래 --슬리피지벌 0,0.0005,0.001,0.002
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_switch_check import 대상종목, 섹터표만들기

from muwon.analysis.market_data import load_histories
from muwon.analysis.switching import 굴리기
from muwon.backtest.costs import TransactionCosts
from muwon.data.price_cache import PriceCache
from muwon.data.universe import Ticker
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

#: 한 해에 이만큼 거래가 없으면 그 해 숫자는 판단에 쓰지 않는다.
표본최소기준 = 20

실거래목록파일 = Path(__file__).resolve().parent.parent / "docs/자료/실거래_매매대상.json"


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).짧은이름
    except Exception:  # noqa: BLE001
        return 키


def _티커(코드: str, 이름: str, 시장: str) -> Ticker:
    끝 = "KQ" if 시장 == "KOSDAQ" else "KS"
    return Ticker(symbol=코드, name=이름, market=시장, yahoo_symbol=f"{코드}.{끝}")


def 실거래종목() -> list[Ticker]:
    """구글 시트 종목 탭의 활성 종목. 실거래가 실제로 보는 목록이다.

    시트에 직접 닿을 인증이 이 자리에 없어서 사본 파일을 읽는다. **시트가
    바뀌면 갈린다.** 사본을 언제 읽은 것인지 결과에 적는다."""
    자료 = json.loads(실거래목록파일.read_text(encoding="utf-8"))
    return [_티커(r["종목코드"], r["종목명"], r["시장"]) for r in 자료["활성"]], 자료["읽은날"]


def 평가종목(db경로: str) -> list[Ticker]:
    """상태 DB의 시가총액 상위 스냅샷. 지금 전략 평가 결과가 쓰는 목록이다."""
    import sqlite3

    c = sqlite3.connect(db경로)
    최신 = c.execute(
        "select max(snapshot_at) from universe_snapshots where kind='market_cap'"
    ).fetchone()[0]
    줄 = list(
        c.execute(
            "select symbol,name,market from universe_snapshots "
            "where kind='market_cap' and snapshot_at=? order by rank",
            (최신,),
        )
    )
    return [_티커(r[0], r[1], r[2] or "KOSPI") for r in 줄], (최신 or "")[:10]


def 손익분포(결과) -> dict:
    """손익을 종목별로 나누고 큰 것부터 더한다.

    가장 많이 번 몇 종목이 전체의 몇 퍼센트인지 적는다. 100%를 넘으면 그
    종목들을 뺀 나머지가 순손실이라는 뜻이다."""
    손익: collections.Counter = collections.Counter()
    건수: collections.Counter = collections.Counter()
    for t in 결과.closed_trades:
        손익[t.symbol] += t.pnl_amount
        건수[t.symbol] += 1
    전체 = sum(손익.values())
    큰것 = [v for _, v in 손익.most_common()]
    # **전체 손익이 0이거나 마이너스면 비율을 낼 수 없다.** 음수로 나누면
    # 부호가 뒤집혀서 "가장 많이 번 다섯 종목이 -447%"처럼 뜻 없는 값이 나온다.
    # 애초에 전체가 손실인 전략은 "이익이 어디서 나왔나"를 물을 자리가 아니다.
    if 전체 <= 0:
        return {
            "전체손익": round(전체),
            "거래한종목수": len(손익),
            "비율못냄": "전체 손익이 플러스가 아니라 비율을 낼 수 없습니다",
            "번종목수": sum(1 for v in 큰것 if v > 0),
            "잃은종목수": sum(1 for v in 큰것 if v < 0),
        }

    def 몫(n: int) -> float:
        return round(sum(큰것[:n]) / 전체 * 100, 1)

    위 = 손익.most_common(3)
    아래 = 손익.most_common()[-3:]
    return {
        "전체손익": round(전체),
        "거래한종목수": len(손익),
        "가장많이번1종목비율": 몫(1),
        "가장많이번3종목비율": 몫(3),
        "가장많이번5종목비율": 몫(5),
        "번종목수": sum(1 for v in 큰것 if v > 0),
        "잃은종목수": sum(1 for v in 큰것 if v < 0),
        "가장많이번것": [(s, round(v), 건수[s]) for s, v in 위],
        "가장많이잃은것": [(s, round(v), 건수[s]) for s, v in 아래],
    }


def 해마다(결과) -> dict[int, dict]:
    import pandas as pd

    곡선 = 결과.equity_curve.set_index("trade_date")["equity"].astype(float)
    곡선.index = pd.to_datetime(곡선.index)
    거래: dict[int, int] = {}
    for t in 결과.closed_trades:
        해 = pd.Timestamp(t.exit_date).year
        거래[해] = 거래.get(해, 0) + 1
    나온것: dict[int, dict] = {}
    for 해, 조각 in 곡선.groupby(곡선.index.year):
        if len(조각) < 2:
            continue
        나온것[int(해)] = {
            "수익률": round(float((조각.iloc[-1] / 조각.iloc[0] - 1) * 100), 2),
            "낙폭": round(float(((조각 / 조각.cummax()) - 1).min() * 100), 2),
            "거래": int(거래.get(int(해), 0)),
        }
    return 나온것


def 요약(해별: dict[int, dict]) -> dict:
    쓸것 = {h: v for h, v in 해별.items() if v["거래"] >= 표본최소기준}
    if not 쓸것:
        return {"평균": None, "최악": None, "표본해": 0, "전체해": len(해별)}
    값 = [v["수익률"] for v in 쓸것.values()]
    return {
        "평균": round(float(np.mean(값)), 2),
        "최악": round(float(min(값)), 2),
        "최악해": int(min(쓸것, key=lambda h: 쓸것[h]["수익률"])),
        "표본해": len(쓸것),
        "전체해": len(해별),
    }


def main() -> int:
    ㅍ = argparse.ArgumentParser(description="목록을 바꿔 가며 전략 27개를 계산한다")
    ㅍ.add_argument("--시작", default="2021-01-04")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--목록", default="실거래,평가,섹터전체",
                   help="쉼표로 잇는다. 실거래 · 평가 · 섹터전체")
    ㅍ.add_argument("--슬리피지벌", default="0",
                   help="쉼표로 잇는다. 0.001이면 편도 0.1%%")
    ㅍ.add_argument("--db", default="/tmp/claude-0/-home-user-muwon406/"
                                   "2c0e6bf2-a8da-403c-98d4-14c54ac0bb2d/scratchpad/live.db")
    ㅍ.add_argument("--비중", type=float, default=0.15)
    ㅍ.add_argument("--동시보유", type=int, default=6)
    ㅍ.add_argument("--섹터당", type=int, default=3)
    ㅍ.add_argument("--손절", type=float, default=-0.05)
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0)
    ㅍ.add_argument("--저장", default="")
    인자 = ㅍ.parse_args()

    시작, 끝 = date.fromisoformat(인자.시작), date.fromisoformat(인자.끝)
    정책 = RiskPolicy(
        max_position_weight=인자.비중,
        max_concurrent_positions=인자.동시보유,
        stop_loss_pct=인자.손절,
        take_profit_pct=0.0,
        daily_loss_limit_pct=-0.03,
    )
    제약 = {
        "섹터표": 섹터표만들기(), "섹터상한": 인자.섹터당,
        "섹터상한셈": "하루후보", "점수순": True, "결제일수": 0,
        "예수금": 인자.예수금,
    }
    전략키들 = [ㅈ.key for ㅈ in list_definitions()]
    슬리피지벌 = [float(x) for x in 인자.슬리피지벌.split(",")]

    src, cache = YahooFinanceDataSource(), PriceCache(".cache/prices.sqlite")
    목록만들기 = {
        "실거래": 실거래종목,
        "평가": lambda: 평가종목(인자.db),
        "섹터전체": lambda: (대상종목(), "catalog.py"),
    }

    낸것: dict = {
        "설명": "매매 대상 목록을 바꿔 가며 전략 27개를 계산한 것입니다. "
              "손익이 몇 종목에서 나왔는지를 함께 셉니다.",
        "잰날": str(datetime.now(UTC).date()),
        "기간": f"{시작} ~ {끝}",
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · "
               f"섹터당 {인자.섹터당}종목 · 손절 {인자.손절:.0%} · "
               f"예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결"),
        "읽는법": "가장많이번5종목비율이 100을 넘으면 그 다섯을 뺀 나머지 종목 "
               "전체가 순손실이라는 뜻입니다. 그런 전략은 그 몇 종목이 매매 대상에 "
               "있느냐 없느냐로 성적이 정해집니다.",
        "주의": "매매 대상이 지금 살아 있는 종목이라 과거로 가져가면 살아남은 회사만 "
              "봅니다. 절대 수익률은 부풀려져 있고, 같은 편향이 모든 줄에 걸리므로 "
              "줄끼리의 비교에만 씁니다.",
        "목록": {},
    }

    for 목록이름 in 인자.목록.split(","):
        목록이름 = 목록이름.strip()
        if 목록이름 not in 목록만들기:
            print(f"모르는 목록입니다: {목록이름}", file=sys.stderr)
            continue
        종목들, 출처 = 목록만들기[목록이름]()
        print(f"\n■ [{목록이름}] {len(종목들)}종목 (출처 {출처})", file=sys.stderr)
        histories = load_histories(src, 종목들, 시작 - timedelta(days=400), 끝, cache=cache)
        if not histories:
            print("  시세를 하나도 못 받았습니다.", file=sys.stderr)
            continue

        벌모음: dict[str, dict] = {}
        for 슬 in 슬리피지벌:
            costs = TransactionCosts(slippage_pct=슬)
            줄들: dict[str, dict] = {}
            for i, 키 in enumerate(전략키들, 1):
                t = time.time()
                try:
                    나온것 = 굴리기(histories, build_strategy(키), 시작, 끝, 정책,
                                costs=costs, **제약)
                except Exception as 탈:  # noqa: BLE001
                    print(f"  {키} 못 돌림 ({type(탈).__name__}: {탈})", file=sys.stderr)
                    continue
                if 나온것 is None:
                    continue
                결과, 지표 = 나온것
                해별 = 해마다(결과)
                분포 = 손익분포(결과)
                줄들[키] = {
                    "이름": 전략이름(키),
                    "수익률": round(지표.total_return_pct, 2),
                    "낙폭": round(지표.max_drawdown_pct, 2),
                    "거래": len(결과.closed_trades),
                    "해별": 해별,
                    "요약": 요약(해별),
                    "손익분포": 분포,
                }
                print(f"  [{i:2}/{len(전략키들)}] 슬{슬:.4f} {전략이름(키):18} "
                      f"{지표.total_return_pct:+8.1f}% 최악 {줄들[키]['요약']['최악']} "
                      f"1종목 {분포.get('가장많이번1종목비율')}% "
                      f"5종목 {분포.get('가장많이번5종목비율')}% ({time.time()-t:.0f}초)",
                      file=sys.stderr)
            벌모음[f"슬리피지 {슬:.4f}"] = 줄들
            # **한 벌이 끝날 때마다 남긴다.** 마지막에 한 번만 저장하면 도중에
            # 끊겼을 때 앞의 계산이 통째로 날아간다.
            낸것["목록"][목록이름] = {"종목수": len(histories), "출처": 출처, "벌": 벌모음}
            if 인자.저장:
                Path(인자.저장).write_text(
                    json.dumps(낸것, ensure_ascii=False, indent=1), encoding="utf-8"
                )

    if 인자.저장:
        print(f"\n저장했습니다: {인자.저장}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
