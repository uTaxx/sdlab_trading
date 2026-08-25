from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class PriceBar:
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Signal:
    symbol: str
    trade_date: date
    signal_type: SignalType
    strategy_name: str
    score: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class FillInfo:
    """주문이 실제로 얼마에·몇 주나 체결됐는지.

    OrderResult.price는 주문을 넣을 때 우리가 갖고 있던 기준가(직전 종가)일
    뿐 실제 체결가가 아니다 — 시장가 주문은 넣어봐야 얼마에 되는지 알 수
    있어서, 기준가만 기록하면 손익 집계에 오차가 계속 쌓인다. 체결 조회로
    받아온 진짜 값을 담는 자리다.

    filled_quantity가 주문 수량보다 적을 수 있다(부분 체결). 0이면 아직
    체결되지 않았거나 거부된 것이다."""

    order_id: str
    symbol: str
    ordered_quantity: int
    filled_quantity: int
    avg_fill_price: float
    #: 증권사가 준 종목명. 알림에 종목코드만 찍으면 사람은 무슨 주식인지
    #: 모른다 — '066970'을 보고 엘앤에프를 떠올릴 수 있는 사람은 없다.
    name: str = ""

    @property
    def is_fully_filled(self) -> bool:
        return self.filled_quantity >= self.ordered_quantity > 0

    @property
    def is_unfilled(self) -> bool:
        return self.filled_quantity == 0


@dataclass(frozen=True)
class OpenOrder:
    """아직 다 채워지지 않은 주문 하나 — 증권사가 들고 있는 미체결분.

    시장가 주문은 대개 즉시 다 체결되지만, 호가에 물량이 모자라면 일부만
    되고 나머지(잔여)가 장 마감까지 남는다. 남아 있는 동안에는 아직
    **되돌릴 수 있다** — 그게 이 자료형이 있는 이유다.

    취소 주문을 넣으려면 주문번호만으로는 모자라고, 증권사가 그 주문을
    받은 지점번호(branch_no)와 주문구분코드(ord_dvsn_cd)를 원주문과 똑같이
    되돌려줘야 한다. 그래서 조회 결과를 통째로 들고 다닌다."""

    order_id: str
    symbol: str
    name: str
    side: OrderSide
    ordered_quantity: int
    filled_quantity: int
    #: 아직 안 채워진 수량. 이만큼이 취소 대상이다.
    remaining: int
    #: 주문 단가. 시장가면 0으로 온다.
    price: float
    #: 주문구분코드(01=시장가, 00=지정가 …). 취소할 때 그대로 되돌려준다.
    ord_dvsn_cd: str
    #: 주문채번지점번호 = 취소 요청의 KRX_FWDG_ORD_ORGNO.
    branch_no: str
    #: 거래소ID구분코드(KRX/NXT/SOR). 비어 있으면 KRX로 본다.
    exchange: str = "KRX"
    #: 주문시각(HHMMSS). 사람에게 보여줄 때만 쓴다.
    ordered_at: str = ""

    @property
    def 한줄(self) -> str:
        방향 = "매수" if self.side == OrderSide.BUY else "매도"
        이름 = self.name or self.symbol
        때 = f" ({self.ordered_at[:2]}:{self.ordered_at[2:4]})" if len(self.ordered_at) >= 4 else ""
        return (
            f"{이름}({self.symbol}) {방향} {self.ordered_quantity}주 중 "
            f"{self.filled_quantity}주 체결 · 잔여 {self.remaining}주{때}"
        )


@dataclass(frozen=True)
class Holding:
    """증권사 계좌가 실제로 들고 있다고 말하는 보유 종목 하나."""

    symbol: str
    name: str
    quantity: int
    avg_buy_price: float
    current_price: float
    eval_amount: float
    pnl_amount: float


@dataclass(frozen=True)
class AccountBalance:
    """증권사 계좌의 실제 잔고 — 우리 DB가 자체 계산해 온 가상 현금과
    대조하기 위한 "정답지"다.

    이 프로그램은 그동안 현금을 스스로 계산해 왔다(engine_state.cash).
    주문이 일부만 체결되거나 거부되면 그 계산이 실제 계좌와 조용히
    어긋나는데, 대조할 기준이 없어 눈치챌 방법이 없었다."""

    #: 예수금 총액(dnca_tot_amt). **주문 가능 금액과 같지 않다** — 매수 대금은
    #: 결제(T+2)가 끝나야 여기서 빠지므로, 오늘 산 것은 이 값에 아직 안 잡힌다.
    cash: float
    total_eval_amount: float  # 보유 주식 평가금액 합계
    net_asset: float  # 순자산(현금+주식)
    holdings: list[Holding]
    #: 증권사가 준 계좌요약 원본(output2). 어떤 필드가 무엇인지 눈으로
    #: 확인해야 할 때가 있어서 그대로 들고 다닌다.
    raw_summary: dict[str, str] = field(default_factory=dict)

    def holding_for(self, symbol: str) -> Holding | None:
        return next((h for h in self.holdings if h.symbol == symbol), None)


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: OrderSide
    quantity: int
    #: 실제 체결가(확인됐다면) 또는 기준가. fill_confirmed로 어느 쪽인지 구분한다.
    price: float
    order_id: str
    is_paper: bool
    #: 판단 근거가 된 가격 — 전략이 본 마지막 종가.
    #: 이걸 남겨야 "결정한 가격과 실제로 산 가격이 얼마나 벌어졌나"를 잴 수 있다.
    #: 지금까지는 체결가가 확인되면 price를 덮어써서 기준가가 사라졌다.
    reference_price: float = 0.0
    #: price가 실제 체결가인지, 조회 실패로 기준가를 그대로 쓴 것인지.
    #: 이 구분이 없으면 슬리피지 통계에 '차이 0'인 가짜 표본이 섞인다.
    fill_confirmed: bool = False
    #: 우리가 **주문한** 수량. quantity는 그중 체결된 것이다.
    #: 부분 체결은 흔한 일이라 사고가 아니다 — 다만 알림에 "12주 중 4주,
    #: 잔여 8주"처럼 그대로 적어야 사람이 무슨 일이 있었는지 안다.
    #: 0이면 조회를 안 했거나 못 한 것이라 quantity와 같다고 본다.
    ordered_quantity: int = 0

    @property
    def 잔여(self) -> int:
        """주문했는데 아직 안 채워진 수량. 모르면 0."""
        return max(0, self.ordered_quantity - self.quantity) if self.ordered_quantity else 0
