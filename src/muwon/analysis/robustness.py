"""전략을 여러 기간에 나눠 검증해 "그 시기에만 잘 맞았던 전략"을 걸러낸다.

한 구간에서 +59%가 나왔다고 그 전략이 좋은 게 아니다. 과거 데이터에 우연히
맞아떨어진 것일 수 있고(과최적화), 그런 전략은 앞으로는 통하지 않는다.
같은 전략을 여러 기간에 각각 실행해서 결과가 들쭉날쭉한지 꾸준한지를 봐야
"운"과 "실력"을 구분할 수 있다.

판단 기준을 평균 수익률 하나로 두지 않는 이유: 한 구간 +100%, 다른 구간
-60%인 전략은 평균이 +20%라도 실전에서 못 버틴다. 그래서 최악 구간 수익률과
플러스 구간 비율을 함께 본다. 살아남는 것이 먼저다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from muwon.backtest.engine import BacktestEngine
from muwon.risk.manager import RiskManager
from muwon.strategy.registry import StrategyDefinition

# 지표 예열에 쓸 여유 기간. 가장 긴 창이 60거래일(sma_long, price_channel_60)이라
# 주말·공휴일을 감안해 넉넉히 잡는다. 이 구간은 매매에 쓰이지 않는다.
WARMUP_DAYS = 150


@dataclass(frozen=True)
class PeriodWindow:
    label: str
    trade_from: date
    end: date

    @property
    def data_from(self) -> date:
        """지표 예열까지 포함해 실제로 시세를 받아와야 하는 시작일."""
        return self.trade_from - timedelta(days=WARMUP_DAYS)


def yearly_windows(start_year: int, end_year: int) -> list[PeriodWindow]:
    """연 단위 구간: 상승장/하락장이 해마다 다르므로 가장 읽기 쉬운 분할이다."""
    return [
        PeriodWindow(label=str(year), trade_from=date(year, 1, 1), end=date(year, 12, 31))
        for year in range(start_year, end_year + 1)
    ]


def half_year_windows(start_year: int, end_year: int) -> list[PeriodWindow]:
    """반기 단위: 구간을 더 잘게 나눠 우연히 맞은 경우를 더 잘 드러낸다."""
    windows = []
    for year in range(start_year, end_year + 1):
        windows.append(
            PeriodWindow(f"{year}H1", date(year, 1, 1), date(year, 6, 30))
        )
        windows.append(
            PeriodWindow(f"{year}H2", date(year, 7, 1), date(year, 12, 31))
        )
    return windows


@dataclass(frozen=True)
class PeriodOutcome:
    label: str
    return_pct: float
    mdd_pct: float
    win_rate_pct: float
    num_trades: int


@dataclass(frozen=True)
class RobustnessResult:
    key: str
    display_name: str
    category: str
    outcomes: list[PeriodOutcome]

    @property
    def returns(self) -> list[float]:
        return [o.return_pct for o in self.outcomes]

    @property
    def mean_return_pct(self) -> float:
        return statistics.fmean(self.returns) if self.returns else 0.0

    @property
    def worst_return_pct(self) -> float:
        """최악 구간의 수익률: 실전에서 버틸 수 있는지를 가르는 값."""
        return min(self.returns) if self.returns else 0.0

    @property
    def best_return_pct(self) -> float:
        return max(self.returns) if self.returns else 0.0

    @property
    def spread_pct(self) -> float:
        """최고와 최악의 차이. 크면 그 전략은 시기를 심하게 탄다는 뜻이다."""
        return self.best_return_pct - self.worst_return_pct

    @property
    def positive_ratio(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(1 for r in self.returns if r > 0) / len(self.returns)

    @property
    def worst_mdd_pct(self) -> float:
        return min((o.mdd_pct for o in self.outcomes), default=0.0)

    @property
    def total_trades(self) -> int:
        return sum(o.num_trades for o in self.outcomes)

    @property
    def verdict(self) -> str:
        """사람이 바로 읽을 수 있는 한 줄 판정.

        "평균이 높다"만 보면 한 구간의 대박에 속는다. 모든 구간에서 잃지
        않았는지를 먼저 보고, 그다음 얼마나 들쭉날쭉한지를 본다."""
        if self.total_trades == 0:
            return "거래없음"
        if self.positive_ratio == 1.0:
            return "✅ 전 구간 플러스"
        if self.positive_ratio >= 0.5 and self.worst_return_pct > -10:
            return "△ 대체로 양호"
        if self.best_return_pct > 20 and self.worst_return_pct < -20:
            return "⚠️ 기복 심함(과최적화 의심)"
        return "❌ 불안정"


def evaluate_robustness(
    definitions: list[StrategyDefinition],
    price_histories: dict[str, pd.DataFrame],
    windows: list[PeriodWindow],
    risk_manager: RiskManager,
    initial_cash: float = 10_000_000.0,
) -> list[RobustnessResult]:
    """각 전략을 각 구간에서 따로 백테스트한다.

    price_histories는 전체 기간(예열 포함)을 한 번에 받아 두고, 구간마다
    잘라 쓴다. 같은 데이터를 구간 수만큼 다시 내려받으면 느리기만 하다.
    구간마다 새 BacktestEngine을 만들어 초기 자금부터 다시 시작하므로,
    앞 구간의 성과가 뒤 구간에 섞이지 않는다."""
    results = []

    for definition in definitions:
        outcomes = []
        for window in windows:
            sliced = _slice_histories(price_histories, window)
            if not sliced:
                logger.warning(f"{window.label} 구간에 시세가 없어 건너뜁니다.")
                continue

            engine = BacktestEngine(
                strategy=definition.factory(),
                risk_manager=risk_manager,
                initial_cash=initial_cash,
            )
            result = engine.run(sliced, trade_from=window.trade_from)
            outcomes.append(
                PeriodOutcome(
                    label=window.label,
                    return_pct=result.total_return_pct,
                    mdd_pct=result.max_drawdown_pct,
                    win_rate_pct=result.win_rate_pct,
                    num_trades=result.num_trades,
                )
            )

        results.append(
            RobustnessResult(
                key=definition.key,
                display_name=definition.display_name,
                category=definition.category,
                outcomes=outcomes,
            )
        )
    return results


def _slice_histories(
    price_histories: dict[str, pd.DataFrame], window: PeriodWindow
) -> dict[str, pd.DataFrame]:
    """구간 + 예열 기간에 해당하는 부분만 잘라낸다."""
    sliced = {}
    for symbol, df in price_histories.items():
        if len(df) == 0:
            continue
        mask = (df["trade_date"] >= window.data_from) & (df["trade_date"] <= window.end)
        part = df[mask].reset_index(drop=True)
        # 예열만 있고 매매 구간이 없으면 의미가 없다
        if len(part) > 0 and (part["trade_date"] >= window.trade_from).any():
            sliced[symbol] = part
    return sliced


def rank_by_robustness(results: list[RobustnessResult]) -> list[RobustnessResult]:
    """최악 구간 수익률을 1순위로 정렬한다.

    평균 수익률로 줄을 세우면 "한 구간에서 크게 벌고 나머지에서 크게 잃는"
    전략이 위로 올라온다. 실전에서 중요한 건 못 버티는 구간이 있느냐이므로
    최악 구간을 먼저 보고, 같으면 평균으로 가른다."""
    return sorted(results, key=lambda r: (r.worst_return_pct, r.mean_return_pct), reverse=True)


def format_robustness_table(results: list[RobustnessResult], windows: list[PeriodWindow]) -> str:
    """콘솔·텔레그램에 그대로 쓸 수 있는 표 문자열."""
    header = f"{'전략키':<26}" + "".join(f"{w.label:>9}" for w in windows)
    header += f"{'평균':>9}{'최악':>9}{'거래':>7}  판정"
    lines = [header, "-" * len(header)]

    for r in results:
        by_label = {o.label: o.return_pct for o in r.outcomes}
        row = f"{r.key:<26}"
        row += "".join(f"{by_label.get(w.label, 0.0):>+8.1f}%" for w in windows)
        row += f"{r.mean_return_pct:>+8.1f}%{r.worst_return_pct:>+8.1f}%{r.total_trades:>7}  {r.verdict}"
        lines.append(row)
    return "\n".join(lines)
