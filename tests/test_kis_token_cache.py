"""발급받은 KIS 토큰을 프로세스 밖에 남겨 두는지 고정한다.

2026-08-25에 KIS가 토큰 발급을 세 번 막았다(403). 워크플로가 돌 때마다
새 프로세스라 매번 새로 발급받았기 때문이다. 그중 하나가 **승인 매수**였고
주문이 한 건도 안 나간 채 끝났다.

평소처럼 하루 두세 번이면 안 걸린다. 손볼 일이 생겨 연달아 돌리는 날
— 즉 이미 뭔가 잘못된 날 — 에 걸린다. 그게 제일 나쁜 때다.
"""

from __future__ import annotations

import time

from muwon.data.kis_client import KISClient


class _보관소:
    """SettingsService에서 토큰 부분만 흉내 낸다."""

    def __init__(self, 토큰: str = "", 만료: float = 0.0):
        self.토큰, self.만료 = 토큰, 만료
        self.저장횟수 = 0

    def get_kis_token(self) -> tuple[str, float]:
        return self.토큰, self.만료

    def set_kis_token(self, token: str, expires_at: float) -> None:
        self.토큰, self.만료 = token, expires_at
        self.저장횟수 += 1


def _client(store=None) -> KISClient:
    return KISClient(app_key="k", app_secret="s", account_no="1", token_store=store)


def test_보관된_토큰을_새_프로세스가_이어_쓴다():
    """이 시험이 사고의 핵심이다 — 이어 쓰지 못해 매번 발급받았다."""
    보관소 = _보관소("남아있던토큰", time.time() + 3600)

    assert _client(보관소)._ensure_token() == "남아있던토큰"
    assert 보관소.저장횟수 == 0, "이어 쓸 수 있는데 새로 발급받았다"


def test_만료된_토큰은_이어_쓰지_않는다():
    """만료된 것을 그대로 쓰면 401로 막힌다 — 403보다 나을 것이 없다."""
    보관소 = _보관소("낡은토큰", time.time() - 10)
    c = _client(보관소)

    assert c._access_token == "낡은토큰"
    assert time.time() >= c._token_expires_at, "만료로 판정돼야 한다"


def test_보관소가_없으면_예전처럼_돈다():
    """테스트와 일회성 스크립트는 보관 없이 도는 편이 간단하다."""
    c = _client(None)

    assert c._access_token is None
    assert c._token_expires_at == 0.0


def test_보관소가_비어_있으면_발급받을_준비를_한다():
    c = _client(_보관소())

    assert c._access_token is None


def test_새로_발급받으면_보관한다(monkeypatch):
    보관소 = _보관소()
    c = _client(보관소)

    class _응답:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "새토큰", "expires_in": 86400}

    monkeypatch.setattr(c, "_post", lambda *a, **k: _응답())

    assert c._ensure_token() == "새토큰"
    assert 보관소.토큰 == "새토큰"
    assert 보관소.저장횟수 == 1
    # 만료 60초 앞당겨 잡는다 — 쓰는 도중에 만료되면 그 회차가 통째로 죽는다.
    assert 보관소.만료 <= time.time() + 86400 - 60
