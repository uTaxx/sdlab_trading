"""KIS 실시간 시세(웹소켓) 클라이언트: 장중 체결가를 스트리밍으로 받는다.

REST(KISClient)와는 별도 프로토콜이다. 접속키(approval_key)는 REST로
먼저 발급받고, 그 키로 웹소켓을 열어 종목을 구독한다. 실시간 체결가
자체는 모의투자/실전투자 구분 없이 동일한 시장 데이터이므로, TR_ID는
공통이고 접속키 발급 엔드포인트만 모의/실전이 다르다.

**이 환경(개발 샌드박스)은 KIS 비표준 포트(9443/29443 등)가 막혀 있어
이 클래스는 실제 접속으로는 검증 못 했다.** 대신 한국투자증권이 공식
운영하는 예제 저장소(github.com/koreainvestment/open-trading-api,
examples_user/kis_auth.py・domestic_stock_functions_ws.py)의 현재 코드와
대조해 다음을 확인했다: Approval 발급 바디의 파라미터명이 appsecret이
아니라 secretkey인 것, 구독 메시지의 header/body 구조(approval_key・
custtype・tr_type + body.input.tr_id), 그리고 _parse_price_message가
쓰는 필드 인덱스(0=MKSC_SHRN_ISCD 종목코드, 2=STCK_PRPR 현재가,
12=CNTG_VOL 체결거래량)까지 전부 일치한다. 그래도 실제 TCP 연결·인증·
장중 수신 자체는 못 해봤으니, 배포 후 첫 실행에서 실제 메시지로 한 번
더 확인은 필요하다."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import requests
import websockets

from muwon.data.tick_aggregator import Tick

APPROVAL_URL_REAL = "https://openapi.koreainvestment.com:9443/oauth2/Approval"
APPROVAL_URL_PAPER = "https://openapivts.koreainvestment.com:29443/oauth2/Approval"
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_PAPER = "ws://ops.koreainvestment.com:31000"
TR_ID_REALTIME_PRICE = "H0STCNT0"

# H0STCNT0(주식체결통보) 응답 필드 순서: KIS Developers 문서 기준.
# 인덱스 0: 유가증권 단축 종목코드, 2: 주식 현재가, 12: 체결 거래량.
_FIELD_SYMBOL = 0
_FIELD_PRICE = 2
_FIELD_VOLUME = 12


def get_approval_key(app_key: str, app_secret: str, is_paper: bool = True) -> str:
    """웹소켓 접속에 필요한 approval_key를 REST로 발급받는다."""
    url = APPROVAL_URL_PAPER if is_paper else APPROVAL_URL_REAL
    response = requests.post(
        url,
        json={"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["approval_key"]


class KISWebSocketClient:
    def __init__(self, approval_key: str, is_paper: bool = True):
        self._approval_key = approval_key
        self._url = WS_URL_PAPER if is_paper else WS_URL_REAL

    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        async with websockets.connect(self._url, ping_interval=None) as ws:
            for symbol in symbols:
                await ws.send(json.dumps(self._subscribe_message(symbol)))

            async for raw in ws:
                tick = self._parse_price_message(raw)
                if tick is not None:
                    yield tick

    def _subscribe_message(self, symbol: str) -> dict:
        return {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {"input": {"tr_id": TR_ID_REALTIME_PRICE, "tr_key": symbol}},
        }

    @staticmethod
    def _parse_price_message(raw: str) -> Tick | None:
        """실시간 체결가 메시지만 파싱한다. 형식: '0|TR_ID|건수|필드^필드^...'
        PINGPONG 등 제어 메시지(JSON, '{'로 시작)는 무시한다."""
        if not raw or raw[0] not in ("0", "1"):
            return None
        parts = raw.split("|")
        if len(parts) < 4 or parts[1] != TR_ID_REALTIME_PRICE:
            return None

        fields = parts[3].split("^")
        try:
            symbol = fields[_FIELD_SYMBOL]
            price = float(fields[_FIELD_PRICE])
            volume = int(fields[_FIELD_VOLUME])
        except (IndexError, ValueError):
            return None
        return Tick(symbol=symbol, price=price, volume=volume, timestamp=datetime.now(UTC))
