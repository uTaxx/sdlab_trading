from dataclasses import dataclass


@dataclass(frozen=True)
class TransactionCosts:
    """국내 주식 기준 근사치. 실제 수수료율은 증권사·시점에 따라 다르므로
    실거래 전환 전 반드시 실제 계좌 조건으로 다시 확인할 것."""

    buy_fee_pct: float = 0.00015  # 매수 수수료 약 0.015%
    sell_fee_pct: float = 0.00015  # 매도 수수료 약 0.015%
    sell_tax_pct: float = 0.0018  # 매도 시 증권거래세 약 0.18% (근사치)

    #: 체결가와 종가의 차이. 백테스트는 "종가에 원하는 만큼 체결됐다"고
    #: 가정하는데 실제로는 그렇지 않다. 호가가 벌어져 있고, 시장가로 치면
    #: 반대 호가를 먹고 들어간다.
    #:
    #: 크기 감을 잡으려면 호가 단위를 보면 된다. 6만원짜리 종목의 호가
    #: 단위는 100원이라 한 칸이 0.17%다. 반호가만 잡아도 편도 0.08%,
    #: 물량이 크면 여러 호가를 먹으므로 더 든다.
    #:
    #: 기본값은 0으로 둔다. 지금까지 낸 숫자가 전부 이 가정 위에 있으므로,
    #: 값을 바꾸는 순간 과거 결과와 비교가 안 된다. 얼마가 맞는지는 실측으로
    #: 정할 문제라 스윕으로 민감도를 본 뒤에 정한다.
    slippage_pct: float = 0.0

    @property
    def total_sell_cost_pct(self) -> float:
        return self.sell_fee_pct + self.sell_tax_pct

    def buy_price(self, close: float) -> float:
        """살 때는 종가보다 불리하게(비싸게) 체결된다."""
        return close * (1 + self.slippage_pct)

    def sell_price(self, close: float) -> float:
        """팔 때는 종가보다 불리하게(싸게) 체결된다."""
        return close * (1 - self.slippage_pct)
