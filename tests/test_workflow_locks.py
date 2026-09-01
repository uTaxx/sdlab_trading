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


def test_액션_표현식에_한글_식별자를_쓰지_않는다():
    """GitHub Actions 표현식 파서는 한글 식별자를 못 읽는다.

    이 저장소에서 두 번 겪었다. 처음은 `id: 배포`와 `steps.배포.outputs`였고
    (워크플로가 0초 만에 로그도 없이 죽었다), 두 번째는 workflow_dispatch
    입력 이름을 `기간`으로 두었다가 dispatch가 422로 거절된 것이다.

    증상이 고약하다. 파일 전체가 파싱에 실패해서 무엇이 틀렸는지 안 보인다.
    사람이 읽는 name·description·값은 한글이어도 된다. 식별자만 영문이다."""
    import re
    from pathlib import Path

    나쁜것 = []
    for 길 in sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml")):
        글 = 길.read_text(encoding="utf-8")
        for 식 in re.findall(r"\$\{\{(.*?)\}\}", 글, re.DOTALL):
            # 따옴표 안은 그냥 글자다. 식별자만 본다.
            벗긴것 = re.sub(r"'[^']*'|\"[^\"]*\"", "", 식)
            if any("가" <= ㄱ <= "힣" for ㄱ in 벗긴것):
                나쁜것.append(f"{길.name}: {식.strip()}")
    assert not 나쁜것, "표현식에 한글 식별자가 있습니다:\n  " + "\n  ".join(나쁜것)


def test_기간검증은_상태DB를_안_고치므로_잠그지_않는다():
    """읽기만 하는 워크플로를 state-write에 묶으면 5분마다 도는 손절 감시가
    검증 15분을 기다린다. 손절이 늦는 쪽이 훨씬 나쁘다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "period-check.yml").read_text(encoding="utf-8")
    assert "group: period-check" in 글
    assert "group: state-write" not in 글
    # DB를 올리는 줄이 있으면 위의 전제가 깨진다.
    assert "gdrive_sync.py upload" not in 글


#: 아직 입력값을 `run:` 안에 직접 박고 있는 워크플로들. **줄일 수만 있다.**
#:
#: 이 자리는 셸 주입이 가능한 모양이다. 다만 이것들은 GitHub에 쓰기 권한이
#: 있는 사람만 부를 수 있어서, 화면에서 아무나 값을 넣는 period-check와는
#: 위험이 다르다. 한꺼번에 고치면 시험해 볼 수 없는 워크플로를 여럿 건드리게
#: 되므로 목록으로 남겨 두고 손대는 김에 하나씩 고친다.
아직안고친것 = {
    "adopt-holdings.yml", "analysis-report.yml", "cancel-orders.yml",
    "drop-phantom.yml", "execute-approved.yml", "experiment.yml",
    "hypothesis-log.yml", "market-report.yml", "propose-buys.yml",
    "push-records.yml", "reconcile-orders.yml", "robustness-check.yml",
    "settle-fills.yml", "switch-strategy.yml",
    "update-universe.yml", "verify-kis-order.yml",
}


def test_사람이_넣은_값을_명령줄에_직접_박지_않는다():
    """`${{ github.event.inputs.* }}`는 셸 스크립트 글자 안으로 그대로
    치환된다. 따옴표나 `$(...)`가 섞인 값이 오면 그게 명령으로 실행된다.

    화면에서 전략을 골라 보낼 수 있게 한 뒤로 이 자리에 아무 글자나 들어올
    수 있다. 환경변수로 넘기면 셸이 그 값을 글자로만 본다.

    다만 `type: choice` 입력은 GitHub이 목록 밖의 값을 안 받으므로 예외다.
    `== '값'`으로 견주기만 하는 것도 안전하다."""
    import re
    from pathlib import Path

    나쁜것: list[str] = []
    이미아는것: set[str] = set()
    for 길 in sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml")):
        글 = 길.read_text(encoding="utf-8")
        # `run:` 블록만 본다. env:에 넣는 것은 안전한 쪽이다.
        for 덩이 in re.findall(r"\n        run: \|\n(.*?)(?=\n      - |\n  \w|\Z)", 글, re.DOTALL):
            for 식 in re.findall(r"\$\{\{(.*?)\}\}", 덩이, re.DOTALL):
                if "github.event.inputs" not in 식:
                    continue
                # `A == 'x' && '켬' || ''` 처럼 값을 견주기만 하는 것은 괜찮다.
                if "==" in 식:
                    continue
                if 길.name in 아직안고친것:
                    이미아는것.add(길.name)
                    continue
                나쁜것.append(f"{길.name}: {식.strip()}")

    assert not 나쁜것, (
        "입력값을 run: 안에 직접 박았습니다. env:로 넘기고 \"$이름\"으로 쓰세요:\n  "
        + "\n  ".join(나쁜것)
    )
    # 목록은 줄기만 해야 한다. 고쳐 놓고 목록에서 안 빼면 다음에 다시
    # 들어가도 아무것도 안 빨개진다.
    남은것 = 아직안고친것 - 이미아는것
    assert not 남은것, (
        "이미 고쳤는데 '아직안고친것'에 남아 있습니다. 지우세요: "
        + ", ".join(sorted(남은것))
    )


# ── 전략 검토·반영 (2026-09-01) ────────────────────────────────


def test_전략검토는_상태DB를_안_고치므로_잠그지_않는다():
    """읽기만 하는 워크플로를 state-write에 묶으면 5분마다 도는 손절 감시가
    전략 27개 계산을 기다린다. 손절이 늦는 쪽이 훨씬 나쁘다.

    이 스크립트가 DB에 한 줄이라도 쓰게 되면 upload 줄이 생기고, 그러면
    위의 `test_DB를_고치면_같은_자물쇠를_쓴다`가 잡는다."""
    글 = (_워크플로[0].parent / "strategy-review.yml").read_text(encoding="utf-8")
    assert "group: strategy-review" in 글
    assert "group: state-write" not in 글
    assert "gdrive_sync.py upload" not in 글


def test_전략반영은_상태DB를_고치므로_state_write다():
    """전략 키를 상태 DB에 쓴다. 겹치면 DB가 통째로 덮인다."""
    길 = _워크플로[0].parent / "strategy-apply.yml"
    글 = 길.read_text(encoding="utf-8")
    assert "gdrive_sync.py upload" in 글, "반영했으면 DB를 올려야 합니다"
    묶음 = _읽기(길).get("concurrency")
    assert 묶음.get("group") == DB잠금
    assert 묶음.get("cancel-in-progress") is False


def test_전략_변경은_사람이_두_번_누른_뒤에만_된다():
    """검토가 전략을 바꾸면 승인 단계가 없는 것과 같다.

    검토 스크립트는 계산만 하고, 반영 스크립트만 전략을 쓴다. 둘이 한
    워크플로에 섞이면 그 경계가 사라진다."""
    검토 = (_워크플로[0].parent / "strategy-review.yml").read_text(encoding="utf-8")
    반영 = (_워크플로[0].parent / "strategy-apply.yml").read_text(encoding="utf-8")
    assert "apply_strategy_change.py" not in 검토, "검토가 전략을 바꾸면 안 됩니다"
    assert "run_strategy_review.py" in 검토
    assert "apply_strategy_change.py" in 반영
    assert "run_strategy_review.py" not in 반영


def test_전략_반영이_매수_후보_산출보다_먼저다():
    """후보를 뽑은 뒤에 바꾸면 화면에 뜬 후보와 실제 전략이 하루 어긋난다.

    시각은 n8n 시계가 정한다. 여기서는 그 순서를 문서에 적어 두었는지만
    본다 — 순서를 잊으면 어긋난 뒤에야 알게 된다."""
    글 = (_워크플로[0].parent / "strategy-apply.yml").read_text(encoding="utf-8")
    assert "매수 후보를 뽑기 전이어야 한다" in 글


def test_셸_변수_이름에_한글을_안_쓴다():
    """bash가 한글 변수 이름을 못 읽는다.

    `인자=""`를 쓰면 `command not found`로 죽는다. 2026-09-01에 실제로
    겪었다. GitHub Actions 표현식만 그런 줄 알았는데 셸도 같다.

    파이썬 argparse 인자 이름은 한글이어도 된다(`--최소운용일`). 셸이
    그건 그냥 글자로 넘긴다. 막히는 것은 **대입문의 왼쪽**이다."""
    import re
    from pathlib import Path

    나쁜것: list[str] = []
    for 길 in sorted((Path(__file__).resolve().parent.parent
                     / ".github" / "workflows").glob("*.yml")):
        for ㄴ, 줄 in enumerate(길.read_text(encoding="utf-8").splitlines(), 1):
            벗김 = 줄.strip()
            if 벗김.startswith("#"):
                continue
            # `이름=값` 꼴에서 이름에 한글이 있으면 셸이 못 읽는다.
            맞은것 = re.match(r"^([^\s=#]+)=", 벗김)
            if 맞은것 and any("가" <= ㄱ <= "힣" for ㄱ in 맞은것.group(1)):
                나쁜것.append(f"{길.name}:{ㄴ}  {벗김[:60]}")

    assert not 나쁜것, (
        "셸 변수 이름에 한글이 있습니다. bash가 command not found로 죽습니다:\n  "
        + "\n  ".join(나쁜것)
    )


def test_전략_승인_버튼이_고친_DB가_드라이브로_올라간다():
    """2026-09-01에 이 구멍이 있었다.

    telegram-n8n.yml은 버튼이 시트에만 쓴다는 전제로 DB를 안 올렸다. 전략
    승인 버튼이 붙으면서 상태 DB에 쓰게 됐는데 업로드 단계가 없었다.
    러너가 사라질 때 예약이 통째로 없어지고, 그런데도 텔레그램에는
    "선택했습니다"가 뜬다. 조용히 성공한 척하는 실패다.

    이 시험은 세 가지를 같이 본다. 하나만 있어도 안 된다."""
    길 = _워크플로[0].parent / "telegram-n8n.yml"
    글 = 길.read_text(encoding="utf-8")

    assert "--touch-when-changed" in 글, "무엇이 바뀌었는지 표시를 안 남깁니다"
    assert "gdrive_sync.py upload" in 글, "고친 DB를 안 올립니다"
    assert "hashFiles('.changed')" in 글, "바뀐 게 없어도 올리면 다른 변경을 덮습니다"

    묶음 = _읽기(길).get("concurrency")
    assert 묶음.get("group") == DB잠금, (
        "DB를 고치므로 state-write여야 합니다. 겹치면 DB가 통째로 덮입니다."
    )


def test_버튼이_DB를_고쳤는지_스크립트가_알린다():
    """올릴지 말지는 워크플로가 정하지만, 무엇이 바뀌었는지는 스크립트만 안다.

    읽은 위치(offset)만 보면 안 된다. n8n이 넘겨주는 길에서는 offset을 아예
    안 건드리는데, 전략 승인 버튼이 그 길로 들어온다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent
          / "scripts" / "telegram_control.py").read_text(encoding="utf-8")
    assert "_DB고쳤나" in 글
    assert "올릴까 = (마지막 != offset) or _DB고쳤나" in 글
