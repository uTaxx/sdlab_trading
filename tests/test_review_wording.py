"""전략 검토가 사람에게 보내는 문장에서 조사가 이름에 맞는지 본다.

## 왜 이 파일이 따로 있나

전략 이름은 설정에서 온다. 그래서 문장을 만드는 자리에서는 무엇이 올지
모르고, 조사를 글자로 박으면 이름에 따라 어색해진다.

같은 실수를 두 번 했다. 처음에는 총평에서 "변동성 돌파이 1주, 1개월
구간에서 함께 앞섭니다"가 나왔고(설계안 §38), 그때 받침을 보고 고르는
함수를 넣고 총평과 우위 배수 문장 둘을 고쳤다. 그런데 2026-09-01 17:50
첫 자동 실행에서 세 번째 자리가 드러났다. 예약이 걸려 있어 후보를 안 낼
때의 까닭이다.

    ■ 후보를 안 냅니다: 변동성 돌파이 이미 확정되어 반영을 기다리는 상태입니다.

이 문장은 텔레그램과 시트와 화면에 동시에 나간다. 그날은 후보가 없어서
사람이 읽는 문장이 사실상 이것 하나였다.

그래서 전략 이름이 들어가는 문장을 **등록된 전략 전부에 대해** 확인한다.
한 이름으로만 시험하면 그 이름의 받침에 맞는 것만 통과한다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from muwon.cloud.strategy_approval import 누르기
from muwon.db.models import Base
from muwon.strategy.registry import list_definitions
from muwon.text import 이가

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "run_strategy_review.py"
_스펙 = importlib.util.spec_from_file_location("run_strategy_review_wording", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["run_strategy_review_wording"] = _모듈
_스펙.loader.exec_module(_모듈)

막는까닭 = _모듈.막는까닭
전략이름 = _모듈.전략이름

오늘 = date(2026, 9, 1)
지금키 = "volume_surge_5d_ma20"
아는것 = [ㄱ.key for ㄱ in list_definitions()]


@pytest.fixture
def ㅅ():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


#: 지금 걸린 것과 같은 전략은 예약이 안 된다. 그래서 후보에서 뺀다.
예약할수있는것 = [ㄱ for ㄱ in 아는것 if ㄱ != 지금키]


@pytest.mark.parametrize("키", 예약할수있는것)
def test_예약이_걸렸을_때의_까닭에_조사가_맞는다(ㅅ, 키):
    누르기(ㅅ, 오늘, 오늘, 지금키, 키, 아는것, 근거구간="1개월", 등급="확인필요")

    글 = 막는까닭(ㅅ, 오늘, 최소운용일=30)
    이름 = 전략이름(키)

    assert 글, f"{키}: 예약이 걸렸는데 까닭이 비었습니다"
    assert 글.startswith(f"{이름}{이가(이름)} 이미 "), 글


def test_받침_없는_이름에_가가_붙는다(ㅅ):
    """실제로 새어 나간 것이 이 경우다. 변동성 돌파는 받침이 없다."""
    누르기(ㅅ, 오늘, 오늘, 지금키, "volatility_breakout_k05", 아는것,
         근거구간="1개월", 등급="확인필요")

    글 = 막는까닭(ㅅ, 오늘, 최소운용일=30)

    assert "변동성 돌파가 이미" in 글, 글
    assert "변동성 돌파이" not in 글, 글


def test_받침_있는_이름에_이가_붙는다(ㅅ):
    누르기(ㅅ, 오늘, 오늘, 지금키, "volume_surge_3d", 아는것,
         근거구간="1개월", 등급="확인필요")

    글 = 막는까닭(ㅅ, 오늘, 최소운용일=30)

    assert "거래량 급증 3일이 이미" in 글, 글


def test_예약이_없으면_까닭도_없다(ㅅ):
    """까닭이 비어 있어야 후보를 낸다. 여기가 늘 채워지면 검토가 아무것도
    제안하지 못한 채 매일 같은 문장만 보낸다."""
    assert 막는까닭(ㅅ, 오늘, 최소운용일=30) == ""
