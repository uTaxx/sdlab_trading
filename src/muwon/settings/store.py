import time
from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import InvalidToken
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from muwon.db.models import AppSettingHistoryRow, AppSettingRow
from muwon.settings.crypto import decrypt, encrypt


@dataclass
class HistoryRow:
    key: str
    old_value: str | None
    new_value: str
    is_secret: bool
    changed_at: datetime


class SettingsStore:
    """DB에 저장된 키-값 설정을 읽고 쓰는 저수준 저장소.

    운영 봇 프로세스와 (미래의) 대시보드 프로세스가 같은 DB를 공유할 수
    있으므로, 로컬 캐시는 TTL이 지나면 자동 갱신하고, 이 프로세스에서 직접
    set()한 경우에는 즉시 캐시를 갱신한다.
    """

    def __init__(
        self,
        session_factory: sessionmaker,
        master_key: str | None = None,
        cache_ttl_seconds: float = 5.0,
    ):
        self._session_factory = session_factory
        self._master_key = master_key or None
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[str, bool]] = {}
        self._cache_loaded_at: float = 0.0

    @property
    def has_master_key(self) -> bool:
        return self._master_key is not None

    def try_decrypt(self, value: str | None) -> str | None:
        """복호화 가능하면 평문을, 값이 없거나·마스터키가 없거나·지금 마스터키로는
        열리지 않으면(=다른 키로 암호화된 값) None을 돌려준다."""
        if value is None or not self._master_key:
            return None
        try:
            return decrypt(value, self._master_key)
        except InvalidToken:
            return None

    def _refresh_cache_if_stale(self) -> None:
        if time.time() - self._cache_loaded_at < self._cache_ttl:
            return
        with self._session_factory() as session:
            rows = session.query(AppSettingRow).all()
            self._cache = {row.key: (row.value, row.is_secret) for row in rows}
        self._cache_loaded_at = time.time()

    def get(self, key: str, default: str | None = None) -> str | None:
        self._refresh_cache_if_stale()
        if key not in self._cache:
            return default
        stored_value, is_secret = self._cache[key]
        if not is_secret:
            return stored_value
        if not self._master_key:
            raise RuntimeError(
                f"'{key}' 값은 암호화되어 있는데 MUWON_MASTER_KEY가 설정되지 "
                "않아 복호화할 수 없습니다."
            )
        try:
            return decrypt(stored_value, self._master_key)
        except InvalidToken:
            # MUWON_MASTER_KEY를 새로 발급했는데 DB에는 옛 키로 암호화된 값이
            # 남아 있는 상황: 실제로 겪었다. 여기서 예외를 그대로 올리면
            # 대시보드 전체가 죽고 다른 정상 설정까지 못 보게 되므로, 못 읽는
            # 값 하나로 처리하고 넘어간다(경고는 남긴다). 어느 키가 안 열리는지는
            # undecryptable_secret_keys()로 조회할 수 있고, 해당 값을 다시
            # 저장하면 새 키로 재암호화되어 복구된다.
            logger.warning(
                f"'{key}'를 현재 MUWON_MASTER_KEY로 복호화할 수 없습니다 "
                "(다른 키로 암호화된 값). 해당 값을 다시 저장하면 복구됩니다."
            )
            return default

    def undecryptable_secret_keys(self) -> list[str]:
        """지금 마스터키로 열리지 않는 비밀값 키 목록: 화면에 "이 값들은 다시
        입력해야 한다"고 알려주기 위한 용도."""
        self._refresh_cache_if_stale()
        if not self._master_key:
            return []
        broken = []
        for key, (stored_value, is_secret) in self._cache.items():
            if not is_secret:
                continue
            try:
                decrypt(stored_value, self._master_key)
            except InvalidToken:
                broken.append(key)
        return sorted(broken)

    def set(self, key: str, value: str, secret: bool = False) -> None:
        if secret and not self._master_key:
            raise RuntimeError(
                "비밀값을 저장하려면 MUWON_MASTER_KEY 환경변수가 필요합니다."
            )
        stored_value = encrypt(value, self._master_key) if secret else value
        with self._session_factory() as session:
            row = session.get(AppSettingRow, key)
            old_value = row.value if row is not None else None
            if row is None:
                session.add(AppSettingRow(key=key, value=stored_value, is_secret=secret))
            else:
                row.value = stored_value
                row.is_secret = secret
            if old_value != stored_value:
                session.add(
                    AppSettingHistoryRow(
                        key=key, old_value=old_value, new_value=stored_value, is_secret=secret
                    )
                )
            session.commit()
        self._cache[key] = (stored_value, secret)
        self._cache_loaded_at = time.time()

    def get_history(self, limit: int = 100) -> list[HistoryRow]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(AppSettingHistoryRow)
                .order_by(AppSettingHistoryRow.changed_at.desc())
                .limit(limit)
            ).all()
            return [
                HistoryRow(
                    key=r.key,
                    old_value=r.old_value,
                    new_value=r.new_value,
                    is_secret=r.is_secret,
                    changed_at=r.changed_at,
                )
                for r in rows
            ]
