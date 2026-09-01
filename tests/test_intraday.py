"""30분봉 수집 검증.

**오늘 못 받은 칸은 내일 받을 수 없다.** 한국투자증권 API가 당일치만
주기 때문이다. 그래서 여기서 조용히 틀리면 몇 달 뒤에 구멍투성이 데이터를
발견하게 되고, 그때는 되돌릴 방법이 없다."""

from datetime import date

from muwon.data.intraday import SLOT_ENDS, MinuteBar, aggregate, slot_for
from muwon.data.intraday_store import coverage, format_coverage, save, stored_days
from muwon.data.kis_client import parse_minute_bars


def _분(hhmm, o, h, low, c, v=100):
    return MinuteBar(hhmm=hhmm, open=o, high=h, low=low, close=c, volume=v)


def test_the_day_is_cut_into_thirteen_slots():
    assert len(SLOT_ENDS) == 13
    assert SLOT_ENDS[0] == "0930" and SLOT_ENDS[-1] == "1530"


def test_the_opening_auction_print_lands_in_the_first_slot():
    """09:00 정각 체결을 빼면 그날 시가가 사라진다. 첫 30분 수익률을
    재려면 그게 있어야 한다. 이 수집의 목적이 바로 그것이다."""
    assert slot_for("0900") == "0930"


def test_times_outside_the_regular_session_are_dropped():
    assert slot_for("0859") is None
    assert slot_for("1531") is None
    assert slot_for("1800") is None  # 시간외 거래


def test_each_minute_lands_in_the_slot_that_ends_after_it():
    assert slot_for("0930") == "0930"
    assert slot_for("0931") == "1000"
    assert slot_for("1500") == "1500"
    assert slot_for("1501") == "1530"


def test_a_slot_takes_its_open_and_close_from_the_ends_in_time_order():
    """KIS는 최신 것부터 거꾸로 주고, 여러 번 나눠 받으면 더 섞인다.
    시가·종가는 순서가 틀리면 조용히 바뀌는 값이다. 고가·저가와 달리
    티가 안 나서 더 위험하다."""
    섞인것 = [_분("0910", 5, 6, 4, 5), _분("0901", 1, 2, 1, 2), _분("0930", 9, 10, 8, 9)]
    (칸,) = aggregate("005930", date(2026, 8, 19), 섞인것)
    assert 칸.open == 1, "가장 이른 봉의 시가여야 한다"
    assert 칸.close == 9, "가장 늦은 봉의 종가여야 한다"
    assert 칸.high == 10
    assert 칸.low == 1
    assert 칸.volume == 300
    assert 칸.bars == 3


def test_slots_come_back_in_time_order():
    bars = [_분("1400", 1, 1, 1, 1), _분("0901", 2, 2, 2, 2), _분("1100", 3, 3, 3, 3)]
    칸들 = aggregate("005930", date(2026, 8, 19), bars)
    assert [c.slot for c in 칸들] == ["0930", "1100", "1400"]


def test_an_empty_slot_is_not_invented():
    """없는 칸을 0으로 채우면 없던 거래가 생긴다. 나중에 '그날 09시에
    거래가 0이었다'와 '그 칸을 못 받았다'를 구분할 수 없게 된다."""
    칸들 = aggregate("005930", date(2026, 8, 19), [_분("0901", 1, 1, 1, 1)])
    assert len(칸들) == 1
    assert 칸들[0].slot == "0930"


def test_bars_outside_the_session_do_not_create_slots():
    assert aggregate("005930", date(2026, 8, 19), [_분("1800", 1, 1, 1, 1)]) == []


def test_the_parser_drops_bars_with_zero_prices():
    """체결이 없던 시간대에 0이 담겨 오는 경우가 있다. 그대로 쓰면
    시가·저가가 0으로 잡혀 30분 칸 전체가 망가진다."""
    payload = {
        "output2": [
            {"stck_cntg_hour": "093000", "stck_oprc": "100", "stck_hgpr": "110",
             "stck_lwpr": "90", "stck_prpr": "105", "cntg_vol": "50"},
            {"stck_cntg_hour": "092900", "stck_oprc": "0", "stck_hgpr": "0",
             "stck_lwpr": "0", "stck_prpr": "0", "cntg_vol": "0"},
        ]
    }
    (봉,) = parse_minute_bars(payload)
    assert 봉.hhmm == "0930"
    assert 봉.close == 105


def test_the_parser_survives_a_row_missing_fields():
    payload = {"output2": [{"stck_cntg_hour": "093000"}, {"stck_cntg_hour": "bad"}]}
    assert parse_minute_bars(payload) == []


def test_the_parser_handles_an_empty_response():
    """그 시간대에 체결이 없을 수도 있고, 15:20~15:30처럼 단일가 구간이라
    분봉 자체가 없을 수도 있다. 오류가 아니다."""
    assert parse_minute_bars({}) == []
    assert parse_minute_bars({"output2": []}) == []


def test_saving_the_same_day_twice_does_not_double_the_rows(tmp_path):
    """수집이 도중에 끊겨 다시 실행하는 일이 잦을 텐데, 그때마다 줄이 두 배로
    늘면 나중에 쓸 수가 없다."""
    db = tmp_path / "intraday.db"
    칸들 = aggregate("005930", date(2026, 8, 19), [_분("0901", 1, 2, 1, 2)])
    save(칸들, db)
    save(칸들, db)
    cov = coverage(date(2026, 8, 19), ["005930"], db)
    assert cov.칸수 == 1


def test_coverage_names_the_slots_that_are_missing_forever(tmp_path):
    db = tmp_path / "intraday.db"
    save(aggregate("005930", date(2026, 8, 19), [_분("0901", 1, 2, 1, 2)]), db)
    cov = coverage(date(2026, 8, 19), ["005930", "000660"], db)

    assert cov.칸수 == 1
    assert cov.기대칸수 == 26
    assert cov.빈칸["000660"] == list(SLOT_ENDS), "한 칸도 못 받은 종목"
    assert "1000" in cov.빈칸["005930"]

    글 = format_coverage(cov)
    assert "내일 받을 수 없습니다" in 글, "되돌릴 수 없다는 말이 빠지면 그냥 넘어가게 된다"
    assert "000660" in 글


def test_coverage_says_nothing_is_missing_when_the_day_is_full(tmp_path):
    db = tmp_path / "intraday.db"
    전부 = [_분(f"{끝}", 1, 2, 1, 2) for 끝 in SLOT_ENDS]
    save(aggregate("005930", date(2026, 8, 19), 전부), db)
    cov = coverage(date(2026, 8, 19), ["005930"], db)
    assert cov.빈칸 == {}
    assert cov.채움률 == 100.0
    assert "빠진 칸 없음" in format_coverage(cov)


def test_it_remembers_how_many_days_have_piled_up(tmp_path):
    """장중 모멘텀을 재려면 최소 6개월은 있어야 한다. 며칠 쌓였는지가
    '아직 이르다'와 '이제 재도 된다'를 가른다."""
    db = tmp_path / "intraday.db"
    for 일 in (17, 18, 19):
        save(aggregate("005930", date(2026, 8, 일), [_분("0901", 1, 2, 1, 2)]), db)
    assert stored_days(db) == [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
