"""매매 결과·설정·검증 결과를 한 덩어리 텍스트로 모아 주는 분석 리포트.

용도가 분명하다: **이 텍스트를 통째로 복사해 Claude에게 붙여넣으면 전략
진단을 받을 수 있게** 하는 것. 그래서 두 가지를 지킨다.

1. 자기완결성: 리포트만 보고도 판단할 수 있어야 한다. "승률 33%"만 던지면
   그게 좋은지 나쁜지 알 수 없으므로, 어떤 전략을 어떤 리스크 설정으로
   실행했고 청산 사유가 무엇이었는지까지 같이 넣는다.
2. 압축: 텔레그램은 한 메시지에 4096자 제한이 있고, 사람이 눈으로도 읽을
   수 있어야 한다. 원본 데이터를 다 붙이지 않고 집계와 최근 사례만 담는다.

LLM API를 직접 호출하지 않는 이유: 키 관리·비용·장애 요소가 늘어나는 데
비해, 판단은 어차피 사람이 최종 확인해야 하는 영역이라 얻는 게 적다.
사람이 붙여넣는 한 단계를 두면 그 확인이 자연스럽게 들어간다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from muwon.db.models import BacktestRunRow, PositionRow, TradeRow
from muwon.settings.schema import RiskPolicy

TELEGRAM_LIMIT = 4000  # 실제 제한은 4096이고, 헤더와 여유분을 뺀 값이다


@dataclass(frozen=True)
class TradeStats:
    count: int
    wins: int
    losses: int
    total_pnl: float
    avg_win_pct: float
    avg_loss_pct: float
    avg_holding_days: float

    @property
    def win_rate_pct(self) -> float:
        return self.wins / self.count * 100 if self.count else 0.0

    @property
    def profit_factor(self) -> float:
        """이긴 거래의 평균 이익 ÷ 진 거래의 평균 손실.

        승률만 보면 오해한다. 승률 33%라도 이길 때 3배로 벌면 본전 이상이다.
        이 값이 1보다 크면 "적게 맞아도 크게 먹는" 구조라는 뜻."""
        if self.avg_loss_pct == 0:
            return 0.0
        return abs(self.avg_win_pct / self.avg_loss_pct)


def summarize_trades(trades: list[TradeRow]) -> TradeStats:
    if not trades:
        return TradeStats(0, 0, 0, 0.0, 0.0, 0.0, 0.0)

    wins = [t for t in trades if t.pnl_amount > 0]
    losses = [t for t in trades if t.pnl_amount <= 0]
    holding_days = [
        max((t.exited_at - t.entered_at).total_seconds() / 86400, 0.0)
        for t in trades
        if t.exited_at and t.entered_at
    ]
    return TradeStats(
        count=len(trades),
        wins=len(wins),
        losses=len(losses),
        total_pnl=sum(t.pnl_amount for t in trades),
        avg_win_pct=statistics.fmean([t.pnl_pct for t in wins]) if wins else 0.0,
        avg_loss_pct=statistics.fmean([t.pnl_pct for t in losses]) if losses else 0.0,
        avg_holding_days=statistics.fmean(holding_days) if holding_days else 0.0,
    )


def group_by(trades: list[TradeRow], key_fn) -> dict[str, list[TradeRow]]:
    grouped: dict[str, list[TradeRow]] = {}
    for trade in trades:
        grouped.setdefault(key_fn(trade) or "(없음)", []).append(trade)
    return grouped


def _section_settings(strategy_key: str, policy: RiskPolicy, universe_size: int) -> list[str]:
    return [
        "■ 현재 설정",
        f"활성 전략: {strategy_key}",
        (
            f"리스크: 종목당 {policy.max_position_weight:.0%}, 손절 {policy.stop_loss_pct:.0%}, "
            f"일일한도 {policy.daily_loss_limit_pct:.0%}, 최대 {policy.max_concurrent_positions}종목"
        ),
        f"자동매매: {'ON' if policy.trading_enabled else 'OFF'}",
        f"매매 대상: {universe_size}종목",
    ]


def _section_performance(trades: list[TradeRow], days: int) -> list[str]:
    stats = summarize_trades(trades)
    if stats.count == 0:
        return [
            "",
            f"■ 실전 성과 (최근 {days}일)",
            "청산 완료된 매매가 아직 없습니다.",
        ]

    return [
        "",
        f"■ 실전 성과 (최근 {days}일)",
        f"총 매매: {stats.count}건 (승 {stats.wins} / 패 {stats.losses}) 승률 {stats.win_rate_pct:.1f}%",
        f"누적 손익: {stats.total_pnl:+,.0f}원",
        (
            f"평균 이익 {stats.avg_win_pct:+.2f}% / 평균 손실 {stats.avg_loss_pct:+.2f}% "
            f"(손익비 {stats.profit_factor:.2f})"
        ),
        f"평균 보유: {stats.avg_holding_days:.1f}일",
    ]


def _section_by_group(trades: list[TradeRow], title: str, key_fn) -> list[str]:
    if not trades:
        return []
    lines = ["", title]
    grouped = group_by(trades, key_fn)
    for name, group in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        stats = summarize_trades(group)
        avg_pct = statistics.fmean([t.pnl_pct for t in group])
        lines.append(
            f"{name}: {stats.count}건, 승률 {stats.win_rate_pct:.0f}%, "
            f"평균 {avg_pct:+.2f}%, 합계 {stats.total_pnl:+,.0f}원"
        )
    return lines


def _section_recent_trades(trades: list[TradeRow], limit: int) -> list[str]:
    if not trades:
        return []
    lines = ["", f"■ 최근 매매 (최대 {limit}건)"]
    for trade in sorted(trades, key=lambda t: t.exited_at, reverse=True)[:limit]:
        lines.append(
            f"{trade.exited_at:%m-%d} {trade.symbol} {trade.entry_price:,.0f}→{trade.exit_price:,.0f} "
            f"({trade.pnl_pct:+.2f}%) [{trade.entry_reason} → {trade.exit_reason}]"
        )
    return lines


def _section_positions(positions: list[PositionRow]) -> list[str]:
    if not positions:
        return ["", "■ 보유 중: 없음"]
    lines = ["", f"■ 보유 중 ({len(positions)}종목)"]
    for p in positions:
        lines.append(
            f"{p.symbol} {p.quantity}주 @{p.entry_price:,.0f} ({p.entry_date}) [{p.entry_reason}]"
        )
    return lines


def _section_robustness(runs: list[BacktestRunRow]) -> list[str]:
    """다기간 검증 결과: 전략별로 구간별 수익률을 한 줄에 모은다."""
    if not runs:
        return []
    by_strategy: dict[str, list[BacktestRunRow]] = {}
    for run in runs:
        by_strategy.setdefault(run.strategy_key, []).append(run)

    lines = ["", "■ 다기간 검증 (구간별 수익률, 과최적화 확인용)"]
    scored = []
    for key, rows in by_strategy.items():
        rows.sort(key=lambda r: r.period_start)
        returns = [r.total_return_pct for r in rows]
        label = " / ".join(
            f"{r.notes.split(':', 1)[-1]} {r.total_return_pct:+.1f}" for r in rows
        )
        scored.append((min(returns), f"{key}: {label} (최악 {min(returns):+.1f}%)"))

    for _worst, line in sorted(scored, reverse=True):
        lines.append(line)
    return lines


def _section_recent_review(runs: list[BacktestRunRow], active_key: str) -> list[str]:
    """최근 일일 리뷰: 지금 전략이 다른 전략 대비 어디쯤인지."""
    if not runs:
        return []
    ranked = sorted(runs, key=lambda r: r.total_return_pct, reverse=True)
    active = next((r for r in ranked if r.strategy_key == active_key), None)
    rank = ranked.index(active) + 1 if active else None

    lines = ["", f"■ 최근 리뷰 ({ranked[0].period_start} ~ {ranked[0].period_end})"]
    if active is not None:
        lines.append(
            f"활성 전략 순위: {rank}/{len(ranked)}위 ({active.total_return_pct:+.2f}%, "
            f"{active.num_trades}건)"
        )
    lines.append("상위 5개: " + ", ".join(
        f"{r.strategy_key} {r.total_return_pct:+.1f}%" for r in ranked[:5]
    ))
    return lines


def build_analysis_report(
    session_factory,
    strategy_key: str,
    policy: RiskPolicy,
    universe_size: int,
    days: int = 30,
    recent_trade_limit: int = 15,
    account_summary: list[str] | None = None,
) -> str:
    """진단에 필요한 모든 것을 한 덩어리 텍스트로 만든다."""
    since = datetime.utcnow() - timedelta(days=days)  # noqa: DTZ003 (기록 시각과 같은 기준)
    with session_factory() as session:
        trades = list(
            session.scalars(
                select(TradeRow).where(TradeRow.exited_at >= since).order_by(TradeRow.exited_at)
            ).all()
        )
        positions = list(session.scalars(select(PositionRow)).all())
        robustness_runs = list(
            session.scalars(
                select(BacktestRunRow).where(BacktestRunRow.notes.like("robustness:%"))
            ).all()
        )
        review_runs = list(
            session.scalars(
                select(BacktestRunRow)
                .where(BacktestRunRow.notes == "daily_review")
                .order_by(BacktestRunRow.created_at.desc())
                .limit(30)
            ).all()
        )

    latest_review = _latest_batch(review_runs)

    lines = [
        f"📊 muwon406 분석 리포트 ({datetime.now():%Y-%m-%d %H:%M})",  # noqa: DTZ005 (표시용)
        "이 내용을 Claude에게 그대로 붙여넣으면 전략 진단을 받을 수 있습니다.",
        "",
    ]
    lines += _section_settings(strategy_key, policy, universe_size)
    if account_summary:
        lines += ["", "■ 계좌 대조", *account_summary]
    lines += _section_performance(trades, days)
    lines += _section_by_group(trades, "■ 전략별 성과", lambda t: t.strategy_key)
    lines += _section_by_group(trades, "■ 청산 사유별", lambda t: t.exit_reason)
    lines += _section_by_group(trades, "■ 진입 사유별", lambda t: t.entry_reason)
    lines += _section_recent_trades(trades, recent_trade_limit)
    lines += _section_positions(positions)
    lines += _section_recent_review(latest_review, strategy_key)
    lines += _section_robustness(robustness_runs)

    return "\n".join(lines)


def _latest_batch(runs: list[BacktestRunRow]) -> list[BacktestRunRow]:
    """같은 기준일의 리뷰 결과만 남긴다(여러 날치가 섞이면 비교가 안 된다)."""
    if not runs:
        return []
    latest_end = max(r.period_end for r in runs)
    seen: set[str] = set()
    batch = []
    for run in runs:
        if run.period_end != latest_end or run.strategy_key in seen:
            continue
        seen.add(run.strategy_key)
        batch.append(run)
    return batch


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """텔레그램 길이 제한에 맞춰 자른다.

    줄 중간에서 자르면 표가 깨져 읽기 어려우므로 줄 단위로만 나눈다.
    한 줄이 제한보다 길면 어쩔 수 없이 그 줄만 강제로 자른다."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            chunks.append(line[:limit])
            line = line[limit:]

        # +1은 줄바꿈 문자
        if current and current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1

    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]
