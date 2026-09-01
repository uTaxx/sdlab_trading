"""개별 Factor 검증: 특히 예전 구조에서 만들 수 없던 두 개.

상대강도와 시장국면은 '다른 종목을 봐야' 계산되므로, generate_signals(symbol, df)
시절에는 구현 자체가 불가능했다. 그게 정말로 가능해졌는지 확인하는 게 이
파일의 핵심이다."""

from datetime import date, timedelta
from itertools import pairwise

import pandas as pd
import pytest

from muwon.factors.cross_sectional import MarketRegimeFactor, RelativeStrengthFactor
from muwon.factors.technical import (
    MomentumFactor,
    PullbackFactor,
    TrendFactor,
    VolumeFactor,
)
from muwon.strategy.portfolio import MarketContext


def frame(closes, volumes=None, start=date(2024, 1, 2)):
    n = len(closes)
    volumes = volumes or [100_000] * n
    return pd.DataFrame(
        {
            "trade_date": [start + timedelta(days=i) for i in range(n)],
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def ctx_of(histories, index_history=None):
    as_of = max(df["trade_date"].iloc[-1] for df in histories.values())
    return MarketContext(as_of=as_of, histories=histories, index_history=index_history)


def run_factor(factor, ctx):
    """실제 호출 순서 그대로: warmup(실행당 1회) → prepare(날짜별) → score.

    이 순서를 지키지 않으면 지표 표가 비어 있어 전부 '데이터 부족'이 된다."""
    factor.warmup(ctx.histories)
    factor.prepare(ctx)
    return factor


def score_of(factor_cls, ctx, symbol="A", params=None):
    return run_factor(factor_cls(params), ctx).score(symbol, ctx)


# ── 상대강도: 옛 구조에서 불가능했던 것 ──────────────────────────


def test_relative_strength_ranks_within_universe():
    """같은 날 다른 종목과 비교해야만 나오는 점수다.

    셋 다 오르지만 오름폭이 다르면, 가장 많이 오른 쪽이 상위여야 한다."""
    histories = {
        "FAST": frame([100 + i * 1.0 for i in range(120)]),
        "MID": frame([100 + i * 0.5 for i in range(120)]),
        "SLOW": frame([100 + i * 0.1 for i in range(120)]),
    }
    ctx = ctx_of(histories)
    factor = run_factor(RelativeStrengthFactor({"period": 60}), ctx)

    scores = {s: factor.score(s, ctx).score for s in histories}

    assert scores["FAST"] > scores["MID"] > scores["SLOW"]
    assert scores["FAST"] == 100.0


def test_relative_strength_uses_index_when_given():
    """지수를 주면 초과수익 기준으로 바뀐다. 지수가 없어도 죽지 않아야 한다."""
    histories = {"A": frame([100 + i for i in range(120)])}
    index = frame([100 + i * 2 for i in range(120)])

    ctx = ctx_of(histories, index_history=index)
    assert "지수 대비" in score_of(RelativeStrengthFactor, ctx, params={"period": 60}).reason

    ctx2 = ctx_of(histories)
    assert "유니버스 내" in score_of(RelativeStrengthFactor, ctx2, params={"period": 60}).reason


def test_relative_strength_is_not_absolute_return():
    """전부 하락하는 장에서도 '덜 빠진 종목'은 상위여야 한다.
    절대 수익률로 점수를 매기면 이 구분이 사라진다."""
    histories = {
        "LESS_BAD": frame([200 - i * 0.2 for i in range(120)]),
        "WORSE": frame([200 - i * 1.0 for i in range(120)]),
    }
    ctx = ctx_of(histories)
    factor = run_factor(RelativeStrengthFactor({"period": 60}), ctx)

    assert factor.score("LESS_BAD", ctx).score > factor.score("WORSE", ctx).score


# ── 시장 국면 ────────────────────────────────────────────────────


def test_regime_gives_every_symbol_the_same_score():
    """국면은 종목을 고르는 값이 아니라 시장 전체를 누르거나 띄우는 값이다."""
    histories = {f"S{i}": frame([100 + i * 0.1 + j for j in range(80)]) for i in range(4)}
    ctx = ctx_of(histories)
    factor = run_factor(MarketRegimeFactor(), ctx)

    scores = {s: factor.score(s, ctx).score for s in histories}
    assert len(set(scores.values())) == 1


def test_regime_reports_unavailable_when_history_too_short():
    ctx = ctx_of({"A": frame([100.0] * 10)})
    factor = run_factor(MarketRegimeFactor(), ctx)

    assert factor.regime is None
    assert factor.score("A", ctx).score is None


# ── 추세 ─────────────────────────────────────────────────────────


def test_trend_scores_full_alignment_higher_than_broken():
    rising = frame([100 + i for i in range(200)])
    falling = frame([300 - i for i in range(200)])
    up = score_of(TrendFactor, ctx_of({"A": rising})).score
    down = score_of(TrendFactor, ctx_of({"A": falling})).score

    assert up == 100.0
    assert down == 0.0


def test_trend_reports_reason_in_words():
    """점수만 남기면 나중에 왜 그랬는지 알 수 없다."""
    result = score_of(TrendFactor, ctx_of({"A": frame([100 + i for i in range(200)])}))
    assert "정배열" in result.reason and "종가>20일선" in result.reason


# ── 눌림목 ───────────────────────────────────────────────────────


def test_pullback_prefers_moderate_dip_over_deep_crash():
    """많이 떨어졌다고 좋은 게 아니다. 눌림과 추세훼손을 구분해야 한다."""
    base = [100 + i for i in range(120)]  # 꾸준한 상승으로 60일선 위 확보
    moderate = frame(base + [base[-1] * 0.94])  # 고점 대비 -6%
    crash = frame(base + [base[-1] * 0.80])  # -20%

    moderate_score = score_of(PullbackFactor, ctx_of({"A": moderate})).score
    crash_score = score_of(PullbackFactor, ctx_of({"A": crash})).score

    assert moderate_score > crash_score


def test_pullback_is_zero_below_long_term_average():
    """장기선 아래로 내려간 건 눌림이 아니라 하락이다."""
    falling = frame([300 - i * 1.5 for i in range(120)])
    result = score_of(PullbackFactor, ctx_of({"A": falling}))

    assert result.score == 0.0
    assert "하락" in result.reason


# ── 거래량 ───────────────────────────────────────────────────────


def test_volume_scales_with_surge_ratio():
    quiet = frame([100.0] * 40, [100_000] * 40)
    surge = frame([100.0] * 40, [100_000] * 39 + [300_000])
    assert (
        score_of(VolumeFactor, ctx_of({"A": surge})).score
        > score_of(VolumeFactor, ctx_of({"A": quiet})).score
    )


def test_volume_rejects_illiquid_stock():
    """거래대금이 너무 작으면 신호가 맞아도 원하는 가격에 못 산다."""
    thin = frame([100.0] * 40, [100] * 39 + [1_000])
    result = score_of(
        VolumeFactor, ctx_of({"A": thin}), params={"min_turnover_krw": 2_000_000_000}
    )

    assert result.score == 0.0
    assert "유동성" in result.reason


# ── 미래를 보지 않는가 ───────────────────────────────────────────


def test_factors_ignore_data_after_as_of():
    """백테스트는 성능 때문에 전체 프레임을 그대로 넘긴다. 자르는 책임이
    Factor에 있으므로, as_of 이후 행이 결과를 바꾸면 안 된다."""
    closes = [100 + i for i in range(150)]
    full = frame(closes + [999.0] * 10)  # as_of 이후에 폭등이 있는 프레임
    as_of = full["trade_date"].iloc[len(closes) - 1]

    trimmed_ctx = MarketContext(as_of=as_of, histories={"A": frame(closes)})
    full_ctx = MarketContext(as_of=as_of, histories={"A": full})

    for factor_cls in (TrendFactor, PullbackFactor, VolumeFactor, MomentumFactor):
        assert score_of(factor_cls, trimmed_ctx).score == pytest.approx(
            score_of(factor_cls, full_ctx).score
        ), f"{factor_cls.__name__}이 as_of 이후 데이터를 보고 있다"


def test_momentum_weights_long_horizons_over_a_single_spike():
    """최근 수익률만 보면 하루 급등에 속는다.

    오래 흘러내리다 마지막 하루 튄 종목(5일 수익률은 크지만 120일은 마이너스)
    보다, 꾸준히 오른 종목이 높아야 한다. 장기 기간에 더 큰 가중치를 두는
    이유가 이것이다(인수인계서 8.2항)."""
    steady = frame([100 + i * 0.8 for i in range(150)])
    faded = frame([200 - i * 0.6 for i in range(149)] + [140.0])  # 하락 뒤 하루 급등

    ctx = ctx_of({"STEADY": steady, "FADED": faded})
    factor = run_factor(MomentumFactor(), ctx)

    steady_result = factor.score("STEADY", ctx)
    faded_result = factor.score("FADED", ctx)

    assert "5일 +2" in faded_result.reason  # 단기만 보면 급등처럼 보이지만
    assert steady_result.score > faded_result.score  # 장기가 반영돼 뒤집힌다


def test_cross_sectional_factors_ignore_data_after_as_of():
    """상대강도·국면은 '그날 전 종목'을 보므로 미래를 볼 위험이 가장 크다.

    warmup에서 전 기간 시계열을 미리 만들어 두기 때문에 더더욱 확인이 필요하다.
    rolling 값 자체는 과거만 쓰지만, 꺼내 쓰는 날짜를 잘못 잡으면 새어 든다."""
    base = {
        "A": [100 + i for i in range(150)],
        "B": [100 + i * 0.3 for i in range(150)],
        "C": [150 - i * 0.2 for i in range(150)],
    }
    trimmed = {k: frame(v) for k, v in base.items()}
    # as_of 이후에 순위를 뒤집을 만한 폭등을 붙인다
    full = {k: frame(v + [v[-1] * 3] * 10) for k, v in base.items()}
    as_of = trimmed["A"]["trade_date"].iloc[-1]

    trimmed_ctx = MarketContext(as_of=as_of, histories=trimmed)
    full_ctx = MarketContext(as_of=as_of, histories=full)

    for factor_cls in (RelativeStrengthFactor, MarketRegimeFactor):
        a = run_factor(factor_cls(), trimmed_ctx)
        b = run_factor(factor_cls(), full_ctx)
        for symbol in base:
            assert a.score(symbol, trimmed_ctx).score == pytest.approx(
                b.score(symbol, full_ctx).score
            ), f"{factor_cls.__name__}이 as_of 이후 데이터를 보고 있다"


# ── 국면 안정성 ──────────────────────────────────────────────────


def _regime_series(histories, params=None):
    """전 거래일의 확정 국면을 순서대로 뽑는다."""
    factor = MarketRegimeFactor(params)
    factor.warmup(histories)
    dates = sorted({d for df in histories.values() for d in df["trade_date"]})
    out = []
    for day in dates:
        factor.prepare(MarketContext(as_of=day, histories=histories))
        out.append(factor.regime)
    return out


def test_regime_does_not_flip_on_a_short_bounce():
    """하락장 한복판의 2~3일 반등에 국면이 뒤집히면 안 된다.

    실측에서 이 문제로 2022년 국면이 245거래일 동안 67번 바뀌었고, 그 짧은
    STRONG_BULL 구간에 사서 손절당했다. 평활화와 확정 지연이 그걸 막는다."""
    # 길게 하락하다 3일만 튀고 곧바로 원래 흐름으로 돌아가는 시장.
    # 튄 뒤에도 높은 수준에 머물면 그건 반등이 아니라 추세 전환이므로,
    # 반드시 직전 수준 아래로 되돌려야 의도한 상황이 된다.
    decline = [300 - i for i in range(150)]  # 300 → 151
    bounce = [175, 195, 205]
    after = [148 - i for i in range(40)]
    histories = {f"S{i}": frame(decline + bounce + after) for i in range(5)}

    smoothed = _regime_series(histories)
    raw = _regime_series(histories, {"breadth_smoothing": 1, "confirm_days": 1})

    assert "STRONG_BULL" in raw, "평활화가 없으면 짧은 반등에 강세 국면이 선언된다"
    assert "STRONG_BULL" not in smoothed, "3일 반등은 국면 전환으로 인정하면 안 된다"


def test_regime_changes_are_far_fewer_when_smoothed():
    """국면이 며칠마다 바뀌면 그건 국면이 아니라 잡음이다."""
    closes = [100 + (12 if i % 5 in (0, 1) else -12) + i * 0.1 for i in range(220)]
    histories = {f"S{i}": frame(closes) for i in range(5)}

    def transitions(series):
        return sum(1 for a, b in pairwise(series) if a != b)

    assert transitions(_regime_series(histories)) < transitions(
        _regime_series(histories, {"breadth_smoothing": 1, "confirm_days": 1})
    )


def test_regime_confirmation_looks_backward_only():
    """확정 지연이 '앞으로 며칠 유지될지'를 보면 그건 미래참조다.
    뒤에 붙는 데이터가 이전 날짜의 국면을 바꾸면 안 된다."""
    closes = [100 + i for i in range(200)]
    short = {f"S{i}": frame(closes) for i in range(3)}
    long = {f"S{i}": frame(closes + [999.0] * 20) for i in range(3)}
    as_of = short["S0"]["trade_date"].iloc[-1]

    def regime_at(histories):
        factor = MarketRegimeFactor()
        factor.warmup(histories)
        factor.prepare(MarketContext(as_of=as_of, histories=histories))
        return factor.regime

    assert regime_at(short) == regime_at(long)


def test_market_filter_rejects_a_bounce_inside_a_downtrend():
    """하락 추세의 반등 꼭지를 강세로 부르지 않아야 한다.

    반등이 크면 가격이 잠시 장기 평균선을 넘는다. 그리고 그 자리가 정확히
    반등 꼭지다. 그래서 '평균선 위'만으로는 부족하고, 평균선 자체가 아직
    내려가고 있다는 것까지 봐야 한다. 2022년 58종목에서 이 조건 하나가
    최악 구간을 -39.1%에서 -29.2%로 바꿨다."""
    # 300일 하락 뒤 40일 급반등: 반등 끝에서 가격은 200일선을 넘지만
    # 200일선 자체는 여전히 내려가는 중이다
    falling = [200.0 - i * 0.4 for i in range(300)]
    bounce = [falling[-1] + i * 2.0 for i in range(40)]
    # _market_uptrend는 종가가 아니라 일간 수익률을 받는다
    returns = pd.Series(falling + bounce).pct_change()

    above_only = MarketRegimeFactor._market_uptrend([returns], 200, 0, returns.index)
    with_slope = MarketRegimeFactor._market_uptrend([returns], 200, 60, returns.index)

    assert bool(above_only.iloc[-1]), "반등 꼭지에서 가격은 평균선을 넘는다"
    assert not bool(with_slope.iloc[-1]), "평균선이 아직 내려가면 강세가 아니다"


def test_market_filter_allows_a_real_uptrend():
    """고치면서 필터가 전부 막아 버리면 안 된다. 진짜 상승장은 통과해야 한다."""
    rising = pd.Series([100.0 + i * 0.5 for i in range(400)]).pct_change()

    result = MarketRegimeFactor._market_uptrend([rising], 200, 60, rising.index)

    assert bool(result.iloc[-1])


def test_stored_config_without_params_keeps_factor_defaults():
    """params 없이 저장된 설정 하나가 시장 필터를 조용히 꺼서는 안 된다."""
    from muwon.scoring.config import StrategyConfig

    restored = StrategyConfig.from_json(
        '{"factors": {"market_regime": {"enabled": true, "weight": 15}}}'
    )

    assert restored.factors["market_regime"].params["uptrend_slope"] == 60
