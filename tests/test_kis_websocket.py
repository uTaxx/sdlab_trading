"""웹소켓 접속 자체는 이 환경에서 검증 불가. 메시지 파싱과 접속키 발급
요청 구성만 실제 네트워크 없이 검증한다."""

from unittest.mock import MagicMock, patch

from muwon.data.kis_websocket import KISWebSocketClient, get_approval_key


def make_price_message(symbol="005930", price=71000, volume=15):
    fields = ["0"] * 13
    fields[0] = symbol
    fields[2] = str(price)
    fields[12] = str(volume)
    return f"0|H0STCNT0|001|{'^'.join(fields)}"


def test_parse_price_message_extracts_symbol_price_volume():
    client = KISWebSocketClient(approval_key="dummy")
    tick = client._parse_price_message(make_price_message(symbol="005930", price=71500, volume=10))

    assert tick is not None
    assert tick.symbol == "005930"
    assert tick.price == 71500.0
    assert tick.volume == 10


def test_parse_price_message_ignores_control_messages():
    client = KISWebSocketClient(approval_key="dummy")
    assert client._parse_price_message('{"header":{"tr_id":"PINGPONG"}}') is None


def test_parse_price_message_ignores_other_tr_ids():
    client = KISWebSocketClient(approval_key="dummy")
    assert client._parse_price_message("0|H0STASP0|001|005930^...") is None


def test_parse_price_message_handles_malformed_data_gracefully():
    client = KISWebSocketClient(approval_key="dummy")
    assert client._parse_price_message("0|H0STCNT0|001|005930") is None


@patch("muwon.data.kis_websocket.requests.post")
def test_get_approval_key_paper_uses_paper_url(mock_post):
    mock_post.return_value = MagicMock(json=lambda: {"approval_key": "abc123"})
    mock_post.return_value.raise_for_status = lambda: None

    key = get_approval_key("key", "secret", is_paper=True)

    assert key == "abc123"
    assert "openapivts" in mock_post.call_args.args[0]


@patch("muwon.data.kis_websocket.requests.post")
def test_get_approval_key_real_uses_real_url(mock_post):
    mock_post.return_value = MagicMock(json=lambda: {"approval_key": "xyz789"})
    mock_post.return_value.raise_for_status = lambda: None

    get_approval_key("key", "secret", is_paper=False)

    assert "openapivts" not in mock_post.call_args.args[0]
