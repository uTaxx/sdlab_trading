"""섹터가 최근에 어떻게 움직였나. 화면의 '시장 트렌드' 탭이 읽는 숫자.

## 무엇을 재나

섹터마다 동일가중 지수를 만들고(`market/sector_index.build_index`), 그 지수의
최근 5거래일·20거래일·60거래일 등락률을 낸다. 같은 방식으로 코스피도 재서
한 줄로 같이 싣는다. 시장이 같이 빠진 날의 섹터 등락률만 보면 그 섹터가
나쁜 것처럼 읽히는데, 실제로는 전부 빠진 날일 수 있다.

## 왜 거래일로 세나

달력으로 세면 연휴가 낀 구간의 등락률이 다른 구간보다 짧은 기간을 재게
된다. 기사도 "최근 20거래일"처럼 적는다. 화면에도 거래일로 적어서, 다른
탭의 1주·1개월과 같은 말로 보이지 않게 한다. 그 둘은 달력 기준이라 여기와
같은 값이 아니다.

## 이 숫자로 매매하지 않는다

섹터 강도로 종목을 거르는 것은 2026-08-19에 재 보고 기각했다
(`docs/섹터선정_검증.md`). 여섯 조합 전부 우연의 폭 안이었다. 그래서 이
값은 화면에 보여 주기만 한다. 매수 대상을 정하는 데는 쓰지 않는다.

미국 섹터를 보고 국내 같은 섹터를 사는 규칙은 이것과 다른 이야기다
(설계안 §48~§51). 그쪽은 미국 ETF를 보고 판단하며 전략 안에 들어 있다.

## 못 잰 것을 0으로 채우지 않는다

상장한 지 얼마 안 됐거나 시세를 못 받으면 그 구간은 None이다. 0%로 채우면
"안 움직였다"로 읽히는데, 실제로는 "모른다"이다. 화면도 빈칸으로 그린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

#: 화면에 그릴 구간. (열쇠, 화면에 적을 이름, 거래일 수)
#:
#: 다른 탭의 1주·1개월·3개월은 달력 기준이라 여기와 값이 다르다. 그래서
#: 이름에 거래일을 박아 둔다. 같은 말로 보이면 두 표를 견주게 된다.
구간들: tuple[tuple[str, str, int], ...] = (
    ("d5", "5거래일", 5),
    ("d20", "20거래일", 20),
    ("d60", "60거래일", 60),
)

구간표: dict[str, tuple[str, int]] = {ㅋ: (이름, 날) for ㅋ, 이름, 날 in 구간들}

#: 시장(코스피)을 담는 줄의 코드. 섹터와 같은 표에 두되 구별할 수 있어야
#: 화면이 기준선으로 따로 그린다.
시장코드 = "KOSPI"
시장이름 = "코스피"

#: 지수를 만들려면 종목이 이만큼은 있어야 한다. 두세 종목짜리 지수는 그
#: 종목들의 이야기이지 섹터의 이야기가 아니다.
최소종목수 = 3

#: 20거래일 이동평균. 지금 어디쯤인지를 보는 값이지 방향이 아니다.
이평길이 = 20


@dataclass(frozen=True)
class 섹터움직임:
    """한 섹터의 최근 등락. 못 잰 구간은 None으로 남는다."""

    코드: str
    이름: str
    종목수: int
    등락: dict[str, float | None] = field(default_factory=dict)
    #: 60거래일 기준 시장 대비 초과 등락(%p). 시장 줄에서는 None이다.
    상대강도: float | None = None
    #: 마지막 종가가 20거래일 이동평균 위인가. 지수 기준이다.
    이평위: bool | None = None
    #: 시세가 모자라 지수에서 빠진 종목 이름들.
    못잰것: tuple[str, ...] = ()

    @property
    def 시장인가(self) -> bool:
        return self.코드 == 시장코드

    def 값(self, 구간열쇠: str) -> float | None:
        return self.등락.get(구간열쇠)


def _등락(지수: pd.Series, 거래일: int) -> float | None:
    """최근 N거래일 등락률(%). 봉이 모자라면 None."""
    if 지수 is None or len(지수) < 거래일 + 1:
        return None
    앞 = float(지수.iloc[-1 - 거래일])
    뒤 = float(지수.iloc[-1])
    if 앞 <= 0:
        return None
    return (뒤 / 앞 - 1) * 100.0


def _이평위(지수: pd.Series, 길이: int = 이평길이) -> bool | None:
    if 지수 is None or len(지수) < 길이:
        return None
    return float(지수.iloc[-1]) > float(지수.tail(길이).mean())


def _지수시리즈(표: pd.DataFrame) -> pd.Series | None:
    if 표 is None or len(표) == 0 or "close" not in 표:
        return None
    ㅅ = 표["close"].astype(float)
    ㅅ = ㅅ[ㅅ > 0]
    return ㅅ if len(ㅅ) else None


def 한줄(코드: str, 이름: str, 지수: pd.Series, 종목수: int,
       시장지수: pd.Series | None = None, 못잰것: tuple[str, ...] = ()) -> 섹터움직임:
    """지수 하나를 구간마다 재서 한 줄로 만든다."""
    등락 = {ㅋ: _등락(지수, 날) for ㅋ, _, 날 in 구간들}
    상대 = None
    if 시장지수 is not None:
        기준 = 구간들[-1]
        내것 = _등락(지수, 기준[2])
        시장것 = _등락(시장지수, 기준[2])
        if 내것 is not None and 시장것 is not None:
            상대 = 내것 - 시장것
    return 섹터움직임(
        코드=코드, 이름=이름, 종목수=종목수, 등락=등락,
        상대강도=상대, 이평위=_이평위(지수), 못잰것=tuple(못잰것),
    )


def 재기(섹터시세: dict, 이름표: dict[str, str],
       시장: pd.Series | None = None) -> list[섹터움직임]:
    """섹터마다 지수를 만들어 한 줄씩 낸다.

    `섹터시세`는 `{섹터코드: {종목코드: (종목, 일봉)}}`이다. 08:30 실행이
    이미 이 모양으로 들고 있다. 같은 자료를 두 번 받지 않으려고 그대로 받는다.

    시장을 주면 표 맨 앞에 코스피 줄을 하나 넣는다. 섹터만 보면 전부 빠진
    날에 어느 섹터가 나쁜 것처럼 읽힌다."""
    from muwon.market.sector_index import build_index

    나온것: list[섹터움직임] = []
    if 시장 is not None and len(시장):
        나온것.append(한줄(시장코드, 시장이름, 시장.astype(float), 종목수=1))

    for 코드, 모음 in 섹터시세.items():
        이름 = 이름표.get(코드, 코드)
        시세들 = {심볼: df for 심볼, (_, df) in 모음.items()}
        if len(시세들) < 최소종목수:
            # 지수를 안 만든다. **빈 줄로라도 남긴다.** 표에서 섹터가 통째로
            # 사라지면 "그 섹터는 안 움직였나"로 읽힌다.
            나온것.append(섹터움직임(코드=코드, 이름=이름, 종목수=len(시세들),
                                등락=dict.fromkeys(구간표, None)))
            continue
        지수 = _지수시리즈(build_index(시세들))
        if 지수 is None:
            나온것.append(섹터움직임(코드=코드, 이름=이름, 종목수=len(시세들),
                                등락=dict.fromkeys(구간표, None)))
            continue
        나온것.append(한줄(코드, 이름, 지수, 종목수=len(시세들), 시장지수=시장))
    return 나온것


#: 시트 `섹터트렌드` 탭의 머리. 화면과 n8n 연결이 이 순서를 읽는다.
머리 = [
    "열쇠", "잰때", "섹터코드", "섹터", "종목수",
    *[이름 + "%" for _, 이름, _ in 구간들],
    "상대강도%p", "20일선위", "못잰종목",
]


def _수(값: float | None, 자리: int = 2) -> str:
    """못 잰 값은 빈칸이다. 0으로 적으면 안 움직인 것과 섞인다."""
    return "" if 값 is None else f"{값:.{자리}f}"


def 줄들만들기(움직임들: list[섹터움직임], 잰때: date | str) -> list[list[str]]:
    """시트에 덧붙일 줄. 열쇠는 잰 날짜와 섹터 코드로 만든다."""
    날 = 잰때.isoformat() if isinstance(잰때, date) else str(잰때)
    나온것 = []
    for ㅁ in 움직임들:
        나온것.append([
            f"T{날}|{ㅁ.코드}",
            날,
            ㅁ.코드,
            ㅁ.이름,
            str(ㅁ.종목수),
            *[_수(ㅁ.값(ㅋ)) for ㅋ, _, _ in 구간들],
            _수(ㅁ.상대강도),
            "" if ㅁ.이평위 is None else ("예" if ㅁ.이평위 else "아니오"),
            ", ".join(ㅁ.못잰것),
        ])
    return 나온것


def 요약글(움직임들: list[섹터움직임], 구간열쇠: str = "d20") -> str:
    """한 줄 요약. 화면 맨 위와 로그에 같이 쓴다.

    **어느 섹터를 사라고 말하지 않는다.** 섹터 강도로 종목을 거르는 것은
    이미 재 보고 기각했다."""
    잰것 = [ㅁ for ㅁ in 움직임들 if not ㅁ.시장인가 and ㅁ.값(구간열쇠) is not None]
    이름 = 구간표.get(구간열쇠, (구간열쇠, 0))[0]
    if not 잰것:
        return f"{이름} 섹터 등락을 계산하지 못했습니다."
    오른것 = [ㅁ for ㅁ in 잰것 if ㅁ.값(구간열쇠) > 0]
    차례 = sorted(잰것, key=lambda ㅁ: -ㅁ.값(구간열쇠))
    말 = (f"{이름} 기준으로 {len(잰것)}개 섹터 중 {len(오른것)}개가 상승했습니다. "
          f"상승률이 가장 높은 섹터는 {차례[0].이름} {차례[0].값(구간열쇠):+.2f}%, "
          f"가장 낮은 섹터는 {차례[-1].이름} {차례[-1].값(구간열쇠):+.2f}%입니다.")
    시장 = next((ㅁ for ㅁ in 움직임들 if ㅁ.시장인가), None)
    if 시장 is not None and 시장.값(구간열쇠) is not None:
        말 += f" 같은 기간 코스피는 {시장.값(구간열쇠):+.2f}%입니다."
    return 말
