"""전략 전략 평가 결과 검증.

이 표는 다시 계산하지 않는 기록이다. 그래서 틀리면 조용히 틀린다.
화면은 멀쩡해 보이는데 숫자만 옛것이거나 빠져 있다."""

import json

import pytest

from muwon.analysis.report_card import (
    BAD_YEAR_LIMIT,
    MIN_TRADES,
    VERDICTS,
    load,
    판정뜻,
    판정색,
    판정하기,
)
from muwon.strategy.registry import list_definitions


def test_the_saved_card_loads_and_covers_every_registered_strategy():
    """등록된 전략인데 전략 평가 결과에 없으면, 화면은 '평가가 끝났다'는 인상을
    주면서 실제로는 빠뜨린 것이 된다."""
    카드 = load()
    적힌것 = {r.키 for r in 카드.전략}
    등록된것 = {d.key for d in list_definitions()}
    assert 등록된것 - 적힌것 == set(), f"전략 평가 결과에 빠진 전략: {sorted(등록된것 - 적힌것)}"


def test_every_row_has_a_plain_korean_note():
    """숫자만 있으면 '누구나 이해할 수 있게'가 아니다."""
    카드 = load()
    for r in 카드.전략 + 카드.조합:
        assert len(r.한줄평) > 15, f"{r.이름}: 한줄평이 없거나 너무 짧다"


def test_the_verdict_matches_the_rule_not_someone_s_opinion():
    """항목마다 사람이 판정을 매기면 '이건 평균이 높으니까 봐주자'가 들어간다.
    한 번 들어가면 표 전체를 믿을 수 없게 된다."""
    카드 = load()
    for r in 카드.전략 + 카드.조합:
        assert r.판정 == 판정하기(r.최악, r.거래), f"{r.이름}: 판정이 규칙과 다르다"


def test_the_rule_ranks_by_the_worst_year_not_the_average():
    """1순위 기준이 최악의 해라는 것이 이 표의 핵심 약속이다."""
    # 평균이 아무리 높아도 최악의 해가 -30%면 '안씀'
    assert 판정하기(최악=-30.0, 거래=500) == "안씀"
    # 평균이 낮아도 5년 내내 플러스면 '쓸만함'
    assert 판정하기(최악=0.5, 거래=500) == "쓸만함"
    assert 판정하기(최악=BAD_YEAR_LIMIT + 1, 거래=500) == "조건부"


def test_too_few_trades_is_held_not_judged():
    """5년에 2건 거래한 전략은 성적이 좋은 게 아니라 작동을 안 한 것이다."""
    assert 판정하기(최악=-2.2, 거래=2) == "보류"
    assert 판정하기(최악=-2.2, 거래=MIN_TRADES) != "보류"


def test_every_verdict_has_a_colour_and_a_meaning():
    for 판정 in VERDICTS:
        assert 판정색(판정)
        assert len(판정뜻(판정)) > 10


def test_the_card_says_where_its_numbers_came_from():
    """출처 없는 숫자는 나중에 검증할 수가 없다."""
    기준 = load().기준
    assert 기준["커밋"]
    assert 기준["실행"]
    assert 기준["측정일"]
    assert "슬리피지" in 기준["비용"], "비용 가정을 안 적으면 숫자를 과신하게 된다"


def test_a_stale_card_is_flagged():
    """다시 계산하지 않는 기록이라, 옛 숫자를 최신인 척 보여 주는 것이
    이 설계의 가장 위험한 실패 방식이다."""
    from datetime import date

    카드 = load()
    assert 카드.오래됐나(today=date(2099, 1, 1))
    assert not 카드.오래됐나(today=date.fromisoformat(카드.측정일))


def test_a_broken_row_fails_loudly(tmp_path):
    """반쯤 비어 있는 표는 없는 것보다 나쁘다."""
    깨진것 = tmp_path / "깨진표.json"
    깨진것.write_text(
        json.dumps({"기준": {}, "전략": [{"키": "x", "이름": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="빠진 칸"):
        load(깨진것)


def test_an_unknown_verdict_fails_loudly(tmp_path):
    항목 = {
        "키": "x", "이름": "x", "계열": "x", "평균": 1.0, "최악": 1.0,
        "샤프": 1.0, "낙폭": -1.0, "손익비": 1.0, "거래": 100,
        "판정": "그럭저럭", "한줄평": "x",
    }
    경로 = tmp_path / "이상한판정.json"
    경로.write_text(json.dumps({"기준": {}, "전략": [항목]}), encoding="utf-8")
    with pytest.raises(ValueError, match="모르는 판정"):
        load(경로)


def test_the_generator_reproduces_the_saved_file():
    """손으로 고친 채 잊으면 화면과 생성 스크립트가 서로 다른 말을 한다."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    저장된것 = (root / "docs" / "전략평가.json").read_text(encoding="utf-8")
    결과 = subprocess.run(
        [sys.executable, str(root / "scripts" / "build_report_card.py")],
        capture_output=True, text=True, cwd=root, check=False,
    )
    assert 결과.returncode == 0, 결과.stdout + 결과.stderr
    assert (root / "docs" / "전략평가.json").read_text(encoding="utf-8") == 저장된것
