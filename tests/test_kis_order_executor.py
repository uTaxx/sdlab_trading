"""주문 후 실제 체결가를 반영하는 로직 검증.

시장가 주문은 넣어봐야 얼마에 체결되는지 알 수 있어서, 주문 시점의
기준가(직전 종가)만 기록하면 손익 집계에 오차가 쌓인다. 체결 조회로
실제 값을 받아 기록을 바로잡되, 조회가 실패해도 매매 자체는 멈추지
않아야 한다."""

from unittest.mock import MagicMock

from muwon.domain.types import FillInfo, OrderResult, OrderSide
from muwon.execution.kis_order_executor import KISOrderExecutor


def make_order(price: float = 70_000.0, quantity: int = 10) -> OrderResult:
    return OrderResult(
        symbol="005930",
        side=OrderSide.BUY,
        quantity=quantity,
        price=price,
        order_id="ORDER123",
        is_paper=True,
    )


def make_executor(client) -> KISOrderExecutor:
    return KISOrderExecutor(client, sleep_fn=lambda _: None)


def test_records_actual_fill_price_instead_of_reference_price():
    client = MagicMock()
    client.place_cash_order.return_value = make_order(price=70_000.0, quantity=10)
    client.get_fill.return_value = FillInfo(
        order_id="ORDER123",
        symbol="005930",
        ordered_quantity=10,
        filled_quantity=10,
        avg_fill_price=70_450.0,  # 실제 체결은 기준가보다 비싸게 됐다
    )

    result = make_executor(client).submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    assert result.price == 70_450.0
    assert result.quantity == 10
    assert result.order_id == "ORDER123"


def test_partial_fill_records_only_filled_quantity():
    """부분 체결이면 주문 수량이 아니라 실제 체결 수량으로 기록해야 한다.
    안 그러면 보유하지도 않은 수량을 들고 있다고 착각한다."""
    client = MagicMock()
    client.place_cash_order.return_value = make_order(quantity=10)
    client.get_fill.return_value = FillInfo(
        order_id="ORDER123",
        symbol="005930",
        ordered_quantity=10,
        filled_quantity=4,
        avg_fill_price=70_100.0,
    )

    result = make_executor(client).submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    assert result.quantity == 4
    assert result.price == 70_100.0


def test_retries_then_falls_back_to_reference_price_when_never_filled():
    """조회할 때마다 미체결이면 몇 번 재시도한 뒤 기준가로 기록하고 넘어간다.
    여기서 매매를 멈추면 나머지 종목까지 처리가 밀린다."""
    client = MagicMock()
    client.place_cash_order.return_value = make_order(price=70_000.0)
    client.get_fill.return_value = None

    result = make_executor(client).submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    assert result.price == 70_000.0
    assert result.quantity == 10
    assert client.get_fill.call_count == 3


def test_unfilled_result_is_treated_as_not_yet_filled():
    client = MagicMock()
    client.place_cash_order.return_value = make_order(price=70_000.0)
    client.get_fill.return_value = FillInfo(
        order_id="ORDER123",
        symbol="005930",
        ordered_quantity=10,
        filled_quantity=0,  # 접수는 됐지만 아직 체결 전
        avg_fill_price=0.0,
    )

    result = make_executor(client).submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    # 체결가 0원으로 기록되면 손익이 완전히 망가진다. 기준가를 유지해야 한다
    assert result.price == 70_000.0


def test_fill_lookup_failure_does_not_break_trading():
    client = MagicMock()
    client.place_cash_order.return_value = make_order(price=70_000.0)
    client.get_fill.side_effect = RuntimeError("체결조회 API 장애")

    result = make_executor(client).submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    assert result.price == 70_000.0  # 주문 자체는 성공했으므로 기록은 남긴다


def test_confirm_fills_can_be_disabled():
    """체결 조회는 API 호출을 추가로 쓰므로(초당 제한이 빡빡하다) 끌 수 있어야 한다."""
    client = MagicMock()
    client.place_cash_order.return_value = make_order(price=70_000.0)

    executor = KISOrderExecutor(client, confirm_fills=False, sleep_fn=lambda _: None)
    result = executor.submit_order("005930", OrderSide.BUY, 10, 70_000.0)

    assert result.price == 70_000.0
    client.get_fill.assert_not_called()
