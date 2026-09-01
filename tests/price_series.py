"""테스트용 합성 가격 시계열 생성 헬퍼. 실제 데이터가 아니라 전략/엔진
로직을 검증하기 위해 의도적으로 설계된 패턴이다."""

from datetime import date, timedelta

import pandas as pd


def make_price_df(closes: list[float], volumes: list[int] | None = None, start: date = date(2024, 1, 2)) -> pd.DataFrame:
    n = len(closes)
    volumes = volumes or [100_000] * n
    dates = [start + timedelta(days=i) for i in range(n)]
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": volumes,
        }
    )


def _noisy_flat(days: int, base_price: float) -> list[float]:
    """상승/하락이 번갈아 섞인 횡보 구간: RSI 계산식은 하락이 아예 없으면
    100에 붙어버리므로(0/0 처리 관례), 완전한 상수 가격은 현실적인 '횡보'를
    표현하지 못한다. 작은 진동을 섞어 RSI가 중립권에 머물게 한다."""
    prices = []
    price = base_price
    for i in range(days):
        price += 0.4 if i % 2 == 0 else -0.4
        prices.append(price)
    return prices


def flat_then_breakout(
    flat_days: int = 30,
    flat_price: float = 100.0,
    breakout_price: float = 102.0,
    tail_days: int = 5,
    tail_price: float = 103.0,
) -> pd.DataFrame:
    """20일선 상향돌파 + 거래량 급증 시나리오: 횡보 구간 뒤 거래량을 동반한
    급등 하루, 그 뒤로 며칠 더 유지."""
    closes = _noisy_flat(flat_days, flat_price) + [breakout_price] + [tail_price] * tail_days
    volumes = [100_000] * flat_days + [300_000] + [120_000] * tail_days
    return make_price_df(closes, volumes)


def range_bound(days: int = 40, base_price: float = 100.0) -> pd.DataFrame:
    """골든/데드크로스도, RSI 극단값도 만들지 않는 순수 횡보 구간."""
    return make_price_df(_noisy_flat(days, base_price))


def breakout_entry_then_dead_cross_exit(
    flat_days: int = 30,
    flat_price: float = 100.0,
    breakout_price: float = 102.0,
    hold_days: int = 5,
    hold_price: float = 103.0,
    drop_price: float = 99.0,
    tail_days: int = 5,
) -> pd.DataFrame:
    """거래량 급증 골든크로스로 진입한 뒤, 한동안 유지되다 20일선 아래로
    데드크로스하며 청산되는 온전한 왕복 시나리오. 백테스트 엔진의
    진입→청산 전체 흐름을 검증하는 데 쓴다."""
    closes = (
        _noisy_flat(flat_days, flat_price)
        + [breakout_price]
        + [hold_price] * hold_days
        + [drop_price] * (tail_days + 1)
    )
    volumes = (
        [100_000] * flat_days
        + [300_000]
        + [120_000] * hold_days
        + [100_000] * (tail_days + 1)
    )
    return make_price_df(closes, volumes)


def uptrend_then_dead_cross(
    up_days: int = 25,
    start_price: float = 100.0,
    daily_gain: float = 1.0,
    drop_price: float = 95.0,
    tail_days: int = 5,
) -> pd.DataFrame:
    """상승 추세 뒤 20일선 아래로 급락하는 데드크로스 시나리오."""
    closes = [start_price + i * daily_gain for i in range(up_days)]
    closes += [drop_price] * (tail_days + 1)
    return make_price_df(closes)


def uptrend_with_oversold_dip_and_bounce(
    lead_days: int = 65,
    start_price: float = 100.0,
    daily_gain: float = 0.5,
    dip_days: int = 4,
    dip_drop_pct: float = 0.03,
    bounce_days: int = 3,
) -> pd.DataFrame:
    """느린 상승 추세(60일선을 서서히 끌어올림) 중간에 짧고 가파른 조정으로
    RSI를 30 밑까지 떨어뜨렸다가, 추세선(60일선) 위에서 반등하는 시나리오."""
    closes = [start_price + i * daily_gain for i in range(lead_days)]
    price = closes[-1]
    for _ in range(dip_days):
        price *= 1 - dip_drop_pct
        closes.append(price)
    for _ in range(bounce_days):
        price *= 1 + dip_drop_pct * 1.3
        closes.append(price)
    closes += [price * 1.002] * 10
    return make_price_df(closes)


def sharp_uptrend_overbought(days: int = 30, start_price: float = 100.0, daily_gain_pct: float = 0.04) -> pd.DataFrame:
    """며칠 연속 급등으로 RSI를 80 위까지 밀어올리는 과매수 시나리오."""
    closes = [start_price]
    for _ in range(days - 1):
        closes.append(closes[-1] * (1 + daily_gain_pct))
    return make_price_df(closes)
