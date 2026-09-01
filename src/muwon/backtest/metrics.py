"""백테스트 결과를 비교 가능한 지표로 환산한다.

지금까지는 총수익률·MDD·승률·거래수만 봤는데, 그걸로는 전략을 제대로 비교할
수 없다. 기간이 다르면 총수익률은 비교가 안 되고(1년 +20%와 3년 +20%는 전혀
다르다), 승률은 인수인계서 25항이 경고하듯 그 자체로는 의미가 없다.
승률 80%여도 평균이익 +1%에 평균손실 -6%면 장기적으로 잃는다.

그래서 기간에 무관한 값(CAGR)과 위험 대비 값(Sharpe·Sortino), 한 거래당
기대값(Expectancy)을 함께 낸다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class BacktestMetrics:
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    profit_factor: float
    expectancy_pct: float
    win_rate_pct: float
    num_trades: int
    avg_holding_days: float
    exposure_pct: float
    turnover: float

    def as_row(self) -> dict:
        return {
            "CAGR": self.cagr_pct,
            "MDD": self.max_drawdown_pct,
            "Sharpe": self.sharpe,
            "Sortino": self.sortino,
            "PF": self.profit_factor,
            "기대값%": self.expectancy_pct,
            "승률": self.win_rate_pct,
            "거래": self.num_trades,
            "보유일": self.avg_holding_days,
            "노출%": self.exposure_pct,
        }


def _daily_returns(equity: pd.Series) -> pd.Series:
    return equity.pct_change().dropna()


def cagr_pct(equity_curve: pd.DataFrame) -> float:
    """연평균 복리 수익률. 기간이 다른 결과를 같은 자로 재기 위한 값이다."""
    if len(equity_curve) < 2:
        return 0.0
    start_value = float(equity_curve["equity"].iloc[0])
    end_value = float(equity_curve["equity"].iloc[-1])
    if start_value <= 0 or end_value <= 0:
        return 0.0
    days = (equity_curve["trade_date"].iloc[-1] - equity_curve["trade_date"].iloc[0]).days
    if days <= 0:
        return 0.0
    return ((end_value / start_value) ** (365.25 / days) - 1) * 100


def max_drawdown_pct(equity: pd.Series) -> float:
    """고점 대비 최대 낙폭. 엔진 밖에서 만든 곡선(예: 갈래를 합친 곡선)도
    같은 자로 재려면 곡선만 받아 계산할 수 있어야 한다."""
    if len(equity) < 2:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1).min() * 100)


def sharpe(equity_curve: pd.DataFrame, risk_free_rate: float = 0.0) -> float:
    """수익률을 변동성으로 나눈 값: 같은 수익이면 덜 흔들린 쪽이 낫다.

    무위험수익률은 기본 0으로 둔다. 넣으려면 값을 정해야 하는데, 그 선택이
    결과를 흔드는 데 비해 전략끼리 비교하는 목적에는 영향이 없다."""
    returns = _daily_returns(equity_curve["equity"])
    if len(returns) < 2:
        return 0.0
    excess = returns - risk_free_rate / TRADING_DAYS_PER_YEAR
    std = float(excess.std())
    if not math.isfinite(std) or std == 0:
        return 0.0
    return float(excess.mean() / std * math.sqrt(TRADING_DAYS_PER_YEAR))


def sortino(equity_curve: pd.DataFrame) -> float:
    """Sharpe와 같되 '아래로 흔들린 것'만 위험으로 본다.

    위로 크게 튀는 건 벌하지 않는 게 맞다. 투자자가 싫어하는 건 손실 쪽
    변동이지 이익 쪽 변동이 아니다."""
    returns = _daily_returns(equity_curve["equity"])
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    # 하락일이 하나뿐이면 표준편차가 NaN이다(표본 분산은 n-1로 나눈다).
    # 0인지만 검사하면 NaN이 그대로 통과해 비교표에 조용히 섞인다.
    # 실제로 그 상태로 한 번 나왔다.
    if len(downside) < 2:
        return 0.0
    downside_std = float(downside.std())
    if not math.isfinite(downside_std) or downside_std == 0:
        return 0.0
    return float(returns.mean() / downside_std * math.sqrt(TRADING_DAYS_PER_YEAR))


def profit_factor(trades) -> float:
    """번 돈 총액 ÷ 잃은 돈 총액. 1보다 크면 이익이 손실보다 많다.

    분석 리포트의 '손익비'와 다른 값이니 혼동하면 안 된다. 손익비는
    평균이익 ÷ 평균손실(건당 크기 비교)이고, 이건 총액 비교다. 거래 건수가
    한쪽에 몰리면 둘이 정반대를 가리킬 수 있다."""
    gains = sum(t.pnl_amount for t in trades if t.pnl_amount > 0)
    losses = -sum(t.pnl_amount for t in trades if t.pnl_amount < 0)
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def expectancy_pct(trades) -> float:
    """한 거래당 평균 손익률(%). 승률과 손익 크기를 한 숫자로 합친 값이다.

    승률만 보면 속는다(인수인계서 25항). 이 값이 양수여야 거래를 반복할수록
    돈이 늘어난다."""
    if not trades:
        return 0.0
    return sum(t.pnl_pct for t in trades) / len(trades)


def exposure_pct(equity_curve: pd.DataFrame) -> float:
    """전체 기간 중 몇 %의 날에 종목을 들고 있었는가.

    수익률이 낮아도 노출이 낮았다면 '기회가 없어서'일 수 있고, 노출이 높은데
    낮았다면 '판단이 틀려서'다. 둘은 다른 문제라 구분이 필요하다."""
    if len(equity_curve) == 0 or "positions" not in equity_curve:
        return 0.0
    return float((equity_curve["positions"] > 0).mean() * 100)


def turnover(trades, equity_curve: pd.DataFrame) -> float:
    """매매 금액 총합 ÷ 평균 평가금액. 자금을 몇 번 굴렸는지에 해당한다.

    회전율이 높을수록 수수료·세금·슬리피지가 성과를 갉아먹으므로, 같은
    수익률이면 낮은 쪽이 낫다."""
    if not trades or len(equity_curve) == 0:
        return 0.0
    average_equity = float(equity_curve["equity"].mean())
    if average_equity <= 0:
        return 0.0
    traded = sum(t.entry_price * t.quantity + t.exit_price * t.quantity for t in trades)
    return traded / average_equity


def avg_holding_days(trades) -> float:
    if not trades:
        return 0.0
    return sum((t.exit_date - t.entry_date).days for t in trades) / len(trades)


def compute_metrics(result) -> BacktestMetrics:
    """BacktestResult 하나를 지표 묶음으로."""
    curve = result.equity_curve
    trades = result.closed_trades
    return BacktestMetrics(
        total_return_pct=result.total_return_pct,
        cagr_pct=cagr_pct(curve),
        max_drawdown_pct=result.max_drawdown_pct,
        sharpe=sharpe(curve),
        sortino=sortino(curve),
        profit_factor=profit_factor(trades),
        expectancy_pct=expectancy_pct(trades),
        win_rate_pct=result.win_rate_pct,
        num_trades=result.num_trades,
        avg_holding_days=avg_holding_days(trades),
        exposure_pct=exposure_pct(curve),
        turnover=turnover(trades, curve),
    )
