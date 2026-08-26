"""한국투자증권(KIS) Developers API 클라이언트.

REAL_BASE_URL / PAPER_BASE_URL은 KIS Developers 공식 문서에 명시된
실전투자/모의투자 도메인이다. 두 도메인 모두 비표준 포트(9443/29443)를 쓰는데,
egress 정책에 따라 이 포트들이 막혀 있으면 이 클래스는 애초에 서버에 닿지
못한다 — 개발 중엔 백테스트/드라이런 전용인 YahooFinanceDataSource +
SimulatedOrderExecutor로 파이프라인을 검증하고, 이 클래스는 실제 KIS
네트워크 접근이 되는 환경(운영 서버 등)에서 실거래/모의투자로 전환할 때
쓴다.

엔드포인트/TR_ID는 KIS Developers 포털(https://apiportal.koreainvestment.com)
문서 기준으로 작성했지만, 이 개발 환경에서는 KIS 서버에 접근할 수 없어
실제 호출로 검증하지 못했다 — 실거래 전환 전 반드시 최신 문서와 대조하고
모의투자 계좌로 먼저 검증할 것.

실제 GitHub Actions에서 모의투자 계좌로 처음 인증에 성공한 실행에서,
유니버스 종목을 아무 간격 없이 연달아 조회하다 9번째 요청부터 500이 나는
걸 확인했다 — 초당 호출 제한으로 보고 _throttle()로 요청 간 최소 간격을
뒀는데(아래 _MIN_REQUEST_INTERVAL_*), 그 다음 실행에서도 이번엔 2번째
요청부터 500이 나서 단순 횟수 제한만은 아닌 것으로 보인다. get_daily_ohlcv
(GET, 멱등)에는 재시도(_get_with_retry)까지 추가해 둘 다 대응한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from datetime import date
from typing import ClassVar

import pandas as pd
import requests
from loguru import logger

from muwon.data.intraday import MinuteBar
from muwon.domain.interfaces import MarketDataSource
from muwon.domain.types import (
    AccountBalance,
    FillInfo,
    Holding,
    OpenOrder,
    OrderResult,
    OrderSide,
)
from muwon.settings.service import SettingsService

REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"

# 국내주식 현금주문 TR_ID — 모의투자(V)와 실전투자(T)가 서로 다르다.
_BUY_TR_ID = {"paper": "VTTC0802U", "real": "TTTC0802U"}
_SELL_TR_ID = {"paper": "VTTC0801U", "real": "TTTC0801U"}
_MARKET_ORDER_DVSN = "01"  # 시장가

# 주문체결조회(최근 3개월 이내) TR_ID. 필드명은 한국투자증권 공식 예제
# 저장소(koreainvestment/open-trading-api)의 COLUMN_MAPPING과 대조해 확인했다.
_DAILY_CCLD_TR_ID = {"paper": "VTTC0081R", "real": "TTTC0081R"}

# 주문체결조회는 한 번에 50건까지 온다. 기간으로 부르면 연속조회로 이어받는데,
# 잘못된 응답으로 무한히 도는 것을 막으려 쪽수 상한을 둔다. 20쪽 = 1000건이면
# 이 저장소의 몇 달치보다 넉넉하다.
_CCLD_MAX_PAGES = 20

# 주식잔고조회 TR_ID (같은 저장소에서 확인).
_BALANCE_TR_ID = {"paper": "VTTC8434R", "real": "TTTC8434R"}

#: 매수가능조회. 우리가 스스로 세던 현금 대신 **증권사가 계산한** 살 수
#: 있는 수량을 받는다. 2026-08-25에 우리 현금이 계좌와 294만원 어긋난
#: 채로 그 위에서 비중 상한이 돌았다.
_PSBL_ORDER_TR_ID = {"paper": "VTTC8908R", "real": "TTTC8908R"}

# 주식주문(정정취소) TR_ID. 취소는 모의투자에서도 된다.
#
# 짝이 되는 "주식정정취소가능주문조회"(TTTC0084R)는 **실전에만 있다** —
# 모의투자 계좌로는 호출할 수 없다. 그래서 취소할 주문 목록은 이미 쓰고 있는
# 주문체결조회(inquire-daily-ccld)에서 잔여수량으로 뽑는다. 두 환경에서 같은
# 길을 쓰게 되니 오히려 시험하기 쉽다.
_RVSECNCL_TR_ID = {"paper": "VTTC0013U", "real": "TTTC0013U"}

# 정정취소구분코드: 01=정정(값을 바꾼다), 02=취소(없던 일로 한다).
# 우리는 취소만 쓴다 — 정정은 "얼마에 다시 낼 것인가"라는 판단이 필요한데,
# 그 판단은 다음 실행에서 전략이 처음부터 다시 하는 편이 낫다.
_취소 = "02"

# KIS는 초당 호출 횟수를 제한한다 — 모의투자가 실전투자보다 훨씬 빡빡하다
# (문서상 모의투자 초당 2건, 실전투자 초당 20건). 요청 간 최소 간격을 둬서
# 이를 피한다.
#
# 실제 검증에서 시세조회 0.5초 간격은 통과했지만, 그 직후의 주문 요청이
# 곧바로 EGW00201(초당 거래건수 초과)로 거부됐다 — 주문 엔드포인트가 더
# 빡빡하거나 별도 버킷을 쓰는 것으로 보인다. 그래서 모의투자 간격을 1초로
# 올렸다(18종목 기준 실행이 9초 늘어나는 대신 제한에 걸릴 확률을 낮춘다).
_MIN_REQUEST_INTERVAL_PAPER = 1.0
_MIN_REQUEST_INTERVAL_REAL = 0.05

# KIS가 초당 호출 제한을 알릴 때 쓰는 코드. 이 거부는 "요청이 접수되지
# 않았다"는 뜻이므로 주문이라도 재시도해도 중복 체결 위험이 없다.
_RATE_LIMIT_MSG_CD = "EGW00201"

# 토큰이 죽었을 때 KIS가 알리는 코드. **시간이 남았어도 죽을 수 있다** —
# 같은 앱키로 토큰을 새로 발급하면 앞의 토큰이 무효가 된다. 우리는 파이썬과
# n8n 둘이 같은 앱키를 쓰므로, n8n이 새로 받으면 우리가 캐시해 둔 것이
# 만료 시각과 무관하게 죽는다.
#
# 2026-08-26 아침에 그 일이 실제로 났다. 저장해 둔 토큰이 죽은 채로 계좌를
# 부르니 KIS가 거부했고, 그 거부가 매수가능조회에서 "0주"로 둔갑해
# 승인한 두 종목이 통째로 안 팔렸다(사고 기록 참고).
_TOKEN_EXPIRED_MSG_CD = "EGW00123"


class KISOrderRejected(RuntimeError):
    """KIS가 요청 자체는 정상적으로 받아들였지만 업무 규칙상 거부한 경우
    (장 시간 아님, 주문가능금액 부족, 수량 초과 등).

    네트워크·인증 오류(requests의 HTTPError)와 반드시 구분해야 한다 —
    이 예외가 났다는 건 엔드포인트·TR_ID·인증·요청 바디 형식이 전부
    맞았다는 뜻이기도 하다. 주문 경로를 검증할 때 이 구분이 핵심이라
    별도 예외로 뒀고, 나중에 "장 시간 아님"처럼 재시도할 만한 거부와
    "잔고 부족"처럼 재시도하면 안 되는 거부를 코드로 나눌 여지도 남긴다."""

    def __init__(self, rt_cd: str, msg_cd: str, msg1: str):
        self.rt_cd = rt_cd
        self.msg_cd = msg_cd
        self.msg1 = msg1
        super().__init__(f"KIS 주문 거부: {msg1} (rt_cd={rt_cd}, msg_cd={msg_cd})")


_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.0


def _kis_payload(response: requests.Response) -> dict | None:
    """응답 본문이 KIS의 업무 응답(rt_cd를 담은 JSON)이면 그걸 돌려준다.

    KIS는 업무 오류를 HTTP 200이 아니라 500으로 내려주면서 본문에 사유를
    담는다(예: 초당 호출 제한 → HTTP 500 + {"rt_cd":"1","msg_cd":"EGW00201"}).
    그래서 상태 코드만 보고 raise_for_status()로 먼저 터뜨리면 정작 사유를
    못 읽고 "정체불명의 서버 오류"로 오판하게 된다 — 실제로 그렇게 오판했다.
    본문을 먼저 확인하고, KIS 업무 응답이 아닐 때만 HTTP 오류로 취급한다."""
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and "rt_cd" in payload else None


def _토큰이_죽었나(payload: dict | None) -> bool:
    """KIS가 '이 토큰 못 쓴다'고 답했나.

    만료 시각만 믿으면 안 된다 — 같은 앱키로 새 토큰을 발급하면 앞의 것이
    바로 죽는다. 우리 시계에는 아직 몇 시간 남아 있어도 그렇다."""
    if not payload:
        return False
    if str(payload.get("msg_cd", "")) == _TOKEN_EXPIRED_MSG_CD:
        return True
    말 = str(payload.get("msg1", ""))
    return "만료된 token" in 말 or "유효하지 않은 token" in 말


def parse_minute_bars(payload: dict) -> list[MinuteBar]:
    """KIS 분봉 응답을 MinuteBar로. 네트워크 없이 테스트할 수 있게 따로 뒀다.

    값이 0인 봉은 버린다 — 체결이 없던 시간대에 0이 담겨 오는 경우가 있는데,
    그대로 쓰면 시가·저가가 0으로 잡혀 30분 칸 전체가 망가진다."""
    bars: list[MinuteBar] = []
    for row in payload.get("output2") or []:
        시각 = str(row.get("stck_cntg_hour", ""))[:4]
        if len(시각) != 4:
            continue
        try:
            시가, 고가, 저가, 종가 = (
                float(row["stck_oprc"]),
                float(row["stck_hgpr"]),
                float(row["stck_lwpr"]),
                float(row["stck_prpr"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        if min(시가, 고가, 저가, 종가) <= 0:
            continue
        bars.append(
            MinuteBar(
                hhmm=시각,
                open=시가,
                high=고가,
                low=저가,
                close=종가,
                volume=int(float(row.get("cntg_vol") or 0)),
            )
        )
    return bars


class KISClient(MarketDataSource):
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str = "",
        account_product_cd: str = "01",
        is_paper: bool = True,
        sleep_fn: Callable[[float], None] = time.sleep,
        token_store=None,
    ):
        #: 발급받은 토큰을 프로세스 밖에 남겨 두는 곳(SettingsService).
        #: 없으면 예전처럼 메모리에만 두고 매번 새로 발급받는다 —
        #: 테스트와 일회성 스크립트는 그 편이 간단하다.
        self._token_store = token_store
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.account_product_cd = account_product_cd
        self.is_paper = is_paper
        self.base_url = PAPER_BASE_URL if is_paper else REAL_BASE_URL
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        if token_store is not None:
            보관된것, 만료 = token_store.get_kis_token()
            if 보관된것:
                self._access_token, self._token_expires_at = 보관된것, 만료
        self._sleep = sleep_fn
        self._min_request_interval = _MIN_REQUEST_INTERVAL_PAPER if is_paper else _MIN_REQUEST_INTERVAL_REAL
        self._last_request_at: float = 0.0

    def _throttle(self) -> None:
        wait = self._min_request_interval - (time.time() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.time()

    def _post(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        return requests.post(url, **kwargs)

    def _get(self, url: str, **kwargs) -> requests.Response:
        self._throttle()
        return requests.get(url, **kwargs)

    def _토큰_갈아끼우기(self, kwargs: dict) -> bool:
        """죽은 토큰을 버리고 새로 받아 헤더에 끼워 넣는다.

        `_ensure_token`이 새 토큰을 보관소에도 다시 써 주므로, 다음 프로세스는
        살아 있는 것을 물려받는다."""
        headers = kwargs.get("headers")
        if not isinstance(headers, dict):
            return False
        self._access_token = None
        self._token_expires_at = 0.0
        if self._token_store is not None:
            # 보관소에 죽은 것을 남겨 두면 다음 프로세스가 또 그걸 집는다.
            with suppress(Exception):
                self._token_store.set_kis_token("", 0.0)
        headers["authorization"] = f"Bearer {self._ensure_token()}"
        logger.info("KIS 토큰이 죽어 있어 새로 발급받았습니다 — 그 요청을 한 번 다시 보냅니다.")
        return True

    def _post_with_rate_limit_retry(self, url: str, **kwargs) -> requests.Response:
        """초당 호출 제한(EGW00201)으로 거부된 경우에만 재시도한다.

        POST(주문)를 무턱대고 재시도하면 중복 체결 위험이 있지만, 이 거부는
        "요청이 아예 접수되지 않았다"는 뜻이라 재시도해도 안전하다. 그 외의
        거부(잔고 부족·장 시간 아님 등)는 재시도하지 않고 그대로 올린다."""
        response = self._post(url, **kwargs)
        토큰다시 = True
        for attempt in range(1, _MAX_RETRIES):
            payload = _kis_payload(response)
            # 토큰이 죽은 것은 재시도할 값어치가 있다. 주문이 접수되지 않은
            # 것이 확실하므로 중복 체결 위험도 없다.
            if 토큰다시 and _토큰이_죽었나(payload) and self._토큰_갈아끼우기(kwargs):
                토큰다시 = False
                response = self._post(url, **kwargs)
                continue
            if payload is None or payload.get("msg_cd") != _RATE_LIMIT_MSG_CD:
                return response
            logger.warning(
                f"KIS 초당 호출 제한으로 거부됨 — {attempt}차 재시도 "
                f"({_RETRY_BACKOFF_SECONDS * attempt:.1f}초 대기)"
            )
            self._sleep(_RETRY_BACKOFF_SECONDS * attempt)
            response = self._post(url, **kwargs)
        return response

    def _get_with_retry(self, url: str, **kwargs) -> requests.Response:
        response = self._get(url, **kwargs)
        # 토큰이 죽어 있으면 HTTP 200으로 오기도 한다. 상태 코드만 보면 못 잡는다.
        if _토큰이_죽었나(_kis_payload(response)) and self._토큰_갈아끼우기(kwargs):
            response = self._get(url, **kwargs)
        attempt = 1
        while response.status_code >= 500 and attempt < _MAX_RETRIES:
            # 예전엔 "원인 모를 500"으로 뭉뚱그려 재시도했는데, 주문 검증 과정에서
            # KIS가 초당 호출 제한을 500+EGW00201로 내려준다는 걸 확인했다.
            # 사유를 남겨 두면 다음에 같은 문제를 추측하지 않아도 된다.
            payload = _kis_payload(response)
            if payload is not None:
                logger.warning(
                    f"KIS 시세조회 거부({response.status_code}) — "
                    f"{payload.get('msg1', '')} (msg_cd={payload.get('msg_cd', '')}), {attempt}차 재시도"
                )
            self._sleep(_RETRY_BACKOFF_SECONDS * attempt)
            response = self._get(url, **kwargs)
            attempt += 1
        return response

    @classmethod
    def from_settings(cls, settings_service: SettingsService) -> KISClient:
        """SettingsService(=DB, 대시보드/CLI로 갱신됨)에서 현재 인증정보를
        읽어 클라이언트를 만든다. 인증정보가 바뀔 수 있으므로 오래 붙들고
        쓰지 말고, 필요할 때마다(예: 스케줄 작업 시작 시) 새로 생성할 것."""
        creds = settings_service.get_kis_credentials()
        return cls(
            token_store=settings_service,
            app_key=creds.app_key,
            app_secret=creds.app_secret,
            account_no=creds.account_no,
            account_product_cd=creds.account_product_cd,
            is_paper=not creds.is_real,
        )

    def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        response = self._post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload["expires_in"]) - 60
        if self._token_store is not None:
            self._token_store.set_kis_token(self._access_token, self._token_expires_at)
        return self._access_token

    def _auth_headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._ensure_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
        }

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        response = self._get_with_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self._auth_headers("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("output2") or []
        if not rows:
            return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            {
                "trade_date": [
                    date(int(r["stck_bsop_date"][:4]), int(r["stck_bsop_date"][4:6]), int(r["stck_bsop_date"][6:8]))
                    for r in rows
                ],
                "open": [float(r["stck_oprc"]) for r in rows],
                "high": [float(r["stck_hgpr"]) for r in rows],
                "low": [float(r["stck_lwpr"]) for r in rows],
                "close": [float(r["stck_clpr"]) for r in rows],
                "volume": [int(r["acml_vol"]) for r in rows],
            }
        )
        return df.sort_values("trade_date").reset_index(drop=True)

    def get_minute_bars(self, symbol: str, until: str) -> list[MinuteBar]:
        """**당일** 분봉을 until(HHMMSS) 시각부터 거꾸로 최대 30개 받는다.

        과거 날짜는 못 받는다 — 이 API는 당일치만 준다. 그래서 30분 칸
        하나가 정확히 한 번의 호출과 맞아떨어진다(칸 하나 = 30분 = 30개).
        예: until="093000"이면 09:01~09:30이 온다.

        빈 응답은 오류가 아니다. 그 시간대에 체결이 없었을 수도 있고,
        15:20~15:30처럼 단일가 구간이라 분봉 자체가 없을 수도 있다."""
        response = self._get_with_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            headers=self._auth_headers("FHKST03010200"),
            params={
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": until,
                "FID_PW_DATA_INCU_YN": "N",
            },
            timeout=10,
        )
        response.raise_for_status()
        return parse_minute_bars(response.json())

    def place_cash_order(
        self, symbol: str, side: OrderSide, quantity: int, reference_price: float
    ) -> OrderResult:
        """시장가 현금주문. reference_price는 실제 체결가가 아니라, 우리
        쪽 기록/알림에 쓸 기준가(직전 종가)다 — 체결가는 별도 주문조회
        API로 확인해야 하며 이 MVP는 그 조회를 하지 않는다."""
        env = "paper" if self.is_paper else "real"
        tr_id = _BUY_TR_ID[env] if side == OrderSide.BUY else _SELL_TR_ID[env]

        response = self._post_with_rate_limit_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._auth_headers(tr_id),
            json={
                "CANO": self.account_no,
                "ACNT_PRDT_CD": self.account_product_cd,
                "PDNO": symbol,
                "ORD_DVSN": _MARKET_ORDER_DVSN,
                "ORD_QTY": str(quantity),
                "ORD_UNPR": "0",
            },
            timeout=10,
        )
        # 상태 코드보다 본문을 먼저 본다 — KIS는 업무 거부도 HTTP 500으로
        # 내려주면서 본문에 사유를 담기 때문에, raise_for_status()를 먼저
        # 호출하면 "초당 호출 제한" 같은 명확한 사유를 놓치게 된다.
        payload = _kis_payload(response)
        if payload is None:
            response.raise_for_status()
            raise RuntimeError(f"KIS 주문 응답을 해석할 수 없습니다: {response.text[:300]}")

        if payload.get("rt_cd") != "0":
            raise KISOrderRejected(
                rt_cd=str(payload.get("rt_cd", "")),
                msg_cd=str(payload.get("msg_cd", "")),
                msg1=str(payload.get("msg1", payload)),
            )

        return OrderResult(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=reference_price,
            order_id=payload["output"]["ODNO"],
            is_paper=self.is_paper,
            reference_price=reference_price,
        )

    def _daily_ccld_rows(
        self, order_date: date | None = None, end_date: date | None = None
    ) -> list[dict] | None:
        """주문체결조회 원본 행들. 조회를 거부당하면 None.

        체결가 확인(get_fill), 미체결 주문 찾기(get_open_orders), 기간 대조
        (get_orders_between)이 같은 API를 본다 — 한 번만 짜 두고 나눠 쓴다.

        한 번에 오는 것은 50건까지라, 기간으로 부르면 **연속조회로 끝까지
        따라간다.** 안 따라가면 오래된 것부터 조용히 빠지는데, 대조 도구에서
        그건 "증권사에 없는 주문"으로 둔갑한다."""
        env = "paper" if self.is_paper else "real"
        시작 = (order_date or date.today()).strftime("%Y%m%d")  # noqa: DTZ011 — 날짜만 필요
        끝 = (end_date or order_date or date.today()).strftime("%Y%m%d")  # noqa: DTZ011

        모은것: list[dict] = []
        fk = nk = ""
        tr_cont = ""
        for _ in range(_CCLD_MAX_PAGES):
            headers = self._auth_headers(_DAILY_CCLD_TR_ID[env])
            if tr_cont:
                headers["tr_cont"] = tr_cont
            response = self._get_with_retry(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                headers=headers,
                params={
                    "CANO": self.account_no,
                    "ACNT_PRDT_CD": self.account_product_cd,
                    "INQR_STRT_DT": 시작,
                    "INQR_END_DT": 끝,
                    "SLL_BUY_DVSN_CD": "00",  # 00: 전체
                    "INQR_DVSN": "00",  # 00: 역순
                    "PDNO": "",
                    "CCLD_DVSN": "00",  # 00: 전체(체결/미체결 모두)
                    "ORD_GNO_BRNO": "",
                    "ODNO": "",
                    "INQR_DVSN_3": "00",
                    "INQR_DVSN_1": "",
                    "CTX_AREA_FK100": fk,
                    "CTX_AREA_NK100": nk,
                },
                timeout=10,
            )
            payload = _kis_payload(response)
            if payload is None:
                response.raise_for_status()
                raise RuntimeError(f"KIS 체결조회 응답을 해석할 수 없습니다: {response.text[:300]}")
            if payload.get("rt_cd") != "0":
                logger.warning(
                    f"체결조회 거부: {payload.get('msg1')} (msg_cd={payload.get('msg_cd')})"
                )
                return None if not 모은것 else 모은것

            모은것.extend(payload.get("output1") or [])

            # 헤더의 tr_cont가 M/F면 다음 쪽이 있다. 이어받을 열쇠는 본문에 온다.
            다음 = str(response.headers.get("tr_cont", "")).strip().upper()
            if 다음 not in ("M", "F"):
                break
            fk = str(payload.get("ctx_area_fk100", "") or "").strip()
            nk = str(payload.get("ctx_area_nk100", "") or "").strip()
            tr_cont = "N"
        else:
            logger.warning(
                f"체결조회가 {_CCLD_MAX_PAGES}쪽에서 멈췄습니다 — 더 있을 수 있습니다. "
                "기간을 나눠 다시 부르세요."
            )
        return 모은것

    def get_orders_between(self, start: date, end: date) -> list[dict] | None:
        """기간 안의 주문체결 원본 행들. 우리 기록과 대조할 때 쓴다."""
        return self._daily_ccld_rows(start, end)

    def get_fill(self, order_id: str, order_date: date | None = None) -> FillInfo | None:
        """주문번호로 실제 체결 수량·평균 체결가를 조회한다.

        시장가 주문은 넣어봐야 얼마에 체결되는지 알 수 있어서, 주문 시점의
        기준가(직전 종가)만 기록하면 손익 집계에 오차가 쌓인다. 체결 직후
        이걸로 실제 값을 받아와 기록을 바로잡는다.

        해당 주문번호를 못 찾으면 None을 돌려준다(조회 시점에 아직 반영되지
        않았을 수 있다). 필드명은 한국투자증권 공식 예제 저장소의
        COLUMN_MAPPING과 대조했다: odno=주문번호, tot_ccld_qty=총체결수량,
        avg_prvs=평균가, ord_qty=주문수량."""
        rows = self._daily_ccld_rows(order_date)
        if rows is None:
            return None

        for row in rows:
            if str(row.get("odno", "")).lstrip("0") != str(order_id).lstrip("0"):
                continue
            filled = int(float(row.get("tot_ccld_qty") or 0))
            # 미체결이면 평균가가 0/빈 값으로 오므로 그대로 두면 손익이 왜곡된다
            avg_price = float(row.get("avg_prvs") or 0)
            return FillInfo(
                order_id=str(row.get("odno", order_id)),
                symbol=str(row.get("pdno", "")),
                ordered_quantity=int(float(row.get("ord_qty") or 0)),
                filled_quantity=filled,
                avg_fill_price=avg_price,
                name=str(row.get("prdt_name", "")).strip(),
            )
        return None

    def get_open_orders(self, order_date: date | None = None) -> list[OpenOrder]:
        """아직 다 채워지지 않은 그날 주문들 — 되돌릴 수 있는 것만.

        "정정취소가능주문조회"가 모의투자에 없어서, 이미 쓰고 있는
        주문체결조회에서 뽑는다. 남기는 기준은 셋이다:
          - 취소여부(cncl_yn)가 Y가 아니고,
          - 잔여수량(rmn_qty)이 1주 이상이고,
          - 주문번호가 있다.

        조회가 실패하면 **빈 목록이 아니라 예외**를 올린다. 여기서 조용히
        빈 목록을 주면 "취소할 게 없습니다"로 읽혀서, 남아 있는 주문을 못
        본 채로 지나가게 된다."""
        rows = self._daily_ccld_rows(order_date)
        if rows is None:
            raise RuntimeError("미체결 주문을 조회하지 못했습니다 — 취소할 것이 없는지 알 수 없습니다.")

        나온것: list[OpenOrder] = []
        for row in rows:
            if str(row.get("cncl_yn", "")).upper() == "Y":
                continue
            order_id = str(row.get("odno", "")).strip()
            if not order_id or order_id.lstrip("0") == "":
                continue

            주문 = int(float(row.get("ord_qty") or 0))
            체결 = int(float(row.get("tot_ccld_qty") or 0))
            남은것 = row.get("rmn_qty")
            잔여 = int(float(남은것)) if str(남은것 or "").strip() else 주문 - 체결
            if 잔여 <= 0:
                continue

            나온것.append(
                OpenOrder(
                    order_id=order_id,
                    symbol=str(row.get("pdno", "")).strip(),
                    name=str(row.get("prdt_name", "")).strip(),
                    # 01=매도, 02=매수 (KIS 주문체결조회 sll_buy_dvsn_cd)
                    side=OrderSide.SELL if str(row.get("sll_buy_dvsn_cd", "")) == "01" else OrderSide.BUY,
                    ordered_quantity=주문,
                    filled_quantity=체결,
                    remaining=잔여,
                    price=float(row.get("ord_unpr") or 0),
                    ord_dvsn_cd=str(row.get("ord_dvsn_cd", "")).strip() or _MARKET_ORDER_DVSN,
                    branch_no=str(row.get("ord_gno_brno", "")).strip(),
                    # KIS 공식 예제의 필드명이 excg_id_dvsn_Cd(대문자 C)로
                    # 적혀 있다. 어느 쪽으로 오든 읽도록 둘 다 본다.
                    exchange=str(
                        row.get("excg_id_dvsn_cd") or row.get("excg_id_dvsn_Cd") or "KRX"
                    ).strip()
                    or "KRX",
                    ordered_at=str(row.get("ord_tmd", "")).strip(),
                )
            )
        return 나온것

    def cancel_order(self, order: OpenOrder) -> str:
        """미체결 잔여를 **전량 취소**한다. 새로 채번된 취소주문번호를 돌려준다.

        지금까지 이 저장소에는 낸 주문을 되돌릴 길이 없었다 — 잘못 나갔다는
        걸 알아도 한국투자증권에 직접 로그인하는 수밖에 없었다. 이게 그 길이다.

        일부만 취소하는 길은 일부러 안 만들었다. 되돌리는 상황은 "이 주문이
        잘못됐다"는 판단이고, 그때 몇 주만 남기는 선택은 새 판단이지
        되돌리기가 아니다. 되돌리기는 전부 아니면 전무로 둔다.

        이미 체결된 주문은 KIS가 거부한다(그게 맞다). 그 거부는
        KISOrderRejected로 그대로 올라온다."""
        env = "paper" if self.is_paper else "real"

        response = self._post_with_rate_limit_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl",
            headers=self._auth_headers(_RVSECNCL_TR_ID[env]),
            json={
                "CANO": self.account_no,
                "ACNT_PRDT_CD": self.account_product_cd,
                "KRX_FWDG_ORD_ORGNO": order.branch_no,
                "ORGN_ODNO": order.order_id,
                "ORD_DVSN": order.ord_dvsn_cd,
                "RVSE_CNCL_DVSN_CD": _취소,
                "ORD_QTY": str(order.remaining),
                "ORD_UNPR": "0",  # 취소에는 단가가 의미 없다
                "QTY_ALL_ORD_YN": "Y",  # 잔량 전부
                "EXCG_ID_DVSN_CD": order.exchange,
            },
            timeout=10,
        )
        payload = _kis_payload(response)
        if payload is None:
            response.raise_for_status()
            raise RuntimeError(f"KIS 취소 응답을 해석할 수 없습니다: {response.text[:300]}")

        if payload.get("rt_cd") != "0":
            raise KISOrderRejected(
                rt_cd=str(payload.get("rt_cd", "")),
                msg_cd=str(payload.get("msg_cd", "")),
                msg1=str(payload.get("msg1", payload)),
            )

        새주문번호 = str((payload.get("output") or {}).get("ODNO", ""))
        logger.info(
            f"취소 접수: {order.symbol} 원주문 {order.order_id} 잔여 "
            f"{order.remaining}주 → 취소주문 {새주문번호}"
        )
        return 새주문번호

    def get_orderable(self, symbol: str, price: float) -> int:
        """이 종목을 **미수 없이** 몇 주까지 살 수 있나. 못 물어보면 -1.

        우리 엔진은 현금을 스스로 계산해 왔다 — 사면 빼고 팔면 더한다.
        부분 체결·거부·손매매가 있으면 그 값이 조용히 어긋나고, 비중 상한과
        일일 손실한도가 전부 그 어긋난 값 위에서 돈다. 2026-08-25에 실제로
        294만원이 벌어진 채로 돌았다.

        증권사는 증거금율까지 반영해 정확히 알려 준다. 그걸 쓴다.

        **주문구분을 반드시 01(시장가)로 준다.** 00(지정가)로 물어보면
        종목증거금율이 반영되지 않은 수량이 나온다 — 한투 문서가 "반드시"라고
        적어 둔 자리다. 우리는 시장가로만 주문하므로 짝도 맞는다.

        못 물어봤을 때 0이 아니라 **-1**을 돌려준다. 0은 "살 수 없다"는
        답이고 -1은 "못 물어봤다"인데, 둘을 같게 두면 조회 한 번 실패한 것이
        그날 매수를 통째로 막는다.

        ## 2026-08-26에 실제로 그렇게 막혔다

        처음 쓴 코드는 예외만 -1로 돌렸고, **거부 응답과 빈 칸은 그대로 0**이
        됐다. rt_cd를 안 봤고 `or 0`이 빈 값을 0으로 눌렀다.

            output = response.json().get("output") or {}
            return int(float(output.get("nrcvb_buy_qty") or 0))

        그래서 승인된 두 종목이 "증권사가 매수가능수량 0으로 답했습니다"로
        막혔다. 증권사가 그렇게 답한 것이 아니라, **우리가 못 알아들은 것을
        0으로 적은 것**이다. -1을 둔 이유가 바로 이건데 구멍을 남겨 뒀다.

        지금은 셋을 가른다.
          - 통신·예외 → -1 (못 물어봤다)
          - rt_cd가 0이 아님 → -1 (증권사가 조회를 거부했다)
          - 칸이 비었음 → -1 (답에 그 값이 없다)
          - 숫자 0 → 0 (증권사가 진짜로 못 산다고 답했다)

        그리고 **왜 0인지 알 수 있게 주문가능현금을 같이 남긴다.** 안 남기면
        다음에 또 막혔을 때 원인을 못 찾는다.
        """
        env = "paper" if self.is_paper else "real"
        try:
            response = self._get_with_retry(
                f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers=self._auth_headers(_PSBL_ORDER_TR_ID[env]),
                params={
                    "CANO": self.account_no,
                    "ACNT_PRDT_CD": self.account_product_cd,
                    "PDNO": symbol,
                    "ORD_UNPR": str(int(price)),
                    "ORD_DVSN": "01",          # 시장가 — 증거금율이 반영된다
                    "CMA_EVLU_AMT_ICLD_YN": "N",
                    "OVRS_ICLD_YN": "N",
                },
                timeout=10,
            )
            payload = response.json()
        except Exception as e:  # noqa: BLE001 — 조회 실패가 매매를 멈춰선 안 된다
            logger.warning(f"매수가능조회를 못 했습니다({symbol}): {e} — 우리 현금 계산으로 갑니다.")
            return -1

        if str(payload.get("rt_cd", "")) != "0":
            logger.warning(
                f"매수가능조회를 증권사가 거부했습니다({symbol}): "
                f"{payload.get('msg1')} (msg_cd={payload.get('msg_cd')}) "
                "— 우리 현금 계산으로 갑니다."
            )
            return -1

        output = payload.get("output") or {}
        칸 = str(output.get("nrcvb_buy_qty", "")).strip()
        if not 칸:
            logger.warning(
                f"매수가능조회 답에 미수없는매수수량이 없습니다({symbol}) — "
                f"받은 칸: {sorted(output)[:12]}. 우리 현금 계산으로 갑니다."
            )
            return -1

        try:
            수량 = int(float(칸))
        except ValueError:
            logger.warning(f"매수가능수량을 숫자로 못 읽었습니다({symbol}): {칸!r}")
            return -1

        현금 = str(output.get("ord_psbl_cash", "")).strip()
        logger.info(
            f"매수가능조회({symbol} @ {price:,.0f}원): {수량}주"
            + (f" · 주문가능현금 {float(현금):,.0f}원" if 현금 else "")
        )
        return 수량

    def get_balance(self) -> AccountBalance:
        """증권사 계좌의 실제 잔고(현금·보유종목)를 조회한다.

        이 프로그램은 그동안 현금을 스스로 계산해 왔는데(engine_state.cash),
        주문이 일부만 체결되거나 거부되면 그 값이 실제 계좌와 조용히 어긋난다.
        대조할 "정답지"가 필요해서 붙였다.

        필드명은 한국투자증권 공식 예제 저장소의 COLUMN_MAPPING과 대조했다:
        output1(보유종목) pdno/hldg_qty/pchs_avg_pric/prpr/evlu_amt/evlu_pfls_amt,
        output2(계좌요약) dnca_tot_amt(예수금)/scts_evlu_amt(유가평가)/nass_amt(순자산).
        """
        env = "paper" if self.is_paper else "real"
        response = self._get_with_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._auth_headers(_BALANCE_TR_ID[env]),
            params={
                "CANO": self.account_no,
                "ACNT_PRDT_CD": self.account_product_cd,
                "AFHR_FLPR_YN": "N",  # 시간외단일가 반영 안 함
                "OFL_YN": "",
                "INQR_DVSN": "02",  # 종목별
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",  # 전일매매 포함
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            timeout=10,
        )
        payload = _kis_payload(response)
        if payload is None:
            response.raise_for_status()
            raise RuntimeError(f"KIS 잔고조회 응답을 해석할 수 없습니다: {response.text[:300]}")
        if payload.get("rt_cd") != "0":
            raise RuntimeError(
                f"KIS 잔고조회 실패: {payload.get('msg1')} (msg_cd={payload.get('msg_cd')})"
            )

        holdings = []
        for row in payload.get("output1") or []:
            quantity = int(float(row.get("hldg_qty") or 0))
            if quantity <= 0:
                continue  # 과거에 보유했다 청산한 종목이 수량 0으로 남아 오기도 한다
            holdings.append(
                Holding(
                    symbol=str(row.get("pdno", "")),
                    name=str(row.get("prdt_name", "")),
                    quantity=quantity,
                    avg_buy_price=float(row.get("pchs_avg_pric") or 0),
                    current_price=float(row.get("prpr") or 0),
                    eval_amount=float(row.get("evlu_amt") or 0),
                    pnl_amount=float(row.get("evlu_pfls_amt") or 0),
                )
            )

        summary_rows = payload.get("output2") or []
        summary = summary_rows[0] if summary_rows else {}

        # 현금은 **가수도정산금액(prvs_rcdl_excc_amt)**을 쓴다 — 결제(T+2)까지
        # 끝났다고 보고 계산한 현금이라 오늘 낸 주문이 이미 반영돼 있다.
        #
        # 예수금 총액(dnca_tot_amt)을 쓰면 안 된다. 매수 대금이 결제 전까지
        # 거기서 안 빠져서, 오늘 산 것을 이틀 동안 못 본다. 실제로 HPSP 2주를
        # 시험 매수한 날 조회해서 확인한 값이다(2026-08-24):
        #
        #   dnca_tot_amt        10,000,145   ← 매수가 아직 안 빠짐
        #   thdt_buy_amt            90,100   ← 오늘 매수 대금
        #   thdt_tlex_amt               10   ← 오늘 제비용
        #   prvs_rcdl_excc_amt   9,910,035   ← 10,000,145 − 90,100 − 10
        #
        # 우리 엔진의 현금도 "주문을 내면 즉시 빠지는" 값이라 이쪽과 짝이 맞는다.
        현금 = summary.get("prvs_rcdl_excc_amt") or summary.get("dnca_tot_amt")
        return AccountBalance(
            cash=float(현금 or 0),
            total_eval_amount=float(summary.get("scts_evlu_amt") or 0),
            net_asset=float(summary.get("nass_amt") or 0),
            holdings=holdings,
            raw_summary={str(k): str(v) for k, v in summary.items()},
        )

    #: fid_blng_cls_code — 어떤 기준으로 줄을 세울지.
    #: 거래대금을 기본으로 쓴다. 주식 수(거래량)로 세우면 주가가 싼 종목이
    #: 위를 차지하는데, 100원짜리 100만주는 1억원이고 10만원짜리 1만주는
    #: 10억원이다. 실제로 돈이 몰린 곳은 후자다.
    VOLUME_RANK_BASIS: ClassVar[dict[str, str]] = {
        "amount": "3",  # 거래금액순
        "volume": "0",  # 평균거래량
        "surge": "1",  # 거래증가율
    }

    def get_top_volume(
        self,
        market: str = "all",
        limit: int = 100,
        basis: str = "amount",
        min_price: int = 0,
    ) -> list[tuple[str, str, int]]:
        """거래가 몰린 상위 종목을 (종목코드, 종목명, 누적거래대금) 목록으로.

        시가총액 순위와 다른 종목군을 준다. 시총 상위는 대형주라 하루
        1~2% 움직이는 반면, 거래가 몰리는 쪽은 변동성이 크고 단기 전략의
        전제(눌림목·거래량 급증)가 성립하는 자리다. 단기 갈래를 검토하려면
        그 종목군에서 재 봐야 한다 — 시총 상위에서 단타 전략을 시험하는 건
        틀린 운동장에서 재는 것이다.

        min_price를 주면 그 아래 가격의 종목을 뺀다. 저가주는 호가 단위가
        가격 대비 커서 백테스트의 종가 체결 가정이 실제와 크게 벌어진다.

        주의: 순위 API는 모의투자를 지원하지 않을 수 있다(시총 순위와 동일).
        """
        market_codes = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}
        if market not in market_codes:
            raise ValueError(f"market은 {list(market_codes)} 중 하나여야 합니다: {market}")
        if basis not in self.VOLUME_RANK_BASIS:
            raise ValueError(
                f"basis는 {list(self.VOLUME_RANK_BASIS)} 중 하나여야 합니다: {basis}"
            )

        response = self._get_with_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=self._auth_headers("FHPST01710000"),
            params={
                "fid_cond_mrkt_div_code": "J",  # 주식
                "fid_cond_scr_div_code": "20171",  # 화면번호(고정)
                "fid_input_iscd": market_codes[market],
                "fid_div_cls_code": "1",  # 0:전체 1:보통주 2:우선주
                "fid_blng_cls_code": self.VOLUME_RANK_BASIS[basis],
                "fid_trgt_cls_code": "111111111",  # 대상 구분 9자리(전체 포함)
                "fid_trgt_exls_cls_code": "000000",  # 제외 구분 6자리(제외 없음)
                "fid_input_price_1": str(min_price) if min_price else "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_input_date_1": "",
            },
            timeout=10,
        )
        payload = _kis_payload(response)
        if payload is None:
            response.raise_for_status()
            raise RuntimeError(f"KIS 거래량순위 응답을 해석할 수 없습니다: {response.text[:300]}")
        if payload.get("rt_cd") != "0":
            raise RuntimeError(
                f"KIS 거래량순위 조회 실패: {payload.get('msg1')} "
                f"(msg_cd={payload.get('msg_cd')}) — 모의투자 미지원 API일 수 있습니다."
            )

        rows = []
        for row in (payload.get("output") or [])[:limit]:
            symbol = str(row.get("mksc_shrn_iscd", "")).strip()
            name = str(row.get("hts_kor_isnm", "")).strip()
            if not symbol or not name:
                continue
            # 누적거래대금은 원 단위 문자열로 온다 — 백만원으로 줄여 담는다
            turnover = int(float(row.get("acml_tr_pbmn") or 0) / 1_000_000)
            rows.append((symbol, name, turnover))
        return rows

    def get_top_market_cap(
        self, market: str = "all", limit: int = 100
    ) -> list[tuple[str, str, int]]:
        """시가총액 상위 종목을 (종목코드, 종목명, 시가총액) 목록으로 돌려준다.

        손으로 고른 종목 목록은 시간이 지나면 낡는다(상장폐지·순위 역전 등).
        이걸로 주기적으로 다시 뽑아 유니버스를 갱신한다.

        market: "all" | "kospi" | "kosdaq"
        보통주만 조회한다(fid_div_cls_code=1) — 우선주는 같은 회사가 중복으로
        잡히는 데다 거래량도 훨씬 적어 단타 대상으로 부적절하다.

        주의: KIS의 순위·시세 API 일부는 모의투자를 지원하지 않는다. 모의투자
        키로 거부되면 KISOrderRejected가 아니라 RuntimeError로 사유가 올라온다.
        """
        market_codes = {"all": "0000", "kospi": "0001", "kosdaq": "1001"}
        if market not in market_codes:
            raise ValueError(f"market은 {list(market_codes)} 중 하나여야 합니다: {market}")

        response = self._get_with_retry(
            f"{self.base_url}/uapi/domestic-stock/v1/ranking/market-cap",
            headers=self._auth_headers("FHPST01740000"),
            params={
                "fid_cond_mrkt_div_code": "J",  # 주식
                "fid_cond_scr_div_code": "20174",  # 화면번호(고정)
                "fid_div_cls_code": "1",  # 0:전체 1:보통주 2:우선주
                "fid_input_iscd": market_codes[market],
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_input_price_1": "",  # 가격 하한 없음
                "fid_input_price_2": "",  # 가격 상한 없음
                "fid_vol_cnt": "",  # 거래량 하한 없음
            },
            timeout=10,
        )
        payload = _kis_payload(response)
        if payload is None:
            response.raise_for_status()
            raise RuntimeError(f"KIS 시총순위 응답을 해석할 수 없습니다: {response.text[:300]}")
        if payload.get("rt_cd") != "0":
            raise RuntimeError(
                f"KIS 시총순위 조회 실패: {payload.get('msg1')} "
                f"(msg_cd={payload.get('msg_cd')}) — 모의투자 미지원 API일 수 있습니다."
            )

        rows = []
        for row in (payload.get("output") or [])[:limit]:
            symbol = str(row.get("mksc_shrn_iscd", "")).strip()
            name = str(row.get("hts_kor_isnm", "")).strip()
            if not symbol or not name:
                continue
            # 시가총액은 억원 단위 문자열로 온다
            market_cap = int(float(row.get("stck_avls") or 0))
            rows.append((symbol, name, market_cap))
        return rows
