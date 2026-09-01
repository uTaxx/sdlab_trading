"""주문 검증 로그의 종목명 조회가 검증 자체를 막지 않는지 확인한다.

이름은 로그를 읽기 좋게 만드는 부수적인 값이다. 시트를 못 읽는다는
이유로 주문 경로 검증이 멈추면 본말이 뒤바뀐다. 그래서 어떤 실패든
빈 값으로 돌아와야 한다(호출부가 종목코드로 대체한다).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

루트 = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def 모듈():
    sys.path.insert(0, str(루트 / "src"))
    spec = importlib.util.spec_from_file_location(
        "verify_kis_order", 루트 / "scripts" / "verify_kis_order.py"
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_인증정보가_없으면_빈값(모듈, monkeypatch):
    monkeypatch.delenv("GDRIVE_SA_KEY_JSON", raising=False)
    assert 모듈._시트이름("403870") == ""


def test_시트를_못_읽어도_빈값(모듈, monkeypatch):
    """서비스 계정 키가 쓰레기여도 예외가 밖으로 새면 안 된다."""
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", "이건 JSON이 아니다")
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "없는폴더")
    assert 모듈._시트이름("403870") == ""
