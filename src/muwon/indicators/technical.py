"""가격 데이터프레임에 전략이 참조하는 기술적 지표를 추가한다.

add_indicators()는 이동평균+RSI 계열 전략이 쓰는 기본 지표만 붙이고,
그 외 지표(MACD·볼린저밴드·스토캐스틱·돈치안채널·ATX/ADX 등)는 각 전략이
필요한 것만 골라 붙이도록 개별 함수로 나눠 뒀다. 18종목 × 여러 가설을
매일 백테스트하는 구조라, 안 쓰는 지표까지 전부 계산하면 그만큼 느려진다.

모든 함수는 원본을 건드리지 않고 새 DataFrame을 돌려주며, 컬럼명은 파라미터
값과 무관하게 고정이다(호출부가 "sma_short" 같은 역할 이름으로 접근한다).
초반 구간(윈도우 미충족)은 NaN이므로 사용하는 쪽에서 걸러야 한다.
"""

import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, ADXIndicator, EMAIndicator, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands, DonchianChannel


def _sorted(price_history: pd.DataFrame) -> pd.DataFrame:
    return price_history.sort_values("trade_date").reset_index(drop=True)


def add_indicators(
    price_history: pd.DataFrame,
    sma_short: int = 20,
    sma_long: int = 60,
    rsi_period: int = 14,
    volume_ma_window: int = 20,
) -> pd.DataFrame:
    """이동평균+RSI+거래량 이동평균. 반환값에 sma_short, sma_long, rsi,
    volume_ma 컬럼이 추가된다(윈도우 값이 컬럼명에 들어가진 않는다).
    윈도우를 파라미터화한 이유는 같은 전략 로직이라도 윈도우만 다른 여러
    가설(예: 단타용 5/20 vs 스윙용 20/60)을 같은 코드로 비교하기 위해서다."""
    df = _sorted(price_history)
    df["sma_short"] = SMAIndicator(df["close"], window=sma_short).sma_indicator()
    df["sma_long"] = SMAIndicator(df["close"], window=sma_long).sma_indicator()
    df["rsi"] = RSIIndicator(df["close"], window=rsi_period).rsi()
    df["volume_ma"] = df["volume"].rolling(window=volume_ma_window).mean()
    return df


def add_ema_pair(price_history: pd.DataFrame, ema_short: int = 12, ema_long: int = 26) -> pd.DataFrame:
    """지수이동평균 두 개: 단순이동평균(SMA)과 달리 최근 가격에 더 큰
    가중치를 줘서 추세 전환에 더 빨리 반응한다."""
    df = _sorted(price_history)
    df["ema_short"] = EMAIndicator(df["close"], window=ema_short).ema_indicator()
    df["ema_long"] = EMAIndicator(df["close"], window=ema_long).ema_indicator()
    return df


def add_macd(
    price_history: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD: 빠른 지수이동평균에서 느린 지수이동평균을 뺀 값(macd)과, 그
    값을 다시 평활한 신호선(macd_signal). macd가 신호선을 위로 뚫으면
    상승 전환, 아래로 뚫으면 하락 전환으로 본다."""
    df = _sorted(price_history)
    indicator = MACD(df["close"], window_fast=fast, window_slow=slow, window_sign=signal)
    df["macd"] = indicator.macd()
    df["macd_signal"] = indicator.macd_signal()
    df["macd_hist"] = indicator.macd_diff()
    return df


def add_bollinger(
    price_history: pd.DataFrame, window: int = 20, num_std: float = 2.0
) -> pd.DataFrame:
    """볼린저밴드: 이동평균(bb_mid)을 중심으로 표준편차 num_std배만큼
    위아래로 띠(bb_upper/bb_lower)를 그린다. 가격이 아래 띠를 벗어나면
    "과하게 떨어졌다", 위 띠를 벗어나면 "과하게 올랐다"고 본다."""
    df = _sorted(price_history)
    indicator = BollingerBands(df["close"], window=window, window_dev=num_std)
    df["bb_upper"] = indicator.bollinger_hband()
    df["bb_mid"] = indicator.bollinger_mavg()
    df["bb_lower"] = indicator.bollinger_lband()
    return df


def add_stochastic(
    price_history: pd.DataFrame, window: int = 14, smooth_window: int = 3
) -> pd.DataFrame:
    """스토캐스틱: 최근 N일 고가~저가 범위에서 현재 종가가 어디쯤인지를
    0~100으로 나타낸다(stoch_k). stoch_d는 그걸 평활한 신호선이다.
    20 이하는 과매도, 80 이상은 과매수로 보는 게 관례다."""
    df = _sorted(price_history)
    indicator = StochasticOscillator(
        high=df["high"], low=df["low"], close=df["close"], window=window, smooth_window=smooth_window
    )
    df["stoch_k"] = indicator.stoch()
    df["stoch_d"] = indicator.stoch_signal()
    return df


def add_donchian(price_history: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """돈치안 채널: 최근 N일의 최고가(dc_upper)와 최저가(dc_lower).
    "N일 신고가 돌파 시 매수"라는 고전적인 추세추종(터틀) 규칙에 쓴다.

    ta 라이브러리의 DonchianChannel은 당일 고가/저가까지 포함해 계산하므로,
    그대로 쓰면 "오늘 신고가를 넘었는지"를 오늘 값으로 판단하게 되어 항상
    참이 된다(미래참조와 같은 효과). 그래서 한 칸 shift해서 "어제까지의
    N일 신고가"를 기준선으로 만든다."""
    df = _sorted(price_history)
    indicator = DonchianChannel(
        high=df["high"], low=df["low"], close=df["close"], window=window
    )
    df["dc_upper"] = indicator.donchian_channel_hband().shift(1)
    df["dc_lower"] = indicator.donchian_channel_lband().shift(1)
    return df


def add_atr(price_history: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """ATR(평균 진폭): 하루에 보통 얼마나 움직이는지를 나타내는 변동성
    지표. "ATR의 2배만큼 떨어지면 청산" 같은 변동성 기반 손절에 쓴다."""
    df = _sorted(price_history)
    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=df["close"], window=window
    ).average_true_range()
    return df


def add_adx(price_history: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """ADX(추세 강도): 방향과 무관하게 "지금 추세가 뚜렷한가"를 0~100으로
    나타낸다. 25 이상이면 추세장, 그 아래면 횡보장으로 보는 게 관례이며,
    추세추종 전략의 진입 필터로 쓴다."""
    df = _sorted(price_history)
    # ta 라이브러리는 봉이 모자라면 IndexError로 터진다. 상장한 지 얼마
    # 안 된 종목이 유니버스에 들어오면 실제로 걸린다. 60종목 5년 비교에서
    # 종목 하나 때문에 실험 전체가 죽었다.
    #
    # 필요한 최소 봉 수는 window가 아니라 **2*window**다. ta가 먼저 window로
    # 한 번 줄이고(rolling+dropna) 그 결과에 다시 window 번째 칸을 쓰기
    # 때문이다. 처음에 window로 막았다가 같은 자리에서 또 터졌다. window
    # 5·10·14·20에 대해 실제로 재 보고 얻은 값이다(tests/test_indicators.py).
    #
    # 지표를 못 구하는 건 정상 상황이므로 빈 값으로 돌려주고, 판단은 쓰는
    # 쪽에 맡긴다(전략들은 이미 NaN을 걸러낸다).
    if len(df) < window * 2:
        df["adx"] = float("nan")
        return df
    df["adx"] = ADXIndicator(
        high=df["high"], low=df["low"], close=df["close"], window=window
    ).adx()
    return df
