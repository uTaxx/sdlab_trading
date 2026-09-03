"""주인이 읽는 글에 저장소 안에서만 쓰는 말이 남아 있는지 본다.

## 왜 이 시험이 필요한가

2026-09-03에 주인이 지적했다. 제가 "비기는 폭", "지렛대"라고 썼는데 둘 다
사전에도 없고 화면에도 없는 말이다. 원인을 찾아보니 CLAUDE.md 본문이
그 말을 쓰고 있었다. 쓰지 말라고 적어 둔 파일이 스스로 그 말을 쓰면
규칙이 아무 힘이 없다.

말은 한 번 새면 계속 샌다. 문서를 읽고, 그 말이 자연스럽게 느껴지고,
답에 섞이고, 다시 문서에 적힌다. 사람이 매번 잡아 주는 방식으로는 끊기지
않아서 시험으로 고정한다.

## 무엇을 보나

**주인이 실제로 읽는 글만 본다.** 세 자리다.

1. 화면에 그려지는 글 (dashboard/index.html, app.js의 문자열)
2. 텔레그램으로 나가는 글
3. 구글 시트에 적히는 칸 제목과 설명

**코드의 이름과 주석은 안 본다.** `곁말`은 CSS 이름이고 `최악토막`은
자료를 나르는 칸 이름이라 사람 눈에 안 띈다. 그것까지 바꾸면 고칠 곳이
수백 군데인데 읽는 사람에게 달라지는 것은 없다.

**터미널에만 찍히는 글도 안 본다.** 실험 스크립트의 출력은 GitHub Actions
기록에만 남고 주인은 보지 않는다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent

#: 주인에게 보이는 글에 쓰지 않는 말. 오른쪽이 대신 쓸 말이다.
#: CLAUDE.md의 표에서 가져왔다. 그 표를 늘리면 여기도 늘린다.
안쓸말 = {
    "토막": "구간",
    "쪼개": "나누다",
    "견주": "비교하다",
    "돌려 보": "실행하다",
    "창구": "n8n 연결",
    "문턱": "최소 기준",
    "성적표": "평가 결과",
    "훑기": "여러 설정으로 반복 계산한 것",
    "손잡이": "바꿔 본 설정값",
    "격변": "변동성이 큰 구간",
    "지렛대": "효과가 큰 쪽",
    "운동장": "종목 목록",
    "비기는 폭": "동점 범위",
    "반토막": "주가가 절반으로 떨어짐",
    "몰빵": "한 종목에 자금이 몰림",
    "걸어 둔": "설정한",
    "걸린 전략": "설정된 전략",
}


def _문장들(글: str) -> list[str]:
    """HTML에서 사람에게 보이는 글만. 주석과 태그는 뺀다."""
    본문 = re.sub(r"<!--.*?-->", "", 글, flags=re.DOTALL)
    본문 = re.sub(r"<[^>]+>", " ", 본문)
    return 본문.splitlines()


def _파이썬문자열(길: Path) -> list[tuple[int, str]]:
    """한 줄짜리 문자열만 모은다. 긴 여러 줄 글은 대체로 독스트링이다."""
    나온것 = []
    나무 = ast.parse(길.read_text(encoding="utf-8"))
    for 마디 in ast.walk(나무):
        if isinstance(마디, ast.Constant) and isinstance(마디.value, str):
            if "\n" in 마디.value and len(마디.value) > 200:
                continue
            나온것.append((마디.lineno, 마디.value))
    return 나온것


def test_화면_글에_저장소_말이_없다():
    """index.html의 본문과 app.js의 문자열을 본다."""
    쪽 = (뿌리 / "dashboard" / "index.html").read_text(encoding="utf-8")
    나온것 = []
    for i, 줄 in enumerate(_문장들(쪽), 1):
        for ㅁ in 안쓸말:
            # 곁말은 CSS 이름이라 태그를 지우면 남지 않는다. 남았다면 본문이다.
            if ㅁ in 줄:
                나온것.append(f"index.html:{i} [{ㅁ}] {줄.strip()[:60]}")
    assert not 나온것, "\n".join(나온것)


def test_텔레그램_글에_저장소_말이_없다():
    """폰으로 가는 글이다. 여기 이상한 말이 있으면 되물을 곳도 없다."""
    볼곳 = [
        뿌리 / "scripts" / "telegram_control.py",
        뿌리 / "scripts" / "propose_buys.py",
        뿌리 / "src" / "muwon" / "cloud" / "approval.py",
        뿌리 / "src" / "muwon" / "cloud" / "strategy_approval.py",
    ]
    나온것 = []
    for 길 in 볼곳:
        if not 길.exists():
            continue
        for 줄번호, 글 in _파이썬문자열(길):
            for ㅁ in 안쓸말:
                if ㅁ in 글:
                    나온것.append(f"{길.name}:{줄번호} [{ㅁ}] {글[:60]}")
    assert not 나온것, "\n".join(나온것)


def test_시트_칸_제목에_저장소_말이_없다():
    """구글 시트를 열면 첫 줄에 그대로 보인다."""
    from muwon.cloud import sheet_log

    머리들 = [
        이름 for 이름 in dir(sheet_log)
        if 이름.endswith("머리") and isinstance(getattr(sheet_log, 이름), list)
    ]
    나온것 = []
    for 이름 in 머리들:
        for 칸 in getattr(sheet_log, 이름):
            for ㅁ in 안쓸말:
                if ㅁ in str(칸):
                    나온것.append(f"{이름}의 '{칸}' 칸 [{ㅁ}]")
    assert not 나온것, "\n".join(나온것)


def test_시트_설정_설명에_저장소_말이_없다():
    """시트 `설정` 탭의 설명 칸이다. 값을 바꿀 때 그 옆에 보인다."""
    from muwon.settings.from_sheet import 기준표

    나온것 = []
    항목들 = 기준표.values() if hasattr(기준표, "values") else 기준표
    for ㄱ in 항목들:
        for 칸 in ("설명", "이름", "왜"):
            글 = str(getattr(ㄱ, 칸, "") or "")
            for ㅁ in 안쓸말:
                if ㅁ in 글:
                    나온것.append(f"{getattr(ㄱ, '이름', '?')}.{칸} [{ㅁ}] {글[:60]}")
    assert not 나온것, "\n".join(나온것)


def test_전략_설명과_용어_사전에_저장소_말이_없다():
    """둘 다 화면에 그대로 뜬다."""
    from muwon.dashboard import glossary
    from muwon.strategy.registry import list_definitions

    나온것 = []
    for ㄷ in list_definitions():
        for 칸 in ("화면이름", "한줄설명", "쉬운설명", "쉬운참고"):
            글 = str(getattr(ㄷ, 칸, "") or "")
            for ㅁ in 안쓸말:
                if ㅁ in 글:
                    나온것.append(f"{ㄷ.key}.{칸} [{ㅁ}] {글[:60]}")

    표 = getattr(glossary, "TERMS", None) or getattr(glossary, "용어표", None)
    쓸것 = 표.values() if hasattr(표, "values") else (표 or [])
    for t in 쓸것:
        for 칸 in ("뜻", "읽는법"):
            글 = str(getattr(t, 칸, "") or "")
            for ㅁ in 안쓸말:
                # 용어 사전은 그 말 자체를 표제어로 실을 수 있다. 뜻풀이만 본다.
                if ㅁ in 글:
                    나온것.append(f"용어 {t.이름}.{칸} [{ㅁ}] {글[:60]}")
    assert not 나온것, "\n".join(나온것)


def test_claude_md도_같은_규칙을_지킨다():
    """**이 시험이 이 파일의 핵심이다.** 지침 파일이 스스로 그 말을 쓰면
    다음 회차가 그것을 읽고 그대로 따라 쓴다. 실제로 그래서 지적받았다.

    말투를 가르치는 1절은 뺀다. 거기서는 쓰지 말라는 말을 이름으로 불러야
    한다. 표와 큰따옴표로 인용한 줄, 백틱으로 감싼 코드 이름도 뺀다."""
    글 = (뿌리 / "CLAUDE.md").read_text(encoding="utf-8")
    줄들 = 글.splitlines()

    # 1절은 통째로 뺀다. 다음 `## `가 나올 때까지다.
    시작 = next(i for i, ㄱ in enumerate(줄들) if ㄱ.startswith("## 1."))
    끝 = next(i for i, ㄱ in enumerate(줄들[시작 + 1:], 시작 + 1)
             if ㄱ.startswith("## "))

    나온것 = []
    for i, 줄 in enumerate(줄들, 1):
        if 시작 < i <= 끝:
            continue
        ㄱ = re.sub(r"`[^`]*`", "", 줄).strip()   # 코드 이름은 뺀다
        if ㄱ.startswith("|") or '"' in ㄱ:
            continue
        for ㅁ in 안쓸말:
            if ㅁ in ㄱ:
                나온것.append(f"CLAUDE.md:{i} [{ㅁ}] {ㄱ[:70]}")
    assert not 나온것, "\n".join(나온것)
