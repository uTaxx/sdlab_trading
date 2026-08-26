"""전략을 바꾸는 자리.

**미리보기가 통과했다고 실제 적용이 통과한 것이 아니다.** 처음 만들었을 때
`StrategySelection(active_key=...)`로 썼는데, 생성자는 `active_keys`(튜플)를
받고 `active_key`는 읽기 전용 속성이다. 미리보기 경로는 그 줄을 안 지나가서
로컬에서도 CI 미리보기에서도 안 걸렸고, 진짜로 바꾸는 실행에서만 터졌다.

그래서 여기서는 **실제로 바꾸고 다시 읽어서** 확인한다.
"""

import pytest

from muwon.settings.schema import StrategySelection
from muwon.settings.service import SettingsService


class 곳간:
    """DB 없이 돌리는 최소 저장소. get/set만 있으면 SettingsService가 돈다."""

    def __init__(self):
        self.값 = {}

    def get(self, 키, 기본=""):
        return self.값.get(키, 기본)

    def set(self, 키, 값):
        self.값[키] = 값


@pytest.fixture
def service():
    return SettingsService(곳간())


def test_바꾼_뒤_다시_읽으면_바뀌어_있다():
    """이 파일이 있는 이유다. 바꿨다고 찍어 놓고 값이 그대로면,
    화면은 새 전략이라고 하고 매매는 옛 규칙으로 돈다."""
    s = SettingsService(곳간())
    s.set_strategy_selection(StrategySelection(active_keys=("volume_surge_5d_ma20",)))

    assert s.get_strategy_selection().active_key == "volume_surge_5d_ma20"


def test_생성자는_active_key를_안_받는다():
    """옛 이름으로 쓰면 조용히 넘어가는 것이 아니라 터진다는 것을 못 박는다."""
    with pytest.raises(TypeError):
        StrategySelection(active_key="volume_surge_5d_ma20")


def test_대표_전략은_첫_번째다():
    고름 = StrategySelection(active_keys=("가", "나"))

    assert 고름.active_key == "가"


def test_하나도_안_걸려_있으면_빈_문자열이다():
    """빈 튜플에 [0]을 하면 터진다. 화면에 쓸 값이라 안 터져야 한다."""
    assert StrategySelection(active_keys=()).active_key == ""


def test_옛_키로_저장된_값도_읽는다():
    """운영 DB에는 strategy.active_key만 들어 있던 시절의 값이 남아 있다.
    컬럼 하나 바꾸느라 지금 돌고 있는 설정이 초기화되면 안 된다."""
    창고 = 곳간()
    창고.set("strategy.active_key", "volume_surge_5d")

    assert SettingsService(창고).get_strategy_selection().active_key == "volume_surge_5d"
