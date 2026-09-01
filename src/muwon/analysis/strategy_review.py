"""등록된 전략 가설들을 같은 데이터·같은 기간에 대해 비교하는 로직.

scripts/run_hypothesis_sweep.py(사람이 손으로 실행하는 스윕)와
scripts/run_daily_review.py(하루 매매가 끝날 때마다 자동으로 도는 리뷰)가
이 모듈의 sweep_strategies()를 그대로 공유한다. "다른 전략이었으면
수익률이 어땠을까"라는 질문에 답하는 계산이 두 스크립트에서 같아야
비교가 의미 있기 때문이다."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy.orm import sessionmaker

from muwon.backtest.engine import BacktestEngine
from muwon.db.models import BacktestRunRow
from muwon.risk.manager import RiskManager
from muwon.strategy.registry import StrategyDefinition


@dataclass(frozen=True)
class SweepResult:
    key: str
    display_name: str
    status: str
    return_pct: float
    mdd_pct: float
    win_rate_pct: float
    num_trades: int
    params_json: str


def _params_snapshot(strategy) -> str:
    params = getattr(strategy, "params", None)
    if params is not None and dataclasses.is_dataclass(params):
        return json.dumps(dataclasses.asdict(params), ensure_ascii=False)
    return "{}"


def sweep_strategies(
    definitions: list[StrategyDefinition],
    price_histories: dict[str, pd.DataFrame],
    risk_manager: RiskManager,
    initial_cash: float = 10_000_000.0,
) -> list[SweepResult]:
    """등록된 전략마다 새 BacktestEngine으로 같은 price_histories를 돌려
    독립적으로 채점한다. 실전에서 하나만 활성화되는 것과 달리, 여기선
    "그날 다른 전략이었다면"을 물어야 하므로 전략마다 별도 가상계좌로
    평가한다."""
    results = []
    for definition in definitions:
        strategy = definition.factory()
        engine = BacktestEngine(strategy=strategy, risk_manager=risk_manager, initial_cash=initial_cash)
        result = engine.run(price_histories)
        results.append(
            SweepResult(
                key=definition.key,
                display_name=definition.display_name,
                status=definition.status,
                return_pct=result.total_return_pct,
                mdd_pct=result.max_drawdown_pct,
                win_rate_pct=result.win_rate_pct,
                num_trades=result.num_trades,
                params_json=_params_snapshot(strategy),
            )
        )
    return results


def persist_sweep_results(
    session_factory: sessionmaker,
    results: list[SweepResult],
    period_start: date,
    period_end: date,
    notes: str = "",
) -> None:
    with session_factory() as session:
        for r in results:
            session.add(
                BacktestRunRow(
                    strategy_key=r.key,
                    params_json=r.params_json,
                    period_start=period_start,
                    period_end=period_end,
                    total_return_pct=r.return_pct,
                    max_drawdown_pct=r.mdd_pct,
                    win_rate_pct=r.win_rate_pct,
                    num_trades=r.num_trades,
                    notes=notes,
                )
            )
        session.commit()


@dataclass(frozen=True)
class ReviewReport:
    period_start: date
    period_end: date
    live_key: str
    results: list[SweepResult]

    @property
    def live_result(self) -> SweepResult | None:
        return next((r for r in self.results if r.key == self.live_key), None)

    @property
    def best_result(self) -> SweepResult | None:
        if not self.results:
            return None
        return max(self.results, key=lambda r: r.return_pct)

    @property
    def live_is_best(self) -> bool:
        best, live = self.best_result, self.live_result
        return best is not None and live is not None and best.key == live.key


def build_review_report(
    results: list[SweepResult], live_key: str, period_start: date, period_end: date
) -> ReviewReport:
    return ReviewReport(period_start=period_start, period_end=period_end, live_key=live_key, results=results)


def format_review_message(report: ReviewReport) -> str:
    """"다른 전략이었다면 수익률이 더 좋았을 수도, 안 좋았을 수도 있다"를
    사람이 바로 읽을 수 있는 텔레그램 메시지로 바꾼다."""
    lines = [f"📊 전략 리뷰 ({report.period_start} ~ {report.period_end})"]

    live = report.live_result
    if live is None:
        lines.append(f"현재 활성 전략({report.live_key})은 이번 비교 대상에 없습니다.")
        return "\n".join(lines)

    lines.append(
        f"현재 활성 [{live.key}] {live.display_name}: {live.return_pct:+.2f}% "
        f"(MDD {live.mdd_pct:.2f}%, 승률 {live.win_rate_pct:.1f}%, {live.num_trades}건)"
    )

    others = sorted((r for r in report.results if r.key != live.key), key=lambda r: r.return_pct, reverse=True)
    if others:
        lines.append("다른 전략이었다면:")
        for r in others:
            delta = r.return_pct - live.return_pct
            lines.append(f"  [{r.key}] {r.return_pct:+.2f}% (현재 대비 {delta:+.2f}%p, {r.num_trades}건)")

    if report.live_is_best:
        lines.append("→ 지금 쓰는 전략이 비교 대상 중 최고 성과입니다.")
    else:
        best = report.best_result
        lines.append(f"→ 전환 검토: python scripts/configure.py strategy --active-key {best.key}")

    return "\n".join(lines)
