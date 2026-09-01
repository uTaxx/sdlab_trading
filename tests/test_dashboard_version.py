"""화면이 어느 커밋으로 도는지를 화면이 스스로 말하게 한다.

배포된 대시보드가 18개 커밋 전 코드를 그대로 실행하고 있던 적이 있다.
코드는 멀쩡한데 배포가 안 따라온 것이었는데, 화면만 봐서는 알 길이 없어
"고쳤는데 왜 그대로냐"를 한참 헤맸다."""

import subprocess

from muwon.dashboard.app import 화면버전


def test_it_reports_a_commit_we_can_actually_look_up():
    버전 = 화면버전.__wrapped__()  # 캐시를 건너뛰고 진짜 git을 부른다
    assert 버전, "git 저장소에서 실행하는데 버전이 비면 표시할 게 없다"
    커밋 = 버전.split()[0]
    확인 = subprocess.run(
        ["git", "cat-file", "-t", 커밋], capture_output=True, text=True, check=False
    )
    assert 확인.stdout.strip() == "commit", f"{커밋}은 이 저장소의 커밋이 아니다"


def test_a_missing_git_does_not_kill_the_screen(monkeypatch):
    """버전을 모르는 것은 화면이 죽을 이유가 아니다."""

    def 없음(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", 없음)
    assert 화면버전.__wrapped__() == ""
