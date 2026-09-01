"""매일 매매 대상 종목의 트렌드를 재고, 구간별로 전략 순위를 내고, 바꿀 만한
후보를 텔레그램 버튼으로 보낸다. **이 스크립트는 아무것도 바꾸지 않는다.**

전략을 실제로 바꾸는 것은 `scripts/apply_strategy_change.py`이고, 그것도
사람이 버튼을 두 번 누른 뒤에만 한다.

## 순서

    1. 매매 대상 종목의 1주·1개월·3개월 등락을 잰다        (analysis/trend.py)
    2. 같은 구간에 등록된 전략을 전부 계산한다            (period_check.돌려보기)
    3. 구간마다 변경 후보를 하나씩 낸다                  (analysis/strategy_fit.py)
    4. 시트 `전략검토` 탭에 남기고, 텔레그램에 버튼을 붙여 보낸다

## 왜 구간마다 따로 내나

한 구간의 1위를 그대로 걸면 그 구간에 맞춘 것이 된다. 셋을 따로 내면 답이
모이는지 갈리는지가 보인다. 2026-08-28 검증에서 3개월 1위가 12개월에서는
하위권이었다.

## 매일 보내되 등급을 붙인다

이상 없는 날에도 보낸다. 흐름을 매일 보기 위해서다. 대신 등급을 앞에 두어
읽을지 말지를 한 줄에서 정할 수 있게 한다. 버튼은 후보가 있을 때만 붙는다.

## 시세는 한 번만 받는다

제일 긴 구간 하나를 받아 짧은 것은 거기서 잘라 쓴다. 구간마다 따로 받으면
같은 자료를 세 번 받고, 더 나쁘게는 받는 사이에 값이 바뀌어 구간끼리 비교가
안 된다.

사용 예:
    python scripts/run_strategy_review.py --dry-run   # 계산만, 시트·알림 없음
    python scripts/run_strategy_review.py
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import shadow as 그림자
from muwon.analysis.experiment import WARMUP_DAYS
from muwon.analysis.market_data import load_histories
from muwon.analysis.period_check import (
    검증용정책,
    구간,
    기간정의,
    기간표,
    기준글,
    돌려보기,
)
from muwon.analysis.strategy_fit import (
    구간순위,
    기본우위배수,
    기본최소운용일,
    모아등급,
    변경후보,
    전략줄,
    총평,
    후보글,
    후보내기,
)
from muwon.analysis.trend import 트렌드글, 트렌드재기
from muwon.cloud import strategy_approval as 승인
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import ensure_schema, make_session_factory
from muwon.notify.telegram_buttons import 전략버튼, 전략상태블록, 전략키보드
from muwon.settings.from_sheet import build_policy_provider
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")

#: 매일 재는 구간. `period_check.기간표`의 이름을 그대로 쓴다 — 이름이 갈리면
#: 같은 구간이 화면마다 다르게 불린다.
볼구간 = ("1주", "1개월", "3개월")

탭이름 = "전략검토"
추적탭 = "전략추적"

머리 = [
    "열쇠", "잰때", "구간", "시작일", "끝일", "지금전략", "후보전략",
    "지금수익률%", "후보수익률%", "거래수", "지금자리", "전체수", "플러스수",
    "등급", "트렌드", "후보글", "막힌까닭", "추적글",
]

추적머리 = [
    "열쇠", "제안일", "잰날", "지난날수", "구간", "전략", "자리", "지금것",
    "제안것", "골랐나", "제안시수익률%", "뒤수익률%", "뒤거래수", "뒤최대낙폭%",
    "상태", "못한까닭",
]


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001 — 이름을 못 찾는다고 검토가 죽으면 안 된다
        return 키


def 대상종목(sheet_id: str):
    """매매 대상 종목. **매수 후보를 뽑는 곳과 같은 목록이어야 한다.**

    `propose_buys.py`가 구글 시트의 종목 탭을 읽는다. 여기서 시가총액 목록으로
    재면 나온 순위가 실제 매매를 설명하지 못한다."""
    if not sheet_id:
        raise ValueError("시트를 못 찾아 매매 대상 종목을 읽을 수 없습니다.")

    from muwon.cloud.sector_sheet import read as 섹터시트읽기
    from muwon.data.universe import Ticker

    내용 = 섹터시트읽기(sheet_id)
    목록 = [
        Ticker(
            symbol=m.symbol,
            name=m.name,
            market=m.market,
            yahoo_symbol=f"{m.symbol}.{'KQ' if m.market == 'KOSDAQ' else 'KS'}",
        )
        for s in 내용.섹터
        if s.활성
        for m in s.활성종목
    ]
    if not 목록:
        raise ValueError("시트에 켜져 있는 종목이 하나도 없습니다.")
    return 목록


def 구간순위내기(정의, histories, 끝, 정책, 지금키: str) -> tuple[구간순위, list[str]]:
    """한 구간에 등록된 전략을 전부 계산한다. (순위, 못 돌린 것).

    **하나가 터져도 나머지는 봐야 한다.** 전략 하나의 파라미터가 이상해서
    전체 검토가 멈추면, 그날은 아무 숫자도 안 남는다."""
    줄들: list[전략줄] = []
    못돌린것: list[str] = []
    for ㅈ in list_definitions():
        try:
            성적 = 돌려보기(정의, (lambda k=ㅈ.key: build_strategy(k)),
                        histories, 끝, 정책)
        except Exception as 탈:  # noqa: BLE001
            못돌린것.append(f"{ㅈ.key} ({type(탈).__name__})")
            continue
        if 성적 is None:
            못돌린것.append(f"{ㅈ.key} (시세 부족)")
            continue
        줄들.append(전략줄(키=ㅈ.key, 이름=ㅈ.화면이름, 성적=성적))
    return 구간순위(구간=정의.이름, 줄들=줄들, 지금키=지금키), 못돌린것


def 뒤재기만들기(histories, 정책):
    """그림자 추적이 쓸 재기함수. (전략키, 시작, 끝) → 기간성적 또는 None.

    **오늘 순위를 낸 것과 같은 시세, 같은 기초설정, 같은 체결 규칙으로 돈다.**
    다른 것은 전략 하나뿐이어야 차이를 전략 탓으로 읽을 수 있다."""

    def 재기(전략키: str, 시작, 끝):
        일수 = (끝 - 시작).days
        if 일수 <= 0:
            return None
        정의 = 기간정의(
            이름=f"{일수}일 뒤",
            달수=0,
            쪼갬="주",
            설명="그림자 추적용 구간입니다. 화면에서 고를 수 있는 구간이 아닙니다",
            날수=일수,
        )
        return 돌려보기(정의, (lambda k=전략키: build_strategy(k)), histories, 끝, 정책)

    return 재기


def 추적시트줄(줄) -> list[str]:
    def 숫자(값):
        return "" if 값 is None else f"{값:.2f}"

    def 셈(값):
        # None을 str()로 찍으면 시트의 숫자 칸에 "None"이 남는다. 그러면
        # 나중에 그 칸을 세는 계산이 통째로 문자열 오류로 죽는다.
        return "" if 값 is None else str(값)

    return [
        f"S{줄.제안일}|{줄.구간}|{줄.전략}",
        f"{줄.제안일}",
        f"{줄.잰날}" if 줄.잰날 else "",
        셈(줄.지난날수),
        줄.구간,
        전략이름(줄.전략),
        셈(줄.자리),
        "예" if 줄.지금것 else "",
        "예" if 줄.제안것 else "",
        "예" if 줄.골랐나 else "",
        숫자(줄.제안시수익률),
        숫자(줄.뒤수익률),
        셈(줄.뒤거래수),
        숫자(줄.뒤최대낙폭),
        줄.상태,
        줄.못한까닭,
    ]


def 막는까닭(session, 오늘, 최소운용일: int) -> str:
    """이 구간 밖에서 정해지는 이유. 있으면 후보를 아예 안 낸다."""
    지난 = 승인.지난거래일수(승인.마지막반영(session), 오늘)
    if 지난 is not None and 지난 < 최소운용일:
        return (
            f"직전 전략 변경으로부터 {지난}일이 지났습니다. "
            f"최소 운용기간 {최소운용일}일이 지난 뒤에 다시 검토합니다."
        )
    앞 = 승인.지금예약(session)
    if 앞 is not None:
        상태말 = "확정되어 반영을 기다리는" if 앞.상태 == 승인.확정 else "선택된"
        return (
            f"{전략이름(앞.새전략)}이 이미 {상태말} 상태입니다. "
            "먼저 반영하거나 취소한 뒤에 다시 검토합니다."
        )
    return ""


def 알림글만들기(
    잰때: datetime,
    등급: str,
    지금키: str,
    트렌드들: dict,
    후보들: list[변경후보],
    기준: str,
    추적글: str = "",
) -> str:
    머리말 = {
        "이상없음": "전략 검토 | 이상 없음",
        "살펴볼것": "전략 검토 | 살펴볼 것",
        "확인필요": "전략 검토 | 확인 필요",
    }.get(등급, f"전략 검토 | {등급}")

    줄 = [
        f"📊 {머리말}",
        f"{잰때:%m월 %d일} 기준",
        "",
        "현재 전략",
        f"  {전략이름(지금키)}" if 지금키 else "  확인 필요",
        "",
        "매매 대상 종목 트렌드",
    ]
    줄 += [f"  {트렌드글(트렌드들[이름])}" for 이름 in 볼구간 if 이름 in 트렌드들]

    줄 += ["", "구간별 전략 순위"]
    줄 += [f"  {후보글(ㅎ)}" for ㅎ in 후보들]

    줄 += ["", "정리", f"  {총평(후보들, 등급)}"]
    if 추적글:
        줄 += ["", "지난 후보를 다시 계산한 결과", f"  {추적글}"]
    줄 += ["", "계산 조건", f"  {기준}"]

    있는것 = [ㅎ for ㅎ in 후보들 if ㅎ.있나]
    if 있는것:
        줄 += [
            "",
            ("아래 버튼을 누르면 선택만 되고, 확인을 한 번 더 눌러야 "
             "확정됩니다. 확정해도 다음 거래일 매수 후보 산출 전에 반영하므로 "
             "그 전까지 취소할 수 있습니다."),
        ]
    else:
        줄 += ["", "변경 후보가 없어 버튼을 표시하지 않습니다."]
    줄 += ["", 전략상태블록()]
    return "\n".join(줄)


def 시트줄(잰때, 정의, 후보: 변경후보, 트렌드, 기준: str, 추적글: str = "") -> list[str]:
    def 숫자(값):
        return "" if 값 is None else f"{값:.2f}"

    return [
        f"R{잰때:%Y-%m-%d %H:%M}|{후보.구간}",
        f"{잰때:%Y-%m-%d %H:%M}",
        후보.구간,
        f"{정의[0]}",
        f"{정의[1]}",
        전략이름(후보.지금키),
        전략이름(후보.키) if 후보.있나 else "",
        숫자(후보.지금수익률),
        숫자(후보.후보수익률),
        str(후보.거래수),
        str(후보.지금자리 or ""),
        str(후보.전체수),
        str(후보.플러스수),
        후보.등급,
        트렌드글(트렌드) if 트렌드 else "",
        후보글(후보),
        후보.막힌까닭,
        추적글,
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true",
                        help="계산만 한다. 시트에도 안 올리고 알림도 안 보낸다")
    parser.add_argument("--최소운용일", type=int, default=기본최소운용일,
                        help="직전 변경 뒤 이만큼 지나기 전에는 후보를 안 낸다")
    parser.add_argument("--우위배수", type=float, default=기본우위배수,
                        help="1위가 지금 전략보다 이 배수만큼 앞서야 후보로 낸다")
    parser.add_argument("--추적일수", type=int, default=그림자.추적일수,
                        help="제안일부터 이만큼 지난 뒤에 뒤 수익률을 계산한다")
    parser.add_argument("--기록자리", type=int, default=그림자.기록자리,
                        help="구간마다 순위 위쪽 몇 개를 남길 것인가")
    인자 = parser.parse_args()

    잰때 = datetime.now(서울).replace(tzinfo=None)
    print(f"■ 전략 검토 {잰때:%Y-%m-%d %H:%M} (KST)")
    print(f"■ 되는 쪽: {'계산만 (dry-run)' if 인자.dry_run else '시트 기록 + 텔레그램'}")
    print()

    service = build_settings_service()
    고름 = service.get_strategy_selection()
    지금키 = (고름.active_keys or ("",))[0]

    from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

    # 시트를 찾는 길은 매수 후보를 뽑는 곳과 같아야 한다. 여기만 다른 길로
    # 찾으면 어느 날 서로 다른 시트를 보게 되고, 그때 순위와 실제 매매가
    # 다른 기준 위에 선다.
    sheet_id = 인자.sheet_id
    if not sheet_id and 인자.folder_id:
        sheet_id, _ = find_or_create(인자.folder_id, DEFAULT_TITLE)
    if sheet_id:
        정책제공, 설명, _ = build_policy_provider(service, sheet_id)
        정책 = 정책제공()
        print(설명, file=sys.stderr)
    else:
        정책 = service.get_risk_policy()
        print("시트를 못 찾아 DB 기준으로 돕니다.", file=sys.stderr)
    정책 = 검증용정책(정책)

    유니버스 = 대상종목(sheet_id)
    기준 = 기준글(정책, len(유니버스), "섹터시트")
    print(f"■ 대상 {len(유니버스)}종목 · 현재 전략 {전략이름(지금키)}")

    끝 = 잰때.date()
    정의들 = [기간표[이름] for 이름 in 볼구간]
    가장긴것 = max(정의들, key=lambda ㄱ: (ㄱ.달수, ㄱ.날수))
    처음, _ = 구간(가장긴것, 끝)
    histories = load_histories(
        YahooFinanceDataSource(),
        유니버스,
        처음 - timedelta(days=WARMUP_DAYS),
        끝,
        cache=PriceCache(),
    )
    print(f"■ 시세 {len(histories)}종목 · {처음} 앞 예열 포함\n")

    이름표 = {t.symbol: t.name for t in 유니버스}
    구간경계 = {정의.이름: 구간(정의, 끝) for 정의 in 정의들}
    트렌드들 = 트렌드재기(histories, 구간경계, 이름표)
    print("■ 매매 대상 종목 트렌드")
    for 이름 in 볼구간:
        print(f"  {트렌드글(트렌드들[이름])}")
    print()

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)
    with session_factory() as session:
        막힘 = 막는까닭(session, 끝, 인자.최소운용일)
    if 막힘:
        print(f"■ 후보를 안 냅니다: {막힘}\n")

    후보들: list[변경후보] = []
    순위들: dict = {}
    for 정의 in 정의들:
        순위, 못돌린것 = 구간순위내기(정의, histories, 끝, 정책, 지금키)
        후보 = 후보내기(순위, 우위배수=인자.우위배수, 막혔나=막힘)
        후보들.append(후보)
        순위들[정의.이름] = 순위
        print(f"■ {정의.이름} · 계산된 전략 {len(순위.차례)}개")
        for i, ㄱ in enumerate(순위.차례[:5], 1):
            표 = "◀ 지금" if ㄱ.키 == 지금키 else ""
            print(f"   {i}. {ㄱ.이름:28} {ㄱ.수익률:>+8.2f}%  {ㄱ.거래수:>4}건 {표}")
        if 못돌린것:
            print(f"   계산하지 못한 전략: {', '.join(못돌린것)}")
        print(f"   {후보글(후보)}\n")

    등급 = 모아등급(후보들)
    print(f"■ 등급: {등급}")
    print(f"■ {총평(후보들, 등급)}\n")

    # 그림자 추적. **고른 것뿐 아니라 안 고른 것도 남기고 나중에 다시 잰다.**
    # 오늘 순위를 통째로 남겨 두고, 지평이 지난 옛 줄의 뒤 수익률을 여기서
    # 채운다. 시세는 위에서 이미 받은 것을 그대로 쓴다.
    제안키들 = {ㅎ.구간: (ㅎ.키 if ㅎ.있나 else "") for ㅎ in 후보들}
    추적글 = ""
    올릴추적: list = []
    with session_factory() as session:
        try:
            남긴수 = 그림자.기록하기(
                session, 끝, 순위들, 지금키, 등급, 제안키들,
                자리수=인자.기록자리,
            )
            잴것 = 그림자.잴것(session, 끝, 인자.추적일수)
            센것, 못센것 = 그림자.재기(
                session, 잴것, 뒤재기만들기(histories, 정책), 끝
            )
            비교들 = 그림자.견주기(그림자.잰줄들(session))
            요약 = 그림자.모아보기(비교들)
            추적글 = 그림자.학습글(요약, 인자.추적일수)
            올릴추적 = [추적시트줄(ㅈ) for ㅈ in 잴것]
            if 인자.dry_run:
                session.rollback()
            else:
                session.commit()
            print(f"■ 그림자 추적 · 오늘 {남긴수}줄 남김 · "
                  f"{인자.추적일수}일 지난 것 {센것}줄 계산"
                  f"(계산 못 한 것 {못센것}줄)")
            print(f"■ {추적글}\n")
        except Exception as 탈:  # noqa: BLE001 — 추적이 터져도 오늘 검토는 나가야 한다
            session.rollback()
            print(f"그림자 추적 실패: {type(탈).__name__}: {탈}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    if 인자.dry_run:
        print("dry-run이라 시트에도 안 올리고 알림도 안 보냅니다.")
        return 0

    if sheet_id:
        try:
            from muwon.cloud.sheet_log import append

            줄들 = [
                시트줄(잰때, 구간경계[ㅎ.구간], ㅎ, 트렌드들.get(ㅎ.구간), 기준, 추적글)
                for ㅎ in 후보들
            ]
            올린수 = append(sheet_id, 탭이름, 머리, 줄들)
            print(f"시트 '{탭이름}'에 {올린수}줄 올렸습니다.", file=sys.stderr)
            if 올릴추적:
                올린수 = append(sheet_id, 추적탭, 추적머리, 올릴추적)
                print(f"시트 '{추적탭}'에 {올린수}줄 올렸습니다.", file=sys.stderr)
        except Exception as 탈:  # noqa: BLE001 — 시트가 막혀도 알림은 가야 한다
            print(f"시트 기록 실패: {type(탈).__name__}: {탈}", file=sys.stderr)

    cfg = service.get_telegram_config()
    if not cfg.bot_token or not cfg.chat_id:
        print("텔레그램 설정이 없어 알림은 건너뜁니다.", file=sys.stderr)
        return 0

    # **알림이 실패하면 이 워크플로는 빨개진다.** period_check는 반대로 해
    # 두었다(알림 실패를 검증 실패로 안 친다). 거기는 시트와 화면에 결과가
    # 이미 남아 있어서 알림이 하나 빠져도 볼 방법이 있다.
    #
    # 여기는 다르다. **승인 버튼이 텔레그램에만 있다.** 알림이 안 가면 오늘
    # 검토는 아무도 못 보고 아무도 못 누른다. 그건 조용히 넘어가면 안 된다.
    from muwon.notify.telegram_api import send

    버튼들 = [
        전략버튼(키=ㅎ.키, 이름=ㅎ.이름, 구간=ㅎ.구간, 수익률=ㅎ.후보수익률)
        for ㅎ in 후보들
        if ㅎ.있나
    ]
    글 = 알림글만들기(잰때, 등급, 지금키, 트렌드들, 후보들, 기준, 추적글)
    send(cfg.bot_token, cfg.chat_id, 글,
         reply_markup=전략키보드(버튼들, 끝))
    print(f"텔레그램으로 알렸습니다(버튼 {len(버튼들)}개).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 — 무엇이 터지든 로그에 남아야 한다
        traceback.print_exc()
        raise SystemExit(1) from None
