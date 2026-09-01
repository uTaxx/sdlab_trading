"""새로 추가한 전략 계열(추세추종·평균회귀·돌파)의 신호 판정을 검증한다.

합성 가격 시계열로 "이 패턴에서는 이 신호가 나와야 한다"를 확인하며,
특히 돌파 계열은 미래참조(오늘 값으로 오늘 신고가 돌파를 판정해 항상
참이 되는 버그)가 없는지를 중점적으로 본다."""

import pandas as pd
import pytest

from muwon.domain.types import SignalType
from muwon.strategy.breakout import (
    BollingerBreakoutParams,
    BollingerBreakoutStrategy,
    PriceChannelBreakoutStrategy,
    PriceChannelParams,
    VolumeSurgeParams,
    VolumeSurgeStrategy,
)
from muwon.strategy.registry import (
    CATEGORIES,
    REGISTRY,
    build_strategy,
    get_definition,
    list_definitions,
)
from muwon.strategy.reversion import (
    BollingerReversionParams,
    BollingerReversionStrategy,
    RsiReversionParams,
    RsiReversionStrategy,
    StochasticParams,
    StochasticStrategy,
)
from muwon.strategy.trend import (
    DonchianBreakoutParams,
    DonchianBreakoutStrategy,
    EmaCrossParams,
    EmaCrossStrategy,
    GoldenCrossParams,
    GoldenCrossStrategy,
    MacdCrossParams,
    MacdCrossStrategy,
)
from tests.price_series import make_price_df


def buys(signals):
    return [s for s in signals if s.signal_type == SignalType.BUY]


def sells(signals):
    return [s for s in signals if s.signal_type == SignalType.SELL]


def rising_then_falling(
    flat_days: int = 40, rise_days: int = 60, fall_days: int = 60, start: float = 100.0
):
    """횡보하다 꾸준히 오르고 다시 꾸준히 내리는 시계열: 추세추종 계열이
    매수 후 매도 신호를 한 번씩 내야 하는 가장 기본적인 패턴.

    앞의 횡보 구간이 반드시 필요하다: 처음부터 상승으로 시작하면 이동평균
    두 개가 정의되는 시점에 이미 단기선이 장기선 위에 있어서 "교차"라는
    사건 자체가 발생하지 않는다."""
    flat = [start + (0.4 if i % 2 == 0 else -0.4) for i in range(flat_days)]
    up = [start + i for i in range(rise_days)]
    peak = up[-1]
    down = [peak - i for i in range(1, fall_days + 1)]
    return make_price_df(flat + up + down)


# ────────────────────────── 추세추종 ──────────────────────────


def test_golden_cross_buys_on_uptrend_and_sells_on_downtrend():
    df = rising_then_falling()
    signals = GoldenCrossStrategy(GoldenCrossParams(5, 20)).generate_signals("TEST", df)

    assert len(buys(signals)) >= 1
    assert len(sells(signals)) >= 1
    assert buys(signals)[0].trade_date < sells(signals)[0].trade_date  # 매수가 먼저
    assert "골든크로스" in buys(signals)[0].reason


def test_ema_cross_reacts_earlier_than_sma_cross():
    """지수이동평균은 최근 가격에 가중치를 주므로 같은 창 길이에서 단순
    이동평균보다 먼저 교차해야 한다."""
    df = rising_then_falling()
    ema_buys = buys(EmaCrossStrategy(EmaCrossParams(5, 20)).generate_signals("TEST", df))
    sma_buys = buys(GoldenCrossStrategy(GoldenCrossParams(5, 20)).generate_signals("TEST", df))

    assert ema_buys and sma_buys
    assert ema_buys[0].trade_date <= sma_buys[0].trade_date


def test_macd_cross_produces_both_directions():
    df = rising_then_falling()
    signals = MacdCrossStrategy(MacdCrossParams()).generate_signals("TEST", df)
    assert buys(signals) and sells(signals)


def test_macd_positive_filter_reduces_buy_count():
    """0선 위에서의 교차만 인정하면 매수 신호는 줄어들기만 해야 한다."""
    df = rising_then_falling(rise_days=40, fall_days=40)
    plain = buys(MacdCrossStrategy(MacdCrossParams()).generate_signals("TEST", df))
    filtered = buys(
        MacdCrossStrategy(MacdCrossParams(require_positive_macd=True)).generate_signals("TEST", df)
    )
    assert len(filtered) <= len(plain)
    for signal in filtered:
        assert signal.reason == "MACD 신호선 상향돌파"


def test_donchian_does_not_look_ahead_on_flat_series():
    """완전 횡보(가격 변화 없음)에서는 신고가 돌파가 있을 수 없다. 당일
    고가를 포함해 채널을 계산하면 매일 "신고가 돌파"로 잡히는 미래참조
    버그가 생기는데, 그걸 잡는 회귀 테스트다."""
    df = make_price_df([100.0] * 80)
    signals = DonchianBreakoutStrategy(DonchianBreakoutParams(20, 10)).generate_signals("TEST", df)
    assert buys(signals) == []


def test_donchian_buys_on_new_high_breakout():
    closes = [100.0] * 40 + [120.0] + [121.0] * 10
    df = make_price_df(closes)
    signals = DonchianBreakoutStrategy(DonchianBreakoutParams(20, 10)).generate_signals("TEST", df)

    assert len(buys(signals)) >= 1
    assert "신고가 돌파" in buys(signals)[0].reason


def test_donchian_adx_filter_only_reduces_buys():
    df = rising_then_falling()
    plain = buys(
        DonchianBreakoutStrategy(DonchianBreakoutParams(20, 10)).generate_signals("TEST", df)
    )
    filtered = buys(
        DonchianBreakoutStrategy(
            DonchianBreakoutParams(20, 10, adx_filter=25)
        ).generate_signals("TEST", df)
    )
    assert len(filtered) <= len(plain)


# ────────────────────────── 평균회귀 ──────────────────────────


def test_rsi_reversion_buys_on_bounce_above_long_ma():
    """장기 이동평균 위에서 급락 후 반등: 평균회귀가 노리는 전형적 패턴.

    가파른 상승으로 장기선을 한참 아래 남겨둔 뒤 8일 연속 하락시키면
    RSI가 26까지 떨어지고, 반등일에 30을 다시 넘으면서도 종가는 여전히
    장기선 위라 필터를 통과한다(실제 지표값으로 확인한 구성)."""
    closes = [100.0 + i * 2.0 for i in range(80)]
    closes += [closes[-1] * 0.97**n for n in range(1, 9)]  # 8일 연속 -3% → RSI 26
    closes.append(closes[-1] * 1.06)  # 반등 → RSI 30 상향돌파
    closes += [closes[-1] * 1.01] * 3
    df = make_price_df(closes)

    signals = RsiReversionStrategy(RsiReversionParams()).generate_signals("TEST", df)
    assert any("과매도" in s.reason for s in buys(signals))


def test_rsi_reversion_long_ma_filter_blocks_falling_knife():
    """장기 이동평균 아래(=하락 추세)에서는 필터가 켜져 있으면 매수하지
    않아야 한다. 계속 흘러내리는 종목을 받아내는 걸 막는 장치."""
    closes = [200.0 - i * 1.5 for i in range(80)]  # 지속 하락
    closes += [closes[-1] * 1.05] * 5  # 잠깐 반등
    df = make_price_df(closes)

    filtered = RsiReversionStrategy(RsiReversionParams(require_above_long_ma=True))
    unfiltered = RsiReversionStrategy(RsiReversionParams(require_above_long_ma=False))

    assert buys(filtered.generate_signals("TEST", df)) == []
    assert len(buys(unfiltered.generate_signals("TEST", df))) >= len(
        buys(filtered.generate_signals("TEST", df))
    )


def test_bollinger_reversion_buys_on_lower_band_recovery():
    closes = [100.0] * 40 + [80.0] + [95.0] + [100.0] * 5  # 급락 후 밴드 안으로 복귀
    df = make_price_df(closes)
    signals = BollingerReversionStrategy(BollingerReversionParams()).generate_signals("TEST", df)
    assert any("하단 이탈 후 복귀" in s.reason for s in buys(signals))


def test_bollinger_reversion_and_breakout_disagree_on_upper_band():
    """같은 상단 밴드 돌파를 평균회귀는 청산 신호로, 돌파 전략은 매수
    신호로 해석해야 한다. 두 해석을 각각 가설로 둔 이유를 고정한다."""
    closes = [100.0] * 40 + [130.0] + [131.0] * 5
    volumes = [100_000] * 40 + [500_000] + [120_000] * 5
    df = make_price_df(closes, volumes)

    reversion = BollingerReversionStrategy(
        BollingerReversionParams(exit_at_middle=False)
    ).generate_signals("TEST", df)
    breakout = BollingerBreakoutStrategy(BollingerBreakoutParams()).generate_signals("TEST", df)

    assert any("상단 도달" in s.reason for s in sells(reversion))
    assert any("상단 돌파" in s.reason for s in buys(breakout))


def test_stochastic_signals_stay_within_configured_zones():
    df = rising_then_falling()
    params = StochasticParams()
    signals = StochasticStrategy(params).generate_signals("TEST", df)
    for s in buys(signals):
        assert "과매도" in s.reason
    for s in sells(signals):
        assert "과매수" in s.reason


# ────────────────────────── 돌파·모멘텀 ──────────────────────────


def test_bollinger_breakout_requires_volume_surge():
    """거래량이 안 터지면 상단을 뚫어도 매수하지 않아야 한다."""
    closes = [100.0] * 40 + [130.0] + [131.0] * 5
    quiet_volumes = [100_000] * 46  # 거래량 변화 없음
    df_quiet = make_price_df(closes, quiet_volumes)

    signals = BollingerBreakoutStrategy(BollingerBreakoutParams()).generate_signals("TEST", df_quiet)
    assert buys(signals) == []


def test_volume_surge_declares_time_exit_instead_of_emitting_it():
    """이 전략만 청산이 지표가 아니라 시간 기준인데, 그 집행은 전략이 아니라
    엔진의 몫이다.

    전에는 전략이 스스로 '보유 중'을 기억하고 매도 신호까지 냈다. 그 상태는
    엔진이 실제로 샀는지와 무관해서, 리스크 한도로 매수가 거부된 종목도
    전략은 샀다고 믿고 그 뒤 며칠간 신호를 막았다. 이제 전략은 '며칠 뒤
    나가야 한다'는 사실만 선언하고, 실제 보유일 계산은 엔진이 한다."""
    closes = [100.0] * 30 + [110.0] + [110.0] * 20
    volumes = [100_000] * 30 + [400_000] + [100_000] * 20
    df = make_price_df(closes, volumes)

    strategy = VolumeSurgeStrategy(VolumeSurgeParams(holding_days=5))
    signals = strategy.generate_signals("TEST", df)

    assert strategy.max_holding_days == 5
    assert len(buys(signals)) == 1
    assert sells(signals) == [], "청산은 엔진이 집행하므로 전략이 매도 신호를 내면 안 된다"


def test_volume_surge_no_longer_suppresses_later_entries():
    """자리가 없어 못 산 종목이 이후 기회까지 잃던 결함의 회귀 테스트.

    급등이 두 번 오면 신호도 두 번 나야 한다. 실제로 살지 말지는 엔진이
    보유 현황과 리스크 한도를 보고 정할 일이다."""
    closes = [100.0] * 21 + [110.0, 110.0, 110.0, 125.0] + [125.0] * 5
    volumes = [100_000] * 21 + [500_000, 100_000, 100_000, 500_000] + [100_000] * 5
    df = make_price_df(closes, volumes)

    signals = VolumeSurgeStrategy(VolumeSurgeParams(holding_days=5)).generate_signals("TEST", df)

    assert len(buys(signals)) == 2


def test_volume_surge_ignores_volume_without_price_move():
    """거래량만 터지고 가격은 그대로면 매수하지 않아야 한다."""
    closes = [100.0] * 50
    volumes = [100_000] * 30 + [500_000] + [100_000] * 19
    df = make_price_df(closes, volumes)

    signals = VolumeSurgeStrategy(VolumeSurgeParams()).generate_signals("TEST", df)
    assert buys(signals) == []


def test_price_channel_does_not_look_ahead_on_flat_series():
    df = make_price_df([100.0] * 80)
    signals = PriceChannelBreakoutStrategy(PriceChannelParams()).generate_signals("TEST", df)
    assert buys(signals) == []


def test_price_channel_breakout_pct_filters_marginal_breaks():
    """신고가를 아슬아슬하게(0.5%) 넘긴 경우, 1% 문턱을 건 가설은 무시해야 한다."""
    closes = [100.0] * 30 + [100.5] + [100.5] * 5
    df = make_price_df(closes)

    loose = PriceChannelBreakoutStrategy(PriceChannelParams(lookback=20, breakout_pct=0.0))
    strict = PriceChannelBreakoutStrategy(PriceChannelParams(lookback=20, breakout_pct=1.0))

    assert len(buys(loose.generate_signals("TEST", df))) == 1
    assert buys(strict.generate_signals("TEST", df)) == []


# ────────────────────────── 레지스트리 정합성 ──────────────────────────


def test_registry_keys_unique_and_buildable():
    keys = [d.key for d in REGISTRY]
    assert len(keys) == len(set(keys))
    for definition in REGISTRY:
        strategy = build_strategy(definition.key)
        # trades/backtest_runs에 남는 이름이 등록 키와 어긋나면 나중에 집계가 깨진다
        assert strategy.name == definition.key


def test_registry_categories_are_known():
    for definition in REGISTRY:
        assert definition.category in CATEGORIES


def test_registry_has_exactly_one_live_strategy():
    live = [d for d in REGISTRY if d.status == "live"]
    assert len(live) == 1


def test_registry_covers_every_category():
    for category in CATEGORIES:
        assert list_definitions(category), f"{category} 계열 전략이 하나도 없다"


def test_list_definitions_filters_by_category_and_returns_copy():
    trend = list_definitions("추세추종")
    assert all(d.category == "추세추종" for d in trend)
    trend.append("mutation-should-not-leak")
    assert "mutation-should-not-leak" not in REGISTRY


def test_get_definition_unknown_key_raises_with_known_keys_listed():
    with pytest.raises(KeyError, match="ma_rsi_v1"):
        get_definition("does-not-exist")


def _all_signals(definition_key, df):
    """등록된 전략이 종목 단위든 유니버스 단위든 같은 방법으로 실행한다.

    두 종류가 공존하므로 테스트도 공통 경로(PortfolioStrategy)로 실행해야
    '등록된 전략은 전부 돈다'는 보장이 유지된다."""
    from muwon.strategy.portfolio import MarketContext, as_portfolio_strategy

    strategy = as_portfolio_strategy(build_strategy(definition_key))
    histories = {"TEST": df}
    strategy.prepare(histories)
    signals = []
    for trade_date in df["trade_date"]:
        signals.extend(
            strategy.evaluate(MarketContext(as_of=trade_date, histories=histories))
        )
    return signals


def test_every_strategy_runs_on_realistic_series_without_error():
    """등록된 전략 전부가 같은 입력에서 예외 없이 돌고, 신호 형식이 일관되는지."""
    df = rising_then_falling()
    for definition in REGISTRY:
        for s in _all_signals(definition.key, df):
            assert s.symbol == "TEST"
            assert s.strategy_name == definition.key
            assert s.reason  # 사유가 비면 매매 기록에서 원인 추적이 안 된다
            assert s.signal_type in (SignalType.BUY, SignalType.SELL)
            assert isinstance(s.trade_date, type(df["trade_date"].iloc[0]))


def test_strategies_do_not_mutate_input_dataframe():
    """전략이 입력 DataFrame에 지표 컬럼을 덧붙여 버리면, 같은 데이터를
    여러 전략에 실행하는 스윕에서 서로 오염된다."""
    df = rising_then_falling()
    original_columns = list(df.columns)
    original_len = len(df)

    for definition in REGISTRY:
        _all_signals(definition.key, df)

    assert list(df.columns) == original_columns
    assert len(df) == original_len


def test_indicator_helpers_return_new_frame_not_view():
    from muwon.indicators.technical import add_bollinger, add_donchian, add_macd

    df = make_price_df([100.0 + i for i in range(50)])
    for fn in (add_bollinger, add_donchian, add_macd):
        result = fn(df)
        assert result is not df
        assert isinstance(result, pd.DataFrame)
        assert len(result) == len(df)
