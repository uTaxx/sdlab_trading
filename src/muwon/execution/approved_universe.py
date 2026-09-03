"""승인 매매가 **무엇을 살펴볼지** 정한다.

## 규칙 하나가 전부다

    유니버스 = 오늘 승인된 종목  ∪  지금 들고 있는 종목

**승인은 사는 것에만 걸린다.** 들고 있는 종목의 손절·청산은 승인 여부와
관계없이 늘 작동해야 한다.

엔진은 유니버스에 있는 종목만 본다. 그래서 보유 종목을 안 넣으면 엔진이
그 종목을 아예 안 보고 **손절이 조용히 멈춘다.** 화면에는 "체결 없음"으로만
보이고, 손절이 안 걸렸다는 사실은 값이 더 빠진 뒤에야 드러난다.

이 저장소에서 제일 위험한 실수의 모양이라 따로 떼어 시험한다.
"""

from __future__ import annotations

from collections.abc import Iterable

from muwon.data.universe import UNIVERSE, Ticker


def _야후(symbol: str, market: str) -> str:
    """코스닥에 `.KS`를 붙이면 시세가 통째로 빈다. 그러면 그 종목은 조용히
    빠지고, 보유 종목이었다면 손절이 안 걸린다."""
    return symbol + (".KS" if market == "KOSPI" else ".KQ")


def to_ticker(symbol: str, 섹터들: Iterable, 이름: str = "") -> tuple[Ticker, str]:
    """종목코드 → (티커, 어디서 찾았나).

    찾은 자리를 같이 돌려주는 이유는, **아무 데서도 못 찾아 코스피로
    가정한 경우를 부르는 쪽이 알아야** 하기 때문이다. 조용히 가정하면
    코스닥 종목의 시세가 빈 채로 넘어간다."""
    for s in 섹터들:
        for m in s.종목:
            if m.symbol == symbol:
                return Ticker(m.symbol, m.name, m.market, _야후(m.symbol, m.market)), "시트"
    for t in UNIVERSE:
        if t.symbol == symbol:
            return t, "기본목록"
    return Ticker(symbol, 이름 or symbol, "KOSPI", _야후(symbol, "KOSPI")), "가정"


def build_universe(
    승인된심볼: Iterable[str],
    보유심볼: Iterable[str],
    섹터들: Iterable,
    이름표: dict[str, str] | None = None,
) -> tuple[list[Ticker], list[str]]:
    """(살펴볼 티커 목록, 어디서도 못 찾아 가정한 종목들).

    승인된 것을 앞에, 보유 중인 것을 뒤에 둔다. 순서가 성적을 바꾸지는
    않지만 로그를 읽을 때 승인분이 먼저 보이는 편이 낫다."""
    이름표 = 이름표 or {}
    차례 = list(dict.fromkeys(list(승인된심볼) + sorted(보유심볼)))
    티커들, 가정한것 = [], []
    for symbol in 차례:
        t, 어디 = to_ticker(symbol, 섹터들, 이름표.get(symbol, ""))
        티커들.append(t)
        if 어디 == "가정":
            가정한것.append(symbol)
    return 티커들, 가정한것


def 섹터표만들기(섹터들: Iterable) -> dict[str, str]:
    """종목코드 → 섹터 이름.

    09:05 매수에서 섹터당 보유 상한을 세려면 이것이 있어야 한다. 08:30에서
    쓰는 `sector/selection.cap_per_sector`는 후보 목록을 0부터 세기 때문에
    **이미 들고 있는 종목을 모른다.** 반도체를 두 종목 들고 있어도 그날
    후보에 반도체 세 종목이 들어올 수 있었다.

    활성이 꺼진 종목도 넣는다. 지금은 안 사지만 예전에 사서 들고 있을 수
    있고, 들고 있는 것은 상한을 셀 때 세야 한다."""
    표: dict[str, str] = {}
    for s in 섹터들:
        for m in getattr(s, "종목", []):
            표[m.symbol] = s.이름
    return 표
