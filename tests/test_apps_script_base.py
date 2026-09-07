"""Apps Script 기반 코드가 규칙을 지키는가.

## 왜 시험하나

`apps-script/trading`은 이 저장소가 공개인 채로 Google Apps Script에
올라간다. 회사 쪽 프로젝트는 muwon406에 있다. 비밀값이 한 번 들어가면 되돌릴 수 없다.
그리고 매니페스트가 틀리면 배포는 초록불인데 웹 앱이 안 열리거나 권한을
다시 묻는다. 둘 다 조용한 실패라 여기서 막는다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
자리 = 뿌리 / "apps-script"
프로젝트들 = ["trading"]

#: 비밀값처럼 생긴 글자. 이 저장소에 이런 것이 있으면 안 된다.
비밀모양 = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub 개인 토큰"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub 세밀 토큰"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"), "텔레그램 봇 토큰"),
    (re.compile(r"PS[A-Za-z0-9]{30,}"), "KIS 앱키 모양"),
    (re.compile(r"옛열쇠"), "n8n에 박혀 있던 옛 화면 인증 키를 옮겨 적은 흔적"),
]


def _파일들() -> list[Path]:
    return sorted(
        p for p in 자리.rglob("*") if p.is_file() and "node_modules" not in p.parts
    )


def test_비밀값_모양이_없다():
    나쁜것 = []
    for 길 in _파일들():
        글 = 길.read_text(encoding="utf-8", errors="ignore")
        for 무늬, 이름 in 비밀모양:
            if 무늬.search(글):
                나쁜것.append(f"{길.relative_to(뿌리)}: {이름}")
    assert not 나쁜것, "비밀값처럼 생긴 글자가 있습니다:\n  " + "\n  ".join(나쁜것)


@pytest.mark.parametrize("이름", 프로젝트들)
def test_매니페스트가_웹_앱과_시간대와_권한을_다_적는다(이름):
    문서 = json.loads(
        (자리 / 이름 / "src" / "appsscript.json").read_text(encoding="utf-8")
    )
    assert 문서["timeZone"] == "Asia/Seoul"
    assert 문서["runtimeVersion"] == "V8"
    # 화면이 익명으로 부르고, 스크립트는 주인 계정으로 돈다.
    assert 문서["webapp"] == {
        "executeAs": "USER_DEPLOYING",
        "access": "ANYONE_ANONYMOUS",
    }
    범위 = set(문서["oauthScopes"])
    # 권한을 처음부터 다 적어 두어야 「허용」을 한 번만 받는다.
    for 필요 in (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/script.external_request",
        "https://www.googleapis.com/auth/script.scriptapp",
    ):
        assert 필요 in 범위, f"{이름}: {필요} 권한이 빠졌습니다"


@pytest.mark.parametrize("이름", 프로젝트들)
def test_clasp_설정은_rootDir이_src다(이름):
    문서 = json.loads((자리 / 이름 / ".clasp.json").read_text(encoding="utf-8"))
    assert 문서["rootDir"] == "src"
    assert "scriptId" in 문서
    배포 = json.loads((자리 / 이름 / "deployment.json").read_text(encoding="utf-8"))
    assert "deploymentId" in 배포


def test_시계의_워크플로_파일이_전부_있다():
    """시간표에 적힌 워크플로가 저장소에 없으면 GitHub이 404를 주고 그날
    그 일이 조용히 안 돈다. Node 시험도 같은 것을 보지만 파이썬 쪽에서도
    한 번 더 본다. 두 시험 체계가 서로 다른 날 깨질 수 있다."""
    글 = (자리 / "trading" / "src" / "시계.js").read_text(encoding="utf-8")
    파일들 = re.findall(r"워크플로: '([a-z-]+\.yml)'", 글)
    assert len(파일들) >= 10, 파일들
    없는것 = [f for f in 파일들 if not (뿌리 / ".github" / "workflows" / f).exists()]
    assert not 없는것, 없는것


def test_시계는_관찰이_기본이다():
    """최종 연결 전에 올라가는 코드라, 기본값이 실행이면 n8n과 두 번 부른다."""
    글 = (자리 / "trading" / "src" / "설정.js").read_text(encoding="utf-8")
    assert "=== '실행' ? '실행' : '관찰'" in 글


def test_배포_워크플로는_시간표가_없고_PR에서는_올리지_않는다():
    import yaml

    문서 = yaml.safe_load(
        (뿌리 / ".github" / "workflows" / "apps-script-deploy.yml").read_text(
            encoding="utf-8"
        )
    )
    켜짐 = 문서[True] if True in 문서 else 문서["on"]
    assert "schedule" not in 켜짐
    조건 = 문서["jobs"]["deploy"]["if"]
    assert "github.repository == 'uTaxx/sdlab_trading'" in 조건
    assert "pull_request" in 조건


def test_배포_워크플로는_비밀값이_비면_빨갛게_끝난다():
    글 = (뿌리 / ".github" / "workflows" / "apps-script-deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "::error::CLASPRC_JSON 비밀값이 비어 있습니다" in 글
    assert 'rm -f "$HOME/.clasprc.json"' in 글


@pytest.mark.skipif(shutil.which("node") is None, reason="node가 없다")
def test_Node_시험이_통과한다():
    결과 = subprocess.run(
        ["node", "--test", "tests/*.test.js"],
        cwd=자리,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert 결과.returncode == 0, 결과.stdout[-3000:] + 결과.stderr[-2000:]
