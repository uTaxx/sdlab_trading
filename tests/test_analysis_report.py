"""Claude에 붙여넣을 분석 리포트 생성 검증.

이 리포트는 "그것만 보고 판단할 수 있어야" 쓸모가 있다. 그래서 집계가
맞는지뿐 아니라, 판단에 필요한 맥락(설정·청산 사유·검증 결과)이 빠지지
않는지도 확인한다."""

from datetime import date, datetime, timedelta

from muwon.analysis.report import (
    TELEGRAM_LIMIT,
    build_analysis_report,
    split_for_telegram,
    summarize_trades,
)
from muwon.db.models import BacktestRunRow, PositionRow, TradeRow
from muwon.db.session import make_session_factory
from muwon.settings.schema import RiskPolicy


def make_trade(
    pnl_pct: float,
    strategy_key: str = "ma_rsi_v1",
    exit_reason: str = "단기선 하향이탈",
    days_ago: int = 1,
    holding_days: int = 3,
    symbol: str = "005930",
) -> TradeRow:
    exited = datetime.utcnow() - timedelta(days=days_ago)  # noqa: DTZ003 (저장 기준과 동일)
    entry_price = 70_000.0
    return TradeRow(
        symbol=symbol,
        strategy_key=strategy_key,
        quantity=10,
        entry_price=entry_price,
        exit_price=entry_price * (1 + pnl_pct / 100),
        entry_reason="단기선 상향돌파 + 거래량 급증",
        exit_reason=exit_reason,
        pnl_amount=entry_price * (pnl_pct / 100) * 10,
        pnl_pct=pnl_pct,
        is_paper=True,
        entered_at=exited - timedelta(days=holding_days),
        exited_at=exited,
    )


def seed(session_factory, rows) -> None:
    with session_factory() as session:
        for row in rows:
            session.add(row)
        session.commit()


def build(session_factory, **kwargs) -> str:
    defaults = {
        "strategy_key": "ma_rsi_v1",
        "policy": RiskPolicy(),
        "universe_size": 18,
    }
    return build_analysis_report(session_factory, **{**defaults, **kwargs})


def test_summarize_computes_win_rate_and_profit_factor():
    """승률만 보면 오해한다. 승률 33%라도 이길 때 크게 벌면 본전 이상이다.
    손익비를 같이 계산해 그 구분이 되게 한다."""
    trades = [make_trade(9.0), make_trade(-3.0), make_trade(-3.0)]

    stats = summarize_trades(trades)

    assert stats.count == 3
    assert round(stats.win_rate_pct, 1) == 33.3
    assert stats.avg_win_pct == 9.0
    assert stats.avg_loss_pct == -3.0
    assert stats.profit_factor == 3.0  # 이길 때 3배로 번다


def test_summarize_handles_no_trades():
    stats = summarize_trades([])
    assert stats.count == 0
    assert stats.win_rate_pct == 0.0
    assert stats.profit_factor == 0.0


def test_summarize_computes_average_holding_days():
    stats = summarize_trades([make_trade(1.0, holding_days=2), make_trade(1.0, holding_days=6)])
    assert round(stats.avg_holding_days, 1) == 4.0


def test_report_includes_settings_context():
    """숫자만 있으면 좋은지 나쁜지 판단할 수 없다. 어떤 설정으로 돌린
    결과인지가 함께 있어야 진단이 가능하다."""
    session_factory = make_session_factory("sqlite:///:memory:")

    report = build(
        session_factory,
        strategy_key="donchian_20_10",
        policy=RiskPolicy(max_position_weight=0.2, stop_loss_pct=-0.07, trading_enabled=False),
        universe_size=30,
    )

    assert "donchian_20_10" in report
    assert "20%" in report  # 종목당 비중
    assert "-7%" in report  # 손절
    assert "자동매매: OFF" in report
    assert "30종목" in report


def test_report_aggregates_by_strategy_and_exit_reason():
    """어떤 전략이, 어떤 청산 사유로 잃고 있는지가 진단의 출발점이다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(
        session_factory,
        [
            make_trade(-5.2, exit_reason="손절"),
            make_trade(-5.4, exit_reason="손절"),
            make_trade(4.1, exit_reason="RSI 과매수"),
            make_trade(-1.8, strategy_key="rsi2_pullback", exit_reason="단기선 하향이탈"),
        ],
    )

    report = build(session_factory)

    assert "■ 전략별 성과" in report
    assert "ma_rsi_v1" in report and "rsi2_pullback" in report
    assert "■ 청산 사유별" in report
    assert "손절: 2건" in report
    assert "RSI 과매수: 1건" in report


def test_report_lists_recent_trades_with_reasons():
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(session_factory, [make_trade(-2.0, symbol="000660")])

    report = build(session_factory, recent_trade_limit=5)

    assert "■ 최근 매매" in report
    assert "000660" in report
    assert "단기선 상향돌파 + 거래량 급증 → 단기선 하향이탈" in report


def test_report_excludes_trades_outside_window():
    """기간을 좁혀 보고 싶을 때 옛 매매가 섞이면 집계가 흐려진다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(session_factory, [make_trade(5.0, days_ago=2), make_trade(-9.9, days_ago=90)])

    report = build(session_factory, days=30)

    assert "총 매매: 1건" in report
    assert "-9.90%" not in report


def test_report_reports_no_trades_gracefully():
    session_factory = make_session_factory("sqlite:///:memory:")
    report = build(session_factory)
    assert "청산 완료된 매매가 아직 없습니다" in report


def test_report_includes_open_positions():
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(
        session_factory,
        [
            PositionRow(
                symbol="005930",
                quantity=10,
                entry_price=70_000.0,
                entry_date=date(2026, 8, 17),
                entered_at=datetime(2026, 8, 17, 9, 30),  # noqa: DTZ001 (테스트용)
                entry_reason="RSI 과매도 반등",
                strategy_key="ma_rsi_v1",
            )
        ],
    )

    report = build(session_factory)

    assert "■ 보유 중 (1종목)" in report
    assert "005930 10주" in report


def test_report_includes_robustness_with_worst_period():
    """다기간 검증 결과가 있어야 '지금 성적이 나쁜 게 전략 탓인지 시기
    탓인지'를 판단할 수 있다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(
        session_factory,
        [
            BacktestRunRow(
                strategy_key="donchian_20_10",
                params_json="{}",
                period_start=date(y, 1, 1),
                period_end=date(y, 12, 31),
                total_return_pct=r,
                max_drawdown_pct=-10.0,
                win_rate_pct=40.0,
                num_trades=50,
                notes=f"robustness:{y}",
            )
            for y, r in [(2023, 28.1), (2024, -7.8)]
        ],
    )

    report = build(session_factory)

    assert "■ 다기간 검증" in report
    assert "donchian_20_10" in report
    assert "최악 -7.8%" in report


def test_report_shows_active_strategy_rank_in_recent_review():
    """지금 쓰는 전략이 다른 전략 대비 어디쯤인지가 바로 보여야 한다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    seed(
        session_factory,
        [
            BacktestRunRow(
                strategy_key=key,
                params_json="{}",
                period_start=date(2026, 5, 19),
                period_end=date(2026, 8, 17),
                total_return_pct=ret,
                max_drawdown_pct=-5.0,
                win_rate_pct=50.0,
                num_trades=10,
                notes="daily_review",
            )
            for key, ret in [("a", 9.0), ("ma_rsi_v1", 2.0), ("c", -3.0)]
        ],
    )

    report = build(session_factory)

    assert "■ 최근 리뷰" in report
    assert "활성 전략 순위: 2/3위" in report


def test_report_can_carry_account_reconciliation():
    session_factory = make_session_factory("sqlite:///:memory:")
    report = build(session_factory, account_summary=["⚠️ 현금: DB 500만 vs 계좌 570만"])
    assert "■ 계좌 대조" in report
    assert "570만" in report


def test_split_keeps_chunks_within_limit():
    text = "\n".join(f"{i}번째 줄 " + "가" * 100 for i in range(200))
    chunks = split_for_telegram(text)

    assert len(chunks) > 1
    assert all(len(c) <= TELEGRAM_LIMIT for c in chunks)


def test_split_does_not_break_mid_line():
    """줄 중간에서 자르면 표가 깨져 붙여넣기 용도로 못 쓴다."""
    lines = [f"항목{i}: 값{i}" for i in range(500)]
    chunks = split_for_telegram("\n".join(lines))

    rejoined = "\n".join(chunks).split("\n")
    assert rejoined == lines


def test_split_handles_single_line_longer_than_limit():
    """줄 하나가 제한보다 길면 어쩔 수 없이 잘라야 하지만, 내용은 보존한다."""
    long_line = "가" * (TELEGRAM_LIMIT * 2 + 50)
    chunks = split_for_telegram(long_line)

    assert all(len(c) <= TELEGRAM_LIMIT for c in chunks)
    assert "".join(chunks) == long_line


def test_split_returns_single_chunk_for_short_text():
    assert split_for_telegram("짧은 글") == ["짧은 글"]
