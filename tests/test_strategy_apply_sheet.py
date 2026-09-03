"""전략을 바꾼 날, 화면이 언제부터 새 전략을 보여 주는가.

## 왜 이 파일이 있나

실제로 쓰는 전략은 상태 DB에 있고(`strategy.active_keys`), 시트 `설정`
탭의 `strategy` 칸은 그것을 옮겨 적은 사본이다. **화면은 그 사본을 읽는다.**

그런데 사본을 옮겨 적는 것이 17:40 기록 저장뿐이었다. 08:20에 전략을
바꾸면 08:20부터 17:40까지 화면이 옛 전략을 보여 줬다. 그 아홉 시간이
하루 매매 전체를 덮는다. 08:30에 매수 후보를 승인할 때 화면 맨 위 띠가
어제 전략을 적고 있었다.

텔레그램은 맞았다. 거기는 DB를 읽는다. 그래서 알림과 화면이 서로 다른
전략을 말하고 있었고, 어느 쪽이 맞는지 화면만 봐서는 알 수 없었다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "apply_strategy_change.py"
_스펙 = importlib.util.spec_from_file_location("apply_strategy_change_sheet", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["apply_strategy_change_sheet"] = _모듈
_스펙.loader.exec_module(_모듈)

시트의전략칸갱신 = _모듈.시트의전략칸갱신


@pytest.fixture
def 부른것(monkeypatch):
    """`update_setting`이 무엇으로 불렸는지 담는다."""
    담은것 = []

    def 가짜(sheet_id, 이름, 글자, svc=None, 설명=""):
        담은것.append({"시트": sheet_id, "이름": 이름, "값": 글자})
        return "volume_surge_5d_ma20"

    import muwon.cloud.sector_sheet as ㅅ

    monkeypatch.setattr(ㅅ, "update_setting", 가짜)
    return 담은것


def test_반영한_전략을_시트_사본에_바로_옮긴다(부른것):
    """여기가 이 파일의 핵심이다. 안 옮기면 화면이 하루 종일 옛 전략을
    보여 준다."""
    시트의전략칸갱신("시트아이디", "volatility_breakout_k05")

    assert len(부른것) == 1
    assert 부른것[0]["이름"] == "strategy"
    assert 부른것[0]["값"] == "volatility_breakout_k05"


def test_시트를_못_찾으면_조용히_넘기지_않는다(부른것, capsys):
    시트의전략칸갱신("", "volatility_breakout_k05")

    assert 부른것 == []
    assert "시트를 못 찾아" in capsys.readouterr().err


def test_시트_쓰기가_실패해도_반영을_되돌리지_않는다(monkeypatch, capsys):
    """전략은 이미 바뀌었다. 여기서 터뜨리면 워크플로가 빨개지고, 사람은
    전략이 안 바뀐 줄 안다. 실제로는 바뀌어 있어서 그쪽이 더 위험하다."""

    def 터짐(*args, **kwargs):
        raise RuntimeError("구글 시트가 막혔습니다")

    import muwon.cloud.sector_sheet as ㅅ

    monkeypatch.setattr(ㅅ, "update_setting", 터짐)

    시트의전략칸갱신("시트아이디", "volatility_breakout_k05")  # 안 터져야 한다

    말 = capsys.readouterr().err
    assert "못 고쳤습니다" in 말
    assert "17:40" in 말, "언제 복구되는지 적어야 합니다"


def test_반영_경로가_이_함수를_부른다():
    """함수만 있고 안 부르면 아무것도 안 고쳐진다. 그런데 테스트는
    통과한다."""
    글 = _경로.read_text(encoding="utf-8")
    자리 = 글.index("승인.반영표시(session, 줄)")

    assert "시트의전략칸갱신(시트, 줄.새전략)" in 글[자리:], "반영 뒤에 안 부릅니다"


def test_전략_키를_넘긴다_이름이_아니라():
    """시트 사본은 키를 담는 칸이다. 화면 이름을 넣으면 그 칸을 읽는
    쪽이 등록된 전략에서 못 찾는다."""
    글 = _경로.read_text(encoding="utf-8")

    assert "시트의전략칸갱신(시트, 줄.새전략)" in 글
    assert "시트의전략칸갱신(시트, 새이름)" not in 글


# ── 보유 종목이 언제 팔리는지 (2026-09-03) ──────────────────────
#
# 2026-09-02에 청산이 '산 전략'을 따르도록 바뀌었는데 반영 알림 문장만 옛
# 동작을 그대로 적고 있었다. 보유 종목이 언제 팔리는지를 반대로 알리는
# 문장이라, 그 말을 믿고 오늘 안 팔릴 것으로 생각하면 실제로는 팔린다.


def test_반영_알림이_산_전략의_매도_규칙을_말한다():
    class 줄:
        이전전략 = "gap_up_go"
        새전략 = "volume_surge_3d"
        근거구간 = "1개월"
        등급 = "확인필요"
        사유 = ""

    글 = _모듈.반영알림글(줄(), "갭 상승 따라가기", "거래량 급증 3일")

    assert "매수 시점의 전략이 정한 매도 규칙" in 글
    # 옛 문장이 다시 들어오면 여기서 걸린다.
    assert "보유 중인 종목도 오늘부터 새 전략의 매도 규칙" not in 글
    # 전략 키를 못 찾는 보유가 있다는 사실도 같이 적어야 한다.
    assert "확인할 수 없는 종목" in 글


def test_반영_알림에_되돌리는_버튼을_안_붙인다():
    """되돌리기까지 버튼으로 두면 손가락이 스치는 것으로 전략이 오간다."""
    class 줄:
        이전전략 = "gap_up_go"
        새전략 = "volume_surge_3d"
        근거구간 = ""
        등급 = ""
        사유 = ""

    글 = _모듈.반영알림글(줄(), "갭 상승 따라가기", "거래량 급증 3일")
    assert "되돌리는 버튼을 붙이지 않습니다" in 글
