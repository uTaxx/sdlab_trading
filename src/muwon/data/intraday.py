"""장중 30분봉: 과거를 살 수 없으니 오늘부터 쌓는다.

왜 30분인가. 장중 모멘텀(Market Intraday Momentum)이 이 저장소가 조사한
단타 후보 중 **한국 시장 증거가 있는 유일한 것**이기 때문이다. 첫 30분
수익률이 마지막 30분 수익률을 예측한다는 주장이고, KOSPI 지수 30분봉
10년+에서 호가 스프레드를 빼고도 살아남았다(JRFM 15:523).

왜 지금 시작하는가. 한국투자증권 API는 **당일 분봉만** 준다. 과거 분봉은
받을 방법이 없다. 그래서 오늘 안 받으면 오늘치는 **영영 없다.** 이 파일이
있는 이유가 그것 하나다.

## 하루를 어떻게 자르나

한국 정규장은 09:00~15:30이다. 30분씩 자르면 13칸이 나오고, 칸 이름은
**끝나는 시각**으로 붙인다(09:00~09:30 → "0930").

끝 시각으로 부르는 이유는 논문이 그렇게 말하기 때문이다. "첫 30분
수익률"은 09:30까지의 값이고 "마지막 30분 수익률"은 15:30까지의 값이다.

## 알아 둘 것: 마지막 칸은 다르다

15:20~15:30은 **종가 단일가**(장 마감 전 10분간 주문만 받고 한 번에
체결) 구간이라 그 사이 분봉이 비어 있고 15:30에 한 방에 찍힌다. 그래서
마지막 칸은 봉 수가 적은 게 정상이다. 빠졌다고 보면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: 30분 칸의 끝 시각. 한국 정규장 09:00~15:30을 13칸으로 자른 것.
SLOT_ENDS: tuple[str, ...] = (
    "0930", "1000", "1030", "1100", "1130", "1200", "1230",
    "1300", "1330", "1400", "1430", "1500", "1530",
)

#: 장 시작. 09:00 정각 체결(시가 단일가)은 첫 칸에 넣는다.
MARKET_OPEN = "0900"


@dataclass(frozen=True)
class MinuteBar:
    """분봉 하나. KIS가 주는 그대로."""

    hhmm: str  # "0931"
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class SlotBar:
    """30분 칸 하나."""

    symbol: str
    trade_date: date
    slot: str  # 끝나는 시각 "0930"
    open: float
    high: float
    low: float
    close: float
    volume: int
    bars: int  # 이 칸을 만든 분봉 수: 적으면 그 칸을 의심해야 한다


def slot_for(hhmm: str) -> str | None:
    """분봉 하나가 어느 칸에 속하는지. 장 밖이면 None.

    09:00 정각(시가 단일가 체결)은 첫 칸에 넣는다. 빼면 그날 시가가
    사라진다. 첫 30분 수익률을 재려면 그게 있어야 한다."""
    if not (MARKET_OPEN <= hhmm <= SLOT_ENDS[-1]):
        return None
    for 끝 in SLOT_ENDS:
        if hhmm <= 끝:
            return 끝
    return None


def aggregate(symbol: str, trade_date: date, bars: list[MinuteBar]) -> list[SlotBar]:
    """분봉을 30분 칸으로 묶는다.

    **시간 순서를 여기서 정한다.** KIS는 최신 것부터 거꾸로 주고, 여러 번
    나눠 받으면 순서가 더 섞인다. 시가·종가는 순서가 틀리면 조용히 바뀌는
    값이라(고가·저가와 달리 티가 안 난다) 여기서 한 번만 정렬한다."""
    묶음: dict[str, list[MinuteBar]] = {}
    for bar in sorted(bars, key=lambda b: b.hhmm):
        칸 = slot_for(bar.hhmm)
        if 칸 is None:
            continue  # 시간외 거래: 정규장 이야기가 아니다
        묶음.setdefault(칸, []).append(bar)

    결과 = []
    for 칸 in SLOT_ENDS:
        속한것 = 묶음.get(칸)
        if not 속한것:
            continue  # 없는 칸은 만들지 않는다. 0으로 채우면 없던 거래가 생긴다
        결과.append(
            SlotBar(
                symbol=symbol,
                trade_date=trade_date,
                slot=칸,
                open=속한것[0].open,
                high=max(b.high for b in 속한것),
                low=min(b.low for b in 속한것),
                close=속한것[-1].close,
                volume=sum(b.volume for b in 속한것),
                bars=len(속한것),
            )
        )
    return 결과
