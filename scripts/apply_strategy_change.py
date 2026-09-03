"""확정된 전략 변경 예약을 실제로 반영한다. **여기서만 전략이 바뀐다.**

    17:50  검토 → 텔레그램 버튼           run_strategy_review.py
             ↓ 사람이 두 번 누른다
    08:20  여기: 확정된 것만 반영
             ↓
    08:30  새 전략으로 매수 후보를 뽑는다   propose_buys.py

## 왜 매수 후보 산출 전인가

전략이 바뀌면 그날 후보가 달라진다. 후보를 뽑은 뒤에 바꾸면 화면에 뜬 후보와
실제로 쓰는 전략이 하루 어긋난다.

## 바꾸면 보유 종목은 어떻게 되나

**바로 새 규칙으로 팔린다.** 엔진의 청산 판단은 보유 종목이 어떤 전략으로
들어왔는지가 아니라 지금 설정된 전략을 본다. 그래서 바꾸는 순간 들고 있는
것에도 적용된다. 이 사실을 반영 알림에 같이 적는다.

## 아무것도 안 하는 날이 대부분이다

예약이 없으면 조용히 0으로 끝난다. 알림도 안 보낸다. 매일 "오늘도 안
바꿨습니다"를 보내면 알림이 흔해지고, 흔해진 알림은 진짜일 때도 안 읽힌다.

## 막히면 남긴다

반영하려다 조건에 걸리면 상태를 `막힘`으로 적고 까닭을 남긴 뒤 알린다.
**조용히 안 바뀌면 원인을 못 찾는다.** 이 저장소에서 이미 세 번 겪었다.

시트에도 남긴다. 전에는 바꾼 날만 시트에 남겨서, 막힌 날은 화면에 아무
흔적이 없었다. 텔레그램 알림을 놓치면 그날 무슨 일이 있었는지 확인할 길이
없었고, 화면의 자동 실행 일정은 막힌 날에도 "예약이 없는 날에는 아무것도
변경하지 않습니다"만 적혀 있었다. 그래서 반영한 날과 막힌 날을 같은 탭에
`상태` 칸으로 구별해 쌓는다.

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

# 막힌 까닭은 f"...{줄.새전략}"으로 만들어져 전략 키가 그대로 섞인다.
# 사람에게 가는 자리(알림, 시트, 화면)에서는 키가 아니라 이름이어야 한다.
from muwon.cloud.approval import 키를이름으로
from muwon.config import bootstrap_settings
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")


#: 화면의 '전략 변경 이력' 표가 읽는 탭. 칸 순서는 창구
#: (`n8n/대시보드자료_화면모양으로.js`의 `전략변경`)와 같아야 한다.
#: 하나만 밀려도 표의 모든 줄이 한 칸씩 어긋난다.
탭이름 = "전략변경"
머리 = ["열쇠", "때", "이전전략", "새전략", "근거구간", "등급", "사유", "상태"]

#: 시트 `상태` 칸에 쓰는 말. 화면이 이 글자를 그대로 견주므로 바꾸면 양쪽을
#: 같이 고쳐야 한다. `tests/test_n8n_gateway_fields.py`가 묶어 둔다.
반영함 = "반영"
막힘 = "막힘"


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001 (이름을 못 찾는다고 반영이 죽으면 안 된다)
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
        # 2026-09-02에 청산이 '산 전략'을 따르도록 바뀌었는데 이 문장만
        # 옛 동작을 그대로 적고 있었다. 보유 종목이 언제 팔리는지를 반대로
        # 알리는 문장이라 그대로 두면 안 된다.
        ("보유 중인 종목은 매수 시점의 전략이 정한 매도 규칙을 그대로 따릅니다. "
         "새 전략은 오늘 후보 산출부터 적용합니다. 다만 매수 시점의 전략을 "
         "확인할 수 없는 종목은 현재 전략의 매도 규칙을 따릅니다."),
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
        f"사유  {키를이름으로(까닭)}",
        "",
        (
            "전략은 그대로입니다. 예약은 종료했으므로 다시 검토가 필요하면 "
            "오늘 검토 결과에서 새로 선택해 주십시오."
        ),
    ])


def 시트의전략칸갱신(sheet_id: str, 새전략키: str) -> None:
    """시트 `설정` 탭의 `strategy` 칸을 바꾼 값으로 맞춘다.

    ## 왜 여기서 해야 하나

    실제로 쓰는 전략은 상태 DB에 있고(`strategy.active_keys`), 시트의
    `strategy` 칸은 그것을 옮겨 적은 사본이다. 화면은 그 사본을 읽는다.

    그런데 사본을 옮겨 적는 것이 17:40 기록 저장뿐이었다. 그래서 08:20에
    전략을 바꾸면 **08:20부터 17:40까지 화면이 옛 전략을 보여 줬다.** 그
    아홉 시간이 하루 매매 전체를 덮는다. 08:30에 매수 후보를 승인할 때
    화면 맨 위 띠가 어제 전략을 적고 있었다.

    17:40은 그대로 둔다. 같은 값을 다시 쓰는 것은 무해하고, 여기서 실패한
    경우를 그날 저녁에 한 번 더 복구해 준다.

    시트가 막혀도 반영은 이미 끝난 일이라 실패로 치지 않는다. 대신 왜 못
    고쳤는지를 로그에 적는다. 조용히 넘기면 화면이 옛 전략을 보여 주는
    것을 아무도 모른다."""
    if not sheet_id:
        print("시트를 못 찾아 전략 칸을 못 고칩니다.", file=sys.stderr)
        return
    try:
        from muwon.cloud.sector_sheet import update_setting

        옛것 = update_setting(
            sheet_id, "strategy", 새전략키,
            설명="지금 설정된 전략. 여기서 고쳐도 매매는 안 바뀝니다",
        )
        print(f"시트 설정!strategy: {옛것 or '(빈칸)'} → {새전략키}", file=sys.stderr)
    except Exception as 탈:  # noqa: BLE001 (반영은 이미 끝났다)
        print(
            f"시트 전략 칸을 못 고쳤습니다: {type(탈).__name__}: {탈}. "
            "화면은 17:40 기록 저장 전까지 이전 전략을 보여 줍니다.",
            file=sys.stderr,
        )


def 시트에남기기(
    sheet_id: str, 이제, 이전이름: str, 새이름: str, 상태: str,
    근거구간: str = "", 등급: str = "", 사유: str = "",
) -> None:
    """그날 08:20이 무엇을 했는지 시트에 한 줄 남긴다. 막힌 날도 남긴다.

    **상태 DB에만 두면 화면이 못 읽는다.** 화면은 n8n 연결을 거쳐 시트를
    보고, 상태 DB는 구글 드라이브의 파일이라 n8n 연결이 열 수 없다.

    시트가 막혀도 반영이나 막힘 처리는 이미 끝난 일이라 실패로 치지 않는다.
    대신 왜 못 남겼는지를 로그에 적는다."""
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
            근거구간,
            등급,
            사유,
            상태,
        ]])
        print(f"시트 '{탭이름}'에 {상태}으로 남겼습니다.", file=sys.stderr)
    except Exception as 탈:  # noqa: BLE001 (반영이나 막힘 처리는 이미 끝났다)
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
    print(f"■ 지금 설정된 전략  {전략이름(지금키)} ({지금키})")

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
            근거구간 = 앞.근거구간 if 앞 else ""
            등급 = 앞.등급 if 앞 else ""
            if 인자.dry_run:
                print("dry-run이라 상태를 안 고칩니다.")
                return 0
            승인.막힘표시(session, 까닭)
            session.commit()
            # 막힌 날일수록 기록이 중요하다. 전략이 안 바뀐 채로 후보가
            # 나오고, 사람은 바뀐 줄 알고 승인한다.
            시트에남기기(
                시트찾기(인자), 이제, 전략이름(지금키), 전략이름(새전략) if 새전략 else "",
                막힘, 근거구간=근거구간, 등급=등급, 사유=키를이름으로(까닭),
            )
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

        # 실제로 바꾼다. 매도 전략은 건드리지 않는다. 여기서 같이 바꾸면
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

        시트 = 시트찾기(인자)
        # **화면이 읽는 사본을 여기서 같이 맞춘다.** 안 맞추면 오늘 하루
        # 종일 화면이 옛 전략을 보여 준다. 08:30 승인 화면이 특히 그렇다.
        시트의전략칸갱신(시트, 줄.새전략)
        시트에남기기(
            시트, 이제, 이전이름, 새이름, 반영함,
            근거구간=줄.근거구간 or "", 등급=줄.등급 or "", 사유=줄.사유 or "",
        )
        알리기(반영알림글(줄, 이전이름, 새이름))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 (무엇이 터지든 로그에 남아야 한다)
        traceback.print_exc()
        raise SystemExit(1) from None
