"""장중 손절 감시.

## 왜 시험하나

이 자리는 **돈이 나가는 자리인데 하루에 수십 번 돈다.** 조용히 틀리면
그만큼 자주 틀린다.

막아야 하는 것 셋.

1. 장이 닫혀 있을 때 주문이 나가는 것
2. 증권사에 없는 종목을 파는 것 (이미 팔린 것을 또 파는 것)
3. 09:05 회차와 **다른 규칙으로** 파는 것
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "watch_stops.py"
_스펙 = importlib.util.spec_from_file_location("watch_stops_for_test", _경로)
watch_stops = importlib.util.module_from_spec(_스펙)
sys.modules["watch_stops_for_test"] = watch_stops
_스펙.loader.exec_module(watch_stops)

KST = ZoneInfo("Asia/Seoul")


def _때(연, 월, 일, 시, 분):
    return datetime(연, 월, 일, 시, 분, tzinfo=KST)


# ── 장 시간 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("시", "분"), [(9, 0), (9, 5), (12, 30), (15, 19), (15, 20)]
)
def test_장중이면_돈다(시, 분):
    돌까, _ = watch_stops.장중인가(_때(2026, 8, 27, 시, 분))  # 목요일
    assert 돌까


@pytest.mark.parametrize(
    ("시", "분", "무엇"),
    [(8, 59, "장 전"), (15, 21, "장 끝"), (20, 0, "밤"), (3, 0, "새벽")],
)
def test_장_시간이_아니면_안_돈다(시, 분, 무엇):
    돌까, 까닭 = watch_stops.장중인가(_때(2026, 8, 27, 시, 분))
    assert not 돌까, 무엇
    assert 까닭


def test_주말에는_안_돈다():
    토 = watch_stops.장중인가(_때(2026, 8, 29, 12, 0))
    일 = watch_stops.장중인가(_때(2026, 8, 30, 12, 0))

    assert not 토[0] and "주말" in 토[1]
    assert not 일[0] and "주말" in 일[1]


def test_동시호가_구간에는_안_돈다():
    """15:20~15:30 시장가는 예상체결가로 접수돼 본 값과 다르게 체결된다."""
    돌까, 까닭 = watch_stops.장중인가(_때(2026, 8, 27, 15, 25))

    assert not 돌까
    assert "장이 끝났습니다" in 까닭


# ── 어떤 규칙으로 파는가 ─────────────────────────────────────────────────


def test_고정_비율_손절만_켜져_있으면_일봉이_필요_없다():
    from muwon.settings.schema import RiskPolicy

    assert not watch_stops._일봉이필요한가(RiskPolicy())


@pytest.mark.parametrize("칸", ["atr_stop_enabled", "trailing_stop_enabled"])
def test_변동성_규칙을_켜면_일봉이_필요하다(칸):
    """안 받으면 evaluate_exit가 고정 % 손절로 되돌아간다. 그건 09:05
    회차와 다른 규칙으로 파는 것이고, 조용히 다르게 파는 것이 제일 나쁘다."""
    from muwon.settings.schema import RiskPolicy

    assert watch_stops._일봉이필요한가(RiskPolicy(**{칸: True}))


# ── 손절 판단이 09:05 회차와 같은가 ──────────────────────────────────────


def test_손절선_아래면_판다고_한다():
    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    정책 = RiskPolicy(stop_loss_pct=-0.05)
    판정 = evaluate_exit(
        entry_price=33581.0, entry_date=date(2026, 8, 26),
        current_price=31900.0, as_of=date(2026, 8, 27),
        policy=정책, atr=None, history=None,
    )

    assert 판정.should_exit
    assert 판정.reason == "손절"


def test_손절선_위면_그대로_둔다():
    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    판정 = evaluate_exit(
        entry_price=33581.0, entry_date=date(2026, 8, 26),
        current_price=31910.0, as_of=date(2026, 8, 27),
        policy=RiskPolicy(stop_loss_pct=-0.05), atr=None, history=None,
    )

    assert not 판정.should_exit


def test_같은_값이면_09시05분_회차와_같은_판단을_한다():
    """장중 감시와 하루 한 번 회차가 같은 evaluate_exit를 쓴다. 규칙이
    갈라지면 어느 쪽이 맞는지 알 수 없게 된다."""
    from muwon.risk.exits import evaluate_exit
    from muwon.settings.schema import RiskPolicy

    정책 = RiskPolicy(stop_loss_pct=-0.05)
    인자 = dict(  # noqa: C408
        entry_price=100.0, entry_date=date(2026, 8, 20),
        as_of=date(2026, 8, 27), policy=정책, atr=None, history=None,
    )

    assert evaluate_exit(current_price=94.0, **인자).should_exit
    assert not evaluate_exit(current_price=96.0, **인자).should_exit
