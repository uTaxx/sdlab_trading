"""거래량 급증 계열의 파는 규칙이 화면에 나오는가.

## 왜 시험하나

`exit_sma`를 붙인 전략이 생기기 전에 이 계열은 파는 신호가 없었다. 그래서
설명을 만드는 쪽이 판다를 빈 칸으로 두고 있었고, exit_sma가 생긴 뒤에도
그대로였다. 화면은 `volume_surge_5d_ma20`을 놓고 "이 전략은 파는 신호를
내지 않습니다"라고 적고 있었다. 지금 실제로 걸려 있는 전략이 그것이다.

빈 칸은 조용히 틀린다. 없는 규칙과 안 적힌 규칙이 화면에서 같아 보인다.
"""

from muwon.dashboard.strategy_rules import describe
from muwon.strategy.registry import build_strategy


def _규칙(키):
    return describe(build_strategy(키))


def test_매도선이_있으면_파는_규칙이_나온다():
    규칙 = _규칙("volume_surge_5d_ma20")
    assert 규칙.판다, "20일선 매도 전략인데 파는 규칙이 비어 있습니다"
    assert "20일 평균선" in 규칙.판다[0]


def test_매도선이_없으면_파는_규칙도_없다():
    """시간 청산만 있는 전략이다. 엔진이 내는 보유 기간 규칙과 겹치면
    화면에 같은 줄이 두 번 나온다."""
    assert _규칙("volume_surge_5d").판다 == []


def test_매도선_숫자가_설명에_그대로_들어간다():
    """10일선 전략에 20일선이라고 적히면 화면이 거짓말을 한다."""
    assert "10일 평균선" in _규칙("volume_surge_5d_ma10").판다[0]


def test_매도선이_있으면_나가는_길이_둘이라고_적는다():
    참고 = " ".join(_규칙("volume_surge_5d_ma20").참고)
    assert "매도 조건이 두 가지" in 참고
    assert "5거래일" in 참고


def test_매도선이_없으면_시간_청산이라고_적는다():
    참고 = " ".join(_규칙("volume_surge_5d").참고)
    assert "매도 조건이 지표가 아니라 **보유기간**" in 참고
