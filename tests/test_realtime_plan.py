"""실시간 매매 계획 검증.

이 화면에는 전략 평가 결과가 없다. 성적이 아직 하나도 없기 때문이다. 그래서
'왜 없는지'를 대신 보여 주는데, 그게 틀리거나 비면 화면이 "이미 돌고 있나
보다"로 읽힌다. 실시간 매매를 안 하는데 하는 것처럼 보이는 것이 이 화면의
가장 위험한 실패 방식이다."""

import json

import pytest

from muwon.analysis.realtime_plan import GRADES, STAGES, load


def test_the_plan_loads_and_says_what_stage_we_are_in():
    계획 = load()
    assert 계획.단계 in STAGES
    assert len(계획.단계뜻) > 10
    assert len(계획.한줄) > 10


def test_every_candidate_names_its_evidence_grade_and_source():
    """근거 없는 후보가 한 줄 섞이면, 표를 '다들 그렇게 한다'로 읽게 된다."""
    for c in load().후보:
        assert c.등급 in GRADES, f"{c.이름}: 모르는 등급"
        assert c.출처, f"{c.이름}: 출처가 비었다"
        assert len(c.한줄평) > 30, f"{c.이름}: 한줄평이 너무 짧다"
        assert c.한국증거, f"{c.이름}: 한국 시장 증거 칸이 비었다"
        assert c.데이터, f"{c.이름}: 무슨 데이터가 필요한지가 비었다"


def test_a_candidate_with_no_public_evidence_is_graded_D_not_hidden():
    """근거가 없는 것을 목록에서 빼 버리면, 나중에 누군가 그걸 다시 들고 온다.
    빼는 대신 D로 적어 두는 것이 이 표의 쓸모다."""
    등급들 = {c.등급 for c in load().후보}
    assert "D" in 등급들


def test_the_blockers_say_what_to_do_about_them():
    """'막혀 있다'만 있고 '그래서 뭘 하면 되나'가 없으면 화면이 절망만 준다."""
    막는것 = load().막는것
    assert 막는것, "막는 것이 하나도 없으면 왜 성적이 없는지를 설명 못 한다"
    for 항목 in 막는것:
        assert len(항목["설명"]) > 30
        assert len(항목["그래서"]) > 20


def test_what_we_can_measure_right_now_is_visible():
    """새 데이터 없이 지금 잴 수 있는 것이 무엇인지가 다음 할 일을 정한다."""
    계획 = load()
    지금 = 계획.지금가능한후보
    assert 지금, "지금 잴 수 있는 후보가 없으면 다음 할 일은 실험이 아니라 데이터 확보다"
    assert set(지금) <= set(계획.후보)


def test_an_unknown_stage_fails_loudly(tmp_path):
    경로 = tmp_path / "이상한단계.json"
    경로.write_text(json.dumps({"단계": "대충"}), encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 단계"):
        load(경로)


def test_an_unknown_grade_fails_loudly(tmp_path):
    후보 = {
        "키": "x", "이름": "x", "한줄": "x", "등급": "S", "한국증거": "x",
        "데이터": "x", "지금가능": True, "비용민감도": "x", "한줄평": "x", "출처": "x",
    }
    경로 = tmp_path / "이상한등급.json"
    경로.write_text(json.dumps({"단계": "조사", "후보": [후보]}), encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 근거 등급"):
        load(경로)


def test_a_half_filled_candidate_fails_loudly(tmp_path):
    경로 = tmp_path / "반쯤.json"
    경로.write_text(
        json.dumps({"단계": "조사", "후보": [{"키": "x", "이름": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="빠진 칸"):
        load(경로)


def test_every_finding_says_what_was_measured_and_what_was_decided():
    """결과만 있고 판단이 없으면 다음에 같은 걸 또 잰다."""
    for f in load().검증:
        assert len(f.잰것) > 15, f"{f.제목}: 무엇을 쟀는지가 없다"
        assert len(f.결과) > 30, f"{f.제목}: 결과가 없다"
        assert len(f.판단) > 15, f"{f.제목}: 그래서 어떻게 할 것인지가 없다"
        assert f.측정일


def test_the_finding_table_stays_narrow_enough_for_a_phone():
    """폰 폭(390px)에서 표가 잘리는 것을 화면으로 두 번 잡았다. 칸이 많으면
    오른쪽이 통째로 안 보이는데, 잘린 쪽이 하필 결론인 '낮' 칸이었다."""
    for f in load().검증:
        for 줄 in f.결과.splitlines():
            if 줄.startswith("|"):
                칸 = [c for c in 줄.split("|") if c.strip()]
                assert len(칸) <= 3, f"{f.제목}: 표가 {len(칸)}칸이라 폰에서 잘린다"
