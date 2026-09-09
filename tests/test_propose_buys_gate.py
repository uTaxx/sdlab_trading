"""거래량 급증이 없는 건가, 미국 섹터가 문제인 건가.

**후보가 0개인 이유가 둘 중 무엇인지 알림에서 가른다.** 이 시험은
`propose_buys.게이트진단글()`이 `USSectorGateStrategy`의 진단
(`us_sector.게이트정보`)을 받아 사람이 읽을 문장으로 바꾸는가를 본다.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from muwon.strategy.us_sector import 게이트정보

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "propose_buys.py"
_스펙 = importlib.util.spec_from_file_location("propose_buys_for_gate_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["propose_buys_for_gate_test"] = _모듈
_스펙.loader.exec_module(_모듈)

게이트진단글 = _모듈.게이트진단글


@dataclass
class _가짜껍데기:
    마지막게이트: 게이트정보 | None


@dataclass
class _가짜종목:
    name: str


이름표 = {"SEMI": "반도체", "BIO": "바이오"}
종목표 = {
    "005930": ("SEMI", _가짜종목("삼성전자")),
    "091990": ("BIO", _가짜종목("셀트리온헬스케어")),
}


def test_게이트가_없는_전략은_아무_말도_안_한다():
    assert 게이트진단글(_가짜껍데기(마지막게이트=None), 종목표, 이름표) == ""


def test_통과한_것이_있으면_아무_말도_안_한다():
    """이미 후보가 있으면 왜 없는지를 설명할 필요가 없다."""
    껍데기 = _가짜껍데기(마지막게이트=게이트정보(
        원래전략매수수=2, 통과매수수=1, 강한섹터=frozenset({"SEMI"}),
        막힌것=(("091990", "BIO"),),
    ))
    assert 게이트진단글(껍데기, 종목표, 이름표) == ""


def test_원래_전략_자체가_신호가_없으면_그렇게_말한다():
    껍데기 = _가짜껍데기(마지막게이트=게이트정보(
        원래전략매수수=0, 통과매수수=0, 강한섹터=frozenset(),
    ))
    글 = 게이트진단글(껍데기, 종목표, 이름표)

    assert "거래량 급증 조건 자체가 오늘 어떤 종목에서도 나지 않았습니다" in 글
    assert "미국 섹터" in 글


def test_신호는_났는데_미국_섹터에_막혔으면_종목과_섹터를_적는다():
    껍데기 = _가짜껍데기(마지막게이트=게이트정보(
        원래전략매수수=2, 통과매수수=0, 강한섹터=frozenset({"BIO"}),
        막힌것=(("005930", "SEMI"),),
    ))
    글 = 게이트진단글(껍데기, 종목표, 이름표)

    assert "거래량 급증 조건은 2종목에서 났습니다" in 글
    assert "삼성전자(005930)" in 글
    assert "바이오" in 글


def test_통과한_섹터가_없으면_그렇게_적는다():
    껍데기 = _가짜껍데기(마지막게이트=게이트정보(
        원래전략매수수=1, 통과매수수=0, 강한섹터=frozenset(),
        막힌것=(("005930", "SEMI"),),
    ))
    글 = 게이트진단글(껍데기, 종목표, 이름표)

    assert "오늘은 미국 섹터 조건을 통과한 섹터가 없습니다" in 글
