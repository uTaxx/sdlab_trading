"""muwon.db 상태 파일을 구글드라이브와 주고받는 핵심 로직.

GitHub Actions 러너나 Streamlit Cloud 컨테이너는 매번(또는 재배포마다) 새
디스크로 뜬다. 보유 종목·가상현금(positions/engine_state 테이블)이
이어져야 하는 이 프로젝트 구조상, 프로세스가 시작할 때 이 모듈로 muwon.db를
구글드라이브에서 내려받고, 상태가 바뀌면 다시 올려서 다음 실행/다른
프로세스가 이어받게 한다. `scripts/gdrive_sync.py`(CLI)와
`src/muwon/dashboard/app.py`(대시보드)가 둘 다 이 모듈을 쓴다.

서비스 계정으로 인증한다. 사람이 브라우저로 로그인해서 매번 토큰을 새로
받아야 하는 OAuth 사용자 인증 흐름은 GitHub Actions/Streamlit Cloud처럼
사람이 개입할 수 없는 환경엔 안 맞는다. 설정 방법(GCP 서비스 계정 만들기,
드라이브 폴더 공유, 시크릿 등록)은 docs/deploy_github_actions.md 참고.
"""

from __future__ import annotations

import json
import os
import tempfile

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]


def _build_service():
    key_json = os.environ.get("GDRIVE_SA_KEY_JSON")
    if not key_json:
        raise SystemExit("GDRIVE_SA_KEY_JSON 환경변수가 없습니다 (서비스 계정 JSON 키 원문).")
    info = json.loads(key_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def _find_file_id(service, folder_id: str, filename: str) -> str | None:
    query = f"name = '{filename}' and '{folder_id}' in parents and trashed = false"
    result = (
        service.files()
        .list(
            q=query,
            fields="files(id, name)",
            spaces="drive",
            corpora="allDrives",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = result.get("files", [])
    return files[0]["id"] if files else None


def download(folder_id: str, filename: str, out_path: str) -> None:
    service = _build_service()
    file_id = _find_file_id(service, folder_id, filename)
    if file_id is None:
        print(
            f"구글드라이브에 '{filename}'이 아직 없습니다. 첫 실행이면 정상이며, "
            "새 상태(초기 현금)로 시작합니다."
        )
        return

    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    # 임시 파일에 다 받은 다음 원자적으로 교체한다. 대시보드처럼 백그라운드에서
    # 주기적으로 다시 내려받는 동안에도, 그 파일을 동시에 읽는 쪽(DB 쿼리)이
    # 반쯤 쓰인 파일을 보는 일이 없게 한다.
    #
    # 임시 파일 이름은 호출마다 고유해야 한다. 대시보드는 시작 시 1회
    # 동기화와 30초 주기 동기화가 겹칠 수 있는데, 고정된 이름("<out>.tmp")을
    # 쓰면 먼저 끝난 쪽이 그 파일을 치워버려서 나중 쪽의 os.replace가
    # FileNotFoundError로 죽는다(실제로 발생).
    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=f".{os.path.basename(out_path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        os.replace(tmp_path, out_path)
    finally:
        # 성공하면 os.replace로 이미 사라졌으니 no-op, 중간에 실패했으면 정리한다.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    print(f"다운로드 완료: {filename} -> {out_path}")


def upload(folder_id: str, filename: str, path: str) -> None:
    service = _build_service()
    file_id = _find_file_id(service, folder_id, filename)
    media = MediaFileUpload(path, resumable=True)

    if file_id is None:
        metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(
            body=metadata, media_body=media, fields="id", supportsAllDrives=True
        ).execute()
        print(f"신규 업로드 완료: {filename}")
    else:
        service.files().update(
            fileId=file_id, media_body=media, supportsAllDrives=True
        ).execute()
        print(f"업데이트 완료: {filename}")
