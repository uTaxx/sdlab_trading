from collections.abc import Callable
from dataclasses import dataclass

from muwon.settings.schema import RiskPolicy


@dataclass
class RiskDecision:
    approved: bool
    reason: str


class RiskManager:
    """주문 실행 전 마지막으로 거치는 검증 계층.

    전략이 매수 신호를 내더라도, 여기서 정한 규칙을 어기면 주문은 거부된다.
    정책은 매 호출마다 policy_provider()로 새로 읽어오므로, 대시보드/CLI에서
    SettingsService.set_risk_policy()로 값을 바꾸면 재시작 없이 곧바로
    반영된다.
    """

    def __init__(self, policy_provider: Callable[[], RiskPolicy]):
        self._policy_provider = policy_provider

    def get_policy(self) -> RiskPolicy:
        return self._policy_provider()

    def check_new_position(
        self,
        proposed_weight: float,
        current_open_positions: int,
        daily_pnl_pct: float,
    ) -> RiskDecision:
        policy = self._policy_provider()

        if not policy.trading_enabled:
            return RiskDecision(approved=False, reason="자동매매가 꺼져 있음. 신규 진입 중단")
        if daily_pnl_pct <= policy.daily_loss_limit_pct:
            return RiskDecision(
                approved=False,
                reason=f"일일 손실 한도 도달 (daily_pnl={daily_pnl_pct:.2%}, "
                f"한도={policy.daily_loss_limit_pct:.2%}): 신규 진입 중단",
            )
        if current_open_positions >= policy.max_concurrent_positions:
            return RiskDecision(
                approved=False,
                reason=f"최대 동시 보유 종목 수 초과 ({current_open_positions}/"
                f"{policy.max_concurrent_positions})",
            )
        if proposed_weight > policy.max_position_weight:
            return RiskDecision(
                approved=False,
                reason=f"종목당 최대 비중 초과 (제안={proposed_weight:.2%}, "
                f"한도={policy.max_position_weight:.2%})",
            )
        return RiskDecision(approved=True, reason="승인")

    def should_stop_loss(self, entry_price: float, current_price: float) -> bool:
        if entry_price <= 0:
            return False
        policy = self._policy_provider()
        change_pct = (current_price - entry_price) / entry_price
        return change_pct <= policy.stop_loss_pct
