"""가설·검증 기록을 구글 시트에 한 줄 덧붙인다.

숫자는 아티팩트에, 판단은 커밋 메시지에 흩어져 있어서 "그때 그 가설이 왜
기각됐더라"를 찾으려면 여러 곳을 뒤져야 했다. 코드를 안 보는 사람은 아예
못 찾는다. 시트 한 장에 쌓으면 브라우저만 있으면 읽힌다.

사용 예:
    # 한 줄 직접 남기기
    python scripts/log_hypothesis.py \\
      --folder-id XXX \\
      --question "2022년 -39%의 원인이 무엇인가" \\
      --hypothesis "약세장 문턱이 낮아서다" \\
      --reason "BEAR 구간이 전체의 50%였다" \\
      --method "58종목 2022년 구간 진단" \\
      --result "BEAR 116일 동안 매수 0건. 손실은 전부 강세 판정일 진입" \\
      --verdict 기각 \\
      --action "국면 분류기 쪽으로 방향을 돌림"

    # 지난 기록 한꺼번에 채우기
    python scripts/log_hypothesis.py --folder-id XXX --from-json docs/가설기록.json
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.cloud.hypothesis_log import HypothesisRow, append, rows_from_json


def main() -> None:
    parser = argparse.ArgumentParser(description="가설·검증 기록을 구글 시트에 남긴다")
    parser.add_argument("--folder-id", required=True, help="구글드라이브 폴더 ID")
    parser.add_argument("--title", default="", help="시트 이름 (기본값 사용 권장)")
    parser.add_argument("--from-json", default="", help="여러 줄을 담은 JSON 파일")

    parser.add_argument("--date", default="", help="날짜 (비우면 오늘)")
    parser.add_argument("--question", default="", help="무엇을 알고 싶었나")
    parser.add_argument("--hypothesis", default="", help="이럴 것이다")
    parser.add_argument("--reason", default="", help="왜 그렇게 생각했나")
    parser.add_argument("--method", default="", help="어떻게 확인했나")
    parser.add_argument("--result", default="", help="나온 숫자")
    parser.add_argument("--verdict", default="", choices=["", "채택", "기각", "보류"],
                        help="채택 / 기각 / 보류")
    parser.add_argument("--action", default="", help="그래서 뭘 바꿨나 (안 바꿨으면 그렇게)")
    parser.add_argument("--target", default="", help="유니버스·기간")
    parser.add_argument("--commit", default="", help="비우면 현재 HEAD")
    parser.add_argument("--link", default="", help="워크플로 실행 링크 등")
    args = parser.parse_args()

    if args.from_json:
        rows = rows_from_json(args.from_json)
    else:
        if not args.hypothesis:
            raise SystemExit("--hypothesis 또는 --from-json 중 하나는 있어야 합니다")
        rows = [
            HypothesisRow(
                날짜=args.date or datetime.now(UTC).date().isoformat(),
                무엇을_알고_싶었나=args.question,
                가설=args.hypothesis,
                왜_그렇게_생각했나=args.reason,
                어떻게_확인했나=args.method,
                결과=args.result,
                판정=args.verdict,
                그래서_뭘_바꿨나=args.action,
                대상=args.target,
                커밋=args.commit or _git_sha(),
                링크=args.link,
            )
        ]

    kwargs = {"title": args.title} if args.title else {}
    url = append(args.folder_id, rows, **kwargs)
    print(f"✅ {len(rows)}줄 기록: {url}")


def _git_sha() -> str:
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


if __name__ == "__main__":
    main()
