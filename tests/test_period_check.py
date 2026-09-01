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


def test_기간이_짧은_것부터_다섯이다():
    assert [ㄱ.이름 for ㄱ in 기간들] == ["1주", "1개월", "3개월", "12개월", "5년"]
    assert max(기간들, key=lambda ㄱ: ㄱ.달수).이름 == "5년"
    # 쪼개는 단위는 구간 길이를 따라간다. 5년을 달로 쪼개면 토막이 예순 개라
    # 제일 나빴던 한 달은 언제나 크게 나쁘고 그래서 아무 말도 못 한다.
    # 반대로 1개월을 달로 쪼개면 토막이 하나라 구간 전체와 같아진다.
    assert 기간표["1주"].쪼갬 == "날"
    assert 기간표["1개월"].쪼갬 == "주"
    assert 기간표["3개월"].쪼갬 == "달"
    assert 기간표["5년"].쪼갬 == "해"


def test_1주는_달이_아니라_날로_거슬러_올라간다():
    """1주는 달로 안 떨어진다. 달수를 0으로 두고 날수를 쓴다."""
    assert 기간표["1주"].달수 == 0
    assert 구간(기간표["1주"], date(2026, 8, 28)) == (date(2026, 8, 21), date(2026, 8, 28))
    assert 구간(기간표["1개월"], date(2026, 8, 28)) == (date(2026, 7, 28), date(2026, 8, 28))


def test_날과_주로_쪼갠_토막에_이름이_붙는다():
    """이름이 시간 순으로 정렬돼야 제일 나빴던 토막을 옳게 찾는다."""
    from muwon.analysis.period_check import 토막수익률

    곡선 = pd.DataFrame({
        "trade_date": [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)],
        "equity": [100.0, 90.0, 99.0],
    })
    날토막 = 토막수익률(곡선, "날")
    assert [ㅌ[0] for ㅌ in 날토막] == ["2026-08-24", "2026-08-25", "2026-08-26"]
    assert 날토막 == sorted(날토막)

    # 셋이 같은 ISO 주(2026-35주)에 든다. 한 토막으로 묶인다.
    주토막 = 토막수익률(곡선, "주")
    assert len(주토막) == 1
    assert 주토막[0][0] == "2026-35주"


def test_전략신호는_1주와_1개월을_안_본다():
    """거래가 몇 건뿐이라 한 종목이 숫자를 통째로 만든다. 그것으로 신호를
    내면 매주 뭔가 뜨고, 매주 뜨는 것은 진짜일 때도 안 읽힌다."""
    from muwon.analysis.period_check import 전략신호

    나쁜짧은것 = {
        "1주": _성적("1주", total_return_pct=-15.0, max_drawdown_pct=-15.0,
                  num_trades=1, exposure_pct=3.0),
        "1개월": _성적("1개월", total_return_pct=-12.0, max_drawdown_pct=-12.0,
                   num_trades=0, exposure_pct=0.0),
    }
    assert 전략신호(나쁜짧은것) == []


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


# ── 평가 문장 ──────────────────────────────────────────────────────
#
# 이 문장은 시트에 같이 쌓여서 나중에 "그때 뭐라고 적혀 있었지"의 답이 된다.
# 그래서 숫자에서 바로 나오는 말만 적어야 하고, 표본이 없을 때 그 사실을
# 빠뜨리면 안 된다.

def _성적(이름="3개월", **덮개):
    from muwon.analysis.period_check import 기간성적
    from muwon.backtest.metrics import BacktestMetrics

    기본 = {
        "total_return_pct": -8.0, "cagr_pct": -8.0, "max_drawdown_pct": -9.0,
        "sharpe": 0.0, "sortino": 0.0, "profit_factor": 0.5,
        "expectancy_pct": -1.0, "win_rate_pct": 30.0, "num_trades": 40,
        "avg_holding_days": 5.0, "exposure_pct": 60.0, "turnover": 1.0,
    }
    기본.update({k: v for k, v in 덮개.items() if k in 기본})
    return 기간성적(
        이름=이름,
        시작=date(2026, 5, 28),
        끝=date(2026, 8, 28),
        metrics=BacktestMetrics(**기본),
        토막들=덮개.get("토막들", [("2026-06", -7.0), ("2026-07", 1.0)]),
        모자람=덮개.get("모자람", ""),
    )


def test_한_주도_안_샀으면_그것부터_말한다():
    """거래 0건은 수익률 0%로 나온다. 화면에서 그것이 제일 위에 오는데,
    지킨 것이 아니라 아무 일도 안 한 것이다."""
    from muwon.analysis.period_check import 평가글

    글 = 평가글(_성적(num_trades=0, total_return_pct=0.0), 기간표["3개월"])
    assert "매수가 발생하지 않았습니다" in 글
    assert "거래 자체가 없었던 것" in 글


def test_표본이_적으면_판단하지_말라고_적는다():
    from muwon.analysis.period_check import 평가글

    글 = 평가글(_성적(num_trades=9), 기간표["3개월"])
    assert "9건에 불과합니다" in 글
    assert "판단하기 어렵습니다" in 글


def test_최대낙폭이_깊으면_그_뜻을_적는다():
    """이 저장소가 제일 먼저 보는 숫자다. -32%를 숫자로만 찍으면 안 읽는다."""
    from muwon.analysis.period_check import 평가글

    깊은것 = 평가글(_성적(max_drawdown_pct=-32.3), 기간표["5년"])
    assert "고점 대비 30% 이상 감소" in 깊은것
    얕은것 = 평가글(_성적(max_drawdown_pct=-8.0), 기간표["5년"])
    assert "일반적인 변동 범위" in 얕은것


def test_해로_쪼갠_구간이_다_플러스면_그렇게_적는다():
    from muwon.analysis.period_check import 평가글

    글 = 평가글(
        _성적("5년", 토막들=[("2021년", 6.7), ("2022년", 12.0)]), 기간표["5년"]
    )
    assert "손실이 발생한 해가 없습니다" in 글


def test_총평에_지금_걸린_것의_순위가_들어간다():
    from muwon.analysis.period_check import 비교총평

    줄들 = [
        ("a", _성적(total_return_pct=5.0)),
        ("b", _성적(total_return_pct=-3.0)),
        ("c", _성적(total_return_pct=-9.0)),
        ("빈것", _성적(num_trades=0, total_return_pct=0.0)),
    ]
    글 = 비교총평(기간표["3개월"], 줄들, 지금키="b")
    assert "매수가 발생한 전략 3개" in 글
    assert "3개 중 2위" in 글
    assert "매수가 발생하지 않은 전략 1개" in 글
    # 1등을 고르라는 말은 절대 안 적는다.
    assert "과최적화" in 글


def test_총평이_구간_문제인지를_말한다():
    """대부분이 마이너스면 전략을 바꿀 일이 아니라 장이 나빴던 것이다."""
    from muwon.analysis.period_check import 비교총평

    줄들 = [(f"s{i}", _성적(total_return_pct=-5.0 - i)) for i in range(10)]
    글 = 비교총평(기간표["3개월"], 줄들)
    assert "해당 기간의 시장 영향으로 보입니다" in 글


# ── 전략 변경 신호 ─────────────────────────────────────────────────
#
# 이 신호는 "바꿔라"가 아니라 "봐야 한다"다. 한 구간에서 제일 좋았던 것으로
# 갈아타는 일이 곧 과최적화라, 그런 말은 여기서 절대 안 나와야 한다.

def test_이유가_없으면_아무_신호도_안_낸다():
    """억지로 한 줄 만들면 매번 뭔가 뜨고, 그러면 진짜일 때도 안 읽는다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    성적들 = {
        "3개월": _성적("3개월", total_return_pct=2.0, max_drawdown_pct=-5.0),
        "12개월": _성적("12개월", total_return_pct=20.0, max_drawdown_pct=-10.0),
        "5년": _성적("5년", total_return_pct=100.0, max_drawdown_pct=-25.0),
    }
    assert 전략신호(성적들) == []
    등급, 글 = 신호글([])
    assert 등급 == "이상없음"
    assert "수익성이 양호하다는 의미가 아니라" in 글


def test_5년이_마이너스면_확인필요다():
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호({"5년": _성적("5년", total_return_pct=-12.0, num_trades=300)})
    등급, 글 = 신호글(난것)
    assert 등급 == "확인필요"
    assert "유지할 근거를 확인하기 어렵습니다" in 글


def test_한_주도_안_사면_확인필요다():
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호({"3개월": _성적("3개월", num_trades=0, exposure_pct=0.0)})
    등급, _ = 신호글(난것)
    assert 등급 == "확인필요"


def test_최악_구간이_최근에_몰리면_살펴볼것이다():
    """12개월 낙폭이 5년 낙폭에 거의 닿으면, 5년을 통틀어 제일 나빴던 구간이
    바로 최근 1년 안에 있다는 뜻이다. 옛날 얘기가 아니다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호({
        "12개월": _성적("12개월", max_drawdown_pct=-31.4, total_return_pct=24.9),
        "5년": _성적("5년", max_drawdown_pct=-32.3, total_return_pct=154.9),
    })
    등급, 글 = 신호글(난것)
    assert 등급 == "살펴볼것"
    assert "최근 1년" in 글 and "5년 전체" in 글


def test_다_같이_나쁘면_구간_문제라고_말한다():
    """장이 나빠서 다 마이너스인 것은 전략을 바꿀 이유가 아니다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호(
        {"3개월": _성적("3개월", total_return_pct=-20.0, max_drawdown_pct=-22.0)},
        {"3개월": (22, 26, 1)},
    )
    등급, 글 = 신호글(난것)
    assert 등급 == "이상없음"
    assert "시장 영향이 큰 것으로 보입니다" in 글


def test_남들은_버텼는데_하위권이면_살펴볼것이다():
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호(
        {"12개월": _성적("12개월", total_return_pct=-3.0, max_drawdown_pct=-15.0)},
        {"12개월": (24, 26, 20)},
    )
    등급, 글 = 신호글(난것)
    assert 등급 == "살펴볼것"
    assert "이 전략이 해당 구간과 맞지 않았을 가능성" in 글


def test_신호글은_바꾸라고_말하지_않는다():
    """한 구간에서 제일 좋았던 것을 고르는 일이 곧 과최적화다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호({"5년": _성적("5년", total_return_pct=-12.0, num_trades=300)})
    _, 글 = 신호글(난것)
    assert "전략 변경을 지시하는 것이 아니라 검토를 권고하는 것" in 글
    for 하면안되는말 in ("로 바꾸세요", "로 갈아타", "추천"):
        assert 하면안되는말 not in 글


def test_사람에게_가는_문장에_줄표를_안_쓴다():
    """CLAUDE.md의 보고 말투다. 줄표(—)는 쉼표와 마침표로 끊는다.

    이 문장들은 텔레그램과 화면에 그대로 나간다. 여기서 한 번 새면
    화면·알림·시트에 동시에 남고, 나중에 어디를 고쳐야 하는지 헷갈린다."""
    from muwon.analysis.period_check import 기간표, 비교총평, 신호글, 전략신호, 평가글

    난것 = 전략신호(
        {
            "3개월": _성적("3개월", total_return_pct=-20.0, max_drawdown_pct=-22.0),
            "12개월": _성적("12개월", max_drawdown_pct=-31.4, total_return_pct=24.9),
            "5년": _성적("5년", max_drawdown_pct=-32.3, total_return_pct=154.9),
        },
        {"3개월": (22, 26, 1), "12개월": (24, 26, 20)},
    )
    글들 = [신호글(난것)[1], 신호글([])[1]]
    for 이름, 정의 in 기간표.items():
        글들.append(평가글(_성적(이름, total_return_pct=-4.0), 정의))
    글들.append(비교총평(
        기간표["3개월"],
        [("volume_surge_5d_ma20", _성적("3개월", total_return_pct=-20.0)),
         ("macd_cross", _성적("3개월", total_return_pct=1.0))],
        "volume_surge_5d_ma20",
    ))
    for 글 in 글들:
        assert "—" not in 글, 글


def test_신호가_하나면_등급_딱지를_안_붙인다():
    """머리말에 이미 등급이 적힌다. 줄에도 붙이면 같은 말을 두 번 한다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    하나 = 전략신호({
        "12개월": _성적("12개월", max_drawdown_pct=-31.4, total_return_pct=24.9),
        "5년": _성적("5년", max_drawdown_pct=-32.3, total_return_pct=154.9),
    })
    assert len(하나) == 1
    assert "[살펴볼것]" not in 신호글(하나)[1]

    둘 = 전략신호(
        {
            "3개월": _성적("3개월", total_return_pct=-20.0, max_drawdown_pct=-22.0),
            "12개월": _성적("12개월", max_drawdown_pct=-31.4, total_return_pct=24.9),
            "5년": _성적("5년", max_drawdown_pct=-32.3, total_return_pct=154.9),
        },
        {"3개월": (22, 26, 1)},
    )
    assert len(둘) == 2
    assert "[살펴볼것]" in 신호글(둘)[1]


def test_신호글에_알아듣기_어려운_말을_안_쓴다():
    """폰으로 그대로 나가는 글이다. 앞뒤에 표도 설명도 없다.

    2026-08-28에 실제로 받아 보고 무슨 말인지 모르겠다는 말을 들었다.
    낙폭이라는 말을 풀어 쓰지 않았고, 이 저장소 안에서만 통하는 말인
    성적표를 그대로 썼다."""
    from muwon.analysis.period_check import 신호글, 전략신호

    난것 = 전략신호(
        {
            "3개월": _성적("3개월", total_return_pct=-20.0, exposure_pct=5.0),
            "12개월": _성적("12개월", max_drawdown_pct=-31.4, total_return_pct=24.9),
            "5년": _성적("5년", max_drawdown_pct=-32.3, total_return_pct=154.9),
        },
        {"3개월": (22, 26, 1)},
    )
    글 = 신호글(난것)[1]
    for 어려운말 in ("성적표", "같은 자로", "아팠던", "덜 맞"):
        assert 어려운말 not in 글, f"{어려운말!r}: {글}"
    # 낙폭은 쓰되 혼자 두지 않는다. 뜻을 같은 글 안에서 풀어 준다.
    if "낙폭" in 글:
        assert "평가금액이 고점 대비" in 글, 글

    # 평가글도 같은 자리에 뜬다. 여기만 비유가 남으면 화면에서 티가 난다.
    from muwon.analysis.period_check import 기간표, 평가글

    for 이름 in 기간표:
        평 = 평가글(_성적(이름, exposure_pct=8.0), 기간표[이름])
        for 어려운말 in ("덜 맞", "아팠던"):
            assert 어려운말 not in 평, f"{이름} {어려운말!r}: {평}"


def test_알림글에_무슨_전략인지와_번_것이_같이_들어간다():
    """화면과 달리 폰에는 이 글 하나만 간다. 표가 옆에 없다.

    어느 전략인지 안 적으면 무엇에 대한 말인지 알 수 없고, 번 것을 안 적으면
    줄어든 숫자만 남아서 다 망한 것처럼 읽힌다."""
    from muwon.analysis.period_check import 신호글, 알림글, 전략신호

    성적들 = {
        "3개월": _성적("3개월", total_return_pct=-20.74, max_drawdown_pct=-22.61),
        "12개월": _성적("12개월", total_return_pct=24.90, max_drawdown_pct=-31.40),
        "5년": _성적("5년", total_return_pct=154.86, max_drawdown_pct=-32.30),
    }
    난것 = 전략신호(성적들)
    등급, _ = 신호글(난것)
    글 = 알림글(등급, 난것, "거래량 급증 + 20일선", 성적들)

    assert "거래량 급증 + 20일선" in 글
    # 머리줄에는 저장값이 아니라 사람이 부르는 이름이 온다.
    assert 글.splitlines()[0] == "🔎 전략 점검 | 관찰 필요"
    # 구간 수익률이 다 들어가고, 순서는 짧은 것부터다. 한 줄에 셋씩 끊는다.
    assert "3개월 -20.74% · 12개월 +24.90% · 5년 +154.86%" in 글
    for 제목 in ("현재 전략", "성과", "⚠️ 점검 신호", "📌 판단"):
        assert 제목 in 글, f"{제목!r}이 없습니다: {글}"
    assert "—" not in 글
    # 굵게 표시는 못 쓴다. 꼬리를 HTML로 보내면서 본문을 이스케이프한다.
    assert "**" not in 글 and "<b>" not in 글


def test_파는쪽만_갈아_끼워_견줄_수_있다():
    """"사는 조건이 나쁜 건가 파는 조건이 나쁜 건가"를 가르는 자리다.

    `>전부`는 사는 쪽을 고정하고 파는 쪽을 하나씩 갈아 끼운다. 쉼표로 여럿을
    적는 것과 다르다. 그쪽은 OR로 묶어 파는 규칙 **하나**를 만든다."""
    import sys

    sys.path.insert(0, "scripts")
    from run_period_check import 아는열쇠, 전략고르기

    사는키, 파는키, 견주기 = 전략고르기("volume_surge_5d_ma20>전부")
    assert 사는키 == ["volume_surge_5d_ma20"]
    assert 파는키 == 아는열쇠()
    assert 견주기 is True

    # 쉼표는 예전 뜻 그대로다. 둘을 같은 글자로 쓰면 안 된다.
    _, 파는키, 견주기 = 전략고르기("volume_surge_5d_ma20>macd_cross,ma_rsi_v1")
    assert 파는키 == ["macd_cross", "ma_rsi_v1"]
    assert 견주기 is False

    # 사는 쪽도 파는 쪽도 다 돌리면 한 줄이 무엇을 바꾼 결과인지 알 수 없다.
    for 안되는것 in ("전부>전부", "volume_surge_3d,macd_cross>전부"):
        try:
            전략고르기(안되는것)
        except ValueError as 탈:
            assert "하나로 적으세요" in str(탈), 탈
        else:
            raise AssertionError(f"{안되는것!r}가 통과했습니다")


def test_유니버스를_시트로_고를_수_있다():
    """테스트가 보는 종목과 실제로 사는 종목이 달랐다(2026-08-31에 드러남).

    시가총액 상위 30종목은 run_paper_trading.py가 쓰고, 실제 매수 후보를
    뽑는 propose_buys.py는 구글 시트의 종목 탭을 읽는다. 시가총액으로만
    재면 나온 숫자가 실제 매매를 설명하지 못한다."""
    import sys

    sys.path.insert(0, "scripts")
    from run_period_check import 대상종목

    class 인자:
        유니버스 = "없는것"

    try:
        대상종목(인자, "시트없음", None)
    except ValueError as 탈:
        assert "모르는 유니버스" in str(탈), 탈
    else:
        raise AssertionError("모르는 값이 통과했습니다")

    인자.유니버스 = "시트"
    try:
        대상종목(인자, "", None)
    except ValueError as 탈:
        assert "시트를 못 찾아" in str(탈), 탈
    else:
        raise AssertionError("시트 없이 통과했습니다")


def test_기준글에_유니버스_종류가_들어간다():
    """시트에는 두 종류로 잰 줄이 같이 쌓인다. 줄마다 어느 목록으로 잰
    것인지 남아 있어야 나중에 비교할 수 있다."""
    from muwon.analysis.period_check import 기준글

    정책 = RiskPolicy()
    assert "유니버스 시가총액 30종목" in 기준글(정책, 30, "시가총액")
    assert "유니버스 섹터시트 63종목" in 기준글(정책, 63, "섹터시트")
