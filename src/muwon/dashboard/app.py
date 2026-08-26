"""설정·리스크정책·변경이력·개발로그를 한 화면에서 보고 고치는 통합 대시보드.

scripts/configure.py와 마찬가지로 SettingsService 하나만 거쳐서 설정값을
읽고 쓴다 — 저장 위치·형식이 CLI와 완전히 동일하다. 변경 이력/개발 로그는
st.fragment(run_every=...)로 자동 갱신되어, 다른 폼(예: KIS 인증정보 입력
중)을 건드리지 않고 그 구역만 주기적으로 새로고침된다.

로컬 실행:
    streamlit run src/muwon/dashboard/app.py

폰/PC 어디서든 접속 가능한 상시 대시보드로 쓰려면 Streamlit Community
Cloud에 배포한다 — docs/deploy_streamlit_cloud.md 참고. 그 환경은 컨테이너가
재배포될 때마다 로컬 디스크가 사라지므로, 이 파일이 뜰 때 구글드라이브에서
muwon.db를 내려받고(아래 sync_db_from_drive), 설정을 바꿀 때마다 다시
올린다(sync_db_to_drive) — GitHub Actions(scripts/gdrive_sync.py)와 같은
구글드라이브 폴더를 공유해서, 대시보드에서 바꾼 설정이 다음 자동매매 실행에
반영되고 자동매매가 만든 매매 기록이 대시보드에도 보이게 한다.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
REPO_ROOT = Path(__file__).resolve().parents[3]

import pandas as pd
import streamlit as st

st.set_page_config(page_title="muwon406 대시보드", layout="wide")

# Streamlit Community Cloud는 시크릿을 st.secrets로 주지 OS 환경변수로 주지
# 않는다 — 이 프로젝트의 설정 로딩(BootstrapSettings, gdrive_sync)은 전부
# os.environ/.env 기준이라, muwon.* 모듈을 import하기 전에(=BootstrapSettings가
# 만들어지기 전에) 여기서 미리 os.environ에 복사해 둔다. 로컬에서 .env로
# 실행할 때는 secrets.toml이 없어 아무 일도 안 하고 넘어간다.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:  # noqa: BLE001, S110 — secrets.toml 자체가 없는 로컬 실행은 정상 상황
    pass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from muwon.analysis import realtime_plan, report_card
from muwon.cloud.gdrive_sync import download as gdrive_download
from muwon.cloud.gdrive_sync import upload as gdrive_upload
from muwon.config import bootstrap_settings
from muwon.dashboard.glossary import TERMS, terms_for
from muwon.dashboard.schedule import KST, WEEKDAYS, automation_state, upcoming
from muwon.dashboard.strategy_rules import common_rules, describe, exit_rules
from muwon.data.kis_client import KISClient
from muwon.data.universe import UNIVERSE, find_by_symbol
from muwon.data.universe_builder import active_universe
from muwon.db.models import (
    BacktestRunRow,
    OrderRow,
    PositionRow,
    RunLogRow,
    TradeRow,
)
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.schema import (
    KISCredentials,
    RiskPolicy,
    StrategySelection,
    TelegramConfig,
)
from muwon.settings.service import SettingsService, build_settings_service
from muwon.strategy.combined import COMBINE_AND, COMBINE_OR
from muwon.strategy.registry import (
    CATEGORIES,
    build_strategies,
    get_definition,
    list_definitions,
)

HISTORY_REFRESH_SECONDS = 5
DEVLOG_REFRESH_SECONDS = 20
TRADING_REFRESH_SECONDS = 5
DRIVE_SYNC_REFRESH_SECONDS = 30


@contextmanager
def db_guard(무엇: str):
    """DB 조회가 터져도 화면 전체가 죽지 않게, 그리고 **진짜 원인이 보이게**.

    잡지 않으면 Streamlit이 "error message is redacted"라고만 하고 원인을
    감춘다. 화면은 통째로 빨간 상자가 되고, 남은 탭도 못 본다. 실제로 그
    상태로 한참 헤맸다 — 원인은 구글드라이브에서 받아 온 DB에 컬럼 하나가
    없던 것이었는데, 화면만 봐서는 알 길이 없었다.

    여기서 잡으면 ① 어느 부분이 실패했는지 ② 무슨 오류인지가 화면에 남고,
    ③ 나머지 화면은 계속 쓸 수 있다."""
    try:
        yield
    except SQLAlchemyError as e:
        st.error(
            f"**{무엇}**을(를) 불러오지 못했습니다. 화면의 나머지는 그대로 쓸 수 있습니다.\n\n"
            f"원인: `{type(e).__name__}: {e}`\n\n"
            "잠시 뒤 새로고침해 보시고, 계속 같은 오류가 나면 이 문구를 그대로 알려 주세요.",
            icon="🚫",
        )


@st.cache_resource
def get_service() -> SettingsService:
    return build_settings_service()


@st.cache_resource
def get_session_factory():
    return make_session_factory(bootstrap_settings.database_url)


def _mask(value: str) -> str:
    if not value:
        return "(미설정)"
    return value[:2] + "*" * max(len(value) - 2, 0)


def _drive_sync_configured() -> bool:
    return bool(os.environ.get("GDRIVE_SA_KEY_JSON")) and bool(os.environ.get("GDRIVE_FOLDER_ID"))


def _local_db_path() -> str | None:
    prefix = "sqlite:///"
    url = bootstrap_settings.database_url
    if not url.startswith(prefix):
        return None  # Postgres 등 파일 기반이 아닌 DB는 동기화 대상이 아니다
    return url[len(prefix) :]


def sync_db_from_drive() -> None:
    if not _drive_sync_configured():
        return
    path = _local_db_path()
    if path is None:
        return
    gdrive_download(os.environ["GDRIVE_FOLDER_ID"], Path(path).name, path)
    # 받아 온 파일이 옛 스키마일 수 있다. 갈아 끼운 **직후에** 맞춘다 —
    # 세션 팩토리는 캐시돼 있어서 여기서 안 하면 맞출 기회가 없다.
    ensure_schema(bootstrap_settings.database_url)


def sync_db_to_drive() -> None:
    """설정을 바꾼 직후 호출한다 — 안 그러면 이 서버가 재배포되거나 다음
    GitHub Actions 실행이 구글드라이브에서 옛 상태를 받아가서, 방금 화면에서
    바꾼 값이 없던 일이 된다."""
    if not _drive_sync_configured():
        return
    path = _local_db_path()
    if path is None or not Path(path).exists():
        return
    gdrive_upload(os.environ["GDRIVE_FOLDER_ID"], Path(path).name, path)


@st.cache_resource
def _initial_drive_sync() -> bool:
    """프로세스가 뜰 때 딱 한 번만 — st.cache_resource라 위젯 조작으로
    화면이 다시 그려질 때마다(rerun) 다시 받지 않고, 이 서버 프로세스가
    살아있는 동안 최초 1회만 실행된다. 그 뒤로는 아래 주기적 갱신
    (render_drive_sync_fragment)이 최신 상태를 이어받는다."""
    sync_db_from_drive()
    return True


#: 방금 내가 저장한 값을 주기 동기화가 덮어쓰지 않도록 잠깐 쉬는 시간(초).
#: 저장 → 업로드 사이에 내려받기가 끼어들면 방금 바꾼 값이 없던 일이 된다.
SAVE_QUIET_SECONDS = 15


def save_and_sync(action, 성공문구: str) -> bool:
    """설정을 저장하고 구글드라이브에 올린다. 실패하면 이유를 화면에 띄운다.

    잡지 않고 두면 Streamlit이 "error message is redacted"라고만 하고 진짜
    원인을 감춘다 — 실제로 자동매매 스위치가 그렇게 죽었고, 무엇이 문제인지
    알아내는 데 화면만으로는 방법이 없었다."""
    st.session_state["_last_save_at"] = time.time()
    try:
        action()
    except SQLAlchemyError as e:
        st.error(
            "설정을 저장하지 못했습니다. 화면의 값은 바뀌지 않았습니다.\n\n"
            f"원인: `{type(e).__name__}: {e}`\n\n"
            "잠시 뒤 다시 눌러 보시고, 계속 같은 오류가 나면 이 문구를 그대로 알려 주세요.",
            icon="🚫",
        )
        return False
    try:
        sync_db_to_drive()
    except Exception as e:  # noqa: BLE001 — 구글 API가 던지는 예외가 여러 갈래다
        st.warning(
            "저장은 됐지만 구글드라이브에 올리지 못했습니다. 이 화면이 다시 뜨면 "
            f"바뀐 값이 사라질 수 있습니다.\n\n원인: `{type(e).__name__}: {e}`",
            icon="☁️",
        )
        return True
    # st.rerun() 뒤에는 이 자리의 st.success가 지워져서 확인 문구가 한순간
    # 스쳤다가 사라진다. 다음 그림에서 띄우도록 넘겨 둔다 — 저장이 됐는지
    # 안 됐는지 모르는 채로 남는 게 제일 나쁘다.
    st.session_state["_save_notice"] = 성공문구
    return True


def render_save_notice() -> None:
    """직전 저장 결과를 한 번만 띄운다."""
    문구 = st.session_state.pop("_save_notice", None)
    if 문구:
        st.success(문구, icon="✅")


@st.fragment(run_every=DRIVE_SYNC_REFRESH_SECONDS)
def render_drive_sync_fragment() -> None:
    # 방금 저장했으면 잠깐 쉰다 — 내가 올린 것보다 먼저 남의 것을 받아 오면
    # 방금 바꾼 값이 되돌아간다.
    since_save = time.time() - st.session_state.get("_last_save_at", 0.0)
    if since_save < SAVE_QUIET_SECONDS:
        st.caption(f"☁️ 방금 저장한 값을 지키는 중 ({SAVE_QUIET_SECONDS - int(since_save)}초)")
        return
    sync_db_from_drive()
    st.caption(
        f"☁️ 구글드라이브 동기화: {datetime.now():%H:%M:%S}"  # noqa: DTZ005 — 화면 표시용, 로컬시각이면 충분
        " (자동매매가 만든 최신 상태를 주기적으로 받아옵니다)"
    )


CARD_CSS = """
<style>
.muwon-cards { display: flex; gap: 12px; overflow-x: auto; padding: 2px 2px 10px; }
.muwon-card {
  flex: 1 0 190px; background: #fff; border-radius: 16px; padding: 14px 16px;
  box-shadow: 0 1px 3px rgba(16,24,40,.08); border: 1px solid #EEF0F4;
}
.muwon-chip {
  width: 40px; height: 40px; border-radius: 12px; display: flex;
  align-items: center; justify-content: center; font-size: 20px; margin-bottom: 10px;
}
.muwon-label { font-size: 12px; color: #667085; }
.muwon-value { font-size: 20px; font-weight: 700; color: #101828; margin: 2px 0 6px; }
.muwon-badge {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: 11px; font-weight: 600;
}
/* 폰에서는 카드 4개가 가로로 안 들어가 3~4번째가 화면 밖으로 잘렸다.
   가로 스크롤이 가능하다는 표시도 없어서 카드가 넷이라는 걸 알 수 없었다.
   좁은 화면에서는 2×2로 접는다. */
@media (max-width: 640px) {
  .muwon-cards { flex-wrap: wrap; overflow-x: visible; }
  .muwon-card { flex: 1 1 calc(50% - 12px); min-width: 0; }
  .muwon-value { font-size: 17px; }
}
@media (prefers-color-scheme: dark) {
  .muwon-card { background: #1B1E24; border-color: #2A2E36; }
  .muwon-value { color: #ECEDEE; }
  .muwon-label { color: #98A2B3; }
}
</style>
"""

#: 목업의 파스텔 칩 색. 보라=전략, 초록=연결, 파랑=데이터, 주황=시간.
CHIP_COLORS = {
    "purple": ("#F4EBFF", "#7F56D9"),
    "green": ("#E7F6EC", "#12805C"),
    "blue": ("#E8F1FF", "#175CD3"),
    "orange": ("#FFF3E6", "#B54708"),
}


def _card(icon: str, color: str, label: str, value: str, badge: str, badge_color: str) -> str:
    chip_bg, chip_fg = CHIP_COLORS[color]
    badge_bg, badge_fg = CHIP_COLORS[badge_color]
    return (
        f'<div class="muwon-card">'
        f'<div class="muwon-chip" style="background:{chip_bg};color:{chip_fg}">{icon}</div>'
        f'<div class="muwon-label">{label}</div>'
        f'<div class="muwon-value">{value}</div>'
        f'<div class="muwon-badge" style="background:{badge_bg};color:{badge_fg}">{badge}</div>'
        f"</div>"
    )


def realized_pnl(session_factory) -> tuple[float, float, int]:
    """(오늘 실현손익, 누적 실현손익, 오늘 청산 건수).

    '오늘 손익'을 평가금액으로 내려면 지금 시세가 필요한데 대시보드는 시세를
    받지 않는다. 그래서 **청산이 끝난 거래**만으로 낸다 — 추정이 아니라
    실제로 계좌에 반영된 금액이다. 화면 문구도 '실현손익'이라고 못 박는다."""
    today = datetime.now().date()  # noqa: DTZ005 — 화면 표시용
    with session_factory() as session:
        trades = session.query(TradeRow).all()
    todays = [t for t in trades if t.exited_at and t.exited_at.date() == today]
    return (
        sum(t.pnl_amount for t in todays),
        sum(t.pnl_amount for t in trades),
        len(todays),
    )


def last_activity(session_factory) -> datetime | None:
    """마지막으로 무언가 일어난 시각 — 주문·청산 중 가장 최근."""
    with session_factory() as session:
        order = session.query(OrderRow).order_by(OrderRow.created_at.desc()).first()
        trade = session.query(TradeRow).order_by(TradeRow.exited_at.desc()).first()
    stamps = [x for x in (order.created_at if order else None, trade.exited_at if trade else None) if x]
    return max(stamps) if stamps else None


def _ago(moment: datetime | None) -> str:
    if moment is None:
        return "기록 없음"
    delta = datetime.now() - moment  # noqa: DTZ005 — 화면 표시용
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "방금 전"
    if minutes < 60:
        return f"{minutes}분 전"
    if minutes < 60 * 24:
        return f"{minutes // 60}시간 전"
    return f"{minutes // (60 * 24)}일 전"


def section(icon: str, title: str, subtitle: str, badges=(), *, expanded: bool = False):
    """목업의 카드 한 줄 — 아이콘 · 굵은 제목 · 회색 부제 · 오른쪽 뱃지.

    목업은 카드마다 펼침 화살표가 달려 있다. Streamlit에서 그 동작을 그대로
    주는 건 expander뿐인데, expander는 라벨 하나만 받는다. 그래서 아이콘·부제·
    뱃지를 라벨 안에 마크다운으로 넣었다 — CSS로 카드를 흉내내면 Streamlit이
    올라갈 때마다 깨지고, 그 화면은 '고쳐야 할 줄 모르는 채로' 망가진다."""
    chips = "".join(f"  `{b}`" for b in badges)
    return st.expander(f"{icon}  **{title}** — {subtitle}{chips}", expanded=expanded)


def _terms_markdown(terms) -> str:
    """용어를 마크다운 글로 그린다.

    처음엔 표(st.dataframe)로 그렸는데 두 가지가 걸렸다. 설명이 한 줄을
    넘으면 잘려서 정작 중요한 뒷말이 안 보이고, Streamlit 표는 캔버스로
    그려져서 브라우저 Ctrl+F가 안 먹는다 — 사전인데 검색이 안 되면 곤란하다."""
    blocks = []
    for t in terms:
        영문 = f" &nbsp;`{t.영문}`" if t.영문 else ""
        blocks.append(f"**{t.이름}**{영문}  \n{t.뜻}  \n<small>→ {t.읽는법}</small>")
    return "\n\n".join(blocks)


def render_terms(keys) -> None:
    """이 화면에 나오는 말만 골라 풀어 준다.

    사전을 통째로 보여 주면 아무도 안 읽는다. 지금 보고 있는 표에 나오는
    단어만 그 자리에서 풀어 줘야 읽힌다."""
    with st.expander("❓ 이 화면에 나오는 말", expanded=False):
        st.markdown(_terms_markdown(terms_for(keys)), unsafe_allow_html=True)


def render_glossary_panel(key_prefix: str = "") -> None:
    """전체 용어 사전 — 목업 머리글의 '?' 자리.

    key_prefix는 같은 화면에 사전을 두 번 놓을 때 검색칸 키가 부딪히지
    않게 하려는 것이다. Streamlit은 같은 키의 위젯이 둘이면 화면을 죽인다."""
    with st.expander(f"❓ 용어 해설 — 모르는 말이 나오면 여기 ({len(TERMS)}개)", expanded=False):
        st.caption(
            "화면과 알림에 나오는 주식·매매 용어를 전부 모았습니다. "
            "굵은 글씨가 용어, 그 아래가 뜻, 화살표(→)가 '그래서 그 숫자를 보면 무엇을 판단하나'입니다."
        )
        query = st.text_input(
            "찾기", placeholder="예: 손절, MDD, 슬리피지", key=f"{key_prefix}glossary_query"
        ).strip()
        picked = [
            t
            for t in TERMS.values()
            if not query
            or query.lower() in (t.이름 + t.뜻 + t.읽는법 + t.영문).lower()
        ]
        if not picked:
            st.info(f"'{query}'에 해당하는 용어가 없습니다. 저에게 물어보시면 설명하고 사전에 넣겠습니다.")
            return
        if query:
            st.caption(f"{len(picked)}개 찾음")
        st.markdown(_terms_markdown(picked), unsafe_allow_html=True)


def _active_strategy_label(selection) -> str:
    """카드에 쓸 짧은 이름.

    전략을 여러 개 걸 수 있게 된 뒤로 첫 번째 것만 보여 주면 화면이
    거짓말을 한다 — 4개를 걸었는데 카드는 1개라고 말했다."""
    if len(selection.active_keys) <= 1:
        return _display_name_for(selection.active_key)
    묶음 = "모두 동의" if selection.combine == "AND" else "하나라도"
    return f"{len(selection.active_keys)}개 · {묶음}"


def render_summary_cards(service: SettingsService) -> None:
    """목업의 상단 요약 카드 4개.

    목업은 '전략 3개 활성'으로 그려져 있지만 이 엔진은 활성 전략이 하나다.
    숫자를 3으로 맞추면 화면이 거짓말을 한다 — 실제 값을 쓰고, 여러 전략을
    동시에 굴리는 건 엔진 쪽 결정이 끝난 뒤의 일이다(설계안 §11에서 지금은
    만들지 않기로 결론).
    """
    session_factory = get_session_factory()
    policy = service.get_risk_policy()
    selection = service.get_strategy_selection()

    try:
        creds = service.get_kis_credentials()
        connected = bool(creds.app_key and creds.app_secret)
        env_label = "실거래" if creds.kis_env == "real" else "모의투자"
    except RuntimeError:
        connected, env_label = False, "확인 불가"

    today_pnl, total_pnl, today_count = realized_pnl(session_factory)
    activity = last_activity(session_factory)
    with session_factory() as db:
        last_run = db.scalars(
            select(RunLogRow).order_by(RunLogRow.created_at.desc()).limit(1)
        ).first()

    뱃지, 뱃지색, _ = automation_state(policy)

    st.markdown(CARD_CSS, unsafe_allow_html=True)
    cards = [
        # 킬스위치만 보고 'LIVE'를 띄우면 화면이 거짓말을 한다 — 실행
        # 일정 자체가 꺼져 있으면 킬스위치가 켜져 있어도 아무 일도 없다.
        _card("📈", "purple", "활성 전략", _active_strategy_label(selection), 뱃지, 뱃지색),
        _card(
            "🔌", "green", "KIS 연결", env_label,
            "연결됨" if connected else "인증정보 없음",
            "green" if connected else "orange",
        ),
        _card(
            "💰", "blue", "오늘 실현손익",
            f"{today_pnl:+,.0f}원",
            f"누적 {total_pnl:+,.0f}원 · 오늘 {today_count}건",
            "blue",
        ),
        # 목업 4번째 칸은 '최근 동기화'지만, 늘 '방금 전'이라 아무것도 안 알려
        # 준다. 정작 궁금한 건 "엔진이 돌긴 돌았나"라서 그 자리를 실행 기록으로
        # 바꿨다 — 매매가 0건인 날에도 뭔가 말해 주는 유일한 칸이다.
        _card(
            "🕒", "orange", "마지막 실행",
            last_run.created_at.strftime("%m-%d %H:%M") if last_run else "—",
            (
                f"신호 {last_run.buy_signals + last_run.sell_signals} · 주문 {last_run.orders}"
                if last_run
                else _ago(activity)
            ),
            "orange",
        ),
    ]
    st.markdown(f'<div class="muwon-cards">{"".join(cards)}</div>', unsafe_allow_html=True)


def render_notifications_tab() -> None:
    """주문·청산을 시간순으로 모아 보여준다.

    목업에는 '미확인 뱃지'가 있지만 읽음 상태를 저장할 곳이 없다. 표시만
    해 두고 값을 채우면 늘 미확인으로 보이거나 늘 읽음으로 보인다 — 둘 다
    거짓이라 아예 넣지 않았다. 읽음 처리가 필요해지면 테이블을 하나 만들고
    그때 붙인다."""
    session_factory = get_session_factory()
    with session_factory() as session:
        orders = session.query(OrderRow).order_by(OrderRow.created_at.desc()).limit(60).all()
        trades = session.query(TradeRow).order_by(TradeRow.exited_at.desc()).limit(60).all()

    events = [
        {
            "시각": o.created_at,
            "종류": "매수 주문" if o.side == "buy" else "매도 주문",
            "내용": f"{_symbol_name(o.symbol)} {o.quantity}주 @ {o.price:,.0f}원",
            "사유": o.reason,
        }
        for o in orders
    ] + [
        {
            "시각": t.exited_at,
            "종류": "청산 완료",
            "내용": f"{_symbol_name(t.symbol)} {t.pnl_pct:+.2f}% ({t.pnl_amount:+,.0f}원)",
            "사유": t.exit_reason,
        }
        for t in trades
    ]
    events = [e for e in events if e["시각"]]
    if not events:
        st.info(
            "아직 알림으로 보여 줄 기록이 없습니다. 자동매매가 주문을 내거나 "
            "포지션을 청산하면 여기에 시간순으로 쌓입니다."
        )
        return

    events.sort(key=lambda e: e["시각"], reverse=True)
    st.caption("주문과 청산을 시간순으로 모았습니다. 읽음 처리는 아직 없습니다.")
    st.dataframe(
        pd.DataFrame(
            [
                {**e, "시각": e["시각"].strftime("%m-%d %H:%M:%S")}
                for e in events[:80]
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_run_log(limit: int = 15) -> None:
    """엔진이 회차마다 남긴 한 줄을 그대로 보여 준다.

    빈 대시보드는 두 가지를 동시에 뜻한다 — "살 게 없었다"와 "안 돌았다".
    이 표가 그 둘을 가른다. 신호는 났는데 주문이 0이면 막은 이유가 함께
    보인다."""
    with get_session_factory()() as session:
        rows = session.scalars(
            select(RunLogRow).order_by(RunLogRow.created_at.desc()).limit(limit)
        ).all()
    if not rows:
        st.info(
            "실행 기록이 없습니다. 기록을 남기기 시작한 2026-08-18 이전 회차이거나, "
            "아직 한 번도 돌지 않은 것입니다."
        )
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "실행": row.created_at.strftime("%m-%d %H:%M"),
                    "기준일": row.run_date.isoformat() if row.run_date else "시세없음",
                    "전략": row.strategy_key,
                    "대상/판단": f"{row.universe_size}/{row.checked_symbols}",
                    "신호(매수/매도)": f"{row.buy_signals}/{row.sell_signals}",
                    "주문": row.orders,
                    "막힌 이유": row.rejections.replace("\n", " · ") or "—",
                }
                for row in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def render_admin_tab(service: SettingsService) -> None:
    """설정·운영 관리 — 목업의 '관리' 탭.

    매매를 보는 화면과 설정을 바꾸는 화면을 갈라 놓는 게 이 탭의 목적이다.
    지금까지는 한 페이지에 섞여 있어서, 상태를 확인하러 들어와도 인증정보
    입력란이 먼저 보였다."""
    try:
        creds = service.get_kis_credentials()
        kis_badge = "연결됨" if (creds.app_key and creds.app_secret) else "인증정보 없음"
        kis_env = "실거래" if creds.kis_env == "real" else "모의투자"
    except RuntimeError:
        kis_badge, kis_env = "확인 불가", "확인 불가"
    try:
        telegram_badge = "정상" if service.get_telegram_config().bot_token else "미설정"
    except RuntimeError:
        telegram_badge = "확인 불가"

    with section("🔑", "KIS 인증정보", "API 키 및 계좌 연결 상태", [kis_badge, kis_env]):
        st.caption(
            "**KIS**는 한국투자증권입니다. 여기 넣은 키로 프로그램이 증권사에 주문을 냅니다. "
            "지금은 **모의투자**(가짜 돈) 계좌라 잃어도 실제 돈은 나가지 않습니다."
        )
        render_terms(["모의투자", "체결"])
        render_kis_tab(service)

    with section("✈️", "텔레그램 알림", "체결 · 오류 · 리포트 알림", [telegram_badge]):
        st.caption("매수·매도가 체결되거나 오류가 나면 텔레그램으로 바로 알려 줍니다.")
        render_telegram_tab(service)

    with section("📋", "매매 기준", "모의투자를 돌리기 전에 정해 둬야 하는 값들"):
        st.caption(
            "**구글 시트가 원본입니다.** 여기서 고치면 시트가 바뀌고, 다음 실행부터 "
            "그 값으로 돕니다. 텔레그램에서 `/설정 <이름> <값>`으로도 같은 일을 할 수 "
            "있습니다 — 셋이 같은 곳을 봅니다."
        )
        render_criteria_tab()

    with section("🧾", "최근 실행", "돌긴 돌았나 · 무엇을 보고 무엇을 했나"):
        st.caption(
            "**화면이 비어 있을 때 가장 먼저 볼 표입니다.** 체결이 없어도 한 줄은 남습니다."
        )
        render_terms(["신호", "유니버스", "킬스위치"])
        render_run_log()

    with section("🕘", "변경 이력", f"설정 변경 기록 · {HISTORY_REFRESH_SECONDS}초마다 자동 갱신"):
        st.caption("누가 언제 어떤 설정을 바꿨는지 남습니다. 성과가 달라지면 여기부터 보세요.")
        render_history_fragment(service)

    with section("💻", "개발 로그", f"git 커밋 · {DEVLOG_REFRESH_SECONDS}초마다 자동 갱신"):
        st.caption("프로그램 자체가 언제 어떻게 바뀌었는지의 기록입니다.")
        render_devlog_fragment()

    with section("⚙️", "실행 환경", "DB 위치 · 암호화 키 · 동기화 상태"):
        st.caption(
            "목업의 '앱 설정' 자리입니다. 언어·테마 같은 건 아직 없어서, 대신 "
            "**지금 이 화면이 어느 데이터를 보고 있는지**를 둡니다 — 값이 이상할 때 "
            "가장 먼저 의심할 자리입니다."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {"항목": "데이터베이스", "값": bootstrap_settings.database_url},
                    {
                        "항목": "암호화 마스터키",
                        "값": "설정됨" if bootstrap_settings.master_key else "없음 (KIS·텔레그램 저장 불가)",
                    },
                    {
                        "항목": "구글드라이브 동기화",
                        "값": "켜짐" if _drive_sync_configured() else "꺼짐 (이 화면에서만 저장됨)",
                    },
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    render_glossary_panel("admin_")


@st.cache_data(ttl=300)
def 화면버전() -> str:
    """지금 화면이 어느 커밋으로 돌고 있는지.

    왜 필요한가 — 배포된 대시보드가 **18개 커밋 전 코드**를 그대로 돌리고
    있던 적이 있다. 화면만 봐서는 알 길이 없어서 "고쳤는데 왜 그대로냐"를
    한참 헤맸다. 코드는 멀쩡한데 배포가 안 따라온 것이었다.

    한 줄이면 그 상황이 한눈에 보인다."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-1", "--pretty=format:%h (%ad)", "--date=short"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        # 버전을 모르는 것은 화면이 죽을 이유가 아니다. 다만 모른다고 말한다.
        return ""


def main() -> None:
    _initial_drive_sync()
    # st.title은 폰에서 두 줄로 넘쳐 첫 화면의 3분의 1을 먹었다.
    # 목업의 제목 크기에 맞춰 한 단계 낮춘다.
    st.markdown("### 자동매매 운영 대시보드")
    버전 = 화면버전()
    st.caption("자주 쓰는 항목 중심" + (f" · 화면 버전 {버전}" if 버전 else ""))
    render_save_notice()
    if _drive_sync_configured():
        render_drive_sync_fragment()

    if not bootstrap_settings.master_key:
        st.warning(
            "MUWON_MASTER_KEY가 설정되어 있지 않습니다. KIS/텔레그램처럼 "
            "암호화가 필요한 값은 저장·조회할 수 없습니다. `.env`에 키를 "
            "채운 뒤 다시 시작하세요 (docs/config_architecture.md 참고)."
        )

    service = get_service()

    broken_keys = service.undecryptable_secret_keys()
    if broken_keys:
        st.warning(
            "다음 값들이 **지금 MUWON_MASTER_KEY로는 열리지 않습니다** — "
            "마스터키를 새로 발급했는데 DB에는 이전 키로 암호화된 값이 남아 있는 "
            "상태입니다: `" + "`, `".join(broken_keys) + "`\n\n"
            "해당 항목(관리 탭의 KIS 인증정보 / 텔레그램 알림)에 값을 다시 입력해 "
            "저장하면 새 키로 다시 암호화되어 정상으로 돌아옵니다. GitHub "
            "Actions가 매 실행마다 KIS·텔레그램 값을 다시 써 주므로, 다음 "
            "자동매매 실행 뒤에 저절로 해결되기도 합니다.",
            icon="🔑",
        )

    # 목업은 하단 탭 바지만 Streamlit에는 그 위젯이 없다. CSS로 흉내내면
    # 버전이 오를 때마다 깨지므로 상단 탭을 쓴다 — 순서와 이름은 목업 그대로다.
    tab_home, tab_strategy, tab_records, tab_alerts, tab_admin = st.tabs(
        ["🏠 대시보드", "📈 전략", "📋 기록", "🔔 알림", "👤 관리"]
    )

    with tab_home, db_guard("대시보드 요약"):
        render_summary_cards(service)
        render_status_bar(service)
        render_next_run_banner(service)
        render_glossary_panel("home_")
        st.divider()
        render_home_rows(service)

    with tab_strategy:
        st.caption("전략 — 무엇을 기준으로 사고팔지 고릅니다")
        # 매매 주기가 다르면 필요한 데이터도, 실패하는 방식도, 성적을 재는
        # 방법도 다르다. 한 화면에 섞으면 "이 숫자가 어느 쪽 것인지"를
        # 매번 헷갈린다. 그래서 갈라 둔다.
        일단위, 실시간 = st.tabs(["📅 일단위 매매", "⚡ 실시간 매매"])
        with 일단위, db_guard("일단위 매매 화면"):
            render_daily_strategy(service)
        with 실시간, db_guard("실시간 매매 화면"):
            render_realtime_tab()

    with tab_records, db_guard("기록"):
        st.caption("기록 — 실제로 무엇을 사고팔았는지")
        with section("📊", "매매 기록", "청산까지 끝난 거래", expanded=True):
            st.caption(
                "**청산(팔아서 정리)까지 끝난 거래만** 여기 들어옵니다. 아직 들고 있는 종목은 "
                "대시보드 탭의 '보유 종목'에 있습니다 — 팔기 전 손익은 아직 확정된 돈이 아니기 때문입니다."
            )
            render_terms(["진입", "청산", "실현손익", "손절", "익절", "체결"])
            render_trades_tab()
        with section("🧾", "실행 기록", "엔진이 회차마다 무엇을 보고 무엇을 했나"):
            st.caption(
                "**화면이 비어 있을 때 이걸 보세요.** '오늘 살 게 없었다'와 '오늘 아예 안 돌았다'는 "
                "둘 다 빈 화면으로 보이지만 원인이 정반대입니다. "
                "신호가 0이면 전략이 못 찾은 것, 신호는 있는데 주문이 0이면 리스크 정책이 막은 것입니다."
            )
            render_terms(["신호", "유니버스", "킬스위치", "일일손실한도"])
            render_run_log()

    with tab_alerts, db_guard("알림"):
        st.caption("알림 — 체결·청산이 일어난 순서대로")
        render_notifications_tab()

    with tab_admin, db_guard("관리"):
        st.caption("설정 및 운영 관리")
        render_admin_tab(service)


def render_daily_strategy(service: SettingsService) -> None:
    """장 시작·마감에 하루 한 번 판단하는, 지금까지 만들어 온 매매."""
    with section("🎯", "활성 전략", "실거래에 적용되는 전략 하나", expanded=True):
        st.caption(
            "여러 전략 중 **하나만** 실제로 돕니다. 여기서 고른 전략이 매일 아침 "
            "종목을 판단합니다."
        )
        render_terms(["신호", "진입", "청산", "역추세", "추세추종", "팩터"])
        render_strategy_tab(service)
    with section(
        "🎓", "전략 성적표", "지금까지 잰 것을 한 장에", ["22개 전략", "10개 조합"],
        expanded=True,
    ):
        render_report_card()
    with section("⚖️", "전략 리뷰 결과", "다른 전략이었다면 어땠을지 비교"):
        st.caption(
            "매일 자동으로 '지금 이 전략 말고 다른 걸 썼다면 얼마였을까'를 계산해 쌓습니다. "
            "짧은 기간의 1등은 운일 수 있으니, 며칠 이상 계속 위에 있는지를 보세요."
        )
        render_terms(["수익률", "MDD", "승률", "손익비", "백테스트", "과최적화"])
        render_strategy_review_tab(service)


def render_realtime_tab() -> None:
    """실시간(장중) 매매 — 아직 매매하지 않는다. 무엇을 검증 중인지를 보인다.

    일단위 매매 화면과 달리 여기엔 성적표가 없다. 성적이 하나도 없기
    때문이다. 그런데 빈 표를 띄우면 "고장인가"로 읽힌다. 그래서 대신
    **왜 아직 성적이 없는지**를 화면 맨 위에 둔다."""
    try:
        계획 = realtime_plan.load()
    except (OSError, ValueError, KeyError) as e:
        st.error(f"실시간 매매 계획을 읽지 못했습니다: {e}")
        return

    # 이 한 줄이 이 화면에서 가장 중요하다. 실시간 탭이 있다는 사실만으로
    # "이미 돌고 있나 보다"로 읽히는 것을 막는다.
    st.info(f"**{계획.단계} 단계** — {계획.단계뜻}", icon="🔬")
    st.caption(계획.한줄)

    with section(
        "🧱", "무엇이 막고 있나", "성적이 아직 없는 이유", [f"{len(계획.막는것)}개"],
        expanded=True,
    ):
        st.caption(
            "이걸 먼저 두는 이유는, 이게 풀리기 전에는 아래 후보들의 성적을 "
            "낼 수 없기 때문입니다. 코드를 먼저 짜고 나중에 알게 되는 것이 최악입니다."
        )
        for 항목 in 계획.막는것:
            with st.expander(f"⛔ {항목['제목']}", expanded=False):
                st.write(항목["설명"])
                st.markdown(f"**그래서 →** {항목['그래서']}")

    with section(
        "🧪", "재 볼 후보", "무엇을 검증할 것이며 근거가 얼마나 단단한가",
        [f"후보 {len(계획.후보)}", f"즉시 {len(계획.지금가능한후보)}"],
        expanded=True,
    ):
        st.caption(
            "**근거 등급이 이 표의 핵심입니다.** '다들 그렇게 한다'와 '학술지에 실렸다'를 "
            "같은 칸에 두면 표 전체가 쓸모없어집니다."
        )
        with st.expander("근거 등급이 무슨 뜻인가", expanded=False):
            for 등급, (_, 뜻) in realtime_plan.GRADES.items():
                st.markdown(f"- **{등급}** — {뜻}")
        render_terms(["단타", "분봉", "장중모멘텀", "오버나이트", "갭", "슬리피지"])
        _render_candidates(계획.후보)

    with section(
        "📐", "끝난 검증", "실제로 재고 나서 내린 판단",
        [f"{len(계획.검증)}건"], expanded=True,
    ):
        if not 계획.검증:
            st.info(
                "아직 없습니다. 위 후보 중 '지금 가능'인 것부터 잽니다 — "
                "새 데이터를 모으지 않고도 잴 수 있는 것들입니다."
            )
        for 항목 in 계획.검증:
            with st.expander(f"✅ {항목.제목}  ·  {항목.측정일}", expanded=True):
                st.caption(f"**잰 것** — {항목.잰것}")
                st.markdown(항목.결과)
                st.success(f"**판단 →** {항목.판단}", icon="⚖️")

    st.caption(
        "자세한 조사 내용은 저장소의 `docs/단타전략조사.md`에 있습니다 — "
        "후보마다 원 출처와 한국 시장 적용 가능성을 적어 뒀습니다."
    )


def _render_candidates(후보) -> None:
    """후보를 한 줄씩. 표(dataframe)로 하면 긴 한줄평이 잘려서 정작
    읽어야 할 말이 안 보인다 — 용어 사전에서 같은 실수를 한 번 했다."""
    for c in 후보:
        색 = realtime_plan.GRADES[c.등급][0]
        가능 = "🟢 지금 가능" if c.지금가능 else "🔒 데이터 필요"
        st.markdown(
            f"**{c.이름}** &nbsp; :{색}-badge[근거 {c.등급}] &nbsp; "
            f":gray-badge[{가능}] &nbsp; :gray-badge[비용민감도 {c.비용민감도}]"
        )
        st.caption(f"{c.한줄}")
        with st.expander("자세히", expanded=False):
            st.markdown(
                f"- **근거 등급 {c.등급}** — {c.등급뜻}\n"
                f"- **한국 시장 증거** — {c.한국증거}\n"
                f"- **필요한 데이터** — {c.데이터}\n"
                f"- **비용 민감도** — {c.비용민감도}"
            )
            st.write(c.한줄평)
            st.caption(f"출처: {c.출처}")
        st.divider()


def render_next_run_banner(service: SettingsService) -> None:
    """다음 자동 실행까지 남은 시간을 한 줄로.

    펼치지 않아도 보여야 한다. 아무 일도 안 일어난 화면을 보고 "고장인가"와
    "아직 시간이 안 됐나"를 가르는 게 이 한 줄이다."""
    now = datetime.now(KST)
    뱃지, _, 설명 = automation_state(service.get_risk_policy(), now)
    if 뱃지 != "LIVE":
        # 이 줄이 화면 전체에서 가장 중요한 한 줄이다. 여기가 틀리면
        # "왜 아무 일도 안 일어나지"를 엉뚱한 데서 찾게 된다.
        st.warning(f"**자동매매 {뱃지}** — {설명}", icon="⏸️")

    jobs = upcoming(now)
    if not jobs:
        st.warning("자동 실행 일정을 읽지 못했습니다 (.github/workflows 확인).", icon="⏰")
        return
    nxt = jobs[0]
    # st.info는 HTML을 그리지 않는다 — <small>을 넣었더니 태그가 글자로
    # 그대로 나왔다. 그리고 %a는 'Wed'를 준다. 한국시간 안내에 영어 요일이
    # 섞이면 읽는 리듬이 끊긴다.
    st.info(
        f"**다음 {nxt.이름}** — {_korean_datetime(nxt.다음실행)} · "
        f"**{nxt.남은시간(now)}** ({nxt.설명문}, 한국시간)",
        icon="⏰",
    )


def _korean_datetime(moment: datetime | None) -> str:
    if moment is None:
        return "예정 없음"
    return f"{moment:%m월 %d일}({WEEKDAYS[moment.weekday()]}) {moment:%H:%M}"


def render_schedule_table() -> None:
    """자동으로 도는 것들의 전체 일정."""
    now = datetime.now(KST)
    jobs = upcoming(now)
    if not jobs:
        st.warning("워크플로 파일에서 일정을 읽지 못했습니다.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "무엇을": j.이름,
                    "언제": j.설명문,
                    "다음 실행": _korean_datetime(j.다음실행),
                    "남은 시간": j.남은시간(now),
                    "하는 일": j.설명,
                }
                for j in jobs
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "이 표는 `.github/workflows`의 실제 설정을 읽어 만듭니다 — 화면에 시각을 따로 "
        "적어 두지 않으므로, 일정을 바꾸면 여기도 같이 바뀝니다. "
        "GitHub 무료 스케줄러는 몇십 분씩 늦게 도는 일이 있어, 정각에 안 돌아도 정상입니다."
    )


def render_active_rules(service: SettingsService) -> None:
    """지금 돌고 있는 전략이 무엇을 보고 사고파는지.

    화면에는 전략 '이름'만 있었다. 이름은 그 전략이 무엇을 사는지 아무것도
    말해 주지 않는다. 무엇을 기준으로 샀는지 모르면 결과를 봐도 무엇을
    고쳐야 할지 판단할 수 없다."""
    선택 = service.get_strategy_selection()
    policy = service.get_risk_policy()
    try:
        strategy = build_strategies(선택.active_keys, 선택.combine, 선택.sell_keys)
        rules = describe(strategy)
    except (KeyError, ValueError, RuntimeError) as e:
        st.error(f"전략 기준을 읽지 못했습니다: {e}")
        return

    if len(선택.active_keys) > 1:
        묶음 = "모두 동의해야 삽니다 (AND)" if 선택.combine == "AND" else "하나라도 신호나면 삽니다 (OR)"
        st.markdown(f"#### 전략 {len(선택.active_keys)}개 · {묶음}")
        st.caption(" · ".join(f"`{k}`" for k in 선택.active_keys))
    else:
        key = 선택.active_key
        st.markdown(f"#### {_display_name_for(key)}")
        st.caption(f"전략 키 `{key}` · 계열 {_category_for(key)}")

    if rules.산다:
        st.markdown("**🟢 이럴 때 삽니다**")
        st.markdown("\n".join(f"- {line}" for line in rules.산다))
    # 파는 조건은 전략·리스크 정책 양쪽에 흩어져 있다. 화면에서까지
    # 흩어 두면 "매도는 기간밖에 없냐"는 오해가 생긴다 — 실제로 생겼다.
    조건, 주의 = exit_rules(strategy, policy)
    st.markdown("**🔴 이럴 때 팝니다** — 먼저 걸리는 것 하나로 팔립니다")
    st.markdown("\n".join(f"- {line}" for line in 조건))
    for line in 주의:
        st.caption(f"※ {line}")
    if rules.참고:
        st.markdown("**💡 알아 둘 점**")
        st.markdown("\n".join(f"- {line}" for line in rules.참고))
    if not rules.설명있음:
        st.warning(
            "이 전략은 아직 사람 말 설명이 붙지 않았습니다 — 위는 설정값 그대로입니다.",
            icon="📝",
        )

    st.markdown("**⚙️ 전략과 상관없이 항상 적용되는 규칙**")
    st.markdown(
        "\n".join(
            f"- {line}"
            for line in common_rules(policy, len(active_universe(get_session_factory(), list(UNIVERSE))), "market_cap")
        )
    )
    st.caption(
        "전략만 봐서는 '왜 신호가 났는데 안 샀지'를 설명할 수 없습니다. "
        "실제로는 이 규칙들이 먼저 걸러 냅니다."
    )


def render_home_rows(service: SettingsService) -> None:
    """목업 대시보드 탭의 카드 목록 5줄.

    목업은 '전략'·'기록'이 아래 탭에도 있는데 대시보드에도 같은 줄이 있다 —
    대시보드를 바로가기 허브로 쓰는 구조다. 다만 같은 입력 폼을 두 탭에
    그대로 두면 Streamlit이 '같은 키의 폼이 둘'이라며 화면 전체를 죽인다
    (실제로 그렇게 한 번 죽였다). 그래서 다른 탭에 본체가 있는 줄은 여기서
    **요약과 길안내만** 보여 주고, 본체가 여기뿐인 줄만 통째로 편다."""
    session_factory = get_session_factory()
    selection = service.get_strategy_selection()
    policy = service.get_risk_policy()

    with session_factory() as session:
        held = session.query(PositionRow).count()
        trade_count = session.query(TradeRow).count()
    latest_review, _ = _latest_daily_review(session_factory)

    with section(
        "💵", "지금 손익", "증권사 계좌를 그대로 조회한 평가손익",
        ["증권사 기준"], expanded=True,
    ):
        st.caption(
            "**평가손익**은 아직 안 판 종목이 지금 얼마가 됐는지입니다 — "
            "팔아야 실제로 손에 들어오므로, 위 카드의 **실현손익**(팔아서 확정된 손익)과는 "
            "다른 숫자입니다. 장이 열려 있으면 현재가로, 닫혀 있으면 그날 종가로 계산됩니다."
        )
        render_terms(["평가금액", "실현손익", "평가손익", "수익률"])
        render_account_pnl()

    with section(
        "🎯", "매매 기준", "지금 무엇을 보고 사고파는가",
        [automation_state(policy)[0], _active_strategy_label(selection)],
        expanded=True,
    ):
        render_active_rules(service)
        render_terms(["신호", "진입", "청산", "일봉", "유니버스"])
        st.caption("전략을 바꾸려면 **전략** 탭으로 가세요 — 설정은 한 곳에만 둡니다.")

    with section(
        "⏰", "실행 예정", "다음에 무엇이 언제 자동으로 도는가",
        [upcoming()[0].남은시간(datetime.now(KST)) if upcoming() else "확인 불가"],
    ):
        render_schedule_table()

    with section(
        "🥧", "보유 종목 & 최근 주문", "지금 들고 있는 것과 최신 체결",
        [f"{held}건", f"{TRADING_REFRESH_SECONDS}초 갱신"], expanded=True,
    ):
        st.caption(
            "**보유 종목**은 샀는데 아직 안 판 것, **최근 주문**은 사거나 팔라고 낸 지시입니다. "
            "주문을 냈다고 다 체결되는 건 아닙니다 — 장이 닫혀 있거나 가격이 안 맞으면 안 됩니다."
        )
        render_terms(["체결", "진입", "평가금액", "비중", "거래대금"])
        render_trading_fragment()

    with section("📊", "매매 기록", "청산까지 끝난 거래", [f"{trade_count}건"]):
        st.caption("전체 목록과 손익은 **기록** 탭에 있습니다. 여기서는 건수만 확인하세요.")

    with section(
        "⚖️", "전략 리뷰 결과", "다른 전략과의 비교",
        ["최신" if latest_review else "없음"],
    ):
        if latest_review:
            st.caption(
                f"가장 최근 리뷰 기준일은 **{latest_review.period_end}** 입니다. "
                "전체 순위표는 **전략** 탭에 있습니다."
            )
        else:
            st.caption("아직 리뷰 결과가 없습니다. 평일 자동 실행이 한 번은 돌아야 채워집니다.")

    with section(
        "🛡️", "리스크 정책", "손절 · 비중 · 노출 한도",
        ["적용 중" if policy.trading_enabled else "매수 중지"],
    ):
        st.caption(
            "**돈을 버는 규칙이 아니라, 크게 잃지 않기 위한 규칙입니다.** "
            "손절은 '이만큼 빠지면 미련 없이 판다', 비중은 '한 종목에 최대 몇 %까지', "
            "일일 손실 한도는 '오늘 이만큼 잃으면 오늘은 그만'입니다."
        )
        render_terms(["손절", "비중", "노출", "일일손실한도", "킬스위치", "ATR"])
        render_risk_tab(service)


def render_status_bar(service: SettingsService) -> None:
    policy = service.get_risk_policy()

    col_toggle, col_env, col_time = st.columns([2, 2, 1])
    with col_toggle:
        enabled = st.toggle(
            "신규 매수 허용",
            value=policy.trading_enabled,
            help=(
                "끄면 새로 사지 않습니다(킬스위치). 이미 들고 있는 종목의 손절은 "
                "그대로 작동합니다 — '더 안 산다'이지 '방치한다'가 아닙니다."
            ),
        )
        if enabled != policy.trading_enabled:
            켬 = "신규 매수를 허용했습니다" if enabled else "신규 매수를 막았습니다 (보유분 손절은 계속 작동)"
            if save_and_sync(
                lambda: service.set_risk_policy(
                    dataclasses.replace(policy, trading_enabled=enabled)
                ),
                켬,
            ):
                st.rerun()

    with col_env:
        try:
            kis_env = service.get_kis_credentials().kis_env
        except RuntimeError:
            kis_env = "(미확인)"
        # 'paper'라고만 띄우면 그게 좋은 건지 나쁜 건지 알 수가 없다.
        # 이 한 줄이 "지금 진짜 돈이 나가는가"를 답해야 한다.
        if kis_env == "real":
            st.error("**실거래** — 지금 나가는 주문은 진짜 돈입니다", icon="⚠️")
        elif kis_env == "paper":
            st.info("**모의투자** — 증권사가 준 가짜 돈이라 잃어도 실제 돈은 안 나갑니다", icon="🧪")
        else:
            st.warning(f"KIS 환경 확인 불가 ({kis_env})", icon="❓")

    with col_time:
        # 원래 '상태 조회: 00:15:58'이었다. 화면을 새로 그린 시각일 뿐인데
        # 폰에서 한 줄을 통째로 먹으면서 아무것도 안 알려 줬다. 대신 이
        # 토글이 무엇을 여닫는지를 적는다.
        st.caption("이 스위치는 **신규 매수**만 여닫습니다. 보유분 손절은 늘 작동합니다.")


def _best_backtest_by_key(session_factory) -> dict[str, BacktestRunRow]:
    """전략별로 가장 최근 백테스트 기록 하나씩 — 전략 목록 옆에 성적을
    같이 보여주기 위한 것(수동 스윕/일일 리뷰 구분 없이 최신 것)."""
    with session_factory() as session:
        rows = (
            session.query(BacktestRunRow)
            .order_by(BacktestRunRow.created_at.desc())
            .limit(500)
            .all()
        )
    latest: dict[str, BacktestRunRow] = {}
    for row in rows:
        latest.setdefault(row.strategy_key, row)
    return latest


def render_strategy_tab(service: SettingsService) -> None:
    """실거래에 쓰는 전략(가설)을 보여주고 바꾼다.

    "가설"이 뭔지: 이동평균/RSI 계산에 쓰는 숫자(며칠짜리 창을 볼지 등)를
    바꾸면 같은 로직이라도 다른 결과가 나온다 — 그 숫자 조합 하나하나가
    strategy/registry.py에 이름표(전략 키)를 달고 등록되어 있다. 여기서
    "활성"으로 고른 것 하나만 실제 매매(run_paper_trading.py /
    run_realtime_trading.py)에 쓰인다.

    전략이 20개가 넘어가면 한 표에 다 늘어놓는 게 오히려 안 읽히므로,
    계열(추세추종/평균회귀/돌파·모멘텀/복합) 필터와 백테스트 성적을 함께
    붙여 "어떤 계열이 지금 장에 통하는가"를 바로 볼 수 있게 했다."""
    current_key = service.get_strategy_selection().active_key
    backtests = _best_backtest_by_key(get_session_factory())

    selected_categories = st.multiselect(
        "계열 필터",
        options=CATEGORIES,
        default=CATEGORIES,
        help=(
            "추세추종=오르는 걸 따라 사고 꺾이면 판다(승률 낮고 손익비 큼) · "
            "평균회귀=많이 빠지면 되돌아온다에 베팅(승률 높고 한 번에 크게 잃을 위험) · "
            "돌파·모멘텀=박스를 뚫으면 그 방향으로 간다(가짜 돌파가 약점) · "
            "복합=여러 규칙을 섞은 것"
        ),
    )
    only_traded = st.toggle(
        "백테스트에서 거래가 있었던 전략만",
        value=False,
        help="조건이 너무 빡빡해 한 번도 진입하지 않은 가설을 숨깁니다.",
    )

    definitions = [d for d in list_definitions() if d.category in selected_categories]
    if only_traded:
        definitions = [d for d in definitions if (backtests.get(d.key) is not None and backtests[d.key].num_trades > 0)]

    if not definitions:
        st.info("조건에 맞는 전략이 없습니다 — 필터를 넓혀 보세요.")
        return

    rows = []
    for d in definitions:
        run = backtests.get(d.key)
        rows.append(
            {
                "활성": "⭐" if d.key == current_key else "",
                "계열": d.category,
                "전략": d.display_name,
                "키": d.key,
                "수익률": f"{run.total_return_pct:+.2f}%" if run else "-",
                "MDD": f"{run.max_drawdown_pct:.1f}%" if run else "-",
                "승률": f"{run.win_rate_pct:.0f}%" if run else "-",
                "거래": run.num_trades if run else "-",
                "상태": d.status,
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        height=min(38 * (len(rows) + 1) + 3, 460),
    )
    st.caption(
        f"등록 {len(list_definitions())}개 중 {len(definitions)}개 표시 · "
        "성적은 가장 최근 백테스트 기준(`run_hypothesis_sweep.py` / 매일 도는 `run_daily_review.py`)입니다. "
        "MDD=고점 대비 최대 하락폭, 승률=이익으로 끝난 매매 비율."
    )

    with st.expander("전략별 상세 설명", expanded=False):
        for d in definitions:
            st.markdown(f"**{d.display_name}** `{d.key}` · {d.category}  \n{d.description}")

    render_strategy_picker(service)


def render_report_card() -> None:
    """지금까지 잰 전략 성적을 한 장에 — 숫자를 모르는 사람도 읽히게.

    실험 결과가 세 군데에 흩어져 있었다. 숫자는 GitHub Actions 로그(만료된다)와
    아티팩트에, 판단은 설계안 문서에, 가설의 채택·기각은 구글 시트에. 그래서
    "이 전략 써도 되나"를 물으면 세 곳을 다 뒤져야 했다."""
    try:
        카드 = report_card.load()
    except (OSError, ValueError, KeyError) as e:
        st.error(f"성적표를 읽지 못했습니다: {e}")
        return

    기준 = 카드.기준
    st.caption(
        f"**{기준['유니버스']} · {기준['기간']}** 기준으로 잰 결과입니다. "
        f"측정일 {카드.측정일} · 커밋 `{기준['커밋']}`"
    )
    # 체결 가정은 표 맨 위에 있어야 한다. 아래 숫자를 다 읽고 나서
    # 알게 되면 이미 기대가 그 숫자에 맞춰진 뒤다.
    if 기준.get("체결가정"):
        st.warning(기준["체결가정"], icon="⚠️")

    if 카드.오래됐나():
        # 다시 계산하지 않는 기록이라, 오래된 숫자를 최신인 척 보여 주는 것이
        # 이 화면의 가장 위험한 실패 방식이다.
        st.warning(
            f"마지막으로 잰 지 한 달이 넘었습니다({카드.측정일}). "
            "그 뒤에 전략이나 종목 목록이 바뀌었다면 아래 숫자는 지금과 다를 수 있습니다.",
            icon="📅",
        )

    st.markdown("#### 🎓 무엇을 배웠나")
    st.caption("숫자보다 이게 먼저입니다. 표는 그 근거입니다.")
    for 항목 in 카드.배운것:
        with st.expander(항목["제목"], expanded=False):
            st.write(항목["내용"])

    st.markdown("#### 📋 전략 하나씩")
    st.info(기준["판정규칙"], icon="⚖️")
    render_terms(["수익률", "MDD", "샤프", "손익비", "회전율", "백테스트"])
    _render_card_rows(카드.전략)

    st.markdown("#### 🔗 여럿을 묶어 봤을 때")
    st.caption(
        "전략을 여러 개 걸면 나아지는지 실제로 재 본 것입니다. "
        "**[하나라도]** = 하나만 신호 나도 산다, **[모두]** = 전부 신호 내야 산다."
    )
    _render_card_rows(카드.조합)

    st.markdown("#### ⚠️ 이 숫자를 믿을 때 주의할 것")
    st.caption(
        f"**비용 가정**: {기준['비용']}  \n"
        "**과거일 뿐입니다** — 과거에 잘된 것이 앞으로도 잘된다는 보장은 없습니다. "
        "이 표의 쓰임은 '최소한 과거에도 안 통했던 건 거르자'입니다."
    )


def _render_card_rows(rows) -> None:
    if not rows:
        st.info("아직 기록이 없습니다.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "판정": r.판정,
                    "이름": r.이름,
                    "계열": r.계열,
                    "가장 나빴던 해": f"{r.최악:+.1f}%",
                    "5년 평균": f"{r.평균:+.1f}%",
                    "최대 낙폭": f"{r.낙폭:.1f}%",
                    "손익비": f"{r.손익비:.2f}",
                    "5년 거래": r.거래,
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
        height=min(36 * (len(rows) + 1) + 3, 520),
    )
    with st.expander("한 줄씩 풀어 보기", expanded=False):
        for r in rows:
            st.markdown(
                f"{_판정표시(r.판정)} **{r.이름}**  \n"
                f"<small>{r.한줄평}</small>",
                unsafe_allow_html=True,
            )


def _판정표시(판정: str) -> str:
    아이콘 = {"쓸만함": "🟢", "조건부": "🔵", "보류": "🟡", "안씀": "🔴"}
    return f"{아이콘.get(판정, '⚪')} `{판정}`"


def render_strategy_picker(service: SettingsService) -> None:
    """실거래에 걸 전략을 **여러 개** 고른다.

    고르는 칸에는 등록된 전략을 전부 넣는다. 화면 위 표는 계열 필터가
    걸려 있는데, 그 필터 때문에 이미 고른 전략이 목록에서 사라지면 저장하는
    순간 조용히 빠진다 — 필터는 보기 위한 것이지 고르는 범위가 아니다."""
    현재 = service.get_strategy_selection()
    전체 = [d.key for d in list_definitions()]
    기본 = [k for k in 현재.active_keys if k in 전체]

    with st.form("strategy_form"):
        고른것 = st.multiselect(
            "실거래에 쓸 전략 (여러 개 고를 수 있습니다)",
            options=전체,
            default=기본,
            format_func=lambda k: f"{get_definition(k).display_name}  ({k})",
        )
        방식 = st.radio(
            "여러 개를 고른 경우, 언제 삽니까",
            options=[COMBINE_OR, COMBINE_AND],
            index=0 if 현재.combine == COMBINE_OR else 1,
            format_func=lambda m: (
                "하나라도 신호가 나면 산다 (OR)"
                if m == COMBINE_OR
                else "고른 전략이 모두 신호를 내야 산다 (AND)"
            ),
            horizontal=False,
        )
        st.caption(
            "**OR**는 기회가 늘고 신호가 잦아집니다. **AND**는 조건이 까다로워져 "
            "기회가 크게 줄지만 잡음이 걸러집니다.  \n"
            "**파는 쪽은 어느 쪽을 골라도 '하나라도'입니다** — 모두 동의해야 팔게 하면, "
            "한 전략이 침묵하는 동안 손실이 나는 종목을 계속 들고 있게 됩니다."
        )
        저장 = st.form_submit_button("이 전략들로 전환")

    if not 저장:
        return
    if not 고른것:
        st.error("전략을 하나 이상 골라 주세요. 하나도 없으면 아무것도 사지 않습니다.")
        return

    선택 = StrategySelection(active_keys=tuple(고른것), combine=방식)
    if save_and_sync(
        lambda: service.set_strategy_selection(선택),
        f"활성 전략을 바꿨습니다 — {선택.describe()} · 다음 매매 실행부터 반영됩니다.",
    ):
        st.rerun()


@st.cache_data(ttl=600, show_spinner=False)
def _시트연결() -> str:
    """대시보드가 시트를 만질 수 있나. 없으면 화면만 보여 준다.

    **여기서 터지면 관리 탭이 통째로 죽는다.** 구글이 느리거나 자격증명이
    만료돼도 화면은 살아 있어야 한다 — 화면이 죽으면 킬스위치를 끄러 들어올
    수조차 없다. 그래서 어떤 실패든 빈 값으로 돌려준다.

    그리고 캐시한다. 화면을 열 때마다 구글에 물어보면 그만큼 느려지는데,
    시트 주소는 거의 안 바뀐다."""
    import os

    시트 = os.environ.get("MUWON_SHEET_ID", "")
    if 시트:
        return 시트
    폴더 = os.environ.get("GDRIVE_FOLDER_ID", "")
    if not 폴더 or not os.environ.get("GDRIVE_SA_KEY_JSON"):
        return ""
    try:
        from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

        return find_or_create(폴더, DEFAULT_TITLE)[0]
    except Exception:  # noqa: BLE001 — 화면이 죽는 것보다 낫다
        return ""


def render_criteria_tab() -> None:
    """기준표를 그대로 화면에 편다.

    목록을 여기에 손으로 적지 않는다 — `settings/from_sheet.py`의 기준표
    하나만 본다. 기준을 새로 추가했는데 화면에 안 나오면 아무도 못 고친다.

    **왜 이 값이 중요한지를 값 옆에 붙여 둔다.** 숫자만 있으면 몇 달 뒤에
    '이걸 왜 12로 뒀지'를 답할 수 없다."""
    try:
        from muwon.cloud.sector_sheet import read, update_setting
        from muwon.settings.from_sheet import parse_settings, 값글자, 기준들
    except ImportError as e:
        st.warning(f"구글 시트 라이브러리가 없습니다 — {e}", icon="📦")
        return

    sheet_id = _시트연결()
    if not sheet_id:
        st.warning(
            "구글 시트에 연결돼 있지 않습니다 (`GDRIVE_FOLDER_ID`·`GDRIVE_SA_KEY_JSON`). "
            "기준은 시트가 원본이라 여기서는 보여 드릴 수가 없습니다.",
            icon="🔌",
        )
        return

    try:
        시트 = parse_settings(read(sheet_id).설정)
    except Exception as e:  # noqa: BLE001 — 화면이 통째로 죽는 것보다 낫다
        st.error(f"시트를 못 읽었습니다 — {type(e).__name__}: {e}")
        st.caption("**이 상태에서는 매매가 자동으로 꺼집니다.** 시트를 고쳐 주세요.")
        return

    st.link_button("구글 시트에서 열기", f"https://docs.google.com/spreadsheets/d/{sheet_id}")

    바꿀것: dict[str, str] = {}
    with st.form("criteria_form"):
        for b in 기준들:
            지금 = 시트.가져오기(b.이름)
            왼, 오 = st.columns([2, 3])
            with 왼:
                if b.종류 == "참거짓":
                    새것 = st.checkbox(b.표시, value=bool(지금), key=f"cr_{b.이름}")
                    글자 = "true" if 새것 else "false"
                else:
                    새것 = st.number_input(
                        b.표시, value=float(지금), key=f"cr_{b.이름}",
                        min_value=float(b.최소) if b.최소 is not None else None,
                        max_value=float(b.최대) if b.최대 is not None else None,
                        step=1.0 if b.종류 == "정수" else 0.5,
                        format="%d" if b.종류 == "정수" else "%.2f",
                    )
                    글자 = f"{int(새것)}" if b.종류 == "정수" else f"{새것:g}"
                st.caption(f"`{b.이름}` · 지금 {값글자(b, 지금)}")
            with 오:
                st.caption(f"**{b.설명}**")
                st.caption(b.왜)
            if 글자 != _글자로(b, 지금):
                바꿀것[b.이름] = 글자
            st.divider()

        저장 = st.form_submit_button("시트에 저장", type="primary")

    if 저장:
        if not 바꿀것:
            st.info("바뀐 값이 없습니다.")
            return
        for 이름, 글자 in 바꿀것.items():
            update_setting(sheet_id, 이름, 글자)
        st.success(f"{len(바꿀것)}개를 시트에 저장했습니다 — 다음 실행부터 적용됩니다.")
        st.rerun()


def _글자로(b, 것) -> str:
    if b.종류 == "참거짓":
        return "true" if 것 else "false"
    return f"{int(것)}" if b.종류 == "정수" else f"{float(것):g}"


def render_risk_tab(service: SettingsService) -> None:
    current = service.get_risk_policy()

    # 3단계(docs/설계_스트림릿을_걷어낼까.md)부터 **리스크 기준의 원본은
    # 구글 시트**다. 여기 값도 살아 있지만 시트가 이기므로, 그 사실을 안
    # 적어 두면 사람은 여기서 고치고 "왜 안 먹지"를 겪는다.
    #
    # 킬스위치만 규칙이 다르다 — 어느 한쪽에서 꺼도 꺼진다.
    st.info(
        "**이 값들의 원본은 이제 구글 시트입니다.** 시트에 적힌 항목은 시트 값이 "
        "쓰이고, 시트에 없는 항목만 여기 값이 쓰입니다.\n\n"
        "**킬스위치는 예외입니다 — 시트와 여기가 둘 다 켜져야 켜집니다.** "
        "끄는 것은 어느 한쪽만으로도 꺼집니다.",
        icon="📋",
    )

    with st.form("risk_form"):
        max_position_weight = st.number_input(
            "종목당 최대 비중",
            min_value=0.01,
            max_value=1.0,
            value=current.max_position_weight,
            step=0.01,
            format="%.2f",
        )
        stop_loss_pct = st.number_input(
            "손절 기준 (음수, 예: -0.05 = -5%)",
            min_value=-1.0,
            max_value=0.0,
            value=current.stop_loss_pct,
            step=0.01,
            format="%.2f",
        )
        daily_loss_limit_pct = st.number_input(
            "일일 손실 한도 (음수)",
            min_value=-1.0,
            max_value=0.0,
            value=current.daily_loss_limit_pct,
            step=0.01,
            format="%.2f",
        )
        max_concurrent_positions = st.number_input(
            "최대 동시 보유 종목 수",
            min_value=1,
            max_value=50,
            value=current.max_concurrent_positions,
            step=1,
        )
        submitted = st.form_submit_button("저장")

    if submitted:
        저장 = save_and_sync(
            lambda: service.set_risk_policy(
                RiskPolicy(
                    max_position_weight=max_position_weight,
                    stop_loss_pct=stop_loss_pct,
                    daily_loss_limit_pct=daily_loss_limit_pct,
                    max_concurrent_positions=int(max_concurrent_positions),
                    trading_enabled=current.trading_enabled,  # 상단 토글이 이 값의 유일한 창구
                )
            ),
            "리스크 정책 저장 완료 — 봇은 최대 5초 안에 반영합니다.",
        )
        if 저장:
            st.rerun()


def render_kis_tab(service: SettingsService) -> None:
    try:
        current = service.get_kis_credentials()
    except RuntimeError as e:
        st.error(str(e))
        return

    st.caption(
        f"현재: env={current.kis_env} · app_key={_mask(current.app_key)} · "
        f"app_secret={_mask(current.app_secret)} · account_no={_mask(current.account_no)}"
    )
    if current.is_real:
        st.warning("현재 실거래(real) 환경으로 설정되어 있습니다.")

    with st.form("kis_form"):
        kis_env = st.selectbox(
            "환경", options=["paper", "real"], index=["paper", "real"].index(current.kis_env)
        )
        app_key = st.text_input("App Key", value="", type="password", placeholder="변경 시에만 입력")
        app_secret = st.text_input(
            "App Secret", value="", type="password", placeholder="변경 시에만 입력"
        )
        account_no = st.text_input("계좌번호", value="", placeholder="변경 시에만 입력")
        account_product_cd = st.text_input(
            "계좌상품코드", value=current.account_product_cd or "01"
        )
        submitted = st.form_submit_button("저장")

    if submitted:
        try:
            service.set_kis_credentials(
                KISCredentials(
                    kis_env=kis_env,
                    app_key=app_key or current.app_key,
                    app_secret=app_secret or current.app_secret,
                    account_no=account_no or current.account_no,
                    account_product_cd=account_product_cd or current.account_product_cd,
                )
            )
            if save_and_sync(lambda: None, "KIS 인증정보 저장 완료"):
                st.rerun()
        except RuntimeError as e:
            st.error(str(e))


def render_telegram_tab(service: SettingsService) -> None:
    try:
        current = service.get_telegram_config()
    except RuntimeError as e:
        st.error(str(e))
        return

    st.caption(f"현재: chat_id={current.chat_id or '(미설정)'} · bot_token={_mask(current.bot_token)}")

    with st.form("telegram_form"):
        bot_token = st.text_input(
            "Bot Token", value="", type="password", placeholder="변경 시에만 입력"
        )
        chat_id = st.text_input("Chat ID", value=current.chat_id)
        submitted = st.form_submit_button("저장")

    if submitted:
        try:
            service.set_telegram_config(
                TelegramConfig(bot_token=bot_token or current.bot_token, chat_id=chat_id)
            )
            if save_and_sync(lambda: None, "텔레그램 설정 저장 완료"):
                st.rerun()
        except RuntimeError as e:
            st.error(str(e))


def _display_setting_value(value: str | None, is_secret: bool, decrypted: bool) -> str:
    if is_secret and not decrypted:
        return "(복호화 불가)"
    if value is None:
        return "(신규)"
    if is_secret:
        return _mask(value)
    return value


@st.fragment(run_every=HISTORY_REFRESH_SECONDS)
def render_history_fragment(service: SettingsService) -> None:
    render_history_tab(service)
    st.caption(f"마지막 갱신: {datetime.now():%H:%M:%S}")  # noqa: DTZ005 — 화면 표시용, 로컬시각이면 충분


def render_history_tab(service: SettingsService) -> None:
    st.caption("리스크 정책·KIS 인증정보·텔레그램 값이 바뀔 때마다 자동으로 남는 기록입니다.")

    entries = service.get_settings_history(limit=200)
    if not entries:
        st.info("아직 변경 이력이 없습니다.")
        return

    rows = [
        {
            "변경시각": e.changed_at.strftime("%Y-%m-%d %H:%M:%S"),
            "설정키": e.key,
            "이전값": _display_setting_value(e.old_value, e.is_secret, e.decrypted),
            "새값": _display_setting_value(e.new_value, e.is_secret, e.decrypted),
            "비밀값": "예" if e.is_secret else "",
        }
        for e in entries
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


@st.fragment(run_every=DEVLOG_REFRESH_SECONDS)
def render_devlog_fragment() -> None:
    render_devlog_tab()
    st.caption(f"마지막 갱신: {datetime.now():%H:%M:%S}")  # noqa: DTZ005 — 화면 표시용, 로컬시각이면 충분


def render_devlog_tab() -> None:
    try:
        output = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "log", "-n", "50", "--pretty=format:%h|%ad|%s", "--date=short"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        st.error(f"git 로그를 읽을 수 없습니다: {e}")
        return

    rows = []
    for line in output.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append({"커밋": parts[0], "날짜": parts[1], "메시지": parts[2]})

    if not rows:
        st.info("커밋 기록이 없습니다.")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _symbol_name(symbol: str) -> str:
    ticker = find_by_symbol(symbol)
    return ticker.name if ticker else symbol


@st.fragment(run_every=TRADING_REFRESH_SECONDS)
def render_trading_fragment() -> None:
    render_trading_tab()
    st.caption(f"마지막 갱신: {datetime.now():%H:%M:%S}")  # noqa: DTZ005 — 화면 표시용, 로컬시각이면 충분


def _손익색(값: float) -> str:
    """한국은 **빨강이 오름, 파랑이 내림**이다 — 미국과 반대다.

    st.metric은 미국식이라 손실에 빨간 화살표를 붙이는데, 그걸 그대로 두면
    화면이 "올랐다"고 말하는 셈이 된다. 그래서 카드를 직접 그리고 색을
    여기서 정한다. 0은 오르지도 내리지도 않았으니 중립색이다."""
    if 값 > 0:
        return "orange"  # 이 화면에서 가장 붉은 칩
    if 값 < 0:
        return "blue"
    return "green"


#: 계좌 조회 결과를 얼마나 오래 재활용할지. KIS는 **토큰 발급을 자주 하면
#: 403으로 막는다** — 조회할 때마다 새 토큰을 받으므로 화면을 열 때마다
#: 부르면 금방 막힌다(실제로 막혔다). 60초면 사람이 보기엔 '지금 값'이고
#: 증권사 한도에는 여유가 있다.
BALANCE_CACHE_SECONDS = 60


@st.cache_data(ttl=BALANCE_CACHE_SECONDS, show_spinner="증권사 계좌 조회 중…")
def _계좌조회() -> tuple[dict | None, str, str]:
    """증권사 계좌를 조회해 화면에 쓸 값만 추려 돌려준다.

    (내용, 무엇이_잘못됐나, 그래서_뭘_하면_되나) 꼴이다. **왜 안내를 따로
    돌려주나**: 원인이 둘인데 할 일이 정반대다. 인증정보가 없으면 관리 탭에
    값을 넣어야 하고, 증권사가 막은 거면 그냥 기다리면 된다. 안내를 하나로
    뭉쳐 두면 "기다리세요"라고 해 놓고 영영 안 고쳐지는 화면이 된다.

    실패해도 예외를 올리지 않는다 — 이 구역 하나 때문에 대시보드 전체가
    죽으면 안 된다. 화면이 통째로 빨개져서 남은 탭도 못 보게 된 적이 있었다.

    st.cache_data는 돌려준 값을 피클로 저장하므로 평범한 dict로 만든다."""
    try:
        service = get_service()
        creds = service.get_kis_credentials()
        if not (creds.app_key and creds.app_secret and creds.account_no):
            return None, "KIS 인증정보가 없습니다.", (
                "**관리** 탭에서 앱키·시크릿·계좌번호를 넣어 주세요. "
                "기다린다고 해결되지 않습니다."
            )
        잔고 = KISClient.from_settings(service).get_balance()
    except Exception as e:  # noqa: BLE001 — 조회 실패가 화면을 죽이면 안 된다
        return None, f"{type(e).__name__}: {e}", (
            "증권사 서버가 잠깐 막았을 수 있습니다 — **토큰 발급을 자주 하면 막습니다.** "
            "1분쯤 뒤 **지금 다시 조회**를 눌러 보세요."
        )

    원가 = sum(h.quantity * h.avg_buy_price for h in 잔고.holdings)
    return {
        "현금": 잔고.cash,
        "주식평가금": 잔고.total_eval_amount,
        "순자산": 잔고.net_asset,
        "원가": 원가,
        "평가손익": sum(h.pnl_amount for h in 잔고.holdings),
        "조회시각": datetime.now().strftime("%H:%M:%S"),  # noqa: DTZ005 — 화면 표시용
        "종목": [
            # 손익을 앞에 둔다. 폰 폭에서는 표가 옆으로 잘리는데, 뒤에 두면
            # 정작 보려던 두 칸이 화면 밖으로 나가서 가로로 밀어야 보인다.
            {
                "종목": f"{h.name or _symbol_name(h.symbol)}({h.symbol})",
                "평가손익": h.pnl_amount,
                "수익률": (
                    (h.current_price / h.avg_buy_price - 1) * 100 if h.avg_buy_price else 0.0
                ),
                "수량": h.quantity,
                "평균매입가": h.avg_buy_price,
                "현재가": h.current_price,
                "평가금액": h.eval_amount,
            }
            for h in 잔고.holdings
        ],
    }, "", ""


def render_account_pnl() -> None:
    """증권사 계좌를 그대로 조회해 지금 평가손익을 보여 준다.

    **왜 우리 DB로 계산하지 않나**: DB에는 산 값(진입가)만 있고 지금 값이
    없다. 지금 값을 알려면 어차피 시세를 받아야 하는데, 그럴 바엔 증권사가
    이미 계산해 둔 평가손익을 그대로 받는 게 정확하다 — 수수료·세금까지
    반영된 증권사 기준 숫자이고, 우리가 따로 계산하면 두 숫자가 어긋난다.

    **왜 5초마다 자동 갱신하지 않나**: 보유 종목 표는 DB만 읽어서 5초마다
    새로 그려도 공짜지만, 이 구역은 증권사를 부른다. 5초마다 부르면 토큰
    한도에 걸려 아예 안 나오게 된다. 그래서 60초 캐시 + 손 새로고침이다.
    """
    col_left, col_right = st.columns([3, 1])
    with col_right:
        if st.button("🔄 지금 다시 조회", use_container_width=True, key="refresh_balance"):
            _계좌조회.clear()
            st.rerun()

    내용, 오류, 안내 = _계좌조회()
    if 내용 is None:
        with col_left:
            st.warning(f"계좌를 조회하지 못했습니다 — {오류}", icon="📡")
            st.caption(f"{안내} 이 구역만 못 보는 것이고 나머지 화면은 그대로 씁니다.")
        return

    손익 = 내용["평가손익"]
    수익률 = (손익 / 내용["원가"] * 100) if 내용["원가"] else 0.0
    with col_left:
        # st.metric은 글자가 커서 '9,999,135원'이 '9,999,13…'으로 잘렸다
        # (900px에서도 잘렸으니 폰에서는 말할 것도 없다). 이 화면에서 제일
        # 중요한 게 금액인데 그 금액이 안 보이면 카드가 있으나 마나다.
        # 그래서 위 요약 카드와 같은 틀을 쓴다 — 폰 폭에서 이미 검증된 것이다.
        st.markdown(CARD_CSS, unsafe_allow_html=True)
        st.markdown(
            '<div class="muwon-cards">'
            + _card("💼", "blue", "순자산", f"{내용['순자산']:,.0f}원", "현금+주식", "blue")
            + _card(
                "📊", _손익색(손익), "평가손익", f"{손익:+,.0f}원",
                f"{수익률:+.2f}%" if 내용["원가"] else "—", _손익색(손익),
            )
            + _card("💵", "purple", "주문 가능 현금", f"{내용['현금']:,.0f}원", "결제 반영", "purple")
            + "</div>",
            unsafe_allow_html=True,
        )

    if 내용["종목"]:
        표 = pd.DataFrame(내용["종목"])
        st.dataframe(
            표.style.format({
                "평균매입가": "{:,.0f}", "현재가": "{:,.0f}",
                "평가금액": "{:,.0f}", "평가손익": "{:+,.0f}", "수익률": "{:+.2f}%",
            }),
            use_container_width=True, hide_index=True,
        )
    else:
        st.info("증권사 계좌에 보유 종목이 없습니다.")

    st.caption(
        f"증권사 계좌 기준 · {내용['조회시각']} 조회 · {BALANCE_CACHE_SECONDS}초까지 재사용"
    )


def render_trading_tab() -> None:
    session_factory = get_session_factory()
    with session_factory() as session:
        positions = session.query(PositionRow).order_by(PositionRow.entry_date.desc()).all()
        orders = session.query(OrderRow).order_by(OrderRow.created_at.desc()).limit(50).all()

    col_positions, col_orders = st.columns(2)
    with col_positions:
        st.caption("보유 종목")
        if not positions:
            st.info("보유 중인 포지션이 없습니다.")
        else:
            rows = [
                {
                    "종목": f"{_symbol_name(p.symbol)}({p.symbol})",
                    "수량": p.quantity,
                    "진입가": f"{p.entry_price:,.0f}",
                    "진입일": p.entry_date.isoformat(),
                    "진입사유": p.entry_reason,
                }
                for p in positions
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_orders:
        st.caption("최근 주문 (최대 50건)")
        if not orders:
            st.info("주문 기록이 없습니다.")
        else:
            rows = [
                {
                    "시각": o.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "종목": f"{_symbol_name(o.symbol)}({o.symbol})",
                    "구분": "매수" if o.side == "buy" else "매도",
                    "수량": o.quantity,
                    "가격": f"{o.price:,.0f}",
                    "사유": o.reason,
                    "모의": "예" if o.is_paper else "실전",
                }
                for o in orders
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_trades_tab() -> None:
    """청산까지 끝난 매매(진입+청산 한 왕복)만 보여준다 — 아직 들고 있는
    포지션은 위 '보유 종목' 표에 있다. 어떤 전략(strategy_key)이 어떤
    조건에서 이기고 졌는지를 보려는 용도라, 향후 이 데이터를 AI가 읽고
    전략 수정을 제안하는 단계로 이어질 수 있도록 만들어 둔 표다."""
    session_factory = get_session_factory()
    with session_factory() as session:
        trades = session.query(TradeRow).order_by(TradeRow.exited_at.desc()).limit(50).all()

    if not trades:
        st.info("아직 청산까지 완료된 매매 기록이 없습니다.")
        return

    rows = [
        {
            "종목": f"{_symbol_name(t.symbol)}({t.symbol})",
            "전략": t.strategy_key,
            "수량": t.quantity,
            "진입가": f"{t.entry_price:,.0f}",
            "청산가": f"{t.exit_price:,.0f}",
            "손익": f"{t.pnl_amount:+,.0f}",
            "손익률": f"{t.pnl_pct:+.2f}%",
            "진입사유": t.entry_reason,
            "청산사유": t.exit_reason,
            "청산일시": t.exited_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for t in trades
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _display_name_for(strategy_key: str) -> str:
    try:
        return get_definition(strategy_key).display_name
    except KeyError:
        return strategy_key  # 이후 레지스트리에서 빠진 옛 전략 키일 수 있음


def _category_for(strategy_key: str) -> str:
    try:
        return get_definition(strategy_key).category
    except KeyError:
        return "-"


def _latest_daily_review(session_factory) -> tuple[BacktestRunRow | None, list[BacktestRunRow]]:
    """scripts/run_daily_review.py가 매일 남기는 기록(notes="daily_review")
    중, 가장 최근 기준일(period_end)의 전략별 결과를 하나씩만 골라 돌려준다
    (같은 날 여러 번 재실행했으면 가장 최근 것만 남긴다)."""
    with session_factory() as session:
        rows = (
            session.query(BacktestRunRow)
            .filter(BacktestRunRow.notes == "daily_review")
            .order_by(BacktestRunRow.period_end.desc(), BacktestRunRow.created_at.desc())
            .all()
        )
    if not rows:
        return None, []

    latest_period_end = rows[0].period_end
    seen: set[str] = set()
    latest_rows = []
    for row in rows:
        if row.period_end != latest_period_end or row.strategy_key in seen:
            continue
        seen.add(row.strategy_key)
        latest_rows.append(row)
    return rows[0], latest_rows


def render_strategy_review_tab(service: SettingsService) -> None:
    """"오늘 다른 전략이었다면 수익률이 어땠을까"를 매일 자동으로 계산해
    쌓아둔 결과(scripts/run_daily_review.py)를 표로 보여준다. GitHub
    Actions가 평일마다 자동으로 채워주므로, 여기서는 DB에 이미 쌓인
    값을 읽기만 한다."""
    session_factory = get_session_factory()
    latest, rows = _latest_daily_review(session_factory)

    if latest is None:
        st.info(
            "아직 일일 전략 리뷰 결과가 없습니다 — "
            "scripts/run_daily_review.py가 최소 한 번은 실행되어야 합니다 "
            "(GitHub Actions가 평일마다 자동으로 실행합니다)."
        )
        return

    active_key = service.get_strategy_selection().active_key
    active_row = next((r for r in rows if r.strategy_key == active_key), None)

    st.caption(f"기준 기간: {latest.period_start} ~ {latest.period_end} (최근 일일 리뷰)")

    sorted_rows = sorted(rows, key=lambda r: r.total_return_pct, reverse=True)
    table_rows = [
        {
            "활성": "⭐" if r.strategy_key == active_key else "",
            "계열": _category_for(r.strategy_key),
            "전략": _display_name_for(r.strategy_key),
            "수익률": f"{r.total_return_pct:+.2f}%",
            "MDD": f"{r.max_drawdown_pct:.1f}%",
            "승률": f"{r.win_rate_pct:.0f}%",
            "거래": r.num_trades,
            "활성 대비": (
                "-"
                if active_row is None
                else f"{r.total_return_pct - active_row.total_return_pct:+.2f}%p"
            ),
        }
        for r in sorted_rows
    ]
    st.dataframe(
        pd.DataFrame(table_rows),
        use_container_width=True,
        hide_index=True,
        height=min(38 * (len(table_rows) + 1) + 3, 460),
    )

    # 계열별 평균 — 개별 전략의 운을 걷어내고 "지금 장에 어떤 성격이 통하는가"를 본다
    by_category: dict[str, list[float]] = {}
    for r in sorted_rows:
        by_category.setdefault(_category_for(r.strategy_key), []).append(r.total_return_pct)
    if len(by_category) > 1:
        summary = sorted(
            ((cat, sum(v) / len(v), len(v)) for cat, v in by_category.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        st.caption("계열별 평균 수익률 — 개별 전략의 운보다 '지금 장의 성격'을 보여준다")
        st.dataframe(
            pd.DataFrame(
                [{"계열": c, "평균 수익률": f"{avg:+.2f}%", "전략 수": n} for c, avg, n in summary]
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.caption(
        "MDD(최대낙폭)는 그 기간 중 고점 대비 최대 몇 %까지 떨어졌었는지, "
        "승률은 전체 매매 중 이익으로 끝난 비율입니다. "
        "승률이 낮아도 이길 때 크게 이기면 총수익은 플러스일 수 있으니 함께 봐야 합니다."
    )


if __name__ == "__main__":
    main()
