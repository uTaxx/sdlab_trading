"""muwon.db 상태 파일을 구글드라이브와 주고받는 CLI.

실제 로직은 src/muwon/cloud/gdrive_sync.py에 있다. 대시보드도 같은 로직을
쓰기 때문에 여기 있으면 안 되고(스크립트는 패키지로 import가 안 됨), 이
파일은 그 로직을 커맨드라인에서 쓰기 위한 얇은 래퍼다.

사용 예:
    python scripts/gdrive_sync.py download --folder-id XXX --filename muwon.db --out ./muwon.db
    python scripts/gdrive_sync.py upload --folder-id XXX --filename muwon.db --path ./muwon.db
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.cloud.gdrive_sync import download, upload


def main() -> None:
    parser = argparse.ArgumentParser(description="구글드라이브 상태 파일(muwon.db) 동기화")
    sub = parser.add_subparsers(dest="command", required=True)

    dl = sub.add_parser("download", help="구글드라이브 -> 로컬")
    dl.add_argument("--folder-id", required=True)
    dl.add_argument("--filename", required=True)
    dl.add_argument("--out", required=True)

    up = sub.add_parser("upload", help="로컬 -> 구글드라이브 (있으면 덮어쓰기)")
    up.add_argument("--folder-id", required=True)
    up.add_argument("--filename", required=True)
    up.add_argument("--path", required=True)

    args = parser.parse_args()
    if args.command == "download":
        download(args.folder_id, args.filename, args.out)
    elif args.command == "upload":
        upload(args.folder_id, args.filename, args.path)


if __name__ == "__main__":
    main()
