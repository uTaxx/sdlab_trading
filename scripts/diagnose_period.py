"""특정 구간에서 전략이 왜 그런 성적을 냈는지 뜯어보는 진단 도구.

"2022년에 -23.7%였다"만으로는 고칠 수가 없다. 시장 국면이 언제 바뀌었는지,
그때 진입이 막혔는지, 손실이 어느 국면에서 산 종목에서 났는지를 나눠 봐야
"국면 필터가 일을 안 했다"인지 "국면 필터가 진입은 막았지만 이미 산 걸
못 지켰다"인지 구분된다. 그 둘은 고치는 방법이 정반대다.

인수인계서 28항(Factor Contribution)·34항(판단 근거 로그)이 요구하는 분석의
첫 단계이기도 하다.

사용 예:
    python scripts/diagnose_period.py --strategy factor_score_v1 --year 2022
    python scripts/diagnose_period.py --strategy volume_surge_5d --year 2022
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.experiment import WARMUP_DAYS
from muwon.analysis.market_data import load_histories
from muwon.backtest.engine import BacktestEngine
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.factors.cross_sectional import MarketRegimeFactor
from muwon.risk.manager import RiskManager
from muwon.scoring.engine import FactorScoreStrategy, threshold_reachability
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import MarketContext, as_portfolio_strategy
from muwon.strategy.registry import build_strategy

# 예열 길이는 실험 쪽과 같은 값을 쓴다. 여기만 200으로 두었더니 200거래일
# 이동평균이 거의 안 채워져(상승추세 24/381일) 같은 설정인데 실험과 진단이
# 다른 국면을 냈다. 두 곳에 같은 뜻의 상수를 따로 두면 반드시 갈라진다.


def apply_regime_params(strategy, raw: str):
    """국면 Factor의 파라미터만 바꿔 같은 전략을 다시 만든다.

    '필터를 켜면 무엇이 달라지는가'를 보려면 전략 전체가 아니라 그 Factor의
    설정 하나만 갈아 끼울 수 있어야 한다."""
    if not raw or not isinstance(strategy, FactorScoreStrategy):
        return strategy
    overrides = json.loads(raw)
    config = strategy.config
    factor = config.factors["market_regime"]
    varied = replace(
        config,
        factors={
            **config.factors,
            "market_regime": replace(factor, params={**factor.params, **overrides}),
        },
    )
    return FactorScoreStrategy(varied)


def scan_regimes(strategy, histories, dates):
    """날짜별 국면과, 국면별로 실제 어떤 판정이 몇 번 나왔는지.

    판정 분포까지 세는 이유는 '문턱이 일을 했는가'와 '문턱에 닿을 일이
    아예 없었는가'가 다르기 때문이다. 약세장 문턱을 90으로 올려 뒀는데 그
    구간의 최고 점수가 애초에 82였다면, 그 90은 아무 일도 안 한 것이다.

    국면 Factor가 없는 전략이면 빈 값을 돌려준다."""
    if not isinstance(strategy, FactorScoreStrategy):
        return {}, {}
    engine = strategy._engine
    engine.warmup(histories)
    timeline: dict[date, str] = {}
    observed: dict[str, list] = defaultdict(lambda: [0.0, Counter()])
    for day in dates:
        results = engine.evaluate(MarketContext(as_of=day, histories=histories))
        regime = engine._current_regime()
        if not regime:
            continue
        timeline[day] = regime
        seen = observed[regime]
        for r in results:
            seen[0] = max(seen[0], r.total)
            seen[1][r.decision] += 1
    return timeline, dict(observed)


def main() -> None:
    parser = argparse.ArgumentParser(description="구간별 전략 성과 원인 분석")
    parser.add_argument("--strategy", default="factor_score_v1")
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--universe", choices=["market_cap", "volume"], default="market_cap"
    )
    parser.add_argument(
        "--regime-params",
        default="",
        help='국면 Factor 파라미터를 JSON으로 덮어쓴다 (예: \'{"uptrend_ma": 200}\')',
    )
    args = parser.parse_args()

    trade_from = date(args.year, 1, 1)
    trade_to = date(args.year, 12, 31)

    session_factory = make_session_factory(bootstrap_settings.database_url)
    universe = active_universe(session_factory, list(UNIVERSE), kind=args.universe)
    source = YahooFinanceDataSource()

    histories = load_histories(
        source,
        universe,
        trade_from - timedelta(days=WARMUP_DAYS),
        trade_to,
        cache=PriceCache(),
    )
    names = {t.symbol: t.name for t in universe}
    print(f"{args.strategy} · {args.year}년 · {len(histories)}종목\n")

    result = BacktestEngine(
        strategy=apply_regime_params(
            as_portfolio_strategy(build_strategy(args.strategy)), args.regime_params
        ),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    ).run(histories, trade_from=trade_from)

    dates = sorted({d for df in histories.values() for d in df["trade_date"] if d >= trade_from})
    scanner = apply_regime_params(
        as_portfolio_strategy(build_strategy(args.strategy)), args.regime_params
    )
    timeline, observed = scan_regimes(scanner, histories, dates)

    print(f"수익률 {result.total_return_pct:+.2f}%  MDD {result.max_drawdown_pct:.2f}%  "
          f"거래 {result.num_trades}건\n")

    if isinstance(scanner, FactorScoreStrategy):
        for factor in scanner._engine.factors:
            if isinstance(factor, MarketRegimeFactor) and factor.total_days:
                print(
                    f"■ 시장 필터  상승추세 판정 {factor.uptrend_days}/{factor.total_days}일"
                    f"  (설정 {args.regime_params or '기본'})\n"
                )

    if timeline:
        counts = Counter(timeline.values())
        total = sum(counts.values())
        print("■ 시장 국면 분포")
        for regime in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR"):
            if counts.get(regime):
                print(f"  {regime:<12} {counts[regime]:>3}일 ({counts[regime] / total * 100:.0f}%)")

        print("\n■ 국면별 문턱과 실제 도달 점수")
        print("  문턱 옆의 '천장'은 나머지 Factor가 전부 만점일 때의 총점이다.")
        print("  천장 < 문턱이면 그 국면은 매수가 수학적으로 불가능하다.")
        strategy_config = build_strategy(args.strategy)
        config = getattr(strategy_config, "config", None)
        ceilings = threshold_reachability(config) if config else {}
        for regime in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR"):
            if regime not in observed:
                continue
            top, decisions = observed[regime]
            ceiling, threshold = ceilings.get(regime, (float("nan"), float("nan")))
            flag = "  ← 도달 불가" if ceiling < threshold else ""
            dist = " ".join(
                f"{key}={decisions[key]}"
                for key in ("STRONG_BUY", "BUY", "WATCH")
                if decisions[key]
            )
            print(
                f"  {regime:<12} 문턱 {threshold:>3.0f}  천장 {ceiling:>5.1f}  "
                f"실제최고 {top:>5.1f}  {dist}{flag}"
            )

        print("\n■ 국면이 바뀐 시점")
        previous = None
        for day in dates:
            current = timeline.get(day)
            if current and current != previous:
                print(f"  {day}  {previous or '시작'} → {current}")
                previous = current

    if not result.closed_trades:
        print("\n청산된 거래가 없습니다.")
        return

    # 손실이 어느 국면에서 산 종목에서 났는지. 이게 핵심 질문이다.
    # 진입을 못 막은 것과, 진입은 막았지만 이미 산 걸 못 지킨 것은 다른 문제다.
    by_regime: dict[str, list] = defaultdict(list)
    for trade in result.closed_trades:
        by_regime[timeline.get(trade.entry_date, "(국면 없음)")].append(trade)

    if timeline:
        print("\n■ 진입 시점 국면별 손익")
        for regime, trades in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
            pnl = sum(t.pnl_amount for t in trades)
            wins = sum(1 for t in trades if t.pnl_amount > 0)
            print(f"  {regime:<14} {len(trades):>3}건  승 {wins:>3}  손익 {pnl:>+12,.0f}원")

    print("\n■ 손실 상위 5건")
    for trade in sorted(result.closed_trades, key=lambda t: t.pnl_amount)[:5]:
        entry_regime = timeline.get(trade.entry_date, "-")
        exit_regime = timeline.get(trade.exit_date, "-")
        print(
            f"  {names.get(trade.symbol, trade.symbol):<12} "
            f"{trade.entry_date}({entry_regime}) → {trade.exit_date}({exit_regime}) "
            f"{trade.pnl_pct:+6.1f}%  {trade.pnl_amount:>+11,.0f}원  [{trade.exit_reason}]"
        )

    holding = [(t.exit_date - t.entry_date).days for t in result.closed_trades]
    print(f"\n평균 보유 {sum(holding) / len(holding):.0f}일 (달력 기준)")


if __name__ == "__main__":
    main()
