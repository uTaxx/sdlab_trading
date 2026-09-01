"""장중에 손절을 걸었다면 달라졌을까. 일봉의 저가로 재 본다.

지금 엔진은 하루에 한 번, **종가**로만 손절을 검사한다. 그래서 장중에
손절선을 크게 밑돌았다가 회복해 마감하면 팔지 않고, 반대로 밤사이 갭으로
크게 빠지면 손절선보다 훨씬 아래에서 판다.

장중 손절(스톱 주문)을 걸면 달라질까? 실시간 체결 데이터가 없어도 **일봉의
저가**로 근사할 수 있다. 그날 저가가 손절선을 밑돌았다면, 장중 스톱 주문은
그날 발동했을 것이다.

## 이 근사가 낙관적인 지점: 반드시 같이 읽어야 한다

1. **저가를 스쳤다 ≠ 그 가격에 팔렸다.** 스톱 주문은 손절선을 건드리면
   시장가로 나가므로 보통 그보다 더 아래에서 체결된다. 여기서는 손절선
   그대로 팔렸다고 계산하므로 **실제보다 좋게 나온다.**
2. **갭 하락은 스톱도 못 막는다.** 시가가 이미 손절선 아래면 스톱은 시가에
   발동한다. 여기서도 그렇게 계산한다(시가와 손절선 중 낮은 쪽).
3. 하루에 여러 종목이 동시에 걸리는 상황, 자금 재투입 효과는 반영하지
   않는다. 매매 하나하나를 따로 비교할 뿐이다.

그래서 이 분석의 결론은 **"장중 손절이 이만큼 좋다"가 아니라 "이보다
좋을 수는 없다"** 로 읽어야 한다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class StopComparison:
    symbol: str
    지금손익: float  # 실제로 청산된 손익률
    장중손익: float  # 장중 손절을 걸었다면의 손익률
    장중발동: bool  # 장중 손절이 실제로 걸렸나
    며칠빨리: int  # 몇 거래일 일찍 나왔나 (0이면 같은 날)

    @property
    def 차이(self) -> float:
        return self.장중손익 - self.지금손익


def compare(trades, histories: dict[str, pd.DataFrame], stop_loss_pct: float = -0.05):
    """완결된 매매마다 '장중 손절이 있었다면'을 계산한다."""
    결과: list[StopComparison] = []
    for trade in trades:
        df = histories.get(trade.symbol)
        if df is None or len(df) == 0 or trade.entry_price <= 0:
            continue
        구간 = df[
            (df["trade_date"] > trade.entry_date) & (df["trade_date"] <= trade.exit_date)
        ].sort_values("trade_date")
        if len(구간) == 0:
            continue

        손절선 = trade.entry_price * (1 + stop_loss_pct)
        장중손익, 발동, 빨리 = trade.pnl_pct, False, 0
        for 순번, (_, 봉) in enumerate(구간.iterrows()):
            if float(봉["low"]) > 손절선:
                continue
            # 시가가 이미 손절선 아래면 스톱은 시가에 발동한다. 갭은
            # 스톱으로도 못 막는다. 둘 중 낮은 쪽이 실제 체결가에 가깝다.
            체결 = min(손절선, float(봉["open"]))
            장중손익 = (체결 / trade.entry_price - 1) * 100
            발동 = True
            빨리 = len(구간) - 1 - 순번
            break

        결과.append(
            StopComparison(
                symbol=trade.symbol,
                지금손익=trade.pnl_pct,
                장중손익=장중손익,
                장중발동=발동,
                며칠빨리=빨리,
            )
        )
    return 결과


def format_comparison(결과: list[StopComparison], label: str = "") -> str:
    머리 = f"■ 장중 손절을 걸었다면{f' ({label})' if label else ''}"
    if not 결과:
        return f"{머리}\n\n완결된 매매가 없습니다."

    발동 = [c for c in 결과 if c.장중발동]
    lines = [
        머리,
        "  지금은 하루 한 번 **종가**로만 손절을 봅니다. 장중에 크게 빠졌다가",
        "  회복해 마감하면 팔지 않고, 밤사이 갭이 나면 손절선보다 한참 아래에서 팝니다.",
        "",
        "  ⚠ 이 계산은 낙관적입니다. 손절선에 정확히 팔렸다고 봅니다. 실제 스톱",
        "  주문은 그보다 아래에서 체결됩니다. '이만큼 좋다'가 아니라 '이보다",
        "  좋을 수는 없다'로 읽으세요.",
        "",
        (f"완결 매매 {len(결과)}건 중 장중 손절이 걸렸을 매매 {len(발동)}건 "
        f"({len(발동) / len(결과) * 100:.1f}%)"),
    ]

    if not 발동:
        lines.append("\n장중 손절이 걸릴 매매가 없었습니다. 지금 구조로 충분합니다.")
        return "\n".join(lines)

    지금합 = sum(c.지금손익 for c in 결과)
    장중합 = sum(c.장중손익 for c in 결과)
    lines += [
        "",
        f"{'':<16}{'지금(종가 손절)':>16}{'장중 손절':>14}{'차이':>12}",
        (f"{'매매당 평균':<16}{지금합 / len(결과):>15.2f}%{장중합 / len(결과):>13.2f}%"
        f"{(장중합 - 지금합) / len(결과):>+11.2f}%p"),
    ]

    좋아진것 = [c for c in 발동 if c.차이 > 0.01]
    나빠진것 = [c for c in 발동 if c.차이 < -0.01]
    lines += [
        "",
        f"장중 손절로 **좋아진** 매매 {len(좋아진것)}건 "
        f"(평균 {statistics.fmean([c.차이 for c in 좋아진것]):+.2f}%p)"
        if 좋아진것
        else "장중 손절로 좋아진 매매 없음",
        f"장중 손절로 **나빠진** 매매 {len(나빠진것)}건 "
        f"(평균 {statistics.fmean([c.차이 for c in 나빠진것]):+.2f}%p)"
        if 나빠진것
        else "장중 손절로 나빠진 매매 없음",
        "",
        "나빠지는 이유: 장중에 잠깐 손절선을 스쳤다가 회복해 마감한 매매를",
        "그 자리에서 팔아 버리기 때문입니다. 잡음에 손절당하는 것입니다.",
    ]
    if 발동:
        lines.append(
            f"\n장중 손절이 걸린 매매는 지금보다 평균 "
            f"{statistics.fmean([c.며칠빨리 for c in 발동]):.1f}거래일 일찍 나왔습니다."
        )
    return "\n".join(lines)
