"""섹터 지수 검증.

가장 위험한 실패는 **그때 없던 회사를 그 시절 지수에 넣는 것**이다.
LG에너지솔루션은 2022년 상장인데, 2차전지 지수를 오늘 종목 그대로 2015년까지
늘리면 그 시절에 존재하지도 않던 회사가 들어간다. 그러면 과거가 실제보다
좋아 보이고, 그 위에서 낸 전망은 전부 거짓이 된다."""

from datetime import date, timedelta

import pandas as pd
import pytest

from muwon.market.sector_index import (
    MIN_MEMBERS,
    build_index,
    coverage_report,
    relative_strength,
)


def _봉(값들, 시작=date(2020, 1, 1)):
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


def test_an_equal_weight_index_ignores_price_levels():
    """비싼 종목이 지수를 지배하면 그건 '섹터 분위기'가 아니라 '대장주 주가'다.
    수익률을 먼저 평균 내는 이유가 이것이다.

    값이 1,000배 차이 나는 종목을 섞어도, 오른 종목이 하나뿐이면 지수는
    그 종목 상승률의 1/3만 움직여야 한다."""
    싼것 = _봉([100, 110, 121, 133.1])  # 매일 +10%
    비싼것 = _봉([100_000] * 4)  # 제자리
    개 = _봉([50] * 4)
    지수 = build_index({"a": 싼것, "b": 비싼것, "c": 개})
    # 첫 줄은 이미 하루치가 반영된 상태다(그날이 첫 수익률). 그래서 남은
    # 두 줄 사이 변화를 본다. 하루에 (10% + 0 + 0) / 3 = 3.33%.
    한주기 = 지수["close"].iloc[-1] / 지수["close"].iloc[-2] - 1
    assert 한주기 == pytest.approx(0.0333, abs=0.001)


def test_the_index_level_itself_is_meaningless_only_ratios_matter():
    """첫 줄을 억지로 100에 맞추려면 그날의 실제 수익률을 버려야 한다.
    그게 더 나쁘므로 수준은 임의로 둔다. 대신 그 사실을 시험으로 못 박는다."""
    지수 = build_index(
        {"a": _봉([100, 110]), "b": _봉([100, 110]), "c": _봉([100, 110])}
    )
    assert len(지수) == 1
    assert 지수["close"].iloc[0] != pytest.approx(100.0)


def test_a_name_that_did_not_exist_yet_is_not_counted():
    """이 시험이 이 파일의 이유다.

    LG에너지솔루션은 2022년 상장이다. 2차전지 지수를 오늘 종목 그대로
    2015년까지 늘리면, 그때 존재하지도 않던 회사가 그 시절 지수에 들어간다."""
    오래된것 = _봉([100 + i for i in range(10)])
    또다른오래된것 = _봉([100 + i for i in range(10)])
    늦게상장 = _봉([100, 200, 300], 시작=date(2020, 1, 8))  # 8일부터 있다

    표 = build_index({"old1": 오래된것, "old2": 또다른오래된것, "new": 늦게상장})

    # 늦게 상장한 종목이 없던 날에는 종목 수가 둘이어야 하고,
    # MIN_MEMBERS(3) 미만이므로 그 날들은 지수 자체가 없어야 한다.
    없던날 = [d for d in 표.index if d < date(2020, 1, 9)]
    assert not 없던날, f"상장 전 날짜에 지수가 생겼다: {없던날}"
    assert (표["members"] >= MIN_MEMBERS).all()

    # 그리고 상장 후 폭등(+100%)이 그 이전 구간에 스며들지 않아야 한다.
    assert 표.index[0] >= date(2020, 1, 9)


def test_a_day_with_too_few_names_gets_no_value():
    """두 종목의 평균은 '섹터'가 아니라 그냥 두 종목이다."""
    지수 = build_index({"a": _봉([100] * 10), "b": _봉([100] * 10)})
    assert len(지수) == 0, f"활성 {MIN_MEMBERS}종목 미만인데 지수가 나왔다"


def test_the_index_starts_when_enough_names_exist():
    앞선것 = _봉([100] * 20)
    또앞선것 = _봉([100] * 20)
    늦은것 = _봉([100] * 10, 시작=date(2020, 1, 11))
    지수 = build_index({"a": 앞선것, "b": 또앞선것, "c": 늦은것})
    assert len(지수) > 0
    assert 지수.index[0] >= date(2020, 1, 11)


def test_an_empty_input_returns_an_empty_frame_instead_of_crashing():
    assert len(build_index({})) == 0
    assert len(build_index({"a": None})) == 0


def test_zero_or_negative_prices_are_dropped():
    """0이 하나 섞이면 수익률이 무한대가 되어 지수가 통째로 망가진다."""
    지수 = build_index(
        {"a": _봉([100, 0, 100]), "b": _봉([100, 101, 102]), "c": _봉([100, 101, 102]),
         "d": _봉([100, 101, 102])}
    )
    assert len(지수) > 0
    assert 지수["close"].notna().all()
    assert (지수["close"] > 0).all()


def test_relative_strength_is_positive_when_the_sector_beats_the_market():
    """생존편향 때문에 절대 수익률은 못 믿는다. 그래서 시장 대비로 본다."""
    날짜 = [date(2020, 1, 1) + timedelta(days=i) for i in range(60)]
    섹터 = pd.Series([100 * (1.01**i) for i in range(60)], index=날짜)
    시장 = pd.Series([100 * (1.001**i) for i in range(60)], index=날짜)
    강도 = relative_strength(섹터, 시장, window=20)
    assert 강도.dropna().iloc[-1] > 0


def test_the_coverage_report_says_when_each_sector_starts():
    """이걸 안 보면 '2차전지 전망'이 사실 4년치라는 걸 모른다."""
    지수 = build_index({"a": _봉([100] * 10), "b": _봉([100] * 10), "c": _봉([100] * 10)})
    글 = coverage_report({"BATT": 지수, "EMPTY": pd.DataFrame(columns=["close", "members"])})
    assert "BATT" in 글
    assert "없음" in 글  # 지수를 못 만든 섹터도 반드시 보여야 한다
    assert "표본이 짧" in 글
