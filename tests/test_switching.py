"""매일 갈아탔다면 어땠을까를 재는 계산이 맞게 도는가.

## 이 파일이 지키는 것 셋

**하나, 미래를 안 본다.** D일까지의 시세로 고른 답이 D일 뒤의 시세에 따라
달라지면 안 된다. 달라지면 그날 오를 종목을 미리 보고 고른 것이 되어,
나온 수익률이 통째로 뜻이 없어진다. 이건 눈으로 봐서는 안 드러난다.
숫자가 그럴듯하게 나오고, 좋게 나올수록 더 그럴듯해 보인다.

**둘, 계좌가 이어진다.** 전략이 바뀌어도 어제 산 종목을 그대로 들고 있어야
한다. 구간을 잘라 따로 백테스트한 수익률을 이어 붙이면 구간이 바뀔 때마다
보유 종목이 사라지고 현금에서 다시 시작해서, 실제로는 낼 수 없는 성적이
나온다.

**셋, 보유기간이 전략을 따라온다.** 변동성 돌파는 1일이고 거래량 급증
5일은 5일이다. 전략을 바꾸면 들고 있던 종목의 청산 시점도 같이 바뀐다.
실거래가 그렇게 한다. 엔진이 실행 전에 한 번만 읽으면 이걸 못 잰다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.analysis.period_check import 기간표
from muwon.analysis.switching import 갈아타기전략, 굴리기, 날마다고르기
from muwon.backtest.engine import BacktestEngine
from muwon.domain.types import Signal, SignalType
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import MarketContext, PortfolioStrategy
from muwon.strategy.registry import build_strategy
from tests.price_series import flat_then_breakout, make_price_df


def _오름세(날수: int = 90, 시작값: float = 100.0) -> pd.DataFrame:
    """꾸준히 오르되 하루씩 쉬는 시계열. 지표가 극단으로 붙지 않게 한다."""
    값 = []
    ㄱ = 시작값
    for i in range(날수):
        ㄱ *= 1.02 if i % 3 else 0.995
        값.append(round(ㄱ, 2))
    return make_price_df(값)


# ── 갈아타기 전략이 엔진 위에서 제대로 도는가 ────────────────────


def test_한_전략으로_고정하면_그_전략을_직접_돌린_것과_같다():
    """갈아타기 껍데기가 결과를 바꾸지 않는다는 것부터 못박는다. 여기가
    어긋나면 뒤의 모든 비교가 껍데기 탓인지 갈아탄 탓인지 알 수 없다."""
    시세 = {"A": flat_then_breakout(), "B": _오름세()}
    정책 = RiskPolicy()
    시작 = 시세["A"]["trade_date"].iloc[30]
    끝 = 시세["A"]["trade_date"].iloc[-1]

    직접 = 굴리기(시세, build_strategy("ma_rsi_v1"), 시작, 끝, 정책)
    껍데기 = 굴리기(시세, 갈아타기전략({}, build_strategy, "ma_rsi_v1"), 시작, 끝, 정책)

    assert 직접 is not None and 껍데기 is not None
    assert 직접[1].total_return_pct == pytest.approx(껍데기[1].total_return_pct)
    assert 직접[1].num_trades == 껍데기[1].num_trades


def test_날짜에_없는_날은_처음_전략을_쓴다():
    전략 = 갈아타기전략({date(2026, 8, 10): "macd_cross"}, build_strategy, "ma_rsi_v1")

    assert 전략.그날키(date(2026, 8, 10)) == "macd_cross"
    assert 전략.그날키(date(2026, 8, 11)) == "ma_rsi_v1"


# ── 보유기간이 날마다 다시 물어진다 ──────────────────────────────


class _상한이바뀌는전략(PortfolioStrategy):
    """첫날 사고, 그 뒤로는 날마다 다른 보유기간을 답한다.

    실제 전략이 아니라 엔진이 보유기간을 **언제** 읽는지만 재기 위한 것이다."""

    name = "상한바뀜"

    def __init__(self, 살날: date, 상한표: dict[date, int | None]):
        self._살날 = 살날
        self._상한표 = 상한표
        self._오늘: date | None = None

    @property
    def max_holding_days(self) -> int | None:
        return self._상한표.get(self._오늘)

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        self._오늘 = ctx.as_of
        if ctx.as_of != self._살날:
            return []
        return [
            Signal(symbol="A", trade_date=ctx.as_of, signal_type=SignalType.BUY,
                   strategy_name=self.name, score=1.0, reason="시험용 매수")
        ]


def _엔진(전략, 정책):
    return BacktestEngine(
        strategy=전략,
        risk_manager=RiskManager(policy_provider=lambda p=정책: p),
        entry_at_open=True,
        exit_at_open=True,
    )


def test_보유기간을_날마다_다시_읽는다():
    """전략이 바뀌면 들고 있던 종목의 청산 시점도 같이 바뀐다.

    실행 전에 한 번만 읽으면 첫날의 값으로 끝까지 간다. 그러면 매일
    갈아타는 경우를 아예 재 볼 수가 없다."""
    시세 = {"A": _오름세(40)}
    날들 = list(시세["A"]["trade_date"])
    살날 = 날들[10]

    # 처음에는 넉넉히 들고 있다가, 산 지 이틀째부터 상한을 1일로 좁힌다.
    상한표 = {ㄴ: (99 if ㄴ <= 날들[12] else 1) for ㄴ in 날들}
    결과 = _엔진(_상한이바뀌는전략(살날, 상한표), RiskPolicy()).run(시세, trade_from=날들[5])

    assert 결과.num_trades == 1, "한 번 사고 한 번 팔아야 합니다"
    팔린것 = 결과.closed_trades[0]
    assert 팔린것.exit_reason.startswith("보유 상한 1거래일")
    assert 팔린것.exit_date > 날들[12], "상한이 좁아진 뒤에 팔려야 합니다"


def test_기초설정의_보유기간이_전략의_값을_덮는다():
    """실거래 엔진은 `risk/exits.보유상한()`을 쓴다. 백테스트가 전략의 값만
    읽으면, 기초설정에 보유기간을 넣어 둔 순간 두 쪽이 다른 규칙으로 돌고
    아무것도 빨개지지 않는다."""
    시세 = {"A": _오름세(40)}
    날들 = list(시세["A"]["trade_date"])
    상한표 = dict.fromkeys(날들, 99)

    결과 = _엔진(
        _상한이바뀌는전략(날들[10], 상한표), RiskPolicy(max_holding_days=2)
    ).run(시세, trade_from=날들[5])

    assert 결과.num_trades == 1
    assert 결과.closed_trades[0].exit_reason.startswith("보유 상한 2거래일")


def test_기초설정이_0이면_전략이_정한_대로_간다():
    """0은 끔이 아니라 '전략에게 맡김'이다. 여기가 뒤집히면 보유기간이
    통째로 사라진다."""
    시세 = {"A": _오름세(40)}
    날들 = list(시세["A"]["trade_date"])
    상한표 = dict.fromkeys(날들, 3)

    결과 = _엔진(
        _상한이바뀌는전략(날들[10], 상한표), RiskPolicy(max_holding_days=0)
    ).run(시세, trade_from=날들[5])

    assert 결과.num_trades == 1
    assert 결과.closed_trades[0].exit_reason.startswith("보유 상한 3거래일")


# ── 미래를 안 본다 ──────────────────────────────────────────────


def _시세두벌():
    """뒷날이 서로 다른 두 벌. 앞부분은 같고 마지막 닷새만 갈린다."""
    바탕 = {"A": flat_then_breakout(flat_days=60), "B": _오름세(66)}
    자른날 = 바탕["A"]["trade_date"].iloc[-6]

    앞만 = {ㄱ: df[df["trade_date"] <= 자른날].copy() for ㄱ, df in 바탕.items()}

    # 뒷날을 크게 흔든 벌. 미래를 보면 순위가 흔들려야 하는 자료다.
    흔든것 = {}
    for ㄱ, df in 바탕.items():
        ㅅ = df.copy()
        뒤 = ㅅ["trade_date"] > 자른날
        for 칸 in ("open", "high", "low", "close"):
            ㅅ.loc[뒤, 칸] = ㅅ.loc[뒤, 칸] * 3.0
        흔든것[ㄱ] = ㅅ
    return 앞만, 흔든것, 자른날


def test_잰_날_뒤의_시세가_고른_결과를_바꾸지_않는다():
    """이 파일에서 제일 중요한 시험이다.

    뒷날을 세 배로 흔든 자료와 아예 잘라 낸 자료로 같은 날 순위를 낸다.
    답이 갈리면 계산이 미래를 보고 있다는 뜻이고, 그러면 이 검증으로 낸
    수익률은 실제로 낼 수 있는 숫자가 아니다."""
    앞만, 흔든것, 자른날 = _시세두벌()
    정책 = RiskPolicy()
    키들 = ["ma_rsi_v1", "macd_cross", "volume_surge_5d", "golden_cross_5_20"]
    적용날 = 자른날 + timedelta(days=1)

    잘라서 = 날마다고르기(기간표["1개월"], 앞만, [자른날], [적용날], 정책, 키들,
                     build_strategy, "ma_rsi_v1")
    흔들어서 = 날마다고르기(기간표["1개월"], 흔든것, [자른날], [적용날], 정책, 키들,
                      build_strategy, "ma_rsi_v1")

    assert 잘라서[0].고른키 == 흔들어서[0].고른키
    assert [ㄱ for ㄱ, _, _ in 잘라서[0].위쪽] == [ㄱ for ㄱ, _, _ in 흔들어서[0].위쪽]


# ── 고르는 규칙 ─────────────────────────────────────────────────


def test_거래가_없던_전략은_1위가_될_수_없다():
    """수익률 0%로 맨 위에 오는데 그건 지킨 것이 아니라 아무것도 안 한
    것이다. 순위에서 빼는 규칙이 `구간순위.산것`과 같아야 한다."""
    시세 = {"A": flat_then_breakout(flat_days=60)}
    날들 = list(시세["A"]["trade_date"])
    잰날 = 날들[-2]
    키들 = ["ma_rsi_v1", "macd_cross", "volume_surge_5d", "rsi_reversion",
           "golden_cross_20_60", "donchian_20_10"]

    선택 = 날마다고르기(기간표["1개월"], 시세, [잰날], [날들[-1]], RiskPolicy(),
                    키들, build_strategy, "ma_rsi_v1")[0]

    assert all(ㄷ > 0 for _, _, ㄷ in 선택.위쪽), "거래 0건이 순위에 남았습니다"


def test_잴_날과_적용할_날의_개수가_다르면_거부한다():
    """짝이 어긋나면 D일 순위를 엉뚱한 날에 적용하게 된다. 조용히 어긋나면
    결과가 미묘하게만 틀려서 알아채기 어렵다."""
    with pytest.raises(ValueError, match="개수가 다릅니다"):
        날마다고르기(기간표["1개월"], {"A": _오름세()}, [date(2026, 8, 3)],
                 [date(2026, 8, 4), date(2026, 8, 5)], RiskPolicy(),
                 ["ma_rsi_v1"], build_strategy, "ma_rsi_v1")


# ── 언제 갈아탈 것인가 ──────────────────────────────────────────


def _하루(적용날: date, 순위: list[tuple[str, float, int]]):
    from muwon.analysis.switching import 하루선택

    전체 = [(ㄱ, ㄴ, ㄷ, -1.0) for ㄱ, ㄴ, ㄷ in 순위]
    return 하루선택(
        잰날=적용날 - timedelta(days=1), 적용날=적용날,
        고른키=전체[0][0], 앞선키="", 위쪽=[(ㄱ, ㄴ, ㄷ) for ㄱ, ㄴ, ㄷ, _ in 전체[:3]],
        전체=전체,
    )


def _사흘(첫날=date(2026, 8, 3)):
    """사흘 내내 다른 전략이 1위인 순위. 지금 것은 줄곧 3위다."""
    return [
        _하루(첫날, [("A", 10.0, 30), ("B", 5.0, 30), ("지금", 1.0, 30)]),
        _하루(첫날 + timedelta(days=1), [("B", 12.0, 30), ("A", 6.0, 30), ("지금", 1.0, 30)]),
        _하루(첫날 + timedelta(days=2), [("A", 14.0, 30), ("B", 7.0, 30), ("지금", 1.0, 30)]),
    ]


def test_매일_1위_규칙은_1위를_그대로_따라간다():
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    표 = 규칙적용(_사흘(), "지금", 갈아타기규칙("매일 1위", ""))

    assert list(표.values()) == ["A", "B", "A"]


def test_우위배수가_모자라면_안_바꾼다():
    """조금 앞서는 것으로 바꾸면 매번 바뀐다. 그게 이 배수가 있는 이유다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("A", 10.0, 30), ("지금", 9.5, 30)])]

    assert 규칙적용(하루, "지금", 갈아타기규칙("느슨", "", 우위배수=1.0))[date(2026, 8, 3)] == "A"
    assert 규칙적용(하루, "지금", 갈아타기규칙("깐깐", "", 우위배수=1.15))[date(2026, 8, 3)] == "지금"


def test_최소운용일_안에는_다시_안_바꾼다():
    """한 번 바꾸면 그 뒤 이틀은 1위가 바뀌어도 그대로 가야 한다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    표 = 규칙적용(_사흘(), "지금", 갈아타기규칙("천천히", "", 최소운용일=2))

    assert list(표.values()) == ["A", "A", "A"]


def test_지금_전략이_그_구간에_매수를_안_했으면_안_바꾼다():
    """비교할 짝이 없다. `strategy_fit.후보내기`도 이 경우에 후보를 안 낸다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("A", 10.0, 30), ("B", 5.0, 30)])]

    assert 규칙적용(하루, "지금", 갈아타기규칙("매일 1위", ""))[date(2026, 8, 3)] == "지금"


def test_거래수가_모자란_1위는_따라가지_않는다():
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("A", 30.0, 3), ("지금", 1.0, 30)])]
    규 = 갈아타기규칙("표본요구", "", 최소거래수=20)

    assert 규칙적용(하루, "지금", 규)[date(2026, 8, 3)] == "지금"


def test_1위가_이미_지금_것이면_그대로_간다():
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("지금", 10.0, 30), ("A", 5.0, 30)])]

    assert 규칙적용(하루, "지금", 갈아타기규칙("매일 1위", ""))[date(2026, 8, 3)] == "지금"


def test_무조건_1위를_따라가는_규칙은_거래_0건에_안_막힌다():
    """지금 걸린 전략이 그 구간에 매수를 안 했으면 실제 검토는 후보를 안
    낸다. 비교할 짝이 없기 때문이다. 그런데 **"무조건 1위를 따라간다"에
    그 막음이 걸리면** 거래를 안 한 전략에 한 번 걸린 뒤로 거기서 멈춰
    버려서, 이름과 달리 1위를 안 따라가는 규칙이 된다.

    2026-09-01에 그렇게 잰 숫자를 한 번 보고했다. 갈아타기가 실제보다
    좋아 보였다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    # 지금 걸린 '멈춘것'이 순위에 아예 없다. 거래가 0건이라 빠진 것이다.
    하루 = [_하루(date(2026, 8, 3), [("A", 10.0, 30), ("B", 5.0, 30)])]

    막힘 = 규칙적용(하루, "멈춘것", 갈아타기규칙("실제 규칙", "", 우위배수=1.15))
    따라감 = 규칙적용(하루, "멈춘것",
                  갈아타기규칙("매일 1위", "", 지금없어도바꾼다=True))

    assert 막힘[date(2026, 8, 3)] == "멈춘것", "실제 검토는 후보를 안 냅니다"
    assert 따라감[date(2026, 8, 3)] == "A", "무조건 따라가는 규칙은 1위로 갑니다"


def test_지금없어도바꾼다가_다른_막음까지_풀지는_않는다():
    """표본과 최소 운용기간은 그대로 걸려야 한다. 안 그러면 이 값 하나가
    규칙 전체를 무력화한다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("A", 30.0, 3), ("B", 5.0, 30)])]
    규 = 갈아타기규칙("표본요구", "", 최소거래수=20, 지금없어도바꾼다=True)

    assert 규칙적용(하루, "멈춘것", 규)[date(2026, 8, 3)] == "멈춘것"


def test_지금이_순위에_있으면_평소대로_견준다():
    """`지금없어도바꾼다`는 지금 것이 순위에 **없을 때만** 다르게 굴어야
    한다. 있을 때까지 우위 비교를 건너뛰면 배수가 뜻이 없어진다."""
    from muwon.analysis.switching import 갈아타기규칙, 규칙적용

    하루 = [_하루(date(2026, 8, 3), [("A", 10.0, 30), ("지금", 9.5, 30)])]
    규 = 갈아타기규칙("깐깐", "", 우위배수=1.15, 지금없어도바꾼다=True)

    assert 규칙적용(하루, "지금", 규)[date(2026, 8, 3)] == "지금"
