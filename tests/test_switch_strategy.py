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
    """DB 없이 실행하는 최소 저장소. get/set만 있으면 SettingsService가 돈다."""

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


# ── 매수와 매도를 따로 거는 것 ──────────────────────────────────────────


def test_매도를_따로_걸고_다시_읽으면_남아_있다():
    s = SettingsService(곳간())
    s.set_strategy_selection(
        StrategySelection(active_keys=("volume_surge_5d",), sell_keys=("ma_rsi_v1",))
    )

    뒤 = s.get_strategy_selection()
    assert 뒤.active_key == "volume_surge_5d"
    assert 뒤.sell_keys == ("ma_rsi_v1",)
    assert 뒤.매도따로


def test_매도를_지우면_지워진다():
    """빈 값으로 덮어쓰지 않으면 옛 값이 남아 계속 따로 돈다.
    되돌렸다고 생각하는데 안 되돌아간 상태가 제일 나쁘다."""
    창고 = 곳간()
    s = SettingsService(창고)
    s.set_strategy_selection(
        StrategySelection(active_keys=("volume_surge_5d",), sell_keys=("ma_rsi_v1",))
    )
    s.set_strategy_selection(StrategySelection(active_keys=("volume_surge_5d",)))

    뒤 = s.get_strategy_selection()
    assert 뒤.sell_keys == ()
    assert not 뒤.매도따로


def test_안_걸었으면_따로가_아니다():
    assert not StrategySelection(active_keys=("가",)).매도따로


def test_매수와_매도가_같으면_따로가_아니다():
    """같은 것을 양쪽에 적어 둔 것은 안 나눈 것과 같다."""
    assert not StrategySelection(active_keys=("가",), sell_keys=("가",)).매도따로


def test_설명에_양쪽이_다_보인다():
    """사는 쪽만 보이면 '왜 저 규칙으로 팔렸지'를 설명할 수 없다."""
    말 = StrategySelection(active_keys=("volume_surge_5d",), sell_keys=("ma_rsi_v1",)).describe()

    assert "volume_surge_5d" in 말 and "ma_rsi_v1" in 말
    assert "매수" in 말 and "매도" in 말


def test_따로_안_걸었으면_설명이_예전과_같다():
    assert StrategySelection(active_keys=("volume_surge_5d",)).describe() == "volume_surge_5d"


def test_옛_DB에는_매도_키가_아예_없다():
    """운영 DB에는 이 칸이 없던 시절의 값이 들어 있다. 없으면 빈 것으로
    읽혀야 하고, 그러면 지금까지와 똑같이 돈다."""
    창고 = 곳간()
    창고.set("strategy.active_keys", "volume_surge_5d")

    뒤 = SettingsService(창고).get_strategy_selection()
    assert 뒤.sell_keys == ()
    assert not 뒤.매도따로


# ── 섞은 조합의 성적 ──────────────────────────────────────────────
#
# 안 재 본 조합을 숫자 없이 내놓으면 재 본 것처럼 읽힌다. 반대로 재 봤는데
# "안 쟀습니다"라고 하면 사람이 같은 실험을 또 실행한다. 둘 다 막는다.

import importlib.util
import sys
from pathlib import Path

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "switch_strategy.py"
_스펙 = importlib.util.spec_from_file_location("switch_strategy_for_test", _경로)
switch_strategy = importlib.util.module_from_spec(_스펙)
sys.modules["switch_strategy_for_test"] = switch_strategy
_스펙.loader.exec_module(switch_strategy)


def _가짜표(줄들):
    from muwon.analysis.report_card import ReportCard

    return ReportCard(
        기준={"측정일": "2026-08-27"}, 전략=[], 조합=[], 배운것=[], 매수매도분리=줄들
    )


def _줄(키):
    from muwon.analysis.report_card import Row

    return Row(
        키=키, 이름=키, 계열="섞은 것", 평균=20.0, 최악=-3.0, 샤프=1.1,
        낙폭=-15.0, 손익비=1.3, 거래=600, 판정="조건부", 한줄평="시험용 줄입니다.",
    )


def test_재_본_조합이면_숫자를_보여_준다(monkeypatch):
    import muwon.analysis.report_card as rc

    monkeypatch.setattr(rc, "load", lambda *a, **k: _가짜표([_줄("A>B")]))
    글 = switch_strategy._섞은성적("A", "B")

    assert "재 본 숫자가 있습니다" in 글
    assert "가장 나빴던 해 -3.0%" in 글
    assert "시험용 줄입니다." in 글


def test_안_재_본_조합이면_안_쟀다고_적는다(monkeypatch):
    import muwon.analysis.report_card as rc

    monkeypatch.setattr(rc, "load", lambda *a, **k: _가짜표([_줄("A>B")]))
    글 = switch_strategy._섞은성적("C", "D")

    assert "전략 평가 결과에 없습니다" in 글
    # 어떻게 재는지까지 적어야 사람이 다음 걸음을 안다.
    assert "mode=split" in 글
    assert "keys=C>D" in 글


def test_평가결과를_못_읽어도_안_터진다(monkeypatch):
    """전략을 바꾸는 일이 전략 평가 결과 파일 하나 때문에 막히면 안 된다."""
    import muwon.analysis.report_card as rc

    def 터짐(*a, **k):
        raise OSError("없는 파일")

    monkeypatch.setattr(rc, "load", 터짐)
    assert "못 읽었습니다" in switch_strategy._섞은성적("A", "B")
