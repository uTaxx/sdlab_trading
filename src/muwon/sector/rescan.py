"""섹터 안에서 거래대금으로 다시 줄을 세워, 실거래 목록의 편입·제외 후보를 찾는다.

2026-09-06에 사람이 손으로 한 번 계산해서 지금의 39종목을 골랐다
(`catalog.py` 참고). 그때 쓴 규칙은 하나다.

    섹터마다 거래대금 상위 5종목, 최근 20거래일 평균 거래대금이 250억 이상인 것만.

이 모듈은 **같은 규칙을 다시 계산**해서 지금 시트(실거래가 실제로 읽는
종목 목록, `sector_sheet.read()`)와 견준다.

## 새 회사를 찾아내지는 않는다

시트에 이미 올라간 종목(활성이든 아니든)만 다시 순위를 매긴다. 아직 시트에
한 번도 안 올라간 회사가 새로 이 섹터에 들어와야 하는지는 이 모듈이 답할
수 없다. 그러려면 섹터별 종목 전체를 스크리닝하는 자료가 따로 필요하다.

## 네트워크 없이 시험한다

거래대금을 실제로 받아 오는 것은 이 모듈 밖(스크립트)의 일이다. 여기는
이미 잰 거래대금 표를 받아 순위만 매긴다. `sector_sheet.parse()`와 같은
이유다. 순위 규칙을 시험하려고 매번 시세를 받을 수는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from muwon.cloud.sector_sheet import SheetContents, 섹터머리, 종목머리
from muwon.sector.catalog import Sector

#: 섹터마다 최대 몇 종목까지 활성으로 두나.
섹터당상한 = 5

#: 최근 20거래일 평균 거래대금 문턱 (억원).
우량주기준 = 250.0

#: 거래대금을 평균 내는 창(거래일).
거래대금창 = 20


@dataclass(frozen=True)
class 종목평가:
    symbol: str
    name: str
    #: 억원. 시세를 못 받았으면 None이다. 0으로 채우지 않는다. 0은
    #: "거래대금이 없다"는 뜻이라 못 받은 것과 다르다.
    거래대금: float | None
    지금활성: bool


@dataclass(frozen=True)
class 섹터재평가:
    섹터코드: str
    섹터이름: str
    평가: list[종목평가]
    #: 규칙대로 다시 뽑은 활성 종목 코드.
    새활성: frozenset[str]

    @property
    def 편입(self) -> list[종목평가]:
        return [e for e in self.평가 if e.symbol in self.새활성 and not e.지금활성]

    @property
    def 제외(self) -> list[종목평가]:
        return [
            e for e in self.평가
            if e.symbol not in self.새활성 and e.지금활성 and e.거래대금 is not None
        ]

    @property
    def 확인못함(self) -> list[종목평가]:
        return [e for e in self.평가 if e.거래대금 is None]

    @property
    def 바뀐것있음(self) -> bool:
        return bool(self.편입 or self.제외)


def 다시고르기(
    sector: Sector,
    거래대금표: dict[str, float | None],
    섹터당: int = 섹터당상한,
    문턱: float = 우량주기준,
) -> 섹터재평가:
    """한 섹터 안에서 거래대금으로 다시 순위를 매긴다.

    **거래대금을 못 받은 종목은 지금 상태를 그대로 지킨다.** 못 받은 것을
    0으로 놓고 순위에서 빼면, 일시적인 조회 실패로 멀쩡한 종목이 제외
    후보에 오른다."""
    평가 = [
        종목평가(symbol=m.symbol, name=m.name, 거래대금=거래대금표.get(m.symbol), 지금활성=m.활성)
        for m in sector.종목
    ]
    잰것 = [e for e in 평가 if e.거래대금 is not None]
    순위 = sorted(잰것, key=lambda e: e.거래대금, reverse=True)
    새활성 = {e.symbol for e in 순위[:섹터당] if e.거래대금 >= 문턱}
    새활성 |= {e.symbol for e in 평가 if e.거래대금 is None and e.지금활성}
    return 섹터재평가(
        섹터코드=sector.코드, 섹터이름=sector.이름, 평가=평가, 새활성=frozenset(새활성)
    )


def 전체재평가(내용: SheetContents, 거래대금표: dict[str, float | None]) -> list[섹터재평가]:
    """활성 섹터마다 `다시고르기`를 부른다. 꺼 둔 섹터는 그대로 둔다."""
    return [다시고르기(s, 거래대금표) for s in 내용.섹터 if s.활성]


def _멈춤사유(평가: 종목평가, 새활성: bool, 섹터당: int, 문턱: float) -> str:
    if 평가.거래대금 is None:
        return 평가.symbol  # 안 불릴 자리다. 방어용.
    if 새활성:
        return f"거래대금 {평가.거래대금:.0f}억으로 다시 활성화"
    if 평가.거래대금 < 문턱:
        return f"거래대금 {평가.거래대금:.0f}억: 우량주 기준({문턱:.0f}억) 미달"
    return f"거래대금 {평가.거래대금:.0f}억으로 섹터당 {섹터당}종목 상한에서 밀림"


def 반영할행(
    내용: SheetContents,
    재평가들: list[섹터재평가],
    섹터당: int = 섹터당상한,
    문턱: float = 우량주기준,
) -> tuple[list[list[str]], list[list[str]]]:
    """지금 시트 내용에 재평가 결과를 입혀 `sector_sheet.write_catalog`에
    바로 넣을 수 있는 행을 만든다.

    **바뀐 종목만 활성·메모를 고친다.** 재평가하지 않은 섹터(꺼 둔 섹터)와
    바뀌지 않은 종목은 시트에 있던 값 그대로 남는다. 사람이 적어 둔 메모를
    이유 없이 지우지 않기 위해서다."""
    재평가표 = {r.섹터코드: r for r in 재평가들}

    섹터행 = [섹터머리]
    종목행 = [종목머리]
    for s in 내용.섹터:
        섹터행.append(
            [s.코드, s.이름, "Y" if s.활성 else "N", f"{s.비중상한:g}", s.전망출처, s.성격, ""]
        )
        재평가 = 재평가표.get(s.코드)
        평가표 = {e.symbol: e for e in 재평가.평가} if 재평가 else {}
        for m in s.종목:
            활성, 메모 = m.활성, m.메모
            평가 = 평가표.get(m.symbol)
            if 평가 is not None and 평가.거래대금 is not None:
                새활성 = m.symbol in 재평가.새활성
                if 새활성 != m.활성:
                    활성 = 새활성
                    메모 = _멈춤사유(평가, 새활성, 섹터당, 문턱)
            종목행.append([m.symbol, m.name, m.market, s.코드, "Y" if 활성 else "N", 메모])
    return 섹터행, 종목행
