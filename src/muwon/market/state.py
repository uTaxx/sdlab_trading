"""오늘 장이 어떤 상태인가 — 숫자 몇 개로 적는다.

이 파일이 하는 일은 하나다. **날짜마다 "그날 장이 어땠는지"를 숫자
몇 개로 적어 두는 것.** 그래야 나중에 "지금과 비슷했던 날"을 찾을 수 있다.

## 무엇을 재나

| 지표 | 계산 | 무엇을 말하나 |
|---|---|---|
| `추세_20` | 지수 ÷ 20일 평균 − 1 | 단기 추세. +면 최근 오르는 중 |
| `추세_120` | 지수 ÷ 120일 평균 − 1 | 장기 추세 |
| `고점대비` | 지수 ÷ 최근 250일 최고 − 1 | 지금 얼마나 빠져 있나. 항상 0 이하 |
| `변동성` | 최근 20일 일간 등락률의 표준편차 | 조용한 장인가 요동치는 장인가 |

지수마다 이 넷을 낸다. 그리고 짝지어 보는 것 둘을 더한다.

| 지표 | 계산 | 무엇을 말하나 |
|---|---|---|
| `금구리비` | 금 ÷ 구리 | **위험 회피 신호.** 오르면 겁먹은 것, 내리면 경기를 믿는 것 |
| `금리수준` | 미 10년물 금리 그대로 | 오르면 성장주가 눌린다 |

## 반드시 지켜야 할 것 — 미래를 보지 않는다

"변동성이 평소보다 높다"를 말하려면 '평소'가 있어야 한다. 그런데
**30년 전체 평균을 쓰면 반칙이다** — 2005년에는 2020년 데이터를 모른다.

그래서 **그 시점까지의 과거로만** 표준화한다(rolling z-score). 3년(750
거래일)이 쌓이기 전에는 아예 값을 내지 않는다.

이걸 어기면 백테스트가 실제보다 좋게 나오고, 그 사실이 화면에 아무 표시도
안 남는다. 이 저장소는 이미 그런 사고(백테스트와 실거래가 다른 규칙)를
겪었다.

## z점수를 쓰는 이유

지표들의 단위가 제각각이다 — 추세는 %, 금리는 %p, 금구리비는 배수.
그대로 두고 "비슷한 날"을 찾으면 **숫자가 큰 지표가 전부를 결정한다.**

z점수는 "그동안의 평소에 견줘 몇 배쯤 벗어났나"로 바꾼 값이라 서로
견줄 수 있게 된다. 0이면 평소, +2면 평소보다 한참 위다.
"""

from __future__ import annotations

import pandas as pd

#: 표준화에 쓸 과거 창(거래일). 약 4년.
STD_WINDOW = 1000
#: 최소 이만큼 쌓이기 전에는 z점수를 내지 않는다. 약 3년.
MIN_HISTORY = 750
#: 고점을 어디까지 거슬러 볼 것인가. 약 1년.
PEAK_WINDOW = 250


def _추세(close: pd.Series, window: int) -> pd.Series:
    평균 = close.rolling(window, min_periods=window).mean()
    return close / 평균 - 1


def _고점대비(close: pd.Series, window: int = PEAK_WINDOW) -> pd.Series:
    고점 = close.rolling(window, min_periods=window // 2).max()
    return close / 고점 - 1


def _변동성(close: pd.Series, window: int = 20) -> pd.Series:
    수익률 = close.pct_change()
    return 수익률.rolling(window, min_periods=window).std()


def raw_indicators(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """날짜별 원시 지표. 표준화 전이라 단위가 제각각이다.

    시계열마다 거래일이 다르다(미국장·한국장·선물시장). **거래일 합집합에
    맞춰 앞의 값으로 채운다** — 미국 휴장일에 한국 장이 서면 전날 미국
    종가를 쓰는 것이 실제 상황과 같다."""
    frames = []
    for key, df in series.items():
        s = df.set_index("trade_date")["close"].astype(float)
        frames.append(s.rename(key))
    가격 = pd.concat(frames, axis=1).sort_index()
    # 앞 값으로 채운다. 뒤 값으로 채우면 미래를 보는 것이 된다.
    가격 = 가격.ffill()

    out = pd.DataFrame(index=가격.index)
    for key in 가격.columns:
        close = 가격[key]
        # 금리는 '가격'이 아니라 수준 자체가 뜻을 가진다. 추세·고점대비를
        # 매기면 "금리가 20일 평균보다 3% 높다" 같은 읽기 힘든 값이 된다.
        if key == "ust10y":
            out["금리수준"] = close
            out["금리_20일변화"] = close - close.shift(20)
            continue
        out[f"{key}_추세20"] = _추세(close, 20)
        out[f"{key}_추세120"] = _추세(close, 120)
        out[f"{key}_고점대비"] = _고점대비(close)
        out[f"{key}_변동성"] = _변동성(close)

    if "gold" in 가격.columns and "copper" in 가격.columns:
        구리 = 가격["copper"]
        비 = 가격["gold"] / 구리.where(구리 > 0)
        out["금구리비"] = 비
        out["금구리비_추세60"] = _추세(비, 60)

    return out


def rolling_z(frame: pd.DataFrame, window: int = STD_WINDOW, min_periods: int = MIN_HISTORY):
    """**그 시점까지의 과거로만** 표준화한다.

    `shift(1)`이 핵심이다. 오늘 값을 오늘 평균에 넣어 표준화하면 오늘을
    보고 오늘을 재는 셈이라 미세하게 미래를 본다. 하루 밀어서, **어제까지의
    평균과 표준편차**로 오늘을 잰다."""
    과거 = frame.shift(1)
    평균 = 과거.rolling(window, min_periods=min_periods).mean()
    표준편차 = 과거.rolling(window, min_periods=min_periods).std()
    # `replace(0, pd.NA)`를 쓰면 자료형이 object로 바뀌어 아래 clip이 조용히
    # 아무 일도 안 한다(테스트가 이걸 잡았다). where로 숫자형을 지킨다.
    #
    # 표준편차가 0인 구간은 값이 한 번도 안 변한 구간이다. 그런 데서
    # "평소보다 몇 배 벗어났나"는 뜻이 없으므로 비워 둔다.
    z = (frame - 평균) / 표준편차.where(표준편차 > 0)
    # 극단값을 잘라 낸다. 2020년 3월 같은 날은 z가 10을 넘는데, 그대로 두면
    # 거리 계산에서 그 하루가 다른 모든 지표를 압도한다.
    return z.clip(-4, 4)


def build_state(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """원시 지표 → z점수. 이게 '그날 장 상태'다.

    한 칸이라도 비어 있는 날은 뺀다. 반쯤 채워진 상태로 '비슷한 날'을
    찾으면 무엇을 보고 비슷하다고 했는지 알 수 없게 된다."""
    원시 = raw_indicators(series)
    z = rolling_z(원시)
    return z.dropna(how="any")


#: 원래 단위가 비율(%)인 지표들. 나머지(금리·금구리비 등)는 그대로 쓴다.
_퍼센트지표 = ("추세20", "추세120", "고점대비", "변동성", "추세60")


def describe_today(state: pd.DataFrame, raw: pd.DataFrame | None = None) -> str:
    """가장 최근 날의 상태를 사람 말로.

    **z점수만 찍으면 읽을 수가 없다.** z점수는 '평소에 견줘 얼마나 벗어났나'
    이지 값 자체가 아니라서, `고점대비 -2.80`을 보고 "고점에서 2.8% 빠졌다"로
    읽는 일이 실제로 있었다. 원래 값(고점에서 12.3% 아래)을 나란히 둔다.

    `raw`는 예전부터 받기만 하고 안 쓰던 자리다 — 이제 쓴다."""
    if len(state) == 0:
        return "상태를 잴 수 있는 날이 없습니다 — 표준화에 필요한 과거(3년)가 아직 안 쌓였습니다."
    오늘 = state.iloc[-1]
    날짜 = state.index[-1]

    원시오늘 = None
    if raw is not None and len(raw):
        같은날 = raw.loc[:날짜]
        if len(같은날):
            원시오늘 = 같은날.iloc[-1]

    lines = [
        f"■ {날짜} 장 상태",
        "  왼쪽이 실제 값, 오른쪽 z는 '평소에 견줘 얼마나 벗어났나'입니다.",
        "  z는 0이 평소, ±1이 흔한 범위, ±2면 몇 해에 한 번 볼까 말까 한 자리입니다.",
        "",
    ]
    for 이름, 값 in 오늘.items():
        표시 = "▁▂▃▄▅▆▇"[min(int((값 + 4) / 8 * 7), 6)]
        실제 = ""
        if 원시오늘 is not None and 이름 in 원시오늘.index:
            몫 = float(원시오늘[이름])
            if 몫 == 몫:  # noqa: PLR0124 — NaN이면 비운다
                실제 = (
                    f"{몫:>+8.2%}"
                    if any(이름.endswith(ㄲ) for ㄲ in _퍼센트지표)
                    else f"{몫:>+8.2f}"
                )
        lines.append(f"  {표시} {이름:<22}{실제:>10}   z {값:>+6.2f}")
    return "\n".join(lines)
