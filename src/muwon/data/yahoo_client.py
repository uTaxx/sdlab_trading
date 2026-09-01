"""백테스트 전용 시세 소스: Yahoo Finance 공개 차트 API.

KIS API는 이 프로젝트의 운영(모의투자/실거래) 시세 소스지만, 비표준 포트
(9443/29443)를 쓰기 때문에 개발 환경에 따라 접근이 막혀 있을 수 있다.
백테스트는 굳이 운영 시세 소스와 같을 필요가 없으므로, 표준 443 포트로
접근 가능한 Yahoo Finance를 개발·백테스트 전용으로 쓴다. 실거래 신호
생성과 주문 실행은 반드시 KISClient를 거친다. 이 클래스는 실거래에 쓰지 않는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import requests

from muwon.domain.interfaces import MarketDataSource

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class YahooFinanceDataSource(MarketDataSource):
    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """symbol은 Yahoo 티커다(예: '005930.KS'). universe.py의 yahoo_symbol을 쓴다."""
        period1 = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp())
        period2 = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp()) + 86400

        response = requests.get(
            CHART_URL.format(symbol=symbol),
            params={
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return self._parse_chart_response(payload)

    @staticmethod
    def _parse_chart_response(payload: dict) -> pd.DataFrame:
        columns = ["trade_date", "open", "high", "low", "close", "volume"]
        results = payload.get("chart", {}).get("result") or []
        if not results:
            return pd.DataFrame(columns=columns)

        result = results[0]
        timestamps = result.get("timestamp") or []
        quote = result["indicators"]["quote"][0]

        df = pd.DataFrame(
            {
                "trade_date": [
                    datetime.fromtimestamp(ts, tz=UTC).date() for ts in timestamps
                ],
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"],
            }
        )
        return df.dropna(subset=["close"]).reset_index(drop=True)
