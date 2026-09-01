"""시장 분위기를 재는 데 쓰는 바깥 시계열들.

## 왜 개별 종목이 아니라 이것들인가

우리 종목 데이터는 **5년**뿐이고, 그것도 **오늘까지 살아남은 종목**이다
(그 사이 망한 회사는 목록에 없다). 그 5년에는 진짜 폭락장이 하나도 없다.

반면 지수와 국제 시세는 훨씬 길고 생존편향도 없다. 실제로 받아 보니
이렇다.

| | 일수 | 시작 | 안에 들어 있는 것 |
|---|---|---|---|
| 코스피 | 7,307 | 1996-12 | 1997 외환위기, 2000, 2008, 2011, 2020, 2022 |
| 코스닥 | 6,368 | 2000-10 | |
| 금·은·구리 선물 | ~6,520 | 2000-08 | |
| 달러/원 | 5,892 | 2003-12 | |
| 미 10년물 금리 | 9,193 | 1990-01 | |
| 나스닥 | 9,221 | 1990-01 | |
| 필라델피아 반도체 | 8,124 | 1994-05 | **반도체 섹터 전망용** |

**"지금과 비슷했던 과거"를 찾으려면 과거가 길어야 한다.** 5년으로는
비슷했던 때가 몇 번 안 나온다.

## 렌즈: 지표를 몇 개까지 쓸 것인가

시계열마다 시작 날짜가 달라서, **많이 쓸수록 표본 기간이 짧아진다.**
그래서 두 벌을 둔다.

- **`기본`** (1996~, 약 7,300일): 코스피 하나. 가장 길지만 단순하다
- **`확장`** (2003~, 약 5,600일): 코스피·코스닥·금÷구리·환율·미금리.
  23년이면 2008·2011·2020·2022가 다 들어간다. **기본값으로 쓴다**

둘 다 만들어 두고 결과를 비교한다. 렌즈를 바꿨을 때 답이 크게 달라지면
그건 그 답을 믿을 수 없다는 뜻이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class Series:
    """바깥에서 받아 오는 시계열 하나."""

    키: str
    이름: str
    야후심볼: str
    쓰임: str
    시작: date  # 실제로 받아 본 첫 날 (2026-08-19 확인)


#: 받아 본 것들. 시작 날짜는 실측이다. 추측으로 적으면 렌즈의 표본 기간이 거짓이 된다.
SERIES: dict[str, Series] = {
    s.키: s
    for s in [
        Series("kospi", "코스피", "^KS11", "시장 전체 분위기의 중심", date(1996, 12, 11)),
        Series("kosdaq", "코스닥", "^KQ11", "중소형·성장주 쪽 분위기", date(2000, 10, 16)),
        Series("gold", "금 선물", "GC=F", "위험 회피 신호. 겁먹으면 금으로 간다", date(2000, 8, 30)),
        Series("silver", "은 선물", "SI=F", "금과 함께 원자재 ETF 전망에 쓴다", date(2000, 8, 30)),
        Series("copper", "구리 선물", "HG=F", "경기 신호. 제조업이 돌면 구리가 오른다", date(2000, 8, 30)),
        Series("usdkrw", "달러/원", "KRW=X", "수출주(반도체·자동차)에 직접 영향", date(2003, 12, 1)),
        Series("ust10y", "미 10년물 금리", "^TNX", "금리가 오르면 성장주가 눌린다", date(1990, 1, 2)),
        Series("nasdaq", "나스닥", "^IXIC", "전날 미국장: 우리 시가에 영향", date(1990, 1, 2)),
        Series("sox", "필라델피아 반도체", "^SOX", "반도체 섹터 전망용. 우리 종목보다 25년 길다", date(1994, 5, 4)),
    ]
}

#: 렌즈: 어떤 지표 묶음으로 '지금 상태'를 적을 것인가.
#: 값은 (쓰는 시계열, 표본이 시작되는 해, 설명).
LENSES: dict[str, tuple[tuple[str, ...], int, str]] = {
    "기본": (("kospi",), 1997, "코스피 하나만. 가장 길지만 단순하다"),
    "확장": (
        ("kospi", "kosdaq", "gold", "copper", "usdkrw", "ust10y"),
        2004,
        "코스피·코스닥·금÷구리·환율·미금리. 23년이면 2008·2011·2020·2022가 다 들어간다",
    ),
}
DEFAULT_LENS = "확장"


def lens_series(lens: str = DEFAULT_LENS) -> tuple[Series, ...]:
    if lens not in LENSES:
        raise KeyError(f"모르는 렌즈: {lens} (있는 것: {', '.join(LENSES)})")
    return tuple(SERIES[k] for k in LENSES[lens][0])


def lens_start_year(lens: str = DEFAULT_LENS) -> int:
    """이 렌즈로 볼 수 있는 표본이 어느 해부터인가.

    가장 늦게 시작하는 시계열이 정한다. 하나라도 없으면 그날 상태를
    적을 수 없기 때문이다."""
    if lens not in LENSES:
        raise KeyError(f"모르는 렌즈: {lens}")
    return LENSES[lens][1]


#: 받아 온 데이터가 알려진 시작일에서 이만큼 안쪽까지는 닿아야 한다(일).
#: 상장·산출 시작 근처의 며칠 차이는 정상이므로 넉넉히 잡는다.
COVERAGE_SLACK_DAYS = 400
#: 짧게 왔을 때 다시 시도할 횟수.
RETRIES = 3


def _충분한가(df: pd.DataFrame, s: Series, start: date) -> bool:
    """받아 온 구간이 기대만큼 거슬러 올라가나.

    **왜 이 확인이 필요한가**. 야후가 같은 요청에 어떤 때는 30년치를,
    어떤 때는 최근 19일치만 준다. 실제로 미 10년물(^TNX)이 그랬다.
    짧게 온 걸 모르고 쓰면 렌즈의 다른 지표들과 겹치는 날이 19일뿐이 되어
    **장 상태 표가 통째로 비고**, 그 사실은 "표가 비었다"로만 나타난다.
    무엇이 원인인지 알 방법이 없다."""
    if df is None or len(df) == 0:
        return False
    기대시작 = max(start, s.시작)
    실제시작 = min(df["trade_date"])
    return (실제시작 - 기대시작).days <= COVERAGE_SLACK_DAYS


def load(
    keys, start: date, end: date, source=None, cache=None
) -> dict[str, pd.DataFrame]:
    """시계열들을 받아 온다. 캐시를 주면 이미 받은 구간은 다시 안 받는다.

    **하나라도 못 받거나 짧게 오면 터뜨린다.** 조용히 넘어가면 렌즈가
    말하는 지표 수와 실제로 쓴 지표 수가 달라지는데, 그건 화면에 아무
    표시도 안 남는다."""
    from muwon.data.yahoo_client import YahooFinanceDataSource

    source = source or YahooFinanceDataSource()
    결과 = {}
    for key in keys:
        시리즈 = SERIES[key] if isinstance(key, str) else key
        df = None
        for 시도 in range(RETRIES):
            if cache is not None and 시도 == 0:
                df = cache.fetch(source, 시리즈.야후심볼, 시리즈.야후심볼, start, end)
            else:
                # 다시 받을 때는 캐시를 건너뛴다. 짧게 온 것이 캐시에
                # 들어갔을 수 있고, 그러면 몇 번을 시도해도 같은 것이 온다.
                df = source.get_daily_ohlcv(시리즈.야후심볼, start, end)
                if cache is not None and _충분한가(df, 시리즈, start):
                    cache.put(시리즈.야후심볼, df, start, end)
            if _충분한가(df, 시리즈, start):
                break
        if not _충분한가(df, 시리즈, start):
            받은것 = "0일" if df is None or len(df) == 0 else f"{len(df)}일 ({min(df['trade_date'])}~)"
            raise RuntimeError(
                f"{시리즈.이름}({시리즈.야후심볼}) 시세가 짧게 왔습니다. {받은것}. "
                f"{max(start, 시리즈.시작)} 근처까지 필요합니다. "
                f"{RETRIES}번 다시 받아도 같았습니다. 야후가 간헐적으로 최근 며칠만 주는 일이 "
                f"있으니 잠시 뒤 다시 실행해 보세요."
            )
        결과[시리즈.키] = df.sort_values("trade_date").reset_index(drop=True)
    return 결과
