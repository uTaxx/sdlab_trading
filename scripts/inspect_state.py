"""운영 DB에 무엇이 들어 있는지 그대로 찍는다. 읽기 전용.

"자동매매가 정말 돌고 있나"를 확인할 방법이 없었다. 대시보드가 비어 있어도
'아직 안 샀다'인지 '기록이 저장되지 못했다'인지 구분이 안 된다. 둘은 고치는
방법이 전혀 다르다.

아무것도 쓰지 않는다. 구글드라이브에 다시 올리지도 않는다. 확인하려고
실행한 것이 운영 상태를 바꾸면 안 된다.

`--kis`를 붙이면 증권사 계좌까지 조회해 DB 기록과 **대조**한다. 이 프로그램의
현금은 스스로 계산해 온 값이라(매수하면 빼고 매도하면 더한다), 우리를 거치지
않은 주문이 있으면 조용히 어긋난다. 조회만 하지 고치지는 않는다.
"""

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import func, select

from muwon.config import bootstrap_settings
from muwon.db.models import (
    AppSettingRow,
    BacktestRunRow,
    EngineStateRow,
    OrderRow,
    PositionRow,
    RunLogRow,
    SignalRow,
    StrategyChangeRow,
    TradeRow,
    UniverseSnapshotRow,
)
from muwon.db.session import make_session_factory


def _계좌대조(session_factory) -> None:
    """증권사 계좌를 조회해 DB 기록과 대조한다. 읽기만 한다."""
    from muwon.data.kis_client import KISClient
    from muwon.execution import state_repository
    from muwon.execution.reconciliation import reconcile
    from muwon.settings.service import build_settings_service

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        print("\n■ 계좌 대조: KIS 인증정보가 없어 건너뜁니다.")
        return

    # 잔고는 **한 번만** 조회한다. KIS는 토큰 발급을 자주 하면 403으로 막는데,
    # 조회할 때마다 새 클라이언트가 토큰을 받으므로 두 번 부르면 그만큼
    # 한도에 가까워진다. 점검하러 실행한 것이 점검을 막으면 안 된다.
    try:
        잔고 = KISClient.from_settings(service).get_balance()
    except Exception as e:  # noqa: BLE001 (점검 실패가 나머지 출력을 지우면 안 된다)
        print(f"\n■ 계좌 대조는 잔고 조회 실패로 건너뜁니다: {type(e).__name__} {e}")
        return

    보유 = state_repository.load_positions(session_factory)
    현금, _ = state_repository.load_engine_state(session_factory, 10_000_000.0)
    print("\n=== 계좌 대조 (DB 기록 vs 실제 모의투자 계좌) ===")
    for line in reconcile(보유, 현금, 잔고).summary_lines():
        print(line)

    # 어느 필드가 무엇인지는 증권사 응답을 직접 봐야 안다. 예수금 총액
    # (dnca_tot_amt)은 매수 대금이 결제(T+2) 전까지 안 빠져서 오늘 산 것을
    # 못 본다. 그래서 현금은 가수도정산금액을 쓴다. 원본을 같이 찍어 둔다.
    print("\n■ 계좌요약 원본: 예수금 관련 필드 (결제 시점 때문에 서로 다르다)")
    for k, v in sorted(잔고.raw_summary.items()):
        print(f"  {k:<24} {v}")


def main() -> None:
    parser = argparse.ArgumentParser(description="운영 DB 점검 (읽기 전용)")
    parser.add_argument(
        "--kis", action="store_true", help="증권사 계좌까지 조회해 DB 기록과 대조한다"
    )
    args = parser.parse_args()

    path = bootstrap_settings.database_url
    print(f"■ 운영 DB 점검: {path}")
    print(f"  조회 시각 {datetime.now(UTC).isoformat(timespec='seconds')}\n")

    session_factory = make_session_factory(path)
    with session_factory() as session:
        counts = {
            "실행기록(run_logs)": session.scalar(select(func.count()).select_from(RunLogRow)),
            "신호(signals)": session.scalar(select(func.count()).select_from(SignalRow)),
            "주문(orders)": session.scalar(select(func.count()).select_from(OrderRow)),
            "보유(positions)": session.scalar(select(func.count()).select_from(PositionRow)),
            "완결매매(trades)": session.scalar(select(func.count()).select_from(TradeRow)),
            "백테스트(backtest_runs)": session.scalar(
                select(func.count()).select_from(BacktestRunRow)
            ),
            "유니버스 스냅샷": session.scalar(
                select(func.count()).select_from(UniverseSnapshotRow)
            ),
            "설정(app_settings)": session.scalar(
                select(func.count()).select_from(AppSettingRow)
            ),
        }
        for name, value in counts.items():
            print(f"  {name:<24} {value:>6}건")

        print("\n■ 엔진 상태 (회차 사이에 이어지는 값)")
        states = session.scalars(select(EngineStateRow)).all()
        if not states:
            print("  비어 있음. 실거래 엔진이 한 번도 상태를 저장한 적이 없다는 뜻이다.")
        for row in states:
            print(f"  {row.key:<20} {row.value}")

        print("\n■ 최근 실행 10회: 무엇을 보고 무엇을 했나")
        runs = session.scalars(
            select(RunLogRow).order_by(RunLogRow.created_at.desc()).limit(10)
        ).all()
        if not runs:
            print("  없음. 실행 기록을 남기기 전(2026-08-18) 회차이거나, 한 번도 안 돈 것이다.")
        for r in runs:
            when = r.run_date.isoformat() if r.run_date else "시세없음"
            print(
                f"  {r.created_at:%m-%d %H:%M} [{when}] {r.strategy_key:<18} "
                f"대상 {r.universe_size:>3}/판단 {r.checked_symbols:>3} "
                f"신호 매수{r.buy_signals} 매도{r.sell_signals} 주문 {r.orders}"
            )
            if r.rejections:
                for line in r.rejections.splitlines():
                    print(f"      거부: {line}")

        print("\n■ 최근 신호 10건")
        signals = session.scalars(
            select(SignalRow).order_by(SignalRow.created_at.desc()).limit(10)
        ).all()
        if not signals:
            print("  없음")
        for s in signals:
            print(
                f"  {s.trade_date} {s.symbol} {s.signal_type:<4} "
                f"{s.strategy_name:<22} 점수 {s.score:.1f}"
            )

        print("\n■ 최근 주문 10건")
        orders = session.scalars(
            select(OrderRow).order_by(OrderRow.created_at.desc()).limit(10)
        ).all()
        if not orders:
            print("  없음")
        for o in orders:
            ref = f"{o.reference_price:,.0f}" if o.reference_price else "—"
            confirmed = {True: "체결확인", False: "미확인", None: "(옛기록)"}[o.fill_confirmed]
            print(
                f"  {o.created_at:%Y-%m-%d %H:%M} {o.symbol} {o.side:<4} "
                f"{o.quantity}주 체결 {o.price:,.0f} / 기준 {ref} [{confirmed}]"
            )

        print("\n■ 유니버스 스냅샷: 기준별로 몇 줄씩 있나")
        # 실거래는 market_cap 기준만 집어 간다. 거래대금(volume) 스냅샷만
        # 쌓여 있으면 실거래는 여전히 기본 18종목으로 돈다. 실제로 그랬다.
        by_kind = session.execute(
            select(UniverseSnapshotRow.kind, func.count())
            .group_by(UniverseSnapshotRow.kind)
        ).all()
        for kind, count in by_kind:
            label = kind or "(NULL=시총으로 간주)"
            print(f"  {label:<24} {count:>6}줄")
        if not any((k or "market_cap") == "market_cap" for k, _ in by_kind):
            print("  ⚠ 시총 기준 스냅샷이 하나도 없다 → 실거래는 기본 18종목으로 돈다.")

        print("\n■ 최근 유니버스 스냅샷")
        latest = session.scalars(
            select(UniverseSnapshotRow)
            .order_by(UniverseSnapshotRow.snapshot_at.desc())
            .limit(3)
        ).all()
        if not latest:
            print("  없음. 기본 18종목으로 매매하고 있다는 뜻이다.")
        for row in latest:
            print(f"  {row.snapshot_at:%Y-%m-%d %H:%M} kind={row.kind} {row.symbol} {row.name}")

        # **예약해 놓고 확인할 자리가 없었다**(2026-09-05에 붙임).
        #
        # 화면이나 텔레그램에서 전략 변경을 예약하면 다음 거래일 08:20에
        # 반영된다. 그런데 그때까지 예약이 진짜로 들어갔는지 볼 방법이
        # 없었다. 반영이 안 되고 나서야 알게 되는데, 그때는 이미 그날
        # 매매가 옛 전략으로 돈 뒤다.
        print("\n■ 아직 반영 안 된 전략 변경 예약")
        기다리는것 = session.scalars(
            select(StrategyChangeRow)
            .where(StrategyChangeRow.상태.in_(("고름", "확정")))
            .order_by(StrategyChangeRow.만든때.desc())
        ).all()
        if not 기다리는것:
            print("  없음. 다음 08:20에 전략이 안 바뀐다는 뜻이다.")
        for r in 기다리는것:
            경로 = r.승인경로 or "(빈칸=텔레그램으로 본다)"
            print(
                f"  {r.만든때:%m-%d %H:%M}(UTC) [{r.상태}] {r.이전전략 or '(없음)'}"
                f" → {r.새전략} · 제안일 {r.제안일} · 경로 {경로}"
            )
        # **둘 이상이면 다음 날 무엇이 반영되는지 알 수 없다.** 규칙은
        # 하나만 두게 돼 있으므로, 둘이 보이면 그 규칙이 새고 있는 것이다.
        if len(기다리는것) > 1:
            print(f"  ⚠ 기다리는 예약이 {len(기다리는것)}개다. 하나만 있어야 한다.")

        print("\n■ 최근 전략 변경 이력 5건")
        지난것 = session.scalars(
            select(StrategyChangeRow)
            .where(StrategyChangeRow.상태.notin_(("고름", "확정")))
            .order_by(StrategyChangeRow.만든때.desc())
            .limit(5)
        ).all()
        if not 지난것:
            print("  없음.")
        for r in 지난것:
            때 = f"{r.반영때:%m-%d %H:%M}" if r.반영때 else "  -  "
            print(
                f"  {때} [{r.상태}] {r.이전전략 or '(없음)'} → {r.새전략}"
                + (f" · 막힌까닭: {r.막힌까닭}" if r.막힌까닭 else "")
            )

    if args.kis:
        _계좌대조(session_factory)


if __name__ == "__main__":
    main()
