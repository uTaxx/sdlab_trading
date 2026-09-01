"""여러 전략을 AND/OR로 묶는 규칙 검증.

이 규칙이 조용히 틀리면 "왜 안 샀지"를 전략 하나하나 뜯어 보게 된다."""

from datetime import date

import pandas as pd
import pytest

from muwon.domain.types import Signal, SignalType
from muwon.strategy.combined import COMBINE_AND, COMBINE_OR, CombinedStrategy
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy

오늘 = date(2024, 3, 5)


class 정해진신호(PortfolioStrategy):
    """정해진 신호만 그대로 내는 가짜 전략."""

    def __init__(self, name: str, signals: list[Signal], max_holding_days=None):
        self.name = name
        self._signals = signals
        self.max_holding_days = max_holding_days
        self.prepared = 0

    def prepare(self, histories):
        self.prepared += 1

    def evaluate(self, ctx):
        return list(self._signals)


def 매수(name: str, symbol: str, score: float = 50.0, reason: str = "삼") -> Signal:
    return Signal(symbol, 오늘, SignalType.BUY, name, score=score, reason=reason)


def 매도(name: str, symbol: str, reason: str = "팜") -> Signal:
    return Signal(symbol, 오늘, SignalType.SELL, name, reason=reason)


def ctx() -> MarketContext:
    return MarketContext(as_of=오늘, histories={"A": pd.DataFrame()})


def test_or_buys_when_any_single_strategy_says_so():
    combined = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A")]), 정해진신호("을", [])], mode=COMBINE_OR
    )
    나온것 = combined.evaluate(ctx())
    assert [s.symbol for s in 나온것] == ["A"]


def test_and_needs_every_strategy_to_agree():
    """AND의 요점은 '까다롭게 산다'이다. 하나라도 빠지면 안 산다."""
    둘다 = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A")]), 정해진신호("을", [매수("을", "A")])],
        mode=COMBINE_AND,
    )
    하나만 = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A")]), 정해진신호("을", [])], mode=COMBINE_AND
    )
    assert [s.symbol for s in 둘다.evaluate(ctx())] == ["A"]
    assert 하나만.evaluate(ctx()) == []


def test_and_works_with_more_than_two():
    """'복수'는 둘이 아니라 원하는 만큼이다."""
    키들 = ["갑", "을", "병", "정", "무"]
    전부 = CombinedStrategy(
        [정해진신호(k, [매수(k, "A")]) for k in 키들], mode=COMBINE_AND
    )
    하나빠짐 = CombinedStrategy(
        [정해진신호(k, [매수(k, "A")] if k != "무" else []) for k in 키들],
        mode=COMBINE_AND,
    )
    assert len(전부.evaluate(ctx())) == 1
    assert 하나빠짐.evaluate(ctx()) == []


def test_one_strategy_signalling_twice_is_still_one_vote():
    """같은 전략이 한 종목에 신호를 두 번 내도 AND가 통과되면 안 된다.
    '두 전략이 동의했다'와 구분이 안 되기 때문이다."""
    combined = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A"), 매수("갑", "A")]), 정해진신호("을", [])],
        mode=COMBINE_AND,
    )
    assert combined.evaluate(ctx()) == []


def test_selling_is_always_or_even_in_and_mode():
    """AND로 팔면 하나라도 침묵할 때 못 판다. 손실을 키우는 쪽의 실수는
    되돌리기 어려우므로, 살 때는 까다롭게 팔 때는 관대하게 간다."""
    combined = CombinedStrategy(
        [정해진신호("갑", [매도("갑", "A")]), 정해진신호("을", [])], mode=COMBINE_AND
    )
    나온것 = combined.evaluate(ctx())
    assert [(s.symbol, s.signal_type) for s in 나온것] == [("A", SignalType.SELL)]


def test_and_scores_by_the_weakest_leg():
    """자리가 모자라면 엔진이 점수 순으로 줄을 세운다. AND를 최댓값으로
    잡으면 간신히 통과한 종목이 앞줄에 선다."""
    combined = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A", 90)]), 정해진신호("을", [매수("을", "A", 30)])],
        mode=COMBINE_AND,
    )
    assert combined.evaluate(ctx())[0].score == 30


def test_or_scores_by_the_strongest_leg():
    combined = CombinedStrategy(
        [정해진신호("갑", [매수("갑", "A", 90)]), 정해진신호("을", [매수("을", "A", 30)])],
        mode=COMBINE_OR,
    )
    assert combined.evaluate(ctx())[0].score == 90


def test_the_reason_keeps_every_strategy_that_fired():
    """'무엇 때문에 샀나'가 한 전략만 남으면 묶은 뜻이 없다."""
    combined = CombinedStrategy(
        [
            정해진신호("갑", [매수("갑", "A", reason="거래량 급증")]),
            정해진신호("을", [매수("을", "A", reason="골든크로스")]),
        ],
        mode=COMBINE_AND,
    )
    사유 = combined.evaluate(ctx())[0].reason
    assert "거래량 급증" in 사유
    assert "골든크로스" in 사유


def test_a_very_long_reason_is_summarised_not_truncated():
    """기록 칸이 100자다. 잘릴 바에는 몇 개가 겹쳤는지라도 남긴다."""
    긴사유 = "아주 긴 사유 " * 12
    combined = CombinedStrategy(
        [정해진신호(f"전략{i}", [매수(f"전략{i}", "A", reason=긴사유)]) for i in range(4)],
        mode=COMBINE_OR,
    )
    사유 = combined.evaluate(ctx())[0].reason
    assert len(사유) <= 100
    assert "4개 전략" in 사유


def test_the_shortest_holding_limit_wins():
    """하나가 '5일 지나면 무조건 판다'고 정해 뒀는데 더 들고 있으면,
    그 전략은 자기가 검증된 조건 밖에서 도는 것이다."""
    combined = CombinedStrategy(
        [
            정해진신호("갑", [], max_holding_days=5),
            정해진신호("을", [], max_holding_days=20),
            정해진신호("병", [], max_holding_days=None),
        ],
        mode=COMBINE_OR,
    )
    assert combined.max_holding_days == 5


def test_prepare_reaches_every_member():
    """하나라도 예열을 안 하면 그 전략은 그날 아무 신호도 못 낸다."""
    members = [정해진신호("갑", []), 정해진신호("을", [])]
    CombinedStrategy(members, mode=COMBINE_OR).prepare({"A": pd.DataFrame()})
    assert [m.prepared for m in members] == [1, 1]


def test_bad_input_fails_loudly():
    with pytest.raises(ValueError):
        CombinedStrategy([], mode=COMBINE_OR)
    with pytest.raises(ValueError):
        CombinedStrategy([정해진신호("갑", [])], mode="아무거나")


def test_a_single_key_is_not_wrapped():
    """하나뿐인데 감싸면 기록에 남는 전략 이름이 바뀌어, 지금까지 쌓인
    매매 기록과 이어지지 않는다."""
    from muwon.strategy.registry import build_strategies

    하나 = build_strategies(["volume_surge_5d"])
    assert hasattr(하나, "params"), "감싸지 않고 원래 전략 그대로여야 한다"
    assert 하나.name == "volume_surge_5d"


def test_several_keys_become_one_combined_strategy():
    from muwon.strategy.registry import build_strategies

    묶음 = build_strategies(["volume_surge_5d", "golden_cross_20_60"], combine="AND")
    assert isinstance(묶음, CombinedStrategy)
    assert 묶음.mode == "AND"
    assert len(묶음.members) == 2
