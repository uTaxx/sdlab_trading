"""매매 저장소의 파일을 화면 저장소로 옮긴다.

## 왜 필요한가

저장소가 둘이다. 매매는 `uTaxx/muwon406`에서 돌고, 화면은
`uTaxx/sdlab_trading`의 `main`에서 GitHub Pages로 나간다. **매매 저장소에
커밋해도 주인이 보는 화면은 안 바뀐다.** 2026-09-03에 이것 때문에 이틀치
화면 작업이 주인에게 안 보였다.

**화면 파일만 옮기면 배포가 막힌다.** 배포 워크플로가 올리기 전에
`export_dashboard_data.py --check`로 `dashboard/자료`의 JSON이 파이썬
원본과 같은지 본다. 원본이 `src`에 있으므로 같이 가야 한다. 그래서 통째로
옮긴다.

## 통째로 지우고 다시 푸는 방식을 그만둔다 (2026-09-05)

전에는 이렇게 했다.

    git ls-files -z | xargs -0 rm -f
    git -C ../muwon406 archive HEAD | tar -x

**화면 저장소에만 있는 파일이 생기는 순간 조용히 사라진다.** 지금까지는
그런 파일이 없어서 우연히 안전했을 뿐이고, 사라져도 어디에도 안 남는다.

여기서는 **매매 저장소에 없는 파일을 발견하면 멈춘다.** 지워도 되는 것이면
`--지워도됨`을 주고, 화면 저장소에만 두어야 하는 것이면 `전용파일`에 적는다.
실수로 도는 쪽이 아니라 실수로 안 도는 쪽으로 기울인 것이다.

## 실행

    python scripts/publish_dashboard.py --화면저장소 /home/user/sdlab_trading
    python scripts/publish_dashboard.py --화면저장소 ... --올리기 --메시지 "..."
"""

from __future__ import annotations

import argparse
import subprocess
from fnmatch import fnmatch
from pathlib import Path

#: 화면 저장소에만 있어도 되는 것. 여기 적힌 것은 안 지운다.
#:
#: **비어 있는 것이 지금 맞다.** 2026-09-05에 두 저장소를 견줘 보니 화면
#: 저장소에만 있는 파일이 하나도 없었다. 나중에 생기면 여기에 적는다.
#: 적지 않고 두면 옮길 때 스크립트가 멈추고 이름을 보여 준다.
전용파일: tuple[str, ...] = ()


def 지울것정하기(화면파일들, 매매파일들, 봐줄것=전용파일) -> list[str]:
    """화면 저장소에만 있는 파일. 이것이 있으면 사람이 한 번 봐야 한다.

    `봐줄것`은 glob이다. `docs/화면전용/*`처럼 적을 수 있다."""
    남는것 = set(화면파일들) - set(매매파일들)
    return sorted(
        ㄱ for ㄱ in 남는것
        if not any(fnmatch(ㄱ, ㅂ) for ㅂ in 봐줄것)
    )


def _달리기(명령, 어디, 받을까=True) -> str:
    결과 = subprocess.run(
        명령, cwd=어디, capture_output=받을까, text=True, check=True,
    )
    return (결과.stdout or "") if 받을까 else ""


def 파일목록(저장소: Path) -> list[str]:
    """추적 중인 파일 이름. **`-z`로 받는다.**

    `git ls-files`는 그냥 부르면 한글 이름을 `"\\355\\231\\224..."`처럼
    이스케이프해 돌려준다. 이 저장소는 한글 파일이 많다
    (`dashboard/자료/상한측정.json`, `docs/단기매매_재설계.md`).

    이스케이프된 이름으로 지우면 그런 파일이 없으므로 **지우기가 조용히
    실패한다.** `unlink(missing_ok=True)`라 예외도 안 난다. 이 저장소가
    제일 비싸다고 적어 둔 실패 방식이다. `-z`는 이스케이프를 안 한다."""
    글 = subprocess.run(
        ["git", "ls-files", "-z"], cwd=저장소,
        capture_output=True, text=True, check=True,
    ).stdout
    return [ㄱ for ㄱ in 글.split("\0") if ㄱ]


def main() -> int:
    받은것 = argparse.ArgumentParser(description=__doc__)
    받은것.add_argument("--화면저장소", required=True)
    받은것.add_argument("--매매저장소",
                    default=str(Path(__file__).resolve().parent.parent))
    받은것.add_argument("--가지", default="main", help="화면 저장소의 가지")
    받은것.add_argument("--올리기", action="store_true",
                    help="커밋하고 push한다. 안 주면 무엇이 바뀌는지만 보여 준다")
    받은것.add_argument("--메시지", default="", help="커밋 메시지")
    받은것.add_argument("--지워도됨", action="store_true",
                    help="매매 저장소에 없는 파일을 지워도 좋다")
    인자 = 받은것.parse_args()

    화면 = Path(인자.화면저장소).resolve()
    매매 = Path(인자.매매저장소).resolve()
    if not (화면 / ".git").is_dir():
        print(f"::error::화면 저장소가 아닙니다: {화면}")
        return 1

    print(f"■ 화면 저장소 {화면}의 {인자.가지}를 원격과 맞춥니다.")
    _달리기(["git", "fetch", "origin", 인자.가지], 화면)
    _달리기(["git", "checkout", 인자.가지], 화면)
    _달리기(["git", "reset", "--hard", f"origin/{인자.가지}"], 화면)

    화면것 = 파일목록(화면)
    매매것 = 파일목록(매매)
    지울것 = 지울것정하기(화면것, 매매것)

    print(f"■ 화면 {len(화면것)}개 · 매매 {len(매매것)}개")
    if 지울것:
        print(f"■ 화면 저장소에만 있는 파일 {len(지울것)}개:")
        for ㄱ in 지울것[:40]:
            print(f"    {ㄱ}")
        if len(지울것) > 40:
            print(f"    ... 그 밖에 {len(지울것) - 40}개")
        if not 인자.지워도됨:
            # **조용히 지우지 않는다.** 지워도 어디에도 안 남으므로,
            # 사람이 한 번 보게 만든다.
            print("::error::이 파일들이 사라집니다. 지워도 되면 --지워도됨을,")
            print("::error::화면 저장소에 남겨야 하면 publish_dashboard.py의")
            print("::error::`전용파일`에 적으세요.")
            return 1
        # **지웠는지 확인한다.** `missing_ok=True`는 이름이 틀려도 조용히
        # 넘어간다. 이름이 이스케이프돼 있으면 아무것도 안 지우고 지웠다고
        # 말하게 된다. 실제로 그랬다.
        못지운것 = []
        for ㄱ in 지울것:
            (화면 / ㄱ).unlink(missing_ok=True)
            if (화면 / ㄱ).exists():
                못지운것.append(ㄱ)
        if 못지운것:
            print("::error::지우지 못한 파일이 있습니다:")
            for ㄱ in 못지운것:
                print(f"::error::  {ㄱ}")
            return 1
        print(f"  {len(지울것)}개를 지웠습니다.")
    else:
        print("■ 화면 저장소에만 있는 파일이 없습니다.")

    # 매매 저장소의 지금 커밋을 그대로 풀어 덮는다.
    print("■ 매매 저장소의 파일을 풉니다.")
    묶음 = subprocess.Popen(
        ["git", "archive", "HEAD"], cwd=매매, stdout=subprocess.PIPE,
    )
    푸는것 = subprocess.Popen(
        ["tar", "-x", "-C", str(화면)], stdin=묶음.stdout,
    )
    묶음.stdout.close()
    푸는것.communicate()
    if 푸는것.returncode != 0 or 묶음.wait() != 0:
        print("::error::파일을 푸는 데 실패했습니다.")
        return 1

    _달리기(["git", "add", "-A"], 화면)
    바뀐것 = _달리기(["git", "status", "--porcelain"], 화면).splitlines()
    if not 바뀐것:
        print("■ 바뀐 것이 없습니다. 올릴 것도 없습니다.")
        return 0

    print(f"■ 바뀐 파일 {len(바뀐것)}개")
    for ㄱ in 바뀐것[:20]:
        print(f"    {ㄱ}")
    if len(바뀐것) > 20:
        print(f"    ... 그 밖에 {len(바뀐것) - 20}개")

    if not 인자.올리기:
        print("\n미리보기라 커밋하지 않았습니다. --올리기 를 주면 올립니다.")
        print("**화면 저장소가 고쳐진 채로 남아 있습니다.** 되돌리려면")
        print(f"  git -C {화면} reset --hard origin/{인자.가지}")
        return 0

    if not 인자.메시지:
        print("::error::--올리기 에는 --메시지 가 있어야 합니다.")
        return 1
    _달리기(["git", "commit", "-m", 인자.메시지], 화면)
    _달리기(["git", "push", "origin", 인자.가지], 화면)
    print("■ 올렸습니다. 배포가 끝나야 주인 화면에 반영됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
