"""DB 기록 vs 실제 계좌 잔고 대조 로직 검증.

이 프로그램은 현금을 스스로 계산해 왔고, 주문이 일부만 체결되거나 거부되면
그 값이 실제 계좌와 조용히 어긋난다. 여기서 검증하는 건 "어긋난 걸 빠짐없이
찾아내는가"다. 못 찾으면 어긋난 현금으로 비중 계산과 손실한도가 계속
돌아가게 된다."""

from datetime import date, datetime

from muwon.db.models import PositionRow
from muwon.domain.types import AccountBalance, Holding
from muwon.execution.reconciliation import CASH_TOLERANCE_KRW, reconcile


def make_position(symbol: str, quantity: int) -> PositionRow:
    return PositionRow(
        symbol=symbol,
        quantity=quantity,
        entry_price=70_000.0,
        entry_date=date(2026, 8, 17),
        entered_at=datetime(2026, 8, 17, 9, 30),  # noqa: DTZ001 (테스트용, tz 무관)
        entry_reason="테스트",
        strategy_key="ma_rsi_v1",
    )


def make_holding(symbol: str, quantity: int) -> Holding:
    return Holding(
        symbol=symbol,
        name=symbol,
        quantity=quantity,
        avg_buy_price=70_000.0,
        current_price=71_000.0,
        eval_amount=71_000.0 * quantity,
        pnl_amount=1_000.0 * quantity,
    )


def make_balance(cash: float, holdings: list[Holding] | None = None) -> AccountBalance:
    holdings = holdings or []
    return AccountBalance(
        cash=cash,
        total_eval_amount=sum(h.eval_amount for h in holdings),
        net_asset=cash + sum(h.eval_amount for h in holdings),
        holdings=holdings,
    )


def test_reports_consistent_when_everything_matches():
    report = reconcile(
        db_positions={"005930": make_position("005930", 10)},
        db_cash=5_000_000.0,
        balance=make_balance(5_000_000.0, [make_holding("005930", 10)]),
    )

    assert report.is_consistent is True
    assert report.matched_symbols == ["005930"]
    assert report.quantity_mismatches == []
    assert "일치합니다" in report.summary_lines()[0]


def test_detects_cash_drift():
    report = reconcile(
        db_positions={},
        db_cash=5_000_000.0,
        balance=make_balance(4_300_000.0),  # 실제로는 70만원 적다
    )

    assert report.cash_matches is False
    assert report.is_consistent is False
    assert report.cash_difference == -700_000.0
    assert any("현금" in line and "-700,000" in line for line in report.summary_lines())


def test_small_cash_difference_is_tolerated():
    """수수료·세금 반올림으로 몇 원 차이는 늘 생긴다. 매번 경고하면
    진짜 문제가 묻힌다."""
    report = reconcile(
        db_positions={},
        db_cash=5_000_000.0,
        balance=make_balance(5_000_000.0 - CASH_TOLERANCE_KRW + 1),
    )
    assert report.cash_matches is True
    assert report.is_consistent is True


def test_detects_partial_fill_quantity_drift():
    """주문은 10주 넣었는데 4주만 체결된 상황: DB엔 10주로 기록돼 있고
    계좌엔 4주만 있다. 이걸 못 잡으면 없는 6주를 팔려고 시도하게 된다."""
    report = reconcile(
        db_positions={"005930": make_position("005930", 10)},
        db_cash=1_000_000.0,
        balance=make_balance(1_000_000.0, [make_holding("005930", 4)]),
    )

    assert report.is_consistent is False
    assert len(report.quantity_mismatches) == 1
    mismatch = report.quantity_mismatches[0]
    assert mismatch.db_quantity == 10
    assert mismatch.account_quantity == 4
    assert "DB 10주 vs 계좌 4주" in mismatch.description


def test_detects_position_only_in_account():
    """계좌엔 있는데 DB엔 없는 종목: 수동 매매했거나 우리 기록이 유실된
    경우다. DB에만 있는 경우와 반대 방향이라 둘 다 잡아야 한다."""
    report = reconcile(
        db_positions={},
        db_cash=1_000_000.0,
        balance=make_balance(1_000_000.0, [make_holding("000660", 3)]),
    )

    assert len(report.quantity_mismatches) == 1
    assert "계좌엔 3주 있는데 DB엔 기록 없음" in report.quantity_mismatches[0].description


def test_detects_position_only_in_db():
    report = reconcile(
        db_positions={"005930": make_position("005930", 7)},
        db_cash=1_000_000.0,
        balance=make_balance(1_000_000.0, []),
    )

    assert len(report.quantity_mismatches) == 1
    assert "DB엔 7주인데 계좌엔 없음" in report.quantity_mismatches[0].description


def test_checks_union_of_both_sides():
    """한쪽에만 있는 종목이 여러 개 섞여 있어도 전부 잡아야 한다."""
    report = reconcile(
        db_positions={
            "005930": make_position("005930", 10),  # 일치
            "035720": make_position("035720", 5),  # DB에만
        },
        db_cash=1_000_000.0,
        balance=make_balance(
            1_000_000.0,
            [make_holding("005930", 10), make_holding("000660", 2)],  # 000660은 계좌에만
        ),
    )

    assert report.matched_symbols == ["005930"]
    assert {m.symbol for m in report.quantity_mismatches} == {"035720", "000660"}
