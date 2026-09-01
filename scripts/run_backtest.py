"""규칙기반 전략(MovingAverageRsiStrategy)을 유니버스 전체에 대해 백테스트한다.

시세는 YahooFinanceDataSource(개발·백테스트 전용, KIS와 무관)에서 가져온다.
이 세션의 네트워크 정책이 query1.finance.yahoo.com을 막고 있다면 여기서
바로 실패한다. docs/kis_api_setup.md 안내 이전에, 환경 설정에서 네트워크
정책부터 확인할 것.

사용 예:
    python scripts/run_backtest.py --start 2023-01-01 --end 2024-12-31
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.backtest.engine import BacktestEngine
from muwon.data.universe import UNIVERSE
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.rule_based import MovingAverageRsiStrategy


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007 (날짜만 필요, tz 무관)
def main() -> None:
    parser = argparse.ArgumentParser(description="규칙기반 전략 백테스트")
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    args = parser.parse_args()

    data_source = YahooFinanceDataSource()
    price_histories = {}
    for ticker in UNIVERSE:
        print(f"시세 수집 중: {ticker.name} ({ticker.symbol})...", file=sys.stderr)
        df = data_source.get_daily_ohlcv(ticker.yahoo_symbol, args.start, args.end)
        if len(df) > 0:
            price_histories[ticker.symbol] = df

    policy_provider = lambda: RiskPolicy()
    risk_manager = RiskManager(policy_provider=policy_provider)
    engine = BacktestEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=risk_manager,
        initial_cash=args.initial_cash,
    )

    result = engine.run(price_histories)

    print(f"\n=== 백테스트 결과 ({args.start} ~ {args.end}) ===")
    print(f"초기자본: {args.initial_cash:,.0f}원")
    print(f"최종자산: {result.final_equity:,.0f}원")
    print(f"총수익률: {result.total_return_pct:+.2f}%")
    print(f"최대낙폭(MDD): {result.max_drawdown_pct:.2f}%")
    print(f"거래횟수: {result.num_trades}건")
    print(f"승률: {result.win_rate_pct:.1f}%")


if __name__ == "__main__":
    main()
