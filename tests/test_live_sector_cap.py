"""09:05 매수도 섹터당 보유 상한을 지킨다.

## 무엇이 문제였나 (2026-09-02)

섹터 상한은 08:30 후보를 뽑을 때만 걸렸다. 그 계산(`cap_per_sector`)은 후보
목록을 0부터 세기 때문에 **이미 들고 있는 종목을 모른다.** 반도체를 두
종목 들고 있어도 그날 후보에 반도체 세 종목이 들어올 수 있었고, 다 승인하면
반도체 다섯 종목이 됐다. 분산한 줄 알았는데 사실상 한 섹터에 다섯 배로 건
것이다.

화면의 설명문은 "동일 섹터에서 동시에 보유할 수 있는 최대 종목 수"라고
적혀 있었다. 적힌 것과 도는 것이 달랐다.

## 두 곳을 같이 고쳐야 한다

09:05만 고치면 후보에는 뜨는데 안 사는 종목이 생겨서 "승인했는데 왜 안
샀지"가 남는다. 08:30도 들고 있는 것부터 세게 했다. 여기 시험이 양쪽을
같이 본다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from muwon.cloud.approval import 상한넘긴것, 알림글
from muwon.cloud.approval import 후보 as 후보목록
from muwon.data.universe import Ticker
from muwon.db.models import PositionRow
from muwon.db.session import make_session_factory
from muwon.execution.approved_universe import 섹터표만들기
from muwon.execution.engine import TradingEngine
from muwon.execution.simulated_executor import SimulatedOrderExecutor
from muwon.risk.manager import RiskManager
from muwon.sector.selection import cap_per_sector
from muwon.settings.schema import RiskPolicy
from muwon.strategy.rule_based import MovingAverageRsiStrategy
from tests.price_series import flat_then_breakout

티커들 = [
    Ticker("000001", "반도체가", "KOSPI", "000001.KS"),
    Ticker("000002", "반도체나", "KOSPI", "000002.KS"),
    Ticker("000003", "반도체다", "KOSPI", "000003.KS"),
    Ticker("000004", "2차전지가", "KOSPI", "000004.KS"),
]
섹터표 = {
    "000001": "반도체", "000002": "반도체", "000003": "반도체",
    "000004": "2차전지",
}


class 가짜시세:
    def __init__(self, 틀: dict[str, pd.DataFrame]):
        self.틀 = 틀

    def get_daily_ohlcv(self, symbol, start, end):
        빈것 = pd.DataFrame(
            columns=["trade_date", "open", "high", "low", "close", "volume"]
        )
        return self.틀.get(symbol, 빈것)


class 가짜알림:
    def send(self, message: str) -> None:
        pass


def 엔진(섹터상한: int, 미리보유: list[str] | None = None):
    """네 종목 전부에 같은 매수 신호가 뜨는 판을 만든다."""
    틀 = {t.symbol: flat_then_breakout(tail_days=0) for t in 티커들}
    session_factory = make_session_factory("sqlite:///:memory:")
    for 심볼 in 미리보유 or []:
        마지막 = 틀[심볼].iloc[-1]
        with session_factory() as session:
            # 진입일을 마지막 봉으로, 진입가를 그날 종가로 둔다. 오늘 아침에
            # 청산되면 자리가 비어서 다시 사는 것이 맞는 동작이라, 그러면
            # 정작 재려는 것(들고 있는 것을 세는가)을 못 잰다.
            session.add(PositionRow(
                symbol=심볼, quantity=1, entry_price=float(마지막["close"]),
                entry_date=마지막["trade_date"],
                entry_reason="시험", strategy_key="시험",
            ))
            session.commit()
    return TradingEngine(
        strategy=MovingAverageRsiStrategy(),
        risk_manager=RiskManager(policy_provider=lambda: RiskPolicy(
            max_position_weight=0.15, max_concurrent_positions=8,
        )),
        data_source=가짜시세(틀),
        order_executor=SimulatedOrderExecutor(),
        notifier=가짜알림(),
        session_factory=session_factory,
        universe=티커들,
        source_symbol=lambda t: t.symbol,
        섹터표=섹터표,
        섹터상한=섹터상한,
    )


def 산심볼(summary) -> set[str]:
    return {a.symbol for a in summary.actions if a.side.value == "buy"}


def test_한_섹터에서_상한만큼만_산다():
    summary = 엔진(섹터상한=2).run_once()
    산것 = 산심볼(summary)
    반도체 = {ㅅ for ㅅ in 산것 if 섹터표[ㅅ] == "반도체"}
    assert len(반도체) == 2
    assert "000004" in 산것, "다른 섹터는 상한과 무관하게 산다"


def test_이미_들고_있는_것을_센다():
    """반도체 2종목을 이미 들고 있으면 상한 2에서는 더 안 산다.

    여기가 이번에 고친 자리다. 옛 코드는 보유를 안 세서 반도체를 두 종목
    더 샀고, 결과가 네 종목이 됐다."""
    summary = 엔진(섹터상한=2, 미리보유=["000001", "000002"]).run_once()
    산것 = 산심볼(summary)
    assert {ㅅ for ㅅ in 산것 if 섹터표[ㅅ] == "반도체"} == set()
    assert "000004" in 산것


def test_안_산_까닭을_남긴다():
    """조용히 건너뛰면 '승인했는데 왜 안 샀지'가 남는다."""
    summary = 엔진(섹터상한=1, 미리보유=["000001"]).run_once()
    까닭 = summary.거부사유.get("000002", "")
    assert "반도체" in 까닭
    assert "섹터당 보유 상한" in 까닭
    assert any("반도체" in ㄱ for ㄱ in summary.rejections)


def test_상한이_0이면_제한하지_않는다():
    assert len(산심볼(엔진(섹터상한=0).run_once())) == 4


def test_음수_상한이_매수를_막지_않는다():
    """백테스트 엔진에서 실제로 겪은 자리다. 음수를 그대로 두면 첫 종목부터
    상한에 걸려 매수가 통째로 막힌다."""
    assert len(산심볼(엔진(섹터상한=-1).run_once())) == 4


def test_섹터를_모르는_종목은_묶지_않는다():
    """섹터를 모르는 종목을 빈 이름 하나로 묶으면 서로 관계없는 종목들이
    한 섹터가 되어, 실제로는 분산돼 있는데 상한에 걸린다."""
    엔 = 엔진(섹터상한=1)
    엔._섹터표 = {}
    assert len(산심볼(엔.run_once())) == 4


# ── 08:30 후보 산출도 같은 규칙으로 센다 ──────────────────────────


class 후보:
    def __init__(self, symbol, sector):
        self.symbol, self.sector = symbol, sector


def test_후보_산출도_보유를_세면_같은_답이_나온다():
    """09:05만 고치면 후보에는 뜨는데 안 사는 종목이 생긴다."""
    줄선것 = [후보("000002", "SEMI"), 후보("000003", "SEMI"), 후보("000004", "BATT")]
    남김, 밀림 = cap_per_sector(줄선것, 상한=2, 시작={"SEMI": 2})
    assert [c.symbol for c in 남김] == ["000004"]
    assert [c.symbol for c in 밀림] == ["000002", "000003"]


def test_시작을_안_주면_예전과_같다():
    줄선것 = [후보("000002", "SEMI"), 후보("000003", "SEMI"), 후보("000004", "BATT")]
    남김, 밀림 = cap_per_sector(줄선것, 상한=2)
    assert [c.symbol for c in 남김] == ["000002", "000003", "000004"]
    assert 밀림 == []


# ── 섹터표 만들기 ────────────────────────────────────────────────


class 가짜종목:
    def __init__(self, symbol, 활성=True):
        self.symbol, self.활성 = symbol, 활성


class 가짜섹터:
    def __init__(self, 코드, 이름, 종목):
        self.코드, self.이름, self.종목 = 코드, 이름, 종목


def test_섹터표는_활성이_꺼진_종목도_넣는다():
    """지금은 안 사지만 예전에 사서 들고 있을 수 있다. 들고 있는 것은
    상한을 셀 때 세야 한다."""
    표 = 섹터표만들기([
        가짜섹터("SEMI", "반도체", [가짜종목("000001"), 가짜종목("000002", 활성=False)]),
        가짜섹터("BATT", "2차전지", [가짜종목("000004")]),
    ])
    assert 표 == {"000001": "반도체", "000002": "반도체", "000004": "2차전지"}


# ── 알림에 빨간 램프로 적는다 ────────────────────────────────────


def _넘긴것(보유수=3, 오늘후보수=0, 상한=3):
    return 상한넘긴것(symbol="247540", name="에코프로비엠", 섹터이름="2차전지",
                   보유수=보유수, 오늘후보수=오늘후보수, 상한=상한)


def test_상한에_걸린_것을_빨간_램프로_적는다():
    """조용히 빼면 오늘 전략이 무엇을 찾았는지가 안 보인다."""
    글 = 알림글([], date(2026, 9, 2), "http://시트", 살펴본수=60,
             상한초과=[_넘긴것()])
    assert "🔴" in 글
    assert "에코프로비엠(247540)" in 글
    assert "2차전지" in 글
    assert "섹터당 보유 상한은 3종목입니다" in 글


def test_승인_버튼이_없다고_적는다():
    """09:05가 같은 상한을 다시 보므로 눌러도 안 산다. 그 사실을 적어야
    '승인했는데 왜 안 샀지'가 안 남는다."""
    글 = 알림글([], date(2026, 9, 2), "http://시트", 상한초과=[_넘긴것()])
    assert "승인 버튼을 만들지 않았습니다" in 글


def test_막힌_까닭을_보유와_오늘_후보로_나눠_적는다():
    """사람이 할 일이 다르다. 이미 들고 있어서 막힌 것은 팔려야 자리가
    나고, 오늘 후보끼리 밀린 것은 다른 것을 거절하면 된다."""
    보유만 = 알림글([], date(2026, 9, 2), "http://시트",
                 상한초과=[_넘긴것(보유수=3, 오늘후보수=0)])
    assert "2차전지를 이미 3종목 들고 있어" in 보유만
    assert "오늘 후보에" not in 보유만

    섞임 = 알림글([], date(2026, 9, 2), "http://시트",
                상한초과=[_넘긴것(보유수=2, 오늘후보수=1)])
    assert "2차전지를 이미 2종목 들고 있고" in 섞임
    assert "오늘 후보에 1종목이 더 있어" in 섞임

    오늘만 = 알림글([], date(2026, 9, 2), "http://시트",
                 상한초과=[_넘긴것(보유수=0, 오늘후보수=3)])
    assert "들고 있고" not in 오늘만
    assert "오늘 후보에 2차전지가 3종목 있어" in 오늘만


def test_후보가_있는_날에도_같이_적는다():
    글 = 알림글(
        [후보목록(symbol="005930", name="삼성전자", strategy="x",
               quantity=1, price=70000.0, sector="SEMI", sector_name="반도체")],
        date(2026, 9, 2), "http://시트", 상한초과=[_넘긴것()],
    )
    assert "삼성전자(005930)" in 글
    assert "🔴 [2차전지] 에코프로비엠(247540)" in 글


def test_넘긴_것이_없으면_아무_말도_안_한다():
    """이유가 없는데 한 줄 만들면 매번 뭔가 뜨고, 매번 뜨는 것은 정작
    중요할 때도 넘기게 된다."""
    글 = 알림글([], date(2026, 9, 2), "http://시트", 상한초과=[])
    assert "🔴" not in 글
    assert "섹터 보유 상한" not in 글


def test_사람에게_가는_글에_줄표를_안_쓴다():
    글 = 알림글([], date(2026, 9, 2), "http://시트",
             상한초과=[_넘긴것(보유수=2, 오늘후보수=1)])
    for 줄 in 글.splitlines():
        if "🔴" in 줄 or "섹터당 보유 상한" in 줄:
            assert "—" not in 줄
