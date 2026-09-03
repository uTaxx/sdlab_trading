"""판 돈은 이틀 뒤에 쓸 수 있다 (T+2).

## 무엇이 문제였나 (2026-09-02)

백테스트가 판 즉시 `cash += 매도대금`으로 현금을 늘리고, 그 돈으로 같은 날
다시 샀다. 실제로는 한국 주식 매도 대금이 2거래일 뒤에 들어온다.

**회전이 빠른 전략일수록 유리해진다.** 갭 상승 따라가기처럼 매일 사고파는
전략은 매일 매도 대금을 그날 재투자하는 것으로 계산된다. 하루 한 바퀴 도는
전략과 5거래일 들고 가는 전략을 나란히 놓으면 앞쪽이 실제보다 좋게 나온다.

## 기본값은 0으로 둔다

지금까지의 모든 백테스트 숫자가 즉시 결제 위에 있다. 기본값을 2로 바꾸면
전략 평가 결과와 기간 검증 숫자가 전부 다른 조건의 값이 된다. 켜는 쪽에서
그 사실을 적는다.

## 평가금액에서는 빼지 않는다

돈이 사라진 것이 아니라 아직 못 쓸 뿐이다. 빼고 세면 파는 날마다 자산이
뚝 떨어졌다가 이틀 뒤 돌아온다.
"""

from __future__ import annotations

import inspect

from muwon.backtest.engine import BacktestEngine
from muwon.domain.types import Signal, SignalType
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import PortfolioStrategy
from tests.price_series import flat_then_breakout

시세틀 = flat_then_breakout(tail_days=0)
날들 = list(시세틀["trade_date"])
시세 = {"000001": 시세틀}


class 사고팔기(PortfolioStrategy):
    """정한 날에 사고 정한 날에 판다. 시세와 무관하게 움직인다."""

    def __init__(self, 살날들, 팔날들):
        self.name = "시험"
        self.max_holding_days = None
        self._살날들 = set(살날들)
        self._팔날들 = set(팔날들)

    def prepare(self, histories):
        pass

    def evaluate(self, ctx):
        나온것 = []
        for 심볼 in ctx.histories:
            if ctx.as_of in self._팔날들 and 심볼 in ctx.held:
                나온것.append(Signal(심볼, ctx.as_of, SignalType.SELL,
                                   self.name, reason="시험 매도"))
            elif ctx.as_of in self._살날들 and 심볼 not in ctx.held:
                나온것.append(Signal(심볼, ctx.as_of, SignalType.BUY,
                                   self.name, score=1.0, reason="시험 매수"))
        return 나온것


def _돌리기(결제일수: int, 살날들, 팔날들):
    """자금을 한 종목에 다 넣는다. 그래야 판 돈이 잠겼는지가 드러난다."""
    return BacktestEngine(
        strategy=사고팔기(살날들, 팔날들),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy(
            max_position_weight=1.0, max_concurrent_positions=1,
        )),
        결제일수=결제일수,
    ).run(시세, trade_from=날들[-10])


# 마지막 열흘: [-10] … [-1]
사는날 = [날들[-9], 날들[-7], 날들[-5]]
파는날 = [날들[-8], 날들[-6]]


def test_즉시_결제면_판_다음_날_바로_다시_산다():
    """지금까지의 모든 백테스트 숫자가 이 조건에서 나왔다."""
    결과 = _돌리기(0, 사는날, 파는날)
    assert 결과.num_trades == 2


def test_T2면_판_다음_날에는_못_산다():
    """판 돈이 아직 안 들어와서 살 현금이 없다. 여기가 이번에 고친 자리다."""
    결과 = _돌리기(2, 사는날, 파는날)
    assert 결과.num_trades < 2, "판 다음 날 바로 다시 사면 T+2가 안 걸린 것이다"


def test_T2라도_이틀_뒤에는_살_수_있다():
    """영영 못 사는 것이 아니라 이틀 늦게 산다."""
    늦게 = [날들[-9], 날들[-5]]   # 팔고 이틀 뒤
    결과 = _돌리기(2, 늦게, [날들[-7]])
    assert 결과.num_trades >= 1
    assert len(결과.final_positions) == 1, "이틀 뒤에는 살 수 있어야 한다"


def test_판_날에_평가금액이_떨어지지_않는다():
    """잠긴 돈도 내 돈이다. 빼고 세면 파는 날마다 자산이 뚝 떨어졌다가
    이틀 뒤 돌아온다. 최대낙폭이 통째로 거짓말이 된다."""
    결과 = _돌리기(2, [날들[-9]], [날들[-8]])
    곡선 = 결과.equity_curve.set_index("trade_date")["equity"]
    판날, 그다음 = 날들[-8], 날들[-7]
    떨어진비율 = float(곡선.loc[그다음]) / float(곡선.loc[판날]) - 1
    assert abs(떨어진비율) < 0.05, (
        f"판 다음 날 평가금액이 {떨어진비율:.1%} 움직였습니다. "
        "잠긴 돈을 평가금액에서 빼고 있는 것으로 보입니다"
    )


def test_기본값은_즉시_결제다():
    """기본값을 바꾸면 지금까지 낸 모든 숫자가 다른 조건의 값이 된다."""
    기본 = inspect.signature(BacktestEngine.__init__).parameters["결제일수"].default
    assert 기본 == 0


def test_음수를_받아도_즉시_결제로_간다():
    엔진 = BacktestEngine(
        strategy=사고팔기([], []),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
        결제일수=-3,
    )
    assert 엔진._결제일수 == 0


def test_자산_곡선에_잠긴_돈이_따로_적힌다():
    """`equity - cash`를 보유 평가액으로 읽는 곳이 있다. 잠긴 돈을 안 빼면
    그것이 통째로 "아직 안 판 수익"으로 잡힌다. T+2를 켜고 처음 드러났고,
    하락장 구간에서 안판것이 +66.71%로 찍혔다."""
    결과 = _돌리기(2, [날들[-9]], [날들[-8]])
    곡선 = 결과.equity_curve.set_index("trade_date")
    assert "결제대기" in 곡선.columns
    판날 = 날들[-8]
    잠긴 = float(곡선.loc[판날, "결제대기"])
    assert 잠긴 > 0, "판 날에는 결제 대기 금액이 있어야 한다"
    보유평가액 = (
        float(곡선.loc[판날, "equity"]) - float(곡선.loc[판날, "cash"]) - 잠긴
    )
    assert abs(보유평가액) < 1.0, "다 팔았으므로 보유 평가액은 0이어야 한다"


def test_즉시_결제면_잠긴_돈이_늘_0이다():
    결과 = _돌리기(0, [날들[-9]], [날들[-8]])
    assert float(결과.equity_curve["결제대기"].abs().max()) == 0.0
