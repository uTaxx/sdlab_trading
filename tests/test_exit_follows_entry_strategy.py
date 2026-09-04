"""청산은 **그 종목을 산 전략**을 따른다.

## 무엇이 문제였나 (2026-09-02)

청산 판단이 지금 걸린 전략 하나를 봤다. 그래서 전략을 바꾸는 순간 이미 들고
있던 종목의 규칙이 발밑에서 바뀌었다.

그날 아침 08:20에 보유 상한 1거래일짜리 전략이 반영됐고, 5거래일 계획으로
들어간 종목들이 45분 뒤 09:05에 한꺼번에 정리됐다. 손절이 걸린 것도 매도
신호가 난 것도 아니고 규칙이 바뀐 것이다.

살 때 이미 "며칠 들고 언제 판다"가 정해져 있었으므로 그것을 따른다. 보유
기간, 익절선, 매도 신호를 한 전략에서 뽑아야 "왜 팔렸나"에 답할 때 종목
하나에 전략 하나만 보면 된다.

여기 시험이 실거래 엔진과 백테스트 엔진 양쪽을 같이 본다. 한쪽만 고치면
백테스트 숫자가 실거래를 설명하지 못한다.
"""

from __future__ import annotations

from muwon.backtest.engine import BacktestEngine
from muwon.data.universe import Ticker
from muwon.db.models import PositionRow
from muwon.db.session import make_session_factory
from muwon.execution.engine import TradingEngine
from muwon.execution.simulated_executor import SimulatedOrderExecutor
from muwon.risk.exits import 익절기준
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import PortfolioStrategy
from muwon.strategy.registry import build_strategy, get_definition
from tests.price_series import flat_then_breakout

티커 = Ticker("000001", "테스트", "KOSPI", "000001.KS")


class 가짜시세:
    def __init__(self, df):
        self.df = df

    def get_daily_ohlcv(self, symbol, start, end):
        return self.df


class 가짜알림:
    def send(self, message: str) -> None:
        pass


class 조용한전략(PortfolioStrategy):
    """아무 신호도 안 낸다. 보유 상한만 갖고 있다."""

    def __init__(self, 이름: str, 보유일: int | None, 익절: float = 0.0):
        self.name = 이름
        self.max_holding_days = 보유일
        self.take_profit_pct = 익절

    def prepare(self, histories):
        pass

    def evaluate(self, ctx):
        return []


def _엔진(지금전략, 미리보유키: str, 보유일전: int):
    """어제 `미리보유키`로 산 종목 하나를 들고 오늘을 맞는다."""
    df = flat_then_breakout(tail_days=0)
    session_factory = make_session_factory("sqlite:///:memory:")
    날들 = list(df["trade_date"])
    with session_factory() as session:
        session.add(PositionRow(
            symbol=티커.symbol, quantity=1,
            entry_price=float(df["close"].iloc[-1 - 보유일전]),
            entry_date=날들[-1 - 보유일전],
            entry_reason="시험", strategy_key=미리보유키,
        ))
        session.commit()
    return TradingEngine(
        strategy=지금전략,
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy(sell_enabled=True)),
        data_source=가짜시세(df),
        order_executor=SimulatedOrderExecutor(),
        notifier=가짜알림(),
        session_factory=session_factory,
        universe=[티커],
        source_symbol=lambda t: t.symbol,
    )


def 판것(summary):
    return [a for a in summary.actions if a.side.value == "sell"]


# ── 실거래 엔진 ──────────────────────────────────────────────────


def test_전략을_바꿔도_산_전략의_보유기간을_따른다():
    """여기가 09-02에 실제로 터진 자리다.

    5거래일 계획으로 산 종목을 상한 1거래일짜리 전략으로 바꾸고 돌린다.
    옛 코드는 그 자리에서 팔았다."""
    지금 = 조용한전략("gap_up_go", 보유일=1)
    summary = _엔진(지금, 미리보유키="volume_surge_5d", 보유일전=2).run_once()
    assert 판것(summary) == [], "산 전략의 상한(5거래일)이 아직 안 됐으므로 안 판다"


def test_산_전략의_보유기간이_되면_판다():
    지금 = 조용한전략("golden_cross_20_60", 보유일=None)
    summary = _엔진(지금, 미리보유키="volume_surge_5d", 보유일전=6).run_once()
    팔림 = 판것(summary)
    assert len(팔림) == 1
    assert "보유 상한 5거래일" in 팔림[0].reason


def test_산_전략을_못_찾으면_지금_전략으로_간다():
    """묶은 전략이나 옛 기록은 등록된 키가 아니라 만들 수 없다.
    조용히 안 팔면 손절이 멈춘 것과 같아서, 지금 전략으로라도 판단한다."""
    지금 = 조용한전략("아무거나", 보유일=1)
    summary = _엔진(지금, 미리보유키="등록안된키", 보유일전=3).run_once()
    팔림 = 판것(summary)
    assert len(팔림) == 1
    assert "보유 상한 1거래일" in 팔림[0].reason


def test_산_전략의_익절선을_따른다():
    """전략마다 익절선을 다르게 둘 수 있다. 기초설정은 0(안 정함)이다."""
    df = flat_then_breakout(tail_days=0)
    날들 = list(df["trade_date"])
    session_factory = make_session_factory("sqlite:///:memory:")
    with session_factory() as session:
        session.add(PositionRow(
            symbol=티커.symbol, quantity=1,
            entry_price=float(df["close"].iloc[-1]) * 0.8,  # 25% 올라 있다
            entry_date=날들[-1], entry_reason="시험", strategy_key="익절전략",
        ))
        session.commit()

    지금 = 조용한전략("지금전략", 보유일=None, 익절=0.0)
    엔진 = TradingEngine(
        strategy=지금,
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy(sell_enabled=True)),
        data_source=가짜시세(df),
        order_executor=SimulatedOrderExecutor(),
        notifier=가짜알림(),
        session_factory=session_factory,
        universe=[티커],
        source_symbol=lambda t: t.symbol,
    )
    # 산 전략을 직접 끼워 준다. 등록된 전략 중에는 아직 익절선을 정한 것이
    # 없어서, 레지스트리로는 이 갈림길을 만들 수 없다.
    엔진._청산전략표 = lambda positions: {"익절전략": 조용한전략("익절전략", None, 0.10)}
    팔림 = 판것(엔진.run_once())
    assert len(팔림) == 1
    assert 팔림[0].reason.startswith("익절")


# ── 익절 기준을 정하는 자리 ──────────────────────────────────────


def test_기초설정이_전략의_익절선을_덮는다():
    전략 = 조용한전략("x", None, 익절=0.08)
    assert 익절기준(전략, RiskPolicy(take_profit_pct=0.15)) == 0.15


def test_기초설정이_0이면_전략이_정한_대로():
    전략 = 조용한전략("x", None, 익절=0.08)
    assert 익절기준(전략, RiskPolicy(take_profit_pct=0.0)) == 0.08


def test_둘_다_0이면_익절을_안_건다():
    전략 = 조용한전략("x", None, 익절=0.0)
    assert 익절기준(전략, RiskPolicy(take_profit_pct=0.0)) == 0.0


def test_익절선을_정한_전략이_어느_것인지_고정한다():
    """2026-09-04까지는 하나도 없었다. 그날 익절선만 다른 여섯을 등록했다.

    익절선은 실제 매매 규칙을 바꾼다. 목록을 여기에 적어 두면, 값이 늘거나
    줄 때 그것이 의도한 변경인지 한 번 묻게 된다."""
    from muwon.strategy.registry import list_definitions

    정한것 = {d.key: d.익절 for d in list_definitions() if d.익절}
    assert 정한것 == {
        "volume_surge_3d_tp5": 0.05,
        "volume_surge_3d_tp8": 0.08,
        "volume_surge_3d_tp12": 0.12,
        "gap_up_go_tp5": 0.05,
        "gap_up_go_tp8": 0.08,
        "gap_up_go_tp12": 0.12,
    }, 정한것


def test_익절선을_정한_전략의_대조군은_익절선이_없다():
    """대조군에 익절선이 붙으면 무엇 때문에 달라졌는지 알 수 없다."""
    from muwon.strategy.registry import get_definition

    for 키 in ("volume_surge_3d", "gap_up_go"):
        assert get_definition(키).익절 == 0.0, 키


def test_정의에_적은_익절선이_전략_객체에_붙는다():
    정의 = get_definition("gap_up_go")
    object.__setattr__(정의, "익절", 0.07)
    try:
        assert getattr(build_strategy("gap_up_go"), "take_profit_pct", 0.0) == 0.07
    finally:
        object.__setattr__(정의, "익절", 0.0)


# ── 껍데기가 익절선을 삼키지 않는가 (2026-09-04) ──────────────────
#
# 여기가 비어 있어서 익절선이 조용히 사라졌다. 정의에 적은 값이 전략 객체에
# 붙는 것까지만 시험하고, 그 객체가 엔진에 들어가는 길은 안 봤다.
#
# 두 엔진 다 단일 종목 전략을 `SingleSymbolAdapter`로 감싸서 쓴다. 그
# 껍데기가 보유 상한은 옮겨 오고 익절선은 안 옮겼다. 그래서 익절선을 5%로
# 걸든 12%로 걸든 매매가 똑같이 나왔다. 아무것도 빨개지지 않았다.


class 첫날사는단일전략:
    """PortfolioStrategy가 아닌 옛 방식 전략. 껍데기를 거쳐 엔진에 들어간다."""

    def __init__(self, 이름, 익절=0.0, 보유일=None):
        self.name = 이름
        self.take_profit_pct = 익절
        self.max_holding_days = 보유일

    def generate_signals(self, symbol, df):
        from muwon.domain.types import Signal, SignalType

        날 = df["trade_date"].iloc[0]
        return [Signal(symbol, 날, SignalType.BUY, self.name, score=1.0, reason="시험 매수")]


def test_껍데기가_익절선을_그대로_넘긴다():
    from muwon.strategy.portfolio import as_portfolio_strategy

    껍데기 = as_portfolio_strategy(첫날사는단일전략("시험", 익절=0.07))
    assert 껍데기.take_profit_pct == 0.07
    assert 익절기준(껍데기, RiskPolicy(take_profit_pct=0.0)) == 0.07


def test_백테스트에서_전략의_익절선이_실제로_걸린다():
    """붙어 있는 것만으로는 모자란다. 매매가 익절로 끝나야 한다."""
    from tests.price_series import make_price_df

    # 첫날 100에 사고 그다음 계속 오른다. 익절선 5%는 105에서 걸린다.
    시세 = {"000001": make_price_df([100.0, 102.0, 104.0, 106.0, 108.0, 110.0])}
    날들 = list(시세["000001"]["trade_date"])

    def 돌리기(익절):
        return BacktestEngine(
            strategy=첫날사는단일전략("시험", 익절=익절),
            risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
        ).run(시세, trade_from=날들[0])

    걸린것 = 돌리기(0.05)
    assert 걸린것.closed_trades, "익절선을 걸었으면 팔려야 한다"
    assert "익절" in 걸린것.closed_trades[0].exit_reason

    안건것 = 돌리기(0.0)
    assert not 안건것.closed_trades, "익절선이 없으면 이 시세에서는 안 팔린다"


def test_익절선이_다르면_판_가격도_다르다():
    """5%와 12%가 같은 값에 팔리면 익절선이 안 걸린 것이다. 실제로 그랬다."""
    from tests.price_series import make_price_df

    시세 = {"000001": make_price_df([100.0, 103.0, 106.0, 109.0, 112.0, 115.0, 118.0])}
    날들 = list(시세["000001"]["trade_date"])

    def 판값(익절):
        결과 = BacktestEngine(
            strategy=첫날사는단일전략("시험", 익절=익절),
            risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
        ).run(시세, trade_from=날들[0])
        assert 결과.closed_trades, f"익절 {익절}에서 안 팔렸습니다"
        return 결과.closed_trades[0].exit_price

    assert 판값(0.05) < 판값(0.12)


# ── 백테스트 엔진 ────────────────────────────────────────────────


class 하루만사는전략(PortfolioStrategy):
    """첫날 하나 사고 그 뒤로는 아무 신호도 안 낸다."""

    def __init__(self, 이름, 보유일, 살날=None):
        self.name = 이름
        self.max_holding_days = 보유일
        self._살날 = 살날

    def prepare(self, histories):
        pass

    def evaluate(self, ctx):
        from muwon.domain.types import Signal, SignalType

        if self._살날 is not None and ctx.as_of == self._살날 and not ctx.held:
            return [Signal("000001", ctx.as_of, SignalType.BUY, self.name,
                           score=1.0, reason="시험 매수")]
        return []


def test_백테스트도_산_전략의_보유기간을_따른다():
    """실행 중간에 전략이 바뀌어도 이미 산 것은 옛 상한으로 간다."""
    df = flat_then_breakout(tail_days=0)
    날들 = list(df["trade_date"])
    시세 = {"000001": df}

    산전략 = 하루만사는전략("산쪽", 보유일=5, 살날=날들[-8])
    바꾼전략 = 하루만사는전략("바꾼쪽", 보유일=1)

    class 갈아타기흉내(PortfolioStrategy):
        """살 때는 산전략, 그 뒤에는 바꾼전략으로 넘긴다."""

        name = "갈아타기흉내"
        max_holding_days = None

        def __init__(self):
            self._지금 = 산전략

        @property
        def 오늘전략(self):
            return self._지금

        def prepare(self, histories):
            산전략.prepare(histories)
            바꾼전략.prepare(histories)

        def evaluate(self, ctx):
            결과 = self._지금.evaluate(ctx)
            if ctx.as_of >= 날들[-7]:
                self._지금 = 바꾼전략
            return 결과

    결과 = BacktestEngine(
        strategy=갈아타기흉내(),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    ).run(시세, trade_from=날들[-9])

    assert 결과.num_trades == 1
    거래 = 결과.closed_trades[0]
    assert "보유 상한 5거래일" in 거래.exit_reason, (
        "산 전략의 상한 5거래일로 팔려야 한다. 바꾼 전략의 1거래일이 아니다"
    )


def test_실행_내내_전략이_하나면_예전과_같다():
    """전략이 안 바뀌는 대부분의 실행에서는 값이 달라지지 않아야 한다."""
    df = flat_then_breakout(tail_days=0)
    날들 = list(df["trade_date"])
    전략 = 하루만사는전략("하나", 보유일=3, 살날=날들[-8])
    결과 = BacktestEngine(
        strategy=전략,
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    ).run({"000001": df}, trade_from=날들[-9])
    assert 결과.num_trades == 1
    assert "보유 상한 3거래일" in 결과.closed_trades[0].exit_reason


def test_보유_기록에_산_전략이_남는다():
    """안 팔린 채로 끝난 종목에도 산 전략이 적혀 있어야 한다. 그래야 다음
    날 청산이 그것을 볼 수 있다."""
    df = flat_then_breakout(tail_days=0)
    날들 = list(df["trade_date"])
    전략 = 하루만사는전략("하나", 보유일=None, 살날=날들[-4])
    결과 = BacktestEngine(
        strategy=전략,
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy()),
    ).run({"000001": df}, trade_from=날들[-5])
    보유 = 결과.final_positions
    assert set(보유) == {"000001"}
    assert 보유["000001"].전략 is 전략
