"""용어 사전 검증.

설명이 조용히 비어 있으면 최악이다. 모르는 말을 눌렀는데 아무 말도 안
나오면, 사전이 있다는 사실 자체가 거짓말이 된다."""

import pytest

from muwon.dashboard.glossary import TERMS, terms_for


def test_every_term_actually_explains_something():
    for key, term in TERMS.items():
        assert term.이름, f"{key}: 이름이 비었다"
        assert len(term.뜻) > 10, f"{key}: 뜻이 너무 짧다"
        assert len(term.읽는법) > 10, f"{key}: 읽는 법이 없으면 뜻만 알고 화면은 못 읽는다"


def test_an_unknown_key_fails_loudly():
    """오타 하나로 설명이 통째로 빠지면, 정작 설명이 필요한 화면이 조용해진다."""
    with pytest.raises(KeyError):
        terms_for(["손절", "손졀"])


def test_terms_come_back_in_the_order_asked():
    picked = terms_for(["비중", "손절"])
    assert [t.이름 for t in picked] == ["비중", "손절"]


def test_dashboard_only_asks_for_terms_that_exist():
    """화면이 부르는 열쇠말과 사전이 어긋나면 화면이 통째로 죽는다.

    terms_for가 KeyError를 던지도록 만들어 뒀으므로, 오타 하나가 곧 흰
    화면이다. 여기서 미리 잡는다."""
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "src" / "muwon" / "dashboard" / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))

    asked: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "render_terms"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            asked |= {
                e.value for e in node.args[0].elts if isinstance(e, ast.Constant)
            }

    assert asked, "화면이 용어를 하나도 안 부르면 사전을 만든 뜻이 없다"
    assert not (asked - set(TERMS)), f"사전에 없는 열쇠말: {sorted(asked - set(TERMS))}"


def test_the_exported_document_is_in_sync():
    """docs/용어사전.md는 손으로 관리하면 반드시 어긋난다.

    대시보드를 안 켜고도 읽을 수 있어야 해서 문서로도 뽑는데, 두 벌이
    갈라지면 어느 쪽이 맞는지 아무도 모른다."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "export_glossary.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=root,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
