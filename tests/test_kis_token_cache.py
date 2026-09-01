"""발급받은 KIS 토큰을 프로세스 밖에 남겨 두는지 고정한다.

2026-08-25에 KIS가 토큰 발급을 세 번 막았다(403). 워크플로가 돌 때마다
새 프로세스라 매번 새로 발급받았기 때문이다. 그중 하나가 **승인 매수**였고
주문이 한 건도 안 나간 채 끝났다.

평소처럼 하루 두세 번이면 안 걸린다. 손볼 일이 생겨 연달아 실행하는 날
: 즉 이미 뭔가 잘못된 날: 에 걸린다. 그게 제일 나쁜 때다.
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
    """이 시험이 사고의 핵심이다. 이어 쓰지 못해 매번 발급받았다."""
    보관소 = _보관소("남아있던토큰", time.time() + 3600)

    assert _client(보관소)._ensure_token() == "남아있던토큰"
    assert 보관소.저장횟수 == 0, "이어 쓸 수 있는데 새로 발급받았다"


def test_만료된_토큰은_이어_쓰지_않는다():
    """만료된 것을 그대로 쓰면 401로 막힌다. 403보다 나을 것이 없다."""
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
    # 만료 60초 앞당겨 잡는다. 쓰는 도중에 만료되면 그 회차가 통째로 죽는다.
    assert 보관소.만료 <= time.time() + 86400 - 60


# ── 만료 시각이 남아 있어도 토큰이 죽을 수 있다 ────────────────────────


class _응답:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}
        self.text = "{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        """토큰 발급 경로가 부른다. 여기서는 늘 정상이다."""
        return


_죽음 = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
_살아있음 = {"rt_cd": "0", "output": {"nrcvb_buy_qty": "42"}}


def _토큰발급되는_client(보관소, 응답들):
    """토큰 발급은 성공하고, 업무 요청은 주어진 순서대로 답하는 클라이언트."""
    c = KISClient(app_key="k", app_secret="s", account_no="1", token_store=보관소)
    남은것 = list(응답들)
    보낸헤더 = []

    def 발급(url, **kwargs):
        return _응답({"access_token": "새토큰", "expires_in": 86400})

    def 업무(url, **kwargs):
        보낸헤더.append(kwargs["headers"]["authorization"])
        return 남은것.pop(0)

    c._post = 발급  # type: ignore[method-assign]
    c._get = 업무  # type: ignore[method-assign]
    return c, 보낸헤더


def test_KIS가_토큰이_죽었다고_하면_새로_받아_다시_보낸다():
    """**2026-08-26 아침을 막는 시험이다.**

    같은 앱키로 토큰을 새로 발급하면 앞의 것이 바로 죽는다. n8n과 파이썬이
    같은 앱키를 쓰므로, 우리 시계에 몇 시간 남아 있어도 죽어 있을 수 있다.

    그날 죽은 토큰으로 매수가능조회를 불렀고, KIS의 거부가 '0주'로 둔갑해
    승인한 두 종목이 통째로 안 팔렸다."""
    보관소 = _보관소("죽은토큰", time.time() + 3600)   # 시계로는 아직 한 시간 남았다
    c, 헤더들 = _토큰발급되는_client(보관소, [_응답(_죽음), _응답(_살아있음)])

    assert c.get_orderable("103140", 150000) == 42
    assert 헤더들 == ["Bearer 죽은토큰", "Bearer 새토큰"], "죽은 토큰으로 한 번, 새 토큰으로 다시"
    assert 보관소.토큰 == "새토큰", "다음 프로세스가 죽은 것을 또 집으면 안 된다"


def test_한_번만_다시_보낸다():
    """새 토큰으로도 죽었다고 하면 그건 다른 문제다. 무한히 돌면 안 된다."""
    보관소 = _보관소("죽은토큰", time.time() + 3600)
    c, 헤더들 = _토큰발급되는_client(보관소, [_응답(_죽음), _응답(_죽음)])

    assert c.get_orderable("103140", 150000) == -1, "모르는 것은 모른다고 해야 한다"
    assert len(헤더들) == 2


def test_멀쩡한_토큰이면_다시_안_받는다():
    보관소 = _보관소("멀쩡한토큰", time.time() + 3600)
    c, 헤더들 = _토큰발급되는_client(보관소, [_응답(_살아있음)])

    assert c.get_orderable("103140", 150000) == 42
    assert 헤더들 == ["Bearer 멀쩡한토큰"]
    assert 보관소.저장횟수 == 0
