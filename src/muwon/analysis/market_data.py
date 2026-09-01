"""실험·진단 스크립트가 공유하는 시세 적재.

두 스크립트가 같은 루프를 각자 갖고 있었고, 그래서 한쪽만 고치면 다른 쪽은
그대로 깨졌다. 실제로 그런 일이 났다. 2022년 한 해만 조회했더니 그 해에
아직 상장 전이던 종목에서 Yahoo가 400을 돌려줬고, 종목 하나 때문에 진단
전체가 죽었다.

상장 전 종목이 유니버스에 섞이는 건 정상이다(유니버스는 오늘 기준 시총
상위다). 그러니 못 받은 종목은 건너뛰되, **몇 개를 건너뛰었는지 반드시
찍는다**. 조용히 빼면 '60종목으로 실행했다'고 착각하게 된다.
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd
import requests

from muwon.data.price_cache import PriceCache


def load_histories(
    source, tickers, start: date, end: date, cache: PriceCache | None = None
) -> dict[str, pd.DataFrame]:
    """유니버스 시세를 모은다. 조회에 실패하거나 데이터가 없는 종목은 뺀다.

    cache를 주면 이미 받아 둔 구간은 다시 받지 않는다."""
    histories: dict[str, pd.DataFrame] = {}
    skipped: list[str] = []
    for ticker in tickers:
        try:
            if cache is not None:
                df = cache.fetch(source, ticker.symbol, ticker.yahoo_symbol, start, end)
            else:
                df = source.get_daily_ohlcv(ticker.yahoo_symbol, start, end)
        except requests.RequestException:
            # 해당 구간에 상장 전이면 400이 온다. 전체를 멈출 이유가 아니다.
            skipped.append(ticker.symbol)
            continue
        if len(df):
            histories[ticker.symbol] = df
        else:
            skipped.append(ticker.symbol)

    print(f"시세 {len(histories)}종목 · {start} ~ {end}", file=sys.stderr)
    if cache is not None:
        print(f"  {cache.summary()}", file=sys.stderr)
    if skipped:
        print(
            f"  건너뜀 {len(skipped)}종목 (해당 구간 데이터 없음): "
            + ", ".join(skipped[:10])
            + ("…" if len(skipped) > 10 else ""),
            file=sys.stderr,
        )
    return histories
