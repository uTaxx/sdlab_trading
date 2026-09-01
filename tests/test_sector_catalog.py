"""섹터 카탈로그 검증.

**종목코드가 틀리면 엉뚱한 회사를 산다.** 그리고 그건 주문이 나간 뒤에야
드러난다. 실제 시세 조회는 별도 스크립트가 하고(네트워크가 필요하므로),
여기서는 네트워크 없이 잡을 수 있는 것들을 잡는다."""

import re

import pytest

from muwon.sector.catalog import (
    CATALOG,
    SectorMember,
    all_members,
    sector_by_code,
    국제시세,
)


def test_every_symbol_is_six_digits():
    """국내 종목코드는 여섯 자리다. 다섯 자리를 적으면 조회부터 실패한다."""
    for 코드, m in all_members():
        assert re.fullmatch(r"\d{6}", m.symbol), f"{코드}/{m.name}: 종목코드 형식이 아니다 ({m.symbol})"


def test_no_symbol_appears_in_two_sectors():
    """한 종목이 두 섹터에 있으면 섹터별 비중 상한을 두는 의미가 없다.
    그 종목만 두 배로 살 수 있게 된다."""
    본것: dict[str, str] = {}
    for 코드, m in all_members():
        assert m.symbol not in 본것, f"{m.symbol} {m.name}이 {본것[m.symbol]}와 {코드} 양쪽에 있다"
        본것[m.symbol] = 코드


def test_sector_codes_are_unique():
    코드들 = [s.코드 for s in CATALOG]
    assert len(코드들) == len(set(코드들))


def test_every_market_is_one_we_know():
    for 코드, m in all_members():
        assert m.market in ("KOSPI", "KOSDAQ"), f"{코드}/{m.name}: 모르는 시장 {m.market}"


def test_yahoo_suffix_follows_the_market():
    """접미사가 틀리면 조회가 실패한다. 실제로 엘앤에프가 여기 걸렸다."""
    assert SectorMember("005930", "삼성전자", "KOSPI").yahoo_symbol == "005930.KS"
    assert SectorMember("247540", "에코프로비엠", "KOSDAQ").yahoo_symbol == "247540.KQ"


def test_a_sector_index_needs_at_least_three_live_names():
    """활성 종목이 둘뿐이면 섹터 지수가 사실상 그 둘의 평균이다. 그러면
    '섹터'라고 부를 이유가 없고, 섹터 전망도 그 종목 전망일 뿐이다.

    원자재가 실제로 여기 걸렸다(금·은 ETF 둘). 그래서 기준을 낮추는 대신
    **전망을 국제 시세로 내도록 바꿨다**. 국내 ETF 둘보다 훨씬 나은 표본이다."""
    for s in CATALOG:
        if not s.활성 or s.전망출처 != "섹터지수":
            continue
        assert len(s.활성종목) >= 3, f"{s.코드} {s.이름}: 활성 종목이 {len(s.활성종목)}개뿐"


def test_a_sector_that_does_not_use_its_own_index_says_what_it_watches():
    """'국제시세로 전망한다'고만 하고 무엇을 보는지 안 적으면 만들 수가 없다."""
    for s in CATALOG:
        if s.전망출처 == "섹터지수":
            continue
        보는것 = 국제시세.get(s.코드)
        assert 보는것, f"{s.코드}: 전망출처가 국제시세인데 무엇을 보는지가 없다"
        assert all(심볼 and 이름 for 심볼, 이름 in 보는것)


def test_the_forecast_source_is_one_we_know():
    for s in CATALOG:
        assert s.전망출처 in ("섹터지수", "국제시세"), f"{s.코드}: 모르는 전망출처"


def test_a_disabled_name_keeps_a_reason():
    """지우지 않고 끄는 이유는 '왜 뺐는지'를 남기기 위해서다.
    이유 없이 꺼져 있으면 나중에 누가 다시 켠다."""
    for 코드, m in all_members():
        if not m.활성:
            assert m.메모, f"{코드}/{m.name}: 껐으면 왜 껐는지를 적어야 한다"


def test_weight_caps_are_sane():
    for s in CATALOG:
        assert 0 < s.비중상한 <= 100, f"{s.코드}: 비중상한 {s.비중상한}"


def test_the_caps_together_leave_room_for_more_than_one_sector():
    """상한 하나가 100%면 한 섹터에 전부 넣을 수 있다는 뜻이라
    섹터를 나눈 의미가 사라진다."""
    for s in CATALOG:
        assert s.비중상한 <= 50, f"{s.코드}: 한 섹터에 절반 넘게 넣을 수 있으면 분산이 아니다"


def test_looking_up_an_unknown_sector_fails_loudly():
    with pytest.raises(KeyError, match="모르는 섹터 코드"):
        sector_by_code("없는코드")


def test_copper_is_not_tradable_and_the_catalog_says_so():
    """국내 구리 ETF는 거래가 없다(가장 큰 것이 11억). 그래서 껐는데,
    이유를 안 적어 두면 나중에 누가 '구리 왜 없지' 하고 다시 넣는다."""
    구리 = [m for _, m in all_members() if "구리" in m.name]
    assert 구리, "구리 ETF를 목록에서 지우면 왜 못 사는지가 사라진다"
    assert all(not m.활성 for m in 구리)
    assert all(m.메모 for m in 구리)
