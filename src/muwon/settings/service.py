from dataclasses import dataclass
from datetime import datetime

from muwon.scoring.config import StrategyConfig
from muwon.settings.schema import (
    KISCredentials,
    RiskPolicy,
    StrategySelection,
    TelegramConfig,
)
from muwon.settings.store import SettingsStore


@dataclass
class SettingHistoryEntry:
    key: str
    old_value: str | None
    new_value: str | None
    is_secret: bool
    changed_at: datetime
    decrypted: bool  # is_secret인데 마스터키가 없어 값을 못 읽었으면 False


class SettingsService:
    """리스크 정책·KIS 인증정보·텔레그램 설정에 대한 타입 안전한 접근 지점.

    CLI(scripts/configure.py)와 (Phase 2+) 대시보드는 모두 이 서비스 하나를
    통해 설정을 읽고 쓴다. 저장 방식이 바뀌어도 호출부는 영향받지 않는다.
    """

    def __init__(self, store: SettingsStore):
        # **session_factory를 바로 주는 실수를 여기서 잡는다.**
        #
        # 사이에 SettingsStore가 있어야 하고, 그게 비밀값을 푸는 열쇠를 들고
        # 있다. 안 잡으면 생성은 조용히 지나가고 한참 뒤 첫 읽기에서
        # `'sessionmaker' object has no attribute 'get'`으로 터진다. 무엇을
        # 잘못 넘겼는지가 안 보이는 자리에서.
        #
        # 2026-08-20~25에 이걸로 두 가지가 망가져 있었다. 30분봉 수집은 일곱
        # 번 내리 실패했고(그날치 분봉은 다시 못 받는다), 시장 리포트는 여덟
        # 번 내리 텔레그램을 못 보냈는데 try/except 안이라 **워크플로는 여덟
        # 번 다 초록불이었다.**
        if not hasattr(store, "get") or not hasattr(store, "set"):
            raise TypeError(
                f"SettingsService에는 SettingsStore를 줘야 합니다. 받은 것: "
                f"{type(store).__name__}.\n"
                "session_factory를 바로 넘기셨다면 build_settings_service()를 쓰세요 "
                "(사이에 있는 SettingsStore가 비밀값을 푸는 열쇠를 들고 있습니다)."
            )
        self._store = store

    def get_risk_policy(self) -> RiskPolicy:
        d = RiskPolicy()
        return RiskPolicy(
            max_position_weight=float(
                self._store.get("risk.max_position_weight", str(d.max_position_weight))
            ),
            stop_loss_pct=float(self._store.get("risk.stop_loss_pct", str(d.stop_loss_pct))),
            daily_loss_limit_pct=float(
                self._store.get("risk.daily_loss_limit_pct", str(d.daily_loss_limit_pct))
            ),
            max_concurrent_positions=int(
                self._store.get(
                    "risk.max_concurrent_positions", str(d.max_concurrent_positions)
                )
            ),
            trading_enabled=self._store.get(
                "risk.trading_enabled", str(d.trading_enabled)
            )
            == "True",
            # 매도는 **없으면 켠 것으로 본다.** 매수와 반대 방향이다.
            # 값이 비어 있는 이유가 무엇이든(첫 실행, 저장 실패, 열쇠 오타)
            # 그 사이 손절이 멈춰 있으면 안 된다.
            sell_enabled=self._store.get("risk.sell_enabled", str(d.sell_enabled))
            != "False",
            atr_stop_enabled=self._store.get("risk.atr_stop_enabled", str(d.atr_stop_enabled))
            == "True",
            atr_stop_multiple=float(
                self._store.get("risk.atr_stop_multiple", str(d.atr_stop_multiple))
            ),
            trailing_stop_enabled=self._store.get(
                "risk.trailing_stop_enabled", str(d.trailing_stop_enabled)
            )
            == "True",
            trailing_stop_multiple=float(
                self._store.get("risk.trailing_stop_multiple", str(d.trailing_stop_multiple))
            ),
            atr_window=int(self._store.get("risk.atr_window", str(d.atr_window))),
        )

    def set_risk_policy(self, policy: RiskPolicy) -> None:
        self._store.set("risk.max_position_weight", str(policy.max_position_weight))
        self._store.set("risk.stop_loss_pct", str(policy.stop_loss_pct))
        self._store.set("risk.daily_loss_limit_pct", str(policy.daily_loss_limit_pct))
        self._store.set(
            "risk.max_concurrent_positions", str(policy.max_concurrent_positions)
        )
        self._store.set("risk.trading_enabled", str(policy.trading_enabled))
        self._store.set("risk.sell_enabled", str(policy.sell_enabled))
        self._store.set("risk.atr_stop_enabled", str(policy.atr_stop_enabled))
        self._store.set("risk.atr_stop_multiple", str(policy.atr_stop_multiple))
        self._store.set("risk.trailing_stop_enabled", str(policy.trailing_stop_enabled))
        self._store.set("risk.trailing_stop_multiple", str(policy.trailing_stop_multiple))
        self._store.set("risk.atr_window", str(policy.atr_window))

    def get_kis_credentials(self) -> KISCredentials:
        d = KISCredentials()
        return KISCredentials(
            kis_env=self._store.get("kis.env", d.kis_env),
            app_key=self._store.get("kis.app_key", d.app_key) or "",
            app_secret=self._store.get("kis.app_secret", d.app_secret) or "",
            account_no=self._store.get("kis.account_no", d.account_no) or "",
            account_product_cd=self._store.get(
                "kis.account_product_cd", d.account_product_cd
            )
            or "",
        )

    def set_kis_credentials(self, creds: KISCredentials) -> None:
        self._store.set("kis.env", creds.kis_env)
        self._store.set("kis.app_key", creds.app_key, secret=True)
        self._store.set("kis.app_secret", creds.app_secret, secret=True)
        self._store.set("kis.account_no", creds.account_no, secret=True)
        self._store.set("kis.account_product_cd", creds.account_product_cd)

    def get_telegram_config(self) -> TelegramConfig:
        d = TelegramConfig()
        return TelegramConfig(
            bot_token=self._store.get("telegram.bot_token", d.bot_token) or "",
            chat_id=self._store.get("telegram.chat_id", d.chat_id) or "",
        )

    def set_telegram_config(self, cfg: TelegramConfig) -> None:
        self._store.set("telegram.bot_token", cfg.bot_token, secret=True)
        self._store.set("telegram.chat_id", cfg.chat_id)

    def get_telegram_offset(self) -> int:
        """텔레그램에서 '여기까지 읽었다'고 남겨 둔 표시.

        텔레그램은 이 표시를 우리가 올려 줄 때까지 같은 메시지를 계속 준다.
        안 남기면 **워크플로가 돌 때마다 어제 명령이 다시 실행된다.**"""
        try:
            return int(self._store.get("telegram.update_offset", "0") or 0)
        except ValueError:
            return 0

    def set_telegram_offset(self, offset: int) -> None:
        self._store.set("telegram.update_offset", str(int(offset)))

    # ── KIS 접근토큰 보관 ──────────────────────────────────────
    #
    # KIS는 **토큰 발급을 자주 하면 403으로 막는다.** 그런데 토큰을 메모리에만
    # 두면 워크플로가 돌 때마다 새 프로세스라 매번 새로 발급받게 된다.
    #
    # 2026-08-25에 이것 때문에 세 번 막혔다. 그중 하나는 **승인 매수**였고,
    # 주문이 한 건도 안 나간 채 끝났다. 평소처럼 하루 두세 번이면 안 걸리지만
    # 손볼 일이 생겨 연달아 실행하면 그날 매매가 통째로 막힌다.
    #
    # 발급받은 토큰은 보통 24시간 쓸 수 있다. DB에 두면 그동안 재사용된다.
    # n8n 쪽은 이미 그렇게 하고 있었고(20시간 캐시) 파이썬 쪽만 안 했다.
    #
    # 토큰은 비밀값이다. app_secret과 같은 자리에 같은 방식으로 넣는다.

    def get_kis_token(self) -> tuple[str, float]:
        """(토큰, 만료시각 epoch)를 돌려준다. 없으면 ("", 0.0)."""
        토큰 = self._store.get("kis.access_token", "") or ""
        try:
            만료 = float(self._store.get("kis.token_expires_at", "0") or 0)
        except ValueError:
            만료 = 0.0
        return 토큰, 만료

    def set_kis_token(self, token: str, expires_at: float) -> None:
        self._store.set("kis.access_token", token, secret=True)
        self._store.set("kis.token_expires_at", str(expires_at))

    def get_strategy_selection(self) -> StrategySelection:
        """전략 여러 개를 걸 수 있게 바뀌었지만 옛 키(strategy.active_key)도
        읽는다. 운영 DB에는 그게 이미 들어 있고, 컬럼 하나 바꾸느라 지금
        돌고 있는 설정이 초기화되면 안 된다."""
        d = StrategySelection()
        saved = self._store.get("strategy.active_keys", "")
        if saved:
            keys = tuple(k.strip() for k in saved.split(",") if k.strip())
        else:
            keys = (self._store.get("strategy.active_key", d.active_key),)
        combine = self._store.get("strategy.combine", d.combine).upper()
        # 파는 쪽은 안 걸려 있는 것이 기본이다. 빈 값이면 사는 쪽이 양쪽을
        # 다 맡는다. 지금까지의 동작이 그것이다.
        판것 = self._store.get("strategy.sell_keys", "")
        sell_keys = tuple(k.strip() for k in 판것.split(",") if k.strip())
        return StrategySelection(
            active_keys=keys or d.active_keys,
            combine=combine if combine in ("OR", "AND") else d.combine,
            sell_keys=sell_keys,
        )

    def set_strategy_selection(self, selection: StrategySelection) -> None:
        self._store.set("strategy.active_keys", ",".join(selection.active_keys))
        self._store.set("strategy.combine", selection.combine)
        # 빈 문자열로 지운다. 안 지우면 매도만 따로 걸었다가 되돌릴 때
        # 옛 값이 남아 계속 따로 돈다.
        self._store.set("strategy.sell_keys", ",".join(selection.sell_keys))
        # 옛 키도 같이 갱신한다. 아직 이 값을 읽는 자리(리포트의 '대표 전략')가
        # 남아 있어서, 안 맞춰 두면 화면과 리포트가 서로 다른 말을 한다.
        self._store.set("strategy.active_key", selection.active_key)

    def get_strategy_config(self) -> StrategyConfig:
        """Factor 가중치·ON/OFF·임계값. 저장된 게 없으면 V1 기본값을 쓴다."""
        return StrategyConfig.from_json(self._store.get("strategy.factor_config", ""))

    def set_strategy_config(self, config: StrategyConfig) -> None:
        """JSON 한 덩어리로 저장한다. 필드마다 키를 나누면 Factor를 추가할
        때마다 스키마를 손대야 하고, 변경 이력도 조각나 읽기 어려워진다."""
        self._store.set("strategy.factor_config", config.to_json())

    def undecryptable_secret_keys(self) -> list[str]:
        """지금 MUWON_MASTER_KEY로 열리지 않는 비밀값 키 목록. 마스터키를 새로
        발급한 뒤 DB에 옛 키로 암호화된 값이 남아 있으면 여기 잡힌다. 해당
        값을 다시 저장하면 새 키로 재암호화되어 사라진다."""
        return self._store.undecryptable_secret_keys()

    def get_settings_history(self, limit: int = 100) -> list[SettingHistoryEntry]:
        entries = []
        for row in self._store.get_history(limit=limit):
            if not row.is_secret:
                old_value, new_value, decrypted = row.old_value, row.new_value, True
            else:
                old_value = self._store.try_decrypt(row.old_value)
                new_value = self._store.try_decrypt(row.new_value)
                # 마스터키가 있어도 옛 키로 암호화된 값은 못 읽는다.
                # 키 보유 여부가 아니라 실제 복호화 성공 여부로 판단한다.
                decrypted = self._store.has_master_key and new_value is not None
            entries.append(
                SettingHistoryEntry(
                    key=row.key,
                    old_value=old_value,
                    new_value=new_value,
                    is_secret=row.is_secret,
                    changed_at=row.changed_at,
                    decrypted=decrypted,
                )
            )
        return entries


def build_settings_service(
    database_url: str | None = None, master_key: str | None = None
) -> SettingsService:
    from muwon.config import bootstrap_settings
    from muwon.db.session import make_session_factory

    session_factory = make_session_factory(database_url or bootstrap_settings.database_url)
    store = SettingsStore(
        session_factory, master_key=master_key if master_key is not None else bootstrap_settings.master_key
    )
    return SettingsService(store)
