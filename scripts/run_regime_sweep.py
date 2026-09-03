"""시장 변동성 구간마다 전략 27개를 여러 설정으로 돌려 본다.

## 무엇에 답하려는 것인가

2026-09-02에 "거래가 많은 전략이 격변장에서 특히 나빴다"를 찾았다. 일곱
구간을 재니 조용한 장에서는 회전 많고 적음의 차이가 7.9%p인데 격변장에서는
20.2%p였고, 격변장에서 플러스인 것은 2개월 거래 10건 이하 칸뿐이었다.

그런데 그것만으로는 **원인을 모른다.** 거래 수는 전략의 성질이지 우리가
돌린 손잡이가 아니다. "회전을 줄이면 좋아진다"인지 "회전이 많은 전략들이
그냥 나쁜 것"인지가 갈리지 않는다. 앞의 것이면 설정으로 고칠 수 있고 뒤의
것이면 전략을 바꿔야 한다.

그래서 **같은 전략에 손잡이를 돌려 본다.** 동시보유 수를 줄이면 회전이
강제로 줄어든다. 같은 전략이 그때 좋아지면 회전이 원인이다.

손절도 같이 본다. 하루에 5% 움직이는 장에서 -5% 손절은 반나절 잡음이라
진짜 손실이 아닌 것에 걸린다는 의심이 있었다.

## 구간을 어떻게 골랐나

코스피 20일 실현변동성으로 갈랐다. 격변 구간은 하루 3% 넘게 움직인 날이
있는 곳이고, 조용한 구간은 없는 곳이다. 조용한 쪽 셋은 국면(상승·조정·하락)
검증에 쓰던 것과 같은 구간이라 앞의 숫자와 이어 볼 수 있다.

**구간 이름과 실제 계산 범위를 반드시 같이 찍는다.** 임시 스크립트에 날짜를
글자로 박아 뒀다가 실제와 이틀 어긋난 채로 보고한 적이 있다(2026-09-02).

## 동시보유를 그냥 줄이면 두 가지가 같이 바뀐다 (2026-09-02에 알게 됨)

첫 훑기는 한 종목 비중을 15%로 고정해 두고 동시보유만 1에서 6으로 바꿨다.
그러면 1종목 칸은 자금의 15%만 쓰고 6종목 칸은 90%까지 쓴다. **회전이 아니라
투입 금액이 여섯 배 차이 난다.** 실제로 나온 숫자가 대체로 여섯 배 비례라
"회전을 줄여서 좋아진 것"인지 "돈을 덜 넣어서 덜 잃은 것"인지 갈리지 않는다.

`--노출고정`을 주면 한 종목 비중을 `1/동시보유`로 잡아 어느 칸이든 자금을
다 쓴다. 그때 남는 차이가 집중과 회전의 효과다.

## 주의

**절대 수익률을 그대로 믿으면 안 된다.** 매매 대상이 지금 살아 있는 종목
목록이라 과거로 가져가면 살아남은 회사만 본다. 같은 편향이 모든 칸에 똑같이
걸리므로 칸끼리의 비교에만 쓴다.

사용 예:
    python scripts/run_regime_sweep.py --무엇 동시보유
    python scripts/run_regime_sweep.py --무엇 손절 --구간 격변
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_switch_check import 구간고르기, 대상종목, 섹터표만들기, 전략이름

from muwon.analysis.market_data import load_histories
from muwon.analysis.period_check import 구간, 돌려보기
from muwon.backtest.costs import TransactionCosts
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, list_definitions

#: (이름, 끝날, 되돌아볼 날수, 성격). 날수는 달력 날수다.
구간표 = [
    ("2020 코로나", date(2020, 3, 31), 60, "격변"),
    ("2026 2~3월", date(2026, 3, 31), 58, "격변"),
    ("2026 4~6월", date(2026, 6, 30), 61, "격변"),
    ("2026 7~8월", date(2026, 8, 31), 60, "격변"),
    ("2024 상승", date(2024, 3, 29), 60, "조용"),
    ("2024 조정", date(2024, 5, 31), 60, "조용"),
    ("2023 하락", date(2023, 10, 31), 61, "조용"),
]

#: 지표 예열. 60일선과 ATR이 나오려면 이만큼은 앞이 있어야 한다.
예열날수 = 900


def 한칸(histories, 정의, 끝, 동시보유: int, 손절: float, 섹터당: int = 3,
        노출고정: bool = False):
    """한 설정으로 전략 27개를 돌린다. (전략키, 수익률, 거래수, 최대낙폭) 목록.

    `노출고정`을 켜면 한 종목에 넣는 비중을 `1/동시보유`로 잡는다. 그러면 어느
    칸이든 자금을 100% 가까이 쓰게 되고, 칸끼리의 차이가 '얼마를 넣었나'가
    아니라 '몇 개로 나눴나'만 남는다. 끄면 한 종목당 15%로 고정이라
    1종목 칸은 자금의 15%만, 6종목 칸은 90%까지 쓴다.
    """
    비중 = (1.0 / 동시보유) if 노출고정 else 0.15
    정책 = RiskPolicy(
        max_position_weight=비중,
        max_concurrent_positions=동시보유,
        stop_loss_pct=손절,
        daily_loss_limit_pct=-0.03,
    )
    제약 = {"섹터표": 섹터표만들기(), "섹터상한": 섹터당, "섹터상한셈": "하루후보",
           "점수순": True, "예수금": 10_000_000.0}
    나온것 = []
    for ㅈ in list_definitions():
        try:
            성적 = 돌려보기(정의, (lambda k=ㅈ.key: build_strategy(k)), histories, 끝,
                        정책, costs=TransactionCosts(slippage_pct=0.0), **제약)
        except Exception as 탈:  # noqa: BLE001 (전략 하나 때문에 훑기가 멈추면 안 된다)
            print(f"    건너뜀 {ㅈ.key} ({type(탈).__name__})", file=sys.stderr)
            continue
        if 성적 is None:
            continue
        나온것.append((ㅈ.key, 성적.수익률, 성적.metrics.num_trades,
                    성적.metrics.max_drawdown_pct))
    return 나온것


def 요약(것들, 이름: str) -> str:
    """거래 0건은 뺀다. 아무것도 안 한 것을 0%로 세면 지킨 것처럼 보인다."""
    쓸것 = [ㄱ for ㄱ in 것들 if ㄱ[2] > 0]
    if not 쓸것:
        return f"  {이름:<22s} 거래가 있는 전략이 없습니다"
    ㅅ = [ㄱ[1] for ㄱ in 쓸것]
    return (f"  {이름:<22s} {len(쓸것):>2d}개  평균 {statistics.mean(ㅅ):+7.2f}%  "
            f"중앙값 {statistics.median(ㅅ):+7.2f}%  "
            f"플러스 {sum(1 for x in ㅅ if x > 0):>2d}개  "
            f"거래 중앙값 {statistics.median(ㄱ[2] for ㄱ in 쓸것):>3.0f}건  "
            f"평균 낙폭 {statistics.mean(ㄱ[3] for ㄱ in 쓸것):+7.2f}%")


def main() -> int:
    ㅍ = argparse.ArgumentParser(description=__doc__)
    ㅍ.add_argument("--무엇", default="동시보유", choices=["동시보유", "손절"],
                   help="무슨 손잡이를 돌릴 것인가")
    ㅍ.add_argument("--노출고정", action="store_true",
                   help="한 종목 비중을 1/동시보유로 잡아 어느 칸이든 자금을 다 쓰게 한다")
    ㅍ.add_argument("--구간", default="전부", choices=["전부", "격변", "조용"])
    ㅍ.add_argument("--이름", default="",
                   help="쉼표로 구간 이름을 골라 돌린다. 중간에 죽었을 때 남은 것만 이어서 돌린다")
    ㅍ.add_argument("--값들", default="",
                   help="쉼표로. 동시보유면 `1,2,3,4,6`, 손절이면 `-0.05,-0.08,-0.12`")
    ㅍ.add_argument("--저장", default="", help="결과를 남길 JSON 경로")
    인자 = ㅍ.parse_args()

    기본값 = {"동시보유": "1,2,3,4,6", "손절": "-0.05,-0.08,-0.12,-0.20"}
    값들 = [ㄱ for ㄱ in (인자.값들 or 기본값[인자.무엇]).split(",") if ㄱ]
    값들 = [int(ㄱ) if 인자.무엇 == "동시보유" else float(ㄱ) for ㄱ in 값들]

    고를것 = [ㄱ for ㄱ in 구간표 if 인자.구간 in ("전부", ㄱ[3])]
    if 인자.이름:
        찾는것 = [ㄱ.strip() for ㄱ in 인자.이름.split(",") if ㄱ.strip()]
        모르는것 = [ㄱ for ㄱ in 찾는것 if ㄱ not in [ㄴ[0] for ㄴ in 구간표]]
        if 모르는것:
            # 조용히 0개를 돌리면 "빨리 끝났네"로 읽힌다.
            raise SystemExit(f"그런 구간이 없습니다: {', '.join(모르는것)}")
        고를것 = [ㄱ for ㄱ in 고를것 if ㄱ[0] in 찾는것]
    print(f"■ 손잡이: {인자.무엇} {값들}"
          f"{' · 노출 고정' if 인자.노출고정 else ' · 한 종목 15% 고정'}", file=sys.stderr)
    print(f"■ 구간 {len(고를것)}개 · 전략 {len(list_definitions())}개 "
          f"→ 계산 {len(고를것) * len(값들) * len(list_definitions())}회", file=sys.stderr)

    시작때 = time.time()
    모은것: list[dict] = []
    for 이름, 끝, 날수, 성격 in 고를것:
        정의 = 구간고르기(f"{날수}일")
        시작, _ = 구간(정의, 끝)
        histories = load_histories(
            YahooFinanceDataSource(), 대상종목(), 시작 - timedelta(days=예열날수),
            끝, PriceCache(),
        )
        # 이름과 실제 범위를 같이 적는다. 하나만 적으면 나중에 어긋나도 모른다.
        print(f"\n{'=' * 78}")
        print(f"{이름} ({성격}) · {시작} ~ {끝} · 대상종목 {len(histories)}개")
        print(f"{'=' * 78}")
        for 값 in 값들:
            동시보유 = 값 if 인자.무엇 == "동시보유" else 6
            손절 = 값 if 인자.무엇 == "손절" else -0.05
            것들 = 한칸(histories, 정의, 끝, 동시보유, 손절, 노출고정=인자.노출고정)
            칸이름 = (f"동시보유 {동시보유}종목" if 인자.무엇 == "동시보유"
                    else f"손절 {손절 * 100:.0f}%")
            if 인자.노출고정:
                칸이름 += f" (한 종목 {100 / 동시보유:.0f}%)"
            print(요약(것들, 칸이름))
            for 키, 수익, 거래, 낙폭 in 것들:
                모은것.append({"구간": 이름, "성격": 성격, "시작": str(시작), "끝": str(끝),
                            "손잡이": 인자.무엇, "값": 값, "노출고정": 인자.노출고정,
                            "전략": 키,
                            "이름": 전략이름(키), "수익률": 수익, "거래수": 거래,
                            "최대낙폭": 낙폭})
            print(f"    ({time.time() - 시작때:.0f}초)", file=sys.stderr)
            sys.stdout.flush()  # 파일로 넘길 때 버퍼에 갇히면 진행이 안 보인다
        남기기(인자.저장, 모은것)   # 구간마다 남긴다. 중간에 죽어도 앞의 것은 건진다

    남기기(인자.저장, 모은것)
    return 0


def 남기기(경로: str, 모은것: list[dict]) -> None:
    """구간이 끝날 때마다 부른다.

    30분씩 도는 훑기가 중간에 죽은 적이 있다(2026-09-02). 끝에서 한 번만
    남기면 그때까지 계산한 것이 통째로 사라진다.
    """
    if not 경로:
        return
    Path(경로).write_text(
        json.dumps(모은것, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  ({len(모은것)}줄을 {경로}에 남겼습니다)", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
