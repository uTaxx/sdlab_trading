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
    """이 시험 자체가 무의미해지는 것을 막는다. 찾는 방법이 틀리면
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
    "settle-fills.yml",
    "update-universe.yml", "verify-kis-order.yml",
}


def test_사람이_넣은_값을_명령줄에_직접_박지_않는다():
    """`${{ github.event.inputs.* }}`는 셸 스크립트 글자 안으로 그대로
    치환된다. 따옴표나 `$(...)`가 섞인 값이 오면 그게 명령으로 실행된다.

    화면에서 전략을 골라 보낼 수 있게 한 뒤로 이 자리에 아무 글자나 들어올
    수 있다. 환경변수로 넘기면 셸이 그 값을 글자로만 본다.

    다만 `type: choice` 입력은 GitHub이 목록 밖의 값을 안 받으므로 예외다.
    `== '값'`으로 비교만 하는 것도 안전하다."""
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
                # `A == 'x' && '켬' || ''` 처럼 값을 비교만 하는 것은 괜찮다.
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


def test_전략검토도_상태DB를_고치므로_state_write다():
    """2026-09-01에 옮겼다. 전에는 읽기만 해서 따로 묶여 있었다.

    지금은 그림자 추적이 오늘 순위를 `strategy_shadows`에 남기고 지평이 지난
    옛 줄에 뒤 수익률을 채운다. 쓰는데 안 올리면 러너가 사라질 때 통째로
    없어지고, 그런데도 텔레그램에는 검토 결과가 뜬다."""
    길 = _워크플로[0].parent / "strategy-review.yml"
    글 = 길.read_text(encoding="utf-8")
    assert "gdrive_sync.py upload" in 글, "DB에 썼으면 올려야 합니다"
    묶음 = _읽기(길).get("concurrency")
    assert 묶음.get("group") == DB잠금
    # 쓰는 도중에 끊기면 반쯤 고친 DB가 올라간다.
    assert 묶음.get("cancel-in-progress") is False


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
    본다. 순서를 잊으면 어긋난 뒤에야 알게 된다."""
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


def test_섹터당_상한을_워크플로에_박아_두지_않는다():
    """`propose_buys.py`는 `--max-per-sector`가 0 이상이면 시트를 안 읽는다
    (`args.max_per_sector >= 0`). 그래서 워크플로가 숫자를 넘기면 시트의
    `max_per_sector`가 아예 안 쓰인다.

    2026-09-01까지 기본값이 2로 박혀 있었다. 예약 실행은 inputs가 비어서
    `|| '2'`로 떨어지므로, 08:30이 매일 시트를 무시하고 2를 쓰고 있었다.
    시트에서 값을 바꿔도 아무 일도 일어나지 않고, 아무것도 빨개지지 않는다.

    -1이 "시트 값을 쓴다"는 뜻이다. 0은 "제한 없음"이라 뜻이 다르다."""
    글 = (Path(__file__).resolve().parent.parent
          / ".github" / "workflows" / "propose-buys.yml").read_text(encoding="utf-8")

    맞는것 = "--max-per-sector \"${{ github.event.inputs.max_per_sector || '-1' }}\""
    assert 맞는것 in 글, "예약 실행이 시트 값을 못 읽습니다"

    자리 = 글.index("max_per_sector:")
    assert 'default: "-1"' in 글[자리:자리 + 200], 글[자리:자리 + 200]


# ── 실패했을 때 주인이 아는가 (2026-09-05) ────────────────────────
#
# 저장소 전체에 실패 알림이 하나도 없었다. 워크플로 서른세 개 중
# `if: failure()`가 붙은 것이 0개였다.
#
# 2026-09-04 08:30 매수 후보 산출이 통째로 죽었는데 주인은 모르셨다.
# 다음 날 우연히 로그를 열어 보고 알았다. 그날 매수 후보가 하나도 안
# 나왔고 아무도 그 사실을 몰랐다.
#
# **GitHub 화면의 빨간불을 매일 보는 사람이 없으면 빨간불은 없는 것과
# 같다.** 이 저장소가 "조용히 성공한 척하는 실패가 제일 비싸다"를 세 번
# 적어 두고도 비어 있던 자리다.

#: 사람이 안 보고 있어도 도는 것들. 여기서 실패하면 알림 말고는 알 길이 없다.
알려야하는것 = {
    "propose-buys.yml",      # 08:30 매수 후보
    "execute-approved.yml",  # 09:05 실제 주문
    "watch-stops.yml",       # 장중 손절 감시
    "settle-fills.yml",      # 17:30 체결 정산
    "push-records.yml",      # 17:40 기록
    "strategy-apply.yml",    # 08:20 전략 반영
    "strategy-review.yml",   # 17:50 검토
    "collect-intraday.yml",  # 분봉 (그날 안에만 받을 수 있다)
    "store-window-scan.yml",
    "update-universe.yml",
}


def _워크플로들():
    from pathlib import Path

    뿌리 = Path(__file__).resolve().parent.parent / ".github" / "workflows"
    return {길.name: 길.read_text(encoding="utf-8") for 길 in sorted(뿌리.glob("*.yml"))}


def test_사람이_안_보는_워크플로는_실패를_알린다():
    import yaml

    빠진것 = []
    for 이름 in sorted(알려야하는것):
        글 = _워크플로들()[이름]
        문서 = yaml.safe_load(글)
        붙었나 = False
        for 잡 in 문서["jobs"].values():
            for 단계 in 잡.get("steps", []):
                if (단계.get("if") == "failure()"
                        and "notify-failure" in str(단계.get("uses", ""))):
                    붙었나 = True
        if not 붙었나:
            빠진것.append(이름)
    assert not 빠진것, (
        "실패해도 주인이 모르는 워크플로입니다. `if: failure()`로 "
        "`./.github/actions/notify-failure`를 붙이세요:\n  " + "\n  ".join(빠진것)
    )


def test_실패_알림이_워크플로를_더_빨갛게_만들지_않는다():
    """이미 실패한 실행이다. 알림까지 실패했다고 종료 코드를 덮으면 진짜
    원인이 로그 아래로 밀린다. 이 저장소가 겪은 일이다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent
          / ".github" / "actions" / "notify-failure" / "action.yml"
          ).read_text(encoding="utf-8")
    assert "exit 0" in 글, "비밀값이 없을 때 종료 코드를 0으로 두어야 합니다"
    assert "|| echo \"000\"" in 글, "curl이 죽어도 단계가 실패하지 않아야 합니다"


def test_실패_알림이_비밀값_없을_때_조용히_넘어가지_않는다():
    """토큰이 비어 있으면 알림이 영영 안 간다. 그것을 안 적으면 '알림을
    붙였으니 됐다'고 믿게 되는데, 그게 지금까지의 상태와 똑같다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent
          / ".github" / "actions" / "notify-failure" / "action.yml"
          ).read_text(encoding="utf-8")
    assert "::error::텔레그램 비밀값이 비어 있어" in 글


def test_실패_알림_액션에_한글_식별자를_안_쓴다():
    """GitHub Actions에 한글 식별자를 쓰면 파일 전체가 파싱에 실패한다.
    `id: 배포`로 워크플로가 0초 만에 죽고 로그도 안 남은 적이 있다."""
    import re
    from pathlib import Path

    자리 = Path(__file__).resolve().parent.parent / ".github" / "actions"
    for 길 in 자리.rglob("action.yml"):
        # 폴더 이름
        assert not re.search(r"[가-힣]", str(길.relative_to(자리))), 길
        import yaml
        문서 = yaml.safe_load(길.read_text(encoding="utf-8"))
        for 키 in 문서.get("inputs", {}):
            assert not re.search(r"[가-힣]", 키), f"{길}: 입력 이름 {키}"


def test_실패_알림이_무엇이_실패했는지_적는다():
    """'워크플로가 실패했습니다'만 오면 무엇을 해야 할지 모른다."""
    import yaml

    표 = _워크플로들()
    for 이름 in sorted(알려야하는것):
        문서 = yaml.safe_load(표[이름])
        for 잡 in 문서["jobs"].values():
            for 단계 in 잡.get("steps", []):
                if "notify-failure" in str(단계.get("uses", "")):
                    무엇 = 단계.get("with", {}).get("what", "")
                    assert len(무엇) >= 4, f"{이름}: what이 너무 짧습니다({무엇!r})"
