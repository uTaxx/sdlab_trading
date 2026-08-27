"""익절선이 전략 위에 걸리는가.

## 왜 이 파일이 있나

익절은 **전략이 아니라 기준**이다. 어떤 전략을 걸든 그 위에서 걸린다.
손절과 같은 자리에 있다.

그 개념은 처음부터 있었는데(`RiskPolicy.take_profit_pct`), 켜고 끄는
길과 켜졌을 때 보이는 길이 없었다. 화면은 "익절은 이 시스템에 아예
없습니다"라고 단언했고, 매수 알림의 매도전략 줄에도 안 실렸다.

**켜 뒀는데 화면이 없다고 말하는 것이 제일 나쁘다.** 그러면 익절로 팔린
날 "왜 갑자기 팔렸지"에 답할 곳이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from muwon.dashboard.strategy_rules import exit_rules
from muwon.execution.engine import 매도규칙, 매수알림
from muwon.risk.exits import evaluate_exit
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy


@dataclass
class 주문:
    quantity: int = 43
    price: float = 33581.0
    ordered_quantity: int = 43
    잔여: int = 0


# ── 엔진이 실제로 집행하는가 ─────────────────────────────────────────────


def test_익절선에_닿으면_판다():
    판정 = evaluate_exit(
        entry_price=100.0, entry_date=date(2026, 8, 20), current_price=110.0,
        as_of=date(2026, 8, 27), policy=RiskPolicy(take_profit_pct=0.10),
    )

    assert 판정.should_exit
    assert "익절" in 판정.reason


def test_익절선_아래면_그대로_둔다():
    판정 = evaluate_exit(
        entry_price=100.0, entry_date=date(2026, 8, 20), current_price=109.0,
        as_of=date(2026, 8, 27), policy=RiskPolicy(take_profit_pct=0.10),
    )

    assert not 판정.should_exit


def test_0이면_아무리_올라도_익절로_안_판다():
    """0은 빈 값이 아니라 '끈다'는 뜻이다."""
    판정 = evaluate_exit(
        entry_price=100.0, entry_date=date(2026, 8, 20), current_price=500.0,
        as_of=date(2026, 8, 27), policy=RiskPolicy(take_profit_pct=0.0),
    )

    assert not 판정.should_exit


def test_손절이_익절보다_먼저다():
    """둘 다 걸릴 수는 없지만 순서를 못 박아 둔다. 손실을 막는 쪽이 언제나
    먼저여야 한다."""
    판정 = evaluate_exit(
        entry_price=100.0, entry_date=date(2026, 8, 20), current_price=90.0,
        as_of=date(2026, 8, 27),
        policy=RiskPolicy(stop_loss_pct=-0.05, take_profit_pct=0.10),
    )

    assert 판정.reason == "손절"


def test_어느_전략을_걸든_똑같이_걸린다():
    """익절은 전략 위에 있다. 전략을 바꿔도 이 규칙은 그대로다."""
    정책 = RiskPolicy(take_profit_pct=0.10)
    for 키 in ("volume_surge_5d", "volume_surge_5d_ma20", "ma_rsi_v1", "macd_cross"):
        줄들 = 매도규칙(build_strategy(키), 정책, 산값=100.0)
        assert any("익절" in ㄱ for ㄱ in 줄들), 키


# ── 화면과 알림이 그것을 아는가 ──────────────────────────────────────────


def test_켜_두면_매수_알림에_익절선이_가격으로_적힌다():
    """'+10%'는 규칙이지 가격이 아니다. 사람은 가격을 봐야 안다."""
    글 = 매수알림("한국전력", "015760", 주문(), "거래량 급증",
                전략=build_strategy("volume_surge_5d"),
                정책=RiskPolicy(take_profit_pct=0.10))

    assert "+10% 익절 (36,939원에서 매도)" in 글


def test_꺼_두면_매수_알림에_익절이_안_적힌다():
    """없는 규칙을 적으면 화면이 거짓말을 한다."""
    글 = 매수알림("한국전력", "015760", 주문(), "거래량 급증",
                전략=build_strategy("volume_surge_5d"), 정책=RiskPolicy())

    assert "익절" not in 글


def test_켜_두면_전략_화면이_없다고_말하지_않는다():
    """전에는 켜 뒀을 때도 '익절은 이 시스템에 아예 없습니다'라고 적었다."""
    조건, 주의 = exit_rules(build_strategy("volume_surge_5d"), RiskPolicy(take_profit_pct=0.10))

    assert any("익절" in ㄱ for ㄱ in 조건)
    assert not any("익절" in ㄱ and "없습니다" in ㄱ for ㄱ in 주의)


def test_꺼_두면_왜_껐는지까지_적는다():
    """'없다'와 '재 보고 뺐다'는 다른 말이다. 뒤쪽이어야 다시 재 보지 않는다."""
    조건, 주의 = exit_rules(build_strategy("volume_surge_5d"), RiskPolicy())

    assert not any("익절" in ㄱ for ㄱ in 조건)
    익절주의 = [ㄱ for ㄱ in 주의 if "익절" in ㄱ]
    assert 익절주의
    assert "꺼져 있습니다" in 익절주의[0]
    assert "0.28%" in 익절주의[0], "왜 뺐는지 숫자가 있어야 한다"


# ── 시트에서 오는 값을 제대로 읽는가 ─────────────────────────────────────


@pytest.mark.parametrize(("시트값", "정책값"), [("0", 0.0), ("10", 0.10), ("7.5", 0.075)])
def test_시트의_퍼센트를_비율로_바꾼다(시트값, 정책값):
    """시트에는 10으로 적고 코드는 0.10을 쓴다. 그대로 넘기면 1,000%다."""
    from muwon.settings.from_sheet import parse_settings

    시트 = parse_settings({"take_profit_pct": 시트값})

    assert 시트.덮개["take_profit_pct"] == pytest.approx(정책값)


def test_시트에서_켠_익절이_실제_정책에_들어간다():
    """읽는 것과 쓰이는 것 사이가 끊어져 있으면 화면만 켜지고 매매는 그대로다."""
    from muwon.settings.from_sheet import apply, parse_settings

    정책, 출처 = apply(RiskPolicy(), parse_settings({"take_profit_pct": "12"}))

    assert 정책.take_profit_pct == pytest.approx(0.12)
    assert 출처["take_profit_pct"] == "시트"


def test_익절은_음수를_못_받는다():
    """음수 익절은 뜻이 없다. 손절 자리에 잘못 넣은 것이다."""
    from muwon.settings.from_sheet import 기준표

    assert 기준표["take_profit_pct"].최소 == 0
