"""매수 후보 산출이 어떤 종류의 전략에도 도는가.

## 무엇이 문제였나 (2026-09-04)

`propose_buys.py`가 종목마다 `strategy.generate_signals(심볼, df)`를 불렀다.
옛 방식 전략(Strategy)에만 있는 메서드다. 여러 종목을 같이 봐야 하는
전략(PortfolioStrategy)에는 없다.

9월 3일에 미국 섹터를 보는 전략으로 바꿨고, 그 뒤 첫 평일인 9월 4일
08:30 실행이 AttributeError로 통째로 멈췄다. **그날 매수 후보가 하나도
안 나왔다.**

전략을 바꾸는 것과 후보를 내는 것이 서로 다른 곳에 있어서, 바꾸는 쪽은
성공했는데 내는 쪽이 다음 날 아침에 죽었다.

## 여기서 막는 것

등록된 전략 전부가 후보 산출이 쓰는 길을 지나갈 수 있어야 한다. 백테스트와
실거래 엔진은 이미 `as_portfolio_strategy`로 감싸서 쓰고 있었다. 후보
산출만 옛 길에 남아 있었다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.strategy.portfolio import MarketContext, as_portfolio_strategy
from muwon.strategy.registry import REGISTRY, build_strategies

#: 미국 ETF 시세를 받아 와야 하는 전략. 시험이 네트워크에 걸리면 안 된다.
#: 이들은 매매 대상에 섹터 종목이 없으면 받으러 가지 않는다.
바깥을보는것 = {"us_sector_follow_60_2", "volume_surge_3d_us60_2"}


def _시세(종목수: int = 3, 날수: int = 300) -> dict[str, pd.DataFrame]:
    시작 = date(2024, 1, 1)
    날들 = [시작 + timedelta(days=ㄱ) for ㄱ in range(날수)]
    표 = {}
    for ㄴ in range(종목수):
        값 = [100.0 + (ㄱ % 17) - (ㄴ * 3) for ㄱ in range(날수)]
        표[f"00000{ㄴ}"] = pd.DataFrame({
            "trade_date": 날들,
            "open": 값, "high": [ㄱ * 1.01 for ㄱ in 값],
            "low": [ㄱ * 0.99 for ㄱ in 값], "close": 값,
            "volume": [100_000 + (ㄱ % 7) * 20_000 for ㄱ in range(날수)],
        })
    return 표


@pytest.mark.parametrize("키", [ㄷ.key for ㄷ in REGISTRY], ids=lambda ㅋ: ㅋ)
def test_모든_전략이_후보_산출_길을_지나간다(키):
    """`propose_buys.py`가 쓰는 것과 같은 순서다. 감싸고, 예열하고, 오늘을 묻는다."""
    시세 = _시세()
    껍데기 = as_portfolio_strategy(build_strategies([키]))
    껍데기.prepare(시세)
    신호 = 껍데기.evaluate(MarketContext(
        as_of=시세["000000"]["trade_date"].iloc[-1],
        histories=시세,
        held=frozenset(),
    ))
    assert isinstance(신호, list)


def test_후보_산출이_옛_메서드를_안_부른다():
    """`generate_signals`를 직접 부르면 여러 종목을 같이 보는 전략에서 죽는다.
    실제로 08:30 실행이 그렇게 멈췄다."""
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent
          / "scripts" / "propose_buys.py").read_text(encoding="utf-8")
    코드 = "\n".join(줄 for 줄 in 글.splitlines()
                   if not 줄.lstrip().startswith("#"))
    assert "strategy.generate_signals" not in 코드
    assert "as_portfolio_strategy" in 코드


def test_감싸도_옛_방식_전략의_신호가_같다():
    """껍데기를 씌우는 것이 결과를 바꾸면 안 된다. 지금까지 낸 후보가
    달라졌다는 뜻이 되고, 그러면 이 수정이 조용한 변경이 된다."""
    시세 = _시세()
    속 = build_strategies(["volume_surge_3d"])
    마지막 = 시세["000000"]["trade_date"].iloc[-1]

    옛것 = [ㅅ for 심볼, df in 시세.items()
          for ㅅ in 속.generate_signals(심볼, df) if ㅅ.trade_date == 마지막]

    껍데기 = as_portfolio_strategy(build_strategies(["volume_surge_3d"]))
    껍데기.prepare(시세)
    새것 = [ㅅ for ㅅ in 껍데기.evaluate(MarketContext(
        as_of=마지막, histories=시세, held=frozenset()))]

    assert {(ㅅ.symbol, ㅅ.signal_type) for ㅅ in 옛것} == \
           {(ㅅ.symbol, ㅅ.signal_type) for ㅅ in 새것}


def test_미국_시세가_필요한_전략은_목록에_적혀_있다():
    """새로 등록할 때 이 목록을 안 고치면 시험이 네트워크를 타게 된다."""
    from muwon.strategy.registry import get_definition

    for 키 in 바깥을보는것:
        assert get_definition(키), 키
