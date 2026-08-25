"""KIS 서버로 실제(또는 KIS 모의투자) 주문을 넣는 OrderExecutor 구현체.

TradingEngine은 OrderExecutor 인터페이스만 알기 때문에, 개발 중에는
SimulatedOrderExecutor를, KIS 네트워크 접근이 되는 환경에서는 이걸로
바꿔 끼우기만 하면 된다.

주문을 넣은 뒤 체결 조회로 실제 체결가를 확인해 기록을 바로잡는다 —
시장가 주문은 넣어봐야 얼마에 되는지 알 수 있어서, 주문 시점의 기준가
(직전 종가)만 기록하면 손익 집계에 오차가 계속 쌓이기 때문이다."""

from __future__ import annotations

import time
from collections.abc import Callable

from loguru import logger

from muwon.data.kis_client import KISClient
from muwon.domain.interfaces import OrderExecutor
from muwon.domain.types import OrderResult, OrderSide

# 주문 직후엔 체결 내역이 아직 안 잡힐 수 있어 잠깐 기다렸다 조회한다.
# 시장가 주문은 보통 즉시 체결되므로 짧게 몇 번만 확인하고 포기한다 —
# 여기서 오래 붙들고 있으면 나머지 종목 처리가 밀린다.
_FILL_LOOKUP_ATTEMPTS = 3
_FILL_LOOKUP_DELAY_SECONDS = 1.0


class KISOrderExecutor(OrderExecutor):
    def __init__(
        self,
        client: KISClient,
        confirm_fills: bool = True,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self._client = client
        self._confirm_fills = confirm_fills
        self._sleep = sleep_fn

    def submit_order(
        self, symbol: str, side: OrderSide, quantity: int, reference_price: float
    ) -> OrderResult:
        order = self._client.place_cash_order(symbol, side, quantity, reference_price)
        if not self._confirm_fills:
            return order
        return self._with_actual_fill(order)

    def _with_actual_fill(self, order: OrderResult) -> OrderResult:
        """체결 조회로 실제 체결가·수량을 반영한 OrderResult를 돌려준다.

        조회가 실패하거나 아직 체결 내역이 없으면 원래 값(기준가)을 그대로
        쓴다 — 기록 정확도를 높이려는 보조 단계일 뿐이라, 여기서 예외를
        올려 매매 파이프라인 전체를 멈추는 건 과하다. 대신 경고를 남겨
        나중에 손익이 어긋난 이유를 추적할 수 있게 한다."""
        for attempt in range(_FILL_LOOKUP_ATTEMPTS):
            try:
                fill = self._client.get_fill(order.order_id)
            except Exception as e:  # noqa: BLE001 — 조회 실패가 매매를 멈춰선 안 된다
                logger.warning(f"체결 조회 실패({order.order_id}): {e} — 기준가로 기록합니다.")
                return order

            if fill is not None and not fill.is_unfilled:
                if fill.filled_quantity != order.quantity:
                    # **사고가 아니다.** 부분 체결은 흔한 일이라 경보를 걸면
                    # 경보가 죽는다. 사실만 남기고, 사람에게는 체결 알림에
                    # "12주 중 4주, 잔여 8주"로 그대로 적어 보낸다.
                    logger.info(
                        f"부분 체결: {order.symbol} 주문 {order.quantity}주 중 "
                        f"{fill.filled_quantity}주 체결, 잔여 "
                        f"{order.quantity - fill.filled_quantity}주"
                    )
                logger.info(
                    f"체결 확인: {order.symbol} {fill.filled_quantity}주 @ "
                    f"{fill.avg_fill_price:,.0f}원 (기준가 {order.price:,.0f}원)"
                )
                return OrderResult(
                    symbol=order.symbol,
                    side=order.side,
                    quantity=fill.filled_quantity,
                    price=fill.avg_fill_price,
                    order_id=order.order_id,
                    is_paper=order.is_paper,
                    # 기준가를 함께 남긴다 — 체결가로 덮어쓰면 "결정한 가격과
                    # 실제로 산 가격이 얼마나 벌어졌나"를 영영 잴 수 없다.
                    reference_price=order.reference_price or order.price,
                    fill_confirmed=True,
                    ordered_quantity=fill.ordered_quantity or order.quantity,
                )

            if attempt < _FILL_LOOKUP_ATTEMPTS - 1:
                self._sleep(_FILL_LOOKUP_DELAY_SECONDS)

        logger.warning(
            f"체결 내역을 확인하지 못했습니다({order.order_id}) — 기준가 "
            f"{order.price:,.0f}원으로 기록합니다. 미체결이거나 반영이 늦을 수 있습니다."
        )
        return order
