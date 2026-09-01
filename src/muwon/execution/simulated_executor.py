"""KIS 없이 매매 파이프라인(신호→리스크→체결→알림→기록)을 검증하기 위한
가짜 주문 실행기.

KIS 모의투자와 다른 개념이다. KIS 모의투자는 한국투자증권 서버가 실제로
받아서 처리하는 주문이고, 이건 그 서버를 아예 거치지 않고 reference_price
그대로 체결됐다고 로컬에서 가정한다. 네트워크 정책이 KIS 포트(9443/29443)를
막고 있어 KIS 서버 자체에 닿을 수 없는 개발 환경에서, "신호가 나면 실제로
주문 로직/리스크 검증/텔레그램 알림/DB 기록이 다 맞물려 돌아가는지"를
확인하는 용도로만 쓴다. 실거래·KIS 모의투자 전환 시에는 KISOrderExecutor로
바꿔 끼운다."""

from __future__ import annotations

import uuid

from muwon.domain.interfaces import OrderExecutor
from muwon.domain.types import OrderResult, OrderSide


class SimulatedOrderExecutor(OrderExecutor):
    def submit_order(
        self, symbol: str, side: OrderSide, quantity: int, reference_price: float
    ) -> OrderResult:
        return OrderResult(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=reference_price,
            order_id=f"SIM-{uuid.uuid4().hex[:12]}",
            is_paper=True,
            reference_price=reference_price,
            # 가짜 체결이므로 확인된 체결이 아니다. 이걸 True로 두면
            # 슬리피지 통계에 '차이 0'인 표본이 잔뜩 섞여 실제보다 작게 나온다.
            fill_confirmed=False,
        )
