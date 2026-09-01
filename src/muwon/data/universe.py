"""코스피/코스닥 대형주 유니버스.

Phase 1 MVP를 위한 시작용 목록이며, 실시간 시가총액 순위로 자동 산출한 게
아니라 사람이 고른 대표 종목들이다. 실거래 전환 전에는 실제 시가총액 상위
200종목을 주기적으로 갱신하는 방식으로 교체해야 한다 (이 파일을 손으로
계속 고치는 대신, KRX 시가총액 데이터를 주기적으로 가져와 자동 산출하는
파이프라인으로 대체: 후속 작업).

symbol: KIS 6자리 종목코드 (운영 시 KIS API가 쓰는 정식 식별자).
yahoo_symbol: 백테스트 전용 데이터 소스(YahooFinanceDataSource)가 쓰는 티커.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticker:
    symbol: str
    name: str
    market: str  # "KOSPI" | "KOSDAQ"
    yahoo_symbol: str


UNIVERSE: list[Ticker] = [
    Ticker("005930", "삼성전자", "KOSPI", "005930.KS"),
    Ticker("000660", "SK하이닉스", "KOSPI", "000660.KS"),
    Ticker("373220", "LG에너지솔루션", "KOSPI", "373220.KS"),
    Ticker("207940", "삼성바이오로직스", "KOSPI", "207940.KS"),
    Ticker("005380", "현대차", "KOSPI", "005380.KS"),
    Ticker("005490", "POSCO홀딩스", "KOSPI", "005490.KS"),
    Ticker("000270", "기아", "KOSPI", "000270.KS"),
    Ticker("068270", "셀트리온", "KOSPI", "068270.KS"),
    Ticker("035420", "NAVER", "KOSPI", "035420.KS"),
    Ticker("105560", "KB금융", "KOSPI", "105560.KS"),
    Ticker("055550", "신한지주", "KOSPI", "055550.KS"),
    Ticker("012330", "현대모비스", "KOSPI", "012330.KS"),
    Ticker("035720", "카카오", "KOSPI", "035720.KS"),
    Ticker("051910", "LG화학", "KOSPI", "051910.KS"),
    Ticker("006400", "삼성SDI", "KOSPI", "006400.KS"),
    Ticker("066570", "LG전자", "KOSPI", "066570.KS"),
    Ticker("247540", "에코프로비엠", "KOSDAQ", "247540.KQ"),
    Ticker("086520", "에코프로", "KOSDAQ", "086520.KQ"),
]


def find_by_symbol(symbol: str) -> Ticker | None:
    for ticker in UNIVERSE:
        if ticker.symbol == symbol:
            return ticker
    return None
