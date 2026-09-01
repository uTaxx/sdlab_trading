"""섹터·종목·비중을 구글 시트로 관리한다.

## 왜 시트인가

**대시보드를 걷어내고 시트를 화면으로 쓰기로 했다**(`docs/설계_스트림릿을_
걷어낼까.md`). 폰에서 구글 시트 앱이 더 편하고, 표 만드는 코드가 필요 없고,
사람이 직접 열어 감사할 수 있고, **우리가 깨뜨릴 코드가 없다.**

## 시트가 원본이고 코드는 읽기만 한다

편집은 시트에서만 한다. 두 곳에서 고칠 수 있으면 충돌 처리를 만들어야
하는데, 개인용 도구에서 그 값이 비용보다 작다.

## 읽자마자 검증한다. 틀리면 매매를 멈춘다

**반쯤 잘못된 목록으로 실거래를 도는 것이 최악이다.** 종목코드 한 자리가
틀리면 엉뚱한 회사를 사고, 그 사실은 주문이 나간 뒤에야 드러난다.

그래서 읽을 때마다 확인한다.
- 종목코드가 여섯 자리인가
- 같은 종목이 두 섹터에 있지 않은가 (있으면 비중 상한이 두 배로 뚫린다)
- 없는 섹터코드를 가리키지 않는가
- 활성 종목이 섹터당 3개 이상인가 (둘이면 '섹터'가 아니다)
- 비중 상한이 0~50% 안인가

**하나라도 걸리면 예외를 던진다.** 조용히 넘어가면 화면에 아무 표시도
안 남는다.

## 탭 구조

| 탭 | 무엇 |
|---|---|
| `섹터` | 섹터코드 · 섹터명 · 활성 · 비중상한 · 전망출처 · 메모 |
| `종목` | 종목코드 · 종목명 · 시장 · 섹터코드 · 활성 · 메모 |
| `설정` | 이름 · 값 · 설명 (킬스위치·손절선 등 한 줄짜리 값들) |
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build

from muwon.sector.catalog import CATALOG, Sector, SectorMember

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DEFAULT_TITLE = "muwon406 섹터·설정"

섹터머리 = ["섹터코드", "섹터명", "활성", "비중상한", "전망출처", "성격", "메모"]
종목머리 = ["종목코드", "종목명", "시장", "섹터코드", "활성", "메모"]
설정머리 = ["이름", "값", "설명"]

MIN_LIVE_MEMBERS = 3
MAX_WEIGHT_CAP = 50.0


class SheetError(ValueError):
    """시트 내용이 매매에 쓸 수 없는 상태다."""


@dataclass(frozen=True)
class SheetContents:
    섹터: list[Sector]
    설정: dict[str, str]

    def 섹터찾기(self, 코드: str) -> Sector:
        for s in self.섹터:
            if s.코드 == 코드:
                return s
        raise KeyError(f"모르는 섹터 코드: {코드}")


def _credentials():
    key_json = os.environ.get("GDRIVE_SA_KEY_JSON")
    if not key_json:
        raise SystemExit("GDRIVE_SA_KEY_JSON 환경변수가 없습니다 (서비스 계정 JSON 키 원문).")
    return service_account.Credentials.from_service_account_info(
        json.loads(key_json), scopes=SCOPES
    )


#: 명시적으로 이 값이어야 '꺼짐'이다.
꺼짐표시 = ("N", "NO", "FALSE", "0", "X", "아니오", "끔")


def _yn(값: str) -> bool:
    """활성 여부. **빈 칸은 켜진 것으로 본다.**

    반대로 하면 시트에서 새 줄을 추가할 때마다 Y를 적어야 하고, 안 적으면
    그 종목이 **조용히 빠진다.** 화면에는 아무 표시도 안 남는다. 끄는 것은
    드문 일이니 그때만 명시하게 한다."""
    return str(값).strip().upper() not in 꺼짐표시


def parse(섹터행: list[list[str]], 종목행: list[list[str]], 설정행: list[list[str]]) -> SheetContents:
    """시트에서 읽은 날것 → 검증된 구조.

    **네트워크 없이 시험할 수 있게 따로 뺐다.** 검증 규칙이 이 함수의
    거의 전부이고, 그걸 시험하려고 매번 구글에 붙을 수는 없다."""
    섹터들: list[Sector] = []
    본코드: set[str] = set()
    for 줄 in 섹터행[1:]:
        if not 줄 or not str(줄[0]).strip():
            continue
        칸 = (list(줄) + [""] * len(섹터머리))[: len(섹터머리)]
        코드 = str(칸[0]).strip().upper()
        if 코드 in 본코드:
            raise SheetError(f"섹터코드가 겹칩니다: {코드}")
        본코드.add(코드)
        try:
            비중 = float(칸[3] or 0)
        except ValueError as e:
            raise SheetError(f"{코드}: 비중상한이 숫자가 아닙니다 ({칸[3]!r})") from e
        if not 0 < 비중 <= MAX_WEIGHT_CAP:
            raise SheetError(
                f"{코드}: 비중상한 {비중}%는 0 초과 {MAX_WEIGHT_CAP:g}% 이하여야 합니다. "
                "한 섹터에 절반 넘게 넣을 수 있으면 분산이 아닙니다"
            )
        출처 = str(칸[4] or "섹터지수").strip()
        if 출처 not in ("섹터지수", "국제시세"):
            raise SheetError(f"{코드}: 모르는 전망출처 {출처!r} (섹터지수 / 국제시세)")
        섹터들.append(
            Sector(코드=코드, 이름=str(칸[1]).strip(), 성격=str(칸[5]).strip(),
                   종목=[], 활성=_yn(칸[2]), 비중상한=비중, 전망출처=출처)
        )

    if not 섹터들:
        raise SheetError("섹터 탭이 비어 있습니다. 이 상태로는 매매 대상을 정할 수 없습니다")

    종목들: dict[str, list[SectorMember]] = {s.코드: [] for s in 섹터들}
    본종목: dict[str, str] = {}
    for 줄 in 종목행[1:]:
        if not 줄 or not str(줄[0]).strip():
            continue
        칸 = (list(줄) + [""] * len(종목머리))[: len(종목머리)]
        코드 = str(칸[0]).strip()
        if not (코드.isdigit() and len(코드) == 6):
            raise SheetError(f"종목코드가 여섯 자리 숫자가 아닙니다: {코드!r} ({칸[1]}). "
                             "한 자리만 틀려도 엉뚱한 회사를 삽니다")
        섹터코드 = str(칸[3]).strip().upper()
        if 섹터코드 not in 종목들:
            raise SheetError(f"{코드} {칸[1]}: 없는 섹터코드 {섹터코드!r}")
        if 코드 in 본종목:
            raise SheetError(
                f"{코드} {칸[1]}이 {본종목[코드]}와 {섹터코드} 양쪽에 있습니다. "
                "한 종목이 두 섹터에 있으면 그 종목만 비중 상한을 두 배로 씁니다"
            )
        본종목[코드] = 섹터코드
        시장 = str(칸[2]).strip().upper() or "KOSPI"
        if 시장 not in ("KOSPI", "KOSDAQ"):
            raise SheetError(f"{코드} {칸[1]}: 모르는 시장 {시장!r} (KOSPI / KOSDAQ)")
        종목들[섹터코드].append(
            SectorMember(symbol=코드, name=str(칸[1]).strip(), market=시장,
                         활성=_yn(칸[4]), 메모=str(칸[5]).strip())
        )

    완성 = []
    for s in 섹터들:
        멤버 = 종목들[s.코드]
        살아있는것 = [m for m in 멤버 if m.활성]
        if s.활성 and s.전망출처 == "섹터지수" and len(살아있는것) < MIN_LIVE_MEMBERS:
            raise SheetError(
                f"{s.코드} {s.이름}: 활성 종목이 {len(살아있는것)}개뿐입니다 "
                f"(최소 {MIN_LIVE_MEMBERS}개): 그러면 섹터 지수가 사실상 그 종목입니다"
            )
        완성.append(
            Sector(코드=s.코드, 이름=s.이름, 성격=s.성격, 종목=멤버,
                   활성=s.활성, 비중상한=s.비중상한, 전망출처=s.전망출처)
        )

    설정 = {}
    for 줄 in 설정행[1:]:
        if not 줄 or not str(줄[0]).strip():
            continue
        칸 = (list(줄) + ["", ""])[:3]
        설정[str(칸[0]).strip()] = str(칸[1]).strip()

    return SheetContents(섹터=완성, 설정=설정)


def catalog_rows() -> tuple[list[list[str]], list[list[str]]]:
    """지금 코드에 있는 카탈로그를 시트에 넣을 모양으로. 첫 채움에 쓴다."""
    섹터행 = [섹터머리]
    종목행 = [종목머리]
    for s in CATALOG:
        섹터행.append([s.코드, s.이름, "Y" if s.활성 else "N", f"{s.비중상한:g}",
                       s.전망출처, s.성격, ""])
        for m in s.종목:
            종목행.append([m.symbol, m.name, m.market, s.코드,
                           "Y" if m.활성 else "N", m.메모])
    return 섹터행, 종목행


def default_settings_rows() -> list[list[str]]:
    """설정 탭 초안. **값은 사람이 시트에서 고친다.**

    목록을 여기에 손으로 적지 않는다. `settings/from_sheet.py`의 기준표
    하나만 보고 만든다. 두 군데에 적어 두면 하나만 고치고 다른 하나를
    잊는데, 그러면 시트를 새로 만들 때 그 줄이 조용히 빠진다."""
    from muwon.settings.from_sheet import 기준들

    return [설정머리, *[[b.이름, b.기본, b.설명] for b in 기준들]]


def _service():
    return build("sheets", "v4", credentials=_credentials())


def read(sheet_id: str) -> SheetContents:
    """시트를 읽고 검증한다. 틀리면 SheetError를 던진다."""
    sheets = _service().spreadsheets().values()

    def 받기(탭: str) -> list[list[str]]:
        결과 = sheets.get(spreadsheetId=sheet_id, range=f"{탭}!A1:Z2000").execute(num_retries=3)
        return 결과.get("values", [])

    return parse(받기("섹터"), 받기("종목"), 받기("설정"))


def find_or_create(folder_id: str, title: str = DEFAULT_TITLE) -> tuple[str, bool]:
    """폴더에서 시트를 찾고 없으면 만든다. (시트ID, 새로만들었나)."""
    creds = _credentials()
    drive = build("drive", "v3", credentials=creds)
    found = (
        drive.files()
        .list(
            q=f"'{folder_id}' in parents and name = '{title}' and mimeType = '{SHEET_MIME}' and trashed = false",
            fields="files(id)", supportsAllDrives=True, includeItemsFromAllDrives=True,
        )
        .execute(num_retries=3)
        .get("files", [])
    )
    if found:
        return found[0]["id"], False
    만든것 = (
        drive.files()
        .create(body={"name": title, "mimeType": SHEET_MIME, "parents": [folder_id]},
                fields="id", supportsAllDrives=True)
        .execute(num_retries=3)
    )
    return 만든것["id"], True


def write_all(sheet_id: str, 섹터행, 종목행, 설정행) -> None:
    """세 탭을 통째로 덮어쓴다. **첫 채움에만 쓴다**. 그 뒤에는 사람이
    시트에서 고치고 코드는 읽기만 한다."""
    svc = _service().spreadsheets()
    있는탭 = {s["properties"]["title"] for s in svc.get(spreadsheetId=sheet_id).execute()["sheets"]}
    요청 = [
        {"addSheet": {"properties": {"title": 탭}}}
        for 탭 in ("섹터", "종목", "설정")
        if 탭 not in 있는탭
    ]
    if 요청:
        svc.batchUpdate(spreadsheetId=sheet_id, body={"requests": 요청}).execute(num_retries=3)
    for 탭, 행 in (("섹터", 섹터행), ("종목", 종목행), ("설정", 설정행)):
        svc.values().clear(spreadsheetId=sheet_id, range=f"{탭}!A1:Z5000").execute(num_retries=3)
        svc.values().update(
            spreadsheetId=sheet_id, range=f"{탭}!A1",
            valueInputOption="RAW", body={"values": 행},
        ).execute(num_retries=3)


def write_catalog(sheet_id: str, 섹터행, 종목행) -> None:
    """`섹터`와 `종목` 탭만 덮어쓴다. **`설정` 탭은 건드리지 않는다.**

    `write_all`은 설정까지 기본값으로 되돌린다. 그러면 킬스위치와 걸어 둔
    전략이 초기화되는데, 종목 목록을 늘리려다 매매 설정을 잃는 것은
    말이 안 된다. 종목을 더할 때는 이쪽을 쓴다."""
    svc = _service().spreadsheets()
    있는탭 = {s["properties"]["title"] for s in svc.get(spreadsheetId=sheet_id).execute()["sheets"]}
    요청 = [
        {"addSheet": {"properties": {"title": 탭}}}
        for 탭 in ("섹터", "종목")
        if 탭 not in 있는탭
    ]
    if 요청:
        svc.batchUpdate(spreadsheetId=sheet_id, body={"requests": 요청}).execute(num_retries=3)
    for 탭, 행 in (("섹터", 섹터행), ("종목", 종목행)):
        svc.values().clear(spreadsheetId=sheet_id, range=f"{탭}!A1:Z5000").execute(num_retries=3)
        svc.values().update(
            spreadsheetId=sheet_id, range=f"{탭}!A1",
            valueInputOption="RAW", body={"values": 행},
        ).execute(num_retries=3)


def append_settings(sheet_id: str, 줄들, svc=None) -> int:
    """`설정` 탭 맨 아래에 줄을 한꺼번에 붙인다. 있는 값은 안 건드린다.

    한 줄씩 `update_setting`을 부르면 줄 수만큼 읽고 쓰기를 반복한다.
    다섯 개면 열 번이고, 그중 하나만 시간 초과가 나도 통째로 실패한다.
    붙이는 일은 한 번에 할 수 있으므로 한 번에 한다."""
    if not 줄들:
        return 0
    svc = svc or _service().spreadsheets()
    svc.values().append(
        spreadsheetId=sheet_id, range="설정!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [list(줄) for 줄 in 줄들]},
    ).execute(num_retries=3)
    return len(줄들)


def update_setting(sheet_id: str, 이름: str, 글자: str, svc=None, 설명: str = "") -> str:
    """`설정` 탭의 한 줄을 고친다. 옛 값을 돌려준다.

    **텔레그램에서 온 변경도 여기로 들어온다.** 시트가 원본이라는 규칙을
    깨지 않기 위해서다. 코드가 따로 기억하는 값이 생기면 시트를 봐도
    지금 뭐가 걸려 있는지 알 수 없게 된다. 텔레그램 명령은 '사람이 시트를
    고친 것'과 같은 취급이다.

    줄이 없으면 맨 아래에 새로 만든다. 기준을 새로 추가했는데 시트가
    아직 옛것일 때 그냥 터지면 고칠 방법이 없다."""
    svc = svc or _service().spreadsheets()
    칸 = svc.values().get(spreadsheetId=sheet_id, range="설정!A1:C1000").execute(num_retries=3)
    줄들 = 칸.get("values", [])

    for i, 줄 in enumerate(줄들):
        if 줄 and str(줄[0]).strip() == 이름:
            옛것 = str(줄[1]).strip() if len(줄) > 1 else ""
            svc.values().update(
                spreadsheetId=sheet_id, range=f"설정!B{i + 1}",
                valueInputOption="RAW", body={"values": [[글자]]},
            ).execute(num_retries=3)
            return 옛것

    # 줄을 새로 만들 때 설명 칸을 채운다. 시트는 사람이 열어 보는 곳이라
    # "텔레그램에서 추가됨"만 적혀 있으면 그 값이 무엇인지 알 수 없다.
    svc.values().append(
        spreadsheetId=sheet_id, range="설정!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": [[이름, 글자, 설명 or "텔레그램에서 추가됨"]]},
    ).execute(num_retries=3)
    return ""
