"""점수 합산 엔진 검증.

이 방식의 전제는 딱 두 가지다. (1) 모든 Factor가 0~100이라는 것,
(2) 꺼지거나 평가 못 한 Factor의 가중치가 나머지로 재분배된다는 것.
이 둘이 깨지면 가중치도 매수 기준선(75점)도 의미를 잃는다."""

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.domain.types import SignalType
from muwon.factors.base import Factor, percentile_scores, piecewise
from muwon.factors.cross_sectional import REGIME_SCORES, MarketRegimeFactor
from muwon.scoring.config import DEFAULT_FACTORS, FactorConfig, StrategyConfig
from muwon.scoring.engine import FactorScoreStrategy, ScoreEngine
from muwon.strategy.portfolio import FactorResult, MarketContext


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


class StubFactor(Factor):
    """지정한 점수를 그대로 돌려주는 Factor: 합산 로직만 떼어 검증한다."""

    def __init__(self, key, scores):
        super().__init__({})
        self.key = key
        self.scores = scores

    def score(self, symbol, ctx):
        return FactorResult(self.key, self.scores.get(symbol), "stub")


# ── 가중치 재정규화 ──────────────────────────────────────────────


def test_disabled_factor_weights_are_redistributed():
    """Factor를 끄면 나머지가 100을 다시 채워야 한다.

    안 그러면 Factor 하나 끌 때마다 전 종목 총점이 내려앉아, 같은 75점
    기준선이 전혀 다른 엄격함을 뜻하게 된다."""
    config = StrategyConfig(
        factors={
            "trend": FactorConfig(enabled=True, weight=20),
            "momentum": FactorConfig(enabled=True, weight=20),
            "volume": FactorConfig(enabled=False, weight=60),
        }
    )
    weights = config.enabled_weights()

    assert sum(weights.values()) == pytest.approx(100.0)
    assert weights == {"trend": pytest.approx(50.0), "momentum": pytest.approx(50.0)}


def test_unavailable_factor_does_not_drag_score_down():
    """데이터가 없어 평가 못 한 Factor는 0점이 아니라 '빠짐'이어야 한다.

    수급 데이터가 하루 안 들어왔다고 그날 전 종목이 매수 기준 미달이 되면
    안 된다."""
    config = StrategyConfig(
        factors={
            "trend": FactorConfig(enabled=True, weight=50),
            "momentum": FactorConfig(enabled=True, weight=50),
        }
    )
    engine = ScoreEngine(
        config,
        factors=[
            StubFactor("trend", {"A": 80.0}),
            StubFactor("momentum", {"A": None}),  # 평가 불가
        ],
    )

    result = engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])}))[0]

    assert result.total == pytest.approx(80.0), "남은 Factor 점수가 그대로 총점이어야 한다"
    assert result.skipped and "momentum" in result.skipped[0]


def test_all_factors_unavailable_yields_no_result():
    """판단 근거가 하나도 없으면 아무 판정도 내리지 않는다. 0점으로 팔면 안 된다."""
    config = StrategyConfig(factors={"trend": FactorConfig(enabled=True, weight=100)})
    engine = ScoreEngine(config, factors=[StubFactor("trend", {"A": None})])

    assert engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])})) == []


# ── 합산과 판정 ──────────────────────────────────────────────────


def test_weighted_sum_is_computed_as_declared():
    config = StrategyConfig(
        buy_threshold=75,
        strong_buy_threshold=85,
        factors={
            "trend": FactorConfig(enabled=True, weight=30),
            "momentum": FactorConfig(enabled=True, weight=70),
        },
        regime_buy_threshold={},
    )
    engine = ScoreEngine(
        config,
        factors=[StubFactor("trend", {"A": 100.0}), StubFactor("momentum", {"A": 50.0})],
    )

    result = engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])}))[0]

    assert result.total == pytest.approx(100 * 0.3 + 50 * 0.7)


@pytest.mark.parametrize(
    ("score", "expected"),
    [(90.0, "STRONG_BUY"), (78.0, "BUY"), (68.0, "WATCH"), (40.0, "NO_TRADE")],
)
def test_thresholds_map_to_decisions(score, expected):
    config = StrategyConfig(
        buy_threshold=75,
        strong_buy_threshold=85,
        factors={"trend": FactorConfig(enabled=True, weight=100)},
        regime_buy_threshold={},
    )
    engine = ScoreEngine(config, factors=[StubFactor("trend", {"A": score})])

    result = engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])}))[0]
    assert result.decision == expected


def test_results_are_sorted_by_score():
    """자리가 모자랄 때 엔진이 위에서부터 집어갈 수 있어야 한다."""
    config = StrategyConfig(factors={"trend": FactorConfig(enabled=True, weight=100)})
    engine = ScoreEngine(config, factors=[StubFactor("trend", {"A": 30.0, "B": 90.0, "C": 60.0})])

    ctx = MarketContext(as_of=date(2024, 1, 2), histories={s: frame([1]) for s in "ABC"})
    assert [r.symbol for r in engine.evaluate(ctx)] == ["B", "C", "A"]


# ── 국면과 기준선의 상호작용 ─────────────────────────────────────


def test_bear_regime_makes_buying_unreachable_by_default():
    """기본값에서 BEAR는 '기준이 높다'가 아니라 '매수 불가'다.

    국면이 점수도 깎고(20점) 기준선도 올리므로(90점) 이중으로 적용돼,
    나머지 Factor가 전부 만점이어도 88점이 최대다. 의도된 차단일 수는
    있지만 우연이어서는 안 되므로 계산을 여기에 못 박는다."""
    config = StrategyConfig()
    weights = config.enabled_weights()
    regime_weight = weights["market_regime"]
    others = sum(w for k, w in weights.items() if k != "market_regime")

    best_in_bear = REGIME_SCORES["BEAR"] * regime_weight / 100 + others

    assert best_in_bear == pytest.approx(88.0)
    assert best_in_bear < config.threshold_for("BEAR")

    # 강세장에서는 반대로 여유가 있다
    assert REGIME_SCORES["BULL"] * regime_weight / 100 + others > config.threshold_for("BULL")


def test_regime_classification_from_breadth():
    """국면은 우리 유니버스의 Breadth로 판정한다. 지수 데이터가 없어도 돌아야 한다."""
    rising = [100.0 + i for i in range(80)]
    falling = [180.0 - i for i in range(80)]
    as_of = frame(rising)["trade_date"].iloc[-1]

    bull_ctx = MarketContext(as_of=as_of, histories={f"S{i}": frame(rising) for i in range(5)})
    bear_ctx = MarketContext(as_of=as_of, histories={f"S{i}": frame(falling) for i in range(5)})

    bull, bear = MarketRegimeFactor(), MarketRegimeFactor()
    for factor, ctx in ((bull, bull_ctx), (bear, bear_ctx)):
        factor.warmup(ctx.histories)
        factor.prepare(ctx)

    assert bull.regime == "STRONG_BULL"
    assert bear.regime == "BEAR"


# ── 정규화 도우미 ────────────────────────────────────────────────


def test_percentile_gives_equal_values_equal_scores():
    """완전히 같은 종목 둘이 다른 점수를 받으면 선택이 임의로 갈린다."""
    assert percentile_scores({"a": 10, "b": 10, "c": 30}) == {"a": 50.0, "b": 50.0, "c": 100.0}


def test_piecewise_can_penalise_extremes():
    """'많이 떨어질수록 좋다'가 아니어야 한다. 눌림과 추세훼손은 다르다."""
    curve = [(-12.0, 10.0), (-6.0, 100.0), (0.0, 10.0)]

    assert piecewise(-6.0, curve) == 100.0
    assert piecewise(-12.0, curve) == 10.0  # 너무 깊으면 감점
    assert piecewise(-1.0, curve) < 50.0  # 너무 얕아도 낮음


# ── 설정 저장/복원 ───────────────────────────────────────────────


def test_config_survives_round_trip():
    config = StrategyConfig(buy_threshold=70)
    config.factors["volume"] = FactorConfig(enabled=False, weight=5, params={"ma_window": 10})

    restored = StrategyConfig.from_json(config.to_json())

    assert restored.buy_threshold == 70
    assert restored.factors["volume"].enabled is False
    assert restored.factors["volume"].params == {"ma_window": 10}


def test_corrupt_config_falls_back_to_defaults():
    """설정 하나가 깨졌다고 매매가 멈추면 안 된다."""
    assert StrategyConfig.from_json("{쓰레기").factors.keys() == DEFAULT_FACTORS.keys()
    assert StrategyConfig.from_json("").buy_threshold == StrategyConfig().buy_threshold


# ── 전략으로서의 동작 ────────────────────────────────────────────


def test_strategy_emits_buy_only_above_threshold():
    strategy = FactorScoreStrategy(
        StrategyConfig(
            buy_threshold=75,
            factors={"trend": FactorConfig(enabled=True, weight=100)},
            regime_buy_threshold={},
        )
    )
    strategy._engine = ScoreEngine(
        strategy.config, factors=[StubFactor("trend", {"A": 80.0, "B": 40.0})]
    )

    ctx = MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1]), "B": frame([1])})
    signals = strategy.evaluate(ctx)

    assert [(s.symbol, s.signal_type) for s in signals] == [("A", SignalType.BUY)]
    assert "trend 80" in signals[0].reason


def test_strategy_sells_held_symbol_when_score_collapses():
    """살 이유가 사라졌으면 들고 있을 이유도 없다(Score Exit).
    단, 보유 중인 종목에만 해당한다. 안 들고 있는 종목을 팔 수는 없다."""
    strategy = FactorScoreStrategy(
        StrategyConfig(
            sell_threshold=45,
            factors={"trend": FactorConfig(enabled=True, weight=100)},
            regime_buy_threshold={},
        )
    )
    strategy._engine = ScoreEngine(
        strategy.config, factors=[StubFactor("trend", {"HELD": 30.0, "FREE": 30.0})]
    )

    ctx = MarketContext(
        as_of=date(2024, 1, 2),
        histories={"HELD": frame([1]), "FREE": frame([1])},
        held=frozenset({"HELD"}),
    )
    signals = strategy.evaluate(ctx)

    assert [(s.symbol, s.signal_type) for s in signals] == [("HELD", SignalType.SELL)]


# ── 설정 저장소 연동 ─────────────────────────────────────────────


def test_strategy_config_round_trips_through_settings_store():
    """가중치를 바꾸면 다음 실행부터 반영돼야 한다. 저장이 실제로 되는지."""
    from muwon.db.session import make_session_factory
    from muwon.settings.service import SettingsService
    from muwon.settings.store import SettingsStore

    service = SettingsService(
        SettingsStore(make_session_factory("sqlite:///:memory:"), master_key="")
    )

    assert service.get_strategy_config().buy_threshold == StrategyConfig().buy_threshold

    changed = StrategyConfig(buy_threshold=60)
    changed.factors["momentum"] = FactorConfig(enabled=False, weight=20)
    service.set_strategy_config(changed)

    loaded = service.get_strategy_config()
    assert loaded.buy_threshold == 60
    assert loaded.factors["momentum"].enabled is False
    assert "momentum" not in loaded.enabled_weights()


class FixedRegimeFactor(MarketRegimeFactor):
    """국면을 고정해 두는 Factor: 판정 사다리만 떼어서 보기 위한 것."""

    def __init__(self, regime, score=None):
        # Factor.__init__이 곧바로 warmup()을 부르므로 먼저 채워 둔다
        self._fixed = regime
        self._score = REGIME_SCORES[regime] if score is None else score
        super().__init__({})

    def warmup(self, histories):  # 시세가 필요 없다
        self.regime = self._fixed

    def prepare(self, ctx):
        self.regime = self._fixed

    def score(self, symbol, ctx):
        return FactorResult(self.key, self._score, self._fixed)


def test_strong_buy_cannot_bypass_a_raised_regime_threshold():
    """STRONG_BUY는 더 강한 신호라는 이름표지 더 느슨한 관문이 아니다.

    판정 사다리는 위에서부터 검사한다. 그래서 STRONG_BUY 문턱(85)이 국면
    문턱(BEAR 90)보다 낮으면, 87점짜리가 BUY 검사에 닿기도 전에 STRONG_BUY로
    통과해 버린다. 약세장에 문턱을 올려 둔 설정이 통째로 무력화된다."""
    config = StrategyConfig(
        buy_threshold=75,
        strong_buy_threshold=85,
        factors={
            "market_regime": FactorConfig(enabled=True, weight=1),
            "trend": FactorConfig(enabled=True, weight=99),
        },
        regime_buy_threshold={"BEAR": 90.0},
    )
    # 국면 가중치를 1로 낮춰 총점 87을 만들 수 있게 한다. 기본 가중치(15)에서는
    # BEAR 천장이 88이라 87을 만들 수는 있어도 여유가 거의 없다
    engine = ScoreEngine(
        config,
        factors=[FixedRegimeFactor("BEAR", score=0.0), StubFactor("trend", {"A": 88.0})],
    )

    result = engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])}))[0]

    assert result.total == pytest.approx(87.12)
    assert result.decision != "STRONG_BUY", "85점 문턱이 90점 관문을 뚫으면 안 된다"
    assert result.decision == "WATCH"


def test_strong_buy_still_works_when_the_regime_threshold_is_lower():
    """반대로 강세장에서는 STRONG_BUY가 원래대로 붙어야 한다. 고치면서
    STRONG_BUY 자체를 없애 버리면 안 된다."""
    config = StrategyConfig(
        buy_threshold=75,
        strong_buy_threshold=85,
        factors={
            "market_regime": FactorConfig(enabled=True, weight=1),
            "trend": FactorConfig(enabled=True, weight=99),
        },
        regime_buy_threshold={"STRONG_BULL": 70.0},
    )
    engine = ScoreEngine(
        config,
        factors=[FixedRegimeFactor("STRONG_BULL"), StubFactor("trend", {"A": 88.0})],
    )

    result = engine.evaluate(MarketContext(as_of=date(2024, 1, 2), histories={"A": frame([1])}))[0]
    assert result.decision == "STRONG_BUY"


def test_threshold_reachability_reports_the_impossible_regime():
    """설정의 숫자가 거짓말인지 계산해서 드러낸다.

    BEAR 문턱을 90에서 85로 낮춰도 천장이 88이라 여전히 잘 안 걸린다는 걸
    값을 만지는 사람이 알아야 한다."""
    from muwon.scoring.engine import threshold_reachability

    table = threshold_reachability(StrategyConfig())

    ceiling, threshold = table["BEAR"]
    assert ceiling == pytest.approx(88.0)
    assert threshold == 90.0
    assert ceiling < threshold, "기본 설정의 BEAR는 도달 불가여야 한다(현 상태 고정)"

    for regime in ("STRONG_BULL", "BULL", "NEUTRAL"):
        ceiling, threshold = table[regime]
        assert ceiling > threshold, f"{regime}은 도달 가능해야 한다"
