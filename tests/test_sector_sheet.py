"""시트에서 읽은 섹터·종목·설정 검증.

**반쯤 잘못된 목록으로 실거래를 도는 것이 최악이다.** 시트는 아무나
아무 셀이나 고칠 수 있고, 종목코드 한 자리가 틀리면 엉뚱한 회사를 산다.
그 사실은 주문이 나간 뒤에야 드러난다.

그래서 읽을 때마다 검증하고, 걸리면 **조용히 넘어가지 않고 터뜨린다.**"""

import pytest

from muwon.cloud.sector_sheet import (
    MIN_LIVE_MEMBERS,
    SheetError,
    catalog_rows,
    default_settings_rows,
    parse,
    설정머리,
    섹터머리,
    종목머리,
)


def _섹터(*줄들):
    return [섹터머리, *줄들]


def _종목(*줄들):
    return [종목머리, *줄들]


def _설정(*줄들):
    return [설정머리, *줄들]


기본섹터 = _섹터(["SEMI", "반도체", "Y", "25", "섹터지수", "수출", ""])
기본종목 = _종목(
    ["005930", "삼성전자", "KOSPI", "SEMI", "Y", ""],
    ["000660", "SK하이닉스", "KOSPI", "SEMI", "Y", ""],
    ["042700", "한미반도체", "KOSPI", "SEMI", "Y", ""],
)


def test_the_shipped_catalog_survives_a_round_trip():
    """코드에 있는 초안을 시트에 넣었다가 다시 읽어도 같아야 한다.
    첫 채움이 곧 첫 검증이다."""
    섹터행, 종목행 = catalog_rows()
    내용 = parse(섹터행, 종목행, default_settings_rows())
    assert len(내용.섹터) == len(섹터행) - 1
    assert sum(len(s.종목) for s in 내용.섹터) == len(종목행) - 1


def test_a_six_digit_code_is_required():
    """다섯 자리를 적으면 조회부터 실패하고, 여섯 자리인데 한 자리가
    틀리면 **엉뚱한 회사를 산다.** 형식이라도 먼저 막는다."""
    나쁜것 = _종목(["5930", "삼성전자", "KOSPI", "SEMI", "Y", ""])
    with pytest.raises(SheetError, match="여섯 자리"):
        parse(기본섹터, 나쁜것, _설정())


def test_a_symbol_cannot_live_in_two_sectors():
    """한 종목이 두 섹터에 있으면 그 종목만 비중 상한을 두 배로 쓴다."""
    두섹터 = _섹터(
        ["SEMI", "반도체", "Y", "25", "섹터지수", "", ""],
        ["BATT", "2차전지", "Y", "25", "섹터지수", "", ""],
    )
    겹친것 = _종목(
        ["005930", "삼성전자", "KOSPI", "SEMI", "Y", ""],
        ["000660", "SK하이닉스", "KOSPI", "SEMI", "Y", ""],
        ["042700", "한미반도체", "KOSPI", "SEMI", "Y", ""],
        ["005930", "삼성전자", "KOSPI", "BATT", "Y", ""],
        ["373220", "LG엔솔", "KOSPI", "BATT", "Y", ""],
        ["006400", "삼성SDI", "KOSPI", "BATT", "Y", ""],
    )
    with pytest.raises(SheetError, match="양쪽에 있습니다"):
        parse(두섹터, 겹친것, _설정())


def test_an_unknown_sector_code_is_caught():
    """오타 하나로 종목이 조용히 사라지면, 왜 안 사는지 알 수 없다."""
    잘못가리킴 = _종목(["005930", "삼성전자", "KOSPI", "SEMII", "Y", ""])
    with pytest.raises(SheetError, match="없는 섹터코드"):
        parse(기본섹터, 잘못가리킴, _설정())


def test_a_sector_needs_enough_live_names():
    """활성 종목이 둘뿐이면 섹터 지수가 사실상 그 둘의 평균이다."""
    부족 = _종목(
        ["005930", "삼성전자", "KOSPI", "SEMI", "Y", ""],
        ["000660", "SK하이닉스", "KOSPI", "SEMI", "Y", ""],
    )
    with pytest.raises(SheetError, match=f"최소 {MIN_LIVE_MEMBERS}개"):
        parse(기본섹터, 부족, _설정())


def test_an_inactive_sector_is_not_held_to_that_rule():
    """끈 섹터까지 3종목을 요구하면, 정리하려고 껐는데 오히려 못 끄게 된다."""
    꺼진것 = _섹터(["SEMI", "반도체", "N", "25", "섹터지수", "", ""])
    부족 = _종목(["005930", "삼성전자", "KOSPI", "SEMI", "Y", ""])
    내용 = parse(꺼진것, 부족, _설정())
    assert not 내용.섹터찾기("SEMI").활성


def test_a_forecast_from_world_prices_does_not_need_three_names():
    """원자재는 국내 ETF가 둘뿐이라 국제 시세로 전망한다."""
    원자재 = _섹터(["COMM", "원자재", "Y", "15", "국제시세", "", ""])
    둘 = _종목(
        ["411060", "ACE KRX금현물", "KOSPI", "COMM", "Y", ""],
        ["144600", "KODEX 은선물", "KOSPI", "COMM", "Y", ""],
    )
    내용 = parse(원자재, 둘, _설정())
    assert 내용.섹터찾기("COMM").전망출처 == "국제시세"


def test_a_weight_cap_over_half_is_rejected():
    """상한 하나가 60%면 한 섹터에 절반 넘게 넣을 수 있다는 뜻이라
    섹터를 나눈 의미가 사라진다."""
    과한것 = _섹터(["SEMI", "반도체", "Y", "60", "섹터지수", "", ""])
    with pytest.raises(SheetError, match="분산이 아닙니다"):
        parse(과한것, 기본종목, _설정())


def test_a_non_numeric_weight_is_caught():
    나쁜것 = _섹터(["SEMI", "반도체", "Y", "스물다섯", "섹터지수", "", ""])
    with pytest.raises(SheetError, match="숫자가 아닙니다"):
        parse(나쁜것, 기본종목, _설정())


def test_an_unknown_market_is_caught():
    """야후 티커 접미사가 달라 조회가 실패한다. 실제로 엘앤에프가 걸렸다."""
    나쁜것 = _종목(["005930", "삼성전자", "코스피", "SEMI", "Y", ""])
    with pytest.raises(SheetError, match="모르는 시장"):
        parse(기본섹터, 나쁜것, _설정())


def test_an_unknown_forecast_source_is_caught():
    나쁜것 = _섹터(["SEMI", "반도체", "Y", "25", "점쟁이", "", ""])
    with pytest.raises(SheetError, match="모르는 전망출처"):
        parse(나쁜것, 기본종목, _설정())


def test_duplicate_sector_codes_are_caught():
    겹친것 = _섹터(
        ["SEMI", "반도체", "Y", "25", "섹터지수", "", ""],
        ["SEMI", "반도체2", "Y", "25", "섹터지수", "", ""],
    )
    with pytest.raises(SheetError, match="겹칩니다"):
        parse(겹친것, 기본종목, _설정())


def test_an_empty_sector_tab_stops_everything():
    """섹터가 없으면 매매 대상 자체가 없다. 빈 목록으로 조용히 도는 것보다
    터지는 게 낫다."""
    with pytest.raises(SheetError, match="비어 있습니다"):
        parse(_섹터(), 기본종목, _설정())


def test_blank_rows_are_skipped_not_treated_as_errors():
    """시트에는 사람이 지운 빈 줄이 흔하다. 그걸로 터지면 못 쓴다."""
    빈줄섞임 = _종목(
        ["005930", "삼성전자", "KOSPI", "SEMI", "Y", ""],
        [],
        ["", "", "", "", "", ""],
        ["000660", "SK하이닉스", "KOSPI", "SEMI", "Y", ""],
        ["042700", "한미반도체", "KOSPI", "SEMI", "Y", ""],
    )
    내용 = parse(기본섹터, 빈줄섞임, _설정())
    assert len(내용.섹터찾기("SEMI").종목) == 3


def test_short_rows_are_padded_instead_of_crashing():
    """시트는 오른쪽 빈 칸을 아예 안 보내 준다. 줄마다 길이가 다르다."""
    짧은줄 = _종목(
        ["005930", "삼성전자", "KOSPI", "SEMI"],
        ["000660", "SK하이닉스", "KOSPI", "SEMI"],
        ["042700", "한미반도체", "KOSPI", "SEMI"],
    )
    내용 = parse(기본섹터, 짧은줄, _설정())
    # 활성 칸이 비면 켜진 것으로 본다. 시트에서 새 줄을 추가할 때
    # 매번 Y를 적게 하면 실수로 안 적어 조용히 빠지는 일이 생긴다.
    assert all(m.활성 for m in 내용.섹터찾기("SEMI").종목)


def test_the_kill_switch_default_is_off_in_the_shipped_settings():
    """설정 초안이 켜진 채로 나가면, 시트를 처음 만든 순간 매매가 켜진다."""
    설정 = parse(기본섹터, 기본종목, default_settings_rows()).설정
    assert 설정["trading_enabled"] == "false"
    assert 설정["require_approval"] == "true"


def test_only_an_explicit_mark_turns_something_off():
    """빈 칸을 '꺼짐'으로 읽으면 시트에서 새 줄을 추가할 때마다 Y를 적어야
    하고, 안 적으면 조용히 빠진다. 끄는 것은 드무니 그때만 명시하게 한다."""
    섞인것 = _종목(
        ["005930", "삼성전자", "KOSPI", "SEMI", "", ""],
        ["000660", "SK하이닉스", "KOSPI", "SEMI", "Y", ""],
        ["042700", "한미반도체", "KOSPI", "SEMI", "y", ""],
        ["000990", "DB하이텍", "KOSPI", "SEMI", "N", "거래 부족"],
    )
    종목 = {m.symbol: m for m in parse(기본섹터, 섞인것, _설정()).섹터찾기("SEMI").종목}
    assert 종목["005930"].활성, "빈 칸은 켜진 것"
    assert 종목["000660"].활성
    assert 종목["042700"].활성, "소문자 y도 켜진 것"
    assert not 종목["000990"].활성, "N만 꺼진 것"
