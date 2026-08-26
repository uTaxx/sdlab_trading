"""장중 실시간(체결 틱 단위) 매매를 시작하는 스크립트.

장이 열려 있는 동안(09:00~15:30 KST) 계속 떠 있어야 하는 프로세스라
VPS 등 상시 서버에서 실행한다. 하루 1회 배치(scripts/run_paper_trading.py,
GitHub Actions)와는 완전히 다른 운영 모드다 — 같은 계좌에 두 개를 동시에
돌리지 말 것.

KIS 웹소켓은 이 프로젝트를 개발한 환경(비표준 포트 차단)에서 실제 접속
검증을 못 했다 — 처음 배포할 때는 로그를 보면서 실제로 틱이 들어오는지,
메시지 파싱이 맞는지 확인할 것 (src/muwon/data/kis_websocket.py 참고).

사용 예:
    python scripts/run_realtime_trading.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from loguru import logger

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.data.kis_websocket import KISWebSocketClient, get_approval_key
from muwon.data.universe import UNIVERSE
from muwon.db.session import make_session_factory
from muwon.execution.kis_order_executor import KISOrderExecutor
from muwon.execution.realtime_engine import RealtimeTradingEngine
from muwon.execution.realtime_runner import run_forever
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.manager import RiskManager
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import build_strategies


async def main() -> None:
    settings_service = build_settings_service()
    creds = settings_service.get_kis_credentials()
    if creds.is_real:
        raise SystemExit(
            "kis.env가 'real'입니다. 모의투자로 충분히 검증하기 전엔 이 스크립트를 "
            "real로 돌리지 마세요."
        )
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("KIS 인증정보가 없습니다. python scripts/configure.py kis ...로 먼저 설정하세요.")

    client = KISClient.from_settings(settings_service)

    session_factory = make_session_factory(bootstrap_settings.database_url)
    selection = settings_service.get_strategy_selection()
    logger.info(f"활성 전략: {selection.describe()}")

    engine = RealtimeTradingEngine(
        strategy=build_strategies(selection.active_keys, selection.combine, selection.sell_keys),
        risk_manager=RiskManager(policy_provider=settings_service.get_risk_policy),
        order_executor=KISOrderExecutor(client),
        notifier=TelegramNotifier(settings_service),
        session_factory=session_factory,
        universe=UNIVERSE,
    )
    engine.start()

    symbols = [t.symbol for t in UNIVERSE]
    logger.info(f"실시간 매매 시작 — {len(symbols)}종목 구독")

    def make_stream():
        # 재연결마다 approval_key도 새로 받는다 — 오래 붙들고 있으면 만료될 수 있어서다.
        approval_key = get_approval_key(creds.app_key, creds.app_secret, is_paper=True)
        return KISWebSocketClient(approval_key, is_paper=True).stream_ticks(symbols)

    await run_forever(engine, make_stream)


if __name__ == "__main__":
    asyncio.run(main())
