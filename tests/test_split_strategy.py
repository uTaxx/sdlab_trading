"""매수와 매도를 다른 전략으로 굴린다.

지금까지는 전략 하나에 사는 규칙과 파는 규칙이 같이 들어 있었다. 파는 것만
바꾸려면 이름을 새로 붙인 전략을 등록해야 했고(`volume_surge_5d_ma20`이
그렇게 생겼다), 조합이 늘면 이름이 곱으로 늘어난다.

**여기서 제일 중요한 시험은 신호가 새지 않는 것이다.** 매도 쪽이 낸 매수
신호가 섞여 들어가면, 화면에는 "매수 전략 A"라고 적혀 있는데 실제로는 B가
사고 있는 상태가 된다. 그건 로그를 봐도 안 보인다.
"""

from datetime import date

import pytest

from muwon.domain.types import Signal, SignalType
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy
from muwon.strategy.split import SplitStrategy


class 가짜전략(PortfolioStrategy):
    """정해 둔 신호를 그대로 내는 전략. 준비 횟수도 센다."""

    def __init__(self, name, 신호들, max_holding_days=None):
        self.name = name
        self._신호들 = 신호들
        self.max_holding_days = max_holding_days
        self.준비횟수 = 0

    def prepare(self, histories):
        self.준비횟수 += 1

    def evaluate(self, ctx):
        return list(self._신호들)


def _신호(종류, 종목="005930", 이름="누구", 사유="그래서", 점수=70.0):
    return Signal(
        symbol=종목,
        trade_date=date(2026, 8, 26),
        signal_type=종류,
        strategy_name=이름,
        score=점수,
        reason=사유,
    )


def _ctx():
    return MarketContext(as_of=date(2026, 8, 26), histories={})


def test_매수는_매수쪽에서만_나온다():
    """이 파일에서 제일 중요한 시험이다. 매도 쪽이 낸 매수 신호가 새어
    들어가면, 화면에는 매수 전략 A라고 적혀 있는데 실제로는 B가 산다."""
    사는쪽 = 가짜전략("사는쪽", [_신호(SignalType.BUY, 종목="A", 이름="사는쪽")])
    파는쪽 = 가짜전략("파는쪽", [_신호(SignalType.BUY, 종목="B", 이름="파는쪽")])

    낸것 = SplitStrategy(사는쪽, 파는쪽).evaluate(_ctx())

    assert [s.symbol for s in 낸것] == ["A"]


def test_매도는_매도쪽에서만_나온다():
    사는쪽 = 가짜전략("사는쪽", [_신호(SignalType.SELL, 종목="A", 이름="사는쪽")])
    파는쪽 = 가짜전략("파는쪽", [_신호(SignalType.SELL, 종목="B", 이름="파는쪽")])

    낸것 = SplitStrategy(사는쪽, 파는쪽).evaluate(_ctx())

    assert [s.symbol for s in 낸것] == ["B"]


def test_양쪽_신호가_다_나온다():
    사는쪽 = 가짜전략("사는쪽", [_신호(SignalType.BUY, 종목="A", 이름="사는쪽")])
    파는쪽 = 가짜전략("파는쪽", [_신호(SignalType.SELL, 종목="B", 이름="파는쪽")])

    낸것 = SplitStrategy(사는쪽, 파는쪽).evaluate(_ctx())

    종류 = {(s.symbol, s.signal_type) for s in 낸것}
    assert 종류 == {("A", SignalType.BUY), ("B", SignalType.SELL)}


def test_보유_기간_상한은_매도쪽_것을_쓴다():
    """보유 기간도 청산 규칙이다. 파는 자리를 두 군데로 나누면 왜 팔렸는지
    설명할 수 없게 된다."""
    사는쪽 = 가짜전략("사는쪽", [], max_holding_days=5)
    파는쪽 = 가짜전략("파는쪽", [], max_holding_days=20)

    assert SplitStrategy(사는쪽, 파는쪽).max_holding_days == 20


def test_매도쪽에_나가는_길이_없으면_말해_준다():
    """매도 신호도 없고 보유 상한도 없으면 손절 말고는 파는 길이 없다.
    막지는 않되 조용히 두지도 않는다."""
    사는쪽 = 가짜전략("사는쪽", [], max_holding_days=5)
    파는쪽 = 가짜전략("파는쪽", [], max_holding_days=None)

    말 = SplitStrategy(사는쪽, 파는쪽).왜조심해야하나

    assert "손절 말고는" in 말 and "파는쪽" in 말


def test_나가는_길이_있으면_경고하지_않는다():
    사는쪽 = 가짜전략("사는쪽", [])
    파는쪽 = 가짜전략("파는쪽", [], max_holding_days=20)

    assert SplitStrategy(사는쪽, 파는쪽).왜조심해야하나 == ""


def test_기록에_묶은_이름과_원래_이름이_같이_남는다():
    """묶은 이름만 남으면 어느 쪽이 낸 신호인지 모르고, 원래 이름만 남으면
    어떤 조합에서 나온 매매인지 모른다."""
    사는쪽 = 가짜전략("사는쪽", [_신호(SignalType.BUY, 이름="사는쪽", 사유="거래량 급증")])
    파는쪽 = 가짜전략("파는쪽", [])

    (낸것,) = SplitStrategy(사는쪽, 파는쪽, name="묶음").evaluate(_ctx())

    assert 낸것.strategy_name == "묶음"
    assert "[사는쪽]" in 낸것.reason and "거래량 급증" in 낸것.reason


def test_사유가_기록_칸을_넘으면_원래_사유를_살린다():
    """기록 칸이 100자다. 잘릴 바에는 무엇 때문에 샀는지를 남긴다."""
    긴사유 = "가" * 95
    사는쪽 = 가짜전략("사는쪽", [_신호(SignalType.BUY, 이름="아주긴이름", 사유=긴사유)])

    (낸것,) = SplitStrategy(사는쪽, 가짜전략("파는쪽", [])).evaluate(_ctx())

    assert 낸것.reason == 긴사유


def test_같은_전략을_양쪽에_놓으면_한_번만_준비한다():
    """준비는 무거운 계산이다. 두 번 하면 회차 시간이 그만큼 늘어난다."""
    하나 = 가짜전략("하나", [])

    SplitStrategy(하나, 하나).prepare({})

    assert 하나.준비횟수 == 1


def test_같은_전략을_양쪽에_놓아도_신호가_두_번_안_나온다():
    하나 = 가짜전략("하나", [
        _신호(SignalType.BUY, 종목="A", 이름="하나"),
        _신호(SignalType.SELL, 종목="B", 이름="하나"),
    ])

    낸것 = SplitStrategy(하나, 하나).evaluate(_ctx())

    assert len(낸것) == 2


def test_이름을_안_주면_양쪽이_다_보이는_이름이_생긴다():
    묶음 = SplitStrategy(가짜전략("사는쪽", []), 가짜전략("파는쪽", []))

    assert "사는쪽" in 묶음.name and "파는쪽" in 묶음.name


# ── 설정에서 실제로 걸리는가 ────────────────────────────────────────────


def test_등록된_키로_매수와_매도를_따로_묶는다():
    from muwon.strategy.registry import build_strategies

    묶음 = build_strategies(("volume_surge_5d",), "OR", ("ma_rsi_v1",))

    assert isinstance(묶음, SplitStrategy)
    assert 묶음.매수쪽.name == "volume_surge_5d"
    assert 묶음.매도쪽.name == "ma_rsi_v1"


def test_매도를_안_주면_지금까지와_똑같다():
    """기본값을 바꾸면 이미 돌고 있는 설정의 뜻이 달라진다."""
    from muwon.strategy.registry import build_strategies

    묶음 = build_strategies(("volume_surge_5d",), "OR")

    assert not isinstance(묶음, SplitStrategy)
    assert 묶음.name == "volume_surge_5d"


def test_매도가_매수와_같으면_감싸지_않는다():
    """감싸면 기록에 남는 전략 이름이 바뀌어 지금까지 쌓인 매매와 안 이어진다."""
    from muwon.strategy.registry import build_strategies

    묶음 = build_strategies(("volume_surge_5d",), "OR", ("volume_surge_5d",))

    assert not isinstance(묶음, SplitStrategy)
    assert 묶음.name == "volume_surge_5d"


def test_없는_키를_매도로_주면_바로_터진다():
    from muwon.strategy.registry import build_strategies

    with pytest.raises(KeyError, match="없는전략"):
        build_strategies(("volume_surge_5d",), "OR", ("없는전략",))
