"""등록된 전략을 여러 기간에 나눠 검증해 "그때만 잘 맞았던 전략"을 걸러낸다.

한 구간에서 +59%가 나왔다고 좋은 전략이 아니다. 과거 데이터에 우연히
맞아떨어졌을 수 있고(과최적화), 그런 전략은 앞으로는 통하지 않는다. 같은
전략을 연/반기 단위로 각각 돌려 결과가 들쭉날쭉한지 꾸준한지를 본다.

정렬 기준은 평균이 아니라 **최악 구간 수익률**이다. 평균으로 줄을 세우면
"한 구간 대박 + 다른 구간 폭망" 전략이 위로 올라오는데, 실전에서 중요한 건
못 버티는 구간이 있느냐이기 때문이다.

결과는 backtest_runs 테이블에 notes="robustness:<구간>"으로 쌓여 나중에
다시 조회할 수 있다.

사용 예:
    python scripts/run_robustness_check.py --from-year 2021 --to-year 2024
    python scripts/run_robustness_check.py --from-year 2023 --to-year 2024 --half-year
    python scripts/run_robustness_check.py --from-year 2022 --to-year 2024 --keys donchian_20_10,ma_rsi_v1
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.robustness import (
    evaluate_robustness,
    format_robustness_table,
    half_year_windows,
    rank_by_robustness,
    yearly_windows,
)
from muwon.config import bootstrap_settings
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.models import BacktestRunRow
from muwon.db.session import make_session_factory
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import list_definitions


def main() -> None:
    parser = argparse.ArgumentParser(description="전략 다기간 검증(과최적화 탐지)")
    parser.add_argument("--from-year", type=int, required=True)
    parser.add_argument("--to-year", type=int, required=True)
    parser.add_argument("--half-year", action="store_true", help="연 단위 대신 반기 단위로 나눈다")
    parser.add_argument("--keys", type=str, default="", help="쉼표로 구분된 전략 키(비우면 전체)")
    parser.add_argument("--initial-cash", type=float, default=10_000_000.0)
    args = parser.parse_args()

    definitions = list_definitions()
    if args.keys:
        wanted = {k.strip() for k in args.keys.split(",")}
        definitions = [d for d in definitions if d.key in wanted]
        if not definitions:
            raise SystemExit(f"--keys에 해당하는 전략이 없습니다: {args.keys}")

    windows = (
        half_year_windows(args.from_year, args.to_year)
        if args.half_year
        else yearly_windows(args.from_year, args.to_year)
    )

    session_factory = make_session_factory(bootstrap_settings.database_url)
    universe = active_universe(session_factory, list(UNIVERSE))

    # 예열 구간까지 한 번에 받아 두고 구간마다 잘라 쓴다. 구간 수만큼
    # 다시 내려받으면 느리기만 하다.
    data_from = min(w.data_from for w in windows)
    data_to = max(w.end for w in windows)
    print(f"시세 수집: {data_from} ~ {data_to} ({len(universe)}종목)", file=sys.stderr)

    data_source = YahooFinanceDataSource()
    price_histories = {}
    for ticker in universe:
        df = data_source.get_daily_ohlcv(ticker.yahoo_symbol, data_from, data_to)
        if len(df) > 0:
            price_histories[ticker.symbol] = df

    if not price_histories:
        raise SystemExit("시세를 하나도 받지 못했습니다.")

    results = evaluate_robustness(
        definitions,
        price_histories,
        windows,
        RiskManager(policy_provider=lambda: RiskPolicy()),
        initial_cash=args.initial_cash,
    )
    ranked = rank_by_robustness(results)

    print(f"\n=== 다기간 검증 결과 ({args.from_year}~{args.to_year}, {len(windows)}개 구간) ===")
    print("정렬: 최악 구간 수익률 기준 (평균이 아니라 '못 버티는 구간이 있느냐'가 기준)")
    print()
    print(format_robustness_table(ranked, windows))

    _persist(session_factory, ranked, windows)
    print("\n결과는 backtest_runs 테이블에 notes='robustness:<구간>'으로 저장됩니다.")

    best = next((r for r in ranked if r.total_trades > 0), None)
    if best is not None:
        print(
            f"\n최악 구간이 가장 나은 전략: {best.display_name} ({best.key})\n"
            f"  구간별 {', '.join(f'{o.label} {o.return_pct:+.1f}%' for o in best.outcomes)}\n"
            f"  평균 {best.mean_return_pct:+.2f}% · 최악 {best.worst_return_pct:+.2f}% · {best.verdict}"
        )


def _persist(session_factory, results, windows) -> None:
    by_label = {w.label: w for w in windows}
    with session_factory() as session:
        for result in results:
            for outcome in result.outcomes:
                window = by_label[outcome.label]
                session.add(
                    BacktestRunRow(
                        strategy_key=result.key,
                        params_json="{}",
                        period_start=window.trade_from,
                        period_end=window.end,
                        total_return_pct=outcome.return_pct,
                        max_drawdown_pct=outcome.mdd_pct,
                        win_rate_pct=outcome.win_rate_pct,
                        num_trades=outcome.num_trades,
                        notes=f"robustness:{outcome.label}",
                    )
                )
        session.commit()


if __name__ == "__main__":
    main()
