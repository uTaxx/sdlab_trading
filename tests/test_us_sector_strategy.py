"""미국 섹터 따라가기 전략 (설계안 §48). 네트워크 없이 검사한다."""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from muwon.domain.types import SignalType
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy
from muwon.strategy.registry import build_strategy, get_definition
from muwon.strategy.us_sector import (
    USSectorFollowStrategy,
    USSectorGateStrategy,
    기준지수,
    섹터짝,
)

날들 = pd.bdate_range("2024-01-01", periods=160)


def _시세(값들) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [d.date() for d in 날들], "close": list(값들),
                         "open": list(값들), "high": list(값들), "low": list(값들),
                         "volume": [1000] * len(날들)})


def _오르는(시작=100.0, 기울기=0.5):
    return _시세([시작 + 기울기 * i for i in range(len(날들))])


def _내리는(시작=100.0, 기울기=0.5):
    return _시세([시작 - 기울기 * i for i in range(len(날들))])


def _평평한(값=100.0):
    return _시세([값] * len(날들))


def 가짜미국(강한섹터: str):
    """강한 섹터 ETF만 오르고 나머지와 SPY는 평평하다."""
    def 가져오기(심볼, 시작, 끝):
        if 심볼 == 섹터짝[강한섹터]:
            return _오르는()
        return _평평한()
    return 가져오기


섹터표 = {"A": "SEMI", "B": "BIO", "X": "없는섹터"}


def _전략(가져오기):
    return USSectorFollowStrategy(N=60, k=1, 지연=1, 보유상한=20, 가져오기=가져오기, 섹터표=섹터표)


def test_강한_섹터의_오르는_종목만_산다():
    전략 = _전략(가짜미국("SEMI"))
    시세 = {"A": _오르는(), "B": _오르는(), "X": _오르는()}
    전략.prepare(시세)
    d = 날들[-1].date()
    신호 = 전략.evaluate(MarketContext(as_of=d, histories=시세))
    산것 = {s.symbol for s in 신호 if s.signal_type == SignalType.BUY}
    assert 산것 == {"A"}, f"반도체(A)만 사야 하는데 {산것}"


def test_강한_섹터라도_종목이_내리면_안_산다():
    전략 = _전략(가짜미국("SEMI"))
    시세 = {"A": _내리는(), "B": _오르는()}
    전략.prepare(시세)
    신호 = 전략.evaluate(MarketContext(as_of=날들[-1].date(), histories=시세))
    assert not [s for s in 신호 if s.signal_type == SignalType.BUY]


def test_들고_있는데_섹터가_약해지면_판다():
    전략 = _전략(가짜미국("BIO"))   # 반도체는 강하지 않다
    시세 = {"A": _오르는(), "B": _오르는()}
    전략.prepare(시세)
    신호 = 전략.evaluate(MarketContext(as_of=날들[-1].date(), histories=시세,
                                     held=frozenset({"A"})))
    판것 = [s for s in 신호 if s.signal_type == SignalType.SELL and s.symbol == "A"]
    assert 판것 and "약해짐" in 판것[0].reason


def test_미국_시세를_못_받으면_안_사고_표시를_남긴다():
    def 터지는(심볼, 시작, 끝):
        raise ConnectionError("막힘")
    전략 = _전략(터지는)
    시세 = {"A": _오르는()}
    전략.prepare(시세)
    assert 전략.미국시세없음 is True
    신호 = 전략.evaluate(MarketContext(as_of=날들[-1].date(), histories=시세))
    assert not [s for s in 신호 if s.signal_type == SignalType.BUY]


def test_섹터_종목이_없으면_미국_시세를_받으러_가지_않는다():
    """등록된 전략 전부를 가짜 종목 하나로 돌리는 테스트가 네트워크에 걸리면 안 된다."""
    부른횟수 = []
    def 세는(심볼, 시작, 끝):
        부른횟수.append(심볼); return _평평한()
    전략 = _전략(세는)
    전략.prepare({"TEST": _오르는()})
    assert 부른횟수 == []


def test_미국_시세는_하루_미룬다():
    """오늘 미국이 강해져도 오늘은 모른다. 내일부터 안다."""
    받은범위 = {}
    def 가져오기(심볼, 시작, 끝):
        받은범위[심볼] = (시작, 끝)
        return _오르는() if 심볼 == 섹터짝["SEMI"] else _평평한()
    전략 = USSectorFollowStrategy(N=60, k=1, 지연=1, 가져오기=가져오기, 섹터표=섹터표)
    전략.prepare({"A": _오르는()})
    # 미국 시세 요청 범위가 국내 시세보다 예열만큼 앞서야 한다
    시작, 끝 = 받은범위[기준지수]
    assert 시작 < 날들[0].date() - timedelta(days=300)
    assert 끝 == 날들[-1].date()


def test_등록되어_있고_네트워크_없이_만들_수_있다():
    정의 = get_definition("us_sector_follow_60_2")
    전략 = build_strategy("us_sector_follow_60_2")
    assert isinstance(전략, USSectorFollowStrategy)
    assert 전략.max_holding_days == 20
    assert 정의.쉬운설명 and "미국" in 정의.쉬운설명


class _다사는전략(PortfolioStrategy):
    """시험용. 모든 종목에 매일 매수, 들고 있으면 매도 신호."""

    name = "다사기"
    max_holding_days = 3
    take_profit_pct = 0.0

    def prepare(self, histories):
        pass

    def evaluate(self, ctx):
        from muwon.domain.types import Signal
        나온것 = []
        for 심볼 in ctx.histories:
            if 심볼 in ctx.held:
                나온것.append(Signal(심볼, ctx.as_of, SignalType.SELL, self.name, reason="시험"))
            else:
                나온것.append(Signal(심볼, ctx.as_of, SignalType.BUY, self.name, score=1.0))
        return 나온것


def test_문은_강한_섹터의_매수만_통과시키고_매도는_그대로_둔다():
    전략 = USSectorGateStrategy(_다사는전략(), "다사기", N=60, k=1, 지연=1,
                               가져오기=가짜미국("SEMI"), 섹터표=섹터표)
    시세 = {"A": _오르는(), "B": _오르는(), "X": _오르는()}
    전략.prepare(시세)
    신호 = 전략.evaluate(MarketContext(as_of=날들[-1].date(), histories=시세, held=frozenset({"B"})))
    산것 = {s.symbol for s in 신호 if s.signal_type == SignalType.BUY}
    판것 = {s.symbol for s in 신호 if s.signal_type == SignalType.SELL}
    assert 산것 == {"A"}, f"반도체(A)만 통과해야 하는데 {산것}"
    assert 판것 == {"B"}, "매도 신호는 섹터와 무관하게 그대로 나가야 한다"


def test_문은_원래_전략의_보유_상한을_그대로_쓴다():
    전략 = USSectorGateStrategy(_다사는전략(), "다사기", 가져오기=가짜미국("SEMI"), 섹터표=섹터표)
    assert 전략.max_holding_days == 3


def test_문도_미국_시세를_못_받으면_매수를_전부_막는다():
    def 터지는(심볼, 시작, 끝):
        raise ConnectionError("막힘")
    전략 = USSectorGateStrategy(_다사는전략(), "다사기", 가져오기=터지는, 섹터표=섹터표)
    시세 = {"A": _오르는()}
    전략.prepare(시세)
    assert 전략.미국시세없음 is True
    신호 = 전략.evaluate(MarketContext(as_of=날들[-1].date(), histories=시세))
    assert not [s for s in 신호 if s.signal_type == SignalType.BUY]
