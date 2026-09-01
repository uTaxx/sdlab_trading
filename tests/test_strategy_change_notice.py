"""아침 매수 후보 알림이 오늘 전략이 바뀌었는지 알려 주는가.

## 왜 후보 알림에 붙였나

08:20 반영은 바꿨을 때와 막혔을 때만 알린다. 아무 일도 없는 날이 대부분인데
매일 "오늘도 안 바꿨습니다"를 따로 보내면 알림이 흔해지고, 흔해진 알림은
진짜일 때도 안 읽힌다. 그 판단은 `apply_strategy_change.py` 머리에 적혀 있다.

그런데 그러면 아침에 받은 후보가 어느 전략으로 나온 것인지 알 방법이 없다.
2026-09-01에 그것을 알려면 GitHub Actions 로그를 열어야 했다.

그래서 이미 매일 나가는 08:30 후보 알림 안에 한 줄로 넣는다. 알림 수는
안 늘고, 후보를 승인할 그 자리에서 같이 읽힌다.

## 여기서 제일 조심하는 것

**시각 칸이 UTC다.** 08:20 한국시각은 UTC로 전날 23:20이라, 날짜를 그냥
비교하면 오늘 바꾼 것을 어제 것으로 읽고 "변경 없음"이라고 알린다. 그러면
전략이 바뀐 날 사람은 안 바뀐 줄 안다. 조용히 틀리는 쪽이라 따로 시험한다.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from muwon.cloud.approval import 변경없음글, 전략변경글, 키를이름으로
from muwon.cloud.strategy_approval import 막힘, 반영, 오늘변경
from muwon.db.models import Base, StrategyChangeRow

오늘 = date(2026, 9, 2)


@pytest.fixture
def ㅅ():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _줄(ㅅ, 상태, 때, *, 이전="volume_surge_5d_ma20", 새것="volatility_breakout_k05",
       까닭=""):
    줄 = StrategyChangeRow(
        제안일=오늘, 상태=상태, 이전전략=이전, 새전략=새것, 막힌까닭=까닭,
    )
    if 상태 == 반영:
        줄.반영때 = 때
    else:
        줄.바뀐때 = 때
    ㅅ.add(줄)
    ㅅ.flush()
    return 줄


# ── 시각이 UTC라는 것 ─────────────────────────────────────────


def test_한국시각_08시20분에_바꾼_것을_오늘로_읽는다(ㅅ):
    """여기가 이 파일의 핵심이다. 08:20 KST는 UTC로 전날 23:20이다.
    날짜를 그냥 비교하면 어제 것으로 읽고 변경 없음이라고 알린다."""
    _줄(ㅅ, 반영, datetime(2026, 9, 1, 23, 20))  # noqa: DTZ001 (UTC 기록값)

    assert 오늘변경(ㅅ, 오늘) is not None


def test_진짜_어제_바꾼_것은_오늘이_아니다(ㅅ):
    _줄(ㅅ, 반영, datetime(2026, 8, 31, 23, 20))  # noqa: DTZ001 (UTC 기록값)

    assert 오늘변경(ㅅ, 오늘) is None


def test_아무_기록도_없으면_없다(ㅅ):
    assert 오늘변경(ㅅ, 오늘) is None


def test_막힌_줄도_찾는다(ㅅ):
    """막힌 날은 전략이 안 바뀐 채로 후보가 나온다. 바꾼 날보다 더 중요하다."""
    _줄(ㅅ, 막힘, datetime(2026, 9, 1, 23, 20), 까닭="예약이 확정되지 않았습니다.")  # noqa: DTZ001

    줄 = 오늘변경(ㅅ, 오늘)

    assert 줄 is not None
    assert 줄.상태 == 막힘


def test_반영과_막힘이_다른_칸에_적혀도_둘_다_본다(ㅅ):
    """반영한 줄은 반영때에, 막힌 줄은 바뀐때에 적힌다. 한쪽만 보면
    다른 쪽을 통째로 놓친다."""
    _줄(ㅅ, 반영, datetime(2026, 8, 20, 23, 20))  # noqa: DTZ001
    _줄(ㅅ, 막힘, datetime(2026, 9, 1, 23, 20), 까닭="이미 설정되어 있습니다.")  # noqa: DTZ001

    줄 = 오늘변경(ㅅ, 오늘)

    assert 줄 is not None and 줄.상태 == 막힘


# ── 사람에게 가는 문장 ────────────────────────────────────────


def test_안_바뀐_날도_그렇다고_적는다():
    """변경 여부를 묻는 것이므로 안 바뀐 날에도 답이 있어야 한다."""
    글 = 전략변경글(None)

    assert 글 == 변경없음글
    assert "없음" in 글


def test_바뀐_날은_무엇에서_무엇으로인지_적는다(ㅅ):
    줄 = _줄(ㅅ, 반영, datetime(2026, 9, 1, 23, 20))  # noqa: DTZ001

    글 = 전략변경글(줄)

    assert "거래량 급증 + 20일선에서" in 글
    assert "변동성 돌파로 바꿨습니다" in 글


def test_바뀐_날의_조사가_이름에_맞는다(ㅅ):
    """받침이 없는 이름에 으로를 붙이면 변동성 돌파으로가 된다.
    2026-09-01에 같은 실수를 두 번 겪었다."""
    받침없음 = _줄(ㅅ, 반영, datetime(2026, 9, 1, 23, 20), 새것="volatility_breakout_k05")  # noqa: DTZ001
    assert "변동성 돌파로" in 전략변경글(받침없음)
    assert "변동성 돌파으로" not in 전략변경글(받침없음)

    받침있음 = _줄(ㅅ, 반영, datetime(2026, 9, 1, 23, 30), 새것="volume_surge_3d")  # noqa: DTZ001
    assert "거래량 급증 3일로" in 전략변경글(받침있음)


def test_막힌_날은_경고와_까닭을_적는다(ㅅ):
    줄 = _줄(ㅅ, 막힘, datetime(2026, 9, 1, 23, 20),  # noqa: DTZ001
           까닭="직전 변경으로부터 3일이 지났습니다.")

    글 = 전략변경글(줄)

    assert "막혔습니다" in 글
    assert "직전 변경으로부터 3일" in 글
    assert "이전 전략으로 계산한 것" in 글, "옛 전략으로 나온 후보라는 사실이 빠지면 안 됩니다"


def test_막힌_까닭에_전략_키가_그대로_새지_않는다(ㅅ):
    """막힌 까닭은 f"...{줄.새전략}"으로 만들어져 키가 섞인다.
    volume_surge_3d는 처음 보는 사람에게 아무 뜻도 없다."""
    줄 = _줄(ㅅ, 막힘, datetime(2026, 9, 1, 23, 20),  # noqa: DTZ001
           까닭="예약한 전략이 목록에 없습니다: volume_surge_3d")

    글 = 전략변경글(줄)

    assert "volume_surge_3d" not in 글
    assert "거래량 급증 3일" in 글


def test_긴_키를_먼저_바꾼다():
    """volume_surge_5d를 먼저 바꾸면 volume_surge_5d_ma20이 반쪽만 바뀐다."""
    글 = 키를이름으로("지금 volume_surge_5d_ma20을 씁니다")

    assert "_ma20" not in 글
    assert "거래량 급증 + 20일선" in 글
