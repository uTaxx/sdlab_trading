"""매매 대상 종목이 최근에 어떻게 움직였나.

## 무엇을 재나

구간마다(1주·1개월·3개월) 종목별 등락률을 내고, 유니버스 전체를 한 줄로
요약한다. 요약은 셋이다.

- 오른 종목 비율: 100%면 전부 올랐다. 50%면 반반이다.
- 등락률 중앙값: 평균이 아니라 중앙값이다. 한 종목이 200% 오르면 평균은
  그 종목 이야기가 된다.
- 20일 이동평균선 위 비율: 값이 이미 오른 뒤에야 올라가는 수준 지표다.
  방향이 아니라 지금 어디쯤인지를 말한다.

## 이 값으로 전략을 고르지 않는다

여기는 숫자를 내는 자리이지 판단하는 자리가 아니다. 판단은
`analysis/strategy_fit.py`가 하고, 그것도 이 값이 아니라 **구간마다 전략을
실제로 계산한 결과**로 한다.

시장 국면을 이름으로 부르고(강세/약세) 그 이름에 전략을 붙이는 방식은
2026-08-18에 재 보고 기각했다. 국면 판정이 하락장 중간의 반등을 강세로
불렀고, 2022년 손실 50건 중 49건이 그렇게 강세로 판정된 날의 진입에서 났다
(설계안 9장). 그래서 이 파일은 국면 이름을 만들지 않는다. 숫자만 낸다.

## 20일선 위 비율은 무엇이 아닌가

이 값이 높다고 사기 좋은 때가 아니다. 반등 꼭지에서 가장 높게 나온다.
화면에 적을 때 "지금 오르는 중"으로 읽히지 않게 써야 한다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

#: 유니버스 요약에 쓰는 이동평균선 길이(거래일).
BREADTH_MA = 20

#: 구간 요약을 낼 때 이만큼은 종목이 있어야 한다. 그 아래면 몇 종목의
#: 이야기라서 유니버스 요약이라고 부를 수 없다.
MIN_SYMBOLS = 5


@dataclass(frozen=True)
class 종목움직임:
    """한 종목이 한 구간에서 어떻게 움직였나."""

    symbol: str
    name: str
    등락: float
    시작가: float
    끝가: float

    @property
    def 올랐나(self) -> bool:
        return self.등락 > 0


@dataclass(frozen=True)
class 구간트렌드:
    """한 구간의 유니버스 요약.

    `못잰것`이 비어 있지 않으면 그 종목들은 시세가 모자라 빠진 것이다.
    빼고 계산한 숫자를 전체 숫자처럼 읽으면 안 되므로 같이 들고 다닌다."""

    구간: str
    시작: date
    끝: date
    종목들: list[종목움직임] = field(default_factory=list)
    #: 이 구간을 계산할 만큼 시세가 없던 종목들.
    못잰것: list[str] = field(default_factory=list)
    #: 마지막 날 종가가 20일 이동평균선 위였던 종목 비율(%). 못 재면 None.
    이평위비율: float | None = None

    @property
    def 종목수(self) -> int:
        return len(self.종목들)

    @property
    def 오른수(self) -> int:
        return sum(1 for ㄱ in self.종목들 if ㄱ.올랐나)

    @property
    def 오른비율(self) -> float | None:
        """올라간 종목이 몇 %인가. 종목이 모자라면 None."""
        if self.종목수 < MIN_SYMBOLS:
            return None
        return self.오른수 / self.종목수 * 100

    @property
    def 중앙값등락(self) -> float | None:
        """평균이 아니라 중앙값이다. 한 종목이 크게 오르면 평균은 그 종목
        이야기가 되고, 유니버스가 어땠는지는 안 보인다."""
        if self.종목수 < MIN_SYMBOLS:
            return None
        return statistics.median(ㄱ.등락 for ㄱ in self.종목들)

    @property
    def 믿을만한가(self) -> bool:
        return self.종목수 >= MIN_SYMBOLS

    def 오른것(self, 몇개: int = 3) -> list[종목움직임]:
        return sorted(self.종목들, key=lambda ㄱ: -ㄱ.등락)[:몇개]

    def 빠진것(self, 몇개: int = 3) -> list[종목움직임]:
        return sorted(self.종목들, key=lambda ㄱ: ㄱ.등락)[:몇개]


def _구간등락(df: pd.DataFrame, 시작: date, 끝: date) -> tuple[float, float, float] | None:
    """(등락률%, 시작가, 끝가). 구간 안에 봉이 둘 미만이면 None.

    시작가는 **구간 시작일 이후 첫 종가**다. 구간 시작일 종가를 쓰면 그날
    휴장이거나 상장 전이면 값이 없다."""
    if df is None or df.empty:
        return None
    안 = df[(df["trade_date"] >= 시작) & (df["trade_date"] <= 끝)]
    if len(안) < 2:
        return None
    처음 = float(안["close"].iloc[0])
    마지막 = float(안["close"].iloc[-1])
    if 처음 <= 0:
        return None
    return (마지막 / 처음 - 1) * 100, 처음, 마지막


def _이평위(df: pd.DataFrame, 끝: date, window: int = BREADTH_MA) -> bool | None:
    """마지막 종가가 이동평균선 위인가. 봉이 모자라면 None.

    **0으로 채우지 않는다.** 이동평균이 아직 안 만들어진 종목을 '선 아래'로
    세면 비율이 실제보다 낮게 나온다."""
    if df is None or df.empty:
        return None
    안 = df[df["trade_date"] <= 끝]
    if len(안) < window:
        return None
    종가 = 안["close"].astype(float)
    선 = float(종가.tail(window).mean())
    return float(종가.iloc[-1]) > 선


def 구간재기(
    histories: dict[str, pd.DataFrame],
    시작: date,
    끝: date,
    구간이름: str,
    이름표: dict[str, str] | None = None,
) -> 구간트렌드:
    """한 구간을 유니버스 전체에 대해 잰다."""
    이름표 = 이름표 or {}
    종목들: list[종목움직임] = []
    못잰것: list[str] = []
    위 = 0
    잰것 = 0

    for symbol in sorted(histories):
        df = histories[symbol]
        결과 = _구간등락(df, 시작, 끝)
        if 결과 is None:
            못잰것.append(symbol)
        else:
            등락, 처음, 마지막 = 결과
            종목들.append(
                종목움직임(
                    symbol=symbol,
                    name=이름표.get(symbol, symbol),
                    등락=round(등락, 2),
                    시작가=처음,
                    끝가=마지막,
                )
            )
        위인가 = _이평위(df, 끝)
        if 위인가 is not None:
            잰것 += 1
            위 += int(위인가)

    return 구간트렌드(
        구간=구간이름,
        시작=시작,
        끝=끝,
        종목들=종목들,
        못잰것=못잰것,
        이평위비율=(위 / 잰것 * 100) if 잰것 >= MIN_SYMBOLS else None,
    )


def 트렌드재기(
    histories: dict[str, pd.DataFrame],
    구간들: dict[str, tuple[date, date]],
    이름표: dict[str, str] | None = None,
) -> dict[str, 구간트렌드]:
    """구간 여럿을 한 번에. 열쇠는 구간 이름이다.

    구간 경계는 밖에서 정해 넣는다. 이 파일이 `기간정의`를 알면 `period_check`
    와 서로를 부르게 되고, 그러면 어느 쪽이 구간을 정하는지 알 수 없어진다."""
    return {
        이름: 구간재기(histories, 시작, 끝, 이름, 이름표)
        for 이름, (시작, 끝) in 구간들.items()
    }


def 트렌드글(트렌드: 구간트렌드) -> str:
    """한 구간을 한 문장으로. 화면과 알림이 같은 문장을 쓴다.

    **판단하지 않는다.** 오른 종목이 많다고 사기 좋은 때라고 적지 않는다.
    그 판단은 전략 계산 결과가 하고, 이 문장은 무슨 일이 있었는지만 적는다."""
    if not 트렌드.믿을만한가:
        return f"{트렌드.구간}: 계산된 종목이 {트렌드.종목수}개뿐이라 요약하지 않습니다."

    중앙 = 트렌드.중앙값등락
    비율 = 트렌드.오른비율
    말 = [
        (
            f"{트렌드.구간}: 종목 {트렌드.종목수}개 중 {트렌드.오른수}개 상승"
            f"({비율:.0f}%), 등락률 중앙값 {중앙:+.2f}%."
        )
    ]
    if 트렌드.이평위비율 is not None:
        말.append(
            f"{BREADTH_MA}일 이동평균선 위에 있는 종목은 "
            f"{트렌드.이평위비율:.0f}%입니다."
        )
    if 트렌드.못잰것:
        말.append(f"시세가 모자라 {len(트렌드.못잰것)}종목은 제외했습니다.")
    return " ".join(말)
