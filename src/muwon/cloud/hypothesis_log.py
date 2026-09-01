"""가설과 검증 결과를 구글 시트에 한 줄씩 쌓는다.

왜 시트인가. 지금 실험 기록은 세 군데에 흩어져 있다. 숫자는 GitHub Actions
로그(만료됨)와 아티팩트에, 판단은 커밋 메시지와 설계안 문서에. 그래서
"그때 그 가설이 왜 기각됐더라"를 찾으려면 세 곳을 다 뒤져야 하고, 코드를
안 보는 사람은 아예 접근할 수가 없다.

시트 한 장에 한 줄씩 쌓으면 브라우저만 있으면 읽힌다. 그리고 append이므로
과거 줄이 덮이지 않는다. 기각된 가설이 남아 있는 게 이 기록의 핵심이다.
같은 걸 두 번 시험하지 않으려면 실패가 보여야 한다.

**자동으로 채울 수 있는 칸과 없는 칸이 갈린다.** 실행 조건·커밋·링크는
기계가 안다. 하지만 '무엇을 알고 싶었나', '왜 그렇게 생각했나', '판정'은
사람(또는 그 자리에서 판단한 에이전트)이 써야 한다. 그래서 이 모듈은
줄을 통째로 받아 넣기만 하고, 무엇을 쓸지는 부르는 쪽이 정한다.

인증은 muwon.db 동기화와 같은 서비스 계정을 쓴다. 다만 스코프에
spreadsheets가 추가로 필요하고, GCP 프로젝트에서 Sheets API가 켜져 있어야
한다. 안 켜져 있으면 명확한 오류로 알려 준다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DEFAULT_TITLE = "muwon406 가설·검증 기록"


@dataclass
class HypothesisRow:
    """시트 한 줄. 칸 이름을 그대로 사람 말로 둔다. 이 기록을 읽는 사람이
    코드를 안 볼 수도 있다."""

    날짜: str = ""
    무엇을_알고_싶었나: str = ""
    가설: str = ""
    왜_그렇게_생각했나: str = ""
    어떻게_확인했나: str = ""
    결과: str = ""
    판정: str = ""  # 채택 / 기각 / 보류
    그래서_뭘_바꿨나: str = ""
    대상: str = ""  # 유니버스·기간
    커밋: str = ""
    링크: str = ""

    def as_values(self) -> list[str]:
        data = asdict(self)
        return [str(data[f.name]) for f in fields(self)]


def header_row() -> list[str]:
    return [f.name.replace("_", " ") for f in fields(HypothesisRow)]


def _credentials():
    key_json = os.environ.get("GDRIVE_SA_KEY_JSON")
    if not key_json:
        raise SystemExit("GDRIVE_SA_KEY_JSON 환경변수가 없습니다 (서비스 계정 JSON 키 원문).")
    return service_account.Credentials.from_service_account_info(
        json.loads(key_json), scopes=SCOPES
    )


def find_or_create_sheet(folder_id: str, title: str = DEFAULT_TITLE) -> str:
    """폴더에서 시트를 찾고, 없으면 만들어 머리글까지 넣는다."""
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    found = (
        drive.files()
        .list(
            q=f"name = '{title}' and '{folder_id}' in parents and trashed = false",
            fields="files(id)",
            spaces="drive",
            corpora="allDrives",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    ).get("files", [])
    if found:
        return found[0]["id"]

    created = (
        drive.files()
        .create(
            body={"name": title, "mimeType": SHEET_MIME, "parents": [folder_id]},
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )
    return created["id"]


def needs_header(existing: dict) -> bool:
    """머리글을 넣어야 하는가. 첫 칸이 비어 있으면 넣는다.

    시트를 만들 때 한 번만 넣으면 안 된다. 실제로 시트 생성(Drive API)은
    성공하고 값 쓰기(Sheets API)만 실패한 적이 있는데, 그러면 다음 실행은
    '시트가 이미 있다'고 판단해 머리글 없이 데이터부터 채운다. 칸 이름이
    없는 표는 아무도 못 읽는다."""
    return not existing.get("values")


def _ensure_header(creds, sheet_id: str) -> None:
    sheets = build("sheets", "v4", credentials=creds)
    existing = (
        sheets.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="A1:A1")
        .execute()
    )
    if needs_header(existing):
        _append_values(creds, sheet_id, [header_row()])


def updated_row_count(result: dict, values: list) -> int:
    """구글 응답에서 실제로 들어간 줄 수를 꺼낸다.

    updatedRows는 정수다. 여기에 len()을 씌워서 터뜨린 적이 있다. 응답
    모양을 확인하지 않고 '없으면 values를 쓰자'는 기본값을 넣은 게 원인이다.
    타입이 섞이는 기본값은 넣지 않는다."""
    return int(result.get("updates", {}).get("updatedRows", len(values)))


def _append_values(creds, sheet_id: str, values: list[list[str]]) -> int:
    sheets = build("sheets", "v4", credentials=creds)
    result = (
        sheets.spreadsheets()
        .values()
        .append(
            spreadsheetId=sheet_id,
            range="A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        )
        .execute()
    )
    return updated_row_count(result, values)


def append(folder_id: str, rows: list[HypothesisRow], title: str = DEFAULT_TITLE) -> str:
    """줄들을 시트 끝에 덧붙이고 시트 URL을 돌려준다.

    덮어쓰지 않는다. 기각된 가설이 남아 있어야 같은 걸 두 번 시험하지 않는다."""
    if not rows:
        raise ValueError("남길 줄이 없습니다")
    try:
        creds = _credentials()
        sheet_id = find_or_create_sheet(folder_id, title)
        _ensure_header(creds, sheet_id)
        _append_values(creds, sheet_id, [r.as_values() for r in rows])
    except HttpError as e:
        raise SystemExit(_explain(e)) from e
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"


def _explain(error: HttpError) -> str:
    """구글 API 오류를 무엇을 해야 하는지로 바꿔 준다.

    'HttpError 403'만 보고는 서비스 계정 권한 문제인지 API가 안 켜진 건지
    알 수 없다. 둘은 고치는 방법이 전혀 다르다."""
    detail = str(error)
    if "accessNotConfigured" in detail or "has not been used in project" in detail:
        return (
            "❌ 구글 Sheets API가 이 프로젝트에서 꺼져 있습니다.\n"
            "   GCP 콘솔 → API 및 서비스 → 라이브러리에서 'Google Sheets API'를 "
            "사용 설정한 뒤 다시 실행하세요.\n"
            f"   (원문: {detail[:300]})"
        )
    if "insufficientPermissions" in detail or "forbidden" in detail.lower():
        return (
            "❌ 서비스 계정이 이 폴더에 쓸 권한이 없습니다.\n"
            "   드라이브 폴더를 서비스 계정 이메일에 '편집자'로 공유했는지 "
            "확인하세요.\n"
            f"   (원문: {detail[:300]})"
        )
    return f"❌ 구글 시트 기록 실패: {detail[:500]}"


def rows_from_json(path: str) -> list[HypothesisRow]:
    """JSON 파일에서 여러 줄을 읽는다. 지난 기록을 한 번에 채워 넣을 때 쓴다."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    known = {f.name for f in fields(HypothesisRow)}
    rows = []
    for item in data:
        # 모르는 키는 조용히 버리지 않고 알려 준다. 오타 하나로 내용이
        # 통째로 빠지면 기록의 뜻이 없다.
        unknown = set(item) - known
        if unknown:
            raise SystemExit(f"알 수 없는 칸 이름: {sorted(unknown)}")
        rows.append(HypothesisRow(**item))
    return rows
