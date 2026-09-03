"""**두 단계로 고른다. 먼저 섹터, 그다음 종목.**

## 왜 두 단계인가

지금까지는 45종목을 한 줄로 세워 놓고 신호가 난 것을 샀다. 그러면 어떤
날은 반도체 다섯 종목이 한꺼번에 잡힌다. **분산한 줄 알았는데 사실상
반도체 한 종목에 다섯 배로 건 것**이다. 같은 이유로 오르고 같은 이유로
빠지는 종목들이기 때문이다.

섹터를 먼저 고르면 그 일이 안 생긴다. 그리고 "무엇을 왜 샀나"가 두 문장이
된다. "요즘 반도체가 시장을 이기고 있어서(1차), 그중 거래량이 터진
HPSP를 샀다(2차)".

## 1차: 무엇으로 섹터를 고르나

**시장 대비 강도**다. 최근 N일 동안 그 섹터 지수가 코스피보다 몇 %p 더
갔나. 절대 수익률이 아니라 **시장과의 차이**를 쓰는 이유는, 우리 섹터
지수가 오늘의 종목 목록으로 과거를 만든 것이라 절대값이 부풀려져 있기
때문이다(살아남은 종목만 들어 있다). 시장도 같이 오른 날은 강도가 0에
가까워져서, 그 부풀림이 상당 부분 상쇄된다.

**이 기준이 실제로 도움이 되는지는 따로 쟀다**.
`scripts/verify_sector_rotation.py`와 `docs/섹터선정_검증.md`를 볼 것.
숫자를 보기 전에는 "그럴듯하다"에 불과하다.

## 2차: 종목은 기존 전략이 고른다

새 규칙을 만들지 않는다. 1차에서 살아남은 섹터의 종목만 기존 매수 신호에
태운다. **바뀐 것은 대상이지 판단 기준이 아니다**. 그래야 성적이 달라졌을
때 무엇 때문인지 안다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

#: 며칠을 돌아보고 강도를 잴 것인가.
LOOKBACK = 20
#: 몇 개 섹터까지 고를 것인가.
TOP_N = 3
#: 이만큼(%p)은 시장을 이겨야 고른다. 0이면 "시장보다 낫기만 하면".
MIN_STRENGTH = 0.0


@dataclass(frozen=True)
class 섹터점수:
    코드: str
    이름: str
    상대강도: float | None  # %p. None이면 잴 수 없었다
    뽑힘: bool = False
    사유: str = ""

    def 한줄(self) -> str:
        표 = "○" if self.뽑힘 else " "
        강도 = f"{self.상대강도:>+7.1f}%p" if self.상대강도 is not None else "      —"
        꼬리 = f"  {self.사유}" if self.사유 else ""
        return f"  {표} {self.이름:<12}{강도}{꼬리}"


def _강도(지수: pd.Series, 시장: pd.Series, 기준일: date | None, lookback: int) -> float | None:
    """기준일까지의 자료만 보고 잰다. **그 뒤 자료를 쓰면 검증이 거짓말이 된다.**"""
    공통 = 지수.index.intersection(시장.index)
    if 기준일 is not None:
        공통 = [d for d in 공통 if d <= 기준일]
    if len(공통) < lookback + 1:
        return None
    s, m = 지수.loc[공통], 시장.loc[공통]
    앞s, 뒤s = float(s.iloc[-1 - lookback]), float(s.iloc[-1])
    앞m, 뒤m = float(m.iloc[-1 - lookback]), float(m.iloc[-1])
    if 앞s <= 0 or 앞m <= 0:
        return None
    return ((뒤s / 앞s) - (뒤m / 앞m)) * 100.0


def rank(
    지수들: dict[str, pd.Series],
    이름표: dict[str, str],
    시장: pd.Series,
    기준일: date | None = None,
    lookback: int = LOOKBACK,
) -> list[섹터점수]:
    """섹터를 강도순으로 세운다. 아직 아무것도 고르지 않는다."""
    점수들 = []
    for 코드, 지수 in 지수들.items():
        강도 = _강도(지수, 시장, 기준일, lookback)
        점수들.append(
            섹터점수(
                코드=코드,
                이름=이름표.get(코드, 코드),
                상대강도=강도,
                사유="" if 강도 is not None else f"자료가 {lookback + 1}일보다 짧습니다",
            )
        )
    # 잴 수 없었던 것은 언제나 뒤로. 0으로 채우면 '보통'인 척하게 된다.
    점수들.sort(key=lambda p: (p.상대강도 is not None, p.상대강도 or 0), reverse=True)
    return 점수들


def pick(
    순위: list[섹터점수], top_n: int = TOP_N, 최소강도: float = MIN_STRENGTH
) -> list[섹터점수]:
    """강도순 위에서 top_n개까지, 단 최소강도를 넘는 것만.

    **개수를 채우려고 약한 섹터를 넣지 않는다.** 전부 시장보다 못한 날에는
    아무 섹터도 안 고른다. 그날은 안 사는 것이 답이다."""
    결과 = []
    뽑은수 = 0
    for p in 순위:
        if p.상대강도 is None:
            결과.append(p)
            continue
        if 뽑은수 < top_n and p.상대강도 >= 최소강도:
            결과.append(섹터점수(p.코드, p.이름, p.상대강도, True, p.사유))
            뽑은수 += 1
        else:
            사유 = p.사유 or ("자리가 찼습니다" if 뽑은수 >= top_n else "시장보다 못합니다")
            결과.append(섹터점수(p.코드, p.이름, p.상대강도, False, 사유))
    return 결과


def format_ranking(결과: list[섹터점수], lookback: int = LOOKBACK) -> str:
    뽑힌것 = [p for p in 결과 if p.뽑힘]
    줄 = [
        f"■ 1차: 섹터 고르기 (최근 {lookback}일 시장 대비 강도)",
        "",
        *[p.한줄() for p in 결과],
        "",
    ]
    if 뽑힌것:
        줄.append(f"  → {len(뽑힌것)}개 섹터: {', '.join(p.이름 for p in 뽑힌것)}")
    else:
        줄.append("  → **고른 섹터가 없습니다.** 전부 시장보다 못했습니다. 오늘은 안 삽니다.")
    return "\n".join(줄)


#: 한 섹터에서 최대 몇 종목까지 살 것인가.
MAX_PER_SECTOR = 2


def cap_per_sector(후보들, 상한: int = MAX_PER_SECTOR, 섹터키=None, 시작=None):
    """한 섹터에서 몇 종목까지만 남긴다. **예측이 아니라 제약이다.**

    45종목을 한 줄로 세워 놓고 신호가 난 것을 사면 어떤 날은 반도체 다섯
    종목이 한꺼번에 잡힌다. 분산한 줄 알았는데 **사실상 반도체 하나에
    다섯 배로 건 것**이다. 같은 이유로 오르고 같은 이유로 빠지는 종목들
    이니까.

    이건 "어디가 오를까"를 안 물어도 성립한다. 그래서 섹터 강도 검증이
    실패한 것(`docs/섹터선정_검증.md`)과 무관하게 유효하다. 미래를 맞히는
    장치가 아니라 **한 번에 얼마나 잃을 수 있는지를 줄이는 장치**다.

    후보들은 점수가 높은 순으로 들어와야 한다. 돌려주는 것은
    (남긴 것, 밀려난 것)이며, **밀려난 것도 돌려주는 이유는 왜 안 샀는지가
    왜 샀는지만큼 중요하기 때문**이다.

    `시작`은 **이미 들고 있는 종목의 섹터별 개수**다(2026-09-02에 더함).
    안 주면 0부터 세는데, 그러면 반도체를 두 종목 들고 있어도 그날 후보에
    반도체를 상한만큼 더 넣는다. 다 승인하면 상한을 넘긴 채로 보유하게
    된다. 09:05 실행이 같은 상한을 진짜 보유 기준으로 다시 보므로, 여기서
    안 세면 후보와 실제 매수가 어긋나 "승인했는데 왜 안 샀지"가 남는다.
    """
    섹터키 = 섹터키 or (lambda c: getattr(c, "sector", ""))
    센것: dict[str, int] = dict(시작 or {})
    남김, 밀림 = [], []
    for c in 후보들:
        키 = 섹터키(c)
        if 센것.get(키, 0) >= 상한:
            밀림.append(c)
            continue
        센것[키] = 센것.get(키, 0) + 1
        남김.append(c)
    return 남김, 밀림
