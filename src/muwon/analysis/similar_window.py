"""지금과 비슷했던 과거 구간을 찾고, 그때 좋았던 전략을 본다 (2026-09-06).

주인이 정한 전략 수립 과정의 2단계다. 1단계에서 변수를 고정하고, 여기서
"최근 20거래일과 비슷했던 과거가 언제였나"를 찾은 뒤, 그 시점 이후 20거래일에
어느 전략이 좋았는지를 본다.

## 왜 달력 구간이 아니라 비슷한 구간인가

지금까지의 저녁 검토는 1주·1개월·3개월이라는 달력 구간에서 순위를 냈다.
그 방식의 문제는 최근 구간의 1위를 그대로 고르면 결국 최근에 맞추는 것이고,
이 저장소가 이미 여러 번 기각한 방식이라는 것이다(설계안 §36, §41).

여기서는 "요즘 장이 이러이러하다"는 상태를 먼저 재고, 과거에 상태가 비슷했던
때를 찾아 그때 무엇이 통했는지를 본다. 최근에 맞추는 것이 아니라 상태에
맞추는 것이다. **그것이 더 낫다는 근거는 아직 없다.** 이 파일은 후보를
보여 줄 뿐이고, 채택은 사람이 한다.

## 무엇으로 비슷한지를 재나

넷이다. 주인과 정한 것이다.

1. 주가 추이   구간 등락률
2. 변동성      일간 등락률의 표준편차
3. 거래량      구간 평균 거래량 ÷ 그 앞 장기 평균
4. 외국인 수급 구간 외국인 순매매 합 ÷ 구간 거래량 합

넷을 각각 z점수로 바꾼 뒤 유클리드 거리를 잰다. z점수라 단위가 같다.
**지표마다 가중치를 두지 않는다.** 가중치를 정할 근거가 지금 없고, 근거
없이 정하면 그것이 곧 과최적화다. `market/analog.py`와 같은 생각이다.

## 반드시 지키는 넷

### ① 미래를 보지 않는다

과거 구간의 "이후 20거래일"이 오늘까지 다 지나가 있어야 한다. 그래서
후보에서 최근 `지평`거래일을 통째로 뺀다. 이걸 안 빼면 아직 끝나지 않은
구간의 성적을 아는 척하게 된다.

z점수도 그 시점까지의 자료로만 낸다. 전체 기간 평균으로 z를 내면 오늘의
평균이 과거 날짜의 z에 섞인다.

### ② 겹치는 날은 하나로 센다

연속된 날들은 거의 같은 상태다. 2022년 6월 20일과 21일은 같은 사건이지
두 사건이 아니다. 안 묶으면 표본이 스무 배 많은 것처럼 보인다.

### ③ 표본이 몇 개인지 반드시 같이 적는다

**숫자를 감추지는 않는다**(2026-09-06에 주인이 정함). 구간이 적어도
결과를 보여 주되, 몇 개로 낸 숫자인지를 함께 적고 `표본충분`을 False로
둔다. 고를지 말지는 사람이 정한다.

### ④ 이것으로 아무것도 자동으로 바꾸지 않는다

후보를 보여 주고 판단을 사람에게 넘긴다. 좋아 보인다고 갈아 끼우면 결국
최근에 맞추는 것이고, 그것은 이 저장소가 이미 기각한 방식이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from muwon.analysis.period_check import slice_for_range
from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.backtest.metrics import compute_metrics
from muwon.market.analog import _거리, _구간으로_묶기
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy

#: 최근 몇 거래일을 하나의 구간으로 볼 것인가.
구간길이 = 20

#: 비슷했던 구간 다음 며칠을 볼 것인가. 구간 길이와 같게 둔다. 다르게 두면
#: "20일이 비슷했으니 그다음 20일도 비슷할 것"이라는 말이 성립하지 않는다.
지평 = 20

#: 가까운 것 몇 %를 후보로 볼 것인가.
상위비율 = 5.0

#: 이만큼 안에 있는 날은 같은 사건으로 본다(거래일).
묶는간격 = 20

#: 구간이 이보다 적으면 표본이 모자란다고 적는다. 숫자는 그대로 낸다.
최소구간 = 8

#: z점수를 낼 때 돌아보는 거래일. 약 1년이다.
돌아보기 = 250

#: 거래량이 평소보다 얼마나 많은지를 잴 때 쓰는 기준 구간(거래일).
거래량기준 = 60

#: 서버가 UTC라 datetime.now(tz=서울).date()를 그냥 쓰면 오전 9시 이전에 어제가 나온다.
서울 = ZoneInfo("Asia/Seoul")

특징이름 = ("등락률", "변동성", "거래량비", "외국인비")


@dataclass(frozen=True)
class 상태:
    """한 구간의 상태. 네 값이 곧 '요즘 장이 어떤가'다."""

    끝일: date
    등락률: float
    변동성: float
    거래량비: float
    외국인비: float | None  # 외국인 자료가 없으면 None

    def 한줄(self) -> str:
        수급 = ("외국인 수급 " + f"{self.외국인비:+.2f}%"
              if self.외국인비 is not None else "외국인 수급 없음")
        return (f"등락률 {self.등락률:+.2f}% · 변동성 {self.변동성:.2f}% · "
                f"거래량 평소의 {self.거래량비:.2f}배 · {수급}")


@dataclass(frozen=True)
class 비슷한구간:
    """비슷했던 과거 하나. 대표일 다음 거래일부터 지평만큼을 본다."""

    대표일: date
    시작: date
    끝: date
    거리: float
    묶인일수: int


@dataclass(frozen=True)
class 전략성적:
    """한 전략이 비슷했던 구간들에서 낸 성적."""

    키: str
    구간수: int
    중앙값: float
    최악: float
    최고: float
    이긴구간: int
    거래합: int
    못잰구간: int = 0

    @property
    def 이긴비율(self) -> float:
        return self.이긴구간 / self.구간수 * 100 if self.구간수 else 0.0


@dataclass(frozen=True)
class 찾은것:
    """2단계의 결과 전체."""

    기준일: date
    지금: 상태 | None
    구간들: list[비슷한구간] = field(default_factory=list)
    순위: list[전략성적] = field(default_factory=list)
    사유: str = ""
    쓴특징: tuple[str, ...] = ()

    @property
    def 구간수(self) -> int:
        return len(self.구간들)

    @property
    def 표본충분(self) -> bool:
        return self.구간수 >= 최소구간

    @property
    def 낼수있나(self) -> bool:
        return bool(self.순위)

    def 표본글(self) -> str:
        """**숫자를 낼 때 반드시 같이 나가는 문장.**

        몇 개로 낸 숫자인지를 안 적으면, 구간 셋으로 낸 순위를 5년치 검증과
        같은 무게로 읽는다."""
        if not self.구간들:
            return "비슷했던 구간을 찾지 못했습니다."
        말 = f"비슷했던 구간 {self.구간수}개로 계산한 결과입니다."
        if not self.표본충분:
            말 += (f" 최소 기준 {최소구간}개에 못 미칩니다. "
                   "표본이 적어 우연일 수 있습니다.")
        return 말


def 특징표(
    histories: dict[str, pd.DataFrame],
    수급: dict[str, pd.DataFrame] | None = None,
    길이: int = 구간길이,
) -> pd.DataFrame:
    """날마다 '그날로 끝나는 구간'의 특징 넷을 낸다.

    종목을 동일가중으로 묶는다. 시가총액으로 무게를 주면 큰 종목 몇이
    전체를 정한다. 이 시스템은 39종목을 같은 비중으로 보므로 동일가중이
    실제 매매와 가깝다."""
    수급 = 수급 or {}
    일별: dict[str, pd.DataFrame] = {}
    for 코드, df in histories.items():
        if df is None or df.empty:
            continue
        표 = df.set_index(pd.to_datetime(df["trade_date"])).sort_index()
        조각 = pd.DataFrame(index=표.index)
        조각["등락"] = 표["close"].astype(float).pct_change() * 100
        조각["거래량"] = 표["volume"].astype(float)
        흐름 = 수급.get(코드)
        if 흐름 is not None and not 흐름.empty:
            맞춘것 = 흐름.reindex(표.index)
            조각["외국인"] = 맞춘것["외국인순매매"].astype(float)
        일별[코드] = 조각

    if not 일별:
        return pd.DataFrame(columns=list(특징이름))

    등락 = pd.concat({ㅋ: v["등락"] for ㅋ, v in 일별.items()}, axis=1).mean(axis=1)
    거래량 = pd.concat({ㅋ: v["거래량"] for ㅋ, v in 일별.items()}, axis=1).sum(axis=1)
    외국인칸 = {ㅋ: v["외국인"] for ㅋ, v in 일별.items() if "외국인" in v}
    외국인 = (pd.concat(외국인칸, axis=1).sum(axis=1, min_count=1)
            if 외국인칸 else None)

    표 = pd.DataFrame(index=등락.index)
    표["등락률"] = 등락.rolling(길이).sum()
    표["변동성"] = 등락.rolling(길이).std()
    # 거래량은 절대값이 아니라 평소 대비로 본다. 종목 수가 바뀌거나 시장이
    # 통째로 커지면 절대값은 뜻을 잃는다.
    표["거래량비"] = (거래량.rolling(길이).mean()
                / 거래량.rolling(거래량기준).mean().shift(길이))
    if 외국인 is not None:
        # 순매매 주식 수를 그대로 쓰면 종목 수와 주가에 휘둘린다. 같은
        # 구간의 거래량으로 나눠 "그 구간 거래 중 외국인 순매수 몫"으로 본다.
        표["외국인비"] = (외국인.rolling(길이).sum()
                    / 거래량.rolling(길이).sum() * 100)
    else:
        표["외국인비"] = np.nan
    return 표


def z로바꾸기(표: pd.DataFrame, 돌아볼일수: int = 돌아보기) -> pd.DataFrame:
    """**그 시점까지의 자료로만** z점수를 낸다.

    전체 기간 평균으로 내면 오늘의 평균이 2021년 날짜의 z에 섞인다. 그것은
    그날 알 수 없던 값이라, 과거를 지금 눈으로 다시 칠하는 것이 된다."""
    평균 = 표.rolling(돌아볼일수, min_periods=돌아볼일수 // 2).mean()
    편차 = 표.rolling(돌아볼일수, min_periods=돌아볼일수 // 2).std()
    # 편차가 0이면 나눌 수 없다. 그 칸은 못 잰 것으로 두고 0으로 채우지
    # 않는다. 0은 "평균과 같다"는 뜻이라 "못 쟀다"와 다르다.
    return (표 - 평균) / 편차.replace(0, np.nan)


def 비슷한구간찾기(
    histories: dict[str, pd.DataFrame],
    수급: dict[str, pd.DataFrame] | None = None,
    기준일: date | None = None,
    길이: int = 구간길이,
    앞으로: int = 지평,
    상위: float = 상위비율,
) -> tuple[list[비슷한구간], 상태 | None, str, tuple[str, ...]]:
    """(구간들, 지금 상태, 못 찾은 사유, 쓴 특징)."""
    원표 = 특징표(histories, 수급, 길이)
    if 원표.empty:
        return [], None, "시세를 못 받아 상태를 재지 못했습니다.", ()

    # 외국인 자료가 통째로 없으면 그 칸을 빼고 셋으로 잰다. NaN을 그대로
    # 두면 거리 계산에서 모든 날이 탈락한다.
    쓸칸 = [ㅋ for ㅋ in 특징이름 if 원표[ㅋ].notna().any()]
    표 = 원표[쓸칸]
    z = z로바꾸기(표).dropna()
    if z.empty:
        return [], None, "z점수를 낼 만큼 과거가 길지 않습니다.", tuple(쓸칸)

    끝 = pd.Timestamp(기준일) if 기준일 else z.index[-1]
    if 끝 not in z.index:
        앞선것 = z.index[z.index <= 끝]
        if len(앞선것) == 0:
            return [], None, f"{끝.date()}까지의 자료가 없습니다.", tuple(쓸칸)
        끝 = 앞선것[-1]

    지금줄 = 표.loc[끝]
    지금 = 상태(
        끝일=끝.date(),
        등락률=float(지금줄.get("등락률", np.nan)),
        변동성=float(지금줄.get("변동성", np.nan)),
        거래량비=float(지금줄.get("거래량비", np.nan)),
        외국인비=(float(지금줄["외국인비"]) if "외국인비" in 지금줄 else None),
    )

    # **미래를 보지 않는다.** 이후 앞으로거래일이 다 지나간 날만 후보다.
    # 그리고 지금 구간과 겹치는 날은 뺀다. 어제와 비슷하다고 해 봐야
    # 어제의 다음 20일에 오늘이 들어 있다.
    쓸수있는끝 = z.index[z.index <= 끝]
    if len(쓸수있는끝) <= 앞으로 + 길이:
        return [], 지금, "비교할 과거가 짧습니다.", tuple(쓸칸)
    후보들 = z.loc[쓸수있는끝[: -(앞으로 + 길이)]]
    if 후보들.empty:
        return [], 지금, "비교할 과거가 짧습니다.", tuple(쓸칸)

    거리 = _거리(후보들, z.loc[끝])
    자를값 = float(np.percentile(거리.to_numpy(), 상위))
    가까운것 = 거리[거리 <= 자를값]
    if 가까운것.empty:
        return [], 지금, "가까운 과거를 찾지 못했습니다.", tuple(쓸칸)

    날짜만 = pd.Series(가까운것.to_numpy(),
                    index=[ㅇ.date() for ㅇ in 가까운것.index])
    구간들 = []
    for 묶음 in _구간으로_묶기(날짜만, gap=묶는간격):
        # 한 구간에서 가장 가까운 하루만 대표로 쓴다. 안 그러면 같은 사건이
        # 여러 번 센 것이 된다.
        대표 = min(묶음, key=lambda ㅇ: float(날짜만.loc[ㅇ]))
        구간들.append(비슷한구간(
            대표일=대표, 시작=min(묶음), 끝=max(묶음),
            거리=float(날짜만.loc[대표]), 묶인일수=len(묶음),
        ))
    구간들.sort(key=lambda ㄱ: ㄱ.거리)
    return 구간들, 지금, "", tuple(쓸칸)


def _거래일더하기(histories: dict[str, pd.DataFrame], 시작: date, 일수: int) -> date:
    """시작일로부터 거래일 기준으로 일수만큼 뒤. 달력일로 세면 연휴에 어긋난다."""
    날들 = sorted({d for df in histories.values()
                 for d in pd.to_datetime(df["trade_date"]).dt.date})
    뒤 = [ㅇ for ㅇ in 날들 if ㅇ > 시작]
    if not 뒤:
        return 시작
    return 뒤[min(일수, len(뒤)) - 1]


def 구간에서재기(
    구간들: list[비슷한구간],
    전략들: dict,
    histories: dict[str, pd.DataFrame],
    정책: RiskPolicy,
    costs: TransactionCosts | None = None,
    앞으로: int = 지평,
    예수금: float = 5_000_000.0,
    섹터표: dict[str, str] | None = None,
    섹터상한: int = 0,
) -> list[전략성적]:
    """비슷했던 구간마다 전략 전부를 계산하고 모아서 순위를 낸다.

    순위는 **가장 나빴던 구간**을 먼저 본다. 이 저장소의 1순위 판단 기준과
    같다(CLAUDE.md §4). 평균이 아무리 높아도 한 번 크게 잃으면 그 뒤가
    없다."""
    모은것: dict[str, list[float]] = {ㅋ: [] for ㅋ in 전략들}
    거래합: dict[str, int] = {ㅋ: 0 for ㅋ in 전략들}
    못잰것: dict[str, int] = {ㅋ: 0 for ㅋ in 전략들}

    for 구간 in 구간들:
        시작 = 구간.대표일
        마지막 = _거래일더하기(histories, 시작, 앞으로)
        잘린것 = slice_for_range(histories, 시작, 마지막)
        if not 잘린것:
            for ㅋ in 전략들:
                못잰것[ㅋ] += 1
            continue
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
                ).run(잘린것, trade_from=시작)
            except Exception:  # noqa: BLE001 (하나 때문에 전부 멈추면 안 된다)
                못잰것[키] += 1
                continue
            ㅈ = compute_metrics(결과)
            # **못 잰 것을 0%로 채우지 않는다.** 0%는 "안 움직였다"는 뜻이라
            # "계산을 못 했다"와 다르다. 섞으면 신호가 실제보다 밋밋해진다.
            모은것[키].append(float(ㅈ.total_return_pct))
            거래합[키] += int(ㅈ.num_trades)

    성적들 = []
    for 키, 값들 in 모은것.items():
        if not 값들:
            continue
        성적들.append(전략성적(
            키=키,
            구간수=len(값들),
            중앙값=float(np.median(값들)),
            최악=float(min(값들)),
            최고=float(max(값들)),
            이긴구간=sum(1 for ㄱ in 값들 if ㄱ > 0),
            거래합=거래합[키],
            못잰구간=못잰것[키],
        ))
    # 거래가 한 건도 없던 전략은 뺀다. 수익률 0%로 위에 오지만 지킨 것이
    # 아니라 아무것도 안 한 것이다. 순위에서 거래 0건을 빼는 것과 같은 이유다.
    성적들 = [ㄱ for ㄱ in 성적들 if ㄱ.거래합 > 0]
    성적들.sort(key=lambda ㄱ: (-ㄱ.최악, -ㄱ.중앙값))
    return 성적들


def 찾기(
    histories: dict[str, pd.DataFrame],
    전략들: dict,
    정책: RiskPolicy,
    수급: dict[str, pd.DataFrame] | None = None,
    기준일: date | None = None,
    costs: TransactionCosts | None = None,
    길이: int = 구간길이,
    앞으로: int = 지평,
    섹터표: dict[str, str] | None = None,
    섹터상한: int = 0,
) -> 찾은것:
    """2단계 전체. 비슷한 구간을 찾고 그때 좋았던 전략을 낸다."""
    구간들, 지금, 사유, 쓴것 = 비슷한구간찾기(
        histories, 수급, 기준일=기준일, 길이=길이, 앞으로=앞으로)
    if not 구간들:
        return 찾은것(
            기준일=(기준일 or (지금.끝일 if 지금 else datetime.now(tz=서울).date())),
            지금=지금, 사유=사유, 쓴특징=쓴것)
    순위 = 구간에서재기(구간들, 전략들, histories, 정책, costs=costs,
                 앞으로=앞으로, 섹터표=섹터표, 섹터상한=섹터상한)
    return 찾은것(
        기준일=지금.끝일 if 지금 else (기준일 or datetime.now(tz=서울).date()),
        지금=지금, 구간들=구간들, 순위=순위, 쓴특징=쓴것,
        사유="" if 순위 else "구간은 찾았으나 매수가 발생한 전략이 없습니다.",
    )
