"""청산을 다음 날 시가에 체결하는 선택지.

왜 만들었나. 수익의 70~92%가 밤사이(종가→시가)에 났다는 것을 쟀다
(설계안 §26). 종가에 파는 지금 방식은 **마지막 밤을 버리고 있다.**

덤으로 하나가 더 걸린다. 지금은 그날 종가를 보고 판단해서 그 종가에
판다고 계산하는데, 실거래는 장 마감 뒤에 정하고 다음 날 아침에 주문을
낸다. 판단과 체결을 하루 벌려 두는 것이 실제에 더 가깝다."""

from datetime import date

import pandas as pd
import pytest

from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.domain.types import Signal, SignalType
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy

무비용 = TransactionCosts(buy_fee_pct=0.0, sell_fee_pct=0.0, sell_tax_pct=0.0, slippage_pct=0.0)


class 정해진날에사고판다:
    """3일차에 사고 5일차에 팔라고 말하는 전략."""

    name = "테스트"
    max_holding_days = None

    def __init__(self, 살날: date, 팔날: date):
        self.살날, self.팔날 = 살날, 팔날

    def generate_signals(self, symbol: str, df: pd.DataFrame) -> list[Signal]:
        # 어댑터가 **전 기간을 한 번에** 넘기고 날짜별로 색인한다.
        # 마지막 봉만 보면 신호가 하루치만 나온다. 여기서 한 번 걸렸다.
        신호 = []
        for 날 in df["trade_date"]:
            종류 = (
                SignalType.BUY
                if 날 == self.살날
                else SignalType.SELL
                if 날 == self.팔날
                else None
            )
            if 종류 is None:
                continue
            신호.append(
                Signal(
                    symbol=symbol,
                    trade_date=날,
                    signal_type=종류,
                    strategy_name=self.name,
                    score=1.0,
                    reason="산다" if 종류 is SignalType.BUY else "판다",
                )
            )
        return 신호


def _bars(rows):
    return pd.DataFrame(
        [
            {
                "trade_date": date(2024, 1, d),
                "open": o,
                "high": max(o, c),
                "low": min(o, c),
                "close": c,
                "volume": 10_000,
            }
            for d, o, c in rows
        ]
    )


def _실행하기(bars, 살날, 팔날, *, 시가청산: bool):
    정책 = RiskPolicy(
        stop_loss_pct=-0.99,
        take_profit_pct=0.0,
        atr_stop_enabled=False,
        trailing_stop_enabled=False,
        max_position_weight=1.0,
        max_concurrent_positions=1,
        daily_loss_limit_pct=-0.99,
    )
    return BacktestEngine(
        strategy=정해진날에사고판다(살날, 팔날),
        risk_manager=RiskManager(policy_provider=lambda: 정책),
        costs=무비용,
        exit_at_open=시가청산,
    ).run({"A": bars})


# 3일에 사고 5일에 팔기로 정한다. 5일 종가는 100, 6일 시가는 130:
# 그 사이 밤에 30% 올랐다. 이 30%를 받느냐 버리느냐가 이 옵션이다.
BARS = _bars([(1, 90, 90), (2, 90, 90), (3, 100, 100), (4, 100, 100), (5, 100, 100), (6, 130, 130)])


def test_the_default_still_sells_at_the_close_of_the_day_it_decided():
    """기존 방식이 바뀌면 지금까지 잰 5년 성적이 전부 뜻이 달라진다."""
    결과 = _실행하기(BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=False)
    (매매,) = 결과.closed_trades
    assert 매매.exit_date == date(2024, 1, 5)
    assert 매매.exit_price == 100.0


def test_selling_at_the_next_open_picks_up_the_last_night():
    결과 = _실행하기(BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=True)
    (매매,) = 결과.closed_trades
    assert 매매.exit_date == date(2024, 1, 6), "체결일은 판단한 다음 날이어야 한다"
    assert 매매.exit_price == 130.0, "6일 시가에 팔려야 마지막 밤을 받는다"
    assert 매매.pnl_pct == pytest.approx(30.0)


def test_a_pending_exit_waits_instead_of_being_dumped_at_the_close():
    """판단한 다음 날 그 종목이 거래되지 않으면 그 다음 날로 미룬다.
    임의로 종가에 팔아 버리면 이 옵션의 뜻이 사라진다."""
    bars = _bars([(1, 90, 90), (2, 90, 90), (3, 100, 100), (4, 100, 100), (5, 100, 100)])
    # 5일에 팔라고 정했는데 그 뒤 봉이 없다. 체결할 자리가 없다.
    결과 = _실행하기(bars, date(2024, 1, 3), date(2024, 1, 5), 시가청산=True)
    assert 결과.closed_trades == [], "팔 자리가 없으면 판 것으로 치면 안 된다"
    assert "A" in 결과.final_positions, "아직 들고 있는 것으로 남아야 한다"


def test_the_same_position_is_not_decided_twice_while_it_waits():
    """대기 중인 종목을 또 청산 판단에 넣으면 이유가 덮어써지고,
    최악의 경우 같은 매매가 두 번 기록된다."""
    결과 = _실행하기(BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=True)
    assert len(결과.closed_trades) == 1


def test_the_cash_is_booked_on_the_day_it_actually_filled():
    """체결 전에 현금이 들어오면 그 하루 평가금액이 부풀고, 그 값으로
    다음 진입 규모를 정하므로 오차가 계속 굴러간다."""
    결과 = _실행하기(BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=True)
    곡선 = 결과.equity_curve.set_index("trade_date")
    # 5일: 아직 들고 있다 → 100원짜리 그대로
    assert 곡선.loc[date(2024, 1, 5), "positions"] == 1
    # 6일: 시가 130에 팔렸다 → 전액 현금
    assert 곡선.loc[date(2024, 1, 6), "positions"] == 0
    assert 곡선.loc[date(2024, 1, 6), "cash"] == 곡선.loc[date(2024, 1, 6), "equity"]


def _실행하기2(bars, 살날, 팔날, *, 시가청산: bool, 시가진입: bool):
    정책 = RiskPolicy(
        stop_loss_pct=-0.99,
        take_profit_pct=0.0,
        atr_stop_enabled=False,
        trailing_stop_enabled=False,
        max_position_weight=1.0,
        max_concurrent_positions=1,
        daily_loss_limit_pct=-0.99,
    )
    return BacktestEngine(
        strategy=정해진날에사고판다(살날, 팔날),
        risk_manager=RiskManager(policy_provider=lambda: 정책),
        costs=무비용,
        exit_at_open=시가청산,
        entry_at_open=시가진입,
    ).run({"A": bars})


# 3일에 사기로 정한다. 3일 종가 100, 4일 시가 120: 그 밤에 20% 올랐다.
# 종가에 사면 그 밤을 받고, 다음 날 시가에 사면 그 밤을 잃는다.
진입BARS = _bars(
    [(1, 90, 90), (2, 90, 90), (3, 100, 100), (4, 120, 120), (5, 120, 120), (6, 120, 120)]
)


def test_buying_at_the_next_open_gives_up_that_night():
    """수익의 70~92%가 밤사이에 났으므로(§26), 매수를 하루 늦추면 밤 하나를
    잃는다. 청산 쪽과 부호가 반대다. 이게 이 실험의 요점이다."""
    종가 = _실행하기2(진입BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=False, 시가진입=False)
    시가 = _실행하기2(진입BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=False, 시가진입=True)

    (종가매매,) = 종가.closed_trades
    (시가매매,) = 시가.closed_trades
    assert 종가매매.entry_date == date(2024, 1, 3)
    assert 종가매매.entry_price == 100.0
    assert 시가매매.entry_date == date(2024, 1, 4), "체결일은 판단한 다음 날이어야 한다"
    assert 시가매매.entry_price == 120.0, "그 밤의 상승을 못 받는다"
    assert 시가매매.pnl_pct < 종가매매.pnl_pct


def test_the_size_is_decided_at_the_moment_it_fills_not_the_day_before():
    """어제 수량을 정해 두면 밤사이 값이 변한 뒤에 옛 금액으로 사게 된다.
    실거래에서도 아침에 계좌를 보고 수량을 정한다."""
    결과 = _실행하기2(진입BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=False, 시가진입=True)
    (매매,) = 결과.closed_trades
    # 1,000만원을 120원짜리로 → 83,333주. 100원 기준(10만주)이 아니어야 한다.
    assert 매매.quantity == int(10_000_000 / 120)


def test_a_signal_that_could_not_fill_is_dropped_not_carried_over():
    """어제 신호는 어제 것이다. 못 산 것을 계속 들고 있으면 며칠 묵은
    신호로 사게 된다. 실거래에서도 당일 주문이다."""
    # 3일에 사기로 정했는데 그 뒤 봉이 없다.
    bars = _bars([(1, 90, 90), (2, 90, 90), (3, 100, 100)])
    결과 = _실행하기2(bars, date(2024, 1, 3), date(2024, 1, 9), 시가청산=False, 시가진입=True)
    assert 결과.final_positions == {}
    assert 결과.closed_trades == []


def test_both_sides_at_open_is_what_the_live_engine_actually_does():
    """실거래 엔진은 어제 일봉으로 판단해 오늘 시가에 사고 판다.
    이 조합이 실거래와 같은 규칙이다."""
    결과 = _실행하기2(진입BARS, date(2024, 1, 3), date(2024, 1, 5), 시가청산=True, 시가진입=True)
    (매매,) = 결과.closed_trades
    assert 매매.entry_date == date(2024, 1, 4)
    assert 매매.exit_date == date(2024, 1, 6)
