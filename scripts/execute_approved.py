"""**시트에서 승인된 것만 산다.** 승인 스텝의 마지막 칸이다.

    08:30  매수 후보 제안  →  텔레그램 버튼  →  시트에 Y
    09:05  여기 — Y가 적힌 것만 매수

## 왜 따로 만드나

기존 `run_paper_trading.py`는 신호가 나면 그냥 산다. 승인 기록을 아예 안
읽는다. 그걸 고치는 대신 따로 둔 이유는, **둘 중 무엇으로 돌았는지가
기록에 남아야** 하기 때문이다. 나중에 "이 매매는 내가 승인한 건가"를
되짚을 수 없으면 승인 스텝을 둔 의미가 없다.

## 매도는 승인과 무관하다

**승인은 사는 것에만 걸린다.** 들고 있는 종목의 손절·청산은 승인 여부와
관계없이 늘 작동한다. 그래서 유니버스를 이렇게 잡는다.

    오늘 승인된 종목  ∪  지금 들고 있는 종목

보유 종목을 안 넣으면 엔진이 그 종목을 아예 안 보고, **손절이 조용히
멈춘다.** 이게 이 스크립트에서 제일 위험한 실수다.

## 아무것도 안 하면 아무것도 안 산다

승인 칸이 전부 비어 있으면 살 것이 없다. 그래도 **매도 점검은 돈다.**

사용 예:
    python scripts/execute_approved.py --dry-run   # KIS 없이, 주문 흉내만
    python scripts/execute_approved.py             # 모의계좌에 실제 주문
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from muwon.cloud.approval import read_today
from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read
from muwon.config import bootstrap_settings
from muwon.db.models import PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.execution.approved_universe import build_universe
from muwon.execution.engine import TradingEngine
from muwon.execution.reconciliation import check_account_consistency
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.manager import RiskManager
from muwon.settings.from_sheet import build_policy_provider, parse_settings
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import build_strategies

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--dry-run", action="store_true",
                        help="KIS에 붙지 않고 야후 시세로 주문 흉내만 낸다")
    args = parser.parse_args()

    sheet_id = args.sheet_id
    if not sheet_id:
        if not args.folder_id:
            raise SystemExit("MUWON_SHEET_ID도 GDRIVE_FOLDER_ID도 없습니다.")
        sheet_id, _ = find_or_create(args.folder_id, DEFAULT_TITLE)

    내용 = read(sheet_id)
    설정 = parse_settings(내용.설정)
    오늘 = datetime.now(KST).date()

    ensure_schema(bootstrap_settings.database_url)
    service = build_settings_service()
    session_factory = make_session_factory(bootstrap_settings.database_url)

    # ── 킬스위치가 먼저다 ────────────────────────────────────────
    #
    # 시트와 DB가 **둘 다** 켜져야 켜진다. 여기서 막히면 아무것도 안 산다.
    정책제공, 설정설명, _ = build_policy_provider(service, sheet_id)
    print(설정설명)
    print()
    정책 = 정책제공()

    # ── 오늘 승인된 것 ───────────────────────────────────────────
    후보, 결정 = read_today(sheet_id, 오늘)
    승인된것 = [c for c in 후보 if 결정.get(c.symbol) == "Y"]
    거절한것 = [c for c in 후보 if 결정.get(c.symbol) == "N"]
    무응답 = [c for c in 후보 if c.symbol not in 결정]

    print(f"■ {오늘} 승인 현황 — 후보 {len(후보)}종목")
    print(f"  ✅ 승인   {len(승인된것)}종목" + (f" — {', '.join(c.name for c in 승인된것)}" if 승인된것 else ""))
    print(f"  ❌ 거절   {len(거절한것)}종목" + (f" — {', '.join(c.name for c in 거절한것)}" if 거절한것 else ""))
    print(f"  ⬜ 무응답 {len(무응답)}종목" + (f" — {', '.join(c.name for c in 무응답)}" if 무응답 else ""))
    print()

    if not 설정.승인필요:
        print("⚠️ 시트의 require_approval이 꺼져 있습니다. 그래도 **이 스크립트는")
        print("   승인된 것만 삽니다** — 승인 없이 사려면 run_paper_trading.py를 쓰세요.")
        print()

    with session_factory() as session:
        보유 = {p.symbol for p in session.scalars(select(PositionRow))}

    notifier = TelegramNotifier(service)

    # ── 사기 전에 실제 계좌와 대조한다 ───────────────────────────
    #
    # 이 프로그램의 현금은 스스로 계산해 온 값이다 — 매수하면 빼고 매도하면
    # 더한다. 우리를 거치지 않은 주문(손매매·검증 스크립트)이나 부분 체결이
    # 있으면 그 계산이 조용히 어긋난다. 비중 상한·일일 손실한도가 전부 이
    # 현금값 위에서 도니까, 틀어진 걸 모르고 매매하는 게 제일 나쁘다.
    #
    # **살 것이 없는 날에도 돌아야 한다.** 어긋남은 우리가 안 산 날에 생기고,
    # 그런 날 건너뛰면 영영 못 본다. 그래서 유니버스 판정보다 앞에 둔다.
    #
    # 고치지는 않는다. 원인이 부분 체결일 수도, 손매매일 수도, 우리 버그일
    # 수도 있어서 자동으로 덮어쓰면 그것대로 사고다. 알리고 사람이 정한다.
    client = None
    if not args.dry_run:
        from muwon.data.kis_client import KISClient

        creds = service.get_kis_credentials()
        if creds.is_real:
            raise SystemExit(
                "kis.env가 'real'입니다. 이 스크립트는 모의투자 전용이니 "
                "python scripts/configure.py kis --env paper ...로 먼저 되돌리세요."
            )
        if not creds.app_key or not creds.app_secret or not creds.account_no:
            raise SystemExit("KIS 인증정보가 없습니다.")
        client = KISClient.from_settings(service)

        보고 = check_account_consistency(client, session_factory)
        if 보고 is not None and not 보고.is_consistent:
            try:
                notifier.send("🔍 계좌 대조 — 어긋남\n" + "\n".join(보고.summary_lines()))
            except Exception as e:  # noqa: BLE001 — 알림 실패가 매매를 막으면 안 된다
                print(f"대조 알림 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)
        print()

    # ── 유니버스 = 승인된 것 ∪ 보유 중 ──────────────────────────
    #
    # 보유 종목을 빼면 엔진이 그 종목을 아예 안 보고 **손절이 조용히 멈춘다.**
    universe, 가정한것 = build_universe(
        [c.symbol for c in 승인된것], 보유, 내용.섹터,
        {c.symbol: c.name for c in 후보},
    )

    print(f"■ 살펴볼 종목 {len(universe)}개 — 승인 {len(승인된것)} + 보유 {len(보유)}")
    print("  (매도 판단은 승인과 무관하게 보유 종목 전부에 걸립니다)")
    if 가정한것:
        # 코스닥인데 코스피로 가정하면 시세가 통째로 비고, 그 종목은
        # 조용히 빠진다. 보유 종목이었다면 손절이 안 걸린다.
        print(f"  ⚠️ 목록에 없어 **코스피로 가정한 종목** {len(가정한것)}개: {', '.join(가정한것)}")
    print()

    if not universe:
        print("승인된 것도 보유 중인 것도 없습니다. **아무것도 하지 않습니다.**")
        return 0

    selection = service.get_strategy_selection()
    strategy = build_strategies(selection.active_keys, selection.combine)

    if args.dry_run:
        from muwon.data.yahoo_client import YahooFinanceDataSource
        from muwon.execution.simulated_executor import SimulatedOrderExecutor

        data_source, executor = YahooFinanceDataSource(), SimulatedOrderExecutor()
        source_symbol = lambda t: t.yahoo_symbol
        print("■ --dry-run — 야후 시세로 주문 흉내만 냅니다. KIS에 안 붙습니다.")
    else:
        from muwon.execution.kis_order_executor import KISOrderExecutor

        data_source, executor = client, KISOrderExecutor(client)
        source_symbol = lambda t: t.symbol

    engine = TradingEngine(
        strategy=strategy,
        risk_manager=RiskManager(policy_provider=정책제공),
        data_source=data_source,
        order_executor=executor,
        notifier=notifier,
        session_factory=session_factory,
        universe=universe,
        source_symbol=source_symbol,
    )
    summary = engine.run_once()

    print(f"\n=== 승인 매매 결과 ({summary.run_date}) ===")
    print(f"점검 종목 수: {summary.checked_symbols}")
    if not summary.actions:
        print("체결 없음")
    산것, 판것 = [], []
    for a in summary.actions:
        쪽 = "매수" if a.side.value == "buy" else "매도"
        (산것 if a.side.value == "buy" else 판것).append(a)
        print(f"{쪽}: {a.name}({a.symbol}) {a.quantity}주 @ {a.price:,.0f}원 — {a.reason}")
    if summary.rejections:
        print("\n리스크 매니저 거부:")
        for r in summary.rejections:
            print(f"  - {r}")

    # 승인했는데 안 샀으면 그 이유를 알려 줘야 한다 — 안 그러면
    # "승인했는데 왜 안 샀지"가 남는다.
    산심볼 = {a.symbol for a in 산것}
    안산것 = [c for c in 승인된것 if c.symbol not in 산심볼]
    if 안산것:
        print(f"\n⚠️ 승인했지만 안 산 것 {len(안산것)}종목: "
              f"{', '.join(c.name for c in 안산것)}")
        print("   (킬스위치·현금 부족·비중 상한·신호 소멸 중 하나입니다. 위 거부 목록을 보세요)")

    if not 정책.trading_enabled:
        print("\n🛑 **킬스위치가 꺼져 있어 신규 매수는 전부 거부됩니다.**")
        print("   시트와 대시보드가 둘 다 켜져야 켜집니다.")

    try:
        notifier.send(_알림글(오늘, 승인된것, 산것, 판것, 안산것, 정책.trading_enabled))
    except Exception as e:  # noqa: BLE001 — 알림 실패가 매매 결과를 지우면 안 된다
        print(f"\n텔레그램 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


def _알림글(날짜, 승인된것, 산것, 판것, 안산것, 켜짐) -> str:
    줄 = [f"🧾 {날짜} 승인 매매 결과", ""]
    if not 켜짐:
        줄.append("🛑 킬스위치가 꺼져 있어 신규 매수는 없었습니다.")
        줄.append("")
    줄.append(f"승인 {len(승인된것)}종목 → 매수 {len(산것)}건 · 매도 {len(판것)}건")
    for a in 산것:
        줄.append(f"  🟢 {a.name} {a.quantity}주 @ {a.price:,.0f}원")
    for a in 판것:
        줄.append(f"  🔴 {a.name} {a.quantity}주 @ {a.price:,.0f}원 — {a.reason}")
    if 안산것:
        줄 += ["", f"⚠️ 승인했지만 안 산 것: {', '.join(c.name for c in 안산것)}"]
    return "\n".join(줄)


if __name__ == "__main__":
    raise SystemExit(main())
