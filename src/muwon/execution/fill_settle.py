"""장이 끝난 뒤 그날 주문의 **최종** 체결을 받아 기록을 바로잡는다.

## 왜 필요한가

주문 직후 체결을 조회하면 그 순간의 스냅샷이다. 체결은 그 뒤에도 계속된다.

2026-08-25에 엘앤에프 12주를 주문했고 12주가 다 체결됐는데, 우리 기록에는
**4주**로 남았다. `KISOrderExecutor`가 주문 직후 1초 간격으로 세 번만
조회하는데 그때는 4주만 채워져 있었기 때문이다.

    계좌: 12주        DB: 4주        → 그 8주에는 손절이 안 걸린다

부분 체결과 잔여 체결은 비일비재하므로 이건 사고가 아니라 **구조의 문제**다.
주문 시점에 최종 수량을 알 방법이 없다.

## 언제 물어보면 맞나

장이 끝난 뒤다. 그때는 체결이 다 끝났고, 잔여분은 취소됐다. 같은 주문번호로
`inquire_daily_ccld`를 다시 물어보면 **확정된** 총체결수량과 평균체결가가
나온다. 새 장비도, 웹소켓도 필요 없다 — 이미 쓰고 있는 API다.

## 두 가지를 고친다

1. **주문 기록** — 체결수량과 체결가를 최종값으로. 이 저장소는 주문의
   `price`(체결가)와 `reference_price`(판단가)로 슬리피지를 재는데, 첫
   조각의 값이 표본이 되면 통계가 통째로 기운다.
2. **보유 수량** — 계좌가 사실이다. 우리 기록을 계좌에 맞춘다.

## 진입가도 같이 옮긴다

부분 체결의 첫 조각 값이 진입가로 남아 있으면 **손절선이 엉뚱한 자리에
걸린다.** 오늘 산 종목이고 그날 매수 주문이 하나뿐이면, 그 주문의 최종
평균체결가로 옮긴다. 여러 날에 걸쳐 더 산 종목은 평균을 어떻게 잡을지가
따로 판단이라 여기서 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class 주문고침:
    """주문 한 건을 최종 체결로 바로잡는 내용."""

    order_id: str
    symbol: str
    옛수량: int
    새수량: int
    옛체결가: float
    새체결가: float
    판단가: float
    #: 증권사가 준 종목명. 알림에 코드만 찍으면 무슨 주식인지 알 수 없다.
    종목명: str = ""
    #: "BUY" / "SELL". 산 것인지 판 것인지 안 적으면 수량 변화를 읽을 수 없다.
    방향: str = "BUY"

    @property
    def 바뀌나(self) -> bool:
        return self.옛수량 != self.새수량 or abs(self.옛체결가 - self.새체결가) > 0.5

    @property
    def 부른이름(self) -> str:
        return f"{self.종목명}({self.symbol})" if self.종목명 else self.symbol

    @property
    def 산건가(self) -> bool:
        return self.방향.upper() != "SELL"

    @property
    def 슬리피지(self) -> float:
        """판단가 대비 체결가가 몇 %나 벌어졌나. 판단가를 모르면 0.

        **부호가 유리·불리를 뜻하지 않는다.** 살 때 음수는 싸게 산 것이라
        유리하고, 팔 때 음수는 싸게 판 것이라 불리하다. 그래서 화면에
        이 값을 그대로 찍으면 안 된다 — `유리한가`와 같이 써야 한다."""
        if self.판단가 <= 0:
            return 0.0
        return (self.새체결가 - self.판단가) / self.판단가

    @property
    def 유리한가(self) -> bool | None:
        """판단한 값보다 좋은 자리에서 체결됐나. 판단가를 모르면 None.

        사는 쪽은 싸게 살수록 좋고, 파는 쪽은 비싸게 팔수록 좋다."""
        if self.판단가 <= 0 or abs(self.슬리피지) < 0.0001:
            return None
        return self.슬리피지 < 0 if self.산건가 else self.슬리피지 > 0

    @property
    def 슬리피지글(self) -> str:
        """사람이 읽을 한 줄. 부호 대신 '싸게/비싸게'와 '유리/불리'로 적는다."""
        if self.판단가 <= 0:
            return "판단가를 몰라 잴 수 없습니다"
        폭 = abs(self.슬리피지)
        if 폭 < 0.0001:
            return "판단했던 값 그대로 체결됐습니다"
        싸게 = self.새체결가 < self.판단가
        어떻게 = "싸게" if 싸게 else "비싸게"
        무엇 = "샀습니다" if self.산건가 else "팔았습니다"
        판정 = "유리" if self.유리한가 else "불리"
        return f"판단했던 값보다 {폭:.2%} {어떻게} {무엇} ({판정})"


@dataclass(frozen=True)
class 보유고침:
    symbol: str
    옛수량: int
    새수량: int
    옛진입가: float
    새진입가: float

    @property
    def 바뀌나(self) -> bool:
        return self.옛수량 != self.새수량 or abs(self.옛진입가 - self.새진입가) > 0.5


@dataclass(frozen=True)
class 마감계획:
    주문들: list[주문고침] = field(default_factory=list)
    보유들: list[보유고침] = field(default_factory=list)
    #: 계좌에는 있는데 우리 기록에 없는 종목. 여기서 안 들인다 —
    #: 들이는 판단은 `adopt_holdings.py`의 것이고 진입일을 사람이 줘야 한다.
    모르는종목: list[str] = field(default_factory=list)

    @property
    def 할일있나(self) -> bool:
        return any(ㅈ.바뀌나 for ㅈ in self.주문들) or any(ㅂ.바뀌나 for ㅂ in self.보유들)


def 주문맞추기(주문들, 체결조회) -> list[주문고침]:
    """그날 주문들을 최종 체결로 바로잡을 목록.

    `주문들`: (order_id, symbol, quantity, price, reference_price) 튜플들
    `체결조회`: order_id → FillInfo | None. 못 찾으면 그 주문은 건너뛴다 —
      **모르는 것을 0으로 덮으면 안 된다.** 체결된 주문이 조회에서 잠깐
      빠졌다고 수량을 0으로 만들면 보유가 통째로 사라진다.
    """
    나온것 = []
    for 줄 in 주문들:
        # 방향은 나중에 붙인 칸이라 없이 오는 호출도 받는다.
        order_id, symbol, 수량, 체결가, 판단가 = 줄[:5]
        방향 = 줄[5] if len(줄) > 5 else "BUY"
        fill = 체결조회(order_id)
        if fill is None or fill.filled_quantity <= 0:
            continue
        나온것.append(
            주문고침(
                order_id=order_id,
                symbol=symbol,
                옛수량=수량,
                새수량=fill.filled_quantity,
                옛체결가=체결가,
                새체결가=fill.avg_fill_price or 체결가,
                판단가=판단가 or 0.0,
                종목명=getattr(fill, "name", "") or "",
                방향=방향,
            )
        )
    return 나온것


def 보유맞추기(db_positions, holdings, 주문고침들, 오늘: date) -> tuple[list[보유고침], list[str]]:
    """보유 수량을 계좌에 맞추고, 오늘 산 것의 진입가를 최종 평균가로 옮긴다.

    **수량은 언제나 계좌가 이긴다.** 계좌는 언제 물어봐도 맞고, 우리 기록은
    스냅샷이라 안 맞을 수 있다.

    **계좌에 없는 종목은 여기서 안 지운다.** 이미 팔렸을 수도 있지만 조회가
    늦은 것일 수도 있어서, 지우는 판단은 `drop_phantom_holdings.py`가 사람의
    지시를 받아 한다. 여기는 장 끝나고 매일 자동으로 도는 자리라 지우는
    권한까지 주면 위험하다.
    """
    계좌 = {h.symbol: h for h in holdings}

    # 오늘 낸 매수 주문이 **하나뿐인** 종목만 진입가를 옮긴다. 여러 번
    # 나눠 샀으면 평균을 어떻게 잡을지가 따로 판단이다.
    센것: dict[str, int] = {}
    for ㅈ in 주문고침들:
        센것[ㅈ.symbol] = 센것.get(ㅈ.symbol, 0) + 1
    오늘값 = {ㅈ.symbol: ㅈ.새체결가 for ㅈ in 주문고침들 if 센것[ㅈ.symbol] == 1}

    고칠것 = []
    for symbol, pos in db_positions.items():
        h = 계좌.get(symbol)
        if h is None:
            continue
        새진입가 = pos.entry_price
        if pos.entry_date == 오늘 and symbol in 오늘값:
            새진입가 = 오늘값[symbol]
        고칠것.append(
            보유고침(
                symbol=symbol,
                옛수량=pos.quantity,
                새수량=h.quantity,
                옛진입가=pos.entry_price,
                새진입가=새진입가,
            )
        )

    모르는것 = [s for s in 계좌 if s not in db_positions]
    return 고칠것, 모르는것
