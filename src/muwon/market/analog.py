"""지금과 비슷했던 과거를 찾고, 그 다음에 무슨 일이 있었는지 센다.

이게 이 시스템이 말하는 **"전망"**이다. 그리고 전망이 무엇이 아닌지를
먼저 못 박는다.

> **"오를 것이다"라고 말하지 않는다.** 그건 못 맞힌다.
> 대신 **"이런 상태에서 과거에는 이런 일들이 있었다"**를 말한다.

## 어떻게 하나

1. 오늘의 장 상태(z점수 여러 개)를 벡터 하나로 본다
2. 과거 모든 날과의 **거리**를 잰다. 가까울수록 비슷한 날이다
3. 가까운 날들을 고른 뒤, 각각의 **다음 N거래일 수익률**을 본다
4. 그 수익률들의 분포를 낸다 (중앙값, 하위 10%, 오른 비율)

## 반드시 지켜야 할 세 가지

### ① 겹치는 날은 하나로 센다

"비슷한 날 291일 발견"이라고 나와도, 연속된 날들은 **거의 같은 상태**다.
2020년 3월 20일과 21일은 같은 사건이지 두 사건이 아니다.

이걸 안 묶으면 **표본이 17배 많은 것처럼 착각한다.** 그러면 "확률 63%"가
아주 믿을 만해 보이는데, 실은 사건 17개 중 10개일 뿐이다.

그래서 가까운 날들을 **구간으로 묶고, 구간 하나에서 대표 하루만 쓴다.**
화면에는 **언제나 구간 수를 쓴다.** 일수는 괄호 안에 둔다.

### ② 구간이 적으면 숫자를 내지 않는다

구간 8개로 낸 "상승 확률 63%"는 확률이 아니다. 그럴 땐 숫자 대신
**"전망 불가. 비슷했던 때가 3번뿐"**이라고 쓴다.

### ③ 미래를 보지 않는다

오늘과 비교할 과거는 **오늘로부터 N일 이전까지**만이다. 어제와 비슷하다고
해 봐야, 어제의 '다음 20일'에는 오늘이 들어 있어서 답을 미리 아는 셈이 된다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import numpy as np
import pandas as pd

#: 이만큼 안에 있는 날들은 같은 사건으로 본다(거래일).
GAP_DAYS = 20
#: 구간이 이보다 적으면 숫자를 내지 않는다.
MIN_EPISODES = 8
#: 앞으로 며칠을 볼 것인가.
HORIZON = 20
#: 비교할 과거가 이보다 짧으면 거리 계산 자체가 뜻이 없다(약 1년).
MIN_HISTORY_DAYS = 250


@dataclass(frozen=True)
class Episode:
    """비슷했던 구간 하나."""

    대표일: date
    시작: date
    끝: date
    일수: int
    거리: float
    이후수익: float  # 대표일로부터 HORIZON 거래일 뒤까지의 수익률(%)


@dataclass(frozen=True)
class Baseline:
    """아무 조건 없이 그냥 아무 날에나 샀다면: **비교 기준선**.

    이게 없으면 전망을 읽을 수가 없다. "비슷한 날 뒤에 75% 올랐다"가
    좋아 보여도, **그 대상이 원래 아무 날에나 사도 75% 올랐다면 전망은
    아무것도 더하지 않은 것**이다.

    실제로 우리 섹터 지수가 그럴 소지가 크다. 오늘까지 살아남은 종목으로
    만들었기 때문이다(생존편향). 그래서 반드시 같이 낸다."""

    표본수: int
    중앙값: float
    하위10: float
    상승확률: float


@dataclass(frozen=True)
class Forecast:
    """전망 하나. **숫자가 아니라 분포다.**"""

    기준일: date
    대상: str
    구간수: int
    총일수: int
    지평: int
    중앙값: float | None
    상위25: float | None
    하위25: float | None
    하위10: float | None
    상승확률: float | None
    구간들: list[Episode]
    사유: str = ""  # 숫자를 못 낸 이유
    기준선: Baseline | None = None

    @property
    def 낼수있나(self) -> bool:
        return self.중앙값 is not None

    @property
    def 더한것_중앙값(self) -> float | None:
        """전망이 기준선보다 얼마나 더 말해 주나."""
        if self.중앙값 is None or self.기준선 is None:
            return None
        return self.중앙값 - self.기준선.중앙값

    @property
    def 더한것_상승확률(self) -> float | None:
        if self.상승확률 is None or self.기준선 is None:
            return None
        return self.상승확률 - self.기준선.상승확률

    @property
    def 우연폭(self) -> float | None:
        """구간이 이만큼뿐일 때, **아무 정보 없이도** 상승확률이 우연히
        벌어질 수 있는 폭(±%p, 95% 기준).

        구간 16개면 ±24%p다. 즉 "+26%p 더 올랐다"는 우연과 거의 구분이
        안 된다. 이 칸이 없으면 그 차이를 발견으로 읽게 된다."""
        if self.구간수 < 2:
            return None
        return 196.0 * (0.25 / self.구간수) ** 0.5

    @property
    def 우연을_넘었나(self) -> bool | None:
        폭 = self.우연폭
        차이 = self.더한것_상승확률
        if 폭 is None or 차이 is None:
            return None
        return abs(차이) > 폭


def baseline(price: pd.Series, horizon: int = HORIZON, until: date | None = None) -> Baseline | None:
    """조건 없이 아무 날에나 샀다면 그 뒤 horizon일이 어땠나.

    `until`을 주면 그날까지만 본다. 전망과 같은 정보만 쓰게 하기 위해서다."""
    s = price if until is None else price.loc[:until]
    if len(s) <= horizon + 10:
        return None
    앞 = s.iloc[:-horizon].to_numpy(dtype=float)
    뒤 = s.iloc[horizon:].to_numpy(dtype=float)
    쓸수있는것 = 앞 > 0
    수익 = (뒤[쓸수있는것] / 앞[쓸수있는것] - 1) * 100
    if len(수익) == 0:
        return None
    return Baseline(
        표본수=len(수익),
        중앙값=float(np.median(수익)),
        하위10=float(np.percentile(수익, 10)),
        상승확률=float((수익 > 0).sum()) / len(수익) * 100,
    )


def _거리(state: pd.DataFrame, 기준: pd.Series) -> pd.Series:
    """각 날짜와 기준 상태 사이의 거리.

    z점수라 단위가 같으므로 그냥 유클리드 거리를 쓴다. 지표별 가중치를
    두는 방법도 있지만, 가중치를 정할 근거가 지금 없다. 근거 없이 정하면
    그게 곧 과최적화다."""
    차이 = state.to_numpy(dtype=float) - 기준.to_numpy(dtype=float)
    return pd.Series(np.sqrt((차이**2).sum(axis=1)), index=state.index)


def _구간으로_묶기(후보: pd.Series, gap: int = GAP_DAYS) -> list[list[date]]:
    """가까운 날짜들을 구간으로 묶는다.

    후보는 거리 순이 아니라 **날짜 순**으로 들어와야 한다."""
    날짜들 = sorted(후보.index)
    if not 날짜들:
        return []
    구간들, 지금 = [], [날짜들[0]]
    for 앞, 뒤 in pairwise(날짜들):
        if (뒤 - 앞).days <= gap * 2:  # 거래일 gap을 달력일로 넉넉히 잡는다
            지금.append(뒤)
        else:
            구간들.append(지금)
            지금 = [뒤]
    구간들.append(지금)
    return 구간들


def forecast(
    state: pd.DataFrame,
    price: pd.Series,
    대상: str,
    기준일: date | None = None,
    top_pct: float = 5.0,
    horizon: int = HORIZON,
    min_episodes: int = MIN_EPISODES,
) -> Forecast:
    """지금과 비슷했던 과거를 찾아 그 뒤 분포를 낸다.

    state: 날짜별 z점수 표 (build_state 결과)
    price: 같은 날짜 색인의 종가. 이걸로 '그 뒤 수익'을 잰다
    top_pct: 가장 가까운 상위 몇 %를 후보로 볼 것인가
    """
    공통 = state.index.intersection(price.index)
    state, price = state.loc[공통], price.loc[공통]
    if len(state) == 0:
        return Forecast(기준일 or date.min, 대상, 0, 0, horizon, None, None, None, None, None, [], "겹치는 날짜가 없습니다")

    기준일 = 기준일 or state.index[-1]
    if 기준일 not in state.index:
        return Forecast(기준일, 대상, 0, 0, horizon, None, None, None, None, None, [], f"{기준일}의 상태가 없습니다")

    기준 = state.loc[기준일]
    # ③ 미래를 보지 않는다. 기준일로부터 horizon 거래일 이전까지만 후보다.
    자리 = state.index.get_loc(기준일)
    쓸수있는끝 = max(자리 - horizon, 0)
    과거 = state.iloc[:쓸수있는끝]
    # 비교할 과거가 아예 짧으면 거리 계산 자체가 뜻이 없다. 이건 min_episodes와
    # 다른 문제라 따로 본다. 섞어 두면 "구간이 모자라다"와 "과거가 짧다"가
    # 같은 문구로 나와서 무엇을 고쳐야 할지 알 수 없다.
    if len(과거) < MIN_HISTORY_DAYS:
        return Forecast(
            기준일, 대상, 0, 0, horizon, None, None, None, None, None, [],
            f"비교할 과거가 {len(과거)}일뿐입니다 (최소 {MIN_HISTORY_DAYS}일 필요)",
        )

    거리 = _거리(과거, 기준).sort_values()
    뽑을수 = max(int(len(거리) * top_pct / 100), min_episodes)
    후보 = 거리.iloc[:뽑을수]

    # ① 겹치는 날은 하나로 센다
    구간들 = []
    for 묶음 in _구간으로_묶기(후보):
        대표 = min(묶음, key=lambda d: 후보[d])
        i = price.index.get_loc(대표)
        j = i + horizon
        if j >= len(price):
            continue  # 그 뒤 horizon일이 없는 구간은 결과를 모른다
        수익 = (float(price.iloc[j]) / float(price.iloc[i]) - 1) * 100
        구간들.append(
            Episode(
                대표일=대표, 시작=min(묶음), 끝=max(묶음), 일수=len(묶음),
                거리=float(후보[대표]), 이후수익=수익,
            )
        )

    구간들.sort(key=lambda e: e.거리)
    총일수 = sum(e.일수 for e in 구간들)

    # ② 구간이 적으면 숫자를 내지 않는다
    if len(구간들) < min_episodes:
        return Forecast(
            기준일, 대상, len(구간들), 총일수, horizon, None, None, None, None, None, 구간들,
            f"비슷했던 때가 {len(구간들)}번뿐입니다 (최소 {min_episodes}번 필요)",
        )

    수익들 = sorted(e.이후수익 for e in 구간들)
    return Forecast(
        기준일=기준일,
        대상=대상,
        구간수=len(구간들),
        총일수=총일수,
        지평=horizon,
        중앙값=statistics.median(수익들),
        상위25=float(np.percentile(수익들, 75)),
        하위25=float(np.percentile(수익들, 25)),
        하위10=float(np.percentile(수익들, 10)),
        상승확률=sum(1 for r in 수익들 if r > 0) / len(수익들) * 100,
        구간들=구간들,
        기준선=baseline(price, horizon, until=기준일),
    )


def format_forecast(f: Forecast, 보여줄구간: int = 5) -> str:
    머리 = f"■ {f.대상}: {f.기준일} 기준"
    if not f.낼수있나:
        return f"{머리}\n\n  전망 불가. {f.사유}"

    lines = [
        머리,
        "",
        f"  비슷했던 과거 **{f.구간수}개 구간** (총 {f.총일수}일)",
        f"  그 뒤 {f.지평}거래일에 무슨 일이 있었나",
        "",
        f"    중앙값        {f.중앙값:>+7.1f}%",
        f"    좋았을 때     {f.상위25:>+7.1f}%   (상위 25%)",
        f"    나빴을 때     {f.하위25:>+7.1f}%   (하위 25%)",
        f"    아주 나빴을 때 {f.하위10:>+7.1f}%   ← 여기를 보고 비중을 정한다",
        (
            f"    오른 경우      {f.상승확률:>6.0f}%   ({f.구간수}번 중 "
            f"{round(f.상승확률 * f.구간수 / 100)}번)"
        ),
    ]

    # 기준선과 나란히 놓지 않으면 이 숫자를 읽을 수가 없다.
    if f.기준선:
        b = f.기준선
        lines += [
            "",
            f"  ── 그냥 아무 날에나 샀다면 (표본 {b.표본수}일) ──",
            f"    중앙값        {b.중앙값:>+7.1f}%",
            f"    아주 나빴을 때 {b.하위10:>+7.1f}%",
            f"    오른 경우      {b.상승확률:>6.0f}%",
            "",
            (
                f"  **전망이 더한 것**: 중앙값 {f.더한것_중앙값:>+.1f}%p · "
                f"상승확률 {f.더한것_상승확률:>+.0f}%p"
            ),
        ]
        if f.우연폭 is not None:
            판정 = "우연으로 보기 어렵다" if f.우연을_넘었나 else "**우연과 구분이 안 된다**"
            lines.append(
                f"  구간 {f.구간수}개에서 우연히 벌어질 수 있는 폭은 ±{f.우연폭:.0f}%p: {판정}"
            )
        if abs(f.더한것_중앙값) < 0.5 and abs(f.더한것_상승확률) < 5:
            lines.append("  ⚠ 기준선과 거의 같습니다. 이 전망은 아무것도 안 알려 주고 있습니다.")
        if b.상승확률 > 70:
            lines.append(
                "  ⚠ 아무 날에나 사도 70% 넘게 올랐습니다. **생존편향을 의심해야 합니다.** "
                "오늘까지 살아남은 종목으로 과거를 만들었기 때문입니다."
            )
    if f.구간들:
        lines += ["", f"  가장 비슷했던 때 {min(보여줄구간, len(f.구간들))}개"]
        for e in f.구간들[:보여줄구간]:
            lines.append(f"    {e.시작} ~ {e.끝} ({e.일수}일)  →  {e.이후수익:>+6.1f}%")
    lines += [
        "",
        "  읽는 법: '아주 나빴을 때'가 -5%면 평소대로, -25%면 그날은 덜 삽니다.",
        "  **감으로 정하는 게 아니라 과거가 정합니다.**",
    ]
    return "\n".join(lines)
