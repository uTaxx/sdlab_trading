"""기록에만 있는 보유를 지우는 판단을 고정한다.

제일 나쁜 사고는 **진짜 보유를 지우는 것**이다. 지워지면 엔진이 그 종목을
아예 안 보고 손절이 조용히 멈춘다. 화면에는 아무 일도 없는 것처럼 보이고,
값이 더 빠진 뒤에야 드러난다. 그 경우를 먼저 못 박는다.
"""

from __future__ import annotations

from datetime import date

from muwon.db.models import PositionRow
from muwon.domain.types import Holding
from muwon.execution.phantom import plan, 맞출평가금


def _보유(symbol: str, quantity: int = 10, entry_price: float = 1000.0) -> PositionRow:
    return PositionRow(
        symbol=symbol,
        quantity=quantity,
        entry_price=entry_price,
        entry_date=date(2026, 8, 24),
        entry_reason="시험용",
        strategy_key="volume_surge_5d",
    )


def _계좌(symbol: str, quantity: int = 10, current_price: float = 1100.0) -> Holding:
    return Holding(
        symbol=symbol,
        name=f"이름{symbol}",
        quantity=quantity,
        avg_buy_price=1000.0,
        current_price=current_price,
        eval_amount=quantity * current_price,
        pnl_amount=quantity * (current_price - 1000.0),
    )


def test_계좌에_없으면_지운다():
    계획 = plan(["066970"], {"066970": _보유("066970")}, holdings=[])

    assert [p.symbol for p in 계획.지울것] == ["066970"]
    assert 계획.할일있나


def test_계좌에_있으면_이름을_줘도_안_지운다():
    """제일 중요한 시험. 진짜 보유를 지우면 손절이 조용히 멈춘다."""
    계획 = plan(["066970"], {"066970": _보유("066970")}, holdings=[_계좌("066970")])

    assert 계획.지울것 == []
    assert [h.symbol for h in 계획.계좌에있어서거부] == ["066970"]
    assert 계획.막힌게있나


def test_사람과_계좌가_다투면_계좌가_이긴다():
    """수량이 달라도 계좌에 있으면 안 지운다. 부분 체결일 수 있다."""
    계획 = plan(
        ["066970"],
        {"066970": _보유("066970", quantity=12)},
        holdings=[_계좌("066970", quantity=5)],
    )

    assert 계획.지울것 == []
    assert 계획.계좌에있어서거부[0].quantity == 5


def test_DB에도_없으면_할_일이_없다():
    계획 = plan(["066970"], {}, holdings=[])

    assert 계획.지울것 == []
    assert 계획.이미없음 == ["066970"]
    assert not 계획.할일있나


def test_이름을_안_준_유령은_안_건드린다():
    """계좌에 없다고 다 지우면 '방금 산 게 아직 안 잡힌 것'까지 지운다."""
    계획 = plan(
        ["066970"],
        {"066970": _보유("066970"), "411060": _보유("411060")},
        holdings=[],
    )

    assert [p.symbol for p in 계획.지울것] == ["066970"]


def test_같은_것을_두_번_줘도_한_번만():
    계획 = plan(["066970", "066970"], {"066970": _보유("066970")}, holdings=[])

    assert len(계획.지울것) == 1


def test_섞여_있어도_각자_갈린다():
    계획 = plan(
        ["066970", "411060", "999999"],
        {"066970": _보유("066970"), "411060": _보유("411060")},
        holdings=[_계좌("411060")],
    )

    assert [p.symbol for p in 계획.지울것] == ["066970"]
    assert [h.symbol for h in 계획.계좌에있어서거부] == ["411060"]
    assert 계획.이미없음 == ["999999"]


def test_기준평가금은_계좌_현재가로_잡는다():
    """진입가가 아니라 현재가다. 진입가로 잡으면 이미 난 평가손익이
    '오늘 생긴 손실'로 둔갑해 일일 손실한도가 헛돈다."""
    assert 맞출평가금(1_000_000.0, [_계좌("066970", quantity=10, current_price=1100.0)]) == 1_011_000.0


def test_보유가_없으면_기준평가금은_현금뿐():
    assert 맞출평가금(9_910_035.0, []) == 9_910_035.0
