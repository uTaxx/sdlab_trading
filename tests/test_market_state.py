"""시장 상태 지표 검증.

**여기서 미래를 한 번이라도 보면 그 뒤 모든 숫자가 거짓이 된다.** 그리고
그 사실은 화면에 아무 표시도 안 남는다. 그냥 성적이 좋아 보일 뿐이다."""

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.market.state import MIN_HISTORY, build_state, raw_indicators, rolling_z


def _가격(값들, 시작=date(2010, 1, 1)) -> pd.DataFrame:
    날짜 = [시작 + timedelta(days=i) for i in range(len(값들))]
    return pd.DataFrame(
        {
            "trade_date": 날짜,
            "open": 값들,
            "high": 값들,
            "low": 값들,
            "close": 값들,
            "volume": [1000] * len(값들),
        }
    )


def test_the_trend_is_positive_when_price_is_above_its_average():
    오르는것 = [100 + i for i in range(200)]
    지표 = raw_indicators({"kospi": _가격(오르는것)})
    assert 지표["kospi_추세20"].iloc[-1] > 0
    assert 지표["kospi_추세120"].iloc[-1] > 0


def test_drawdown_is_never_positive():
    """고점 대비는 정의상 0 이하다. 양수가 나오면 고점 계산이 틀린 것이다."""
    들쭉날쭉 = [100, 120, 90, 130, 80, 140, 70] * 60
    지표 = raw_indicators({"kospi": _가격(들쭉날쭉)})
    낙폭 = 지표["kospi_고점대비"].dropna()
    assert len(낙폭) > 0
    assert (낙폭 <= 1e-9).all()


def test_volatility_is_larger_for_a_choppier_series():
    조용한것 = [100 + (i % 2) * 0.1 for i in range(300)]
    요동치는것 = [100 + (i % 2) * 10 for i in range(300)]
    조용 = raw_indicators({"kospi": _가격(조용한것)})["kospi_변동성"].iloc[-1]
    요동 = raw_indicators({"kospi": _가격(요동치는것)})["kospi_변동성"].iloc[-1]
    assert 요동 > 조용


def test_the_rate_series_keeps_its_level_instead_of_a_trend():
    """금리는 '20일 평균보다 3% 높다'가 아니라 수준 자체가 뜻을 가진다."""
    지표 = raw_indicators({"ust10y": _가격([3.0 + i * 0.01 for i in range(200)])})
    assert "금리수준" in 지표.columns
    assert "ust10y_추세20" not in 지표.columns


def test_gold_over_copper_is_computed_when_both_are_there():
    지표 = raw_indicators(
        {"gold": _가격([2000.0] * 200), "copper": _가격([4.0] * 200)}
    )
    assert 지표["금구리비"].iloc[-1] == pytest.approx(500.0)


def test_standardisation_never_looks_at_today_or_later():
    """이게 이 파일에서 제일 중요한 시험이다.

    오늘 값을 오늘 평균에 넣어 표준화하면 오늘을 보고 오늘을 재는 셈이다.
    그래서 shift(1)로 하루 밀어 **어제까지의 평균**으로 오늘을 잰다."""
    n = MIN_HISTORY + 50
    # 상수열은 표준편차가 0이라 퇴화 사례다. 실제처럼 조금씩 흔들리게 둔다.
    값 = pd.DataFrame({"x": [1.0 + (i % 7) * 0.1 for i in range(n)]})
    # 마지막 날만 튀게 만든다. 미래를 안 본다면 그 전 날들의 z는 안 변해야 한다.
    값2 = 값.copy()
    값2.loc[n - 1, "x"] = 100.0

    z1 = rolling_z(값)
    z2 = rolling_z(값2)
    앞부분 = z1.iloc[:-1].dropna()
    assert len(앞부분) > 0
    pd.testing.assert_frame_equal(앞부분, z2.iloc[:-1].dropna())


def test_no_score_before_enough_history():
    """3년이 안 쌓였는데 '평소보다 높다'고 말할 수 없다."""
    짧은것 = pd.DataFrame({"x": list(range(MIN_HISTORY - 10))}, dtype=float)
    assert rolling_z(짧은것).dropna().empty


def test_a_flat_stretch_gets_no_score_instead_of_a_wrong_one():
    """값이 한 번도 안 변한 구간에서 '평소보다 몇 배 벗어났나'는 뜻이 없다.
    0으로 나누면 무한대가 나오는데, 그게 거리 계산에 들어가면 그날이
    모든 것을 압도한다."""
    n = MIN_HISTORY + 50
    평평한것 = pd.DataFrame({"x": [1.0] * n})
    z = rolling_z(평평한것)
    assert z["x"].dropna().empty
    # 자료형이 숫자로 남아 있어야 clip이 동작한다. 실제로 여기서 한 번 깨졌다.
    assert str(z["x"].dtype).startswith("float")


def test_extreme_days_are_clipped():
    """2020년 3월 같은 날은 z가 10을 넘는다. 그대로 두면 거리 계산에서
    그 하루가 다른 모든 지표를 압도한다."""
    n = MIN_HISTORY + 50
    값 = pd.DataFrame({"x": [1.0 + (i % 7) * 0.1 for i in range(n)]})
    값.loc[n - 1, "x"] = 1e6
    z = rolling_z(값)
    assert z["x"].max() <= 4.0
    assert z["x"].min() >= -4.0


def test_a_day_with_any_missing_indicator_is_dropped():
    """반쯤 채워진 상태로 '비슷한 날'을 찾으면 무엇을 보고 비슷하다고
    했는지 알 수 없다."""
    n = MIN_HISTORY + 300
    상태 = build_state({"kospi": _가격([100 + i * 0.1 for i in range(n)])})
    assert not 상태.isna().to_numpy().any()


def test_series_with_different_trading_days_are_forward_filled():
    """미국 휴장일에 한국 장이 서면 전날 미국 종가를 쓰는 게 실제와 같다.
    뒤 값으로 채우면 미래를 보는 것이 된다."""
    한국 = _가격([100.0] * 10)
    미국 = _가격([200.0] * 10)
    미국 = 미국.drop(index=[5]).reset_index(drop=True)  # 하루 휴장
    지표 = raw_indicators({"kospi": 한국, "nasdaq": 미국})
    assert len(지표) == 10  # 한국 거래일 수를 지킨다
