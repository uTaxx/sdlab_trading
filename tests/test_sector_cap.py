"""백테스트가 실거래처럼 줄 세우고 섹터 상한을 거는가.

## 왜 이 셋이 결과를 바꾸나

자리는 여덟인데 신호가 열 개면 둘은 못 산다. **누가 잘리느냐가 성적을
바꾼다.** 실거래는 신호 점수가 높은 순으로 세워서 위에서부터 사는데,
백테스트는 시세를 받은 순서대로 샀다. 같은 전략인데 다른 종목을 산다.

섹터 상한은 백테스트에 아예 없었다. `max_per_sector`는 `RiskPolicy`에
없는 값이라 아침 후보를 뽑는 곳만 적용하고 엔진은 섹터를 모른다. 그래서
반도체 다섯 종목이 한꺼번에 잡히는 것을 아무도 안 막았다.

## 세는 방법이 둘이다

실거래의 `cap_per_sector`는 **0부터 센다.** 이미 들고 있는 것은 안 센다.
반도체 셋을 들고 있어도 오늘 반도체 셋을 더 살 수 있다는 뜻이다. 그것이
'섹터당 3종목 보유한도'라는 말과 다르므로 두 방법을 다 잰다.
"""

from __future__ import annotations

from datetime import date

import pytest

from muwon.backtest.engine import BacktestEngine
from muwon.domain.types import Signal, SignalType
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy
from tests.price_series import make_price_df

섹터표 = {
    "반도체1": "SEMI", "반도체2": "SEMI", "반도체3": "SEMI",
    "바이오1": "BIO", "바이오2": "BIO",
}

#: 점수를 일부러 어긋나게 준다. 이름 순으로 사면 반도체1이 먼저 잡히고
#: 점수 순으로 사면 바이오2가 먼저 잡힌다.
점수표 = {"반도체1": 10.0, "반도체2": 20.0, "반도체3": 30.0,
        "바이오1": 40.0, "바이오2": 50.0}


class _다사자(PortfolioStrategy):
    """정한 날 모든 종목에 매수 신호를 낸다. 파는 신호는 안 낸다."""

    name = "다사자"
    max_holding_days = None

    def __init__(self, 살날: date):
        self._살날 = 살날

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        if ctx.as_of != self._살날:
            return []
        return [
            Signal(symbol=ㅅ, trade_date=ctx.as_of, signal_type=SignalType.BUY,
                   strategy_name=self.name, score=점수표[ㅅ], reason=f"{ㅅ} 매수")
            for ㅅ in 섹터표
            if ㅅ not in ctx.held
        ]


def _시세():
    # 종목마다 값이 달라도 되지만, 여기서 보는 것은 무엇을 샀나뿐이다.
    return {ㅅ: make_price_df([100.0 + i] * 30) for i, ㅅ in enumerate(섹터표)}


def _돌리기(**인자):
    시세 = _시세()
    날들 = list(시세["반도체1"]["trade_date"])
    정책 = RiskPolicy(max_concurrent_positions=인자.pop("동시보유", 8))
    결과 = BacktestEngine(
        strategy=_다사자(날들[10]),
        risk_manager=RiskManager(policy_provider=lambda p=정책: p),
        entry_at_open=True, exit_at_open=True,
        **인자,
    ).run(시세, trade_from=날들[5])
    return set(결과.final_positions)


def test_아무것도_안_켜면_옛_동작_그대로다():
    """기본값을 바꾸면 지금까지 낸 전략 평가 결과와 비교가 안 된다."""
    assert _돌리기() == set(섹터표), "제한이 없으면 다섯 다 사야 합니다"


def test_섹터_상한이_한_섹터에_몰리는_것을_막는다():
    산것 = _돌리기(섹터표=섹터표, 섹터상한=2, 점수순=True)

    반도체 = {ㅅ for ㅅ in 산것 if 섹터표[ㅅ] == "SEMI"}
    assert len(반도체) == 2, 산것
    assert len(산것) == 4, "바이오 둘은 상한에 안 걸립니다"


def test_섹터_상한이_점수_높은_쪽을_남긴다():
    """자르는 순서가 결과를 바꾼다. 점수가 낮은 것을 남기면 실거래가
    사는 것과 다른 종목을 산 성적이 나온다."""
    산것 = _돌리기(섹터표=섹터표, 섹터상한=1, 점수순=True)

    assert 산것 == {"반도체3", "바이오2"}, 산것


def test_점수순을_안_켜면_점수를_안_본다():
    """옛 동작이다. 시세를 받은 순서대로 산다."""
    산것 = _돌리기(섹터표=섹터표, 섹터상한=1)

    assert 산것 == {"반도체1", "바이오1"}, 산것


def test_동시보유_상한보다_신호가_많으면_점수_높은_쪽을_산다():
    산것 = _돌리기(동시보유=2, 점수순=True)

    assert 산것 == {"바이오2", "바이오1"}, 산것


def test_하루후보로_세면_이미_들고_있는_것은_안_센다():
    """실거래의 `cap_per_sector`가 0부터 센다. 반도체 둘을 들고 있어도
    오늘 반도체 둘을 더 살 수 있다는 뜻이고, 그것은 '섹터당 2종목
    보유한도'라는 말과 다르다."""
    시세 = _시세()
    날들 = list(시세["반도체1"]["trade_date"])

    # 이틀에 나눠 사게 한다. 첫날 반도체 둘, 이튿날 나머지.
    class _이틀에(PortfolioStrategy):
        name = "이틀에"
        max_holding_days = None

        def evaluate(self, ctx):
            if ctx.as_of == 날들[10]:
                살것 = ["반도체3", "반도체2"]
            elif ctx.as_of == 날들[13]:
                살것 = ["반도체1", "바이오1"]
            else:
                return []
            return [
                Signal(symbol=ㅅ, trade_date=ctx.as_of, signal_type=SignalType.BUY,
                       strategy_name=self.name, score=점수표[ㅅ], reason="시험")
                for ㅅ in 살것 if ㅅ not in ctx.held
            ]

    def 돌려(셈):
        결과 = BacktestEngine(
            strategy=_이틀에(),
            risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
            entry_at_open=True, exit_at_open=True,
            섹터표=섹터표, 섹터상한=2, 섹터상한셈=셈, 점수순=True,
        ).run(시세, trade_from=날들[5])
        return {ㅅ for ㅅ in 결과.final_positions if 섹터표[ㅅ] == "SEMI"}

    assert len(돌려("하루후보")) == 3, "실거래는 들고 있는 것을 안 셉니다"
    assert len(돌려("보유전체")) == 2, "보유 한도로 세면 셋째는 못 삽니다"


@pytest.mark.parametrize("상한", [0, -1])
def test_섹터_상한이_0이면_제한이_없다(상한):
    """시트의 max_per_sector도 0이 '제한 없음'이다."""
    assert _돌리기(섹터표=섹터표, 섹터상한=상한, 점수순=True) == set(섹터표)
