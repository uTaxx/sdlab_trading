"""**낸 주문을 되돌리는 길**을 고정한다.

2026-08-25까지 이 저장소에는 그 길이 없었다. 시장가 주문이 일부만 체결되고
잔여가 장 마감까지 살아 있어도, 알아챈 사람이 할 수 있는 것은 한국투자증권에
직접 로그인하는 것뿐이었다.

여기서 지키는 것은 셋이다.

1. **미체결을 놓치지 않는다** — 조회가 실패했을 때 빈 목록을 주면
   "취소할 게 없습니다"로 읽힌다. 그건 거짓말이라 예외를 올린다.
2. **취소한 것을 또 취소하려 들지 않는다** — 이미 취소된 주문은 목록에서 뺀다.
3. **한 건이 거부돼도 나머지는 간다** — 급한 주문이 남의 실패에 묶이면 안 된다.
"""

from __future__ import annotations

import time

import pytest

from muwon.data.kis_client import KISClient, KISOrderRejected
from muwon.domain.types import OpenOrder, OrderSide
from scripts.cancel_open_orders import 고르기


def _client(rows: list[dict] | None) -> KISClient:
    c = KISClient(app_key="k", app_secret="s", account_no="12345678")
    # 네트워크 없이 인증 헤더를 만들려면 토큰이 이미 있는 척해야 한다.
    c._access_token = "t"
    c._token_expires_at = time.time() + 3600
    c._daily_ccld_rows = lambda order_date=None: rows  # type: ignore[method-assign]
    return c


def _행(**덮개) -> dict:
    바탕 = {
        "odno": "0000012345",
        "pdno": "066970",
        "prdt_name": "엘앤에프",
        "sll_buy_dvsn_cd": "02",  # 매수
        "ord_qty": "12",
        "tot_ccld_qty": "4",
        "rmn_qty": "8",
        "ord_unpr": "0",
        "ord_dvsn_cd": "01",
        "ord_gno_brno": "06010",
        "excg_id_dvsn_cd": "KRX",
        "ord_tmd": "091205",
        "cncl_yn": "N",
    }
    return {**바탕, **덮개}


# ── 미체결 찾기 ──────────────────────────────────────────────────────────


def test_잔여가_있으면_미체결로_잡는다():
    (주문,) = _client([_행()]).get_open_orders()

    assert 주문.order_id == "0000012345"
    assert 주문.symbol == "066970"
    assert 주문.side == OrderSide.BUY
    assert (주문.ordered_quantity, 주문.filled_quantity, 주문.remaining) == (12, 4, 8)
    assert 주문.branch_no == "06010", "취소 요청에 원주문 지점번호가 필요하다"
    assert 주문.ord_dvsn_cd == "01", "주문구분을 원주문과 똑같이 되돌려줘야 한다"


def test_다_체결된_주문은_미체결이_아니다():
    assert _client([_행(tot_ccld_qty="12", rmn_qty="0")]).get_open_orders() == []


def test_이미_취소된_주문은_다시_취소하지_않는다():
    """또 취소하려 들면 증권사가 거부하고, 그 거부가 진짜 문제를 가린다."""
    assert _client([_행(cncl_yn="Y")]).get_open_orders() == []


def test_잔여수량_칸이_비면_주문에서_체결을_뺀다():
    """KIS가 rmn_qty를 늘 채워 주지는 않는다. 빈 칸을 0으로 읽으면
    남아 있는 주문이 통째로 안 보인다."""
    (주문,) = _client([_행(rmn_qty="")]).get_open_orders()

    assert 주문.remaining == 8


def test_매도_주문도_잡는다():
    (주문,) = _client([_행(sll_buy_dvsn_cd="01")]).get_open_orders()

    assert 주문.side == OrderSide.SELL


def test_거래소칸_이름이_대문자_C로_와도_읽는다():
    """KIS 공식 예제의 필드명이 excg_id_dvsn_Cd로 적혀 있다."""
    행 = _행(excg_id_dvsn_cd=None, excg_id_dvsn_Cd="NXT")
    (주문,) = _client([행]).get_open_orders()

    assert 주문.exchange == "NXT"


def test_조회_실패는_빈_목록이_아니라_예외다():
    """이게 이 파일에서 제일 중요한 시험이다. 빈 목록을 주면 '취소할 게
    없습니다'로 읽혀서, 살아 있는 주문을 못 본 채로 지나간다."""
    with pytest.raises(RuntimeError, match="알 수 없습니다"):
        _client(None).get_open_orders()


def test_미체결이_정말_없으면_빈_목록이다():
    assert _client([]).get_open_orders() == []


# ── 취소 요청 ────────────────────────────────────────────────────────────


def _주문(**덮개) -> OpenOrder:
    바탕 = {
        "order_id": "0000012345", "symbol": "066970", "name": "엘앤에프",
        "side": OrderSide.BUY, "ordered_quantity": 12, "filled_quantity": 4,
        "remaining": 8, "price": 0.0, "ord_dvsn_cd": "01", "branch_no": "06010",
        "exchange": "KRX", "ordered_at": "091205",
    }
    return OpenOrder(**{**바탕, **덮개})


class _응답:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self) -> dict:
        return self._payload


def test_취소는_원주문_그대로_잔량_전부를_보낸다():
    c = _client([])
    보낸것: dict = {}

    def 가짜(url, **kwargs):
        보낸것["url"] = url
        보낸것["tr_id"] = kwargs["headers"]["tr_id"]
        보낸것["body"] = kwargs["json"]
        return _응답({"rt_cd": "0", "output": {"ODNO": "0000099999"}})

    c._post_with_rate_limit_retry = 가짜  # type: ignore[method-assign]

    assert c.cancel_order(_주문()) == "0000099999"
    assert 보낸것["url"].endswith("/trading/order-rvsecncl")
    assert 보낸것["tr_id"] == "VTTC0013U", "모의투자 계좌인데 실전 TR_ID로 갔다"
    body = 보낸것["body"]
    assert body["ORGN_ODNO"] == "0000012345"
    assert body["KRX_FWDG_ORD_ORGNO"] == "06010"
    assert body["ORD_DVSN"] == "01"
    assert body["RVSE_CNCL_DVSN_CD"] == "02", "02가 취소다 — 01은 정정이라 값이 바뀐다"
    assert body["QTY_ALL_ORD_YN"] == "Y"
    assert body["ORD_QTY"] == "8", "체결된 4주가 아니라 잔여 8주가 취소 대상이다"


def test_실거래_계좌면_실전_TR_ID로_간다():
    c = KISClient(app_key="k", app_secret="s", account_no="1", is_paper=False)
    c._access_token, c._token_expires_at = "t", time.time() + 3600
    본것: dict = {}
    c._post_with_rate_limit_retry = lambda url, **kw: (  # type: ignore[method-assign]
        본것.update(tr_id=kw["headers"]["tr_id"]),
        _응답({"rt_cd": "0", "output": {"ODNO": "1"}}),
    )[1]

    c.cancel_order(_주문())

    assert 본것["tr_id"] == "TTTC0013U"


def test_증권사가_거부하면_사유가_그대로_올라온다():
    """이미 체결된 주문은 취소할 수 없다. 그 거부를 삼키면 사람은 취소된 줄 안다."""
    c = _client([])
    c._post_with_rate_limit_retry = lambda url, **kw: _응답(  # type: ignore[method-assign]
        {"rt_cd": "1", "msg_cd": "40650000", "msg1": "주문가능수량을 초과하였습니다."}
    )

    with pytest.raises(KISOrderRejected) as e:
        c.cancel_order(_주문())
    assert "초과" in e.value.msg1


# ── 어느 것을 취소할지 고르기 ────────────────────────────────────────────


def test_아무것도_안_주면_전부가_대상이다():
    """취소는 끄는 쪽이라 길을 넓게 둔다."""
    주문들 = [_주문(), _주문(order_id="0000067890", symbol="411060")]

    대상, 못찾음 = 고르기(주문들, [], [])

    assert 대상 == 주문들
    assert 못찾음 == []


def test_종목코드로_고른다():
    주문들 = [_주문(), _주문(order_id="0000067890", symbol="411060")]

    대상, 못찾음 = 고르기(주문들, ["411060"], [])

    assert [o.symbol for o in 대상] == ["411060"]
    assert 못찾음 == []


def test_주문번호는_앞의_0을_무시하고_맞춘다():
    """사람은 앞의 0을 빼고 적는다. 그걸로 못 찾으면 취소를 못 한다."""
    대상, 못찾음 = 고르기([_주문()], [], ["12345"])

    assert len(대상) == 1
    assert 못찾음 == []


def test_없는_이름은_따로_알려준다():
    """조용히 넘어가면 취소했다고 믿은 채 자리를 뜬다."""
    대상, 못찾음 = 고르기([_주문()], ["000000"], [])

    assert 대상 == []
    assert 못찾음 == ["000000"]
