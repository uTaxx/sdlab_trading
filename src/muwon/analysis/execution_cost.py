"""결정한 가격과 실제로 체결된 가격의 차이를 잰다.

백테스트는 "신호가 난 날 종가에 원하는 만큼 체결됐다"고 가정한다. 실거래는
그렇게 돌아가지 않는다.

1. 신호는 **어제 종가**로 계산된다(장중 미완성 봉을 쓰면 미래참조가 된다)
2. 주문은 **오늘 아침 09:05**에 나간다
3. 체결가는 그 시점 호가에 달렸다

그래서 '결정가 대비 체결가'에는 두 가지가 섞여 있다. 밤 사이 갭과 호가
스프레드. 백테스트가 무시하는 것은 이 둘의 합이고, 그래서 여기서도 둘을
합쳐서 잰다. 나누려면 시가 데이터가 더 필요한데, 지금 필요한 답('백테스트
가정이 얼마나 낙관적인가')에는 합계로 충분하다.

**확인된 체결만 센다.** 체결 조회가 실패하면 기준가를 그대로 기록하는데,
그 행을 포함하면 차이가 0인 표본이 섞여 실제보다 작게 나온다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import select

from muwon.db.models import OrderRow


@dataclass(frozen=True)
class CostSample:
    symbol: str
    side: str
    reference_price: float
    fill_price: float

    @property
    def cost_pct(self) -> float:
        """불리한 쪽을 양수로 잡는다.

        사는 쪽은 비싸게 사면 손해, 파는 쪽은 싸게 팔면 손해다. 부호를
        맞춰 두지 않으면 매수·매도가 서로 상쇄돼 '비용 0'처럼 보인다."""
        if self.reference_price <= 0:
            return 0.0
        raw = (self.fill_price / self.reference_price - 1) * 100
        return raw if self.side == "BUY" else -raw


@dataclass(frozen=True)
class CostReport:
    samples: list[CostSample]
    skipped_unconfirmed: int
    skipped_no_reference: int

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def median_pct(self) -> float:
        """중앙값을 대표값으로 쓴다. 갭 한 번에 평균이 끌려가지 않게."""
        return statistics.median(s.cost_pct for s in self.samples) if self.samples else 0.0

    @property
    def mean_pct(self) -> float:
        return statistics.fmean(s.cost_pct for s in self.samples) if self.samples else 0.0

    def by_side(self, side: str) -> list[CostSample]:
        return [s for s in self.samples if s.side == side]

    def suggested_slippage_pct(self) -> float:
        """백테스트에 넣을 값 후보. 편도 기준이라 그대로 slippage_pct에 쓴다.

        표본이 적으면 쓰지 말아야 한다. 판단은 부르는 쪽에 맡기고 여기서는
        숫자만 낸다."""
        return max(self.median_pct, 0.0) / 100


def collect(session_factory) -> CostReport:
    """주문 기록에서 결정가·체결가 쌍을 모은다."""
    with session_factory() as session:
        rows = session.scalars(select(OrderRow).order_by(OrderRow.created_at)).all()

    samples, unconfirmed, no_reference = [], 0, 0
    for row in rows:
        if not row.fill_confirmed:
            unconfirmed += 1
            continue
        if not row.reference_price:
            no_reference += 1
            continue
        samples.append(
            CostSample(
                symbol=row.symbol,
                side=row.side,
                reference_price=float(row.reference_price),
                fill_price=float(row.price),
            )
        )
    return CostReport(samples, unconfirmed, no_reference)


def format_report(report: CostReport) -> str:
    lines = [
        "■ 결정가 대비 체결가. 백테스트가 무시하는 비용",
        "  신호는 어제 종가로 내고 주문은 오늘 아침에 나간다.",
        "  그 사이의 갭과 호가 스프레드가 여기 함께 잡힌다.",
        "",
        (
            f"확인된 체결 {report.count}건 (체결 미확인 "
            f"{report.skipped_unconfirmed}건, 기준가 없음 "
            f"{report.skipped_no_reference}건 제외)"
        ),
    ]
    if not report.count:
        lines += [
            "",
            "아직 잴 수 있는 표본이 없습니다.",
            "모의투자 주문이 쌓여야 실제 값을 알 수 있습니다. 그때까지",
            "백테스트의 슬리피지는 추정값으로만 쓰세요.",
        ]
        return "\n".join(lines)

    lines += [
        f"중앙값 {report.median_pct:+.3f}%   평균 {report.mean_pct:+.3f}%",
        "",
        f"{'종목':<10}{'구분':<6}{'결정가':>12}{'체결가':>12}{'차이':>9}",
    ]
    for s in report.samples[-20:]:
        lines.append(
            f"{s.symbol:<10}{s.side:<6}{s.reference_price:>12,.0f}"
            f"{s.fill_price:>12,.0f}{s.cost_pct:>+8.3f}%"
        )
    for side in ("BUY", "SELL"):
        subset = report.by_side(side)
        if subset:
            median = statistics.median(x.cost_pct for x in subset)
            lines.append(f"  {side} {len(subset)}건 중앙값 {median:+.3f}%")
    lines += [
        "",
        f"백테스트에 넣어 볼 값: --values {report.suggested_slippage_pct() * 100:.3f}",
    ]
    if report.count < 20:
        lines.append(
            "다만 표본이 20건 미만이라 이 값을 믿기엔 이릅니다. 방향만 참고하세요."
        )
    return "\n".join(lines)
