"""유사 구간 검색 검증.

이 표 하나로 "오늘 얼마나 살까"를 정할 참이다. 그래서 여기서 표본을
부풀리거나 미래를 보면, 근거 없는 자신감으로 돈을 걸게 된다."""

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.market.analog import MIN_EPISODES, forecast, format_forecast


def _표(n: int, 열: int = 2, 시작=date(2000, 1, 1)):
    """규칙적으로 되풀이되는 상태 표. 같은 상태가 주기적으로 돌아온다."""
    날짜 = [시작 + timedelta(days=i) for i in range(n)]
    자료 = {f"z{j}": [(i % 50) / 10 - 2.5 + j for i in range(n)] for j in range(열)}
    return pd.DataFrame(자료, index=pd.Index(날짜, name="trade_date"))


def _가격(n: int, 시작=date(2000, 1, 1), 값=None):
    날짜 = [시작 + timedelta(days=i) for i in range(n)]
    return pd.Series(값 or [100.0 + i * 0.1 for i in range(n)], index=날짜)


def test_it_refuses_to_give_numbers_when_the_past_is_too_thin():
    """구간 몇 개로 낸 '상승 확률 63%'는 확률이 아니다."""
    상태, 가격 = _표(60), _가격(60)
    f = forecast(상태, 가격, "시험")
    assert not f.낼수있나
    assert f.사유
    assert "전망 불가" in format_forecast(f)


def test_overlapping_days_collapse_into_one_episode():
    """연속된 날은 거의 같은 상태다. 안 묶으면 표본이 몇십 배 많은 것처럼
    착각하게 되고, 그게 이 방법의 가장 흔한 거짓말이다."""
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", top_pct=10)
    assert f.구간수 > 0
    # 뽑힌 날은 여럿인데 구간은 그보다 적어야 한다.
    assert f.총일수 > f.구간수


def test_episodes_never_reach_into_the_future():
    """어제와 비슷하다고 해 봐야, 어제의 '다음 20일'에는 오늘이 들어 있다.
    답을 미리 아는 셈이다."""
    n = 1500
    상태, 가격 = _표(n), _가격(n)
    기준일 = 상태.index[-1]
    f = forecast(상태, 가격, "시험", 기준일=기준일, horizon=20)
    한계 = 상태.index[-21]
    for e in f.구간들:
        assert e.대표일 <= 한계, f"{e.대표일}은 기준일에서 20일 안쪽이다"


def test_the_forecast_reports_episode_count_not_day_count():
    """화면에 일수만 쓰면 표본이 훨씬 많아 보인다."""
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", top_pct=10)
    글 = format_forecast(f)
    assert f"{f.구간수}개 구간" in 글
    assert f"총 {f.총일수}일" in 글


def test_a_rising_market_shows_a_positive_median():
    상태 = _표(1500)
    가격 = _가격(1500, 값=[100.0 * (1.001**i) for i in range(1500)])
    f = forecast(상태, 가격, "오르는 장", top_pct=10)
    if f.낼수있나:
        assert f.중앙값 > 0
        assert f.상승확률 > 50


def test_a_falling_market_shows_a_negative_median():
    상태 = _표(1500)
    가격 = _가격(1500, 값=[100.0 * (0.999**i) for i in range(1500)])
    f = forecast(상태, 가격, "내리는 장", top_pct=10)
    if f.낼수있나:
        assert f.중앙값 < 0


def test_the_percentiles_are_ordered():
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", top_pct=10)
    if f.낼수있나:
        assert f.하위10 <= f.하위25 <= f.중앙값 <= f.상위25


def test_the_report_leads_with_the_bad_case():
    """'아주 나빴을 때'가 이 표에서 비중을 정하는 칸이다."""
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", top_pct=10)
    if f.낼수있나:
        assert "아주 나빴을 때" in format_forecast(f)
        assert "비중을 정한다" in format_forecast(f)


def test_a_missing_reference_day_fails_softly():
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", 기준일=date(1990, 1, 1))
    assert not f.낼수있나
    assert "상태가 없습니다" in f.사유


def test_no_overlap_between_state_and_price_is_reported():
    상태 = _표(1500)
    가격 = _가격(1500, 시작=date(2100, 1, 1))
    f = forecast(상태, 가격, "시험")
    assert not f.낼수있나
    assert "겹치는 날짜가 없습니다" in f.사유


def test_the_minimum_is_enforced_exactly():
    """문턱이 흐물흐물하면 언제 믿어도 되는지 알 수 없다."""
    assert MIN_EPISODES >= 8
    상태, 가격 = _표(1500), _가격(1500)
    느슨한것 = forecast(상태, 가격, "시험", top_pct=10)
    빡센것 = forecast(상태, 가격, "시험", top_pct=10, min_episodes=느슨한것.구간수 + 1)
    assert not 빡센것.낼수있나
    assert "번뿐입니다" in 빡센것.사유


def test_shallow_history_and_too_few_episodes_give_different_reasons():
    """둘을 같은 문구로 내면 무엇을 고쳐야 할지 알 수 없다. 데이터를 더
    받아야 하는 건지, 문턱을 낮춰야 하는 건지."""
    얕은것 = forecast(_표(60), _가격(60), "시험")
    assert "비교할 과거가" in 얕은것.사유
    상태, 가격 = _표(1500), _가격(1500)
    구간부족 = forecast(상태, 가격, "시험", top_pct=10, min_episodes=10_000)
    assert "번뿐입니다" in 구간부족.사유


def test_percentile_helpers_do_not_crash_on_ties():
    상태 = _표(1500)
    가격 = _가격(1500, 값=[100.0] * 1500)
    f = forecast(상태, 가격, "제자리", top_pct=10)
    if f.낼수있나:
        assert f.중앙값 == pytest.approx(0.0)


def test_the_forecast_always_carries_a_baseline_to_compare_against():
    """'비슷한 날 뒤에 75% 올랐다'가 좋아 보여도, 아무 날에나 사도 75%
    올랐다면 전망은 아무것도 더하지 않은 것이다."""
    상태, 가격 = _표(1500), _가격(1500)
    f = forecast(상태, 가격, "시험", top_pct=10)
    if f.낼수있나:
        assert f.기준선 is not None
        assert f.기준선.표본수 > 0
        assert f.더한것_중앙값 is not None
        assert "그냥 아무 날에나 샀다면" in format_forecast(f)


def test_it_warns_when_the_forecast_adds_nothing():
    """전망과 기준선이 같으면 화면이 그렇다고 말해야 한다. 안 그러면
    쓸모없는 숫자를 근거로 돈을 걸게 된다."""
    from muwon.market.analog import Baseline, Forecast

    쓸모없는것 = Forecast(
        기준일=date(2020, 1, 1), 대상="시험", 구간수=20, 총일수=100, 지평=20,
        중앙값=1.0, 상위25=3.0, 하위25=-1.0, 하위10=-3.0, 상승확률=55.0, 구간들=[],
        기준선=Baseline(표본수=1000, 중앙값=1.0, 하위10=-3.0, 상승확률=55.0),
    )
    assert "아무것도 안 알려 주고 있습니다" in format_forecast(쓸모없는것)


def test_it_warns_about_survivorship_when_the_baseline_is_absurdly_good():
    """아무 날에나 사도 80% 올랐다면 그 지수 자체가 의심스럽다."""
    from muwon.market.analog import Baseline, Forecast

    수상한것 = Forecast(
        기준일=date(2020, 1, 1), 대상="시험", 구간수=20, 총일수=100, 지평=20,
        중앙값=5.0, 상위25=9.0, 하위25=1.0, 하위10=-2.0, 상승확률=85.0, 구간들=[],
        기준선=Baseline(표본수=1000, 중앙값=4.0, 하위10=-3.0, 상승확률=80.0),
    )
    assert "생존편향을 의심" in format_forecast(수상한것)


def test_the_baseline_only_uses_information_up_to_the_reference_day():
    """기준선이 미래를 보면 '전망이 더한 것'이 거짓이 된다."""
    from muwon.market.analog import baseline

    가격 = _가격(1000)
    끝 = 가격.index[500]
    앞부분만 = baseline(가격, horizon=20, until=끝)
    전체 = baseline(가격, horizon=20)
    assert 앞부분만.표본수 < 전체.표본수


def test_it_says_how_much_could_have_happened_by_chance():
    """구간 16개에서 상승확률이 +26%p 벌어져도 우연 폭이 ±24%p면 거의
    구분이 안 된다. 이 칸이 없으면 그 차이를 발견으로 읽게 된다."""
    from muwon.market.analog import Baseline, Forecast

    def _만들기(구간수, 상승확률, 기준상승확률):
        return Forecast(
            기준일=date(2020, 1, 1), 대상="시험", 구간수=구간수, 총일수=구간수 * 10,
            지평=20, 중앙값=2.0, 상위25=5.0, 하위25=-1.0, 하위10=-4.0,
            상승확률=상승확률, 구간들=[],
            기준선=Baseline(표본수=1000, 중앙값=1.0, 하위10=-4.0, 상승확률=기준상승확률),
        )

    적은표본 = _만들기(16, 75.0, 55.0)
    assert 적은표본.우연폭 == pytest.approx(24.5, abs=0.5)
    assert not 적은표본.우연을_넘었나, "16개 구간에서 +20%p는 우연과 구분이 안 된다"
    assert "우연과 구분이 안 된다" in format_forecast(적은표본)

    # 실제로 잰 2차전지가 +26%p였다. 우연 폭이 ±24.5%p라 **간신히 넘는다**.
    # 이런 것을 발견으로 읽으면 안 된다는 걸 숫자로 남겨 둔다.
    아슬아슬 = _만들기(16, 81.0, 55.0)
    assert 아슬아슬.우연을_넘었나
    assert 아슬아슬.더한것_상승확률 - 아슬아슬.우연폭 < 2.0, "간신히 넘는 정도여야 한다"

    # 표본이 늘면 같은 차이가 뜻을 갖는다.
    많은표본 = _만들기(200, 75.0, 55.0)
    assert 많은표본.우연폭 < 적은표본.우연폭
    assert 많은표본.우연을_넘었나
    assert "우연으로 보기 어렵다" in format_forecast(많은표본)
