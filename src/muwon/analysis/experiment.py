"""전략 실험을 실행하고 비교하는 실행기.

지금까지 가설 하나를 확인할 때마다 스크립트를 새로 짰다. 그러다 보니 조건이
조금씩 달라져(예열을 줬는지, 어느 유니버스인지) 결과끼리 비교가 안 되는 일이
생겼다. 실제로 예열 없는 벤치마크와 예열 있는 검증이 다른 숫자를 내서 한참
헤맸다.

그래서 실험을 한 곳으로 모은다. 세 가지를 지킨다.

1. 시세는 한 번만 받아 모든 변형이 같은 데이터를 쓴다. 데이터가 다르면
   비교 자체가 성립하지 않고, 매번 받으면 실험이 느려 실험을 안 하게 된다.
2. 항상 예열 구간을 준다. 지표가 덜 채워진 채 시작하면 그 차이가 결과로
   증폭된다.
3. 구간별로 나눠 실행한다. 한 구간 결과는 우연일 수 있다. donchian_20_10이
   +59%로 1등이었다가 다기간 검증에서 뒤집힌 적이 있다.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.backtest.metrics import (
    BacktestMetrics,
    compute_metrics,
    max_drawdown_pct,
    sharpe,
)
from muwon.risk.manager import RiskManager
from muwon.scoring.config import StrategyConfig
from muwon.scoring.engine import FactorScoreStrategy
from muwon.settings.schema import RiskPolicy

# 420 달력일 ≈ 290 거래일. 120일선에는 250이면 충분했지만, 시장 필터가
# 쓰는 200거래일 이동평균까지 채우려면 그걸로 모자란다. 안 채워진 채로
# 비교하면 "필터가 나빴다"인지 "필터가 켜지지도 않았다"인지 구분이 안 된다.
WARMUP_DAYS = 420


@dataclass(frozen=True)
class PeriodResult:
    label: str
    metrics: BacktestMetrics


@dataclass(frozen=True)
class ExperimentResult:
    """한 설정을 여러 구간에 실행한 결과."""

    name: str
    periods: list[PeriodResult] = field(default_factory=list)

    @property
    def returns(self) -> list[float]:
        return [p.metrics.total_return_pct for p in self.periods]

    @property
    def worst_return_pct(self) -> float:
        """가장 나빴던 구간. 이 시스템의 1순위 판단 기준이다.
        잘 벌 때보다 못 버티는 구간이 있느냐가 먼저다."""
        return min(self.returns) if self.periods else 0.0

    @property
    def mean_return_pct(self) -> float:
        return sum(self.returns) / len(self.periods) if self.periods else 0.0

    @property
    def mean_cagr_pct(self) -> float:
        return (
            sum(p.metrics.cagr_pct for p in self.periods) / len(self.periods)
            if self.periods
            else 0.0
        )

    @property
    def mean_sharpe(self) -> float:
        return (
            sum(p.metrics.sharpe for p in self.periods) / len(self.periods)
            if self.periods
            else 0.0
        )

    @property
    def worst_drawdown_pct(self) -> float:
        return min((p.metrics.max_drawdown_pct for p in self.periods), default=0.0)

    @property
    def total_trades(self) -> int:
        return sum(p.metrics.num_trades for p in self.periods)


def slice_for_year(histories: dict[str, pd.DataFrame], year: int) -> dict[str, pd.DataFrame]:
    """한 해를 매매하되 그 앞에 예열 구간을 붙여 잘라 준다."""
    start = date(year, 1, 1) - timedelta(days=WARMUP_DAYS)
    end = date(year, 12, 31)
    sliced = {}
    for symbol, df in histories.items():
        window = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
        if len(window):
            sliced[symbol] = window
    return sliced


def run_experiment(
    name: str,
    strategy_factory,
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
    costs: TransactionCosts | None = None,
    exit_at_open: bool = False,
    entry_at_open: bool = False,
) -> ExperimentResult:
    """같은 설정을 연도별로 각각 실행한다.

    strategy_factory는 매번 새 전략을 만들어야 한다. 전략이 예열 결과를
    내부에 들고 있어서, 같은 객체를 여러 구간에 재사용하면 앞 구간 데이터가
    남는다."""
    policy = policy or RiskPolicy()
    periods = []
    for year in years:
        sliced = slice_for_year(histories, year)
        if not sliced:
            continue
        result = BacktestEngine(
            strategy=strategy_factory(),
            risk_manager=RiskManager(policy_provider=lambda p=policy: p),
            costs=costs,
            exit_at_open=exit_at_open,
            entry_at_open=entry_at_open,
        ).run(sliced, trade_from=date(year, 1, 1))
        periods.append(PeriodResult(str(year), compute_metrics(result)))
    return ExperimentResult(name, periods)


def factor_contribution(
    config: StrategyConfig,
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> list[ExperimentResult]:
    """Factor를 하나씩 꺼 보고 성과가 어떻게 변하는지 잰다(인수인계서 28항).

    최종 성과만 보면 어느 변수가 실제로 일하는지 알 수 없다. 껐을 때 성과가
    안 떨어지는 Factor는 가중치만 차지하고 있는 것이고, 껐을 때 오히려
    좋아지는 Factor는 해를 끼치고 있는 것이다.

    첫 항목이 전부 켠 기준선이고, 이후 항목이 각각 하나씩 끈 결과다."""
    results = [
        run_experiment(
            "기준선 (전부 켬)", lambda: FactorScoreStrategy(config), histories, years, policy
        )
    ]
    for key, factor_config in config.factors.items():
        if not factor_config.enabled or factor_config.weight <= 0:
            continue
        without = replace(
            config,
            factors={
                **config.factors,
                key: replace(factor_config, enabled=False),
            },
        )
        results.append(
            run_experiment(
                f"{key} 끔", lambda c=without: FactorScoreStrategy(c), histories, years, policy
            )
        )
    return results


def weight_sweep(
    config: StrategyConfig,
    factor_key: str,
    weights: list[float],
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> list[ExperimentResult]:
    """한 Factor의 가중치만 바꿔 가며 실행한다.

    나머지 가중치는 그대로 두지만, 합계가 100이 아니어도 점수 엔진이
    재정규화하므로 '이 Factor의 상대 비중'만 달라지는 효과가 된다."""
    results = []
    for weight in weights:
        varied = replace(
            config,
            factors={
                **config.factors,
                factor_key: replace(config.factors[factor_key], weight=weight, enabled=weight > 0),
            },
        )
        results.append(
            run_experiment(
                f"{factor_key}={weight:g}",
                lambda c=varied: FactorScoreStrategy(c),
                histories,
                years,
                policy,
            )
        )
    return results


def slippage_sweep(
    name: str,
    strategy_factory,
    slippages: list[float],
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> list[ExperimentResult]:
    """체결가 가정만 바꿔 가며 실행한다.

    지금까지 낸 모든 숫자는 "종가에 원하는 만큼 체결됐다"는 가정 위에 있다.
    회전율이 높은 전략일수록 이 가정이 결과를 크게 부풀린다. 1년에 250번
    사고파는 전략은 편도 0.1%만 잡아도 연 수 %가 사라진다.

    어느 값이 맞는지는 실거래로만 알 수 있다. 그래서 하나를 고르지 않고
    민감도를 본다. 0.1%에서 결론이 뒤집히는 전략이면 그 결론은 원래
    없던 것이다."""
    return [
        run_experiment(
            f"{name} 슬리피지 {s * 100:.2f}%",
            strategy_factory,
            histories,
            years,
            policy,
            costs=TransactionCosts(slippage_pct=s),
        )
        for s in slippages
    ]


def take_profit_sweep(
    name: str,
    strategy_factory,
    levels: list[float],
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> list[ExperimentResult]:
    """익절선만 바꿔 가며 실행한다.

    이 시스템에는 익절이 아예 없었다. 오르는 중이면 손절이나 보유 기간에
    걸릴 때까지 그대로 들고 갔다. volume_surge_5d는 파는 조건이 시간이라
    특히 크게 작용한다. 3일째 +15%가 나 있어도 5일째까지 기다린다.

    다만 익절은 공짜가 아니다. 추세추종 계열은 **몇 번 크게 먹는 것**으로
    먹고 사는데 익절이 그 꼬리를 자른다. 그래서 "넣을까 말까"가 아니라
    "얼마에 넣으면 어디까지 좋아지고 어디부터 나빠지나"를 본다.

    levels는 비율이다(0.10 = +10%). 0은 끔 = 지금 상태."""
    base = policy or RiskPolicy()
    return [
        run_experiment(
            f"{name} 익절 {'없음' if level <= 0 else f'+{level * 100:.0f}%'}",
            strategy_factory,
            histories,
            years,
            replace(base, take_profit_pct=level),
        )
        for level in levels
    ]


def param_sweep(
    config: StrategyConfig,
    factor_key: str,
    param: str,
    values: list,
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
    base_params: dict | None = None,
) -> list[ExperimentResult]:
    """한 Factor의 파라미터 하나만 바꿔 가며 실행한다.

    가중치 스윕(weight_sweep)이 '이 변수를 얼마나 믿을 것인가'를 묻는다면,
    이건 '이 변수를 어떻게 계산할 것인가'를 묻는다. 국면 판정 기준을 바꾸는
    실험처럼 가중치로는 표현할 수 없는 가설이 여기에 들어간다."""
    results = []
    # 조건 두 개가 맞물려야 뜻이 생기는 경우가 있다. 시장 필터는 '평균선 위'
    # 없이 '평균선 기울기'만 보면 의미가 없다. 고정할 값은 base_params로 받고,
    # 이름표에 함께 찍어 어떤 조건에서 잰 결과인지 표에 남게 한다.
    base = base_params or {}
    fixed = " ".join(f"{k}={v}" for k, v in base.items())
    for value in values:
        factor_config = config.factors[factor_key]
        varied = replace(
            config,
            factors={
                **config.factors,
                factor_key: replace(
                    factor_config, params={**factor_config.params, **base, param: value}
                ),
            },
        )
        results.append(
            run_experiment(
                f"{fixed} {param}={value}".strip(),
                lambda c=varied: FactorScoreStrategy(c),
                histories,
                years,
                policy,
            )
        )
    return results


def daily_returns_by_strategy(
    named_factories: dict,
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> tuple[dict[str, pd.Series], dict[str, float]]:
    """전략별 일간 수익률과 노출도를 낸다.

    구간 경계에서는 자금이 초기값으로 되돌아가므로 그날의 수익률은 버린다.
    안 버리면 '리셋'이 급등락으로 잡혀 상관계수가 오염된다.

    노출도를 함께 내는 이유가 있다. 상관이 낮은 게 **정말 다른 때에 벌어서**
    인지 **그냥 거의 안 사서**인지를 갈라야 하기 때문이다. 후자라면 그건
    분산이 아니라 그냥 놀고 있는 자금이고, 갈래로 떼어 줄 이유가 없다."""
    policy = policy or RiskPolicy()
    series: dict[str, pd.Series] = {}
    exposure: dict[str, float] = {}
    for name, factory in named_factories.items():
        pieces, held, total = [], 0, 0
        for year in years:
            sliced = slice_for_year(histories, year)
            if not sliced:
                continue
            result = BacktestEngine(
                strategy=factory(),
                risk_manager=RiskManager(policy_provider=lambda p=policy: p),
            ).run(sliced, trade_from=date(year, 1, 1))
            curve = result.equity_curve
            if len(curve) < 2:
                continue
            pieces.append(curve.set_index("trade_date")["equity"].pct_change().dropna())
            held += int((curve["positions"] > 0).sum())
            total += len(curve)
        if pieces:
            series[name] = pd.concat(pieces)
            exposure[name] = held / total * 100 if total else 0.0
    return series, exposure


def sleeve_curves(
    named_factories: dict,
    histories: dict[str, pd.DataFrame],
    years: list[int],
    policy: RiskPolicy | None = None,
) -> tuple[dict[str, dict[int, pd.Series]], dict[str, dict[int, int]]]:
    """갈래별·연도별 자금 곡선(1.0 시작으로 정규화)과 거래 건수.

    정규화하는 이유는 갈래마다 배정 자금이 다르기 때문이다. 비중을 나중에
    곱해서 합치려면 곡선이 '배수'여야 한다."""
    policy = policy or RiskPolicy()
    out: dict[str, dict[int, pd.Series]] = {}
    trades: dict[str, dict[int, int]] = {}
    for name, factory in named_factories.items():
        per_year: dict[int, pd.Series] = {}
        per_year_trades: dict[int, int] = {}
        for year in years:
            sliced = slice_for_year(histories, year)
            if not sliced:
                continue
            result = BacktestEngine(
                strategy=factory(),
                risk_manager=RiskManager(policy_provider=lambda p=policy: p),
            ).run(sliced, trade_from=date(year, 1, 1))
            curve = result.equity_curve
            if len(curve) < 2:
                continue
            equity = curve.set_index("trade_date")["equity"]
            per_year[year] = equity / float(equity.iloc[0])
            per_year_trades[year] = result.num_trades
        out[name] = per_year
        trades[name] = per_year_trades
    return out, trades


def blend_sleeves(
    curves: dict[str, dict[int, pd.Series]],
    weights: dict[str, float],
    years: list[int],
    trades: dict[str, dict[int, int]] | None = None,
) -> ExperimentResult:
    """갈래들을 비중대로 합친 계좌의 성과.

    갈래를 실제로 만들기 전에, 이미 잰 곡선만으로 합친 결과를 낼 수 있다.
    구조를 짜는 데 드는 비용을 치르기 전에 그럴 값어치가 있는지부터 확인하는
    게 순서다.

    연 단위로만 비중을 맞춘다(연중에는 갈래끼리 자금을 옮기지 않는다). 매일
    맞추면 실제 운영과 달라지고, 한쪽이 깨질 때 다른 쪽 돈을 계속 부어 주는
    셈이라 결과가 실제보다 좋게 나온다."""
    total = sum(weights.values())
    share = {k: v / total for k, v in weights.items()} if total else {}
    periods = []
    for year in years:
        parts = {k: c[year] for k, c in curves.items() if year in c and k in share}
        if not parts:
            continue
        frame = pd.DataFrame(parts).ffill().dropna(how="all")
        combined = sum(frame[k].fillna(1.0) * share[k] for k in parts)
        # 합친 계좌에서는 낼 수 없는 값(승률·손익비·기대값)은 NaN으로 둔다.
        # 거래는 갈래별로 일어나므로 '합친 계좌의 한 거래'라는 게 없다.
        # 0으로 채우면 표에서 '최악의 전략'으로 잘못 읽힌다.
        counted = sum(
            (trades or {}).get(key, {}).get(year, 0) for key in parts
        )
        metrics = BacktestMetrics(
            total_return_pct=float(combined.iloc[-1] - 1) * 100,
            cagr_pct=float(combined.iloc[-1] - 1) * 100,  # 한 해 구간이라 총수익률과 같다
            max_drawdown_pct=max_drawdown_pct(combined),
            sharpe=sharpe(pd.DataFrame({"equity": combined.to_numpy()})),
            sortino=float("nan"),
            profit_factor=float("nan"),
            expectancy_pct=float("nan"),
            win_rate_pct=float("nan"),
            num_trades=counted,
            avg_holding_days=float("nan"),
            exposure_pct=float("nan"),
            turnover=float("nan"),
        )
        periods.append(PeriodResult(str(year), metrics))
    label = " + ".join(f"{k} {share[k] * 100:.0f}%" for k in share)
    return ExperimentResult(label, periods)


def correlation_matrix(series: dict[str, pd.Series]) -> pd.DataFrame:
    """전략끼리 같은 날 같이 움직였는가.

    자금을 갈래로 나누는 게 뜻을 가지려면 갈래들이 **서로 다른 때에** 잃어야
    한다. 상관이 높으면 나눠 놓기만 하고 분산 효과는 없는데 관리할 규칙만
    두 배가 된다. 그래서 갈래 구조를 만들기 전에 이걸 먼저 잰다."""
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).corr()


def format_correlation(matrix: pd.DataFrame, exposure: dict[str, float] | None = None) -> str:
    if matrix.empty:
        return "상관 계산 불가"
    width = max(len(str(c)) for c in matrix.columns) + 2
    lines = [" " * width + "".join(f"{c[:8]:>10}" for c in matrix.columns)]
    for name, row in matrix.iterrows():
        lines.append(f"{name:<{width}}" + "".join(f"{v:>10.2f}" for v in row))
    pairs = [
        (matrix.iloc[i, j], matrix.index[i], matrix.columns[j])
        for i in range(len(matrix))
        for j in range(i + 1, len(matrix))
    ]
    if exposure:
        lines.append("")
        lines.append("노출도: 전체 기간 중 종목을 들고 있던 날의 비율")
        lines.append("  낮으면 '다른 때에 벌어서'가 아니라 '그냥 안 사서' 상관이 낮은 것이다")
        for name, value in sorted(exposure.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {name:<24}{value:>6.1f}%")
    if pairs:
        lines.append("")
        lines.append("낮은 순: 분산 효과가 큰 조합부터")
        for value, a, b in sorted(pairs)[:8]:
            verdict = "나눌 가치 큼" if value < 0.4 else ("보통" if value < 0.7 else "거의 같이 움직임")
            lines.append(f"  {a:<20} × {b:<20} {value:>6.2f}   {verdict}")
    return "\n".join(lines)


def run_header(
    mode: str,
    universe_kind: str,
    symbols: list[str],
    years: list[int],
    extra: dict | None = None,
) -> str:
    """결과 위에 붙일 실행 조건.

    오늘 낸 표가 내일 다시 필요할 때, 어떤 조건에서 나온 숫자인지 표 안에
    없으면 재현할 수가 없다. 실제로 58종목 기준선을 네 번 다시 만들었다.
    유니버스 종류·종목 수·기간·커밋을 숫자와 같은 파일에 남긴다.

    종목 목록 전체를 적는 이유는, 유니버스 스냅샷이 나중에 갱신되면 '그때
    그 58종목'을 되살릴 방법이 이것뿐이기 때문이다."""
    lines = [
        f"# 실험 결과: {mode}",
        "",
        f"- 실행: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"- 커밋: {_git_sha()}",
        f"- 유니버스: {universe_kind} · {len(symbols)}종목",
        f"- 기간: {min(years)}~{max(years)} (구간당 예열 {WARMUP_DAYS}일)",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"- {key}: {value}")
    lines += ["", f"<details><summary>종목 {len(symbols)}개</summary>", "", ", ".join(symbols), "", "</details>", ""]
    return "\n".join(lines)


def _git_sha() -> str:
    """어느 코드로 낸 숫자인지. git이 없으면 조용히 넘어간다. 결과를 못
    남기는 것보다 낫다."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "(불명)"


def format_comparison(results: list[ExperimentResult], years: list[int]) -> str:
    """실험 결과를 한 표로. 정렬은 최악 구간 기준이 아니라 입력 순서를 지킨다.
    Factor 기여도는 기준선과의 차이를 봐야 하므로 순서가 뜻을 가진다."""
    if not results:
        return "결과 없음"

    name_width = max(len(r.name) for r in results) + 2
    header = f"{'설정':<{name_width}}" + "".join(f"{y:>8}" for y in years)
    header += f"{'평균':>8}{'최악':>8}{'CAGR':>8}{'Sharpe':>8}{'MDD':>8}{'PF':>7}{'거래':>6}"
    lines = [header, "-" * len(header)]

    baseline = results[0]
    for result in results:
        by_year = {p.label: p.metrics.total_return_pct for p in result.periods}
        row = f"{result.name:<{name_width}}"
        row += "".join(f"{by_year.get(str(y), 0.0):>+8.1f}" for y in years)
        row += (
            f"{result.mean_return_pct:>+8.1f}{result.worst_return_pct:>+8.1f}"
            f"{result.mean_cagr_pct:>+8.1f}{result.mean_sharpe:>8.2f}"
            f"{result.worst_drawdown_pct:>8.1f}"
        )
        pf = [p.metrics.profit_factor for p in result.periods]
        finite = [v for v in pf if math.isfinite(v)]
        # 낼 수 없는 값은 '—'로. 0으로 찍으면 최악의 전략처럼 읽힌다.
        row += f"{(sum(finite) / len(finite)):>7.2f}" if finite else f"{'—':>7}"
        row += f"{result.total_trades:>6}"
        if result is not baseline:
            delta = result.worst_return_pct - baseline.worst_return_pct
            row += f"   최악 {delta:+.1f}%p"
        lines.append(row)
    return "\n".join(lines)
