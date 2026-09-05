"""**두 단계로 고른다. 1차 섹터, 2차 종목.** 그리고 아무것도 사지 않는다.

후보를 시트 `승인대기` 탭에 적고 텔레그램으로 알릴 뿐이다. 실제 매수는
사람이 승인 칸에 Y를 적은 뒤에야 일어난다.

## 1차: 섹터

섹터 지수를 만들고 **최근 N일 시장 대비 강도**로 줄을 세운다.

**기본값은 "보여 주기만" 한다.** 강도 상위 섹터만 사는 것은 기본으로
꺼져 있다. 그게 성적을 올린다는 근거가 없기 때문이다
(`docs/섹터선정_검증.md`: 여섯 조합 전부 우연 폭 안, 여덟 해 중 다섯 해
마이너스). 켜려면 `--sector-filter`를 붙인다.

대신 **한 섹터에서 두 종목까지**만 산다. 이건 예측이 아니라 제약이라
검증 결과와 무관하게 유효하다. 45종목을 한 줄로 세워 놓고 신호 난 것을
사면 어떤 날은 반도체 다섯 종목이 한꺼번에 잡히는데, 그건 분산이 아니라
반도체 하나에 다섯 배로 건 것이다.

## 2차: 종목

**새 규칙을 만들지 않는다.** 1차를 통과한 섹터의 종목만 기존 매수 신호에
태운다. 바뀐 것은 대상이지 판단 기준이 아니다. 그래야 성적이 달라졌을 때
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

from muwon.cloud.approval import (
    pending_rows,
    read_today,
    보유종목,
    상한넘긴것,
    승인머리,
    알림글,
    전략변경글,
    후보,
)
from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read
from muwon.cloud.sheet_log import append
from muwon.cloud.strategy_approval import 오늘변경
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.models import PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.domain.types import SignalType
from muwon.market.sector_index import build_index
from muwon.risk.exits import 보유상한
from muwon.sector.selection import (
    cap_per_sector,
    format_ranking,
    pick,
    rank,
)
from muwon.settings.from_sheet import parse_settings
from muwon.settings.service import build_settings_service
from muwon.strategy.portfolio import (
    MarketContext,
    as_portfolio_strategy,
    bars_since,
)
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
    지금 설정된 전략은 거래량이 늘고 값이 오른 날 사는 것이라, 후보가 사흘
    내리 올랐다면 늦게 들어가는 것일 수 있다. 그 판단은 사람이 한다."""
    종가 = df["close"].astype(float)
    if len(종가) < 2:
        return ()
    변동 = (종가.pct_change() * 100).dropna()
    return tuple(round(float(ㄱ), 2) for ㄱ in 변동.tail(3))


def 보유현황(보유중, 섹터시세, 전략, 정책) -> list[보유종목]:
    """들고 있는 종목마다 매도까지 남은 거래일을 센다.

    **엔진과 같은 방법으로 세야 한다.** 엔진은 `bars_since()`로 진입일
    다음 거래일부터 세고, 그 수가 상한에 닿으면 판다. 여기서 달력 일수로
    세면 알림이 말하는 날과 실제 매도일이 어긋난다.

    시세를 못 받은 종목은 남은거래일을 None으로 둔다. 0으로 채우면
    "오늘 판다"로 읽히는데 그것과 "못 셌다"는 다른 말이다."""
    상한 = 보유상한(전략, 정책)
    시세 = {심볼: (m, df) for 모음 in 섹터시세.values() for 심볼, (m, df) in 모음.items()}

    줄들 = []
    for p in sorted(보유중, key=lambda ㄱ: ㄱ.entry_date):
        만난것 = 시세.get(p.symbol)
        이름 = 만난것[0].name if 만난것 else p.symbol
        남은 = None
        if 상한 and 만난것 is not None:
            거래일들 = list(만난것[1]["trade_date"])
            남은 = 상한 - bars_since(거래일들, p.entry_date, max(거래일들))
        줄들.append(보유종목(symbol=p.symbol, name=이름, entry_date=p.entry_date,
                          상한=상한, 남은거래일=남은))
    return 줄들


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true", help="시트·텔레그램에 안 보내고 화면에만")
    parser.add_argument("--force", action="store_true",
                        help="오늘 후보가 이미 시트에 있어도 다시 뽑아 올린다")
    parser.add_argument("--max", type=int, default=0, help="후보를 몇 개까지 (0이면 설정의 동시보유 수)")
    # 아래 넷은 **시트가 원본**이다. 여기 값은 시험해 볼 때만 쓴다.
    # 안 주면 시트에 적힌 것을 쓰고, 시트에도 없으면 기준표의 기본값을 쓴다.
    parser.add_argument("--lookback", type=int, default=0, help="섹터 강도를 며칠로 잴 것인가")
    parser.add_argument("--top-sectors", type=int, default=0, help="1차에서 몇 섹터를 고를 것인가")
    parser.add_argument("--max-per-sector", type=int, default=-1,
                        help="한 섹터에서 몇 종목까지 (0이면 제한 없음)")
    parser.add_argument("--sector-filter", action="store_true",
                        help="강도 상위 섹터만 매수 대상으로 (근거 없음. docs/섹터선정_검증.md)")
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
    print(f"■ 기초설정: {기초설정글(설정, service, 섹터당)}", file=sys.stderr)

    오늘 = datetime.now(KST).date()

    with make_session_factory(bootstrap_settings.database_url)() as session:
        보유중 = list(session.scalars(select(PositionRow)))
        # 08:20 반영이 오늘 무엇을 했는지 여기서 읽어 알림에 한 줄로 넣는다.
        # 반영 쪽은 바꿨을 때와 막혔을 때만 알리므로, 아무 일도 없는 날에
        # 후보가 어느 전략으로 나온 것인지 알 방법이 없었다.
        전략변경 = 전략변경글(오늘변경(session, 오늘))
    보유심볼 = {p.symbol for p in 보유중}
    print(f"■ {전략변경}", file=sys.stderr)

    source, cache = YahooFinanceDataSource(), PriceCache()
    시작 = 오늘 - timedelta(days=WARMUP_DAYS)

    # ── 시세 받기 (섹터별로 묶어 둔다. 지수를 만들어야 하므로) ──────
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

    # ── 들고 있는 종목은 며칠 뒤에 팔리나 ──────────────────────
    #
    # **거래일로 센다.** 실거래 엔진이 `bars_since()`로 그렇게 세기 때문이다
    # (`execution/engine.py`). 달력으로 세면 연휴가 낀 주에 알림과 실제
    # 매도일이 하루씩 어긋나고, 어긋난 줄도 모른다.
    보유알림 = 보유현황(보유중, 섹터시세, strategy, service.get_risk_policy())

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
              f"{섹터당 or '제한 없이'}종목까지만 삽니다. 이건 예측이 아니라 제약입니다.")
    print()

    # ── 2차: 종목 신호 ───────────────────────────────────────────
    #
    # **두 엔진과 같은 길로 신호를 낸다**(2026-09-04에 고침).
    #
    # 전에는 종목마다 `strategy.generate_signals(심볼, df)`를 불렀다. 옛
    # 방식 전략(Strategy)에만 있는 메서드다. 미국 섹터를 보는 전략처럼
    # 여러 종목을 같이 봐야 하는 것(PortfolioStrategy)에는 그 메서드가
    # 없어서, 2026-09-04 08:30 실행이 AttributeError로 통째로 멈췄다.
    # 9월 3일에 그 전략으로 바꾼 뒤 첫 평일 실행이었다.
    #
    # 백테스트와 실거래 엔진은 둘 다 `as_portfolio_strategy`로 감싸서
    # `prepare(시세) → evaluate(오늘)`을 부른다. 여기도 같게 맞춘다.
    # **감싸는 것이 꼭 필요하다.** 미국 섹터 전략은 `prepare()`에서 미국
    # ETF 시세를 받아 오므로, 그 단계를 건너뛰면 신호를 낼 수가 없다.
    쓸시세 = {
        심볼: df
        for 코드, 모음 in 섹터시세.items() if 코드 in 살섹터
        for 심볼, (_, df) in 모음.items()
    }
    종목표 = {
        심볼: (코드, m)
        for 코드, 모음 in 섹터시세.items() if 코드 in 살섹터
        for 심볼, (m, _) in 모음.items()
    }
    마지막날들 = {심볼: df["trade_date"].iloc[-1] for 심볼, df in 쓸시세.items()}

    껍데기 = as_portfolio_strategy(strategy)
    껍데기.prepare(쓸시세)
    # 마지막 봉이 종목마다 다를 수 있다(거래 정지 등). 각 종목의 마지막
    # 날짜로 확인한다. 안 그러면 오래된 신호로 오늘 산다.
    오늘신호 = 껍데기.evaluate(MarketContext(
        as_of=max(마지막날들.values()),
        histories=쓸시세,
        held=frozenset(보유심볼),
    ))

    신호들 = []
    묶음: dict[str, list] = {}
    for sig in 오늘신호:
        if sig.signal_type != SignalType.BUY:
            continue
        if sig.symbol in 보유심볼 or sig.symbol not in 종목표:
            continue
        if sig.trade_date != 마지막날들.get(sig.symbol):
            continue
        묶음.setdefault(sig.symbol, []).append(sig)

    for 심볼, 살것 in 묶음.items():
        코드, m = 종목표[심볼]
        df = 쓸시세[심볼]
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
        for 심볼 in 모음 if 심볼 not in 보유심볼
    )
    신호들.sort(key=lambda 것: 것[0], reverse=True)
    줄선것 = [c for _, c in 신호들]

    # 한 섹터에 몰리는 것을 막는다.
    #
    # **이미 들고 있는 것부터 센다**(2026-09-02에 고침). 전에는 후보 목록을
    # 0부터 셌다. 반도체를 두 종목 들고 있어도 그날 후보에 반도체를 상한만큼
    # 더 넣었고, 다 승인하면 상한을 넘긴 채로 보유하게 됐다. 분산한 줄
    # 알았는데 사실상 한 섹터에 몰아 건 것이다.
    보유섹터센것: dict[str, int] = {}
    for s in 내용.섹터:
        for m in s.종목:
            if m.symbol in 보유심볼:
                보유섹터센것[s.코드] = 보유섹터센것.get(s.코드, 0) + 1
    if 섹터당:
        줄선것, 밀린것 = cap_per_sector(줄선것, 상한=섹터당, 시작=보유섹터센것)
    else:
        밀린것 = []

    # 상한에 걸린 것은 살 수 없지만 **알림에는 빨간 램프로 적는다.** 조용히
    # 빼면 오늘 전략이 무엇을 찾았는지가 안 보인다. 후보가 둘뿐인 날에
    # 신호가 둘밖에 안 난 것인지, 다섯이 났는데 셋이 상한에 걸린 것인지가
    # 갈린다. 뒤쪽이면 자리가 비는 대로 살 것이 있다는 뜻이다.
    #
    # 승인 버튼은 안 붙이고 승인대기 탭에도 안 올린다. 09:05가 같은 상한을
    # 다시 보므로 눌러도 안 사고, 그것이 곧 "승인했는데 왜 안 샀지"다.

    # 동시에 들 수 있는 수보다 많이 제안하면, 사람이 다 체크했을 때 리스크
    # 매니저가 뒤에서 거부한다. 그러면 "승인했는데 왜 안 샀지"가 된다.
    상한 = args.max or _동시보유(설정, service)
    넘친것 = 줄선것[상한:]
    고른것 = 줄선것[:상한]

    # 오늘 후보에 든 같은 섹터 수는 **자를 것을 자른 뒤에** 센다. 자르기
    # 전에 세면 동시보유 상한에 밀려 빠진 것까지 세서 숫자가 부풀려진다.
    남긴섹터센것: dict[str, int] = {}
    for c in 고른것:
        남긴섹터센것[c.sector] = 남긴섹터센것.get(c.sector, 0) + 1
    상한넘긴것들 = [
        상한넘긴것(
            symbol=c.symbol, name=c.name,
            섹터이름=c.sector_name or c.sector,
            보유수=보유섹터센것.get(c.sector, 0),
            오늘후보수=남긴섹터센것.get(c.sector, 0),
            상한=섹터당,
        )
        for c in 밀린것
    ]

    print(f"■ 2차: 종목 고르기 · 매수 후보 {len(고른것)}종목 (신호 {len(신호들)}개)")
    print()
    for c in 고른것:
        print(f"  {c.name}({c.symbol})  {c.price:>9,.0f}원   [{c.sector_name}] {c.reason}")
    if not 고른것:
        print("  오늘은 매수 신호가 없습니다.")
    print()

    # **왜 안 샀는지가 왜 샀는지만큼 중요하다.**
    if 보유섹터센것:
        이름표기 = {s.코드: s.이름 for s in 내용.섹터}
        적힌것 = ", ".join(f"{이름표기.get(ㅋ, ㅋ)} {ㄴ}종목"
                        for ㅋ, ㄴ in sorted(보유섹터센것.items()))
        print(f"  섹터 상한을 셀 때 들고 있는 종목을 같이 셉니다: {적힌것}")
    if 밀린것:
        print(f"  섹터 상한({섹터당}종목)에 걸려 뺀 것 {len(밀린것)}개")
        for c in 밀린것:
            print(f"    · {c.name}({c.symbol}) [{c.sector_name}]")
    if 넘친것:
        print(f"  동시보유 상한({상한}종목)에 걸려 뺀 것 {len(넘친것)}개")
        for c in 넘친것:
            print(f"    · {c.name}({c.symbol}) [{c.sector_name}]")
    if 못본것:
        print(f"\n  시세를 못 본 종목 {len(못본것)}개: **이유가 있어야 다음에 무엇을 고칠지 안다**")
        for 줄 in 못본것:
            print(f"    · {줄}")

    if not 설정.승인필요:
        print("\n⚠️ 시트의 require_approval이 꺼져 있습니다. 승인 없이 사도록 설정돼 있습니다.")

    주소 = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    if args.dry_run:
        # 보낼 글을 그대로 찍는다. 안 찍으면 dry-run으로는 알림이 어떻게
        # 생겼는지 볼 수가 없고, 문구를 고칠 때마다 진짜로 보내 봐야 한다.
        강한섹터 = " · ".join(
            f"{p.이름} {p.상대강도:+.1f}%p"
            for p in 순위[:3] if p.상대강도 is not None
        )
        print("\n(--dry-run이라 시트·텔레그램에 안 보냈습니다)")
        print("\n─── 텔레그램으로 갈 글 ───")
        print(알림글(고른것, 오늘, 주소, 살펴본수=살펴본수,
                   전략=selection.describe(), 섹터요약=강한섹터,
                   섹터강도=순위, 보유=보유알림, 전략변경=전략변경,
                   상한초과=상한넘긴것들))
        print("─── 여기까지 ───")
        return 0

    # ── 하루에 한 번만 제안한다 ──────────────────────────────
    # n8n 시계(08:30)와 저장소 예약(08:30)이 둘 다 이걸 부른다. 그냥 두면
    # 같은 후보가 시트에 두 줄씩 쌓이고 텔레그램 알림도 두 번 간다.
    #
    # **먼저 도착한 쪽이 이긴다.** 정시를 지키는 것은 n8n이고, 저장소 cron은
    # 몇십 분씩 늦는다. 늦게 울렸을 때는 n8n이 이미 해 놨으므로 여기서
    # 조용히 물러난다. 어느 쪽이 먼저든 결과는 같다.
    #
    # 확인 자체가 실패하면 **제안하는 쪽으로 기운다.** 후보가 두 줄 쌓이는
    # 것보다 오늘 후보가 아예 없는 쪽이 나쁘다. 승인할 것이 없으면 그날은
    # 아무것도 못 산다.
    if not args.force:
        try:
            이미있는것, _ = read_today(sheet_id, 오늘)
        except Exception as 탈:  # noqa: BLE001
            print(f"\n오늘 것이 이미 있는지 못 봤습니다({type(탈).__name__}): 그냥 제안합니다.")
        else:
            if 이미있는것:
                print(f"\n오늘({오늘}) 후보 {len(이미있는것)}종목이 이미 시트에 있습니다. "
                      "두 번 올리지 않고 물러납니다. 다시 뽑으려면 --force.")
                return 0

    올린수 = append(sheet_id, "승인대기", 승인머리, pending_rows(고른것, 오늘))
    print(f"\n승인대기 탭에 {올린수}줄 올렸습니다. {주소}")

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
                     섹터강도=순위, 보유=보유알림, 전략변경=전략변경,
                     상한초과=상한넘긴것들)
            send(cfg.bot_token, cfg.chat_id, 글,
                 reply_markup=keyboard(고른것, 오늘) if 고른것 else None)
            print("텔레그램으로 알렸습니다(버튼 포함).", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 (알림 실패가 후보 목록을 지우면 안 된다)
        print(f"텔레그램 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)

    섹터트렌드남기기(sheet_id, 섹터시세, 이름표, 시장, 오늘)
    return 0


def 섹터트렌드남기기(sheet_id: str, 섹터시세, 이름표, 시장, 오늘) -> None:
    """화면 '시장 트렌드' 탭이 읽을 줄을 시트에 남긴다.

    **후보를 다 올리고 알림까지 보낸 뒤에 한다.** 이 실행의 본래 일은 매수
    후보를 내는 것이고, 보여 주기용 표 때문에 그것이 늦거나 실패하면 안 된다.
    그래서 실패해도 여기서 삼키고 왜 못 했는지만 찍는다.

    섹터 시세는 위에서 이미 받아 두었다. 다시 받지 않는다."""
    try:
        from muwon.analysis.sector_trend import 머리 as 섹터머리
        from muwon.analysis.sector_trend import 요약글, 재기, 줄들만들기

        움직임들 = 재기(섹터시세, 이름표, 시장)
        올린수 = append(sheet_id, "섹터트렌드", 섹터머리, 줄들만들기(움직임들, 오늘))
        print(f"섹터트렌드 탭에 {올린수}줄 올렸습니다.", file=sys.stderr)
        print(f"  {요약글(움직임들)}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001 (보여 주기용 표가 매수 후보를 막으면 안 된다)
        print(f"섹터 트렌드는 못 남겼습니다: {type(e).__name__}: {e}", file=sys.stderr)


def 기초설정글(설정, service, 섹터당: int) -> str:
    """오늘 후보가 어느 조건에서 나온 것인지 한 줄로.

    **조건 없는 숫자는 나중에 검증할 수 없다.** 후보가 왜 셋뿐인지 물으면
    답이 여기에 있다. 자리가 여섯인지 여덟인지, 섹터당 둘인지 셋인지가
    후보 수를 그대로 바꾼다.

    로그로만 나간다. 사람에게 가는 알림에는 안 넣는다. 매일 같은 값이
    나가면 읽히지 않고, 그러면 정작 값이 바뀐 날에도 안 읽힌다.

    2026-09-01에 이 줄이 없어서 지금 걸린 값을 확인할 방법이 화면(n8n
    연결이 필요하다) 말고는 없었다. 같은 날 워크플로가 시트의 섹터당
    상한을 통째로 무시하고 있던 것도 이 줄이 있었으면 한 번에 보였다."""
    정책 = service.get_risk_policy()
    익절 = f"{정책.take_profit_pct * 100:.0f}%" if 정책.take_profit_pct else "끔"
    보유 = f"{정책.max_holding_days}일" if 정책.max_holding_days else "전략이 정한 대로"
    섹터말 = f"{섹터당}종목" if 섹터당 else "제한 없음"
    return (
        f"비중 {정책.max_position_weight * 100:.0f}% · "
        f"동시보유 {_동시보유(설정, service)}종목 · "
        f"섹터당 {섹터말} · "
        f"손절 {정책.stop_loss_pct * 100:.0f}% · 익절 {익절} · 보유 {보유} · "
        f"하루손실 {정책.daily_loss_limit_pct * 100:.0f}%"
    )


def _동시보유(설정, service) -> int:
    값 = 설정.덮개.get("max_concurrent_positions")
    return int(값) if 값 else service.get_risk_policy().max_concurrent_positions


if __name__ == "__main__":
    raise SystemExit(main())
