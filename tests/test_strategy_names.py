"""전략 이름을 화면에 내보내는 자리.

## 왜 시험하나

이름은 조용히 틀린다. `display_name`은 파라미터까지 달고 있어서 폰 폭에서
두 줄로 넘치고, 목록에 늘어놓으면 서로 구별이 안 된다. 짧은 이름을 안 붙인
전략이 하나 생기면 그 줄만 길어지는데, 그건 화면을 열어 보기 전에는
안 보인다.
"""

import re

import pytest

from muwon.strategy.registry import REGISTRY, get_definition, list_definitions


def test_모든_전략에_짧은_한글_이름이_있다():
    빠진것 = [d.key for d in REGISTRY if not d.짧은이름]
    assert not 빠진것, f"짧은이름이 없는 전략: {빠진것}"


def test_짧은이름이_없으면_긴_이름을_쓴다():
    """새 전략을 등록할 때 안 적어도 화면이 비지는 않아야 한다."""
    from muwon.strategy.registry import StrategyDefinition

    d = StrategyDefinition(key="x", display_name="긴 이름", description="", factory=lambda: None)
    assert d.화면이름 == "긴 이름"


def test_짧은이름은_서로_다르다():
    """같은 이름이 둘이면 목록에서 어느 것을 고른 건지 알 수 없다."""
    이름들 = [d.화면이름 for d in REGISTRY]
    겹침 = {이 for 이 in 이름들 if 이름들.count(이) > 1}
    assert not 겹침, f"이름이 겹치는 전략: {겹침}"


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda d: d.key)
def test_짧은이름이_화면에_들어갈_길이다(정의):
    """폰 폭(390px)에서 한 줄에 들어가야 한다. 20자를 넘으면 접힌다."""
    assert len(정의.화면이름) <= 20, f"{정의.key}: {정의.화면이름}"


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda d: d.key)
def test_화면에_내보내는_글에_전략_키가_그대로_들어가지_않는다(정의):
    """`volume_surge_5d` 같은 키가 설명에 박혀 있으면, 이름을 한글로 바꿔도
    설명만 영어로 남는다. 실제로 그런 줄이 있었다."""
    키들 = [d.key for d in REGISTRY]
    박힌것 = [k for k in 키들 if k in 정의.description or k in 정의.화면이름]
    assert not 박힌것, f"{정의.key}의 설명에 전략 키가 그대로 있습니다: {박힌것}"


def test_대시보드가_읽는_JSON에_짧은_이름이_실린다():
    from tests.scripts_for_test import 전략설명

    줄들 = {r["키"]: r for r in 전략설명()}
    assert 줄들["volume_surge_5d_ma20"]["이름"] == get_definition("volume_surge_5d_ma20").화면이름
    # 자세한 이름도 같이 실어야 한다. 파라미터를 확인하고 싶을 때가 있다.
    assert 줄들["volume_surge_5d_ma20"]["자세한이름"]


def test_계열은_한글이다():
    """목록을 계열로 묶어 보여 주므로 계열도 화면에 나온다."""
    for d in list_definitions():
        assert not re.search(r"[A-Za-z]", d.category), f"{d.key}: {d.category}"
