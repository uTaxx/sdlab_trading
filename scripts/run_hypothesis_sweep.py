"""strategy/registry.py에 등록된 가설들을 같은 과거 데이터·같은 기간에
대해 일괄 백테스트하고, 결과를 DB(backtest_runs 테이블)에 누적 저장한다.

"가설을 검증하고 진화시킨다"의 2단계(과거 데이터 검증)를 담당한다. 콘솔에
찍고 끝나는 1회성 스크립트가 아니라 결과가 쌓이므로, 파라미터를 바꿔가며
여러 번 실행해도 시간에 따른 비교가 가능하다. 결과가 마음에 들면
`python scripts/configure.py strategy --active-key <키>`로 실거래에
반영한다(3단계: 코드 배포 없이 설정값 하나로 전환).

시세는 YahooFinanceDataSource(개발·백테스트 전용)에서 가져온다.

사용 예:
    python scripts/run_hypothesis_sweep.py --start 2023-01-01 --end 2024-12-31
    python scripts/run_hypothesis_sweep.py --start 2023-01-01 --end 2024-12-31 --keys ma_rsi_v1,ma_rsi_fast5_20
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.strategy_review import persist_sweep_results, sweep_strategies
from muwon.config import bootstrap_settings
from muwon.data.universe import UNIVERSE
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import list_definitions


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007 (날짜만 필요, tz 무관)
def main() -> None:
    parser = argparse.ArgumentParser(description="등록된 전략 가설 일괄 백테스트")
    parser.add_argument("--start", type=parse_date, required=True)
    parser.add_argument("--end", type=parse_date, required=True)
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    parser.add_argument(
        "--keys", type=str, default="", help="쉼표로 구분된 전략 키. 비우면 등록된 전체를 돈다."
    )
    args = parser.parse_args()

    definitions = list_definitions()
    if args.keys:
        wanted = {k.strip() for k in args.keys.split(",")}
        definitions = [d for d in definitions if d.key in wanted]
        if not definitions:
            raise SystemExit(f"--keys에 해당하는 등록된 전략이 없습니다: {args.keys}")

    data_source = YahooFinanceDataSource()
    price_histories = {}
    for ticker in UNIVERSE:
        print(f"시세 수집 중: {ticker.name} ({ticker.symbol})...", file=sys.stderr)
        df = data_source.get_daily_ohlcv(ticker.yahoo_symbol, args.start, args.end)
        if len(df) > 0:
            price_histories[ticker.symbol] = df

    session_factory = make_session_factory(bootstrap_settings.database_url)
    risk_manager = RiskManager(policy_provider=lambda: RiskPolicy())

    results = sweep_strategies(definitions, price_histories, risk_manager, args.initial_cash)
    persist_sweep_results(session_factory, results, args.start, args.end, notes="manual_sweep")

    rows = sorted(results, key=lambda r: r.return_pct, reverse=True)

    print(f"\n=== 가설 스윕 결과 ({args.start} ~ {args.end}) ===")
    header = f"{'전략키':<20}{'수익률':>10}{'MDD':>10}{'승률':>8}{'거래수':>8}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r.key:<20}{r.return_pct:>+9.2f}%{r.mdd_pct:>9.2f}%{r.win_rate_pct:>7.1f}%{r.num_trades:>8}")
    print("\n결과는 backtest_runs 테이블에 누적 저장됩니다.")
    print("실거래에 반영하려면: python scripts/configure.py strategy --active-key <전략키>")


if __name__ == "__main__":
    main()
