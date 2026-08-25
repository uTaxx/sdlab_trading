"""체결 알림이 부분 체결을 어떻게 적는지 고정한다.

**부분 체결은 사고가 아니라 흔한 일이다.** 처음에는 "주문과 다르다"며
경보를 걸었는데, 흔한 일에 경보를 걸면 며칠 만에 그 경보를 안 읽게 되고
정작 진짜 문제를 놓친다. 경보를 빼고 사실만 적는다.
"""

from __future__ import annotations

from muwon.domain.types import OrderResult, OrderSide
from muwon.execution.engine import _수량글


def _주문(체결: int, 주문: int = 0) -> OrderResult:
    return OrderResult(
        symbol="066970", side=OrderSide.BUY, quantity=체결, price=118300.0,
        order_id="x", is_paper=True, ordered_quantity=주문,
    )


def test_다_채워지면_한_줄이다():
    """평소에 줄이 길어지면 사람이 안 읽는다."""
    assert _수량글(_주문(51, 51)) == "수량: 51주"


def test_부분_체결이면_주문과_잔여를_같이_적는다():
    글 = _수량글(_주문(4, 12))

    assert "12주 중 4주 체결" in 글
    assert "잔여 8주" in 글


def test_부분_체결이어도_경고_표시는_안_붙인다():
    """흔한 일이라 ⚠️를 붙이면 경고가 값을 잃는다."""
    글 = _수량글(_주문(4, 12))

    assert "⚠" not in 글
    assert "경고" not in 글


def test_손댈_것이_없다는_것까지_적는다():
    """무슨 일인지만 알려 주고 끝내면 '그래서 뭘 해야 하나'가 남는다."""
    assert "손댈 것 없습니다" in _수량글(_주문(4, 12))


def test_주문_수량을_모르면_체결분만_적는다():
    """체결 조회에 실패한 회차다. 잔여를 0으로 단정하면 안 되지만,
    모르는 것을 아는 척 적는 것보다는 안 적는 쪽이 낫다."""
    assert _수량글(_주문(7)) == "수량: 7주"


def test_체결이_주문보다_많아도_음수가_안_나온다():
    """있을 리 없지만, 있으면 '잔여 -3주'라는 헛소리가 알림에 나간다."""
    assert _수량글(_주문(15, 12)) == "수량: 15주"


def test_잔여는_주문에서_체결을_뺀_값이다():
    assert _주문(4, 12).잔여 == 8
    assert _주문(12, 12).잔여 == 0
    assert _주문(7).잔여 == 0
