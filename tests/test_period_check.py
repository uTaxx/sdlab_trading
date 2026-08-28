"""기간별 전략 검증의 순수 계산 부분.

시세를 받아 오는 부분은 여기서 안 다룬다. 여기서 지키려는 것은 **날짜를
자르는 규칙**과 **제일 나빴던 토막을 찾는 규칙**이다. 둘 다 조용히 틀리는
자리다 — 한 달 밀려도 표는 멀쩡해 보이고, 토막의 시작값을 잘못 잡아도
숫자가 그럴듯하게 나온다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from muwon.analysis.experiment import WARMUP_DAYS
from muwon.analysis.period_check import (
    slice_for_range,
    검증용정책,
    구간,
    기간들,
    기간표,
    기준글,
    달빼기,
    토막수익률,
)
from muwon.settings.schema import RiskPolicy


def test_달빼기가_달의_마지막_날을_넘지_않는다():
    """3월 31일에서 한 달을 빼면 2월 31일은 없다. 2월 마지막 날로 간다."""
    assert 달빼기(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert 달빼기(date(2024, 3, 31), 1) == date(2024, 2, 29)   # 윤년
    assert 달빼기(date(2026, 1, 15), 1) == date(2025, 12, 15)  # 해를 넘는다
    assert 달빼기(date(2026, 8, 28), 60) == date(2021, 8, 28)


def test_기간이_셋이고_5년이_제일_길다():
    assert [ㄱ.이름 for ㄱ in 기간들] == ["3개월", "12개월", "5년"]
    assert max(기간들, key=lambda ㄱ: ㄱ.달수).이름 == "5년"
    # 짧은 것은 달로, 5년은 해로 쪼갠다. 5년을 달로 쪼개면 토막이 예순 개라
    # 제일 나빴던 한 달은 언제나 크게 나쁘고 그래서 아무 말도 못 한다.
    assert 기간표["3개월"].쪼갬 == "달"
    assert 기간표["5년"].쪼갬 == "해"


def test_구간의_끝은_오늘이고_시작은_달수만큼_앞이다():
    assert 구간(기간표["12개월"], date(2026, 8, 28)) == (date(2025, 8, 28), date(2026, 8, 28))


def _시세(날짜들: list[date]) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": 날짜들, "close": [1.0] * len(날짜들)})


def test_자를_때_예열을_앞에_붙인다():
    """예열이 모자라면 200일 이동평균이 안 채워진 채로 돈다. 그러면 전략이
    나빴던 것인지 켜지지도 않았던 것인지 구별이 안 된다."""
    날짜들 = [date(2024, 1, 1) + pd.Timedelta(days=ㄴ).to_pytimedelta() for ㄴ in range(0, 900, 5)]
    잘린것 = slice_for_range({"005930": _시세(날짜들)}, date(2026, 5, 1), date(2026, 6, 1))
    남은날 = 잘린것["005930"]["trade_date"]
    assert min(남은날) < date(2026, 5, 1), "예열 구간이 안 붙었습니다"
    assert min(남은날) >= date(2026, 5, 1) - pd.Timedelta(days=WARMUP_DAYS + 5).to_pytimedelta()
    assert max(남은날) <= date(2026, 6, 1)


def test_시세가_구간_밖에만_있으면_그_종목은_빠진다():
    잘린것 = slice_for_range(
        {"005930": _시세([date(2019, 1, 1), date(2019, 2, 1)])},
        date(2026, 5, 1), date(2026, 6, 1),
    )
    assert 잘린것 == {}


def _곡선(줄들: list[tuple[date, float]]) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [ㄱ for ㄱ, _ in 줄들], "equity": [ㄴ for _, ㄴ in 줄들]})


def test_토막의_시작값은_앞_토막의_끝값이다():
    """토막 안의 첫 값을 쓰면 토막이 바뀌는 사이에 난 움직임이 어느 쪽에도
    안 잡힌다. 그러면 토막 수익률을 다 곱해도 전체 수익률이 안 나온다."""
    곡선 = _곡선([
        (date(2026, 1, 5), 100.0),
        (date(2026, 1, 30), 110.0),
        (date(2026, 2, 3), 105.0),
        (date(2026, 2, 27), 99.0),
    ])
    토막 = dict(토막수익률(곡선, "달"))
    assert round(토막["2026-01"], 6) == 10.0
    # 110 → 99. 토막 안의 첫 값(105)을 쓰면 -5.7%가 나온다.
    assert round(토막["2026-02"], 6) == -10.0


def test_해로_쪼개면_한_해가_한_토막이다():
    곡선 = _곡선([
        (date(2025, 3, 1), 100.0),
        (date(2025, 12, 30), 120.0),
        (date(2026, 6, 1), 90.0),
    ])
    토막 = 토막수익률(곡선, "해")
    assert [이름 for 이름, _ in 토막] == ["2025년", "2026년"]
    assert [round(값, 6) for _, 값 in 토막] == [20.0, -25.0]


def test_곡선이_짧으면_토막이_없다():
    assert 토막수익률(_곡선([(date(2026, 1, 1), 100.0)]), "달") == []
    assert 토막수익률(None, "달") == []


def test_검증용정책은_스위치_둘만_켠다():
    """킬스위치가 꺼져 있으면 한 주도 안 사서 0.0%이 나온다. 화면에는 그것이
    '이 전략은 아무것도 못 번다'로 보인다. 조용히 틀린 답이다."""
    원본 = RiskPolicy(
        trading_enabled=False, sell_enabled=False,
        stop_loss_pct=-0.07, take_profit_pct=0.1, max_holding_days=3,
        max_position_weight=0.2, max_concurrent_positions=5,
    )
    바뀐것 = 검증용정책(원본)
    assert 바뀐것.trading_enabled is True
    assert 바뀐것.sell_enabled is True
    # 나머지는 그대로여야 한다. 지금 걸어 둔 기준으로 잰 숫자를 보는 것이
    # 이 기능의 목적이라, 손절이나 비중까지 기본값으로 돌리면 뜻이 없어진다.
    assert 바뀐것 == replace(원본, trading_enabled=True, sell_enabled=True)


def test_기준글에_조건이_다_들어간다():
    """조건 없는 숫자는 나중에 검증할 수가 없다."""
    글 = 기준글(RiskPolicy(stop_loss_pct=-0.05, take_profit_pct=0.0), 45, "시가총액")
    for 있어야할것 in ("손절 -5%", "익절 끔", "보유 전략이 정한 대로",
                     "45종목", "다음 날 시가 체결", "슬리피지 0"):
        assert 있어야할것 in 글, f"{있어야할것}이 기준글에 없습니다"

    켠것 = 기준글(RiskPolicy(take_profit_pct=0.1, max_holding_days=3), 45, "시가총액")
    assert "익절 10%" in 켠것
    assert "보유 3일" in 켠것
