"""매매 결과·설정·검증 결과를 한 덩어리로 모아 텔레그램으로 보낸다.

**받은 내용을 그대로 복사해 Claude에게 붙여넣으면 전략 진단을 받을 수 있다.**
그게 이 리포트의 용도라서, 숫자만 나열하지 않고 판단에 필요한 맥락(어떤
전략을 어떤 리스크 설정으로 실행했는지, 청산 사유가 무엇이었는지, 다기간
검증에서 어땠는지)까지 함께 담는다.

LLM을 코드에서 직접 호출하지 않는 이유: 키 관리·비용·장애 요소가 늘어나는
데 비해, 전략 변경 판단은 어차피 사람이 최종 확인해야 하는 영역이라 얻는
게 적다. 사람이 붙여넣는 한 단계가 그 확인을 자연스럽게 만든다.

사용 예:
    python scripts/run_analysis_report.py                # 최근 30일, 콘솔+텔레그램
    python scripts/run_analysis_report.py --days 90
    python scripts/run_analysis_report.py --no-telegram  # 콘솔로만 확인
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.report import build_analysis_report
from muwon.config import bootstrap_settings
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import active_universe
from muwon.db.session import make_session_factory
from muwon.notify.telegram import TelegramNotifier
from muwon.settings.service import build_settings_service


def main() -> None:
    parser = argparse.ArgumentParser(description="Claude에 붙여넣을 분석 리포트 생성")
    parser.add_argument("--days", type=int, default=30, help="집계할 기간 (기본 30일)")
    parser.add_argument("--recent", type=int, default=15, help="개별로 보여줄 최근 매매 건수")
    parser.add_argument("--no-telegram", action="store_true", help="콘솔로만 출력")
    args = parser.parse_args()

    settings_service = build_settings_service()
    session_factory = make_session_factory(bootstrap_settings.database_url)

    universe = active_universe(session_factory, list(UNIVERSE))
    report = build_analysis_report(
        session_factory,
        strategy_key=settings_service.get_strategy_selection().active_key,
        policy=settings_service.get_risk_policy(),
        universe_size=len(universe),
        days=args.days,
        recent_trade_limit=args.recent,
    )

    print(report)

    if args.no_telegram:
        return

    chunks = TelegramNotifier(settings_service).send_long(report)
    print(f"\n(텔레그램으로 {chunks}개 메시지 발송)", file=sys.stderr)


if __name__ == "__main__":
    main()
