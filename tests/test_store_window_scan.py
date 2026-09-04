"""측정 결과 파일을 DB에 쌓는 스크립트를 본다.

## 여기서 꼭 잡아야 하는 것

**매매 대상이 무엇인지 모르면 쌓지 않는다.** 이름을 지어내 넣으면 앞서 쌓은
줄과 다른 측정으로 갈리고, 화면은 한쪽을 통째로 못 본다. 조용히 성공한
척하는 실패다.

**매매가 0건인 전략은 순위에서 뺀다.** 한 건도 안 산 것이 수익률 0%로 맨
위에 오면 안 된다.

**측정 워크플로가 state-write에 묶이면 안 된다.** 한 시간 걸리는 계산이
자물쇠를 잡으면 그동안 장중 손절 감시가 멈춘다.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

_뿌리 = Path(__file__).resolve().parent.parent


def _스크립트():
    자리 = importlib.util.spec_from_file_location(
        "store_window_scan", _뿌리 / "scripts" / "store_window_scan.py")
    쪽 = importlib.util.module_from_spec(자리)
    자리.loader.exec_module(쪽)
    return 쪽


ㅁ = _스크립트()


def _줄(전략="t", 상한=20, 슬립=0.001, 매매수=100, 연환산=12.0) -> dict:
    return {
        "전략": 전략, "이름": 전략, "상한": 상한, "슬리피지": 슬립,
        "매매대상": "sheet", "종목수": 63,
        "시작일": "2021-01-04", "끝일": "2026-09-02",
        "구간": {
            "길이": 상한, "겹침": False, "구간수": 69, "기하평균": 0.9,
            "연환산": 연환산, "산술평균": 1.1, "중앙값": 0.8,
            "플러스비율": 55.0, "하위10": -4.2, "하위25": -1.5,
            "최악": -11.0, "최고": 14.0, "표준편차": 5.5,
            "하락대비수익": 0.31, "구간낙폭중앙값": -3.3,
        },
        "겹친구간": None,
        "매매": {
            "매매수": 매매수, "승률": 48.0, "손익비": 1.4, "기대수익": 0.7,
            "중앙값": 0.2, "평균보유일수": 6.4, "미청산수": 2,
            "갈래비율": {"손절": 30.0, "매도신호": 60.0, "기간만료": 10.0},
            "기간만료비율": 10.0,
        },
        "누적수익률": 154.0, "최대낙폭": -22.5,
    }


def _파일(**바꿀것) -> dict:
    내용 = {
        "잰날": "2026-09-03",
        "매매대상": "실거래 시트 63종목",
        "매매대상열쇠": "sheet",
        "종목수": 63,
        "상한들": [20], "슬리피지들": [0.001],
        "줄": [_줄()],
    }
    내용.update(바꿀것)
    return 내용


# ── 파일 읽기 ───────────────────────────────────────────────────


def test_파일을_읽어_잰것으로_되돌린다():
    잰것들, 열쇠, 잰날 = ㅁ.읽어들이기(_파일())
    assert 열쇠 == "sheet"
    assert 잰날 == date(2026, 9, 3)
    assert len(잰것들) == 1
    assert 잰것들[0].구간.연환산 == pytest.approx(12.0)
    assert 잰것들[0].종목수 == 63


def test_열쇠가_없는_옛_파일도_읽는다():
    """첫 두 측정이 그 칸 없이 나왔다."""
    내용 = _파일()
    del 내용["매매대상열쇠"]
    _, 열쇠, _ = ㅁ.읽어들이기(내용)
    assert 열쇠 == "sheet"

    내용 = _파일(매매대상="시가총액 30종목")
    del 내용["매매대상열쇠"]
    _, 열쇠, _ = ㅁ.읽어들이기(내용)
    assert 열쇠 == "market_cap"


def test_무엇으로_잰_것인지_모르면_빈_글자다():
    """모르는 것을 아무 이름으로나 채우면 서로 다른 측정이 한 이름 밑에
    섞인다."""
    내용 = _파일(매매대상="무언가")
    del 내용["매매대상열쇠"]
    _, 열쇠, _ = ㅁ.읽어들이기(내용)
    assert 열쇠 == ""


def test_종목수를_안_적던_판은_사람이_읽는_글에서_되짚는다():
    """첫 두 측정이 그 칸 없이 나왔다. "실거래 시트 63종목"에 수가 들어 있다."""
    내용 = _파일()
    del 내용["종목수"]
    del 내용["줄"][0]["종목수"]
    잰것들, _, _ = ㅁ.읽어들이기(내용)
    assert 잰것들[0].종목수 == 63


def test_종목수를_못_찾으면_0이다():
    """0은 모른다는 뜻이다. 아무 수나 채워 넣으면 화면이 틀린 조건을
    적는다."""
    내용 = _파일(매매대상="어딘가")
    내용["매매대상열쇠"] = "sheet"
    del 내용["종목수"]
    del 내용["줄"][0]["종목수"]
    잰것들, _, _ = ㅁ.읽어들이기(내용)
    assert 잰것들[0].종목수 == 0


def test_줄에_종목수가_없으면_머리에서_채운다():
    내용 = _파일()
    del 내용["줄"][0]["종목수"]
    잰것들, _, _ = ㅁ.읽어들이기(내용)
    assert 잰것들[0].종목수 == 63


# ── 스크립트 실행 ───────────────────────────────────────────────


def _돌리기(tmp_path, monkeypatch, 내용: dict, 더줄인자=()):
    파일 = tmp_path / "window-scan.json"
    파일.write_text(json.dumps(내용, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "t.db"

    monkeypatch.setattr(ㅁ.bootstrap_settings, "database_url",
                        f"sqlite:///{db}", raising=False)
    monkeypatch.setattr(ㅁ, "기준고르기", lambda 인자: ㅁ.ㅈ.기본기준)
    monkeypatch.setattr(
        "sys.argv",
        ["store_window_scan.py", "--파일", str(파일), *더줄인자],
    )
    return ㅁ.main(), db


def test_쌓으면_DB에서_다시_읽힌다(tmp_path, monkeypatch):
    끝값, db = _돌리기(tmp_path, monkeypatch, _파일())
    assert 끝값 == 0

    from muwon.analysis import window_store as ㅅ
    from muwon.db.session import make_session_factory

    with make_session_factory(f"sqlite:///{db}")() as 세션:
        [읽은것] = ㅅ.읽기(세션, 매매대상="sheet")
    assert 읽은것.구간.연환산 == pytest.approx(12.0)
    assert 읽은것.종목수 == 63


def test_매매_대상을_모르면_쌓지_않는다(tmp_path, monkeypatch, capsys):
    내용 = _파일(매매대상="무언가")
    del 내용["매매대상열쇠"]
    끝값, _ = _돌리기(tmp_path, monkeypatch, 내용)
    assert 끝값 == 1
    assert "매매 대상이 무엇인지" in capsys.readouterr().out


def test_줄이_없으면_쌓지_않는다(tmp_path, monkeypatch):
    끝값, _ = _돌리기(tmp_path, monkeypatch, _파일(줄=[]))
    assert 끝값 == 1


def test_미리보기는_DB를_안_고친다(tmp_path, monkeypatch):
    끝값, db = _돌리기(tmp_path, monkeypatch, _파일(), ("--미리보기",))
    assert 끝값 == 0
    assert not db.exists()


def test_매매가_0건인_전략은_순위에서_뺀다(tmp_path, monkeypatch):
    """한 건도 안 산 것을 수익률 0%로 맨 위에 두지 않는다."""
    내용 = _파일(줄=[_줄(전략="산것", 매매수=80), _줄(전략="안산것", 매매수=0)])
    끝값, db = _돌리기(tmp_path, monkeypatch, 내용)
    assert 끝값 == 0

    from muwon.db.models import StrategyRankRow, WindowPerfRow
    from muwon.db.session import make_session_factory

    with make_session_factory(f"sqlite:///{db}")() as 세션:
        # 요약은 둘 다 남는다. 안 산 것도 사실이다.
        assert 세션.query(WindowPerfRow).count() == 2
        순위 = [ㄱ.전략 for ㄱ in 세션.query(StrategyRankRow)]
    assert 순위 == ["산것"]


def test_상한과_슬리피지마다_따로_줄을_세운다(tmp_path, monkeypatch):
    내용 = _파일(줄=[
        _줄(전략="가", 상한=5, 슬립=0.0), _줄(전략="나", 상한=5, 슬립=0.0),
        _줄(전략="가", 상한=20, 슬립=0.001), _줄(전략="나", 상한=20, 슬립=0.001),
    ])
    끝값, db = _돌리기(tmp_path, monkeypatch, 내용)
    assert 끝값 == 0

    from muwon.db.models import StrategyRankRow
    from muwon.db.session import make_session_factory

    with make_session_factory(f"sqlite:///{db}")() as 세션:
        묶음 = {(ㄱ.상한, ㄱ.슬리피지) for ㄱ in 세션.query(StrategyRankRow)}
        assert 묶음 == {(5, 0.0), (20, 0.001)}
        assert 세션.query(StrategyRankRow).count() == 4


# ── 워크플로 ────────────────────────────────────────────────────


def _워크플로(이름: str) -> dict:
    return yaml.safe_load(
        (_뿌리 / ".github" / "workflows" / 이름).read_text(encoding="utf-8"))


def test_재는_워크플로는_상태DB를_안_잠근다():
    """한 시간 걸리는 계산이 state-write를 잡으면 그동안 장중 손절 감시와
    매수 후보 산출이 전부 기다린다."""
    글 = (_뿌리 / ".github" / "workflows" / "window-scan.yml").read_text(
        encoding="utf-8")
    assert "group: state-write" not in 글
    assert "gdrive_sync.py upload" not in 글, (
        "재는 워크플로가 DB를 올리고 있습니다. 쌓는 워크플로로 옮기세요."
    )


def test_쌓는_워크플로는_상태DB를_잠근다():
    묶음 = _워크플로("store-window-scan.yml").get("concurrency") or {}
    assert 묶음.get("group") == "state-write"
    assert 묶음.get("cancel-in-progress") is False


def test_쌓기가_실패하면_DB를_안_올린다():
    """반쯤 들어간 DB를 올리면 다음 실행이 그 상태를 정상으로 읽는다."""
    글 = (_뿌리 / ".github" / "workflows" / "store-window-scan.yml").read_text(
        encoding="utf-8")
    올리는곳 = 글.index("gdrive_sync.py upload")
    assert "if: success()" in 글[:올리는곳]
    assert "if: always()" not in 글[:올리는곳]


def test_미리보기로_돌면_DB를_안_올린다():
    글 = (_뿌리 / ".github" / "workflows" / "store-window-scan.yml").read_text(
        encoding="utf-8")
    올리는곳 = 글.index("gdrive_sync.py upload")
    assert "dry_run != 'true'" in 글[:올리는곳]
