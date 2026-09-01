"""확정된 전략 변경 예약을 실제로 반영한다. **여기서만 전략이 바뀐다.**

    17:50  검토 → 텔레그램 버튼           run_strategy_review.py
             ↓ 사람이 두 번 누른다
    08:20  여기 — 확정된 것만 반영
             ↓
    08:30  새 전략으로 매수 후보를 뽑는다   propose_buys.py

## 왜 매수 후보 산출 전인가

전략이 바뀌면 그날 후보가 달라진다. 후보를 뽑은 뒤에 바꾸면 화면에 뜬 후보와
실제로 쓰는 전략이 하루 어긋난다.

## 바꾸면 보유 종목은 어떻게 되나

**바로 새 규칙으로 팔린다.** 엔진의 청산 판단은 보유 종목이 어떤 전략으로
들어왔는지가 아니라 지금 걸린 전략을 본다. 그래서 바꾸는 순간 들고 있는
것에도 적용된다. 이 사실을 반영 알림에 같이 적는다.

## 아무것도 안 하는 날이 대부분이다

예약이 없으면 조용히 0으로 끝난다. 알림도 안 보낸다. 매일 "오늘도 안
바꿨습니다"를 보내면 알림이 흔해지고, 흔해진 알림은 진짜일 때도 안 읽힌다.

## 막히면 남긴다

반영하려다 조건에 걸리면 상태를 `막힘`으로 적고 까닭을 남긴 뒤 알린다.
**조용히 안 바뀌면 원인을 못 찾는다.** 이 저장소에서 이미 세 번 겪었다.

사용 예:
    python scripts/apply_strategy_change.py --dry-run
    python scripts/apply_strategy_change.py
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.strategy_fit import 기본최소운용일
from muwon.cloud import strategy_approval as 승인
from muwon.config import bootstrap_settings
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")


#: 화면의 '전략 변경 이력' 표가 읽는 탭. 칸 순서는 창구
#: (`n8n/대시보드자료_화면모양으로.js`의 `전략변경`)와 같아야 한다.
#: 하나만 밀려도 표의 모든 줄이 한 칸씩 어긋난다.
탭이름 = "전략변경"
머리 = ["열쇠", "때", "이전전략", "새전략", "근거구간", "등급", "사유"]


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001 — 이름을 못 찾는다고 반영이 죽으면 안 된다
        return 키


def 알리기(글: str) -> None:
    """알림이 실패해도 반영은 이미 끝난 일이다. 여기서 워크플로를 빨갛게
    만들면 진짜 반영 실패와 구별이 안 된다."""
    try:
        from muwon.notify.telegram_api import send

        cfg = build_settings_service().get_telegram_config()
        if not cfg.bot_token or not cfg.chat_id:
            print("텔레그램 설정이 없어 알림은 건너뜁니다.", file=sys.stderr)
            return
        send(cfg.bot_token, cfg.chat_id, 글)
        print("텔레그램으로 알렸습니다.", file=sys.stderr)
    except Exception as 탈:  # noqa: BLE001
        print(f"알림 실패: {type(탈).__name__}: {탈}", file=sys.stderr)


def 반영알림글(줄, 이전이름: str, 새이름: str) -> str:
    말 = [
        "🔁 전략을 변경했습니다",
        "",
        f"이전  {이전이름}",
        f"현재  {새이름}",
        "",
        f"근거 구간  {줄.근거구간 or '기록 없음'}",
        f"등급      {줄.등급 or '기록 없음'}",
    ]
    if 줄.사유:
        말 += ["", "선택 근거", f"  {줄.사유}"]
    말 += [
        "",
        ("보유 중인 종목도 오늘부터 새 전략의 매도 규칙을 적용합니다. "
         "매수는 오늘 후보 산출부터 새 전략으로 계산합니다."),
        "",
        ("되돌리려면 전략 변경 워크플로를 실행하십시오. "
         "이 알림에는 되돌리는 버튼을 붙이지 않습니다."),
    ]
    return "\n".join(말)


def 막힘알림글(까닭: str, 새전략: str) -> str:
    return "\n".join([
        "⚠️ 전략 변경을 반영하지 못했습니다",
        "",
        f"예약  {전략이름(새전략)}",
        f"사유  {까닭}",
        "",
        (
            "전략은 그대로입니다. 예약은 종료했으므로 다시 검토가 필요하면 "
            "오늘 검토 결과에서 새로 선택해 주십시오."
        ),
    ])


def 시트에남기기(sheet_id: str, 이제, 줄, 이전이름: str, 새이름: str) -> None:
    """반영한 것을 시트에 한 줄 남긴다.

    **상태 DB에만 두면 화면이 못 읽는다.** 화면은 창구를 거쳐 시트를 보고,
    상태 DB는 구글드라이브의 파일이라 창구가 열 수 없다.

    시트가 막혀도 반영은 이미 끝난 일이라 실패로 치지 않는다. 대신 왜 못
    남겼는지를 로그에 적는다."""
    if not sheet_id:
        print("시트를 못 찾아 변경 이력을 안 남깁니다.", file=sys.stderr)
        return
    try:
        from muwon.cloud.sheet_log import append

        append(sheet_id, 탭이름, 머리, [[
            f"C{이제:%Y-%m-%d %H:%M}",
            f"{이제:%Y-%m-%d %H:%M}",
            이전이름,
            새이름,
            줄.근거구간 or "",
            줄.등급 or "",
            줄.사유 or "",
        ]])
        print(f"시트 '{탭이름}'에 남겼습니다.", file=sys.stderr)
    except Exception as 탈:  # noqa: BLE001 — 반영은 이미 끝났다
        print(f"시트 기록 실패: {type(탈).__name__}: {탈}", file=sys.stderr)


def 시트찾기(인자) -> str:
    """매수 후보를 뽑는 곳과 같은 길로 찾는다. 여기만 다른 길로 찾으면
    어느 날 서로 다른 시트를 보게 된다."""
    if 인자.sheet_id:
        return 인자.sheet_id
    if not 인자.folder_id:
        return ""
    from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

    sheet_id, _ = find_or_create(인자.folder_id, DEFAULT_TITLE)
    return sheet_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="무엇을 반영할지 보기만 한다. DB를 안 고친다")
    parser.add_argument("--최소운용일", type=int, default=기본최소운용일,
                        help="직전 변경 뒤 이만큼 지나기 전에는 반영하지 않는다")
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    인자 = parser.parse_args()

    이제 = datetime.now(서울).replace(tzinfo=None)
    print(f"■ 전략 변경 반영 {이제:%Y-%m-%d %H:%M} (KST)")
    print(f"■ 되는 쪽: {'보기만 (dry-run)' if 인자.dry_run else '실제로 바꿉니다'}")

    service = build_settings_service()
    고름 = service.get_strategy_selection()
    지금키 = (고름.active_keys or ("",))[0]
    아는것 = [ㅈ.key for ㅈ in list_definitions()]
    print(f"■ 지금 걸린 전략  {전략이름(지금키)} ({지금키})")

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)

    with session_factory() as session:
        줄, 까닭 = 승인.반영할것(
            session, 이제.date(), 지금키, 아는것, 최소운용일=인자.최소운용일
        )

        if 줄 is None and not 까닭:
            print("■ 반영할 예약이 없습니다. 아무것도 바꾸지 않습니다.")
            return 0

        if 줄 is None:
            print(f"■ 반영하지 못합니다: {까닭}")
            앞 = 승인.지금예약(session)
            새전략 = 앞.새전략 if 앞 else ""
            if 인자.dry_run:
                print("dry-run이라 상태를 안 고칩니다.")
                return 0
            승인.막힘표시(session, 까닭)
            session.commit()
            알리기(막힘알림글(까닭, 새전략))
            return 0

        이전이름 = 전략이름(줄.이전전략 or 지금키)
        새이름 = 전략이름(줄.새전략)
        print(f"■ 반영할 것  {이전이름} → {새이름}")
        print(f"   근거 구간 {줄.근거구간} · 등급 {줄.등급}")
        if 줄.사유:
            print(f"   {줄.사유}")

        if 인자.dry_run:
            print("dry-run이라 전략을 안 바꿉니다.")
            return 0

        # 실제로 바꾼다. 매도 전략은 건드리지 않는다 — 여기서 같이 바꾸면
        # 무엇 때문에 성적이 달라졌는지 나중에 가를 수 없다.
        from dataclasses import replace as _replace

        service.set_strategy_selection(
            _replace(고름, active_keys=(줄.새전략,))
        )
        # 이전 전략은 예약할 때 적어 둔 값이 아니라 **방금 실제로 걸려 있던
        # 것**으로 덮는다. 예약과 반영 사이에 손으로 바꿨을 수 있다.
        줄.이전전략 = 지금키
        승인.반영표시(session, 줄)
        session.commit()
        print("■ 전략을 바꿨습니다.")

        시트에남기기(시트찾기(인자), 이제, 줄, 이전이름, 새이름)
        알리기(반영알림글(줄, 이전이름, 새이름))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — 무엇이 터지든 로그에 남아야 한다
        traceback.print_exc()
        raise SystemExit(1) from None
