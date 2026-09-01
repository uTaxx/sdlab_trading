"""보유하는 동안 값이 어떻게 움직였는지.

익절을 넣을지 말지는 스윕이 답한다("얼마에 넣으면 얼마가 된다"). 그런데
스윕은 **왜 그렇게 되는지**를 말해 주지 않는다. 그래서 같이 잰다.

매매 하나마다 세 값을 본다.

- **고점**: 보유 중 종가가 진입가 대비 가장 높았던 지점
- **청산**: 실제로 판 값
- **되돌림**: 고점 − 청산. 얼마를 벌었다가 도로 뱉었는가

되돌림이 크고 고점이 이른 날에 몰려 있으면 익절이 먹힌다. 되돌림이 작거나
고점이 마지막 날이면 익절은 꼬리만 자른다.

**종가만 본다.** 장중 고가(high)를 쓰면 "그 값에 팔 수 있었다"는 가정이
들어가는데, 이 시스템은 일봉 종가로 판단하고 다음 날 아침에 주문을 낸다.
장중 고가는 실제로 잡을 수 없는 값이다. 그걸로 계산하면 익절이 실제보다
좋아 보인다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TradePath:
    symbol: str
    보유일: int
    고점_pct: float
    청산_pct: float
    고점까지_일: int

    @property
    def 되돌림_pct(self) -> float:
        """벌었다가 도로 뱉은 폭. 음수가 될 수 없다(고점 ≥ 청산)."""
        return max(self.고점_pct - self.청산_pct, 0.0)


def trace(trades, histories: dict[str, pd.DataFrame]) -> list[TradePath]:
    """완결된 매매마다 보유 구간의 종가 경로를 되짚는다."""
    paths: list[TradePath] = []
    for trade in trades:
        df = histories.get(trade.symbol)
        if df is None or trade.entry_price <= 0:
            continue
        window = df[
            (df["trade_date"] >= trade.entry_date) & (df["trade_date"] <= trade.exit_date)
        ]
        if len(window) == 0:
            continue
        closes = window["close"].tolist()
        peak_index = max(range(len(closes)), key=lambda i: closes[i])
        paths.append(
            TradePath(
                symbol=trade.symbol,
                보유일=len(closes),
                고점_pct=(closes[peak_index] / trade.entry_price - 1) * 100,
                청산_pct=trade.pnl_pct,
                고점까지_일=peak_index,
            )
        )
    return paths


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def format_paths(paths: list[TradePath], label: str = "") -> str:
    """되돌림 분포를 표로. 익절선을 논하기 전에 봐야 할 숫자다."""
    머리 = f"■ 보유 중 값이 어떻게 움직였나{f' ({label})' if label else ''}"
    if not paths:
        return f"{머리}\n\n완결된 매매가 없습니다."

    이익 = [p for p in paths if p.청산_pct > 0]
    손실 = [p for p in paths if p.청산_pct <= 0]
    lines = [
        머리,
        "  고점 = 보유 중 종가가 가장 높았던 지점(진입가 대비)",
        "  되돌림 = 고점에서 실제 청산가까지 도로 뱉은 폭",
        "  장중 고가가 아니라 종가로 계산합니다. 장중 고가는 실제로 잡을 수 없는 값입니다.",
        "",
        f"완결 매매 {len(paths)}건 (이익 {len(이익)} · 손실 {len(손실)})",
        "",
        f"{'구분':<10}{'건수':>6}{'고점 중앙':>11}{'청산 중앙':>11}{'되돌림 중앙':>13}{'되돌림 평균':>13}",
    ]
    for 이름, 묶음 in (("전체", paths), ("이익 매매", 이익), ("손실 매매", 손실)):
        if not 묶음:
            continue
        lines.append(
            f"{이름:<10}{len(묶음):>6}"
            f"{_median([p.고점_pct for p in 묶음]):>+10.2f}%"
            f"{_median([p.청산_pct for p in 묶음]):>+10.2f}%"
            f"{_median([p.되돌림_pct for p in 묶음]):>+12.2f}%"
            f"{statistics.fmean([p.되돌림_pct for p in 묶음]):>+12.2f}%"
        )

    lines += ["", "고점이 며칠째에 왔나 (0 = 산 날)"]
    분포: dict[int, int] = {}
    for p in paths:
        분포[p.고점까지_일] = 분포.get(p.고점까지_일, 0) + 1
    for day in sorted(분포):
        몫 = 분포[day] / len(paths) * 100
        lines.append(f"  {day}일째 {분포[day]:>4}건 ({몫:>4.1f}%) {'█' * int(몫 / 2)}")

    lines += ["", "고점이 어디까지 갔었나. 익절선 후보별로 몇 %가 닿았나"]
    for 선 in (3, 5, 8, 10, 15, 20, 30):
        닿음 = [p for p in paths if p.고점_pct >= 선]
        if not 닿음:
            continue
        # 닿았던 매매들이 실제로는 얼마에 끝났는지가 핵심이다. 이 값이
        # 익절선보다 낮으면 그 선에서 팔았을 때 이득이었다는 뜻이다.
        lines.append(
            f"  +{선}% 도달 {len(닿음):>4}건 ({len(닿음) / len(paths) * 100:>4.1f}%)"
            f" → 실제 청산 중앙값 {_median([p.청산_pct for p in 닿음]):+.2f}%"
        )

    lines += [
        "",
        "읽는 법: '도달 건수'가 많고 '실제 청산'이 그 선보다 한참 낮으면 익절이 먹힙니다.",
        "반대로 실제 청산이 그 선보다 높으면, 그 선에서 팔았을 때 꼬리를 자른 셈입니다.",
    ]
    return "\n".join(lines)
