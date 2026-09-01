"""기초전략과 세부전략.

## 나눈 이유

매매를 사는 쪽과 파는 쪽으로 나누고, 각 쪽을 두 층으로 본다.

- **기초전략** — 어떤 전략을 걸든 항상 걸리는 것. 손절선, 익절선,
  보유 기간 상한, 비중, 동시 보유 수 같은 것들이다.
- **세부전략** — 지금 걸려 있는 전략. 무엇을 살지, 어떤 신호에 팔지.

## 여기서 지키는 것

보유 기간은 원래 전략 안에만 있었다(거래량 급증 5일 → 5일). 기초로 올리면서
**답을 내는 자리가 하나여야 한다.** 실제로 파는 엔진, 전략 화면의 청산 조건
목록, 매수 알림의 매도전략 줄 셋이 이 답을 안다. 각자 계산하면 화면과 매매가
어긋나고, 어긋난 줄도 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from muwon.dashboard.strategy_rules import exit_rules
from muwon.execution.engine import 매도규칙, 매수알림
from muwon.risk.exits import 보유상한
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategies, build_strategy


@dataclass
class 주문:
    quantity: int = 43
    price: float = 33581.0
    ordered_quantity: int = 43
    잔여: int = 0


# ── 며칠까지 들고 있나 ───────────────────────────────────────────────────


def test_0이면_전략이_정한_대로():
    """0은 '안 정했다'가 아니라 '전략에게 맡긴다'는 뜻이다."""
    전략 = build_strategy("volume_surge_5d_ma20")  # 스스로 5일

    assert 보유상한(전략, RiskPolicy()) == 5
    assert 보유상한(전략, RiskPolicy(max_holding_days=0)) == 5


def test_숫자를_넣으면_전략이_정한_기간을_덮는다():
    전략 = build_strategy("volume_surge_5d_ma20")

    assert 보유상한(전략, RiskPolicy(max_holding_days=3)) == 3
    assert 보유상한(전략, RiskPolicy(max_holding_days=20)) == 20


def test_전략에_기간이_없어도_기초에서_줄_수_있다():
    """MACD 교차는 스스로 기간 상한이 없다. 기초에서 씌우면 생긴다."""
    전략 = build_strategy("macd_cross")

    assert 보유상한(전략, RiskPolicy()) is None
    assert 보유상한(전략, RiskPolicy(max_holding_days=10)) == 10


def test_매수와_매도를_따로_걸면_파는_쪽_기간을_쓴다():
    """보유 기간도 청산 규칙이다. 파는 자리를 두 군데로 나누면 왜 팔렸는지
    설명할 수 없게 된다."""
    묶음 = build_strategies(("macd_cross",), "OR", ("volume_surge_5d_ma20",))

    assert 보유상한(묶음, RiskPolicy()) == 5


# ── 세 자리가 같은 답을 쓰는가 ───────────────────────────────────────────


@pytest.mark.parametrize("덮개", [0, 3, 12])
def test_엔진과_화면과_알림이_같은_기간을_말한다(덮개):
    전략 = build_strategy("volume_surge_5d_ma20")
    정책 = RiskPolicy(max_holding_days=덮개)
    답 = 보유상한(전략, 정책)

    조건, _ = exit_rules(전략, 정책)
    화면 = [ㄱ for ㄱ in 조건 if "보유기간 만료" in ㄱ]
    알림 = [ㄱ for ㄱ in 매도규칙(전략, 정책, 산값=100.0) if "거래일이 지나면" in ㄱ]

    assert 화면 and f"{답}거래일" in 화면[0]
    assert 알림 and f"{답}거래일" in 알림[0]


def test_기초에서_덮었으면_그렇다고_적는다():
    """전략이 5일이라고 적혀 있는데 3일에 팔리면 화면이 거짓말한 것이 된다."""
    전략 = build_strategy("volume_surge_5d_ma20")
    조건, _ = exit_rules(전략, RiskPolicy(max_holding_days=3))
    보유줄 = next(ㄱ for ㄱ in 조건 if "보유기간 만료" in ㄱ)

    assert "기본 전략에서 설정한 값" in 보유줄


def test_안_덮었으면_그런_말을_안_붙인다():
    전략 = build_strategy("volume_surge_5d_ma20")
    조건, _ = exit_rules(전략, RiskPolicy())
    보유줄 = next(ㄱ for ㄱ in 조건 if "보유기간 만료" in ㄱ)

    assert "기본 전략에서 설정한 값" not in 보유줄


def test_매수_알림에도_덮었다는_말이_붙는다():
    글 = 매수알림("한국전력", "015760", 주문(), "거래량 급증",
                전략=build_strategy("volume_surge_5d_ma20"),
                정책=RiskPolicy(max_holding_days=3))

    assert "3거래일이 지나면 오르든 내리든 매도 (기준에서 정한 값)" in 글


# ── 시트에서 오는가 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("시트값", "정책값"), [("0", 0), ("3", 3), ("20", 20)])
def test_시트의_보유기간이_정책에_들어간다(시트값, 정책값):
    from muwon.settings.from_sheet import apply, parse_settings

    정책, 출처 = apply(RiskPolicy(), parse_settings({"max_holding_days": 시트값}))

    assert 정책.max_holding_days == 정책값
    assert 출처["max_holding_days"] == "시트"


def test_보유기간은_정수여야_한다():
    """2.5거래일은 뜻이 없다."""
    from muwon.settings.from_sheet import SettingsError, parse_settings

    with pytest.raises(SettingsError, match="정수"):
        parse_settings({"max_holding_days": "2.5"})


def test_보유기간은_음수를_못_받는다():
    from muwon.settings.from_sheet import SettingsError, parse_settings

    with pytest.raises(SettingsError):
        parse_settings({"max_holding_days": "-1"})


# ── 화면이 이 값들을 다 가지고 있는가 ────────────────────────────────────


기초칸 = [
    ("비중", "max_position_weight"),
    ("동시보유", "max_concurrent_positions"),
    ("섹터당", "max_per_sector"),
    ("거래대금", "min_turnover_eok"),
    ("하루손실", "daily_loss_limit_pct"),
    ("승인필요", "require_approval"),
    ("손절", "stop_loss_pct"),
    ("익절", "take_profit_pct"),
    ("보유기간", "max_holding_days"),
]


@pytest.mark.parametrize(("칸id", "기준이름"), 기초칸, ids=[ㄱ for ㄱ, _ in 기초칸])
def test_기초전략_칸이_화면에_있다(칸id, 기준이름):
    """시트에는 있는데 화면에 없으면 바꿀 길이 없다. 실제로 다섯 개가
    그 상태였다."""
    from pathlib import Path

    from muwon.settings.from_sheet import 기준표

    쪽 = (Path(__file__).resolve().parent.parent / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )

    assert 기준이름 in 기준표, f"{기준이름}이 시트 기준에 없습니다"
    assert f'id="{칸id}"' in 쪽, f"{칸id} 칸이 화면에 없습니다"


def test_전략_설명에_보유일이_실린다():
    """화면의 '0이면 전략이 정한 대로'가 몇 일인지 말하려면 이 숫자가 필요하다."""
    from tests.scripts_for_test import 전략설명

    줄들 = {r["키"]: r for r in 전략설명()}

    assert 줄들["volume_surge_5d_ma20"]["보유일"] == 5
    assert 줄들["gap_up_go"]["보유일"] == 1
    assert 줄들["macd_cross"]["보유일"] is None
