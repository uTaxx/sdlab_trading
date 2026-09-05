"""화면 저장소로 옮길 때 남의 파일을 조용히 지우지 않는가.

## 무엇이 문제였나 (2026-09-05)

옮기는 방법이 이랬다.

    git ls-files -z | xargs -0 rm -f
    git -C ../muwon406 archive HEAD | tar -x

화면 저장소를 통째로 비우고 매매 저장소를 푼다. **화면 저장소에만 있는
파일이 생기는 순간 조용히 사라진다.** 지금까지는 그런 파일이 없어서
우연히 안전했을 뿐이다.

주인이 "저장소를 같이 두면 다른 시스템이랑 엉키지 않느냐"고 물어서
확인하다 찾았다. 정기 실행은 저장소 조건으로 막혀 있어 안 엉키는데,
이 지우는 방식은 남아 있었다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from publish_dashboard import 전용파일, 지울것정하기


def test_양쪽에_다_있으면_안_지운다():
    assert 지울것정하기(["a.py", "b.py"], ["a.py", "b.py"], ()) == []


def test_화면_저장소에만_있으면_알려_준다():
    """이것을 안 알려 주면 조용히 사라진다."""
    assert 지울것정하기(["a.py", "남은것.txt"], ["a.py"], ()) == ["남은것.txt"]


def test_매매_저장소에만_있는_것은_상관없다():
    """푸는 쪽에서 새로 생긴다. 지울 것이 아니다."""
    assert 지울것정하기(["a.py"], ["a.py", "새것.py"], ()) == []


def test_전용_파일로_적어_두면_봐준다():
    assert 지울것정하기(["a.py", "화면만.txt"], ["a.py"], ("화면만.txt",)) == []


def test_전용_파일은_glob으로_적을_수_있다():
    남는것 = ["docs/화면전용/하나.md", "docs/화면전용/둘.md", "몰래.txt"]
    나온것 = 지울것정하기(["a.py", *남는것], ["a.py"], ("docs/화면전용/*",))
    assert 나온것 == ["몰래.txt"]


def test_지금은_전용_파일이_비어_있다():
    """2026-09-05에 두 저장소를 견줘 보니 화면 저장소에만 있는 파일이
    하나도 없었다. 여기에 무엇을 더하는 것은 두 저장소를 갈라놓는 일이라
    한 번 물어야 한다."""
    assert 전용파일 == ()


def test_기본은_안_지우는_쪽이다():
    """실수로 도는 쪽이 아니라 실수로 안 도는 쪽으로 기울인다. 이 저장소가
    `switch-strategy.yml`에서 쓰는 것과 같은 규칙이다."""
    글 = (Path(__file__).resolve().parent.parent
          / "scripts" / "publish_dashboard.py").read_text(encoding="utf-8")
    assert "--지워도됨" in 글
    assert "if not 인자.지워도됨" in 글
    assert "return 1" in 글


def test_옛_방식을_문서가_더는_안_시킨다():
    """CLAUDE.md가 통째로 지우는 명령을 그대로 적고 있으면, 다음에 읽는
    사람이 스크립트 대신 그것을 그대로 친다."""
    글 = (Path(__file__).resolve().parent.parent
          / "CLAUDE.md").read_text(encoding="utf-8")
    assert "git ls-files -z | xargs -0 rm -f" not in 글
    assert "publish_dashboard.py" in 글


# ── 진짜 저장소로 돌려 본다 ──────────────────────────────────────
#
# 위의 것들은 `지울것정하기` 하나만 본다. 스크립트가 실제로 멈추는지는
# 저장소를 만들어 돌려 봐야 안다. **`--지워도됨` 없이 남의 파일을 지우면
# 그것이 이 스크립트를 만든 이유가 사라진다.**

import subprocess


def _달려(명령, 어디):
    return subprocess.run(명령, cwd=어디, capture_output=True,
                          text=True, check=True).stdout


def _저장소만들기(자리: Path, 파일들: dict[str, str]) -> None:
    자리.mkdir(parents=True, exist_ok=True)
    _달려(["git", "init", "-q", "-b", "main"], 자리)
    _달려(["git", "config", "user.email", "t@t"], 자리)
    _달려(["git", "config", "user.name", "t"], 자리)
    for 이름, 속 in 파일들.items():
        길 = 자리 / 이름
        길.parent.mkdir(parents=True, exist_ok=True)
        길.write_text(속, encoding="utf-8")
    _달려(["git", "add", "-A"], 자리)
    _달려(["git", "commit", "-q", "-m", "첫 줄"], 자리)
    # 스크립트가 origin으로 되돌리므로 자기 자신을 원격으로 둔다.
    _달려(["git", "remote", "add", "origin", str(자리)], 자리)


def _옮기기(화면, 매매, *더):
    뿌리 = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, str(뿌리 / "scripts" / "publish_dashboard.py"),
         "--화면저장소", str(화면), "--매매저장소", str(매매), *더],
        capture_output=True, text=True, check=False,
    )


def test_모르는_파일이_있으면_멈춘다(tmp_path):
    """지워도 어디에도 안 남는다. 사람이 한 번 봐야 한다."""
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {"같은것.txt": "가", "화면만있는것.txt": "나"})

    결과 = _옮기기(화면, 매매)
    assert 결과.returncode == 1, 결과.stdout
    assert "화면만있는것.txt" in 결과.stdout
    assert "--지워도됨" in 결과.stdout
    assert (화면 / "화면만있는것.txt").exists(), "멈췄는데 파일을 지웠습니다"


def test_지워도됨을_주면_지운다(tmp_path):
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {"같은것.txt": "가", "화면만있는것.txt": "나"})

    결과 = _옮기기(화면, 매매, "--지워도됨")
    assert 결과.returncode == 0, 결과.stdout
    assert not (화면 / "화면만있는것.txt").exists()


def test_미리보기는_커밋하지_않는다(tmp_path):
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가", "새것.txt": "다"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {"같은것.txt": "가"})
    앞 = _달려(["git", "rev-parse", "HEAD"], 화면)

    결과 = _옮기기(화면, 매매)
    assert 결과.returncode == 0, 결과.stdout
    assert "미리보기라 커밋하지 않았습니다" in 결과.stdout
    assert _달려(["git", "rev-parse", "HEAD"], 화면) == 앞, "미리보기인데 커밋했습니다"


def test_올리기에_메시지가_없으면_거절한다(tmp_path):
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가", "새것.txt": "다"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {"같은것.txt": "가"})

    결과 = _옮기기(화면, 매매, "--올리기")
    assert 결과.returncode == 1
    assert "--메시지" in 결과.stdout


def test_매매_저장소의_파일이_실제로_옮겨진다(tmp_path):
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가", "src/새것.py": "print(1)"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {"같은것.txt": "가"})

    결과 = _옮기기(화면, 매매)
    assert 결과.returncode == 0, 결과.stdout
    assert (화면 / "src" / "새것.py").read_text(encoding="utf-8") == "print(1)"


def test_한글_파일_이름도_제대로_지운다(tmp_path):
    """`git ls-files`는 그냥 부르면 한글 이름을 이스케이프해 돌려준다.
    그 이름으로 지우면 그런 파일이 없으므로 **지우기가 조용히 실패한다.**
    `unlink(missing_ok=True)`라 예외도 안 난다.

    이 저장소는 한글 파일이 많다. `dashboard/자료/상한측정.json`,
    `docs/단기매매_재설계.md` 같은 것들이다."""
    매매 = tmp_path / "매매"
    _저장소만들기(매매, {"같은것.txt": "가"})
    화면 = tmp_path / "화면"
    _저장소만들기(화면, {
        "같은것.txt": "가",
        "한글이름파일.txt": "나",
        "폴더/안에든것.json": "다",
        "ascii_only.txt": "라",
    })

    결과 = _옮기기(화면, 매매, "--지워도됨")
    assert 결과.returncode == 0, 결과.stdout
    for 이름 in ("한글이름파일.txt", "폴더/안에든것.json", "ascii_only.txt"):
        assert not (화면 / 이름).exists(), f"{이름}을 못 지웠습니다"


def test_지우기가_실패하면_조용히_넘어가지_않는다():
    """`missing_ok=True`는 이름이 틀려도 조용히 넘어간다. 지웠는지 확인하고
    못 지웠으면 멈춰야 한다."""
    글 = (Path(__file__).resolve().parent.parent
          / "scripts" / "publish_dashboard.py").read_text(encoding="utf-8")
    assert "못지운것" in 글
    assert "::error::지우지 못한 파일이 있습니다" in 글


def test_파일_목록을_영으로_끊어_받는다():
    """이스케이프를 막는 유일한 방법이다."""
    글 = (Path(__file__).resolve().parent.parent
          / "scripts" / "publish_dashboard.py").read_text(encoding="utf-8")
    assert '"ls-files", "-z"' in 글
