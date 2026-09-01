"""받아 온 일봉을 로컬 파일에 쌓아 두고 다시 쓴다.

실험 하나 실행할 때마다 야후에서 같은 6년치를 다시 받았다. 하루에 열 번 넘게
실행하면 그 시간이 실험을 덜 하게 만드는 이유가 된다.

**캐시가 고치는 것과 못 고치는 것을 분명히 해 둔다.**

고치는 것: 속도, 그리고 캐시가 살아 있는 동안의 데이터 일관성. 지금
experiment.py가 내건 "모든 변형이 같은 데이터를 본다"는 한 실행 안에서만
참이었다. 실행이 달라지면 야후가 과거 값을 수정했을 때 조용히 달라진다.
캐시가 있으면 적어도 캐시를 지우기 전까지는 같은 데이터를 본다.

못 고치는 것: 완전한 재현성. 캐시를 지우거나 새 기간을 요청하면 그때의
야후 데이터를 받는다. 논문 수준의 재현성이 필요하면 데이터셋을 따로
얼려서 저장소에 넣어야 하는데, 그건 지금 필요보다 무겁다.

운영 DB(muwon.db)에는 넣지 않는다. 그 파일은 워크플로마다 구글드라이브에서
받고 올리는데, 종목 58개 6년치면 8만 행이 넘어 매번 그걸 실어 나르게 된다.
캐시는 없어도 그만인 파일이라 따로 둔다.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from muwon.db.models import PriceBarRow

COLUMNS = ["trade_date", "open", "high", "low", "close", "volume"]

#: 환경변수로 옮길 수 있게 둔다. CI에서는 캐시 디렉터리를 따로 잡는다.
DEFAULT_CACHE_PATH = Path(os.environ.get("MUWON_PRICE_CACHE", ".cache/prices.sqlite"))

#: 짧게 왔을 때 다시 받아 볼 횟수.
RETRIES = 3


class PriceCache:
    """(종목, 날짜) 단위로 일봉을 보관한다.

    '이 구간은 이미 받아 봤다'를 봉 데이터가 아니라 **요청 구간 기록**으로
    판단한다. 처음엔 저장된 봉의 최초/최종 날짜로 판정했는데, 요청 구간의
    시작·끝이 주말이나 휴일이면 그 날짜의 봉이 있을 수가 없어 항상 '덜 받았다'로
    나왔다. 캐시가 한 번도 안 맞았다. 거래일이 아닌 날에 봉을 기대한 것이
    잘못이다.

    부분적으로 이어 붙이지는 않는다. 겹치지 않는 구간을 요청하면 그 종목을
    통째로 다시 받는다. 조각을 이어 붙이다 틀리는 것보다 낫다."""

    def __init__(self, path: Path | str = DEFAULT_CACHE_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{self.path}")
        PriceBarRow.__table__.create(engine, checkfirst=True)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS price_cache_coverage ("
                    "symbol TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL)"
                )
            )
        self._session_factory = sessionmaker(bind=engine, class_=Session)
        self.hits = 0
        self.misses = 0

    def covers(self, symbol: str, start: date, end: date) -> bool:
        """이 구간을 이미 받아 본 적이 있는가."""
        with self._session_factory() as session:
            found = session.execute(
                text(
                    "SELECT 1 FROM price_cache_coverage "
                    "WHERE symbol = :s AND start_date <= :a AND end_date >= :b LIMIT 1"
                ),
                {"s": symbol, "a": start.isoformat(), "b": end.isoformat()},
            ).first()
        return found is not None

    def get(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        """캐시가 구간을 덮고 있으면 돌려주고, 아니면 None."""
        if not self.covers(symbol, start, end):
            return None
        with self._session_factory() as session:
            rows = session.scalars(
                select(PriceBarRow)
                .where(
                    PriceBarRow.symbol == symbol,
                    PriceBarRow.trade_date >= start,
                    PriceBarRow.trade_date <= end,
                )
                .order_by(PriceBarRow.trade_date)
            ).all()
        return pd.DataFrame(
            [
                {
                    "trade_date": r.trade_date,
                    "open": r.open,
                    "high": r.high,
                    "low": r.low,
                    "close": r.close,
                    "volume": r.volume,
                }
                for r in rows
            ],
            columns=COLUMNS,
        )

    def put(self, symbol: str, df: pd.DataFrame, start: date, end: date) -> None:
        """받아 온 구간을 통째로 갈아 끼우고 '이 구간을 받았다'를 기록한다.

        겹치는 날짜를 지우고 다시 넣는다. 야후가 과거 값을 수정했을 때
        옛 값과 새 값이 섞이면 어느 쪽이 쓰인 건지 알 수 없게 된다.

        받은 봉이 없어도(상장 전 구간 등) 기록은 남긴다. 안 남기면 매번 다시
        받아 보고 매번 빈손으로 돌아온다."""
        with self._session_factory() as session:
            session.execute(
                text(
                    "DELETE FROM price_cache_coverage "
                    "WHERE symbol = :s AND start_date >= :a AND end_date <= :b"
                ),
                {"s": symbol, "a": start.isoformat(), "b": end.isoformat()},
            )
            session.execute(
                text(
                    "INSERT INTO price_cache_coverage (symbol, start_date, end_date) "
                    "VALUES (:s, :a, :b)"
                ),
                {"s": symbol, "a": start.isoformat(), "b": end.isoformat()},
            )
            session.commit()
        if not len(df):
            return
        days = set(df["trade_date"])
        with self._session_factory() as session:
            existing = session.scalars(
                select(PriceBarRow).where(
                    PriceBarRow.symbol == symbol,
                    PriceBarRow.trade_date >= min(days),
                    PriceBarRow.trade_date <= max(days),
                )
            ).all()
            for row in existing:
                session.delete(row)
            session.add_all(
                PriceBarRow(
                    symbol=symbol,
                    trade_date=row.trade_date,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume),
                )
                for row in df.itertuples()
            )
            session.commit()

    def fetch(
        self, source, symbol: str, yahoo_symbol: str, start: date, end: date,
        최소일수: int = 0,
    ):
        """캐시에 있으면 캐시로, 없으면 받아서 채운 뒤 돌려준다.

        ## 최소일수를 주면 '짧게 온 것'을 캐시가 굳히지 못하게 한다

        야후는 같은 요청에 어떤 때는 1년치를, 어떤 때는 **최근 20일치만**
        준다. 실제로 미 10년물이 그랬고, ACE KRX금현물·KODEX 은선물도
        그랬다(268일짜리가 20일로 왔다).

        그냥 두면 더 나쁜 일이 생긴다. **짧게 온 것이 캐시에 들어가고,
        캐시는 "이 구간은 받아 봤다"고 기록한다.** 그러면 다음 실행부터는
        야후에 물어보지도 않고 20일치를 돌려준다. 한 번의 통신 오류가
        영구적인 데이터 결손이 된다.

        그래서 최소일수를 주면 그만큼 못 받았을 때 **캐시를 건너뛰고 다시
        받는다.** 끝내 짧으면 그대로 쓰되 이유를 남긴다. 정말로 최근에
        상장한 종목일 수도 있고, 그건 데이터가 아니라 사실이다."""
        cached = self.get(symbol, start, end)
        if cached is not None and len(cached) >= 최소일수:
            self.hits += 1
            return cached
        if cached is not None:
            logger.warning(
                f"{symbol}: 캐시에 {len(cached)}일치뿐이라(최소 {최소일수}일) 다시 받습니다. "
                "짧게 온 것이 캐시에 굳으면 다음부터는 물어보지도 않습니다"
            )
        self.misses += 1

        df = source.get_daily_ohlcv(yahoo_symbol, start, end)
        for 시도 in range(RETRIES - 1):
            if df is not None and len(df) >= 최소일수:
                break
            logger.warning(
                f"{symbol}: {0 if df is None else len(df)}일치만 왔습니다 "
                f"(최소 {최소일수}일): 다시 받습니다 [{시도 + 2}/{RETRIES}]"
            )
            df = source.get_daily_ohlcv(yahoo_symbol, start, end)

        if 최소일수 and (df is None or len(df) < 최소일수):
            logger.warning(
                f"{symbol}: {RETRIES}번 받아도 {0 if df is None else len(df)}일치입니다. "
                "야후가 짧게 주는 중이거나, 정말 최근에 상장한 종목입니다"
            )
        self.put(symbol, df, start, end)
        return df

    def summary(self) -> str:
        total = self.hits + self.misses
        if not total:
            return "시세 캐시 사용 안 함"
        return f"시세 캐시 {self.hits}/{total}종목 재사용 ({self.path})"

    def log_summary(self) -> None:
        logger.info(self.summary())
