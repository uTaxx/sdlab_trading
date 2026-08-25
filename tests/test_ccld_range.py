"""**기간으로 주문체결을 부를 때 끝까지 따라가는지** 고정한다.

주문체결조회는 한 번에 50건까지 온다. 나머지는 연속조회(tr_cont)로 이어받아야
하는데, 안 따라가면 오래된 것부터 조용히 빠진다.

그 빠짐이 조용하다는 게 문제다. 대조 도구(`reconcile_orders`)에서 빠진 주문은
**"증권사에 없는 주문 = 유령"**으로 둔갑한다. 멀쩡한 기록을 지우게 만드는
길이라, 여기서 막는다.
"""

from __future__ import annotations

import time
from datetime import date

from muwon.data.kis_client import KISClient


class _응답:
    def __init__(self, payload: dict, tr_cont: str = ""):
        self._payload = payload
        self.headers = {"tr_cont": tr_cont} if tr_cont else {}
        self.status_code = 200
        self.text = "{}"

    def json(self) -> dict:
        return self._payload


def _쪽(주문번호들: list[str], nk: str = "", tr_cont: str = "") -> _응답:
    return _응답(
        {
            "rt_cd": "0",
            "output1": [{"odno": n, "pdno": "066970", "tot_ccld_qty": "1"} for n in 주문번호들],
            "ctx_area_fk100": "FK",
            "ctx_area_nk100": nk,
        },
        tr_cont=tr_cont,
    )


def _client(쪽들: list[_응답]) -> tuple[KISClient, list[dict]]:
    c = KISClient(app_key="k", app_secret="s", account_no="1")
    c._access_token, c._token_expires_at = "t", time.time() + 3600
    보낸것: list[dict] = []
    남은쪽 = list(쪽들)

    def 가짜(url, **kwargs):
        보낸것.append({"params": kwargs["params"], "headers": kwargs["headers"]})
        return 남은쪽.pop(0)

    c._get_with_retry = 가짜  # type: ignore[method-assign]
    return c, 보낸것


def test_한_쪽이면_한_번만_부른다():
    c, 보낸것 = _client([_쪽(["1", "2"])])

    rows = c.get_orders_between(date(2026, 8, 1), date(2026, 8, 25))

    assert [r["odno"] for r in rows] == ["1", "2"]
    assert len(보낸것) == 1
    assert 보낸것[0]["params"]["INQR_STRT_DT"] == "20260801"
    assert 보낸것[0]["params"]["INQR_END_DT"] == "20260825"
    assert "tr_cont" not in 보낸것[0]["headers"], "첫 쪽에는 연속조회 표시를 붙이면 안 된다"


def test_다음_쪽이_있으면_이어받는다():
    """이 시험이 이 파일의 핵심이다 — 안 이어받으면 옛 주문이 유령이 된다."""
    c, 보낸것 = _client([_쪽(["1", "2"], nk="NK1", tr_cont="M"), _쪽(["3"])])

    rows = c.get_orders_between(date(2026, 8, 1), date(2026, 8, 25))

    assert [r["odno"] for r in rows] == ["1", "2", "3"]
    assert len(보낸것) == 2
    assert 보낸것[1]["headers"]["tr_cont"] == "N"
    assert 보낸것[1]["params"]["CTX_AREA_NK100"] == "NK1"
    assert 보낸것[1]["params"]["CTX_AREA_FK100"] == "FK"


def test_F도_다음_쪽_신호다():
    c, _ = _client([_쪽(["1"], nk="NK1", tr_cont="F"), _쪽(["2"])])

    assert len(c.get_orders_between(date(2026, 8, 1), date(2026, 8, 2))) == 2


def test_쪽수_상한에서_멈춘다():
    """잘못된 응답이 계속 M을 주면 영원히 돈다. 상한이 그걸 끊는다."""
    c, 보낸것 = _client([_쪽([str(i)], nk="NK", tr_cont="M") for i in range(30)])

    rows = c.get_orders_between(date(2026, 8, 1), date(2026, 8, 2))

    assert len(보낸것) == 20, "상한(_CCLD_MAX_PAGES)에서 멈춰야 한다"
    assert len(rows) == 20


def test_첫_쪽부터_거부당하면_None이다():
    c, _ = _client([_응답({"rt_cd": "1", "msg1": "조회권한이 없습니다", "msg_cd": "40910000"})])

    assert c.get_orders_between(date(2026, 8, 1), date(2026, 8, 2)) is None


def test_이어받다_거부당하면_거기까지라도_돌려준다():
    """전부 버리면 이미 받은 것까지 잃는다. 부분이라도 있는 편이 낫다."""
    c, _ = _client([
        _쪽(["1", "2"], nk="NK1", tr_cont="M"),
        _응답({"rt_cd": "1", "msg1": "일시적 오류", "msg_cd": "99999999"}),
    ])

    rows = c.get_orders_between(date(2026, 8, 1), date(2026, 8, 2))

    assert [r["odno"] for r in rows] == ["1", "2"]


def test_하루만_부르면_시작과_끝이_같다():
    """get_fill·get_open_orders가 쓰는 길 — 기간 지원을 붙이며 안 깨졌는지 본다."""
    c, 보낸것 = _client([_쪽(["1"])])

    c.get_fill("1", date(2026, 8, 25))

    assert 보낸것[0]["params"]["INQR_STRT_DT"] == "20260825"
    assert 보낸것[0]["params"]["INQR_END_DT"] == "20260825"
