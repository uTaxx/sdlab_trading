"""텔레그램에서 온 명령을 읽어 **기준을 고친다.**

폰에서 `/설정 max_position_weight 12` 라고 보내면 구글 시트의 그 줄이
바뀌고, 다음 실행부터 그 값으로 돈다.

## 서버 없이 어떻게 받나

웹훅을 걸지 않는다 — 받으려면 상시 도는 서버가 필요한데 이 저장소에는
그런 게 없다. 대신 워크플로가 주기적으로 `getUpdates`로 **물어보러 간다.**
늦어야 몇 분이고, 설정을 바꾸는 일에 몇 분은 늦어도 된다.

## 같은 명령을 두 번 실행하지 않는다

텔레그램은 "여기까지 읽었다"(offset)를 우리가 알려 줄 때까지 같은 메시지를
계속 준다. 그 표시를 DB에 남긴다. 안 남기면 **워크플로가 돌 때마다 어제
명령이 다시 실행된다.**

## 안전 규칙

- 저장된 chat_id에서 온 것만 듣는다 (봇 이름은 누구나 알 수 있다)
- **매매를 켜는 것은 여기서 못 한다.** 끄는 것만 된다
- 값 검증은 시트에서 읽을 때와 같은 규칙을 쓴다
- 모르는 말에는 안내만 한다 — 추측해서 실행하지 않는다

사용 예:
    python scripts/telegram_control.py
    python scripts/telegram_control.py --dry-run   # 읽기만, 아무것도 안 고침
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from muwon.cloud import strategy_approval as 전략승인
from muwon.cloud.approval import approve_in_sheet, read_today, set_decisions
from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read, update_setting
from muwon.config import bootstrap_settings
from muwon.db.session import ensure_schema, make_session_factory
from muwon.notify.telegram import TelegramNotifier
from muwon.notify.telegram_api import (
    answer_callback,
    edit_text,
    get_updates,
    webhook_info,
)
from muwon.notify.telegram_buttons import (
    keyboard,
    parse_callback,
    글에_상태붙이기,
    누른뒤말,
    상태표시,
    예약키보드,
    전략상태블록,
    확인키보드,
)
from muwon.notify.telegram_control import parse_command, 도움말, 바꾼말
from muwon.settings.from_sheet import apply, describe, parse_settings, 기준표
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import get_definition, list_definitions

KST = ZoneInfo("Asia/Seoul")
#: 한 번에 몇 개까지 처리할 것인가. 밀려 있어도 한꺼번에 다 실행하면
#: 무슨 일이 일어났는지 따라갈 수가 없다.
MAX_PER_RUN = 20


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true", help="읽기만 하고 아무것도 안 고친다")
    parser.add_argument("--touch-when-changed", default="",
                        help="처리한 것이 있으면 이 파일을 만든다 (워크플로가 DB를 올릴지 판단)")
    parser.add_argument("--from-payload", default="",
                        help="n8n이 넘겨준 업데이트 JSON 하나를 처리한다 (물어보러 가지 않는다)")
    args = parser.parse_args()

    ensure_schema(bootstrap_settings.database_url)
    service = build_settings_service()
    cfg = service.get_telegram_config()
    if not cfg.bot_token:
        print("텔레그램 봇 토큰이 없습니다 — 할 일이 없습니다.", file=sys.stderr)
        return 0
    if not cfg.chat_id:
        print("chat_id가 없습니다. **누구 말을 들을지 모르면 아무 말도 듣지 않습니다.**",
              file=sys.stderr)
        return 0

    sheet_id = args.sheet_id
    if not sheet_id:
        if not args.folder_id:
            raise SystemExit("MUWON_SHEET_ID도 GDRIVE_FOLDER_ID도 없습니다.")
        sheet_id, _ = find_or_create(args.folder_id, DEFAULT_TITLE)

    보내기 = TelegramNotifier(service).send
    offset = service.get_telegram_offset()

    if args.from_payload:
        # ── n8n이 넘겨준 것 ────────────────────────────────────────
        # 물어보러 가지 않는다. n8n이 텔레그램 웹훅을 받아 그대로 넘겨준
        # 것이므로, 여기서 또 getUpdates를 부르면 웹훅과 충돌해 터진다.
        업데이트 = _payload_updates(args.from_payload)
        print(f"■ n8n이 넘겨준 것 {len(업데이트)}개")
        # 읽은 위치는 안 건드린다 — 폴링으로 되돌아갈 때 쓸 값이다.
        마지막_고정 = True
    else:
        # ── 우리가 물어보러 갈 때 ─────────────────────────────────
        # 웹훅이 걸려 있으면 getUpdates는 영영 빈손이다(한 봇을 두 곳에서
        # 받을 수 없다). 그 사실이 어디에도 안 나타나므로 먼저 물어본다.
        정보 = webhook_info(cfg.bot_token)
        # **언제나 한 줄은 찍는다.** 물어보기에 실패했을 때와 웹훅이 없을
        # 때가 똑같이 조용하면, 조용한 것이 무슨 뜻인지 알 수가 없다.
        # 이 진단을 만든 이유가 바로 그 모호함을 없애는 것이었다.
        if 정보.get("ok") is False:
            print(f"■ 봇 상태를 못 물어봤습니다 — {정보.get('description')}")
        elif 정보.get("url"):
            print(f"■ 이 봇은 **웹훅으로 받고 있습니다**: {정보['url']}")
            print("  n8n이 받아 넘겨주는 구조입니다 — 여기서 물어보러 가면 충돌합니다.")
            print("  이 워크플로의 schedule은 꺼 두는 것이 맞습니다.")
            return 0
        else:
            밀린것 = 정보.get("pending_update_count", 0)
            print(f"■ 이 봇에 **웹훅이 없습니다** — 물어보러 가는 방식으로 돕니다"
                  f" (밀려 있는 것 {밀린것}개)")
        업데이트 = get_updates(cfg.bot_token, offset)
        print(f"■ 새 메시지 {len(업데이트)}개 (offset {offset})")
        마지막_고정 = False

    처리수, 마지막 = 0, offset
    for u in 업데이트[:MAX_PER_RUN]:
        if not 마지막_고정:
            마지막 = max(마지막, int(u.get("update_id", 0)) + 1)
        # ── 버튼을 눌렀을 때 ──────────────────────────────────────
        누른것 = u.get("callback_query")
        if 누른것:
            보낸이 = str(((누른것.get("message") or {}).get("chat") or {}).get("id", ""))
            if 보낸이 != str(cfg.chat_id):
                print(f"  모르는 chat_id({보낸이})가 누른 버튼은 버립니다")
                continue
            print(f"  버튼: {누른것.get('data')!r}")
            처리수 += 1
            if args.dry_run:
                print(f"    → {parse_callback(누른것.get('data', '')).종류} (--dry-run이라 실행 안 함)")
                continue
            try:
                _버튼처리(누른것, sheet_id, cfg)
            except Exception as e:  # noqa: BLE001 — 하나가 터져도 나머지는 처리한다
                print(f"    터짐: {type(e).__name__}: {e}", file=sys.stderr)
                answer_callback(cfg.bot_token, 누른것["id"], f"문제가 생겼습니다: {type(e).__name__}")
            continue

        # ── 글로 보냈을 때 ────────────────────────────────────────
        메시지 = u.get("message") or {}
        보낸이 = str((메시지.get("chat") or {}).get("id", ""))
        글 = (메시지.get("text") or "").strip()
        if not 글:
            continue

        # **정해 둔 사람만.** 봇 이름은 누구나 알 수 있다.
        if 보낸이 != str(cfg.chat_id):
            print(f"  모르는 chat_id({보낸이})에서 온 말은 버립니다: {글[:40]!r}")
            continue

        print(f"  받음: {글!r}")
        처리수 += 1
        if args.dry_run:
            print(f"    → {parse_command(글).종류} (--dry-run이라 실행 안 함)")
            continue
        try:
            보내기(_처리(글, sheet_id, service))
        except Exception as e:  # noqa: BLE001 — 한 명령이 터져도 나머지는 처리한다
            print(f"    터짐: {type(e).__name__}: {e}", file=sys.stderr)
            보내기(f"⚠️ 그 명령을 처리하다 문제가 생겼습니다.\n{type(e).__name__}: {e}")

    if not args.dry_run and not 마지막_고정 and 마지막 != offset:
        # **여기까지 읽었다고 남긴다.** 안 남기면 다음 실행에서 또 실행된다.
        service.set_telegram_offset(마지막)
    print(f"■ 처리 {처리수}건 · 다음 offset {마지막} · 상태 DB 고침 {_DB고쳤나}")

    # 처리한 것이 없으면 DB를 올리지 않는다. 10분마다 무조건 올리면 다른
    # 워크플로가 같은 파일을 올리는 순간과 겹칠 수 있고, 그러면 한쪽 변경이
    # 통째로 덮인다. 바뀐 게 있을 때만 올리면 그 창이 거의 사라진다.
    #
    # **읽은 위치만 보면 안 된다.** n8n이 넘겨주는 길(`--from-payload`)에서는
    # offset을 아예 안 건드린다. 전략 승인 버튼이 상태 DB에 쓰는데 그 길로
    # 들어오므로, DB를 고쳤는지도 같이 봐야 한다.
    올릴까 = (마지막 != offset) or _DB고쳤나
    if args.touch_when_changed and not args.dry_run and 올릴까:
        Path(args.touch_when_changed).write_text("changed\n", encoding="utf-8")
    return 0


def _payload_updates(글: str) -> list[dict]:
    """n8n이 넘겨준 글 → 업데이트 목록.

    n8n의 텔레그램 트리거는 **업데이트 하나**를 넘긴다. 목록으로 와도
    받아 준다 — 나중에 여러 개를 묶어 보내게 바꿔도 여기가 안 깨진다."""
    것 = json.loads(글)
    if isinstance(것, dict):
        # 텔레그램 트리거가 update를 통째로 주기도 하고, 그 안의 body만
        # 주기도 한다. 둘 다 받는다 — n8n 설정 하나 때문에 안 먹으면
        # 원인을 찾기 어렵다.
        if "message" in 것 or "callback_query" in 것:
            return [것]
        if isinstance(것.get("body"), dict):
            return [것["body"]]
        raise SystemExit(f"업데이트 같지 않습니다 (열쇠: {sorted(것)[:8]})")
    if isinstance(것, list):
        return 것
    raise SystemExit(f"업데이트를 못 읽었습니다: {type(것).__name__}")


#: 이번 실행에서 상태 DB를 고쳤나. **여기가 켜져야 DB가 드라이브로 올라간다.**
#:
#: 매수 승인은 시트에만 쓰므로 이 값이 안 켜진다. 전략 승인은 상태 DB에 쓴다.
#: 안 올리면 러너가 사라질 때 예약이 통째로 없어지고, 그런데도 화면에는
#: "선택했습니다"가 뜬다. 조용히 성공한 척하는 실패의 전형이다.
_DB고쳤나 = False


def _전략이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001 — 이름을 못 찾는다고 버튼이 죽으면 안 된다
        return 키


def _전략버튼처리(c, 누른것: dict, cfg) -> None:
    """전략 변경 버튼. **한 번 눌러서는 안 바뀐다.**

    누르면 예약만 되고 확인 버튼이 뜬다. 확인까지 눌러도 그날 안 바뀐다.
    다음 거래일 매수 후보 산출 전에 반영하므로 그 사이에 취소할 수 있다.

    규칙은 전부 `cloud/strategy_approval.py`가 지킨다. 여기서 따로 검사하면
    규칙이 두 벌이 되고, 둘이 어긋나도 아무것도 안 빨개진다."""
    토큰, 질문id = cfg.bot_token, 누른것["id"]
    메시지 = 누른것.get("message") or {}
    chat_id = str((메시지.get("chat") or {}).get("id", ""))
    message_id = 메시지.get("message_id")

    service = build_settings_service()
    지금키 = (service.get_strategy_selection().active_keys or ("",))[0]
    아는것 = [ㅈ.key for ㅈ in list_definitions()]

    session_factory = make_session_factory(bootstrap_settings.database_url)
    with session_factory() as session:
        if c.종류 == "전략취소":
            결과 = 전략승인.취소하기(session)
            판, 붙일글 = None, 전략상태블록()
        elif c.종류 == "전략고름":
            결과 = 전략승인.고르기(
                session, c.날짜, 지금키, c.전략키, 아는것,
                승인경로="텔레그램",
            )
            이름 = _전략이름(c.전략키)
            판 = 확인키보드(c.전략키, 이름, c.날짜) if 결과.된것 else None
            붙일글 = 전략상태블록(c.전략키, 이름, 확정됐나=False) if 결과.된것 else ""
        else:  # 전략확정
            결과 = 전략승인.확정하기(session, c.날짜, c.전략키)
            이름 = _전략이름(c.전략키)
            판 = 예약키보드(c.날짜) if 결과.된것 else None
            붙일글 = 전략상태블록(c.전략키, 이름, 확정됐나=True) if 결과.된것 else ""

        if 결과.된것:
            session.commit()
            global _DB고쳤나
            _DB고쳤나 = True
        else:
            session.rollback()

    if not 결과.된것:
        # 안 됐으면 판을 안 건드린다. 화면이 지금 상태를 계속 보여 줘야
        # 사람이 다음에 무엇을 누를지 안다.
        answer_callback(토큰, 질문id, 결과.말, show_alert=True)
        print(f"    → 전략 버튼 거절: {결과.말}")
        return

    if message_id:
        # 상태 블록을 통째로 갈아 끼운다. 안 자르면 누를 때마다 글이 길어져서
        # 정작 순위표가 화면 밖으로 밀려난다.
        몸통 = 메시지.get("text", "").split(상태표시)[0].rstrip()
        edit_text(토큰, chat_id, message_id, 몸통 + "\n\n" + 붙일글, 판)
    answer_callback(토큰, 질문id, 결과.말)
    print(f"    → 전략 버튼 {c.종류}: {c.전략키 or '(없음)'}")


def _버튼처리(누른것: dict, sheet_id: str, cfg) -> None:
    """버튼 하나를 눌렀을 때. **누른 결과가 화면에 바로 보여야 한다.**

    답을 안 보내면 버튼이 계속 도는 표시로 남고, 판을 안 갈아 끼우면 방금
    누른 게 먹었는지 몰라서 또 누르게 된다. 둘 다 한다."""
    토큰, 질문id = cfg.bot_token, 누른것["id"]
    메시지 = 누른것.get("message") or {}
    chat_id = str((메시지.get("chat") or {}).get("id", ""))
    message_id = 메시지.get("message_id")

    c = parse_callback(누른것.get("data", ""))
    if c.종류 == "모름":
        answer_callback(토큰, 질문id, c.말, show_alert=True)
        return

    오늘 = datetime.now(KST).date()

    # 전략 버튼은 매수 승인과 길이 다르다. 시트가 아니라 상태 DB를 보고,
    # 날짜 검사도 저쪽에서 한다 — 어제 버튼을 눌렀을 때 돌려줄 말이 다르다.
    if c.종류 in ("전략고름", "전략확정", "전략취소"):
        _전략버튼처리(c, 누른것, cfg)
        return

    if c.날짜 != 오늘:
        # 어제 목록의 버튼이다. 어차피 사지 않지만(승인 규칙 ②), 눌린 채로
        # 두면 나중에 기록을 읽을 때 헷갈린다.
        answer_callback(토큰, 질문id,
                        f"{c.날짜} 후보라 이제 못 씁니다. 오늘 목록에서 눌러 주세요.",
                        show_alert=True)
        return

    후보, _ = read_today(sheet_id, 오늘)
    있는것 = {c2.symbol: c2.name for c2 in 후보}

    if c.종류 in ("전부승인", "전부거절"):
        값 = "Y" if c.종류 == "전부승인" else "N"
        결정 = dict.fromkeys(있는것, 값)
    else:
        if c.symbol not in 있는것:
            answer_callback(토큰, 질문id, "오늘 후보에 없는 종목입니다.", show_alert=True)
            return
        결정 = {c.symbol: "Y" if c.종류 == "승인" else "N"}

    적은것, _ = set_decisions(sheet_id, 오늘, 결정)
    후보, 지금결정 = read_today(sheet_id, 오늘)

    # 버튼 글자만 바꾸면 나중에 대화를 훑을 때 무슨 일이 있었는지 안 보인다.
    # 글에도 지금 상태를 적어 둔다 — 버튼은 지금 누르는 것이고 글은 남는다.
    if message_id:
        새글 = 글에_상태붙이기(메시지.get("text", ""), 후보, 지금결정)
        edit_text(토큰, chat_id, message_id, 새글, keyboard(후보, 오늘, 지금결정))
    answer_callback(토큰, 질문id, 누른뒤말(c, 있는것.get(c.symbol, "")))
    print(f"    → {len(적은것)}종목에 적음: {결정}")


def _처리(글: str, sheet_id: str, service) -> str:
    c = parse_command(글)

    if c.종류 == "도움":
        return 도움말()

    if c.종류 == "모름":
        return f"❓ {c.말}"

    if c.종류 == "켜기":
        return (
            "🔒 **매매를 켜는 것은 텔레그램에서 안 됩니다.**\n\n"
            "구글 시트 `설정` 탭의 `trading_enabled`을 true로 바꾸거나 대시보드에서 켜세요.\n"
            "화면을 보면서 켜야 하는 일이라 일부러 막아 뒀습니다.\n\n"
            "끄는 것은 /끄기 로 언제든 됩니다."
        )

    if c.종류 == "끄기":
        옛것 = update_setting(sheet_id, "trading_enabled", "false")
        return (
            f"🛑 **매매를 껐습니다.** (시트 값 {옛것 or '(빈칸)'} → false)\n\n"
            "이미 들고 있는 종목의 손절은 그대로 작동합니다 — '더 안 산다'이지 "
            "'방치한다'가 아닙니다."
        )

    if c.종류 == "상태":
        내용 = read(sheet_id)
        시트 = parse_settings(내용.설정)
        정책, 출처 = apply(service.get_risk_policy(), 시트)
        섹터수 = sum(1 for s in 내용.섹터 if s.활성)
        종목수 = sum(len(s.활성종목) for s in 내용.섹터 if s.활성)
        return (
            describe(정책, 출처, 시트)
            + f"\n\n  유니버스: 섹터 {섹터수}개 · 종목 {종목수}개"
            + "\n\n바꾸려면: /설정 <이름> <값>"
        )

    if c.종류 == "설정":
        b = 기준표[c.이름]
        옛글자 = update_setting(sheet_id, c.이름, c.글자)
        옛것 = _옛값(b, 옛글자)
        return 바꾼말(c.이름, 옛것, c.값)

    if c.종류 == "승인":
        오늘 = datetime.now(KST).date()
        찾음, 못찾음 = approve_in_sheet(sheet_id, 오늘, c.종목들)
        줄 = []
        if 찾음:
            줄.append(f"✅ 승인 {len(찾음)}종목: {', '.join(찾음)}")
        if 못찾음:
            줄.append(
                f"❓ 오늘 후보에 없어서 못 한 것: {', '.join(못찾음)}\n"
                "   승인은 제안된 것 중에 고르는 일이라, 목록에 없는 종목은 사지 않습니다."
            )
        return "\n\n".join(줄) or "오늘 후보가 없습니다."

    return f"❓ 아직 처리하지 못하는 명령입니다: {c.종류}"


def _옛값(b, 글자: str):
    """바뀌기 전 값. 시트가 비어 있었으면 기본값을 보여 준다."""
    from muwon.settings.from_sheet import SettingsError, 기본값, 해석값

    if not 글자:
        return 기본값(b)
    try:
        return 해석값(b, 글자)
    except SettingsError:
        return 글자


if __name__ == "__main__":
    raise SystemExit(main())
