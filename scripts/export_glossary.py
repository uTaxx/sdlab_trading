"""용어 사전을 읽을 수 있는 문서(docs/용어사전.md)로 뽑는다.

대시보드를 안 켜도, 폰이 아니라 저장소에서도 읽을 수 있어야 한다. 손으로
두 벌 관리하면 반드시 어긋나므로 코드에서 뽑고, 어긋나면 테스트가 잡는다.

    python scripts/export_glossary.py          # 파일 갱신
    python scripts/export_glossary.py --check  # 어긋났는지만 확인
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.dashboard.glossary import TERMS

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "용어사전.md"


def render() -> str:
    lines = [
        "# 용어 사전",
        "",
        "> 이 파일은 `src/muwon/dashboard/glossary.py`에서 자동으로 뽑습니다.",
        "> 고칠 때는 그 파일을 고치고 `python scripts/export_glossary.py`를 돌리세요.",
        "",
        "화면과 알림에 나오는 주식·매매 용어입니다. 각 항목은 **뜻**(그 말이",
        "무엇인지)과 **→ 어떻게 읽나**(그 숫자를 봤을 때 무엇을 판단하면 되는지)로",
        "나뉩니다. 뜻만 알아도 화면을 못 읽는 경우가 많아서 판단 기준을 따로 뒀습니다.",
        "",
        f"총 {len(TERMS)}개.",
        "",
    ]
    for term in TERMS.values():
        영문 = f" (`{term.영문}`)" if term.영문 else ""
        lines += [
            f"### {term.이름}{영문}",
            "",
            term.뜻,
            "",
            f"→ {term.읽는법}",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="용어 사전을 마크다운으로 뽑는다")
    parser.add_argument("--check", action="store_true", help="파일이 최신인지만 확인한다")
    args = parser.parse_args()

    text = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            raise SystemExit(
                "❌ docs/용어사전.md가 glossary.py와 어긋났습니다. "
                "python scripts/export_glossary.py 를 돌리세요."
            )
        print("✅ 최신입니다")
        return

    OUTPUT.write_text(text, encoding="utf-8")
    print(f"✅ {OUTPUT.name}: {len(TERMS)}개")


if __name__ == "__main__":
    main()
