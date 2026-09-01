"""KIS 서버에 실제로 붙지 못하는 개발 환경에서, 최소한 요청 구성(URL·헤더·
TR_ID·바디)과 응답 파싱이 KIS Developers 문서와 어긋나지 않는지를 requests를
모킹해서 검증한다. 실제 서버 동작 자체는 검증하지 못한다. 모의투자 계좌로
반드시 별도 확인이 필요하다."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from muwon.data.kis_client import (
    _MIN_REQUEST_INTERVAL_PAPER,
    KISClient,
    KISOrderRejected,
)
from muwon.domain.types import OrderSide


def make_client(is_paper: bool = True) -> KISClient:
    client = KISClient(
        app_key="key", app_secret="secret", account_no="12345678", account_product_cd="01", is_paper=is_paper
    )
    client._access_token = "cached-token"
    client._token_expires_at = 9_999_999_999.0
    return client


@patch("muwon.data.kis_client.requests.post")
def test_ensure_token_requests_once_and_caches(mock_post):
    mock_post.return_value = MagicMock(
        json=lambda: {"access_token": "tok-abc", "expires_in": "3600"}
    )
    mock_post.return_value.raise_for_status = lambda: None

    client = KISClient(app_key="key", app_secret="secret")
    token1 = client._ensure_token()
    token2 = client._ensure_token()

    assert token1 == "tok-abc"
    assert token2 == "tok-abc"
    assert mock_post.call_count == 1  # 캐시된 토큰이 만료 전이면 재요청 안 함


@patch("muwon.data.kis_client.requests.get")
def test_get_daily_ohlcv_parses_output2(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "output2": [
                {
                    "stck_bsop_date": "20240102",
                    "stck_oprc": "70000",
                    "stck_hgpr": "71000",
                    "stck_lwpr": "69500",
                    "stck_clpr": "70500",
                    "acml_vol": "1000000",
                },
                {
                    "stck_bsop_date": "20240103",
                    "stck_oprc": "70500",
                    "stck_hgpr": "72000",
                    "stck_lwpr": "70000",
                    "stck_clpr": "71800",
                    "acml_vol": "1200000",
                },
            ]
        }
    )
    mock_get.return_value.raise_for_status = lambda: None

    client = make_client()
    df = client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))

    assert len(df) == 2
    assert list(df["trade_date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert df["close"].iloc[0] == 70500.0
    assert df["volume"].iloc[1] == 1200000


@patch("muwon.data.kis_client.requests.post")
def test_place_cash_order_uses_paper_buy_tr_id(mock_post):
    mock_post.return_value = MagicMock(
        json=lambda: {"rt_cd": "0", "output": {"ODNO": "ORDER123"}}
    )
    mock_post.return_value.raise_for_status = lambda: None

    client = make_client(is_paper=True)
    result = client.place_cash_order("005930", OrderSide.BUY, 10, 71000.0)

    assert result.order_id == "ORDER123"
    assert result.is_paper is True
    assert mock_post.call_args.kwargs["headers"]["tr_id"] == "VTTC0802U"
    assert mock_post.call_args.kwargs["json"]["ORD_QTY"] == "10"


@patch("muwon.data.kis_client.requests.post")
def test_place_cash_order_uses_real_sell_tr_id(mock_post):
    mock_post.return_value = MagicMock(
        json=lambda: {"rt_cd": "0", "output": {"ODNO": "ORDER456"}}
    )
    mock_post.return_value.raise_for_status = lambda: None

    client = make_client(is_paper=False)
    client.place_cash_order("005930", OrderSide.SELL, 5, 71000.0)

    assert mock_post.call_args.kwargs["headers"]["tr_id"] == "TTTC0801U"


class FakeClock:
    """time.time()을 테스트가 제어하는 값으로 대체: 실제로 잠들지 않고도
    요청 간격 로직(_throttle)을 검증한다."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@patch("muwon.data.kis_client.requests.get")
@patch("muwon.data.kis_client.time.time")
def test_throttle_waits_between_consecutive_paper_requests(mock_time, mock_get):
    """모의투자 계좌로 유니버스 종목을 연달아 조회하다 9번째 요청부터
    500이 난 실제 사고를 재현한 회귀 테스트: 요청 간격이 제한보다 짧으면
    다음 요청 전에 부족한 만큼 대기해야 한다."""
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"output2": []})
    mock_get.return_value.raise_for_status = lambda: None

    clock = FakeClock()
    mock_time.side_effect = clock
    sleeps: list[float] = []

    client = make_client(is_paper=True)
    client._sleep = lambda seconds: (sleeps.append(seconds), clock.advance(seconds))[0]

    client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))
    elapsed = 0.1  # 다음 요청 전 아주 조금만 경과: 제한 간격에 못 미침
    clock.advance(elapsed)
    client.get_daily_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))

    assert len(sleeps) == 1
    # 간격 값 자체를 상수에서 읽는다. 제한을 조정할 때마다 테스트가 깨지면
    # 정작 검증하려는 "부족한 만큼 기다린다"는 성질이 가려진다.
    assert round(sleeps[0], 4) == round(_MIN_REQUEST_INTERVAL_PAPER - elapsed, 4)


@patch("muwon.data.kis_client.requests.get")
@patch("muwon.data.kis_client.time.time")
def test_throttle_skips_wait_once_interval_already_elapsed(mock_time, mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"output2": []})
    mock_get.return_value.raise_for_status = lambda: None

    clock = FakeClock()
    mock_time.side_effect = clock
    sleeps: list[float] = []

    client = make_client(is_paper=True)
    client._sleep = lambda seconds: (sleeps.append(seconds), clock.advance(seconds))[0]

    client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))
    clock.advance(_MIN_REQUEST_INTERVAL_PAPER * 2)  # 제한보다 충분히 지남
    client.get_daily_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))

    assert sleeps == []


@patch("muwon.data.kis_client.requests.get")
@patch("muwon.data.kis_client.time.time")
def test_real_trading_uses_shorter_throttle_interval_than_paper(mock_time, mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"output2": []})
    mock_get.return_value.raise_for_status = lambda: None

    clock = FakeClock()
    mock_time.side_effect = clock
    sleeps: list[float] = []

    client = make_client(is_paper=False)
    client._sleep = lambda seconds: (sleeps.append(seconds), clock.advance(seconds))[0]

    client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))
    clock.advance(0.1)  # 실전투자 제한(0.05초)보다 지남: 대기 불필요
    client.get_daily_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))

    assert sleeps == []


@patch("muwon.data.kis_client.requests.get")
def test_get_daily_ohlcv_retries_on_500_then_succeeds(mock_get):
    """throttle을 둬도 산발적으로 500이 나는 걸 실제로 관찰해서 추가한
    재시도 로직: 두 번째 시도에서 성공하면 그 결과를 그대로 써야 한다."""
    error_response = MagicMock(status_code=500)
    ok_response = MagicMock(status_code=200, json=lambda: {"output2": []})
    ok_response.raise_for_status = lambda: None
    mock_get.side_effect = [error_response, ok_response]

    client = make_client()
    client._sleep = lambda seconds: None  # 테스트에서 실제로 잠들지 않는다

    df = client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))

    assert len(df) == 0
    assert mock_get.call_count == 2


@patch("muwon.data.kis_client.requests.get")
def test_get_daily_ohlcv_gives_up_after_max_retries(mock_get):
    error_response = MagicMock(status_code=500)
    error_response.raise_for_status = MagicMock(side_effect=RuntimeError("500 Server Error"))
    mock_get.return_value = error_response

    client = make_client()
    client._sleep = lambda seconds: None

    try:
        client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))
        raise AssertionError("예외가 발생해야 한다")
    except RuntimeError:
        pass

    assert mock_get.call_count == 3  # _MAX_RETRIES


@patch("muwon.data.kis_client.requests.post")
def test_place_cash_order_raises_on_kis_error(mock_post):
    mock_post.return_value = MagicMock(
        json=lambda: {"rt_cd": "1", "msg1": "잔고 부족"}
    )
    mock_post.return_value.raise_for_status = lambda: None

    client = make_client()
    try:
        client.place_cash_order("005930", OrderSide.BUY, 10, 71000.0)
        raise AssertionError("RuntimeError가 발생해야 한다")
    except RuntimeError as e:
        assert "잔고 부족" in str(e)


@patch("muwon.data.kis_client.requests.post")
def test_order_rate_limit_arrives_as_http_500_and_is_retried(mock_post):
    """실제로 겪은 상황의 회귀 테스트: KIS는 초당 호출 제한을 HTTP 500에
    본문 EGW00201로 내려준다. 상태 코드만 보고 raise_for_status()로 먼저
    터뜨리면 사유를 못 읽고 '정체불명의 서버 오류'로 오판한다(그렇게 오판했다).
    이 거부는 주문이 접수되지 않았다는 뜻이라 재시도해도 안전하다."""
    rate_limited = MagicMock(
        status_code=500,
        json=lambda: {
            "rt_cd": "1",
            "msg_cd": "EGW00201",
            "msg1": "초당 거래건수를 초과하였습니다.",
        },
    )
    rate_limited.raise_for_status = MagicMock(
        side_effect=AssertionError("본문을 먼저 해석해야 하므로 raise_for_status를 부르면 안 된다")
    )
    accepted = MagicMock(
        status_code=200, json=lambda: {"rt_cd": "0", "output": {"ODNO": "ORDER789"}}
    )
    mock_post.side_effect = [rate_limited, accepted]

    client = make_client()
    client._sleep = lambda seconds: None

    result = client.place_cash_order("005930", OrderSide.BUY, 1, 274_500.0)

    assert result.order_id == "ORDER789"
    assert mock_post.call_count == 2


@patch("muwon.data.kis_client.requests.post")
def test_order_business_rejection_is_not_retried(mock_post):
    """잔고 부족처럼 재시도해도 결과가 같은 거부는 다시 보내지 않아야 한다.
    주문 POST를 불필요하게 반복하면 중복 체결 위험만 커진다."""
    rejected = MagicMock(
        status_code=200,
        json=lambda: {"rt_cd": "1", "msg_cd": "40240000", "msg1": "주문가능금액이 부족합니다"},
    )
    mock_post.return_value = rejected

    client = make_client()
    client._sleep = lambda seconds: None

    with pytest.raises(KISOrderRejected, match="주문가능금액"):
        client.place_cash_order("005930", OrderSide.BUY, 100, 274_500.0)
    assert mock_post.call_count == 1


@patch("muwon.data.kis_client.requests.post")
def test_order_raises_http_error_when_body_is_not_a_kis_response(mock_post):
    """KIS 업무 응답이 아닌 진짜 서버 오류(HTML 오류 페이지 등)는 그대로
    HTTP 오류로 올려야 한다. 업무 거부와 뭉뚱그리면 원인을 못 찾는다."""
    import requests as requests_module

    broken = MagicMock(status_code=502, text="<html>Bad Gateway</html>")
    broken.json = MagicMock(side_effect=ValueError("not json"))
    broken.raise_for_status = MagicMock(
        side_effect=requests_module.HTTPError("502 Server Error")
    )
    mock_post.return_value = broken

    client = make_client()
    client._sleep = lambda seconds: None

    with pytest.raises(requests_module.HTTPError):
        client.place_cash_order("005930", OrderSide.BUY, 1, 274_500.0)


@patch("muwon.data.kis_client.requests.post")
def test_order_rejection_exposes_kis_codes_separately_from_network_errors(mock_post):
    """KIS가 업무 규칙으로 거부한 것(요청 형식은 맞음)과 네트워크·인증
    실패(요청 자체가 틀림)를 호출부가 구분할 수 있어야 한다. 주문 경로
    검증 스크립트가 이 구분으로 성공/실패를 판정한다."""
    mock_post.return_value = MagicMock(
        json=lambda: {"rt_cd": "1", "msg_cd": "40570000", "msg1": "장시간이 아닙니다"}
    )
    mock_post.return_value.raise_for_status = lambda: None

    client = make_client()
    with pytest.raises(KISOrderRejected) as excinfo:
        client.place_cash_order("005930", OrderSide.BUY, 1, 71000.0)

    rejection = excinfo.value
    assert rejection.rt_cd == "1"
    assert rejection.msg_cd == "40570000"
    assert rejection.msg1 == "장시간이 아닙니다"
    assert isinstance(rejection, RuntimeError)  # 기존 호출부 호환


@patch("muwon.data.kis_client.requests.get")
def test_get_fill_parses_actual_fill_price_and_quantity(mock_get):
    """체결 조회 응답에서 실제 체결가·수량을 뽑아낸다. 필드명은 한국투자증권
    공식 예제 저장소의 COLUMN_MAPPING과 대조한 것이다."""
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "rt_cd": "0",
            "output1": [
                {"odno": "0000111111", "pdno": "000660", "ord_qty": "5", "tot_ccld_qty": "5", "avg_prvs": "180000"},
                {"odno": "0000123456", "pdno": "005930", "ord_qty": "10", "tot_ccld_qty": "10", "avg_prvs": "70450"},
            ],
        },
    )
    mock_get.return_value.raise_for_status = lambda: None

    client = make_client()
    fill = client.get_fill("0000123456", order_date=date(2026, 8, 18))

    assert fill is not None
    assert fill.symbol == "005930"
    assert fill.filled_quantity == 10
    assert fill.avg_fill_price == 70450.0
    assert fill.is_fully_filled is True


@patch("muwon.data.kis_client.requests.get")
def test_get_fill_matches_order_id_ignoring_leading_zeros(mock_get):
    """KIS가 돌려주는 주문번호는 앞자리가 0으로 채워져 있어, 주문 시 받은
    번호와 문자열이 정확히 일치하지 않을 수 있다."""
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "rt_cd": "0",
            "output1": [
                {"odno": "0000123456", "pdno": "005930", "ord_qty": "10", "tot_ccld_qty": "10", "avg_prvs": "70450"}
            ],
        },
    )
    mock_get.return_value.raise_for_status = lambda: None

    fill = make_client().get_fill("123456", order_date=date(2026, 8, 18))
    assert fill is not None and fill.filled_quantity == 10


@patch("muwon.data.kis_client.requests.get")
def test_get_fill_returns_none_when_order_not_found(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200, json=lambda: {"rt_cd": "0", "output1": []}
    )
    mock_get.return_value.raise_for_status = lambda: None

    assert make_client().get_fill("0000999999", order_date=date(2026, 8, 18)) is None


@patch("muwon.data.kis_client.requests.get")
def test_get_fill_reports_unfilled_order(mock_get):
    """접수는 됐지만 아직 체결 전이면 filled_quantity=0으로 알려줘야 한다.
    호출부가 이걸 보고 기준가를 유지할지 판단한다."""
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "rt_cd": "0",
            "output1": [
                {"odno": "0000123456", "pdno": "005930", "ord_qty": "10", "tot_ccld_qty": "0", "avg_prvs": ""}
            ],
        },
    )
    mock_get.return_value.raise_for_status = lambda: None

    fill = make_client().get_fill("0000123456", order_date=date(2026, 8, 18))
    assert fill is not None
    assert fill.is_unfilled is True
    assert fill.avg_fill_price == 0.0


@patch("muwon.data.kis_client.requests.get")
def test_get_fill_uses_paper_tr_id(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200, json=lambda: {"rt_cd": "0", "output1": []}
    )
    mock_get.return_value.raise_for_status = lambda: None

    make_client(is_paper=True).get_fill("0000123456", order_date=date(2026, 8, 18))
    assert mock_get.call_args.kwargs["headers"]["tr_id"] == "VTTC0081R"

    make_client(is_paper=False).get_fill("0000123456", order_date=date(2026, 8, 18))
    assert mock_get.call_args.kwargs["headers"]["tr_id"] == "TTTC0081R"


BALANCE_RESPONSE = {
    "rt_cd": "0",
    "output1": [
        {
            "pdno": "005930",
            "prdt_name": "삼성전자",
            "hldg_qty": "10",
            "pchs_avg_pric": "70000",
            "prpr": "71000",
            "evlu_amt": "710000",
            "evlu_pfls_amt": "10000",
        },
        {
            # 과거에 보유했다 청산한 종목이 수량 0으로 남아 오기도 한다
            "pdno": "000660",
            "prdt_name": "SK하이닉스",
            "hldg_qty": "0",
            "pchs_avg_pric": "0",
            "prpr": "180000",
            "evlu_amt": "0",
            "evlu_pfls_amt": "0",
        },
    ],
    "output2": [
        {"dnca_tot_amt": "9290000", "scts_evlu_amt": "710000", "nass_amt": "10000000"}
    ],
}


@patch("muwon.data.kis_client.requests.get")
def test_get_balance_parses_cash_and_holdings(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: BALANCE_RESPONSE)
    mock_get.return_value.raise_for_status = lambda: None

    balance = make_client().get_balance()

    assert balance.cash == 9_290_000.0
    assert balance.net_asset == 10_000_000.0
    assert len(balance.holdings) == 1  # 수량 0인 종목은 제외
    holding = balance.holdings[0]
    assert holding.symbol == "005930"
    assert holding.quantity == 10
    assert holding.avg_buy_price == 70_000.0
    assert balance.holding_for("005930") is holding
    assert balance.holding_for("035720") is None


@patch("muwon.data.kis_client.requests.get")
def test_get_balance_uses_correct_tr_id_per_environment(mock_get):
    mock_get.return_value = MagicMock(status_code=200, json=lambda: BALANCE_RESPONSE)
    mock_get.return_value.raise_for_status = lambda: None

    make_client(is_paper=True).get_balance()
    assert mock_get.call_args.kwargs["headers"]["tr_id"] == "VTTC8434R"

    make_client(is_paper=False).get_balance()
    assert mock_get.call_args.kwargs["headers"]["tr_id"] == "TTTC8434R"


@patch("muwon.data.kis_client.requests.get")
def test_get_balance_raises_with_kis_reason_on_rejection(mock_get):
    """잔고를 못 읽으면 그 뒤 대조가 의미 없으므로 조용히 넘어가지 않고
    사유와 함께 올린다."""
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"rt_cd": "1", "msg_cd": "40580000", "msg1": "계좌번호가 올바르지 않습니다"},
    )
    mock_get.return_value.raise_for_status = lambda: None

    client = make_client()
    client._sleep = lambda seconds: None
    with pytest.raises(RuntimeError, match="계좌번호가 올바르지 않습니다"):
        client.get_balance()


# 계좌 잔고 조회: 어떤 필드를 "현금"으로 삼느냐가 대조의 전부다.
#
# 아래 숫자는 2026-08-24에 HPSP 2주(90,100원)를 모의계좌에서 시험 매수한
# 직후 실제로 받은 응답이다. 예수금 총액은 매수를 아직 반영하지 않는다.
_잔고응답_매수직후 = {
    "rt_cd": "0",
    "output1": [
        {
            "pdno": "403870",
            "prdt_name": "HPSP",
            "hldg_qty": "2",
            "pchs_avg_pric": "45050",
            "prpr": "45250",
            "evlu_amt": "90500",
            "evlu_pfls_amt": "400",
        }
    ],
    "output2": [
        {
            "dnca_tot_amt": "10000145",  # 예수금 총액: 매수가 아직 안 빠졌다
            "prvs_rcdl_excc_amt": "9910035",  # 가수도정산금액: 이미 빠졌다
            "nxdy_excc_amt": "10000145",
            "thdt_buy_amt": "90100",
            "thdt_tlex_amt": "10",
            "scts_evlu_amt": "90500",
            "nass_amt": "10000535",
        }
    ],
}


@patch("muwon.data.kis_client.requests.get")
def test_balance_cash_reflects_todays_buy_not_unsettled_deposit(mock_get):
    """오늘 낸 주문이 즉시 반영되는 값을 현금으로 써야 한다.

    예수금 총액을 쓰면 매수 대금이 결제(T+2) 전까지 안 빠져서, 오늘 산 것을
    이틀 동안 못 본다. 그동안 대조는 "현금 일치"라고 답하는데 그건 거짓이다."""
    mock_get.return_value = MagicMock(status_code=200, json=lambda: _잔고응답_매수직후)

    balance = make_client().get_balance()

    assert balance.cash == 9_910_035
    assert balance.cash != 10_000_145  # 예수금 총액을 쓰면 안 된다


@patch("muwon.data.kis_client.requests.get")
def test_balance_keeps_raw_summary_for_eyeballing(mock_get):
    """어떤 필드가 무엇인지는 응답을 직접 봐야 안다. 원본을 버리지 않는다."""
    mock_get.return_value = MagicMock(status_code=200, json=lambda: _잔고응답_매수직후)

    balance = make_client().get_balance()

    assert balance.raw_summary["thdt_buy_amt"] == "90100"
    assert balance.holdings[0].symbol == "403870"


# ── 응답이 아예 오지 않을 때 ────────────────────────────────────────────────
#
# 2026-09-01 장중 손절 감시가 15:00과 15:15 두 차례 실패했다. 증권사가 10초
# 안에 답을 주지 않았고, requests가 올린 ReadTimeout이 그대로 밖으로 나가
# 실행 하나가 통째로 끝났다. 상태 코드를 보는 재시도는 응답이 있어야만
# 동작하므로 이 경우를 못 잡는다.


@patch("muwon.data.kis_client.requests.get")
def test_조회가_시간초과되면_다시_보낸다(mock_get):
    ok = MagicMock(status_code=200, json=lambda: {"output2": []})
    ok.raise_for_status = lambda: None
    mock_get.side_effect = [requests.exceptions.ReadTimeout("read timed out"), ok]

    client = make_client()
    client._sleep = lambda seconds: None

    df = client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))

    assert len(df) == 0
    assert mock_get.call_count == 2


@patch("muwon.data.kis_client.requests.get")
def test_조회가_연결끊김이어도_다시_보낸다(mock_get):
    ok = MagicMock(status_code=200, json=lambda: {"output2": []})
    ok.raise_for_status = lambda: None
    mock_get.side_effect = [requests.exceptions.ConnectionError("연결이 끊겼습니다"), ok]

    client = make_client()
    client._sleep = lambda seconds: None

    client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))

    assert mock_get.call_count == 2


@patch("muwon.data.kis_client.requests.get")
def test_세_번_다_응답이_없으면_그대로_실패시킨다(mock_get):
    """조용히 빈 값을 돌려주면 안 된다. 못 받은 것과 없는 것은 다르다."""
    mock_get.side_effect = requests.exceptions.ReadTimeout("read timed out")

    client = make_client()
    client._min_request_interval = 0.0  # 호출 간격 대기가 섞이면 값을 못 본다
    잔 = []
    client._sleep = 잔.append

    with pytest.raises(requests.exceptions.ReadTimeout):
        client.get_daily_ohlcv("005930", date(2024, 1, 2), date(2024, 1, 3))

    assert mock_get.call_count == 3  # _MAX_RETRIES
    assert 잔 == [1.0, 2.0]  # 기다리는 시간이 시도마다 늘어난다


@patch("muwon.data.kis_client.requests.get")
def test_잔고조회도_시간초과를_다시_보낸다(mock_get):
    """손절 감시가 실제로 부르는 것이 잔고조회다."""
    ok = MagicMock(status_code=200, json=lambda: _잔고응답_매수직후)
    mock_get.side_effect = [requests.exceptions.ReadTimeout("read timed out"), ok]

    client = make_client()
    client._sleep = lambda seconds: None

    balance = client.get_balance()

    assert balance.cash == 9_910_035
    assert mock_get.call_count == 2


@patch("muwon.data.kis_client.requests.post")
def test_주문은_시간초과를_다시_보내지_않는다(mock_post):
    """주문이 접수됐는지 알 수 없는 상태다. 다시 보내면 두 번 체결될 수 있다."""
    mock_post.side_effect = requests.exceptions.ReadTimeout("read timed out")

    client = make_client()
    client._sleep = lambda seconds: None

    with pytest.raises(requests.exceptions.ReadTimeout):
        client.place_cash_order("005930", OrderSide.BUY, 10, 71000.0)

    assert mock_post.call_count == 1
