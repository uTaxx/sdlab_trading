"""하루 매매가 끝난 뒤 "그날 다른 전략이었으면 수익률이 어땠을까"를 자동으로
계산해 텔레그램으로 알려주는 스크립트.

run_hypothesis_sweep.py(사람이 손으로, 임의 기간에 대해 실행하는 스윕)와 달리
이건 매일 자동 실행을 전제로 한다. 기준일(오늘)에서 --lookback-days만큼
거슬러 올라간 최근 구간에 대해 등록된 전략 전체를 재평가하고, 지금 실거래
중인 전략과 비교한다. GitHub Actions 배치(run_paper_trading.py) 직후에
매일 실행하면, "이 전략이 계속 최선인가"를 사람이 매번 손으로 스윕을 실행하지
않아도 자동으로 추적하게 된다. 점점 더 나은 전략으로 수렴해가는 흐름을
만드는 축이다.

결과는 backtest_runs 테이블에 notes="daily_review"로 쌓여서, 수동 스윕
기록과 구분해서 추세를 조회할 수 있다.

사용 예:
    python scripts/run_daily_review.py
    python scripts/run_daily_review.py --lookback-days 40
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import argparse

from muwon.analysis.strategy_review import (
    build_review_report,
    format_review_message,
    persist_sweep_results,
    sweep_strategies,
)
from muwon.config import bootstrap_settings
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.manager import RiskManager
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import list_definitions


def main() -> None:
    parser = argparse.ArgumentParser(description="등록된 전략 가설 대비 오늘까지의 실전 전략 리뷰")
    parser.add_argument(
        "--lookback-days", type=int, default=90, help="기준일에서 며칠 전부터 비교할지 (기본 90일)"
    )
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    args = parser.parse_args()

    end = date.today()  # noqa: DTZ011 (날짜만 필요, 배치 실행 전제라 tz 무관)
    start = end - timedelta(days=args.lookback_days)

    settings_service = build_settings_service()
    live_key = settings_service.get_strategy_selection().active_key

    session_factory = make_session_factory(bootstrap_settings.database_url)
    # 리뷰 대상은 실제 매매 대상과 같아야 비교가 의미 있다
    universe = active_universe(session_factory, list(UNIVERSE))

    data_source = YahooFinanceDataSource()
    price_histories = {}
    for ticker in universe:
        df = data_source.get_daily_ohlcv(ticker.yahoo_symbol, start, end)
        if len(df) > 0:
            price_histories[ticker.symbol] = df

    risk_manager = RiskManager(policy_provider=settings_service.get_risk_policy)
    results = sweep_strategies(list_definitions(), price_histories, risk_manager, args.initial_cash)

    persist_sweep_results(session_factory, results, start, end, notes="daily_review")

    report = build_review_report(results, live_key, start, end)
    message = format_review_message(report)
    print(message)

    TelegramNotifier(settings_service).send(message)


if __name__ == "__main__":
    main()
