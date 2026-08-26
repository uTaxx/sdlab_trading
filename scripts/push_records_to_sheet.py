"""매매 기록과 하루 요약을 구글 시트에 덧붙인다.

`docs/설계_스트림릿을_걷어낼까.md`의 **3단계**. 대시보드를 켜지 않고도
폰에서 "어제 뭘 샀고 어떻게 됐나"를 보게 하는 것이 목적이다.

## 덧붙이기만 한다

지난 줄을 고치지 않는다. 줄마다 열쇠가 있어서 **여러 번 돌려도 줄이
늘지 않는다** — 워크플로 재실행은 실패를 고치는 정상적인 수단이고,
그때마다 줄이 늘면 시트를 세어 만든 숫자가 전부 틀린다.

## 아무것도 사지 않는다

읽어서 올리기만 한다.

사용 예:
    python scripts/push_records_to_sheet.py
    python scripts/push_records_to_sheet.py --days 30
    python scripts/push_records_to_sheet.py --dry-run   # 올릴 것만 보여 준다
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, update_setting
from muwon.cloud.sheet_log import (
    append,
    daily_rows,
    history_rows,
    notice_rows,
    order_rows,
    runlog_rows,
    trade_rows,
    매매머리,
    알림머리,
    요약머리,
    이력머리,
    주문머리,
    회차머리,
)
from muwon.config import bootstrap_settings
from muwon.db.models import AppSettingHistoryRow, OrderRow, RunLogRow, TradeRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.service import build_settings_service


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="최근 며칠치를 올릴 것인가")
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true", help="구글에 붙지 않고 올릴 것만 보여 준다")
    args = parser.parse_args()

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)
    오늘 = datetime.now(ZoneInfo("Asia/Seoul")).date()
    자른날 = 오늘 - timedelta(days=args.days)

    with session_factory() as session:
        매매들 = list(
            session.scalars(
                select(TradeRow).where(TradeRow.exited_at >= 자른날).order_by(TradeRow.id)
            )
        )
        주문들 = list(
            session.scalars(
                select(OrderRow).where(OrderRow.created_at >= 자른날).order_by(OrderRow.id)
            )
        )
        회차들 = list(
            session.scalars(
                select(RunLogRow)
                .where(RunLogRow.created_at >= 자른날)
                .order_by(RunLogRow.id)
            )
        )
        변경들 = list(
            session.scalars(
                select(AppSettingHistoryRow)
                .where(AppSettingHistoryRow.changed_at >= 자른날)
                .order_by(AppSettingHistoryRow.id)
            )
        )

    매매줄 = trade_rows(매매들)

    # 하루 요약은 주문에서 센다. 매매(TradeRow)는 청산돼야 생기므로,
    # 산 날에는 아무것도 안 남아 "그날 아무 일도 없었다"로 보인다.
    날짜별: dict[date, list[OrderRow]] = {}
    for o in 주문들:
        날짜별.setdefault(o.created_at.date(), []).append(o)
    요약줄 = []
    for 날, 것들 in sorted(날짜별.items()):
        요약줄 += daily_rows(
            날,
            매수=sum(1 for o in 것들 if o.side == "buy"),
            매도=sum(1 for o in 것들 if o.side == "sell"),
            거부=0,  # 거부는 주문으로 안 남는다 — 로그에만 있다
            메모="주문 기록에서 셈",
        )

    주문줄 = order_rows(주문들)
    회차줄 = runlog_rows(회차들)
    이력줄 = history_rows(변경들)
    알림줄 = notice_rows(주문들, 매매들, 회차들)

    # 지금 걸린 전략을 시트에 옮겨 적는다. 화면이 DB를 못 보므로, 이게
    # 없으면 대시보드가 "무엇이 걸려 있는지 알 수 없습니다"라고만 한다.
    # **바꾸는 것이 아니라 옮겨 적는 것이다** — 시트의 이 칸은 정책필드가
    # 비어 있어서 무엇으로 바뀌어도 주문에 영향이 없다.
    try:
        전략키 = build_settings_service().get_strategy_selection().active_key
    except Exception as e:  # noqa: BLE001 — 기록 올리기가 이것 때문에 죽으면 안 된다
        전략키 = ""
        print(f"쓰는 전략을 못 읽었습니다: {type(e).__name__}: {e}", file=sys.stderr)

    print(
        f"■ 최근 {args.days}일 — 매매 {len(매매줄)} · 주문 {len(주문줄)} · "
        f"회차 {len(회차줄)} · 알림 {len(알림줄)} · 설정변경 {len(이력줄)} · "
        f"주문이 있던 날 {len(요약줄)}"
    )
    if not any((매매줄, 주문줄, 회차줄, 알림줄, 이력줄, 요약줄)):
        print("\n올릴 것이 없습니다. 아직 아무 회차도 안 돌았다는 뜻입니다.")

    if args.dry_run:
        for 이름, 줄들 in (
            ("매매기록", 매매줄), ("주문기록", 주문줄), ("실행기록", 회차줄),
            ("알림", 알림줄), ("변경이력", 이력줄), ("일일요약", 요약줄),
        ):
            for 줄 in 줄들[:5]:
                print(f"  [{이름}] " + " | ".join(줄))
        print(f"  [설정] strategy = {전략키 or '(못 읽음)'}")
        return 0

    sheet_id = args.sheet_id
    if not sheet_id:
        if not args.folder_id:
            raise SystemExit("MUWON_SHEET_ID도 GDRIVE_FOLDER_ID도 없습니다.")
        sheet_id, _ = find_or_create(args.folder_id, DEFAULT_TITLE)
    print(f"시트: https://docs.google.com/spreadsheets/d/{sheet_id}")

    # 탭마다 따로 올린다. 하나가 실패해도 나머지는 올라가야 한다 —
    # 전부 묶어 두면 알림 한 줄 때문에 매매 기록이 통째로 안 올라간다.
    올린것 = {}
    못올린것 = {}
    for 탭, 머리, 줄들 in (
        ("매매기록", 매매머리, 매매줄),
        ("주문기록", 주문머리, 주문줄),
        ("실행기록", 회차머리, 회차줄),
        ("알림", 알림머리, 알림줄),
        ("변경이력", 이력머리, 이력줄),
        ("일일요약", 요약머리, 요약줄),
    ):
        try:
            올린것[탭] = append(sheet_id, 탭, 머리, 줄들)
        except Exception as e:  # noqa: BLE001
            못올린것[탭] = f"{type(e).__name__}: {e}"

    if 전략키:
        try:
            update_setting(
                sheet_id, "strategy", 전략키,
                설명="지금 걸려 있는 전략. 여기서 고쳐도 매매는 안 바뀝니다",
            )
            올린것["설정!strategy"] = 1
        except Exception as e:  # noqa: BLE001
            못올린것["설정!strategy"] = f"{type(e).__name__}: {e}"

    print("\n올림 — " + " · ".join(f"{ㄱ} {ㄴ}줄" for ㄱ, ㄴ in 올린것.items()))
    if 매매줄 and not 올린것.get("매매기록"):
        print("  (이미 있는 줄이라 안 올렸습니다 — 여러 번 돌려도 줄이 늘지 않습니다)")

    # **조용히 넘어가지 않는다.** 이 저장소에서 제일 비싼 실패가 초록불
    # 아래에서 아무것도 안 하는 것이다. 하나라도 실패하면 종료 코드를 준다.
    if 못올린것:
        for 탭, 까닭 in 못올린것.items():
            print(f"❌ {탭}: {까닭}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
