"""run_forever가 (1) 스트림이 끊겨도 engine을 재사용해 봉 히스토리를
보존하고, (2) 연속 실패 시 백오프가 커지고, (3) 정상 수신 뒤 끊기면
백오프가 초기화되는지 검증한다. 실제 sleep 대신 즉시 반환하는 가짜
sleep을 넣어 테스트를 빠르게 유지한다."""

import asyncio

import pytest

from muwon.data.tick_aggregator import Tick
from muwon.execution.realtime_runner import INITIAL_BACKOFF_SECONDS, run_forever


class FakeEngine:
    def __init__(self):
        self.ticks: list[Tick] = []

    def on_tick(self, tick: Tick) -> None:
        self.ticks.append(tick)


def make_tick(symbol="005930", price=100.0):
    from datetime import UTC, datetime

    return Tick(symbol=symbol, price=price, volume=1, timestamp=datetime.now(UTC))


async def test_reconnects_after_stream_ends_and_preserves_engine_state():
    call_count = 0
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def stream_factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield make_tick(price=100.0)
            return
        if call_count == 2:
            yield make_tick(price=101.0)
            raise asyncio.CancelledError  # 두 번째 연결 뒤 루프 종료

    engine = FakeEngine()
    with pytest.raises(asyncio.CancelledError):
        await run_forever(engine, stream_factory, sleep=fake_sleep)

    assert call_count == 2
    assert len(engine.ticks) == 2  # 재연결돼도 같은 engine이 계속 틱을 누적
    assert sleeps == [INITIAL_BACKOFF_SECONDS]  # 틱을 받은 뒤 끊겼으므로 백오프 초기값


async def test_backoff_grows_on_consecutive_empty_failures():
    call_count = 0
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    async def stream_factory():
        nonlocal call_count
        call_count += 1
        raise ConnectionError("연결 실패")
        yield  # pragma: no cover: async generator 문법상 필요

    engine = FakeEngine()
    with pytest.raises(asyncio.CancelledError):
        await run_forever(engine, stream_factory, sleep=fake_sleep)

    assert sleeps[0] == INITIAL_BACKOFF_SECONDS
    assert sleeps[1] == INITIAL_BACKOFF_SECONDS * 2
    assert sleeps[2] == INITIAL_BACKOFF_SECONDS * 4


async def test_tick_processing_error_does_not_stop_stream():
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    async def stream_factory():
        yield make_tick(symbol="BAD")
        yield make_tick(symbol="GOOD")

    class RaisingOnceEngine:
        def __init__(self):
            self.processed: list[str] = []

        def on_tick(self, tick: Tick) -> None:
            if tick.symbol == "BAD":
                raise ValueError("틱 처리 실패")
            self.processed.append(tick.symbol)

    engine = RaisingOnceEngine()
    with pytest.raises(asyncio.CancelledError):
        await run_forever(engine, stream_factory, sleep=fake_sleep)

    assert engine.processed == ["GOOD"]  # BAD는 실패했지만 GOOD은 계속 처리됨
