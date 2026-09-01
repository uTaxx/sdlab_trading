"""웹소켓 연결이 끊겨도 RealtimeTradingEngine의 봉 히스토리(메모리)를
유지한 채 재연결하는 상시 실행 루프.

'틱 하나 처리 실패'와 '연결 자체가 끊김'은 다른 문제라 따로 다룬다. 틱
처리 실패는 그 틱만 버리고 계속 진행하고(엔진이 이미 그렇게 함), 연결이
끊기면 지수 백오프로 재연결한다. 장중 몇 시간을 붙잡고 있어야 하는
웹소켓이라 네트워크 순단은 예외가 아니라 전제로 설계해야 한다.

engine 객체 자체는 재연결 때마다 새로 안 만든다. 그래야 진행 중이던
분봉 집계(BarAggregator)와 최근 봉 히스토리(sma60 계산에 필요한 60개
분량)가 재연결 후에도 이어진다. 새로 만들면 재연결마다 지표가 다시
채워질 때까지(약 20분) 신호를 못 낸다."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from loguru import logger

from muwon.data.tick_aggregator import Tick
from muwon.execution.realtime_engine import RealtimeTradingEngine

INITIAL_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 60


async def run_forever(
    engine: RealtimeTradingEngine,
    stream_factory: Callable[[], AsyncIterator[Tick]],
    sleep=asyncio.sleep,
) -> None:
    """stream_factory()를 호출할 때마다 새 틱 스트림(=새 웹소켓 연결)을
    받는다. 스트림이 끊기거나 예외로 끝나면 백오프 후 stream_factory()를
    다시 호출해 재연결한다. 틱을 하나라도 받은 뒤 끊겼으면 정상적으로
    돌다가 끊긴 것이므로 백오프를 초기값으로 되돌린다."""
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        received_any = False
        try:
            async for tick in stream_factory():
                received_any = True
                backoff = INITIAL_BACKOFF_SECONDS
                try:
                    engine.on_tick(tick)
                except Exception:  # noqa: BLE001 (틱 하나 처리 실패로 전체 루프가 죽으면 안 됨)
                    logger.exception(f"틱 처리 중 오류 (symbol={tick.symbol}): 다음 틱은 계속 처리")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 (연결 오류의 종류를 예단하지 않고 전부 재연결 대상으로 처리)
            logger.exception("웹소켓 연결 오류")

        logger.warning(f"웹소켓 스트림 종료됨: {backoff}초 후 재연결")
        await sleep(backoff)
        if not received_any:
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
