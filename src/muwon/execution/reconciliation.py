"""우리 DB가 기록해 온 상태와 증권사 계좌의 실제 잔고를 대조한다.

이 프로그램은 현금을 스스로 계산해 왔다(engine_state.cash): 매수하면 빼고
매도하면 더하는 식이다. 그런데 주문이 일부만 체결되거나 거부되면 그 계산이
실제 계좌와 조용히 어긋나고, 대조할 기준이 없으면 어긋난 채로 계속 매매하게
된다. 비중 계산·일일 손실한도가 전부 이 현금값에 기대고 있어서, 틀어지면
리스크 관리 자체가 헛돌게 된다.

여기서는 "무엇이 얼마나 다른지"만 계산하고 고치지는 않는다. 어긋난 원인이
부분 체결일 수도, 수동 매매일 수도, 우리 버그일 수도 있어서 자동으로
덮어쓰면 그것대로 사고를 부른다. 알리고 사람이 판단하게 한다."""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from muwon.db.models import PositionRow
from muwon.domain.types import AccountBalance
from muwon.execution import state_repository

# 이보다 작은 현금 차이는 보고하지 않는다. 수수료·세금 반올림 등으로 몇 원
# 단위 오차는 늘 생기는데, 그걸 매번 경고하면 진짜 문제가 묻힌다.
CASH_TOLERANCE_KRW = 1_000.0


@dataclass(frozen=True)
class QuantityMismatch:
    symbol: str
    db_quantity: int
    account_quantity: int

    @property
    def description(self) -> str:
        if self.db_quantity == 0:
            return f"{self.symbol}: 계좌엔 {self.account_quantity}주 있는데 DB엔 기록 없음"
        if self.account_quantity == 0:
            return f"{self.symbol}: DB엔 {self.db_quantity}주인데 계좌엔 없음"
        return f"{self.symbol}: DB {self.db_quantity}주 vs 계좌 {self.account_quantity}주"


@dataclass(frozen=True)
class ReconciliationReport:
    db_cash: float
    account_cash: float
    quantity_mismatches: list[QuantityMismatch] = field(default_factory=list)
    matched_symbols: list[str] = field(default_factory=list)

    @property
    def cash_difference(self) -> float:
        """실계좌 - DB. 양수면 DB가 실제보다 현금을 적게 잡고 있다는 뜻."""
        return self.account_cash - self.db_cash

    @property
    def cash_matches(self) -> bool:
        return abs(self.cash_difference) <= CASH_TOLERANCE_KRW

    @property
    def is_consistent(self) -> bool:
        return self.cash_matches and not self.quantity_mismatches

    def summary_lines(self) -> list[str]:
        """사람이 읽을 요약: 텔레그램 알림과 콘솔 출력이 같이 쓴다."""
        lines = []
        if self.is_consistent:
            lines.append("✅ DB 기록과 실제 계좌가 일치합니다.")
        else:
            lines.append("⚠️ DB 기록과 실제 계좌가 어긋났습니다.")

        if self.cash_matches:
            lines.append(f"현금: {self.db_cash:,.0f}원 (일치)")
        else:
            lines.append(
                f"현금: DB {self.db_cash:,.0f}원 vs 계좌 {self.account_cash:,.0f}원 "
                f"({self.cash_difference:+,.0f}원)"
            )

        if self.quantity_mismatches:
            lines.append(f"보유 종목 불일치 {len(self.quantity_mismatches)}건:")
            lines.extend(f"  - {m.description}" for m in self.quantity_mismatches)
        else:
            lines.append(f"보유 종목: {len(self.matched_symbols)}개 모두 일치")
        return lines


def reconcile(
    db_positions: dict[str, PositionRow], db_cash: float, balance: AccountBalance
) -> ReconciliationReport:
    """DB 상태와 실계좌 잔고를 비교한 보고서를 만든다.

    양쪽 어디에만 있는 종목도 빠짐없이 잡아야 하므로, 두 쪽 종목코드의
    합집합을 돈다."""
    mismatches = []
    matched = []

    for symbol in sorted(set(db_positions) | {h.symbol for h in balance.holdings}):
        db_quantity = db_positions[symbol].quantity if symbol in db_positions else 0
        holding = balance.holding_for(symbol)
        account_quantity = holding.quantity if holding else 0

        if db_quantity == account_quantity:
            matched.append(symbol)
        else:
            mismatches.append(
                QuantityMismatch(
                    symbol=symbol, db_quantity=db_quantity, account_quantity=account_quantity
                )
            )

    return ReconciliationReport(
        db_cash=db_cash,
        account_cash=balance.cash,
        quantity_mismatches=mismatches,
        matched_symbols=matched,
    )


def check_account_consistency(client, session_factory, initial_cash: float = 10_000_000.0):
    """실제 계좌를 조회해 DB 상태와 대조한 보고서를 돌려준다(콘솔에도 출력).

    잔고 조회가 실패하면 None을 돌려주고 넘어간다. 대조는 어디까지나
    점검이라, 여기서 예외를 올려 그날 매매를 통째로 막는 건 과하다."""
    try:
        balance = client.get_balance()
    except Exception as e:  # noqa: BLE001 (점검 실패가 매매를 막아선 안 된다)
        logger.warning(f"계좌 잔고 조회에 실패해 대조를 건너뜁니다: {e}")
        return None

    db_positions = state_repository.load_positions(session_factory)
    db_cash, _ = state_repository.load_engine_state(session_factory, initial_cash)
    report = reconcile(db_positions, db_cash, balance)

    print("\n=== 계좌 대조 (DB 기록 vs 실제 모의투자 계좌) ===")
    for line in report.summary_lines():
        print(line)
    return report
