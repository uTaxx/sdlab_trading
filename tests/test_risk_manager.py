from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy


def make_manager(policy: RiskPolicy | None = None) -> RiskManager:
    policy = policy or RiskPolicy(
        max_position_weight=0.15,
        stop_loss_pct=-0.05,
        daily_loss_limit_pct=-0.03,
        max_concurrent_positions=8,
    )
    return RiskManager(policy_provider=lambda: policy)


def test_approves_within_all_limits():
    rm = make_manager()
    decision = rm.check_new_position(
        proposed_weight=0.10, current_open_positions=3, daily_pnl_pct=-0.01
    )
    assert decision.approved


def test_blocks_when_daily_loss_limit_hit():
    rm = make_manager()
    decision = rm.check_new_position(
        proposed_weight=0.05, current_open_positions=1, daily_pnl_pct=-0.04
    )
    assert not decision.approved
    assert "일일 손실 한도" in decision.reason


def test_blocks_when_max_positions_reached():
    rm = make_manager()
    decision = rm.check_new_position(
        proposed_weight=0.05, current_open_positions=8, daily_pnl_pct=0.0
    )
    assert not decision.approved
    assert "최대 동시 보유" in decision.reason


def test_blocks_when_weight_exceeds_limit():
    rm = make_manager()
    decision = rm.check_new_position(
        proposed_weight=0.20, current_open_positions=1, daily_pnl_pct=0.0
    )
    assert not decision.approved
    assert "최대 비중" in decision.reason


def test_blocks_new_positions_when_trading_disabled():
    policy = RiskPolicy(
        max_position_weight=0.15,
        stop_loss_pct=-0.05,
        daily_loss_limit_pct=-0.03,
        max_concurrent_positions=8,
        trading_enabled=False,
    )
    rm = make_manager(policy)
    decision = rm.check_new_position(
        proposed_weight=0.05, current_open_positions=0, daily_pnl_pct=0.0
    )
    assert not decision.approved
    assert "자동매매" in decision.reason


def test_stop_loss_triggers_below_threshold():
    rm = make_manager()
    assert rm.should_stop_loss(entry_price=10000, current_price=9400)
    assert not rm.should_stop_loss(entry_price=10000, current_price=9600)


def test_policy_change_takes_effect_without_recreating_manager():
    """대시보드/CLI에서 정책을 바꾸면 RiskManager를 새로 만들지 않아도
    다음 호출부터 바로 반영되어야 한다. SettingsService 연동의 핵심 전제."""
    current_policy = RiskPolicy(max_position_weight=0.15, stop_loss_pct=-0.05,
                                 daily_loss_limit_pct=-0.03, max_concurrent_positions=8)
    rm = RiskManager(policy_provider=lambda: current_policy)

    decision = rm.check_new_position(
        proposed_weight=0.20, current_open_positions=1, daily_pnl_pct=0.0
    )
    assert not decision.approved

    current_policy = RiskPolicy(max_position_weight=0.25, stop_loss_pct=-0.05,
                                 daily_loss_limit_pct=-0.03, max_concurrent_positions=8)
    decision = rm.check_new_position(
        proposed_weight=0.20, current_open_positions=1, daily_pnl_pct=0.0
    )
    assert decision.approved
