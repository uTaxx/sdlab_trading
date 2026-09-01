"""섹터 지수를 우리가 직접 만든다.

## 왜 직접 만드나

KRX 업종 분류는 우리가 보는 방식과 다르다. 2차전지가 화학·금속·전기전자에
흩어져 있다. 그리고 외부 업종지수에 기대면 그 데이터가 끊길 때 시스템이
멈춘다.

**섹터 = 우리가 고른 종목의 묶음이고, 지수도 그 묶음으로 만든다.**

## 동일가중으로 만든다

시가총액 가중으로 하면 **삼성전자 하나가 반도체 섹터 전체가 된다**
(반도체 8종목 중 삼성전자·SK하이닉스의 거래대금이 나머지 여섯의 40배다).
그러면 '섹터 분위기'가 아니라 '대장주 주가'를 재는 것이다.

동일가중은 작은 종목에 과한 무게를 주는 단점이 있지만, **우리가 알고 싶은
것이 "이 업종 전반이 도는가"**라서 이쪽이 맞다.

## 그 시점에 있던 종목만 쓴다

LG에너지솔루션은 2022년 상장이다. 2차전지 지수를 오늘 종목 그대로
2015년까지 늘리면, **그때 존재하지도 않던 회사가 그 시절 지수에 들어간다.**

그래서 날짜마다 **그날 시세가 있는 종목만으로** 그날의 수익률을 낸다.
종목 수가 날짜마다 달라지고, **최소 3종목이 없으면 그날 값을 안 낸다**.
두 종목의 평균은 '섹터'가 아니라 그냥 두 종목이다.

## 생존편향은 못 없앤다. 그래서 어떻게 쓰나

오늘 살아남은 종목으로 과거를 만들면 **과거 수익률이 실제보다 좋게 나온다.**
망한 회사가 목록에 없기 때문이다.

**그래서 섹터 지수의 절대 수익률은 믿지 않는다.** 대신 형태만 쓴다.
추세, 변동성, 시장 대비 강도. 이것들은 "얼마나 벌었나"가 아니라
"어떤 모양으로 움직였나"라서 편향에 덜 민감하다.
"""

from __future__ import annotations

import pandas as pd

#: 이만큼 종목이 없으면 그날 섹터 값을 내지 않는다.
MIN_MEMBERS = 3


def build_index(histories: dict[str, pd.DataFrame], base: float = 100.0) -> pd.DataFrame:
    """종목별 일봉 → 섹터 지수(동일가중).

    돌려주는 표: trade_date 색인, `close`(지수)와 `members`(그날 쓴 종목 수).

    **수익률을 먼저 평균 내고 그걸 이어 붙인다.** 가격을 평균 내면 비싼
    종목이 지수를 지배하는데, 그건 시가총액 가중과 같은 문제다."""
    수익률들 = []
    for symbol, df in histories.items():
        if df is None or len(df) < 2:
            continue
        s = df.sort_values("trade_date").set_index("trade_date")["close"].astype(float)
        s = s[s > 0]
        수익률들.append(s.pct_change().rename(symbol))
    if not 수익률들:
        return pd.DataFrame(columns=["close", "members"])

    표 = pd.concat(수익률들, axis=1).sort_index()
    # 그날 값이 있는 종목만 센다. 상장 전이면 NaN이라 자동으로 빠진다.
    종목수 = 표.notna().sum(axis=1)
    평균수익 = 표.mean(axis=1, skipna=True)

    쓸수있는날 = 종목수 >= MIN_MEMBERS
    평균수익 = 평균수익.where(쓸수있는날)

    # 처음 쓸 수 있는 날부터 시작한다. 그 전은 지수가 없다.
    #
    # 첫 줄의 수준(100 근처가 아닐 수 있다)은 뜻이 없다. 지수는 절대
    # 수준이 아니라 날짜 사이의 비율만 쓴다(추세·변동성·상대강도). 첫 줄을
    # 억지로 100에 맞추려면 그날의 실제 수익률을 버려야 하는데, 그게 더 나쁘다.
    시작 = 쓸수있는날.idxmax() if 쓸수있는날.any() else None
    if 시작 is None:
        return pd.DataFrame(columns=["close", "members"])

    구간 = 평균수익.loc[시작:].fillna(0.0)
    지수 = base * (1 + 구간).cumprod()
    return pd.DataFrame({"close": 지수, "members": 종목수.loc[시작:]})


def relative_strength(sector_close: pd.Series, market_close: pd.Series, window: int = 20):
    """시장 대비 강도: 섹터가 시장보다 얼마나 더(덜) 갔나.

    생존편향에 덜 민감하다. 섹터와 시장이 **같은 편향을 공유하지는 않지만**,
    적어도 "요즘 이 섹터가 시장을 이기고 있나"는 절대 수익률보다 안전한
    물음이다."""
    공통 = sector_close.index.intersection(market_close.index)
    s, m = sector_close.loc[공통], market_close.loc[공통]
    섹터변화 = s / s.shift(window) - 1
    시장변화 = m / m.shift(window) - 1
    return (섹터변화 - 시장변화).rename(f"상대강도_{window}")


def coverage_report(indexes: dict[str, pd.DataFrame]) -> str:
    """섹터마다 지수가 언제부터 있고 종목 수가 몇인지.

    **이걸 안 보면 "2차전지 전망"이 사실 4년치라는 걸 모른다.**"""
    lines = ["■ 섹터 지수 확보 상황", ""]
    lines.append(f"  {'섹터':<10}{'시작':>12}{'일수':>8}{'최근 종목수':>12}{'최소':>6}")
    for 코드, df in sorted(indexes.items()):
        if len(df) == 0:
            lines.append(f"  {코드:<10}{'없음':>12}")
            continue
        lines.append(
            f"  {코드:<10}{df.index[0]!s:>12}{len(df):>8}"
            f"{int(df['members'].iloc[-1]):>12}{int(df['members'].min()):>6}"
        )
    lines += [
        "",
        "  '시작'이 늦은 섹터는 그만큼 표본이 짧습니다. 비슷했던 과거를",
        "  찾을 때 구간이 적게 나오고, 그러면 전망을 못 냅니다.",
    ]
    return "\n".join(lines)
