"""전망 적중 기록 검증.

**전망을 내놓고 결과를 안 남기면, 전망이 쓸모없다는 것도 영영 모른다.**
그래서 이 기록이 조용히 틀리면 근거 없는 숫자를 근거인 줄 알고 돈을 걸게 된다."""

from datetime import date

import pytest

from muwon.market.analog import Baseline, Forecast
from muwon.market.forecast_log import (
    MIN_SCORED,
    TAIL_TARGET,
    LogRow,
    fill_actuals,
    format_scorecard,
    load,
    row_from_forecast,
    save,
    score,
)


def _줄(기준일="2026-01-05", 대상="반도체", 상승확률=70.0, 하위10=-8.0,
        기준선상승확률=55.0, 실제수익=None):
    return LogRow(
        기준일=기준일, 대상=대상, 지평=20, 구간수=16, 총일수=200,
        중앙값=2.0, 하위10=하위10, 상승확률=상승확률,
        기준선_중앙값=1.0, 기준선_상승확률=기준선상승확률, 우연폭=24.5,
        실제수익=실제수익,
    )


def _전망(대상="반도체"):
    return Forecast(
        기준일=date(2026, 1, 5), 대상=대상, 구간수=16, 총일수=200, 지평=20,
        중앙값=2.0, 상위25=6.0, 하위25=-1.0, 하위10=-8.0, 상승확률=70.0, 구간들=[],
        기준선=Baseline(표본수=1000, 중앙값=1.0, 하위10=-9.0, 상승확률=55.0),
    )


def test_a_forecast_becomes_a_row_with_its_baseline():
    """기준선을 같이 안 남기면, 나중에 '전망이 더 나았나'를 물을 수 없다."""
    r = row_from_forecast(_전망(), 렌즈="확장")
    assert r.대상 == "반도체"
    assert r.기준선_상승확률 == 55.0
    assert r.렌즈 == "확장"
    assert not r.채워졌나


def test_a_forecast_that_could_not_produce_numbers_is_still_recorded():
    """못 낸 날이 얼마나 많았나도 판단에 필요하다."""
    못낸것 = Forecast(
        기준일=date(2026, 1, 5), 대상="로봇", 구간수=3, 총일수=10, 지평=20,
        중앙값=None, 상위25=None, 하위25=None, 하위10=None, 상승확률=None,
        구간들=[], 사유="비슷했던 때가 3번뿐",
    )
    r = row_from_forecast(못낸것)
    assert r.상승확률 is None
    assert r.구간수 == 3


def test_saving_twice_does_not_duplicate(tmp_path):
    db = tmp_path / "f.db"
    save([_줄()], db)
    save([_줄()], db)
    assert len(load(db)) == 1


def test_rerunning_never_erases_an_actual_result(tmp_path):
    """다시 실행했다고 답이 사라지면 적중 기록이 통째로 날아간다."""
    db = tmp_path / "f.db"
    save([_줄()], db)
    fill_actuals(lambda 대상, 기준일, 지평: 3.5, db, today=date(2026, 2, 5))
    save([_줄()], db)  # 같은 날 전망을 다시 계산해 덮어씀
    (되읽은것,) = load(db)
    assert 되읽은것.실제수익 == 3.5, "다시 실행하자 실제 결과가 지워졌다"


def test_actuals_are_only_filled_when_the_horizon_has_passed(tmp_path):
    db = tmp_path / "f.db"
    save([_줄()], db)
    채운수 = fill_actuals(lambda 대상, 기준일, 지평: None, db)
    assert 채운수 == 0
    assert not load(db)[0].채워졌나


def test_direction_is_scored_against_what_the_forecast_leaned_towards():
    assert _줄(상승확률=70.0, 실제수익=3.0).방향맞췄나 is True
    assert _줄(상승확률=70.0, 실제수익=-3.0).방향맞췄나 is False
    assert _줄(상승확률=30.0, 실제수익=-3.0).방향맞췄나 is True
    assert _줄(상승확률=30.0, 실제수익=3.0).방향맞췄나 is False


def test_the_tail_is_breached_when_reality_was_worse():
    assert _줄(하위10=-8.0, 실제수익=-12.0).꼬리뚫렸나 is True
    assert _줄(하위10=-8.0, 실제수익=-3.0).꼬리뚫렸나 is False


def test_it_refuses_to_judge_on_a_thin_sample():
    """30건으로도 모자란데 12건으로 '적중률 60%'를 말할 수 없다."""
    s = score([_줄(기준일=f"2026-01-{i:02d}", 실제수익=1.0) for i in range(1, 13)])
    assert not s.판정할수있나
    assert "아직 판단할 수 없습니다" in format_scorecard(s)
    assert "12건뿐" in s.사유


def test_a_forecast_that_beats_chance_is_reported_as_better():
    # 전망은 70%로 '오른다'고 보고, 실제로 대부분 올랐다.
    # 기준선은 45%로 '내린다'고 봐서 대부분 틀린다.
    줄들 = [
        _줄(기준일=f"2026-01-{i:02d}", 상승확률=70.0, 기준선상승확률=45.0, 실제수익=2.0)
        for i in range(1, MIN_SCORED + 5)
    ]
    s = score(줄들, "반도체")
    assert s.판정할수있나
    assert s.적중률 == pytest.approx(100.0)
    assert s.기준선적중률 == pytest.approx(0.0)
    assert s.더나은가
    assert "예" in format_scorecard(s)


def test_a_useless_forecast_is_called_out():
    """적중률이 '그냥 찍었다면'보다 낮으면 화면이 그렇다고 말해야 한다."""
    줄들 = [
        _줄(기준일=f"2026-01-{i:02d}", 상승확률=70.0, 기준선상승확률=45.0, 실제수익=-2.0)
        for i in range(1, MIN_SCORED + 5)
    ]
    s = score(줄들, "반도체")
    assert not s.더나은가
    assert "아무것도 안 알려 주고" in format_scorecard(s)


def test_underestimated_risk_is_the_loudest_warning():
    """하위 10% 칸을 보고 비중을 정할 참이다. 그 칸이 실제 하락을 못
    감싸면 그걸 믿고 비중을 키운 만큼 다친다."""
    줄들 = [
        _줄(기준일=f"2026-01-{i:02d}", 하위10=-8.0, 실제수익=-20.0)
        for i in range(1, MIN_SCORED + 5)
    ]
    s = score(줄들, "반도체")
    assert s.꼬리뚫린비율 == pytest.approx(100.0)
    assert s.위험을낮잡았나
    assert "위험을 심하게 낮잡고" in format_scorecard(s)


def test_a_well_calibrated_tail_is_not_warned_about():
    """열 건 중 한 건쯤 뚫리는 것이 정상이다. 그게 '하위 10%'의 뜻이다."""
    줄들 = [
        _줄(기준일=f"2026-01-{i:02d}", 하위10=-8.0, 실제수익=-20.0 if i % 10 == 0 else 2.0)
        for i in range(1, MIN_SCORED + 5)
    ]
    s = score(줄들, "반도체")
    assert s.꼬리뚫린비율 <= TAIL_TARGET * 2
    assert not s.위험을낮잡았나
    assert "대체로 감쌌습니다" in format_scorecard(s)


def test_loading_can_be_narrowed_to_one_target(tmp_path):
    db = tmp_path / "f.db"
    save([_줄(대상="반도체"), _줄(대상="바이오")], db)
    assert len(load(db)) == 2
    assert len(load(db, 대상="바이오")) == 1


def test_calibration_tells_inverted_apart_from_random():
    """뒤죽박죽인 것과 **거꾸로**인 것은 전혀 다른 이야기다. 뒤죽박죽이면
    정보가 없는 것이고, 거꾸로면 정보는 있는데 부호가 반대인 것이다.
    실제 데이터가 거꾸로였다. 그걸 '뒤죽박죽'으로 적으면 무엇을 고칠지 모른다."""
    from muwon.market.forecast_log import calibration

    def _묶음(하위10, 실제, n=40, 시작=1):
        return [
            LogRow(
                기준일=f"20{20 + (시작 + i) // 300:02d}-{(시작 + i) % 12 + 1:02d}-{(시작 + i) % 28 + 1:02d}",
                대상=f"t{하위10}", 지평=20, 구간수=16, 총일수=200, 중앙값=1.0,
                하위10=하위10, 상승확률=60.0, 기준선_중앙값=1.0,
                기준선_상승확률=55.0, 우연폭=24.5, 실제수익=실제,
            )
            for i in range(n)
        ]

    # 위험을 크게 잡은 날이 오히려 좋았다 = 거꾸로
    거꾸로 = _묶음(-20.0, 6.0) + _묶음(-2.0, 0.5, 시작=100)
    글 = calibration(거꾸로)
    assert "거꾸로입니다" in 글
    assert "늘려야 할 때 줄입니다" in 글

    # 위험을 크게 잡은 날이 실제로 나빴다 = 제대로
    제대로 = _묶음(-20.0, -8.0) + _묶음(-2.0, 2.0, 시작=100)
    assert "순서가 지켜졌습니다" in calibration(제대로)

    # 칸마다 같다 = 구별 못 함
    구별못함 = _묶음(-20.0, 1.0) + _묶음(-2.0, 1.0, 시작=100)
    assert "구별하지 못합니다" in calibration(구별못함)


def test_calibration_needs_enough_per_bucket():
    """구간마다 표본이 몇 개뿐이면 비교 자체가 뜻이 없다."""
    from muwon.market.forecast_log import calibration

    글 = calibration([_줄(하위10=-20.0, 실제수익=5.0)], min_per_bucket=20)
    assert "비교할 수 없습니다" in 글


def test_the_aggregate_tail_number_points_to_the_breakdown():
    """전체로는 14.9%라 괜찮아 보였는데 구간을 나눠 보니 6%에서 33%까지
    벌어져 있었다. 평균이 문제를 가린 것이다. 실제로 그렇게 속았다."""
    줄들 = [
        _줄(기준일=f"2026-01-{i:02d}", 하위10=-8.0, 실제수익=-20.0 if i % 10 == 0 else 2.0)
        for i in range(1, MIN_SCORED + 5)
    ]
    글 = format_scorecard(score(줄들))
    assert "평균이라 문제를 가릴 수 있습니다" in 글
    assert "위험 크기를 구별했나" in 글
