"""승인 알림에 적는 '매도까지 남은 거래일'이 엔진과 같은 답을 내는가.

**여기가 어긋나면 조용히 틀린다.** 알림은 "3거래일 뒤에 판다"고 하는데
실제로는 다른 날 팔린다. 사람은 알림을 보고 자리가 언제 비는지 가늠하므로,
하루 어긋나면 살지 말지 판단이 달라진다.

엔진(`execution/engine.py`)은 `bars_since()`로 진입일 **다음 거래일부터**
센다. 달력 일수가 아니다. 주말이나 연휴가 끼면 두 셈이 갈린다.
"""

import importlib.util
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from muwon.settings.schema import RiskPolicy
from muwon.strategy.breakout import VolumeSurgeParams, VolumeSurgeStrategy

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "propose_buys.py"
_스펙 = importlib.util.spec_from_file_location("propose_buys_for_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["propose_buys_for_test"] = _모듈
_스펙.loader.exec_module(_모듈)

보유현황 = _모듈.보유현황


@dataclass
class 가짜종목:
    symbol: str
    name: str


@dataclass
class 가짜보유:
    symbol: str
    entry_date: date


# 2026-08-24(월)부터 08-31(월)까지. 주말(29·30)은 거래일이 아니다.
거래일 = [
    date(2026, 8, 24),
    date(2026, 8, 25),
    date(2026, 8, 26),
    date(2026, 8, 27),
    date(2026, 8, 28),
    date(2026, 8, 31),
]
_시세 = pd.DataFrame({"trade_date": 거래일, "close": [1.0] * len(거래일)})
섹터시세 = {"SEMI": {"034020": (가짜종목("034020", "두산에너빌리티"), _시세)}}


def _전략():
    return VolumeSurgeStrategy(VolumeSurgeParams(exit_sma=20), name="volume_surge_5d_ma20")


def _한줄(산날: date, 정책=None, symbol="034020"):
    보유 = [가짜보유(symbol, 산날)]
    return 보유현황(보유, 섹터시세, _전략(), 정책 or RiskPolicy())[0]


def test_전략이_정한_보유일수를_쓴다():
    assert _한줄(date(2026, 8, 27)).상한 == 5


def test_진입일_다음_거래일부터_센다():
    """8월 27일에 샀으면 28일과 31일 둘이 지났다. 세 거래일 남는다."""
    assert _한줄(date(2026, 8, 27)).남은거래일 == 3


def test_주말은_안_센다():
    """8월 28일(금)에 샀으면 31일(월) 하나만 지났다.

    달력으로 세면 사흘이 지난 것이 되어 이틀 일찍 판다고 적게 된다."""
    assert _한줄(date(2026, 8, 28)).남은거래일 == 4


def test_상한에_닿으면_0이_된다():
    """8월 24일 매수 → 25·26·27·28·31 다섯 거래일. 오늘 판다."""
    assert _한줄(date(2026, 8, 24)).남은거래일 == 0


def test_시세가_없으면_못_셌다고_한다():
    """0으로 채우면 '오늘 판다'로 읽힌다. 그것과 '못 셌다'는 다른 말이다."""
    한줄 = _한줄(date(2026, 8, 27), symbol="999999")
    assert 한줄.남은거래일 is None
    assert 한줄.name == "999999"


def test_기준에서_보유기간을_덮으면_그_값을_쓴다():
    """`보유상한()`이 내는 답을 그대로 따라야 화면·알림·매매가 같은 말을 한다."""
    한줄 = _한줄(date(2026, 8, 27), 정책=RiskPolicy(max_holding_days=10))
    assert 한줄.상한 == 10
    assert 한줄.남은거래일 == 8


def test_먼저_산_것이_위에_온다():
    """먼저 팔릴 것이 위에 있어야 훑어보고 바로 안다."""
    보유 = [가짜보유("034020", date(2026, 8, 28)), 가짜보유("034020", date(2026, 8, 24))]
    줄들 = 보유현황(보유, 섹터시세, _전략(), RiskPolicy())
    assert [ㄱ.entry_date for ㄱ in 줄들] == [date(2026, 8, 24), date(2026, 8, 28)]
