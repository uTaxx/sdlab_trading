"""**두 단계로 고른다 — 1차 섹터, 2차 종목.** 그리고 아무것도 사지 않는다.

후보를 시트 `승인대기` 탭에 적고 텔레그램으로 알릴 뿐이다. 실제 매수는
사람이 승인 칸에 Y를 적은 뒤에야 일어난다.

## 1차 — 섹터

섹터 지수를 만들고 **최근 N일 시장 대비 강도**로 줄을 세운다.

**기본값은 "보여 주기만" 한다.** 강도 상위 섹터만 사는 것은 기본으로
꺼져 있다 — 그게 성적을 올린다는 근거가 없기 때문이다
(`docs/섹터선정_검증.md`: 여섯 조합 전부 우연 폭 안, 여덟 해 중 다섯 해
마이너스). 켜려면 `--sector-filter`를 붙인다.

대신 **한 섹터에서 두 종목까지**만 산다. 이건 예측이 아니라 제약이라
검증 결과와 무관하게 유효하다. 45종목을 한 줄로 세워 놓고 신호 난 것을
사면 어떤 날은 반도체 다섯 종목이 한꺼번에 잡히는데, 그건 분산이 아니라
반도체 하나에 다섯 배로 건 것이다.

## 2차 — 종목

**새 규칙을 만들지 않는다.** 1차를 통과한 섹터의 종목만 기존 매수 신호에
태운다. 바뀐 것은 대상이지 판단 기준이 아니다 — 그래야 성적이 달라졌을 때
무엇 때문인지 안다.

사용 예:
    python scripts/propose_buys.py --dry-run
    python scripts/propose_buys.py --sector-filter --dry-run   # 1차로 거르기까지
    python scripts/propose_buys.py                             # 시트 + 텔레그램
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests
from sqlalchemy import select

from muwon.cloud.approval import pending_rows, read_today, 승인머리, 알림글, 후보
from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read
from muwon.cloud.sheet_log import append
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.models import PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.domain.types import SignalType
from muwon.market.sector_index import build_index
from muwon.sector.selection import (
    cap_per_sector,
    format_ranking,
    pick,
    rank,
)
from muwon.settings.from_sheet import parse_settings
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import build_strategies

KST = ZoneInfo("Asia/Seoul")
#: 지표 예열에 필요한 기간. 짧으면 이동평균이 안 나와 신호가 통째로 빈다.
WARMUP_DAYS = 400
#: 이만큼은 와야 판단한다. 야후가 간헐적으로 최근 20일치만 주는데,
#: 그걸 모르고 쓰면 지표가 안 나와 그 종목이 조용히 후보에서 빠진다.
MIN_DAYS = 60


def 사흘등락(df) -> tuple[float, ...]:
    """최근 사흘 하루하루의 등락률(%). 오래된 날부터.

    승인 단추를 누를 때 "이미 많이 오른 것을 사는 건 아닌가"를 보는 자리다.
    지금 걸린 전략은 거래량이 늘고 값이 오른 날 사는 것이라, 후보가 사흘
    내리 올랐다면 늦게 들어가는 것일 수 있다. 그 판단은 사람이 한다."""
    종가 = df["close"].astype(float)
    if len(종가) < 2:
        return ()
    변동 = (종가.pct_change() * 100).dropna()
    return tuple(round(float(ㄱ), 2) for ㄱ in 변동.tail(3))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true", help="시트·텔레그램에 안 보내고 화면에만")
    parser.add_argument("--force", action="store_true",
                        help="오늘 후보가 이미 시트에 있어도 다시 뽑아 올린다")
    parser.add_argument("--max", type=int, default=0, help="후보를 몇 개까지 (0이면 설정의 동시보유 수)")
    # 아래 넷은 **시트가 원본**이다. 여기 값은 시험해 볼 때만 쓴다 —
    # 안 주면 시트에 적힌 것을 쓰고, 시트에도 없으면 기준표의 기본값을 쓴다.
    parser.add_argument("--lookback", type=int, default=0, help="섹터 강도를 며칠로 잴 것인가")
    parser.add_argument("--top-sectors", type=int, default=0, help="1차에서 몇 섹터를 고를 것인가")
    parser.add_argument("--max-per-sector", type=int, default=-1,
                        help="한 섹터에서 몇 종목까지 (0이면 제한 없음)")
    parser.add_argument("--sector-filter", action="store_true",
                        help="강도 상위 섹터만 매수 대상으로 (근거 없음 — docs/섹터선정_검증.md)")
    args = parser.parse_args()

    sheet_id = args.sheet_id
    if not sheet_id:
        if not args.folder_id:
            raise SystemExit("MUWON_SHEET_ID도 GDRIVE_FOLDER_ID도 없습니다.")
        sheet_id, _ = find_or_create(args.folder_id, DEFAULT_TITLE)

    내용 = read(sheet_id)
    설정 = parse_settings(내용.설정)

    lookback = args.lookback or int(설정.가져오기("sector_lookback"))
    top_sectors = args.top_sectors or int(설정.가져오기("sector_top_n"))
    섹터당 = args.max_per_sector if args.max_per_sector >= 0 else int(설정.가져오기("max_per_sector"))
    섹터거르기 = args.sector_filter or bool(설정.가져오기("sector_filter_enabled"))

    ensure_schema(bootstrap_settings.database_url)
    service = build_settings_service()
    selection = service.get_strategy_selection()
    strategy = build_strategies(selection.active_keys, selection.combine, selection.sell_keys)
    print(f"■ 전략: {selection.describe()}", file=sys.stderr)

    with make_session_factory(bootstrap_settings.database_url)() as session:
        보유중 = {p.symbol for p in session.scalars(select(PositionRow))}

    source, cache = YahooFinanceDataSource(), PriceCache()
    오늘 = datetime.now(KST).date()
    시작 = 오늘 - timedelta(days=WARMUP_DAYS)

    # ── 시세 받기 (섹터별로 묶어 둔다 — 지수를 만들어야 하므로) ──────
    print("■ 시세 받는 중…", file=sys.stderr)
    섹터시세: dict[str, dict[str, object]] = {}
    이름표: dict[str, str] = {}
    못본것: list[str] = []
    for s in 내용.섹터:
        if not s.활성:
            continue
        이름표[s.코드] = s.이름
        모음 = {}
        for m in s.활성종목:
            야후 = f"{m.symbol}.KS" if m.market == "KOSPI" else f"{m.symbol}.KQ"
            try:
                df = cache.fetch(source, m.symbol, 야후, 시작, 오늘, 최소일수=MIN_DAYS)
            except (requests.RequestException, ValueError, KeyError) as e:
                못본것.append(f"{m.name}({m.symbol}): {type(e).__name__}")
                continue
            if df is None or len(df) < MIN_DAYS:
                못본것.append(f"{m.name}({m.symbol}): 시세 {0 if df is None else len(df)}일")
                continue
            모음[m.symbol] = (m, df)
        섹터시세[s.코드] = 모음

    # ── 1차: 섹터 줄 세우기 ──────────────────────────────────────
    코스피 = cache.fetch(source, "^KS11", "^KS11", 시작, 오늘, 최소일수=200)
    시장 = 코스피.set_index("trade_date")["close"].astype(float)
    지수들 = {
        코드: build_index({심볼: df for 심볼, (_, df) in 모음.items()})["close"]
        for 코드, 모음 in 섹터시세.items()
        if len(모음) >= 3
    }
    순위 = pick(rank(지수들, 이름표, 시장, lookback=lookback), top_n=top_sectors)
    print()
    print(format_ranking(순위, lookback=lookback))
    print()

    if 섹터거르기:
        살섹터 = {p.코드 for p in 순위 if p.뽑힘}
        print("  ⚠️ `--sector-filter`로 **1차에서 거르는 중**입니다. 이 기준이 성적을 "
              "올린다는 근거는 없습니다 (docs/섹터선정_검증.md).")
    else:
        살섹터 = set(섹터시세)
        print("  ※ 지금은 **줄만 세우고 거르지 않습니다.** 강도 상위만 사는 것이 "
              "성적을 올린다는 근거가 없어서입니다 (docs/섹터선정_검증.md).")
        print("     대신 한 섹터에서 최대 "
              f"{섹터당 or '제한 없이'}종목까지만 삽니다 — 이건 예측이 아니라 제약입니다.")
    print()

    # ── 2차: 종목 신호 ───────────────────────────────────────────
    신호들 = []
    for 코드, 모음 in 섹터시세.items():
        if 코드 not in 살섹터:
            continue
        for 심볼, (m, df) in 모음.items():
            if 심볼 in 보유중:
                continue
            # **마지막 봉의 신호만** 본다. generate_signals는 히스토리 전체의
            # 신호를 돌려주므로, 거르지 않으면 3년 전 신호로 오늘 산다.
            마지막날 = df["trade_date"].iloc[-1]
            살것 = [
                sig for sig in strategy.generate_signals(심볼, df)
                if sig.trade_date == 마지막날 and sig.signal_type == SignalType.BUY
            ]
            if not 살것:
                continue
            sig = max(살것, key=lambda s: s.score)
            신호들.append((sig.score, 후보(
                symbol=심볼, name=m.name, strategy=sig.strategy_name,
                quantity=0,  # 수량은 매수 단계에서 그때 현금으로 정한다
                price=float(df["close"].iloc[-1]),
                reason=sig.reason, sector=코드, sector_name=이름표.get(코드, 코드),
                사흘등락=사흘등락(df),
            )))

    살펴본수 = sum(
        1 for 코드, 모음 in 섹터시세.items() if 코드 in 살섹터
        for 심볼 in 모음 if 심볼 not in 보유중
    )
    신호들.sort(key=lambda 것: 것[0], reverse=True)
    줄선것 = [c for _, c in 신호들]

    # 한 섹터에 몰리는 것을 막는다.
    if 섹터당:
        줄선것, 밀린것 = cap_per_sector(줄선것, 상한=섹터당)
    else:
        밀린것 = []

    # 동시에 들 수 있는 수보다 많이 제안하면, 사람이 다 체크했을 때 리스크
    # 매니저가 뒤에서 거부한다 — 그러면 "승인했는데 왜 안 샀지"가 된다.
    상한 = args.max or _동시보유(설정, service)
    넘친것 = 줄선것[상한:]
    고른것 = 줄선것[:상한]

    print(f"■ 2차 — 종목 고르기 · 매수 후보 {len(고른것)}종목 (신호 {len(신호들)}개)")
    print()
    for c in 고른것:
        print(f"  {c.name}({c.symbol})  {c.price:>9,.0f}원   [{c.sector_name}] {c.reason}")
    if not 고른것:
        print("  오늘은 매수 신호가 없습니다.")
    print()

    # **왜 안 샀는지가 왜 샀는지만큼 중요하다.**
    if 밀린것:
        print(f"  섹터 상한({섹터당}종목)에 걸려 뺀 것 {len(밀린것)}개")
        for c in 밀린것:
            print(f"    · {c.name}({c.symbol}) [{c.sector_name}]")
    if 넘친것:
        print(f"  동시보유 상한({상한}종목)에 걸려 뺀 것 {len(넘친것)}개")
        for c in 넘친것:
            print(f"    · {c.name}({c.symbol}) [{c.sector_name}]")
    if 못본것:
        print(f"\n  시세를 못 본 종목 {len(못본것)}개 — **이유가 있어야 다음에 무엇을 고칠지 안다**")
        for 줄 in 못본것:
            print(f"    · {줄}")

    if not 설정.승인필요:
        print("\n⚠️ 시트의 require_approval이 꺼져 있습니다 — 승인 없이 사도록 설정돼 있습니다.")

    if args.dry_run:
        print("\n(--dry-run이라 시트·텔레그램에 안 보냈습니다)")
        return 0

    # ── 하루에 한 번만 제안한다 ──────────────────────────────
    # n8n 시계(08:30)와 저장소 예약(08:30)이 둘 다 이걸 부른다. 그냥 두면
    # 같은 후보가 시트에 두 줄씩 쌓이고 텔레그램 알림도 두 번 간다.
    #
    # **먼저 도착한 쪽이 이긴다.** 정시를 지키는 것은 n8n이고, 저장소 cron은
    # 몇십 분씩 늦는다. 늦게 울렸을 때는 n8n이 이미 해 놨으므로 여기서
    # 조용히 물러난다 — 어느 쪽이 먼저든 결과는 같다.
    #
    # 확인 자체가 실패하면 **제안하는 쪽으로 기운다.** 후보가 두 줄 쌓이는
    # 것보다 오늘 후보가 아예 없는 쪽이 나쁘다 — 승인할 것이 없으면 그날은
    # 아무것도 못 산다.
    if not args.force:
        try:
            이미있는것, _ = read_today(sheet_id, 오늘)
        except Exception as 탈:  # noqa: BLE001
            print(f"\n오늘 것이 이미 있는지 못 봤습니다({type(탈).__name__}) — 그냥 제안합니다.")
        else:
            if 이미있는것:
                print(f"\n오늘({오늘}) 후보 {len(이미있는것)}종목이 이미 시트에 있습니다. "
                      "두 번 올리지 않고 물러납니다. 다시 뽑으려면 --force.")
                return 0

    올린수 = append(sheet_id, "승인대기", 승인머리, pending_rows(고른것, 오늘))
    주소 = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    print(f"\n승인대기 탭에 {올린수}줄 올렸습니다 — {주소}")

    # 후보 밑에 **승인 / 거절 버튼**을 붙여 보낸다. 종목코드 여섯 자리를
    # 폰에서 손으로 치는 일은 귀찮고, 귀찮으면 안 하게 되고, 안 하면 승인
    # 스텝이 없는 것과 같다.
    try:
        from muwon.notify.telegram_api import send
        from muwon.notify.telegram_buttons import keyboard

        cfg = service.get_telegram_config()
        if not cfg.bot_token or not cfg.chat_id:
            print("텔레그램 설정이 없어 알림은 건너뜁니다.", file=sys.stderr)
        else:
            # 후보가 없는 날에도 **몇 종목을 무슨 기준으로 봤는지**를 같이
            # 보낸다. "없습니다"만 보내면 제대로 돌아서 0인지 고장 나서
            # 0인지 구별이 안 된다.
            강한섹터 = " · ".join(
                f"{p.이름} {p.상대강도:+.1f}%p"
                for p in 순위[:3] if p.상대강도 is not None
            )
            글 = 알림글(고른것, 오늘, 주소, 살펴본수=살펴본수,
                     전략=selection.describe(), 섹터요약=강한섹터,
                     섹터강도=순위)
            send(cfg.bot_token, cfg.chat_id, 글,
                 reply_markup=keyboard(고른것, 오늘) if 고른것 else None)
            print("텔레그램으로 알렸습니다(버튼 포함).", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — 알림 실패가 후보 목록을 지우면 안 된다
        print(f"텔레그램 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def _동시보유(설정, service) -> int:
    값 = 설정.덮개.get("max_concurrent_positions")
    return int(값) if 값 else service.get_risk_policy().max_concurrent_positions


if __name__ == "__main__":
    raise SystemExit(main())
