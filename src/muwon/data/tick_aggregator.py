"""틱(체결) 데이터를 N초봉으로 묶는 집계기.

실시간 전략은 틱 하나하나에 반응하지 않는다. 매 체결마다 반응하면
거래비용만 쌓이는 과최적화된 초단타가 되므로, 종목별로 일정 시간
윈도우(기본 60초=1분봉)의 체결을 모아 OHLCV 봉으로 만들고, 봉이 마감될
때만(=다음 봉의 첫 틱이 들어올 때) 전략을 평가한다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Tick:
    symbol: str
    price: float
    volume: int
    timestamp: datetime


@dataclass
class Bar:
    symbol: str
    bar_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarAggregator:
    def __init__(self, bar_seconds: int = 60):
        self._bar_seconds = bar_seconds
        self._open_bars: dict[str, Bar] = {}

    def _bar_start(self, ts: datetime) -> datetime:
        epoch = int(ts.timestamp())
        bucket = epoch - (epoch % self._bar_seconds)
        return datetime.fromtimestamp(bucket, tz=ts.tzinfo)

    def add_tick(self, tick: Tick) -> Bar | None:
        """틱을 추가한다. 이 틱이 새 봉의 시작이면(직전 봉이 마감됐으면)
        마감된 직전 봉을 돌려주고, 아직 같은 봉 안이면 None을 돌려준다."""
        bar_start = self._bar_start(tick.timestamp)
        current = self._open_bars.get(tick.symbol)

        closed_bar = None
        if current is not None and current.bar_start != bar_start:
            closed_bar = current
            current = None

        if current is None:
            current = Bar(
                symbol=tick.symbol,
                bar_start=bar_start,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
            )
        else:
            current.high = max(current.high, tick.price)
            current.low = min(current.low, tick.price)
            current.close = tick.price
            current.volume += tick.volume

        self._open_bars[tick.symbol] = current
        return closed_bar

    def force_close(self, symbol: str) -> Bar | None:
        """장 마감 등으로 마지막 봉을 강제로 확정하고 싶을 때 사용."""
        return self._open_bars.pop(symbol, None)
