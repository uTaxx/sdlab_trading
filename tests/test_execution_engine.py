import pandas as pd

from muwon.data.universe import Ticker
from muwon.db.models import OrderRow, PositionRow, TradeRow
from muwon.db.session import make_session_factory
from muwon.domain.types import OrderSide
from muwon.execution.engine import TradingEngine
from muwon.execution.simulated_executor import SimulatedOrderExecutor
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.rule_based import MovingAverageRsiStrategy
from tests.price_series import breakout_entry_then_dead_cross_exit, flat_then_breakout

TEST_TICKER = Ticker("005930", "삼성전자", "KOSPI", "005930.KS")


class FakeDataSource:
    def __init__(self, frames: dict[str, pd.DataFrame] | None = None):
        self.frames = frames or {}

    def get_daily_ohlcv(self, symbol, start, end):
        return self.frames.get(symbol, pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"]))


class FakeNotifier:
    def __init__(self):
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)


def make_engine(
    data_source,
    policy: RiskPolicy | None = None,
    notifier=None,
    order_executor=None,
    session_factory=None,
    orderable_provider=None,
):
    """session_factory를 넘기면 **같은 DB로 이어서** 돈다.

    "어제 산 것을 들고 있는데 오늘 기준이 바뀌었다"를 재려면 상태가 이어져야
    한다. 새 엔진을 만들면서 DB까지 새로 잡으면 보유가 사라져서, 정작
    재려던 것(들고 있는 종목에 무슨 일이 일어나나)을 못 잰다."""
    policy = policy or RiskPolicy()
    session_factory = session_factory or make_session_factory("sqlite:///:memory:")
    notifier = notifier or FakeNotifier()
    engine = TradingEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=RiskManager(policy_provider=lambda: policy),
        data_source=data_source,
        order_executor=order_executor or SimulatedOrderExecutor(),
        notifier=notifier,
        session_factory=session_factory,
        universe=[TEST_TICKER],
        source_symbol=lambda ticker: ticker.symbol,
        orderable_provider=orderable_provider,
    )
    return engine, session_factory, notifier


def test_buy_signal_executes_order_persists_position_and_notifies():
    df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: df})
    engine, session_factory, notifier = make_engine(data_source)

    summary = engine.run_once()

    assert len(summary.actions) == 1
    action = summary.actions[0]
    assert action.side == OrderSide.BUY
    assert action.symbol == TEST_TICKER.symbol

    with session_factory() as session:
        positions = session.query(PositionRow).all()
        orders = session.query(OrderRow).all()
    assert len(positions) == 1
    assert positions[0].quantity == action.quantity
    assert len(orders) == 1
    assert orders[0].side == "buy"

    assert len(notifier.messages) == 1
    글 = notifier.messages[0]
    assert "🟢 매수체결" in 글
    assert TEST_TICKER.name in 글
    # 처음 쓰는 사람이 제일 먼저 묻는 둘: 얼마를 썼고, 언제 팔리나.
    assert "매수총액 : " in 글, "총액이 없으면 얼마 나갔는지 암산해야 한다"
    assert "매도전략" in 글, "언제 팔리는지 안 적으면 사람이 알 방법이 없다"
    assert "손절" in 글


def test_dead_cross_sells_existing_position_and_notifies():
    entry_df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: entry_df})
    engine, session_factory, notifier = make_engine(data_source)
    engine.run_once()  # 1일차: 진입

    exit_df = breakout_entry_then_dead_cross_exit(tail_days=0)
    data_source.frames[TEST_TICKER.symbol] = exit_df
    summary = engine.run_once()  # 며칠 뒤: 데드크로스로 청산

    assert len(summary.actions) == 1
    action = summary.actions[0]
    assert action.side == OrderSide.SELL
    assert action.reason == "단기선 하향이탈"

    with session_factory() as session:
        positions = session.query(PositionRow).all()
        orders = session.query(OrderRow).all()
        trades = session.query(TradeRow).all()
    assert len(positions) == 0
    assert len(orders) == 2  # 매수 1건 + 매도 1건

    assert len(trades) == 1
    assert trades[0].strategy_key == "ma_rsi_v1"
    assert trades[0].exit_reason == "단기선 하향이탈"
    assert trades[0].pnl_pct < 0  # 진입가보다 낮은 가격에 청산됐으므로 손실

    (매도글,) = [m for m in notifier.messages if "🔴 매도체결" in m]
    # 판 뒤에 제일 먼저 묻는 것은 "벌었나 잃었나"다. 예전 글에는 그게 없었다.
    assert "실현손익 : " in 매도글
    assert "매수단가 : " in 매도글 and "단가 : " in 매도글


def test_trading_disabled_blocks_entry_and_sends_no_notification():
    df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: df})
    policy = RiskPolicy(trading_enabled=False)
    engine, session_factory, notifier = make_engine(data_source, policy=policy)

    summary = engine.run_once()

    assert summary.actions == []
    assert len(summary.rejections) == 1
    assert "자동매매" in summary.rejections[0]
    assert notifier.messages == []

    with session_factory() as session:
        assert session.query(PositionRow).count() == 0


def test_stop_loss_sells_before_dead_cross_signal():
    entry_df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: entry_df})
    engine, _session_factory, _notifier = make_engine(data_source, policy=RiskPolicy(stop_loss_pct=-0.05))
    engine.run_once()  # 진입가 102

    crash_row = entry_df.iloc[[-1]].copy()
    crash_row["close"] = 90.0  # 진입가(102) 대비 -11.8%
    crash_row["trade_date"] = entry_df["trade_date"].iloc[-1] + pd.Timedelta(days=1)
    data_source.frames[TEST_TICKER.symbol] = pd.concat([entry_df, crash_row], ignore_index=True)

    summary = engine.run_once()

    assert len(summary.actions) == 1
    assert summary.actions[0].reason == "손절"


class ScriptedStrategy:
    """종목별로 지정한 점수의 매수 신호만 내는 전략: 선택 순서 검증용."""

    name = "scripted"

    def __init__(self, scores: dict[str, float]):
        self.scores = scores

    def generate_signals(self, symbol, price_history):
        from muwon.domain.types import Signal, SignalType

        last = price_history.iloc[-1]
        return [
            Signal(
                symbol=symbol,
                trade_date=last["trade_date"],
                signal_type=SignalType.BUY,
                strategy_name=self.name,
                reason=f"점수 {self.scores[symbol]}",
                score=self.scores[symbol],
            )
        ]


def make_multi_engine(strategy, tickers, frames, policy):
    session_factory = make_session_factory("sqlite:///:memory:")
    engine = TradingEngine(
        strategy=strategy,
        risk_manager=RiskManager(policy_provider=lambda: policy),
        data_source=FakeDataSource(frames),
        order_executor=SimulatedOrderExecutor(),
        notifier=FakeNotifier(),
        session_factory=session_factory,
        universe=tickers,
        source_symbol=lambda ticker: ticker.symbol,
    )
    return engine, session_factory


def test_buys_strongest_signal_when_slots_are_scarce():
    """자리가 하나뿐인데 신호가 셋이면 '가장 강한' 걸 사야 한다.

    정렬이 없으면 유니버스 순서(=시가총액 순)대로 앞에서부터 사게 되는데,
    종목이 60개로 늘어난 뒤에는 그게 곧 '뒤쪽 종목은 영영 못 산다'는 뜻이
    된다. 그래서 일부러 가장 약한 신호를 목록 맨 앞에 둔다."""
    from tests.price_series import make_price_df

    tickers = [
        Ticker("000001", "약한신호", "KOSPI", "000001.KS"),
        Ticker("000002", "강한신호", "KOSDAQ", "000002.KQ"),
        Ticker("000003", "중간신호", "KOSPI", "000003.KS"),
    ]
    frames = {t.symbol: make_price_df([100.0] * 5) for t in tickers}
    strategy = ScriptedStrategy({"000001": 0.1, "000002": 9.9, "000003": 0.5})

    engine, _ = make_multi_engine(
        strategy, tickers, frames, RiskPolicy(max_concurrent_positions=1)
    )
    summary = engine.run_once()

    assert len(summary.actions) == 1
    assert summary.actions[0].symbol == "000002"  # 목록상 두 번째지만 가장 강하다


def test_equal_scores_keep_universe_order():
    """점수가 같으면 기존 순서를 유지해야 한다. 정렬 도입으로 기존 동작이
    엉뚱하게 바뀌지 않는지 확인한다."""
    from tests.price_series import make_price_df

    tickers = [
        Ticker("000001", "첫째", "KOSPI", "000001.KS"),
        Ticker("000002", "둘째", "KOSPI", "000002.KS"),
    ]
    frames = {t.symbol: make_price_df([100.0] * 5) for t in tickers}
    strategy = ScriptedStrategy({"000001": 0.0, "000002": 0.0})

    engine, _ = make_multi_engine(
        strategy, tickers, frames, RiskPolicy(max_concurrent_positions=1)
    )
    summary = engine.run_once()

    assert summary.actions[0].symbol == "000001"


def test_ignores_todays_incomplete_bar():
    """장중에 돌면 오늘 봉은 거래량이 덜 쌓이고 종가도 확정 전이다.
    그 봉으로 판단하면 '거래량 2배 급증' 같은 조건이 성립할 수 없으므로,
    마지막으로 완성된 일봉까지만 써야 한다."""
    from datetime import date, timedelta

    from tests.price_series import make_price_df

    as_of = date(2026, 8, 18)
    ticker = Ticker("000001", "테스트", "KOSPI", "000001.KS")
    # 마지막 두 봉의 종가를 다르게 둬서 어느 봉을 썼는지 가격으로 구분한다
    df = make_price_df([100.0, 100.0, 100.0, 111.0, 999.0], start=as_of - timedelta(days=4))
    assert df["trade_date"].iloc[-1] == as_of  # 마지막 봉이 '오늘'

    engine, _ = make_multi_engine(
        ScriptedStrategy({"000001": 1.0}), [ticker], {ticker.symbol: df}, RiskPolicy()
    )
    summary = engine.run_once(as_of=as_of)

    assert summary.run_date == as_of - timedelta(days=1)
    assert summary.actions[0].price == 111.0  # 오늘(999)이 아니라 어제 종가로 판단


def test_active_strategy_scores_its_buy_signals():
    """점수 매기기가 실제 전략에 반영돼 있어야 정렬이 의미를 갖는다.
    전부 0이면 정렬은 그냥 기존 순서와 같아진다."""
    from muwon.domain.types import SignalType
    from muwon.strategy.registry import build_strategy

    df = flat_then_breakout(tail_days=0)
    buys = [
        s
        for s in build_strategy("ma_rsi_v1").generate_signals("005930", df)
        if s.signal_type == SignalType.BUY
    ]

    assert buys, "매수 신호가 없으면 이 테스트는 아무것도 검증하지 못한다"
    assert all(s.score > 0 for s in buys)


def test_engine_enforces_time_exit_from_actual_entry_date():
    """시간 기반 청산은 엔진이 '실제로 산 날'을 기준으로 집행해야 한다.

    전략이 스스로 보유일을 세면 엔진이 안 산 종목까지 보유 중으로 착각한다."""
    from datetime import date

    from muwon.domain.types import SignalType
    from muwon.strategy.portfolio import PortfolioStrategy
    from tests.price_series import make_price_df

    class BuyOnceThenSilent(PortfolioStrategy):
        """첫날만 매수 신호, 이후 아무 신호도 안 냄 + 3거래일 뒤 청산 선언."""

        name = "buy_once"
        max_holding_days = 3

        def __init__(self, buy_on):
            self.buy_on = buy_on

        def evaluate(self, ctx):
            from muwon.domain.types import Signal

            if ctx.as_of != self.buy_on:
                return []
            return [
                Signal(
                    symbol="000001",
                    trade_date=ctx.as_of,
                    signal_type=SignalType.BUY,
                    strategy_name=self.name,
                    reason="테스트 진입",
                )
            ]

    start = date(2026, 8, 3)
    ticker = Ticker("000001", "테스트", "KOSPI", "000001.KS")
    df = make_price_df([100.0] * 10, start=start)
    dates = list(df["trade_date"])
    frames = {ticker.symbol: df}

    engine, _ = make_multi_engine(
        BuyOnceThenSilent(buy_on=dates[4]), [ticker], frames, RiskPolicy()
    )

    # 진입일: 신호가 있는 날 다음날 실행(엔진은 완성된 마지막 봉으로 판단)
    entry_summary = engine.run_once(as_of=dates[5])
    assert [a.side for a in entry_summary.actions] == [OrderSide.BUY]

    # 2거래일 경과: 아직 청산 안 됨
    assert engine.run_once(as_of=dates[7]).actions == []

    # 3거래일 경과: 청산
    exit_summary = engine.run_once(as_of=dates[8])
    assert [a.side for a in exit_summary.actions] == [OrderSide.SELL]
    assert "보유 3일 경과" in exit_summary.actions[0].reason


def _runs(session_factory):
    from sqlalchemy import select

    from muwon.db.models import RunLogRow

    with session_factory() as session:
        return session.scalars(select(RunLogRow).order_by(RunLogRow.id)).all()


def test_a_quiet_day_still_leaves_a_record():
    """살 게 없던 날과 아예 안 돈 날은 대시보드에서 똑같이 '빈 화면'이다.

    오늘 실제로 그 구분이 안 돼서, 운영 DB를 통째로 받아 보고 나서야
    '기록이 유실된 게 아니라 한 번도 안 샀다'는 걸 알았다. 체결이 없어도
    한 줄은 남아야 한다."""
    df = flat_then_breakout(tail_days=0)
    flat = df.copy()
    flat["close"] = 50_000.0  # 아무 신호도 안 나는 평평한 시세
    flat["open"] = flat["high"] = flat["low"] = 50_000.0
    engine, session_factory, _ = make_engine(FakeDataSource({TEST_TICKER.symbol: flat}))

    summary = engine.run_once()

    assert not summary.actions
    rows = _runs(session_factory)
    assert len(rows) == 1
    assert rows[0].checked_symbols == 1
    assert rows[0].orders == 0
    assert rows[0].universe_size == 1


def test_no_price_data_is_recorded_as_such_not_as_silence():
    """시세를 하나도 못 받은 회차는 '조용한 날'이 아니라 공급이 끊긴 날이다."""
    engine, session_factory, _ = make_engine(FakeDataSource({}))

    engine.run_once()

    rows = _runs(session_factory)
    assert len(rows) == 1
    assert rows[0].run_date is None
    assert rows[0].checked_symbols == 0


def test_signals_are_persisted_so_we_can_ask_why_nothing_was_bought():
    """signals 테이블은 스키마에만 있고 아무도 쓰지 않았다. 그래서 '0건'이
    신호가 없었다는 뜻인지 기록을 안 했다는 뜻인지 알 수 없었다."""
    from sqlalchemy import select

    from muwon.db.models import SignalRow

    df = flat_then_breakout(tail_days=0)
    engine, session_factory, _ = make_engine(FakeDataSource({TEST_TICKER.symbol: df}))

    engine.run_once()

    with session_factory() as session:
        signals = session.scalars(select(SignalRow)).all()
    assert [s.signal_type for s in signals] == ["buy"]
    assert signals[0].symbol == TEST_TICKER.symbol
    assert _runs(session_factory)[0].buy_signals == 1


def test_a_blocked_signal_records_the_reason():
    """신호는 났는데 주문이 없으면 이유가 어딘가 남아야 한다. 안 그러면
    '신호가 없었다'와 구분이 안 된다."""
    df = flat_then_breakout(tail_days=0)
    engine, session_factory, _ = make_engine(
        FakeDataSource({TEST_TICKER.symbol: df}),
        policy=RiskPolicy(trading_enabled=False),
    )

    engine.run_once()

    row = _runs(session_factory)[0]
    assert row.buy_signals == 1
    assert row.orders == 0
    assert row.rejections, "막은 이유가 비어 있으면 안 된다"


# ── 매도 스위치 (2026-08-25) ──────────────────────────────────
#
# 대시보드에서 매수와 매도를 따로 끌 수 있게 됐다. 매도를 끄면 손절도
# 익절도 보유일수 청산도 전부 멈춘다. **값이 반토막 나도 아무 일도
# 안 일어난다.** 이 저장소가 최악으로 꼽는 모양이라 시험으로 못 박는다.


def test_매도가_꺼지면_데드크로스_청산도_안_한다():
    entry_df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: entry_df})
    policy = RiskPolicy()
    engine, session_factory, _ = make_engine(data_source, policy=policy)
    engine.run_once()  # 진입

    # 여기서 사람이 대시보드에서 매도를 끈다
    policy = RiskPolicy(sell_enabled=False)
    data_source.frames[TEST_TICKER.symbol] = breakout_entry_then_dead_cross_exit(tail_days=0)
    engine, _, _ = make_engine(
        data_source, policy=policy, session_factory=session_factory
    )
    summary = engine.run_once()

    assert not any(a.side == OrderSide.SELL for a in summary.actions)
    with session_factory() as session:
        assert session.query(PositionRow).count() == 1, "청산되면 안 된다"


def test_매도가_꺼지면_보유가_있을_때_크게_알린다():
    """조용히 멈추면 안 된다. 손절이 안 걸리는 상태라는 걸 사람이 알아야 한다."""
    entry_df = flat_then_breakout(tail_days=0)
    data_source = FakeDataSource({TEST_TICKER.symbol: entry_df})
    engine, session_factory, _ = make_engine(data_source)
    engine.run_once()

    policy = RiskPolicy(sell_enabled=False)
    engine, _, notifier = make_engine(
        data_source, policy=policy, session_factory=session_factory
    )
    summary = engine.run_once()

    assert any("매도" in r and "멈춰" in r for r in summary.rejections)
    (경고,) = [m for m in notifier.messages if "🛑" in m]
    assert "자동으로" in 경고 and "팔리지 않습니다" in 경고
    assert TEST_TICKER.name in 경고, "종목코드만 있으면 무슨 주식인지 모른다"
    assert "'자동 매도' 스위치를 켜세요" in 경고, "어디를 눌러야 하는지까지 있어야 한다"


def test_매도가_꺼져도_보유가_없으면_안_시끄럽다():
    """들고 있는 것이 없으면 손절이 멈춰도 잃을 것이 없다. 알림을 아낀다."""
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    policy = RiskPolicy(sell_enabled=False)
    engine, _, notifier = make_engine(data_source, policy=policy)

    engine.run_once()

    assert not any("손절이 안 걸립니다" in m for m in notifier.messages)


def test_매도가_꺼져도_매수는_그대로_돈다():
    """두 스위치는 서로 독립이다."""
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    policy = RiskPolicy(sell_enabled=False, trading_enabled=True)
    engine, session_factory, _ = make_engine(data_source, policy=policy)

    summary = engine.run_once()

    assert any(a.side == OrderSide.BUY for a in summary.actions)
    with session_factory() as session:
        assert session.query(PositionRow).count() == 1


# ── 증권사 매수가능수량 (2026-08-25) ──────────────────────────
#
# 우리 기준은 "얼마나 사고 싶은가"(비중 상한)를, 증권사는 "얼마나 살 수
# 있는가"(증거금율 반영)를 정한다. 둘 중 작은 쪽을 산다.
#
# 이걸 안 물어보면 우리가 스스로 센 현금 위에서만 판단하는데, 그 값은
# 부분 체결·거부·손매매로 조용히 어긋난다.


def test_증권사가_적게_준다고_하면_그만큼만_산다():
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    engine, session_factory, _ = make_engine(
        data_source, orderable_provider=lambda symbol, price: 3
    )

    summary = engine.run_once()

    assert summary.actions[0].quantity == 3
    assert any("매수가능수량이 3주" in r for r in summary.rejections)
    with session_factory() as session:
        assert session.query(PositionRow).one().quantity == 3


def test_증권사가_0이라고_하면_안_산다():
    """현금이나 증거금이 모자란 것이다. 주문을 내 봐야 거부당한다."""
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    engine, session_factory, _ = make_engine(
        data_source, orderable_provider=lambda symbol, price: 0
    )

    summary = engine.run_once()

    assert summary.actions == []
    assert any("0주로 알려 줬습니다" in r for r in summary.rejections)
    # 알림이 "왜 안 샀는지"에 답하려면 종목별로 짝이 지어져 있어야 한다.
    assert summary.거부사유[TEST_TICKER.symbol].startswith("증권사가 살 수 있는 수량을 0주로")
    with session_factory() as session:
        assert session.query(PositionRow).count() == 0


def test_증권사가_넉넉하다고_하면_우리_기준대로_산다():
    """증권사 값은 **상한**이지 목표가 아니다. 살 수 있다고 다 사면
    비중 상한이 무의미해진다."""
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    없이, _, _ = make_engine(data_source)
    기대수량 = 없이.run_once().actions[0].quantity

    engine, _, _ = make_engine(
        FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)}),
        orderable_provider=lambda symbol, price: 999_999,
    )

    assert engine.run_once().actions[0].quantity == 기대수량


def test_못_물어보면_예전처럼_우리_현금으로_간다():
    """조회 한 번 실패한 것이 그날 매수를 통째로 막으면 안 된다.
    -1은 '살 수 없다'가 아니라 '못 물어봤다'다."""
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    engine, _, _ = make_engine(
        data_source, orderable_provider=lambda symbol, price: -1
    )

    assert engine.run_once().actions[0].quantity > 0


def test_조회가_터져도_매매가_안_멈춘다():
    data_source = FakeDataSource({TEST_TICKER.symbol: flat_then_breakout(tail_days=0)})
    def 터짐(symbol, price):
        raise RuntimeError("증권사가 안 받음")

    engine, _, _ = make_engine(data_source, orderable_provider=터짐)

    assert engine.run_once().actions[0].quantity > 0
