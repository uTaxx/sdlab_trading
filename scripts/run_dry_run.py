"""KIS 없이 매매 파이프라인(신호→리스크→체결→텔레그램 알림→DB 기록) 전체를
검증하는 드라이런 스크립트.

KIS 모의투자가 아니다. 시세는 YahooFinanceDataSource(개발 전용)에서 받고,
주문은 SimulatedOrderExecutor가 KIS 서버 없이 로컬에서 체결됐다고 가정한다.
이 환경의 네트워크 정책이 KIS 포트(9443/29443)를 막고 있어 실제 KIS
모의투자(scripts/run_paper_trading.py)를 이 환경에서는 검증할 수 없기
때문에 만든 대체 경로다. KIS 접근이 되는 환경으로 옮기면 run_paper_trading.py를
쓸 것.

매일 장 마감 후 1회 실행하는 걸 전제로 한다 (cron/스케줄러로 등록).

사용 예:
    python scripts/run_dry_run.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.config import bootstrap_settings
from muwon.data.universe import UNIVERSE
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.execution.engine import TradingEngine
from muwon.execution.simulated_executor import SimulatedOrderExecutor
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.manager import RiskManager
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import build_strategies


def main() -> None:
    settings_service = build_settings_service()
    session_factory = make_session_factory(bootstrap_settings.database_url)
    selection = settings_service.get_strategy_selection()
    print(f"활성 전략: {selection.describe()}", file=sys.stderr)

    engine = TradingEngine(
        strategy=build_strategies(selection.active_keys, selection.combine, selection.sell_keys),
        risk_manager=RiskManager(policy_provider=settings_service.get_risk_policy),
        data_source=YahooFinanceDataSource(),
        order_executor=SimulatedOrderExecutor(),
        notifier=TelegramNotifier(settings_service),
        session_factory=session_factory,
        universe=UNIVERSE,
        source_symbol=lambda ticker: ticker.yahoo_symbol,
    )

    summary = engine.run_once()

    print(f"\n=== 드라이런 결과 ({summary.run_date}) ===")
    print(f"점검 종목 수: {summary.checked_symbols}")
    if not summary.actions:
        print("체결 없음")
    for action in summary.actions:
        side = "매수" if action.side.value == "buy" else "매도"
        print(f"{side}: {action.name}({action.symbol}) {action.quantity}주 @ {action.price:,.0f}원: {action.reason}")
    if summary.rejections:
        print("\n리스크 매니저 거부:")
        for rejection in summary.rejections:
            print(f"  - {rejection}")


if __name__ == "__main__":
    main()
