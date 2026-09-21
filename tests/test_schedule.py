"""자동 실행 일정 계산 검증.

이 계산이 틀리면 화면이 "내일 아침 09:05"라고 말하는데 실제로는 오늘
저녁에 돈다. 안내가 아니라 거짓말이 된다."""

from datetime import datetime
from pathlib import Path

from muwon.dashboard.schedule import KST, _crons_in, describe_cron, next_fire, upcoming


def test_weekday_cron_fires_on_the_right_korean_morning():
    """`5 0 * * 1-5`는 UTC 월~금 00:05 = 한국시간 평일 09:05."""
    # 2026-08-19는 수요일
    now = datetime(2026, 8, 19, 8, 0, tzinfo=KST)
    fire = next_fire("5 0 * * 1-5", now)
    assert fire == datetime(2026, 8, 19, 9, 5, tzinfo=KST)


def test_after_todays_run_it_points_at_tomorrow():
    now = datetime(2026, 8, 19, 10, 0, tzinfo=KST)  # 수요일 오전, 이미 지남
    fire = next_fire("5 0 * * 1-5", now)
    assert fire == datetime(2026, 8, 20, 9, 5, tzinfo=KST)


def test_friday_evening_skips_the_weekend():
    now = datetime(2026, 8, 21, 20, 0, tzinfo=KST)  # 금요일 저녁
    fire = next_fire("5 0 * * 1-5", now)
    assert fire.weekday() == 0, "다음은 월요일이어야 한다"
    assert fire == datetime(2026, 8, 24, 9, 5, tzinfo=KST)


def test_sunday_utc_becomes_monday_in_korea():
    """`0 15 * * 0`은 UTC 일요일 15:00 = 한국시간 월요일 00:00.

    요일까지 밀리는 경우다. cron 요일을 그대로 화면에 쓰면 '일요일'이라고
    잘못 안내하게 된다."""
    now = datetime(2026, 8, 19, 12, 0, tzinfo=KST)
    fire = next_fire("0 15 * * 0", now)
    assert fire == datetime(2026, 8, 24, 0, 0, tzinfo=KST)
    assert fire.weekday() == 0
    assert describe_cron("0 15 * * 0") == "매주 월 00:00"


def test_cron_weekday_numbering_is_not_pythons():
    """cron은 일요일이 0, 파이썬 weekday()는 월요일이 0이다.

    그대로 비교하면 하루씩 밀린다. 조용히 틀리는 종류의 버그라 못 박아 둔다."""
    now = datetime(2026, 8, 19, 12, 0, tzinfo=KST)  # 수요일
    # cron 1 = 월요일 → UTC 월 03:00 = KST 월 12:00
    fire = next_fire("0 3 * * 1", now)
    assert fire.weekday() == 0


def test_describe_reads_in_korean_time():
    assert describe_cron("5 0 * * 1-5") == "평일 09:05"
    assert describe_cron("30 6 * * 1-5") == "평일 15:30"
    assert describe_cron("0 0 * * *") == "매일 09:00"


def test_a_malformed_cron_does_not_crash_the_screen():
    """워크플로를 잘못 고쳤을 때 화면 전체가 죽으면 안 된다."""
    assert next_fire("이건 cron이 아니다", datetime.now(KST)) is None
    assert describe_cron("*/5") == "*/5"


def test_it_reads_workflow_files_from_disk(tmp_path: Path):
    """시각을 화면에 손으로 적지 않는다는 것이 이 기능의 핵심이다.

    실제 파일을 안 읽고 상수를 쓰기 시작하면, 일정을 바꿨을 때 화면만
    옛 시각으로 남는다.

    실제 저장소의 워크플로에 지금 살아 있는 cron이 있다고 못 박으면 안
    된다(2026-09-21, 전체 자동 실행을 껐다). 그래서 가짜 파일로 "파일을
    읽어서 반영한다"만 확인한다.

    파일 이름은 `WATCHED`에 있는 것 중 하나를 그대로 써야 한다. `upcoming`이
    지켜보는 파일 이름을 고정해 두고 그것만 읽기 때문이다."""
    workflow = tmp_path / "market-report.yml"
    workflow.write_text('name: 샘플\non:\n  schedule:\n    - cron: "5 0 * * 1-5"\n', encoding="utf-8")
    jobs = upcoming(datetime(2026, 8, 19, 8, 0, tzinfo=KST), workflow_dir=tmp_path)
    assert jobs, "워크플로에서 살아 있는 cron을 하나도 못 읽었다"
    for job in jobs:
        assert job.설명문, f"{job.이름}: 사람이 읽을 문장이 비었다"
        assert job.다음실행 is not None, f"{job.이름}: 다음 실행 시각을 못 구했다"


def test_모든_자동실행이_지금은_꺼져_있다():
    """기존 트레이딩 시스템 전체를 멈췄다(2026-09-21). 주인이 운영해 본
    결과 지금 구조로는 의미가 없다고 판단해서, n8n의 AutoTrading_Schedule·
    AutoTrading_계좌조회·AutoTrading_Telegram을 unpublish하고 이 저장소에
    남아 있던 GitHub Actions 스케줄(매수 후보, 시장·섹터 리포트, 30분봉
    수집, 기록을 시트로, 기준 제안, 실거래 목록 다시 재기, 분석 리포트)
    일곱 개를 전부 주석 처리했다.

    새로 시작하는 시뮬레이터가 자리 잡을 때까지, 실제 워크플로 파일에
    살아 있는 cron이 하나도 없어야 한다. 다시 켤 때 이 테스트가 실패하면서
    '의도한 변경인가'를 한 번 묻게 된다."""
    jobs = upcoming(datetime(2026, 8, 19, 8, 0, tzinfo=KST))
    assert jobs == [], (
        f"자동 실행을 다시 켰다면 이 테스트도 함께 고치세요: {[j.이름 for j in jobs]}"
    )


def test_missing_workflow_directory_is_not_an_error(tmp_path: Path):
    assert upcoming(datetime.now(KST), workflow_dir=tmp_path) == []


def test_remaining_time_is_worded_by_size():
    now = datetime(2026, 8, 19, 8, 0, tzinfo=KST)
    jobs = upcoming(now)
    assert all(job.남은시간(now) for job in jobs)


def test_a_commented_out_cron_is_not_a_schedule():
    """자동 실행을 꺼 둔 뒤에도 화면이 "내일 09:05에 돕니다"라고 하면
    안내가 아니라 거짓말이다. 실제로 오늘 멈추면서 이걸 잡았다."""
    text = """
on:
  # schedule:
  #   - cron: "5 0 * * 1-5"
  workflow_dispatch:
"""
    assert _crons_in(text) == []


def test_a_live_cron_next_to_a_commented_one_still_counts():
    text = """
  schedule:
    # - cron: "30 6 * * 1-5"   # 옛 시각
    - cron: "5 0 * * 1-5"
"""
    assert _crons_in(text) == ["5 0 * * 1-5"]


def test_paper_trading_is_currently_stopped():
    """지금 자동매매를 멈춰 둔 상태라는 것을 못 박는다.

    다시 켤 때 이 테스트가 실패하면서 '의도한 변경인가'를 한 번 묻게 된다."""
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "paper-trading.yml"
    assert _crons_in(workflow.read_text(encoding="utf-8")) == [], (
        "자동매매를 다시 켰다면 이 테스트도 함께 고치세요. "
        "켜 두고 잊는 일이 없도록 일부러 걸어 둔 장치입니다."
    )


def test_a_stopped_schedule_is_not_live_even_with_the_switch_on():
    """스케줄을 꺼 놨는데 킬스위치만 보고 'LIVE'라고 띄우면 화면이
    거짓말을 한다. 실제로 폰에서 그렇게 떠 있는 걸 보고 잡았다."""
    from muwon.dashboard.schedule import automation_state
    from muwon.settings.schema import RiskPolicy

    뱃지, _, 설명 = automation_state(RiskPolicy(trading_enabled=True))
    assert 뱃지 == "자동 실행 꺼짐", "지금 자동매매 일정은 꺼져 있다"
    assert "손으로" in 설명


def test_the_switch_and_the_schedule_are_reported_separately(tmp_path):
    """둘은 다른 것이다. 꺼진 이유가 무엇인지 알아야 어디를 켜는지 안다."""
    from muwon.dashboard.schedule import automation_state, upcoming
    from muwon.settings.schema import RiskPolicy

    workflow = tmp_path / "paper-trading.yml"
    workflow.write_text('  schedule:\n    - cron: "5 0 * * 1-5"\n', encoding="utf-8")

    # 일정이 살아 있다는 것을 먼저 확인한다. 이 검사의 전제다.
    assert upcoming(datetime(2026, 8, 19, 8, 0, tzinfo=KST), workflow_dir=tmp_path)

    import muwon.dashboard.schedule as mod

    원래 = mod.upcoming
    mod.upcoming = lambda now=None, workflow_dir=tmp_path: 원래(now, workflow_dir)
    try:
        assert automation_state(RiskPolicy(trading_enabled=True))[0] == "LIVE"
        assert automation_state(RiskPolicy(trading_enabled=False))[0] == "중지됨"
    finally:
        mod.upcoming = 원래
