"""여러 전략이 공유하는 작은 도우미들.

전략마다 "직전 봉과 현재 봉을 비교해 교차를 판정한다"거나 "Signal을
만든다"는 코드가 똑같이 반복되므로 여기 모았다."""

from __future__ import annotations

import pandas as pd

from muwon.domain.types import Signal, SignalType


def crossed_above(prev_value: float, cur_value: float, prev_ref: float, cur_ref: float) -> bool:
    """직전엔 기준선 이하였는데 지금은 위로 올라섰는가 (상향 돌파)."""
    if pd.isna(prev_value) or pd.isna(cur_value) or pd.isna(prev_ref) or pd.isna(cur_ref):
        return False
    return prev_value <= prev_ref and cur_value > cur_ref


def crossed_below(prev_value: float, cur_value: float, prev_ref: float, cur_ref: float) -> bool:
    """직전엔 기준선 이상이었는데 지금은 아래로 내려섰는가 (하향 이탈)."""
    if pd.isna(prev_value) or pd.isna(cur_value) or pd.isna(prev_ref) or pd.isna(cur_ref):
        return False
    return prev_value >= prev_ref and cur_value < cur_ref


def make_signal(
    symbol: str,
    row: pd.Series,
    signal_type: SignalType,
    strategy_name: str,
    reason: str,
    score: float = 0.0,
) -> Signal:
    """score는 '이 신호가 얼마나 강한가'다. 클수록 강하다.

    같은 날 매수 신호가 살 수 있는 자리보다 많이 뜨면 엔진이 이 값으로
    줄을 세워 상위만 산다. 종목 수를 18개에서 60개로 늘리면서 필요해졌다:
    그 전에는 신호가 워낙 드물어 "먼저 나온 순서대로" 사도 사실상 티가
    안 났지만, 후보가 많아지면 그 순서가 곧 시가총액 순이라 뒤쪽 종목은
    신호가 떠도 영영 못 사게 된다.

    척도는 전략마다 다르다(거래량 배수, 돌파 폭 %, 과매도 깊이 …).
    한 번의 실행에서는 전략 하나만 돌기 때문에 서로 비교할 일이 없고,
    억지로 정규화하면 오히려 의미가 흐려져서 각자 자연스러운 값을 쓴다."""
    return Signal(
        symbol=symbol,
        trade_date=row["trade_date"],
        signal_type=signal_type,
        strategy_name=strategy_name,
        reason=reason,
        score=score,
    )


def volume_ratio(row: pd.Series) -> float:
    """거래량 / 평균거래량. 관심이 얼마나 몰렸는지를 재는 가장 흔한 확인 지표."""
    if pd.isna(row.get("volume_ma")) or row["volume_ma"] <= 0:
        return 0.0
    return float(row["volume"] / row["volume_ma"])


def pct_above(value: float, reference: float) -> float:
    """기준선을 몇 % 넘어섰는지. 돌파 폭이 클수록 강한 신호로 본다."""
    if pd.isna(value) or pd.isna(reference) or reference <= 0:
        return 0.0
    return float((value / reference - 1) * 100)


def has_nan(row: pd.Series, columns: list[str]) -> bool:
    """지표 계산 초반 구간(윈도우 미충족)은 NaN이라 판정에서 제외해야 한다."""
    return any(pd.isna(row[c]) for c in columns)
