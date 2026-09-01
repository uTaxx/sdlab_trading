from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BootstrapSettings(BaseSettings):
    """DB에 접속하기 위해 최소한으로 필요한, .env로만 관리하는 값들.

    KIS 인증정보/텔레그램/리스크 정책 등 실제로 자주 바뀌는 값은 여기 두지
    않는다. DB의 app_settings 테이블에 저장되고, SettingsService를 통해
    CLI(scripts/configure.py) 또는 (Phase 2+) 대시보드에서 재시작 없이
    변경한다. 설계 배경은 docs/config_architecture.md 참고.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "sqlite:///./muwon.db"
    master_key: str = Field(default="", validation_alias="MUWON_MASTER_KEY")


bootstrap_settings = BootstrapSettings()
