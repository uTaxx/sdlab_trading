"""**설정 객체를 잘못 만드는 실수**를 만드는 자리에서 잡는다.

`SettingsService`는 `SettingsStore`를 받아야 한다. 사이에 Store가 있어야 하고,
그게 비밀값을 푸는 열쇠를 들고 있다. 그런데 `session_factory`를 바로 넘겨도
생성은 조용히 지나가고, 한참 뒤 첫 읽기에서 이렇게 터진다.

    AttributeError: 'sessionmaker' object has no attribute 'get'

무엇을 잘못 넘겼는지가 안 보이는 자리다. 2026-08-20~25에 이 실수가 두 곳에
있었고 둘 다 오래 안 보였다.

  - **30분봉 수집** — 일곱 번 내리 실패. 한국투자증권은 분봉을 당일치만 주므로
    그 엿새치는 **영영 못 받는다.** 게다가 뒤따르는 업로드 단계가 파일 없음으로
    또 죽으면서 진짜 원인을 로그 맨 아래로 밀어냈다.
  - **시장·섹터 리포트** — 여덟 번 내리 텔레그램 전송 실패. try/except 안이라
    **워크플로는 여덟 번 다 초록불이었고** 리포트는 폰에 한 번도 안 왔다.

조용히 성공한 척하는 실패가 이 저장소에서 제일 비싸다.
"""

from __future__ import annotations

import pytest

from muwon.settings.service import SettingsService


class _가짜세션팩토리:
    """sessionmaker처럼 부르면 세션을 주지만 get/set은 없는 것."""

    def __call__(self):
        raise AssertionError("여기까지 오면 안 된다")


class _가짜보관소:
    def get(self, key, default=""):
        return default

    def set(self, key, value, secret=False):
        pass


def test_session_factory를_바로_주면_만들_때_막는다():
    with pytest.raises(TypeError) as e:
        SettingsService(_가짜세션팩토리())

    말 = str(e.value)
    assert "SettingsStore" in 말
    assert "build_settings_service" in 말, "무엇을 대신 쓰라는지 알려 줘야 한다"


def test_읽기까지_안_가고_생성에서_터진다():
    """예전에는 get_kis_credentials()를 부를 때까지 아무 일도 안 일어났다.
    그 사이에 무엇을 잘못 넘겼는지의 단서가 다 사라진다."""
    with pytest.raises(TypeError):
        SettingsService(_가짜세션팩토리())


def test_get과_set이_있으면_받아준다():
    """시험용 가짜 보관소가 계속 되게 두려면 클래스 이름이 아니라
    할 줄 아는 것으로 판단해야 한다."""
    service = SettingsService(_가짜보관소())

    assert service.get_risk_policy() is not None


def test_None을_주면_막는다():
    with pytest.raises(TypeError):
        SettingsService(None)
