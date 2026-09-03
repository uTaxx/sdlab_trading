"""변동성에 맞춘 청산: ATR 손절과 트레일링 스톱.

왜 필요한가. 고정 -5% 손절은 모든 종목에 같은 자를 들이댄다. 하루에 1%
움직이는 종목에겐 5일치 여유지만, 4% 움직이는 종목에겐 이틀치 잡음이다.
2022년 진단에서 손실 상위 5건이 전부 손절이었고 종목이 삼성SDI·LG화학·
LG에너지솔루션(변동성 큰 2차전지)에 몰려 있었다. 종목을 잘못 고른 게
아니라 손절폭이 종목 성격에 안 맞았을 가능성이 크다는 신호다.

ATR(평균 진폭)은 그 종목이 하루에 보통 얼마나 움직이는지다. 손절폭을
ATR의 배수로 정하면 조용한 종목엔 좁게, 출렁이는 종목엔 넓게 잡힌다.

'진입 이후 최고가'는 저장하지 않고 가격 히스토리에서 다시 구한다. DB에
컬럼을 늘리면 실거래·백테스트 양쪽 상태를 맞춰야 하는데, 어차피 히스토리가
있으니 계산으로 얻는 편이 어긋날 여지가 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from muwon.indicators.technical import add_atr


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: str = ""


def atr_series(price_history: pd.DataFrame, window: int = 14) -> pd.Series:
    """종목별 ATR을 날짜 색인 시계열로. 실행당 한 번만 계산해 재사용한다."""
    if len(price_history) == 0:
        return pd.Series(dtype=float)
    return add_atr(price_history, window=window).set_index("trade_date")["atr"]


def highest_close_since(price_history: pd.DataFrame, entry_date: date, as_of: date) -> float | None:
    """진입일부터 오늘까지의 최고 종가. 트레일링 스톱의 기준점이다."""
    if len(price_history) == 0:
        return None
    window = price_history[
        (price_history["trade_date"] >= entry_date) & (price_history["trade_date"] <= as_of)
    ]
    return float(window["close"].max()) if len(window) else None


def _value_at(series: pd.Series, day: date) -> float | None:
    if series is None or len(series) == 0 or day not in series.index:
        return None
    value = series.loc[day]
    return None if pd.isna(value) or value <= 0 else float(value)


def 보유상한(전략, 정책) -> int | None:
    """며칠까지 들고 있을 것인가. **기준이 0이면 전략이 정한 대로.**

    세 군데가 이 답을 알아야 한다. 실제로 파는 엔진, 전략 화면의 청산 조건
    목록, 매수 알림의 매도전략 줄이다. 각자 계산하면 화면과 매매가 어긋나고,
    어긋난 줄도 모른다."""
    덮개 = int(getattr(정책, "max_holding_days", 0) or 0)
    if 덮개 > 0:
        return 덮개
    return getattr(전략, "max_holding_days", None)


def evaluate_exit(
    *,
    entry_price: float,
    entry_date: date,
    current_price: float,
    as_of: date,
    policy,
    atr: pd.Series | None = None,
    history: pd.DataFrame | None = None,
    익절: float | None = None,
) -> ExitDecision:
    """청산해야 하는가. 손절이 먼저, 그다음 트레일링.

    ATR을 못 구하면(데이터 부족·지표 미계산) 고정 % 손절로 되돌아간다.
    변동성 정보가 없다고 손절 자체가 사라지면 안 된다."""
    if entry_price <= 0:
        return ExitDecision(False)

    atr_at_entry = _value_at(atr, entry_date) if getattr(policy, "atr_stop_enabled", False) else None

    if atr_at_entry is not None:
        stop_price = entry_price - atr_at_entry * policy.atr_stop_multiple
        if current_price <= stop_price:
            loss_pct = (current_price / entry_price - 1) * 100
            return ExitDecision(True, f"ATR 손절 ({policy.atr_stop_multiple:g}ATR, {loss_pct:+.1f}%)")
    elif (current_price / entry_price - 1) <= policy.stop_loss_pct:
        return ExitDecision(True, "손절")

    # 익절은 손절 다음, 트레일링 앞에서 본다. 손절보다 뒤인 이유는 손실을
    # 막는 쪽이 언제나 먼저여야 하기 때문이고, 트레일링보다 앞인 이유는
    # 익절선에 닿았으면 트레일링이 더 기다릴 이유가 없기 때문이다.
    # 부르는 쪽이 익절선을 직접 넘길 수 있다. 종목마다 다를 수 있기
    # 때문이다. 산 전략이 정한 값이 그 종목에 걸린다(2026-09-02).
    take_profit = 익절 if 익절 is not None else (
        getattr(policy, "take_profit_pct", 0.0) or 0.0
    )
    if take_profit > 0 and (current_price / entry_price - 1) >= take_profit:
        gain_pct = (current_price / entry_price - 1) * 100
        return ExitDecision(True, f"익절 ({gain_pct:+.1f}%)")

    if getattr(policy, "trailing_stop_enabled", False) and history is not None:
        peak = highest_close_since(history, entry_date, as_of)
        atr_now = _value_at(atr, as_of)
        if peak is not None and atr_now is not None:
            trail_price = peak - atr_now * policy.trailing_stop_multiple
            # 진입가 아래로 내려가는 트레일링은 손절과 같은 일을 두 번 하는
            # 셈이라, 고점이 진입가보다 위일 때만 의미가 있다.
            if peak > entry_price and current_price <= trail_price:
                gain_pct = (current_price / entry_price - 1) * 100
                return ExitDecision(
                    True,
                    f"트레일링 스톱 (고점 대비 {policy.trailing_stop_multiple:g}ATR, {gain_pct:+.1f}%)",
                )

    return ExitDecision(False)


def 보유만료글(상한: int, 들고있던일: int) -> str:
    """보유 기간이 다 되어 팔 때의 매도 사유.

    ## 왜 실제 보유일을 같이 적나 (2026-09-02)

    전에는 `f"보유 {상한}일 경과 청산"`이었다. 상한만 적고 실제로 며칠
    들고 있었는지는 안 적었다. 둘이 같은 날은 문제가 없지만, **전략을
    바꾸면 상한이 바뀐다.**

    실제로 그랬다. 09-02 아침에 전략을 갭 상승 따라가기로 바꿨고 그 전략의
    보유 상한이 1일이다. 5거래일 들고 있던 종목이 그 규칙에 걸려 팔렸는데
    알림에는 "보유 1일 경과 청산"이라고 나갔다. 받는 사람은 하루 만에
    팔렸다고 읽는다. 같은 메시지의 다른 칸에는 보유기간 5일이 찍혀 있어서
    둘이 어긋났다.

    청산 사유는 매매 기록에도 그대로 남는다. 나중에 "왜 팔렸나"를 되짚을 때
    상한과 실제 보유일이 둘 다 있어야 답이 나온다."""
    if 들고있던일 == 상한:
        return f"보유 상한 {상한}거래일에 닿아 청산"
    return f"보유 상한 {상한}거래일을 넘겨 청산 (실제 {들고있던일}거래일 보유)"


def 익절기준(전략, 정책) -> float:
    """목표 수익률에 닿으면 판다. 0이면 끔.

    ## 보유기간과 같은 방식으로 정한다 (2026-09-02에 더함)

    기초설정에 숫자가 있으면 그것이 이긴다. 0이면 **전략이 정한 대로** 가고,
    전략도 안 정했으면 끈다.

    전에는 기초설정 0이 곧 "끔"이었다. 전략마다 익절선을 다르게 두려면 그
    자리가 필요하다. 지금 등록된 전략 중 익절을 정한 것이 하나도 없어서,
    이 변경만으로는 동작이 달라지지 않는다.

    **`보유상한`과 같은 규칙이라 같은 자리에 둔다.** 청산 기준이 여기저기서
    각자 계산되면 화면과 매매가 어긋나고, 어긋난 줄도 모른다."""
    덮개 = float(getattr(정책, "take_profit_pct", 0.0) or 0.0)
    if 덮개 > 0:
        return 덮개
    return float(getattr(전략, "take_profit_pct", 0.0) or 0.0)
