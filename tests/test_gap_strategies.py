"""갭·변동성 돌파: 기각하려고 만든 전략들.

싸게 재서 기각하는 게 목적이라도, 신호 판정 자체가 틀리면 기각의 근거가
없어진다. "안 되더라"와 "잘못 구현했더라"는 전혀 다른 이야기다."""

from datetime import date

import pandas as pd
import pytest

from muwon.strategy.gap import (
    GapParams,
    GapStrategy,
    VolatilityBreakoutParams,
    VolatilityBreakoutStrategy,
)


def _bars(rows):
    """(일, 시가, 고가, 저가, 종가)"""
    return pd.DataFrame(
        [
            {
                "trade_date": date(2024, 1, d),
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": 1000,
            }
            for d, o, h, low, c in rows
        ]
    )


# 1일: 폭 = 110-90 = 20. 2일 시가 100 → 돌파선 = 100 + 20×0.5 = 110.
돌파BARS = _bars([(1, 100, 110, 90, 100), (2, 100, 115, 95, 112)])
미달BARS = _bars([(1, 100, 110, 90, 100), (2, 100, 109, 95, 105)])


def test_it_fires_when_the_high_clears_the_trigger():
    (신호,) = VolatilityBreakoutStrategy().generate_signals("A", 돌파BARS)
    assert 신호.trade_date == date(2024, 1, 2)


def test_it_stays_quiet_when_the_high_falls_short():
    assert VolatilityBreakoutStrategy().generate_signals("A", 미달BARS) == []


def test_the_trigger_moves_with_k():
    """K를 올리면 돌파선이 높아져 신호가 줄어야 한다. 안 그러면 K가
    아무 일도 안 하고 있는 것이다."""
    큰K = VolatilityBreakoutStrategy(VolatilityBreakoutParams(k=1.0))
    assert 큰K.generate_signals("A", 돌파BARS) == []


def test_a_flat_previous_day_cannot_produce_a_trigger():
    """어제 폭이 0이면 돌파선이 곧 시가라, 조금만 올라도 신호가 난다.
    그건 돌파가 아니라 '올랐다'일 뿐이다."""
    평평 = _bars([(1, 100, 100, 100, 100), (2, 100, 101, 99, 100)])
    assert VolatilityBreakoutStrategy().generate_signals("A", 평평) == []


def test_the_score_grows_with_how_far_it_cleared():
    """같은 날 후보가 많으면 이 값으로 줄을 세운다. 뒤집혀 있으면
    가장 약한 돌파부터 사게 된다."""
    약한것 = _bars([(1, 100, 110, 90, 100), (2, 100, 111, 95, 110)])
    강한것 = _bars([(1, 100, 110, 90, 100), (2, 100, 130, 95, 128)])
    (약,) = VolatilityBreakoutStrategy().generate_signals("A", 약한것)
    (강,) = VolatilityBreakoutStrategy().generate_signals("A", 강한것)
    assert 강.score > 약.score > 0


def test_gap_up_fires_only_on_a_big_enough_upward_gap():
    큰갭 = _bars([(1, 100, 100, 100, 100), (2, 103, 105, 102, 104)])
    작은갭 = _bars([(1, 100, 100, 100, 100), (2, 101, 105, 100, 104)])
    assert len(GapStrategy(GapParams(direction="up")).generate_signals("A", 큰갭)) == 1
    assert GapStrategy(GapParams(direction="up")).generate_signals("A", 작은갭) == []


def test_gap_down_fires_only_on_a_big_enough_downward_gap():
    큰갭 = _bars([(1, 100, 100, 100, 100), (2, 97, 99, 96, 98)])
    assert len(GapStrategy(GapParams(direction="down")).generate_signals("A", 큰갭)) == 1
    # 상승 갭에는 반응하지 않아야 한다
    위로 = _bars([(1, 100, 100, 100, 100), (2, 103, 105, 102, 104)])
    assert GapStrategy(GapParams(direction="down")).generate_signals("A", 위로) == []


def test_the_two_gap_directions_never_fire_on_the_same_day():
    """한쪽만 만들면 '이쪽이 맞을 것 같아서' 고른 셈이 된다. 둘 다 두되
    서로 겹치지 않아야 비교가 성립한다."""
    bars = _bars([(1, 100, 100, 100, 100), (2, 103, 105, 102, 104), (3, 99, 100, 98, 99)])
    위 = {s.trade_date for s in GapStrategy(GapParams(direction="up")).generate_signals("A", bars)}
    아래 = {
        s.trade_date for s in GapStrategy(GapParams(direction="down")).generate_signals("A", bars)
    }
    assert 위 and 아래
    assert 위 & 아래 == set()


def test_an_unknown_direction_fails_loudly():
    """오타 하나로 조용히 '상승'이 되면, 기각한 게 무엇이었는지 알 수 없다."""
    with pytest.raises(ValueError, match="direction"):
        GapStrategy(GapParams(direction="옆으로"))


def test_unsorted_bars_do_not_change_the_answer():
    """시세를 어떤 순서로 넘기든 같은 답이 나와야 한다. 갭은 '어제'가
    누구인지에 통째로 달려 있다."""
    bars = _bars([(1, 100, 100, 100, 100), (2, 103, 105, 102, 104)])
    거꾸로 = bars.iloc[::-1].reset_index(drop=True)
    전략 = GapStrategy(GapParams(direction="up"))
    assert [s.trade_date for s in 전략.generate_signals("A", bars)] == [
        s.trade_date for s in 전략.generate_signals("A", 거꾸로)
    ]


def test_they_are_all_one_day_holds():
    """원 규칙은 당일 청산이다. 며칠씩 들고 있으면 다른 전략이 된다."""
    assert VolatilityBreakoutStrategy().max_holding_days == 1
    assert GapStrategy().max_holding_days == 1
