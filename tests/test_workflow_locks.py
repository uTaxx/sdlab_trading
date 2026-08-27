"""상태 DB를 고치는 워크플로가 서로 겹치지 않는가.

## 왜 시험하나

상태 DB는 구글드라이브의 **파일 하나**다. 워크플로마다 실행 시작에
내려받고 끝에 올린다. 두 실행이 겹치면 나중 것이 앞 것을 통째로 덮는다.
사이에 낸 주문 기록이 통째로 사라지는 것이다.

지금까지는 겹칠 일이 드물어서 안 드러났다. 장중 손절 감시가 5~10분마다
돌기 시작하면 겹칠 확률이 확 올라간다.

**조용히 덮인다.** 두 실행 다 초록불이고, 없어진 기록은 아무 데도 안 남는다.
그래서 사람이 기억하는 것으로는 못 막고 여기서 막는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_워크플로 = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))

#: 상태 DB를 고치는 워크플로는 전부 이 그룹이어야 한다.
DB잠금 = "state-write"


def _읽기(경로: Path) -> dict:
    return yaml.safe_load(경로.read_text(encoding="utf-8")) or {}


def _DB를올리나(경로: Path) -> bool:
    return "gdrive_sync.py upload" in 경로.read_text(encoding="utf-8")


DB쓰는것 = [ㄱ for ㄱ in _워크플로 if _DB를올리나(ㄱ)]


def test_DB를_고치는_워크플로가_있다():
    """이 시험 자체가 무의미해지는 것을 막는다 — 찾는 방법이 틀리면
    아무것도 안 검사하면서 통과한다."""
    assert len(DB쓰는것) >= 5, [ㄱ.name for ㄱ in DB쓰는것]


@pytest.mark.parametrize("경로", DB쓰는것, ids=lambda p: p.name)
def test_DB를_고치면_같은_자물쇠를_쓴다(경로):
    묶음 = _읽기(경로).get("concurrency")

    assert 묶음, f"{경로.name}에 concurrency가 없습니다. 겹치면 DB가 덮입니다."
    assert 묶음.get("group") == DB잠금, (
        f"{경로.name}의 그룹이 '{묶음.get('group')}'입니다. "
        f"DB를 고치는 워크플로는 전부 '{DB잠금}'이어야 합니다."
    )


@pytest.mark.parametrize("경로", DB쓰는것, ids=lambda p: p.name)
def test_앞_실행을_취소하지_않는다(경로):
    """cancel-in-progress가 켜져 있으면 DB를 올리는 중에 죽을 수 있다.
    반쯤 올라간 DB가 남는 쪽이 기다리는 쪽보다 훨씬 나쁘다."""
    묶음 = _읽기(경로).get("concurrency") or {}

    assert 묶음.get("cancel-in-progress") is False, 경로.name


def test_장중_손절_감시는_판_것이_있을_때만_DB를_올린다():
    """하루에 수십 번 도는 자리다. 안 바뀐 DB를 매번 올리면 그 사이 다른
    실행이 쓴 것을 옛 것으로 덮을 수 있다."""
    글 = (Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "watch-stops.yml").read_text(encoding="utf-8")

    올리는곳 = 글.index("gdrive_sync.py upload")
    앞부분 = 글[:올리는곳]

    assert "hashFiles('db-changed')" in 앞부분, (
        "DB 올리기에 조건이 없습니다. 판 것이 없을 때도 올리면 안 됩니다."
    )
