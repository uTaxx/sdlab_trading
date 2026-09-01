"""**"살 수 없다"와 "못 물어봤다"를 가른다.**

2026-08-26 아침, 승인한 두 종목이 이 한 줄로 막혔다.

    풍산(103140): 증권사가 매수가능수량 0으로 답했습니다. 현금이 모자랍니다
    두산에너빌리티(034020): 같음

증권사가 그렇게 답한 것이 아니었다. **우리가 못 알아들은 것을 0으로
적은 것**이다. 처음 쓴 코드는 예외만 -1로 실행하고, 거부 응답과 빈 칸은
그대로 0이 됐다.

    output = response.json().get("output") or {}
    return int(float(output.get("nrcvb_buy_qty") or 0))

-1을 둔 이유가 정확히 이것인데 구멍을 남겨 뒀다. 조회 한 번 실패한 것이
그날 매수를 통째로 막는다.
"""

from __future__ import annotations

import time

from muwon.data.kis_client import KISClient


class _응답:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}
        self.text = "{}"

    def json(self):
        return self._payload


def _client(payload=None, 터뜨릴것=None) -> KISClient:
    c = KISClient(app_key="k", app_secret="s", account_no="1")
    c._access_token, c._token_expires_at = "t", time.time() + 3600

    def 가짜(url, **kwargs):
        if 터뜨릴것:
            raise 터뜨릴것
        return _응답(payload)

    c._get_with_retry = 가짜  # type: ignore[method-assign]
    return c


def _정상(수량="42", 현금="6998455"):
    return {
        "rt_cd": "0",
        "msg_cd": "MCA00000",
        "msg1": "정상처리",
        "output": {"nrcvb_buy_qty": 수량, "ord_psbl_cash": 현금, "max_buy_qty": "50"},
    }


# ── 못 물어본 것은 전부 -1 ───────────────────────────────────────────


def test_증권사가_조회를_거부하면_모른다고_한다():
    """이게 2026-08-26에 매수를 막은 자리다. rt_cd를 안 보고 있었다."""
    c = _client({"rt_cd": "1", "msg_cd": "40580000", "msg1": "조회할 수 없습니다"})

    assert c.get_orderable("103140", 150000) == -1


def test_답에_수량_칸이_아예_없으면_모른다고_한다():
    """빈 칸을 0으로 누르면 '살 수 없다'가 된다. 모르는 것은 모른다고 한다."""
    c = _client({"rt_cd": "0", "output": {"ord_psbl_cash": "6998455"}})

    assert c.get_orderable("103140", 150000) == -1


def test_수량_칸이_빈_문자열이어도_모른다고_한다():
    c = _client({"rt_cd": "0", "output": {"nrcvb_buy_qty": "  "}})

    assert c.get_orderable("103140", 150000) == -1


def test_output이_통째로_없어도_모른다고_한다():
    c = _client({"rt_cd": "0"})

    assert c.get_orderable("103140", 150000) == -1


def test_숫자가_아닌_값이_와도_모른다고_한다():
    c = _client({"rt_cd": "0", "output": {"nrcvb_buy_qty": "없음"}})

    assert c.get_orderable("103140", 150000) == -1


def test_통신이_실패해도_모른다고_한다():
    c = _client(터뜨릴것=RuntimeError("연결 끊김"))

    assert c.get_orderable("103140", 150000) == -1


# ── 진짜 답은 그대로 쓴다 ────────────────────────────────────────────


def test_정상_응답은_그_수량을_쓴다():
    assert _client(_정상("42")).get_orderable("103140", 150000) == 42


def test_진짜_0은_0이다():
    """증권사가 **숫자 0**으로 답했으면 그건 정말 못 사는 것이다.
    모르는 것과 이것을 가르는 게 이 파일의 전부다."""
    assert _client(_정상("0")).get_orderable("103140", 150000) == 0


def test_소수점으로_와도_읽는다():
    assert _client(_정상("42.0")).get_orderable("103140", 150000) == 42


# ── 물어보는 방식 ────────────────────────────────────────────────────


def test_반드시_시장가로_물어본다():
    """지정가(00)로 물으면 종목증거금율이 반영되지 않은 수량이 온다.
    한국투자증권 문서가 '반드시'라고 적어 둔 자리다."""
    c = _client(_정상())
    본것: dict = {}
    원래 = c._get_with_retry

    def 엿보기(url, **kwargs):
        본것.update(kwargs["params"])
        본것["tr_id"] = kwargs["headers"]["tr_id"]
        return 원래(url, **kwargs)

    c._get_with_retry = 엿보기  # type: ignore[method-assign]
    c.get_orderable("103140", 150000)

    assert 본것["ORD_DVSN"] == "01"
    assert 본것["PDNO"] == "103140"
    assert 본것["ORD_UNPR"] == "150000"
    assert 본것["tr_id"] == "VTTC8908R", "모의투자 계좌인데 실전 TR_ID로 갔다"
