"""뽑아 둔 JSON이 파이썬 원본과 어긋나지 않는지 본다.

원본(`glossary.py`·`strategy_rules.py`)을 고치고 JSON을 다시 안 뽑으면
**화면은 옛 설명을 계속 보여 준다.** 조용히 틀리는 쪽이라 막아야 한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
자료 = 뿌리 / "dashboard" / "자료"


def test_뽑아둔_자료가_파이썬_원본과_같다():
    """다르면 `python scripts/export_dashboard_data.py`를 돌리고 같이 커밋한다."""
    끝난것 = subprocess.run(
        [sys.executable, "scripts/export_dashboard_data.py", "--check"],
        cwd=뿌리, capture_output=True, text=True, check=False,
    )
    assert 끝난것.returncode == 0, 끝난것.stderr


def test_용어사전이_비어_있지_않다():
    낱말들 = json.loads((자료 / "용어사전.json").read_text(encoding="utf-8"))
    assert len(낱말들) >= 30
    # 뜻만 있고 읽는법이 없으면 "그래서 뭘 판단하나"를 알 수 없다.
    # 이 저장소가 용어를 다루는 방식이 그것이라 화면에서도 지킨다.
    빠진것 = [ㄴ["이름"] for ㄴ in 낱말들 if not ㄴ["읽는법"].strip()]
    assert not 빠진것, f"읽는 법이 없는 용어: {빠진것}"


def test_전략설명에_산다_규칙이_있다():
    전략들 = json.loads((자료 / "전략설명.json").read_text(encoding="utf-8"))
    assert len(전략들) >= 20
    설명없음 = [ㅈ["키"] for ㅈ in 전략들 if not ㅈ["설명있음"]]
    # 설명이 없는 전략이 있어도 막지는 않는다 — 다만 몇 개인지는 보이게 둔다.
    assert len(설명없음) <= len(전략들) // 2, f"설명 없는 전략이 너무 많다: {설명없음}"


def test_기준이름에_시트칸과_DB칸이_모두_있다():
    """변경 이력은 DB에서 나온다. 그래서 같은 값이 시트에서는 `stop_loss_pct`,
    DB에서는 `risk.stop_loss_pct`로 적힌다. 한쪽만 넣으면 화면의 절반이
    여전히 열쇠말 그대로 뜬다."""
    from tests.scripts_for_test import 기준이름

    표 = {ㄱ["열쇠"]: ㄱ for ㄱ in 기준이름()}

    assert 표["stop_loss_pct"]["이름"] == "손절선"
    assert 표["risk.stop_loss_pct"]["이름"] == "손절선"
    assert 표["strategy.active_keys"]["이름"] == "쓰는 전략"
    # 열쇠말이 그대로 남은 칸이 있으면 화면에서 그 줄만 영어로 뜬다.
    안옮긴것 = [열쇠 for 열쇠, ㄱ in 표.items() if ㄱ["이름"] == 열쇠]
    assert not 안옮긴것, f"사람이 읽을 이름이 없는 칸: {안옮긴것}"


def test_전략키가_값으로_적히는_칸만_전략값으로_표시된다():
    """`strategy.active_keys`의 값은 전략 키라서 화면이 한글로 옮긴다.
    `stop_loss_pct`의 값은 -0.05라서 옮기면 오히려 틀린 말이 된다."""
    from tests.scripts_for_test import 기준이름

    표 = {ㄱ["열쇠"]: ㄱ["전략값"] for ㄱ in 기준이름()}

    assert 표["strategy.active_keys"] is True
    assert 표["strategy.sell_keys"] is True
    assert 표["strategy"] is True
    assert 표["sell_strategy"] is True
    assert 표["stop_loss_pct"] is False
    assert 표["risk.trading_enabled"] is False


def test_전략설명의_이름이_전부_한글이다():
    """화면 어디에도 `volume_surge_5d_ma20`이 뜨면 안 된다. 처음 보는
    사람에게 아무 뜻도 없고, 뜻이 없으면 그 줄을 아예 안 읽게 된다."""
    from tests.scripts_for_test import 전략설명

    영문뿐인것 = [
        ㅈ["키"] for ㅈ in 전략설명()
        if not any("가" <= ㄱ <= "힣" for ㄱ in ㅈ["이름"])
    ]
    assert not 영문뿐인것, f"한글 이름이 없는 전략: {영문뿐인것}"
