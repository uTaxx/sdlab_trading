"""버튼을 누른 것부터 전략이 바뀌는 데까지, 한 줄로 이어 본다.

## 왜 이 시험이 따로 있나

조각마다 시험이 이미 있다. 버튼 자료를 만드는 것, 자료를 푸는 것, 상태를
바꾸는 것이 각각 통과한다. 그런데 **조각이 다 맞아도 이어지지 않을 수 있다.**
버튼이 내보내는 전략 키와 예약이 받는 전략 키가 다른 이름이면 세 시험이
전부 통과하면서 아무 일도 안 일어난다.

이 저장소에서 이미 겪은 모양이다. 창구가 `잰때`를 `재때`로 보내고 화면은
`잰때`를 읽어서, 워크플로는 초록불이고 표만 비어 있었다.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from muwon.cloud import strategy_approval as 승인
from muwon.db.models import Base
from muwon.notify.telegram_buttons import (
    parse_callback,
    고른것표시,
    고른표,
    전략버튼,
    전략키보드,
)
from muwon.strategy.registry import list_definitions

오늘 = date(2026, 9, 1)
지금키 = "volume_surge_5d_ma20"
고를키 = "volume_surge_3d"


@pytest.fixture
def ㅅ():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _아는것():
    return [ㅈ.key for ㅈ in list_definitions()]


def _누르기(판, 몇번째: int = 0):
    """버튼 판에서 콜백 자료를 꺼내 실제로 눌린 것처럼 푼다."""
    칸 = [ㄱ for 줄 in 판["inline_keyboard"] for ㄱ in 줄][몇번째]
    return parse_callback(칸["callback_data"])


def test_버튼에서_반영까지_한_줄로_이어진다(ㅅ):
    """버튼이 내보내는 키와 예약이 받는 키가 같아야 한다."""
    후보 = 전략버튼(키=고를키, 이름="거래량 급증 3일", 구간="3개월", 수익률=12.4)
    판 = 전략키보드([후보], 오늘)

    # ① 한 번 누르면 예약된다.
    c = _누르기(판)
    assert c.종류 == "전략고름" and c.전략키 == 고를키
    결과 = 승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, _아는것())
    assert 결과.된것, 결과.말
    assert 승인.지금예약(ㅅ).상태 == 승인.확정

    # ② 판에 표시가 붙어 무엇을 골랐는지 보인다.
    표시된판 = 고른것표시(판, c.전략키)
    assert 표시된판["inline_keyboard"][0][0]["text"].startswith(고른표)

    # ③ 다음 거래일에 반영한다.
    줄, 까닭 = 승인.반영할것(ㅅ, 오늘, 지금키, _아는것())
    assert 줄 is not None and 까닭 == ""
    assert 줄.새전략 == 고를키

    승인.반영표시(ㅅ, 줄)
    assert [ㄱ.새전략 for ㄱ in 승인.이력(ㅅ)] == [고를키]


def test_같은_버튼을_다시_누르면_예약이_사라진다(ㅅ):
    """취소 버튼을 따로 두지 않는다. 같은 자리가 취소다."""
    판 = 전략키보드([전략버튼(키=고를키, 이름="거래량 급증 3일", 구간="3개월")], 오늘)
    c = _누르기(판)

    승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, _아는것())
    결과 = 승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, _아는것())

    assert 결과.된것
    assert 승인.지금예약(ㅅ) is None
    줄, 까닭 = 승인.반영할것(ㅅ, 오늘, 지금키, _아는것())
    assert 줄 is None and 까닭 == ""
    # 표시도 같이 사라져야 한다. 남으면 취소했는데 예약된 것처럼 보인다.
    assert not 고른것표시(고른것표시(판, c.전략키), "")[
        "inline_keyboard"][0][0]["text"].startswith(고른표)


def test_옛_메시지의_확인_버튼도_같은_일을_한다(ㅅ):
    """두 단계였을 때 보낸 메시지가 대화방에 남아 있다. 그 확인 버튼을
    눌러도 지금 규칙대로 예약되어야 한다."""
    c = parse_callback(f"c|{오늘}|{고를키}")
    assert c.종류 == "전략확정"
    결과 = 승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, _아는것())
    assert 결과.된것
    assert 승인.지금예약(ㅅ).상태 == 승인.확정


def test_어제_버튼을_눌러도_오늘_안_바뀐다(ㅅ):
    """어제 온 메시지의 버튼이 그대로 살아 있다."""
    어제 = 오늘 - timedelta(days=1)
    판 = 전략키보드([전략버튼(키=고를키, 이름="거래량 급증 3일", 구간="3개월")], 어제)
    c = _누르기(판)
    assert c.날짜 == 어제

    결과 = 승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, _아는것())
    assert not 결과.된것
    assert "오늘 온 목록에서" in 결과.말
    assert 승인.지금예약(ㅅ) is None


def test_구간마다_다른_전략이면_버튼이_여럿이고_하나만_예약된다(ㅅ):
    """둘을 동시에 예약하면 다음 날 무엇이 반영되는지 알 수 없다."""
    판 = 전략키보드(
        [
            전략버튼(키="volume_surge_3d", 이름="거래량 급증 3일", 구간="1주"),
            전략버튼(키="macd_cross", 이름="MACD 교차", 구간="3개월"),
        ],
        오늘,
    )
    칸들 = [ㄱ for 줄 in 판["inline_keyboard"] for ㄱ in 줄]
    assert len(칸들) == 2

    첫째 = _누르기(판, 0)
    승인.누르기(ㅅ, 첫째.날짜, 오늘, 지금키, 첫째.전략키, _아는것())
    둘째 = _누르기(판, 1)
    승인.누르기(ㅅ, 둘째.날짜, 오늘, 지금키, 둘째.전략키, _아는것())

    남은것 = 승인.지금예약(ㅅ)
    assert 남은것.새전략 == 둘째.전략키
    살아있는수 = sum(
        1 for ㄱ in ㅅ.query(type(남은것)).all() if ㄱ.상태 in 승인.살아있는것
    )
    assert 살아있는수 == 1


def test_등록된_전략_전부가_버튼과_예약을_통과한다(ㅅ):
    """새 전략을 등록했을 때 그것만 버튼이 안 만들어지는 일을 막는다."""
    아는것 = _아는것()
    for ㅈ in list_definitions():
        if ㅈ.key == 지금키:
            continue
        c = _누르기(전략키보드([전략버튼(키=ㅈ.key, 이름=ㅈ.화면이름, 구간="3개월")], 오늘))
        assert c.종류 == "전략고름", f"{ㅈ.key}: 버튼 자료를 못 읽습니다"
        결과 = 승인.누르기(ㅅ, c.날짜, 오늘, 지금키, c.전략키, 아는것)
        assert 결과.된것, f"{ㅈ.key}: {결과.말}"
