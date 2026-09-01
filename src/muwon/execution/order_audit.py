"""**우리 주문 기록이 증권사 기록과 같은가**를 기간으로 대조한다.

## 왜 필요한가

이 저장소의 전략 평가 결과: 승률(이긴 거래의 비율), 손익비(이긴 거래의 평균 이익을
진 거래의 평균 손실로 나눈 값), 슬리피지(판단한 값과 실제로 사진 값의 차이)
: 는 전부 `orders` 표 위에서 계산된다. 그 표가 틀리면 **그 위의 숫자는 전부
틀리고, 틀렸다는 사실조차 안 보인다.**

실제로 틀렸다. 2026-08-25에 12주가 체결됐는데 기록에는 4주로 남았고, 같은 날
흉내 실행이 사지도 않은 두 종목을 기록에 써 넣었다. 둘 다 우리 기록만 봐서는
알 수 없었다. 증권사 기록과 나란히 놓아야 보인다.

`settle_fills`는 **그날 것**만 맞춘다. 그날 조회가 실패했거나 그날 이 도구가
아직 없었던 날은 영영 안 맞은 채 남는다. 여기는 기간으로 훑는다.

## 왜 "기간별 매매손익"(TTTC8715R)을 안 쓰나

한국투자증권에는 기간별 매매손익현황조회가 있고, 그게 있으면 증권사가 계산한
실현손익을 그대로 받아 우리 것과 맞대 볼 수 있다. **모의투자 계좌에는 없다.**
모의투자에서 부를 수 있는 국내주식 주문/계좌 API는 다섯 개뿐이다
(잔고조회·주문체결조회·매수가능조회·현금주문·정정취소).

그래서 한 겹 아래에서 대조한다. 증권사의 **주문·체결 원본**과 우리 기록을
맞춰 보는 것이다. 손익은 그 위에서 계산되는 것이므로, 원본이 같으면 손익도
같다. 오히려 어디가 어긋났는지가 더 잘 보인다.

## 무엇을 못 하나

- **취소·미체결 주문은 대조 대상이 아니다.** 체결이 0이면 보유가 안 생기고
  손익에도 안 들어간다. 우리 기록에 없는 것이 맞다.
- **주문번호가 없는 우리 기록은 짝을 못 찾는다.** 흉내 실행(SimulatedOrderExecutor)이
  남긴 것들이다. 없는 것이 아니라 **모르는 것**이라 따로 센다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from muwon.domain.types import OrderSide


def _번호(무엇) -> str:
    """주문번호를 맞대 볼 수 있는 꼴로. KIS는 앞에 0을 채워 준다."""
    return str(무엇 or "").strip().lstrip("0")


@dataclass(frozen=True)
class 증권사주문:
    """증권사가 "이런 주문이 있었다"고 말하는 한 건."""

    order_id: str
    symbol: str
    name: str
    side: OrderSide
    ordered_quantity: int
    filled_quantity: int
    avg_price: float
    ord_dt: str
    cancelled: bool = False

    @property
    def 체결됐나(self) -> bool:
        return self.filled_quantity > 0

    @property
    def 한줄(self) -> str:
        방향 = "매수" if self.side == OrderSide.BUY else "매도"
        날 = f"{self.ord_dt[:4]}-{self.ord_dt[4:6]}-{self.ord_dt[6:8]}" if len(self.ord_dt) == 8 else self.ord_dt
        이름 = self.name or self.symbol
        return f"{날} {이름}({self.symbol}) {방향} {self.filled_quantity}주 @ {self.avg_price:,.0f}원"


@dataclass(frozen=True)
class 놓친체결:
    """증권사엔 체결이 있는데 우리 주문 기록엔 없는 한 건.

    **"손절이 안 걸린다"고 뭉뚱그리면 안 된다.** 손절은 보유(positions)를
    보고 걸리지 주문 기록을 보고 걸리지 않는다. 주문 기록에만 없고 보유로는
    알고 있는 종목이라면 위험은 없고 **전략 평가 결과에서만 빠진다.**

    2026-08-25 첫 대조에서 실제로 그랬다. HPSP 2주가 주문 기록에 없었지만
    보유로는 알고 있어서 손절은 걸려 있었다. 세 갈래를 안 가르면 멀쩡한 것을
    보고 놀라고, 정작 위험한 것과 구별을 못 한다."""

    주문: 증권사주문
    #: 우리가 이 종목을 보유로는 알고 있나.
    보유중: bool
    #: 이 기간 안에서 같은 종목의 매수·매도가 상쇄되나(사고 판 흔적).
    닫힘: bool

    @property
    def 위험한가(self) -> bool:
        """보유로도 모르고 닫히지도 않았다. 아무도 안 지키는 주식이다."""
        return not self.보유중 and not self.닫힘

    @property
    def 뜻(self) -> str:
        if self.위험한가:
            return "보유로도 모릅니다. **이 주식에는 손절이 안 걸립니다**"
        if self.닫힘:
            return "이 기간에 사고 판 흔적입니다. 지금 보유는 아니고 전략 평가 결과에서만 빠집니다"
        return "보유로는 알고 있어 손절은 걸립니다. 전략 평가(승률, 손익비, 슬리피지)에서만 빠집니다"


@dataclass(frozen=True)
class 우리주문:
    """우리 `orders` 표의 한 줄."""

    row_id: int
    order_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    when: datetime
    reference_price: float | None = None
    fill_confirmed: bool | None = None

    @property
    def 한줄(self) -> str:
        방향 = "매수" if self.side == "BUY" else "매도"
        return f"{self.when:%Y-%m-%d %H:%M} {self.symbol} {방향} {self.quantity}주 @ {self.price:,.0f}원"


@dataclass(frozen=True)
class 어긋난짝:
    우리: 우리주문
    증권사: 증권사주문

    @property
    def 수량다름(self) -> bool:
        return self.우리.quantity != self.증권사.filled_quantity

    @property
    def 체결가다름(self) -> bool:
        # 1원 미만 차이는 반올림이다. 그걸로 기록을 고치면 잡음만 남는다.
        return abs(self.우리.price - self.증권사.avg_price) >= 1.0

    @property
    def 바뀌나(self) -> bool:
        return self.수량다름 or self.체결가다름

    @property
    def 한줄(self) -> str:
        조각 = []
        if self.수량다름:
            조각.append(f"수량 {self.우리.quantity}주 → {self.증권사.filled_quantity}주")
        if self.체결가다름:
            조각.append(f"체결가 {self.우리.price:,.0f}원 → {self.증권사.avg_price:,.0f}원")
        return f"{self.우리.symbol} [주문 {self.우리.order_id}] " + " · ".join(조각)


@dataclass(frozen=True)
class 대조:
    #: 양쪽이 같은 것. 손댈 것 없다.
    맞음: list[어긋난짝] = field(default_factory=list)
    #: 양쪽에 있는데 수량이나 체결가가 다른 것. 증권사가 사실이다.
    어긋남: list[어긋난짝] = field(default_factory=list)
    #: 우리 기록에만 있는 것. 유령이다. 그 주식은 실제로 없다.
    우리만: list[우리주문] = field(default_factory=list)
    #: 증권사에만 있는 체결. 우리 주문 기록이 놓친 것들.
    #: 손절이 걸리는지는 보유를 봐야 알 수 있어서 놓친체결이 따로 가른다.
    증권사만: list[놓친체결] = field(default_factory=list)
    #: 주문번호가 없어 짝을 못 찾은 우리 기록(흉내 실행 등).
    대조불가: list[우리주문] = field(default_factory=list)
    #: 체결이 0이라 대조 대상이 아니었던 증권사 주문 수(취소·미체결).
    체결없음: int = 0

    @property
    def 문제있나(self) -> bool:
        return bool(self.어긋남 or self.우리만 or self.증권사만)

    @property
    def 위험한것(self) -> list[놓친체결]:
        """보유로도 모르는 체결: 아무도 안 지키는 주식이다."""
        return [ㄴ for ㄴ in self.증권사만 if ㄴ.위험한가]

    @property
    def 대조한건수(self) -> int:
        return len(self.맞음) + len(self.어긋남)


def 파싱(rows) -> list[증권사주문]:
    """주문체결조회 원본 행들을 증권사주문으로.

    필드명은 한국투자증권 공식 예제의 COLUMN_MAPPING과 대조했다:
    odno=주문번호, pdno=상품번호, ord_qty=주문수량, tot_ccld_qty=총체결수량,
    avg_prvs=평균가, sll_buy_dvsn_cd=매도매수구분코드(01 매도/02 매수)."""
    나온것 = []
    for row in rows or []:
        order_id = str(row.get("odno", "")).strip()
        if not _번호(order_id):
            continue
        나온것.append(
            증권사주문(
                order_id=order_id,
                symbol=str(row.get("pdno", "")).strip(),
                name=str(row.get("prdt_name", "")).strip(),
                side=OrderSide.SELL if str(row.get("sll_buy_dvsn_cd", "")) == "01" else OrderSide.BUY,
                ordered_quantity=int(float(row.get("ord_qty") or 0)),
                filled_quantity=int(float(row.get("tot_ccld_qty") or 0)),
                avg_price=float(row.get("avg_prvs") or 0),
                ord_dt=str(row.get("ord_dt", "")).strip(),
                cancelled=str(row.get("cncl_yn", "")).upper() == "Y",
            )
        )
    return 나온것


def 대조하기(
    우리것: list[우리주문],
    증권사것: list[증권사주문],
    보유종목: frozenset[str] | set[str] = frozenset(),
) -> 대조:
    """주문번호로 짝을 지어 어디가 어긋났는지 가른다.

    **증권사가 사실이다.** 우리 기록이 다르면 우리 것이 틀린 것이지, 증권사에
    따질 일이 아니다. 그래서 '어긋남'의 방향은 언제나 우리 → 증권사다.

    `보유종목`을 주면 "우리 주문 기록엔 없는 체결"을 세 갈래로 가른다.
    보유로는 아는 것 / 이 기간에 사고 판 것 / 아무도 모르는 것. 안 가르면
    멀쩡한 것을 보고 놀라고, 정작 위험한 것과 구별을 못 한다."""
    체결된것 = {_번호(o.order_id): o for o in 증권사것 if o.체결됐나}
    체결없음 = sum(1 for o in 증권사것 if not o.체결됐나)

    맞음: list[어긋난짝] = []
    어긋남: list[어긋난짝] = []
    우리만: list[우리주문] = []
    대조불가: list[우리주문] = []
    짝지은번호: set[str] = set()

    for 우리 in 우리것:
        번호 = _번호(우리.order_id)
        if not 번호:
            대조불가.append(우리)
            continue
        상대 = 체결된것.get(번호)
        if 상대 is None:
            우리만.append(우리)
            continue
        짝지은번호.add(번호)
        짝 = 어긋난짝(우리=우리, 증권사=상대)
        (어긋남 if 짝.바뀌나 else 맞음).append(짝)

    놓친것 = sorted(
        (o for 번호, o in 체결된것.items() if 번호 not in 짝지은번호),
        key=lambda o: (o.ord_dt, o.symbol),
    )

    # 놓친 것들 안에서 같은 종목의 매수·매도가 상쇄되면 사고 판 흔적이다.
    # (verify_kis_order가 주문 경로를 확인하려고 1주 샀다 파는 것이 그 예다.)
    순수량: dict[str, int] = {}
    for o in 놓친것:
        부호 = 1 if o.side == OrderSide.BUY else -1
        순수량[o.symbol] = 순수량.get(o.symbol, 0) + 부호 * o.filled_quantity

    증권사만 = [
        놓친체결(
            주문=o,
            보유중=o.symbol in 보유종목,
            닫힘=순수량.get(o.symbol, 0) == 0,
        )
        for o in 놓친것
    ]

    return 대조(
        맞음=맞음,
        어긋남=어긋남,
        우리만=우리만,
        증권사만=증권사만,
        대조불가=대조불가,
        체결없음=체결없음,
    )
