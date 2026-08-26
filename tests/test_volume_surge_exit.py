"""**거래량 급증 전략의 매도 신호**를 고정한다.

지금 쓰는 volume_surge_5d는 시간이 되면 파는 것뿐이다. 5거래일이 지나면
값이 어떻게 되든 무조건 청산하고, 매도 신호가 없다.

exit_sma를 주면 종가가 그 이동평균 아래로 내려온 날 판다. "정해진 날짜가
아니라 추세가 깨질 때 판다"는 가설이다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from muwon.domain.types import SignalType
from muwon.strategy.breakout import VolumeSurgeParams, VolumeSurgeStrategy


def _시세(종가들: list[float], 거래량들: list[int] | None = None) -> pd.DataFrame:
    n = len(종가들)
    거래량 = 거래량들 or [1000] * n
    return pd.DataFrame(
        {
            "trade_date": pd.date_range("2026-01-01", periods=n, freq="D").date,
            "open": 종가들,
            "high": [ㄱ * 1.01 for ㄱ in 종가들],
            "low": [ㄱ * 0.99 for ㄱ in 종가들],
            "close": 종가들,
            "volume": 거래량,
        }
    )


def _매도들(signals):
    return [s for s in signals if s.signal_type == SignalType.SELL]


def _내려가는_시세(창: int = 10) -> pd.DataFrame:
    """평균선 위에서 한참 오르다가 마지막에 뚝 떨어지는 시세."""
    오름 = list(np.linspace(100.0, 140.0, 60))
    떨어짐 = [120.0, 100.0]
    return _시세(오름 + 떨어짐)


def test_exit_sma를_안_주면_매도_신호가_없다():
    """지금 쓰는 volume_surge_5d의 성격이다. 여기가 바뀌면 과거 성적과
    견줄 수 없게 되므로 기본값을 지킨다."""
    전략 = VolumeSurgeStrategy(VolumeSurgeParams(), name="volume_surge_5d")

    assert _매도들(전략.generate_signals("005930", _내려가는_시세())) == []


def test_평균선_아래로_내려온_날_판다():
    전략 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=10), name="t")

    매도 = _매도들(전략.generate_signals("005930", _내려가는_시세()))

    assert 매도, "평균선을 뚫고 내려왔는데 매도 신호가 없다"
    assert "10일 평균선 아래로 내려왔습니다" in 매도[0].reason


def test_아래에_머무는_동안_매일_내지_않는다():
    """이미 판 종목에 같은 신호가 쌓이면 기록에서 '왜 팔았나'가 흐려진다."""
    오름 = list(np.linspace(100.0, 140.0, 60))
    계속아래 = [110.0, 105.0, 100.0, 95.0, 90.0, 85.0]
    전략 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=10), name="t")

    매도 = _매도들(전략.generate_signals("005930", _시세(오름 + 계속아래)))

    assert len(매도) == 1, f"내려온 날 한 번만 내야 하는데 {len(매도)}번 냈다"


def test_평균선_위에_있으면_안_판다():
    전략 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=10), name="t")
    쭉오름 = _시세(list(np.linspace(100.0, 160.0, 70)))

    assert _매도들(전략.generate_signals("005930", 쭉오름)) == []


def test_매도선을_주면_그_창으로_평균을_낸다():
    """sma_short를 그대로 쓰면 exit_sma=10을 줘도 20일선으로 판다.
    화면에는 10일선이라고 적히고 실제로는 다른 선을 보는 상태가 된다."""
    종가 = list(np.linspace(100.0, 140.0, 40)) + [133.0]
    빠른것 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=5), name="t")
    느린것 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=30), name="t")

    # 133은 최근 5일 평균보다는 아래, 30일 평균보다는 위다.
    assert _매도들(빠른것.generate_signals("005930", _시세(종가)))
    assert _매도들(느린것.generate_signals("005930", _시세(종가))) == []


def test_한_봉에_매수와_매도가_같이_나오지_않는다():
    """엔진이 같은 날 같은 종목을 사고 팔면 기록이 앞뒤가 안 맞는다.

    코드는 매도를 먼저 보고 `continue`로 끊는다. 실제로 둘이 겹치는 봉이
    없는지 흔들리는 시세로 확인한다.

    (조건상 겹치기가 거의 불가능하기도 하다. 평균선을 아래로 뚫으면서 2%
    이상 오르려면 평균선이 값보다 더 빨리 올라야 한다. 다만 '거의'는
    '절대'가 아니므로 코드로 막아 두고 여기서 지킨다.)"""
    rng = np.random.default_rng(20260826)
    값 = 100.0
    종가, 거래량 = [], []
    for _ in range(400):
        값 = max(10.0, 값 * float(1 + rng.normal(0, 0.03)))
        종가.append(값)
        거래량.append(int(1000 * float(rng.lognormal(0, 0.9))))

    전략 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=10), name="t")
    signals = 전략.generate_signals("005930", _시세(종가, 거래량))

    날짜별 = {}
    for s in signals:
        날짜별.setdefault(s.trade_date, set()).add(s.signal_type)
    겹친날 = [ㄴ for ㄴ, ㅅ in 날짜별.items() if len(ㅅ) > 1]

    assert signals, "이 시세에서는 신호가 좀 나와야 한다"
    assert not 겹친날, f"같은 날 매수·매도가 같이 났다: {겹친날[:3]}"


def test_매수_조건은_그대로다():
    """매도를 붙이면서 사는 규칙이 달라지면 두 전략을 견줄 수 없다."""
    조용함 = [100.0] * 40
    급등 = [103.0]
    거래량 = [1000] * 40 + [9000]
    없음 = VolumeSurgeStrategy(VolumeSurgeParams(), name="t")
    있음 = VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=10), name="t")

    def 산것(ㅈ):
        return [
            s.trade_date
            for s in ㅈ.generate_signals("005930", _시세(조용함 + 급등, 거래량))
            if s.signal_type == SignalType.BUY
        ]

    assert 산것(없음) == 산것(있음)
    assert 산것(없음), "이 시세에서는 매수 신호가 나와야 한다"
