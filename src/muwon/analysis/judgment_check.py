"""판단 지침을 전략과 같은 방식으로 검증한다 (2026-09-07).

## 왜 만들었나

전략은 과거 시세로 돌려 보고 고른다. 그런데 **"어느 전략이 좋은지를 무엇으로
판단할 것인가"는 지금까지 검증한 적이 없다.** 가장 나빴던 구간을 먼저 보고,
비슷하면 최대낙폭, 그다음 누적 수익률로 정한다고 적어 두었을 뿐이다.

주인이 2026-09-07에 지적했다. 지침도 매매 전략과 같은 구조로 분석해야 하고,
보유기간이 5영업일인데 한 해 단위로 재는 것은 안 맞는다는 것이다. 맞다.

## 어떻게 검증하나

지침이 좋다는 것은 **그 지침으로 고른 전략이 그다음 구간에서 실제로
좋았다**는 뜻이다. 그러니 이렇게 잰다.

    판정 시점 T마다
      1. T 이전 구간들만 보고 지침을 적용해 1위 전략을 고른다
      2. T 다음 한 구간의 그 전략 성적을 적는다
    이것을 여러 T에 대해 반복해 분포를 낸다

전략을 워크포워드로 검증하는 것과 똑같은 구조다. 지침을 정할 때 미래를
쓰지 않는 것이 핵심이다.

## 구간 단위로 잰다

한 해 단위가 아니라 보유 상한과 같은 길이의 구간으로 자른다. 이 시스템은
한 종목을 정해진 영업일수보다 오래 들고 있지 않으므로, 계좌를 그 길이로
잘라 본 분포가 실제 운용에 가깝다. `analysis/window_perf.py`와 같은 생각이다.

**구간은 이어서 굴린 자산 곡선에서 잘라낸다.** 구간마다 따로 돌리면 구간
시작 때 언제나 현금 100%인데, 실제로는 앞 구간에서 들고 온 종목이 있다.

## 대조군을 반드시 같이 낸다

"지침으로 고른 전략의 다음 구간 중앙값 +2%"는 그 자체로는 아무 뜻이 없다.
아무 전략이나 골라도 +2%였다면 지침은 아무것도 더하지 않은 것이다. 그래서
셋을 같이 낸다.

    무작위      매 판정마다 아무 전략이나 고른 것
    전체 평균   그 구간에 모든 전략이 낸 성적의 평균
    안 바꾸기   전략 하나를 처음부터 끝까지 그냥 둔 것

이 저장소가 미국 섹터 규칙을 검증할 때 쓴 방식과 같다(설계안 §48).

## 이것으로 아무것도 자동으로 바꾸지 않는다

지침을 바꾸면 저녁 검토가 내는 후보가 바뀌고, 그 후보가 텔레그램 버튼으로
나간다. 실제 주문에 영향을 주는 결정이라 사람이 정한다.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from loguru import logger

from muwon.analysis.window_perf import 구간, 구간나누기
from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.backtest.metrics import compute_metrics
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy

#: 판정할 때 몇 구간을 돌아볼 것인가. 20영업일 구간 열둘이면 약 1년이다.
돌아볼구간 = 12

#: 무작위 대조군을 몇 번 굴릴 것인가.
무작위횟수 = 200

#: 돌아본 구간에서 이만큼도 안 산 전략은 후보에서 뺀다.
#:
#: **이걸 안 걸면 아무것도 안 하는 전략이 언제나 1등이다.** 처음 계산했을
#: 때 265번 중 253번을 5년 거래 1건짜리 전략이 가져갔다. 안 사면 수익률이
#: 0이고, 0은 어느 마이너스보다 크기 때문이다. 지킨 것이 아니라 아무것도
#: 안 한 것인데 지침은 그것을 구별하지 못한다.
#:
#: 12구간(약 3개월)에 6건이면 한 구간에 반 건이다. 낮게 잡은 것은 거래가
#: 드문 전략을 통째로 빼려는 것이 아니라 '한 번도 안 사는 것'만 막으려는
#: 것이기 때문이다.
최소매매 = 6


@dataclass(frozen=True)
class 지침후보:
    """전략을 줄 세우는 방법 하나.

    `값내기`는 구간 목록을 받아 점수를 낸다. 언제나 **클수록 좋은** 값을
    돌려준다. 낙폭처럼 작을수록 좋은 것은 부호를 뒤집어 둔다. 그래야
    비교하는 쪽이 방향을 안 헷갈린다."""

    키: str
    이름: str
    값내기: Callable[[list[잰구간]], float]
    설명: str = ""


def _수익률들(구간들: list[잰구간]) -> list[float]:
    return [ㄱ.구간.수익률 for ㄱ in 구간들]


def _매매합(구간들: list[잰구간]) -> int:
    return sum(ㄱ.매수수 for ㄱ in 구간들)


def _백분위(값들: list[float], 비율: float) -> float:
    ㅈ = sorted(값들)
    if not ㅈ:
        return 0.0
    자리 = (len(ㅈ) - 1) * 비율
    아래 = int(자리)
    위 = min(아래 + 1, len(ㅈ) - 1)
    return ㅈ[아래] + (ㅈ[위] - ㅈ[아래]) * (자리 - 아래)


def _기하평균(값들: list[float]) -> float:
    곱 = 1.0
    for ㄱ in 값들:
        곱 *= 1 + ㄱ / 100
        if 곱 <= 0:
            return -100.0
    return (곱 ** (1 / len(값들)) - 1) * 100 if 값들 else 0.0


def _하락대비(값들: list[float]) -> float:
    """기하평균을 마이너스 쪽 흔들림으로 나눈다. 위험 대비 수익이다."""
    if not 값들:
        return 0.0
    아래 = [ㄱ for ㄱ in 값들 if ㄱ < 0]
    if len(아래) < 2:
        return _기하평균(값들)
    흔들림 = statistics.pstdev(아래)
    return _기하평균(값들) / 흔들림 if 흔들림 > 0 else _기하평균(값들)


#: 견줄 지침들. 전부 클수록 좋은 값이다.
지침들: list[지침후보] = [
    지침후보("worst", "가장 나빴던 구간",
          lambda ㄱ: min(_수익률들(ㄱ)),
          "지금 1순위와 같은 생각을 구간 단위로 옮긴 것"),
    지침후보("p10", "하위 10% 구간",
          lambda ㄱ: _백분위(_수익률들(ㄱ), 0.10),
          "가장 나빴던 한 구간에만 휘둘리지 않는다"),
    지침후보("median", "구간 수익률 중앙값",
          lambda ㄱ: statistics.median(_수익률들(ㄱ))),
    지침후보("geo", "기하평균",
          lambda ㄱ: _기하평균(_수익률들(ㄱ)),
          "복리로 쌓이는 속도"),
    지침후보("plus_ratio", "플러스 구간 비율",
          lambda ㄱ: sum(1 for v in _수익률들(ㄱ) if v > 0) / len(ㄱ) * 100),
    지침후보("mdd", "구간 최대낙폭 중앙값",
          lambda ㄱ: statistics.median([v.구간.최대낙폭 for v in ㄱ]),
          "작을수록 좋으므로 음수 그대로 크기를 견준다"),
    지침후보("downside", "하락 대비 수익",
          lambda ㄱ: _하락대비(_수익률들(ㄱ))),
]


@dataclass(frozen=True)
class 잰구간:
    """구간 하나와 그 구간에 실제로 매수한 건수.

    **매매 수를 같이 들고 다녀야 한다.** 안 그러면 아무것도 안 산 전략이
    수익률 0%로 "가장 나빴던 구간 0%"가 되어 늘 1등이 된다. 실제로 그랬다.
    첫 계산에서 265번 중 253번을 5년 거래 1건짜리 전략이 가져갔다."""

    구간: 구간
    매수수: int


@dataclass(frozen=True)
class 판정하나:
    """한 판정 시점에서 무엇을 골랐고 그다음이 어땠나."""

    판정일: date
    고른키: str
    다음수익률: float


@dataclass(frozen=True)
class 지침성적:
    """한 지침이 워크포워드에서 낸 성적."""

    키: str
    이름: str
    판정수: int
    중앙값: float
    최악: float
    플러스비율: float
    바꾼횟수: int
    #: 후보가 하나도 없어 판정을 못 한 횟수. 억지로 하나 고르지 않는다.
    못한판정: int = 0
    판정들: list[판정하나] = field(default_factory=list)

    @property
    def 바꾼비율(self) -> float:
        return self.바꾼횟수 / self.판정수 * 100 if self.판정수 else 0.0


@dataclass(frozen=True)
class 대조군:
    이름: str
    판정수: int
    중앙값: float
    최악: float
    플러스비율: float


def 전략곡선들(
    histories: dict[str, pd.DataFrame],
    전략들: dict,
    정책: RiskPolicy,
    시작: date,
    costs: TransactionCosts | None = None,
    섹터표: dict[str, str] | None = None,
    섹터상한: int = 0,
    예수금: float = 5_000_000.0,
) -> tuple[dict[str, pd.DataFrame], dict[str, int], dict[str, list[date]]]:
    """전략마다 전 기간을 한 번씩 굴려 자산 곡선을 얻는다.

    **한 번만 굴린다.** 판정 시점마다 다시 굴리면 같은 계산을 수십 번 한다.
    곡선 하나를 얻어 두면 구간은 잘라 쓰기만 하면 된다."""
    곡선들: dict[str, pd.DataFrame] = {}
    거래수: dict[str, int] = {}
    진입일들: dict[str, list[date]] = {}
    for 키, 만들기 in 전략들.items():
        try:
            결과 = BacktestEngine(
                strategy=만들기(),
                risk_manager=RiskManager(policy_provider=lambda p=정책: p),
                costs=costs,
                entry_at_open=True,
                exit_at_open=True,
                섹터표=섹터표,
                섹터상한=섹터상한,
                initial_cash=예수금,
            ).run(histories, trade_from=시작)
        except Exception as 탈:  # noqa: BLE001 (하나 때문에 전부 멈추면 안 된다)
            # 조용히 넘기지 않는다. 전략 하나가 빠지면 지침 순위가 달라지는데,
            # 무엇이 빠졌는지 안 적어 두면 그 사실을 알 길이 없다.
            logger.warning(f"{키} 전략을 못 굴렸습니다: {탈}")
            continue
        ㅈ = compute_metrics(결과)
        # 거래가 한 건도 없던 전략은 뺀다. 수익률 0%로 위에 오지만 지킨
        # 것이 아니라 아무것도 안 한 것이다.
        if ㅈ.num_trades <= 0:
            continue
        곡선들[키] = 결과.equity_curve
        거래수[키] = int(ㅈ.num_trades)
        진입일들[키] = sorted(ㅁ.entry_date for ㅁ in 결과.closed_trades)
    return 곡선들, 거래수, 진입일들


def 구간표만들기(
    곡선들: dict[str, pd.DataFrame],
    길이: int,
    진입일들: dict[str, list[date]] | None = None,
) -> dict[str, list[잰구간]]:
    """전략마다 겹치지 않는 구간 목록. 겹치면 표본 수가 부풀려진다.

    구간마다 그 안에서 몇 건을 샀는지도 같이 센다."""
    진입일들 = 진입일들 or {}
    나온것: dict[str, list[잰구간]] = {}
    for 키, 곡선 in 곡선들.items():
        구간들 = 구간나누기(곡선, 길이, 겹치게=False)
        if not 구간들:
            continue
        날들 = 진입일들.get(키, [])
        잰것 = []
        for ㄱ in 구간들:
            샀나 = sum(1 for ㅇ in 날들 if ㄱ.시작일 <= ㅇ <= ㄱ.끝일)
            잰것.append(잰구간(구간=ㄱ, 매수수=샀나))
        나온것[키] = 잰것
    return 나온것


def _가장짧은길이(구간표: dict[str, list[잰구간]]) -> int:
    return min(len(v) for v in 구간표.values()) if 구간표 else 0


def 워크포워드(
    구간표: dict[str, list[잰구간]],
    지침: 지침후보,
    돌아볼: int = 돌아볼구간,
) -> 지침성적 | None:
    """판정 시점마다 지침으로 1위를 고르고 그다음 구간 성적을 적는다.

    **판정에 미래를 쓰지 않는다.** i번째 구간을 고를 때는 i 이전 구간만
    본다. 이걸 어기면 지침이 실제보다 훨씬 좋아 보인다.

    **돌아본 구간에서 거의 안 산 전략은 후보에서 뺀다.** 안 사면 수익률이
    0이고 0은 어느 마이너스보다 크다. 그래서 아무것도 안 하는 전략이 늘
    1등이 된다."""
    칸수 = _가장짧은길이(구간표)
    if 칸수 <= 돌아볼:
        return None
    키들 = sorted(구간표)
    판정들: list[판정하나] = []
    앞서고른것 = ""
    바꾼횟수 = 0
    걸러진판정 = 0
    for i in range(돌아볼, 칸수):
        점수 = {}
        for ㅋ in 키들:
            앞구간 = 구간표[ㅋ][i - 돌아볼: i]
            if not 앞구간 or _매매합(앞구간) < 최소매매:
                continue
            점수[ㅋ] = 지침.값내기(앞구간)
        if not 점수:
            # 후보가 하나도 없는 날은 판정을 안 한 것으로 둔다. 억지로
            # 하나 고르면 그 숫자가 지침의 성적으로 섞인다.
            걸러진판정 += 1
            continue
        고른키 = max(점수, key=lambda ㅋ: (점수[ㅋ], ㅋ))
        if 앞서고른것 and 고른키 != 앞서고른것:
            바꾼횟수 += 1
        앞서고른것 = 고른키
        그구간 = 구간표[고른키][i].구간
        판정들.append(판정하나(그구간.시작일, 고른키, 그구간.수익률))

    if not 판정들:
        return None
    값들 = [ㄱ.다음수익률 for ㄱ in 판정들]
    return 지침성적(
        키=지침.키, 이름=지침.이름, 판정수=len(판정들),
        중앙값=statistics.median(값들), 최악=min(값들),
        플러스비율=sum(1 for ㄱ in 값들 if ㄱ > 0) / len(값들) * 100,
        바꾼횟수=바꾼횟수, 못한판정=걸러진판정, 판정들=판정들,
    )


def 무작위대조군(
    구간표: dict[str, list[잰구간]],
    돌아볼: int = 돌아볼구간,
    횟수: int = 무작위횟수,
    씨앗: int = 20260907,
) -> 대조군 | None:
    """매 판정마다 아무 전략이나 고른 것. **이걸 못 이기면 지침이 아니다.**"""
    칸수 = _가장짧은길이(구간표)
    if 칸수 <= 돌아볼:
        return None
    키들 = sorted(구간표)
    무작위 = random.Random(씨앗)
    중앙값들, 최악들, 플러스들 = [], [], []
    for _ in range(횟수):
        # 지침과 같은 기준으로 후보를 거른다. 대조군만 아무 전략이나 고르면
        # 지침이 쉬운 상대와 견주는 셈이 된다.
        값들 = []
        for i in range(돌아볼, 칸수):
            쓸것 = [ㅋ for ㅋ in 키들
                  if _매매합(구간표[ㅋ][i - 돌아볼: i]) >= 최소매매]
            if not 쓸것:
                continue
            값들.append(구간표[무작위.choice(쓸것)][i].구간.수익률)
        if not 값들:
            continue
        중앙값들.append(statistics.median(값들))
        최악들.append(min(값들))
        플러스들.append(sum(1 for ㄱ in 값들 if ㄱ > 0) / len(값들) * 100)
    return 대조군(
        이름=f"무작위로 고름 ({횟수}번의 가운데값)",
        판정수=칸수 - 돌아볼,
        중앙값=statistics.median(중앙값들),
        최악=statistics.median(최악들),
        플러스비율=statistics.median(플러스들),
    )


def 전체평균대조군(
    구간표: dict[str, list[잰구간]], 돌아볼: int = 돌아볼구간
) -> 대조군 | None:
    """그 구간에 모든 전략이 낸 성적의 평균. 시장이 어땠는지에 가깝다."""
    칸수 = _가장짧은길이(구간표)
    if 칸수 <= 돌아볼:
        return None
    키들 = sorted(구간표)
    값들 = [statistics.mean([구간표[ㅋ][i].구간.수익률 for ㅋ in 키들])
          for i in range(돌아볼, 칸수)]
    return 대조군(
        이름="모든 전략의 평균", 판정수=len(값들),
        중앙값=statistics.median(값들), 최악=min(값들),
        플러스비율=sum(1 for ㄱ in 값들 if ㄱ > 0) / len(값들) * 100,
    )


def 안바꾼대조군(
    구간표: dict[str, list[잰구간]], 키: str, 돌아볼: int = 돌아볼구간
) -> 대조군 | None:
    """전략 하나를 처음부터 끝까지 그냥 둔 것."""
    if 키 not in 구간표:
        return None
    칸수 = _가장짧은길이(구간표)
    if 칸수 <= 돌아볼:
        return None
    값들 = [구간표[키][i].구간.수익률 for i in range(돌아볼, 칸수)]
    return 대조군(
        이름=f"안 바꾸고 {키} 그대로", 판정수=len(값들),
        중앙값=statistics.median(값들), 최악=min(값들),
        플러스비율=sum(1 for ㄱ in 값들 if ㄱ > 0) / len(값들) * 100,
    )


def 모두재기(
    구간표: dict[str, list[잰구간]], 돌아볼: int = 돌아볼구간
) -> list[지침성적]:
    """지침 전부를 같은 자료로 견준다.

    순서는 **가장 나빴던 판정**을 먼저 본다. 지침을 고르는 자리에서도
    평균보다 최악을 먼저 보는 것이 이 저장소의 기준이다."""
    나온것 = [ㅅ for ㅈ in 지침들 if (ㅅ := 워크포워드(구간표, ㅈ, 돌아볼))]
    나온것.sort(key=lambda ㅅ: (-ㅅ.최악, -ㅅ.중앙값))
    return 나온것


# ── 지침을 언제 바꿀 것인가 ──────────────────────────────────
#
# 주인이 2026-09-07에 정했다. 주 단위로 전략을 바꿀 때 지침도 같이 보되,
# **바꾸는 기준을 명확히 하고 전략과 같은 기간 기준으로 분석한다.**
#
# 기준이 없으면 매주 1위가 바뀌는 대로 따라가게 되고, 그것은 지침을 최근에
# 맞추는 것이다. 전략을 최근에 맞추는 것을 이 저장소가 여러 번 기각했는데
# 지침에서 같은 일을 하면 앞뒤가 안 맞는다.

#: 판정이 이보다 적으면 바꾸지 않는다. 표본이 적으면 1위가 우연이다.
최소판정 = 30

#: 최악이 이 안에서 갈리면 같은 것으로 본다(%p). 전략 순위의 동점 범위와
#: 같은 값이다. 좁게 두면 0.1%p 차이로 지침이 매주 바뀐다.
동점범위 = 1.0


@dataclass(frozen=True)
class 변경판단:
    """지침을 바꿀 것인가, 그리고 왜 그렇게 판단했나."""

    바꿀까: bool
    지금지침: str
    고른지침: str
    까닭: str
    구간길이: int
    판정수: int
    성적들: list[지침성적] = field(default_factory=list)
    대조들: list[대조군] = field(default_factory=list)

    def 한줄(self) -> str:
        앞 = "바꿉니다" if self.바꿀까 else "바꾸지 않습니다"
        return f"{앞}. {self.까닭}"


def 지침변경판단(
    성적들: list[지침성적],
    대조들: list[대조군],
    지금지침: str,
    구간길이: int,
    최소판정수: int = 최소판정,
    범위: float = 동점범위,
) -> 변경판단:
    """지침을 바꿀지 정한다. **막는 쪽으로 기울인다.**

    순서대로 본다. 하나라도 걸리면 안 바꾼다.

      1. 판정이 최소 기준에 못 미치면 안 바꾼다
      2. 1위가 지금 지침이면 안 바꾼다
      3. 1위와 지금 지침의 최악 차이가 동점 범위 안이면 안 바꾼다
      4. **1위가 대조군을 못 이기면 안 바꾼다**

    넷째가 핵심이다. 2026-09-07 계산에서 어느 지침도 무작위를 중앙값으로
    못 이겼고, 안 바꾸고 그냥 둔 것이 최악 기준으로 제일 나았다. 그런 자료
    위에서 1위를 골라 바꾸는 것은 근거 없이 바꾸는 것이다."""
    if not 성적들:
        return 변경판단(
            바꿀까=False, 지금지침=지금지침, 고른지침=지금지침,
            까닭="지침을 하나도 계산하지 못했습니다.",
            구간길이=구간길이, 판정수=0, 성적들=성적들, 대조들=대조들)

    판정수 = 성적들[0].판정수
    if 판정수 < 최소판정수:
        return 변경판단(
            바꿀까=False, 지금지침=지금지침, 고른지침=지금지침,
            까닭=(f"판정이 {판정수}번뿐입니다. 최소 기준 {최소판정수}번에 "
                "못 미쳐 바꾸지 않습니다."),
            구간길이=구간길이, 판정수=판정수, 성적들=성적들, 대조들=대조들)

    으뜸 = 성적들[0]
    지금것 = next((ㅅ for ㅅ in 성적들 if ㅅ.키 == 지금지침), None)

    if 으뜸.키 == 지금지침:
        return 변경판단(
            바꿀까=False, 지금지침=지금지침, 고른지침=지금지침,
            까닭=f"지금 지침({으뜸.이름})이 그대로 1위입니다.",
            구간길이=구간길이, 판정수=판정수, 성적들=성적들, 대조들=대조들)

    if 지금것 is not None and 으뜸.최악 - 지금것.최악 <= 범위:
        return 변경판단(
            바꿀까=False, 지금지침=지금지침, 고른지침=지금지침,
            까닭=(f"1위({으뜸.이름} {으뜸.최악:+.1f}%)와 지금 지침"
                f"({지금것.이름} {지금것.최악:+.1f}%)의 차이가 "
                f"{범위:.1f}%p 안입니다. 같은 것으로 봅니다."),
            구간길이=구간길이, 판정수=판정수, 성적들=성적들, 대조들=대조들)

    # 대조군을 못 이기면 바꿀 근거가 없다. 최악과 중앙값 둘 다 본다.
    못이긴것 = [ㄷ.이름 for ㄷ in 대조들
             if 으뜸.최악 <= ㄷ.최악 and 으뜸.중앙값 <= ㄷ.중앙값]
    if 못이긴것:
        return 변경판단(
            바꿀까=False, 지금지침=지금지침, 고른지침=지금지침,
            까닭=(f"1위({으뜸.이름})가 대조군을 못 이깁니다: "
                f"{', '.join(못이긴것)}. 바꿀 근거가 없습니다."),
            구간길이=구간길이, 판정수=판정수, 성적들=성적들, 대조들=대조들)

    return 변경판단(
        바꿀까=True, 지금지침=지금지침, 고른지침=으뜸.키,
        까닭=(f"{으뜸.이름}이 최악 {으뜸.최악:+.1f}%로 지금 지침보다 "
            f"{으뜸.최악 - (지금것.최악 if 지금것 else 으뜸.최악):+.1f}%p 낫고 "
            "대조군도 이깁니다."),
        구간길이=구간길이, 판정수=판정수, 성적들=성적들, 대조들=대조들)
