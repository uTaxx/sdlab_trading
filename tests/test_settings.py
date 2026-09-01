import pytest

from muwon.db.session import make_session_factory
from muwon.settings.crypto import generate_master_key
from muwon.settings.schema import (
    KISCredentials,
    RiskPolicy,
    StrategySelection,
    TelegramConfig,
)
from muwon.settings.service import SettingsService
from muwon.settings.store import SettingsStore


def make_service(master_key: str | None = None) -> SettingsService:
    session_factory = make_session_factory("sqlite:///:memory:")
    store = SettingsStore(session_factory, master_key=master_key, cache_ttl_seconds=0)
    return SettingsService(store)


def test_risk_policy_roundtrip():
    service = make_service()
    policy = RiskPolicy(
        max_position_weight=0.2,
        stop_loss_pct=-0.07,
        daily_loss_limit_pct=-0.04,
        max_concurrent_positions=5,
    )
    service.set_risk_policy(policy)
    assert service.get_risk_policy() == policy


def test_risk_policy_defaults_when_unset():
    service = make_service()
    assert service.get_risk_policy() == RiskPolicy()


def test_trading_enabled_roundtrip():
    service = make_service()
    service.set_risk_policy(RiskPolicy(trading_enabled=False))
    assert service.get_risk_policy().trading_enabled is False
    service.set_risk_policy(RiskPolicy(trading_enabled=True))
    assert service.get_risk_policy().trading_enabled is True


def test_kis_credentials_are_encrypted_and_roundtrip():
    master_key = generate_master_key()
    service = make_service(master_key=master_key)
    creds = KISCredentials(
        kis_env="paper",
        app_key="app-key-123",
        app_secret="app-secret-456",
        account_no="12345678",
        account_product_cd="01",
    )
    service.set_kis_credentials(creds)
    assert service.get_kis_credentials() == creds


def test_secret_write_without_master_key_raises():
    service = make_service(master_key=None)
    with pytest.raises(RuntimeError, match="MUWON_MASTER_KEY"):
        service.set_kis_credentials(KISCredentials(app_key="x", app_secret="y"))


def make_services_sharing_db(old_key: str, new_key: str) -> tuple[SettingsService, SettingsService]:
    """같은 DB를 옛 마스터키/새 마스터키로 각각 여는 서비스 한 쌍: 마스터키를
    새로 발급했는데 DB에는 옛 키로 암호화된 값이 남아 있는 상황을 재현한다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    old = SettingsService(SettingsStore(session_factory, master_key=old_key, cache_ttl_seconds=0))
    new = SettingsService(SettingsStore(session_factory, master_key=new_key, cache_ttl_seconds=0))
    return old, new


def test_rotated_master_key_does_not_crash_and_reports_broken_keys():
    """마스터키를 새로 발급하면 옛 키로 암호화된 값은 못 읽는다. 이때 예외로
    죽지 않고(대시보드 전체가 멈추면 안 됨) 빈 값으로 넘어가면서, 어떤 키가
    안 열리는지 알려줘야 한다."""
    old_service, new_service = make_services_sharing_db(generate_master_key(), generate_master_key())
    old_service.set_kis_credentials(
        KISCredentials(kis_env="paper", app_key="key-a", app_secret="secret-a", account_no="12345678")
    )

    creds = new_service.get_kis_credentials()  # 예외 없이 반환되어야 한다
    assert creds.app_key == ""
    assert creds.app_secret == ""
    assert creds.kis_env == "paper"  # 비밀값이 아닌 항목은 정상적으로 읽힌다

    broken = new_service.undecryptable_secret_keys()
    assert "kis.app_key" in broken
    assert "kis.app_secret" in broken
    assert "kis.env" not in broken  # 비밀값이 아니므로 대상 아님


def test_resaving_with_new_master_key_clears_broken_keys():
    """못 읽던 값을 새 키로 다시 저장하면 복구되어야 한다. 화면 안내가
    약속하는 동작."""
    old_service, new_service = make_services_sharing_db(generate_master_key(), generate_master_key())
    old_service.set_kis_credentials(KISCredentials(app_key="key-a", app_secret="secret-a"))
    assert new_service.undecryptable_secret_keys() != []

    new_service.set_kis_credentials(KISCredentials(app_key="key-b", app_secret="secret-b"))

    assert new_service.undecryptable_secret_keys() == []
    assert new_service.get_kis_credentials().app_key == "key-b"


def test_rotated_master_key_marks_history_entry_undecrypted():
    old_service, new_service = make_services_sharing_db(generate_master_key(), generate_master_key())
    old_service.set_telegram_config(TelegramConfig(bot_token="123:ABC", chat_id="999"))

    entry = next(h for h in new_service.get_settings_history() if h.key == "telegram.bot_token")
    assert entry.decrypted is False
    assert entry.new_value is None


def test_undecryptable_keys_empty_when_master_key_matches():
    master_key = generate_master_key()
    service = make_service(master_key=master_key)
    service.set_kis_credentials(KISCredentials(app_key="key-a", app_secret="secret-a"))
    assert service.undecryptable_secret_keys() == []


def test_strategy_selection_defaults_to_live_key():
    service = make_service()
    assert service.get_strategy_selection() == StrategySelection()


def test_strategy_selection_roundtrip_and_is_logged_in_history():
    service = make_service()
    service.set_strategy_selection(StrategySelection(active_keys=("ma_rsi_v1",)))
    service.set_strategy_selection(StrategySelection(active_keys=("ma_rsi_fast5_20",)))
    assert service.get_strategy_selection().active_key == "ma_rsi_fast5_20"

    entry = next(h for h in service.get_settings_history() if h.key == "strategy.active_keys")
    assert entry.old_value == "ma_rsi_v1"
    assert entry.new_value == "ma_rsi_fast5_20"


def test_several_strategies_and_the_combine_mode_survive_a_roundtrip():
    """전략을 여러 개 거는 것이 이 설정의 요점이다. 개수 제한은 없다."""
    service = make_service()
    선택 = StrategySelection(
        active_keys=("ma_rsi_v1", "golden_cross_20_60", "macd_cross"), combine="AND"
    )
    service.set_strategy_selection(선택)

    돌아온것 = service.get_strategy_selection()
    assert 돌아온것.active_keys == ("ma_rsi_v1", "golden_cross_20_60", "macd_cross")
    assert 돌아온것.combine == "AND"


def test_an_old_single_key_setting_still_loads():
    """운영 DB에는 이미 strategy.active_key 하나만 들어 있다. 컬럼을
    바꾸느라 지금 돌고 있는 설정이 초기화되면 안 된다."""
    service = make_service()
    service._store.set("strategy.active_key", "macd_cross")

    선택 = service.get_strategy_selection()
    assert 선택.active_keys == ("macd_cross",)
    assert 선택.combine == "OR"


def test_telegram_config_roundtrip():
    master_key = generate_master_key()
    service = make_service(master_key=master_key)
    cfg = TelegramConfig(bot_token="123:ABC", chat_id="999")
    service.set_telegram_config(cfg)
    assert service.get_telegram_config() == cfg


def test_history_records_non_secret_change():
    service = make_service()
    service.set_risk_policy(RiskPolicy(max_concurrent_positions=5))
    service.set_risk_policy(RiskPolicy(max_concurrent_positions=6))

    history = service.get_settings_history()
    entry = next(h for h in history if h.key == "risk.max_concurrent_positions")
    assert entry.old_value == "5"
    assert entry.new_value == "6"
    assert entry.decrypted is True


def test_history_skips_entry_when_value_unchanged():
    service = make_service()
    policy = RiskPolicy(max_concurrent_positions=5)
    service.set_risk_policy(policy)
    service.set_risk_policy(policy)  # 같은 값 재저장: 이력 안 남아야 함

    history = [h for h in service.get_settings_history() if h.key == "risk.max_concurrent_positions"]
    assert len(history) == 1


def test_history_decrypts_secret_when_master_key_present():
    master_key = generate_master_key()
    service = make_service(master_key=master_key)
    service.set_kis_credentials(KISCredentials(app_key="key-a", app_secret="secret-a"))
    service.set_kis_credentials(KISCredentials(app_key="key-b", app_secret="secret-a"))

    history = service.get_settings_history()
    entry = next(h for h in history if h.key == "kis.app_key")
    assert entry.old_value == "key-a"
    assert entry.new_value == "key-b"
    assert entry.decrypted is True


def test_history_hides_secret_without_master_key():
    master_key = generate_master_key()
    session_factory = make_session_factory("sqlite:///:memory:")
    store_with_key = SettingsStore(session_factory, master_key=master_key, cache_ttl_seconds=0)
    SettingsService(store_with_key).set_kis_credentials(KISCredentials(app_key="key-a"))

    store_without_key = SettingsStore(session_factory, master_key=None, cache_ttl_seconds=0)
    service_without_key = SettingsService(store_without_key)

    entry = next(
        h for h in service_without_key.get_settings_history() if h.key == "kis.app_key"
    )
    assert entry.decrypted is False
    assert entry.new_value is None
