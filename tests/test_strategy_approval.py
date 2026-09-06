"""전략 변경 예약의 상태 규칙.

## 왜 여기를 두껍게 시험하나

이 파일을 지나면 실제 매매 전략이 바뀐다. 그리고 **바뀌는 것이 조용하다.**
주문이 안 나가는 것도 아니고 워크플로가 빨개지는 것도 아니고, 다음 날부터
다른 전략으로 사고팔 뿐이다. 틀려도 며칠 뒤에나 알게 된다.

그래서 되는 조건보다 **안 되는 조건**을 더 많이 본다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from muwon.cloud.strategy_approval import (
    누르기,
    되돌림,
    막힘,
    막힘표시,
    반영,
    반영표시,
    반영할것,
    이력,
    지금예약,
    지난거래일수,
    취소,
    취소하기,
    확정,
)
from muwon.db.models import Base, StrategyChangeRow

아는것 = ["volume_surge_5d", "volume_surge_5d_ma20", "volume_surge_3d", "macd_cross"]
오늘 = date(2026, 9, 1)


@pytest.fixture
def ㅅ():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _누르기(ㅅ, 새전략="volume_surge_3d", 제안일=오늘, 이전="volume_surge_5d_ma20",
           오늘날=오늘):
    return 누르기(ㅅ, 제안일, 오늘날, 이전, 새전략, 아는것,
                근거구간="1개월,3개월", 등급="확인필요")


# ── 두 단계 ───────────────────────────────────────────────────


def test_한_번_누르면_예약된다():
    """2026-09-01에 두 단계에서 한 단계로 줄였다. 두 번 누르는 것이
    번거롭다는 지적을 받았다."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as ㅅ:
        결과 = _누르기(ㅅ)
        assert 결과.된것
        assert 지금예약(ㅅ).상태 == 확정
        줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것)
        assert 줄 is not None and 까닭 == ""


def test_같은_것을_다시_누르면_취소된다(ㅅ):
    """취소 버튼을 따로 두지 않는다. 같은 자리를 다시 누르는 것이 취소다."""
    _누르기(ㅅ, "volume_surge_3d")
    결과 = _누르기(ㅅ, "volume_surge_3d")
    assert 결과.된것
    assert 결과.줄 is None, "취소는 새 줄을 만들지 않습니다"
    assert 지금예약(ㅅ) is None
    assert "취소했습니다" in 결과.말


def test_취소한_뒤_또_누르면_다시_예약된다(ㅅ):
    """켜고 끄고를 되풀이할 수 있어야 한다. 한 번 끄면 끝이면 안 된다."""
    _누르기(ㅅ, "volume_surge_3d")
    _누르기(ㅅ, "volume_surge_3d")
    결과 = _누르기(ㅅ, "volume_surge_3d")
    assert 결과.된것
    assert 지금예약(ㅅ).상태 == 확정


def test_눌러도_그날_전략은_안_바뀐다(ㅅ):
    """손가락이 스치는 것에 대한 방어가 여기다. 반영은 다음 거래일이다."""
    결과 = _누르기(ㅅ)
    assert "다음 거래일" in 결과.말
    assert "다시 누르면 취소" in 결과.말
    assert 지금예약(ㅅ).반영때 is None


# ── 예약은 하나만 ─────────────────────────────────────────────


def test_다른_것을_누르면_앞의_예약이_취소된다(ㅅ):
    """둘을 동시에 예약하면 다음 날 무엇이 반영되는지 알 수 없다."""
    첫것 = _누르기(ㅅ, "volume_surge_3d").줄
    _누르기(ㅅ, "macd_cross")
    ㅅ.flush()
    assert 첫것.상태 == 취소
    assert "새로 선택해 취소" in 첫것.막힌까닭
    assert 지금예약(ㅅ).새전략 == "macd_cross"


def test_예약은_언제나_하나뿐이다(ㅅ):
    _누르기(ㅅ, "volume_surge_3d")
    _누르기(ㅅ, "macd_cross")
    _누르기(ㅅ, "volume_surge_5d")
    남은것 = ㅅ.query(StrategyChangeRow).filter_by(상태=확정).all()
    assert len(남은것) == 1
    assert 남은것[0].새전략 == "volume_surge_5d"


# ── 안 받는 것 ────────────────────────────────────────────────


def test_등록되지_않은_전략은_예약도_안_된다(ㅅ):
    """버튼 자료는 손으로 만들 수 있습니다."""
    결과 = 누르기(ㅅ, 오늘, 오늘, "volume_surge_5d", "없는전략", 아는것)
    assert not 결과.된것
    assert "등록되지 않은" in 결과.말
    assert 지금예약(ㅅ) is None


def test_이미_걸린_전략은_예약이_안_된다(ㅅ):
    결과 = 누르기(ㅅ, 오늘, 오늘, "volume_surge_3d", "volume_surge_3d", 아는것)
    assert not 결과.된것
    assert "이미 설정되어" in 결과.말


def test_어제_버튼은_안_듣는다(ㅅ):
    """어제 온 메시지의 버튼이 대화방에 그대로 살아 있습니다.

    두 단계였을 때는 확정 단계가 이걸 봤습니다. 한 단계가 되면서 누르기가
    봅니다. 이 검사가 빠지면 어제 판단으로 오늘 전략이 바뀝니다."""
    결과 = _누르기(ㅅ, 제안일=오늘 - timedelta(days=1))
    assert not 결과.된것
    assert "오늘 온 목록에서" in 결과.말
    assert 지금예약(ㅅ) is None


def test_어제_버튼으로_오늘_예약을_취소할_수도_없다(ㅅ):
    """취소도 같은 버튼이라 날짜 검사가 취소에도 걸려야 합니다."""
    _누르기(ㅅ, "volume_surge_3d")
    결과 = _누르기(ㅅ, "volume_surge_3d", 제안일=오늘 - timedelta(days=1))
    assert not 결과.된것
    assert 지금예약(ㅅ) is not None, "거절했으면 예약이 남아 있어야 합니다"


# ── 취소 ──────────────────────────────────────────────────────


def test_확정한_뒤에도_반영_전이면_취소할_수_있다(ㅅ):
    _누르기(ㅅ)
    결과 = 취소하기(ㅅ)
    assert 결과.된것
    assert 지금예약(ㅅ) is None
    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것)
    assert 줄 is None and 까닭 == ""


def test_취소할_것이_없으면_그렇게_말한다(ㅅ):
    결과 = 취소하기(ㅅ)
    assert not 결과.된것


# ── 반영할 때 다시 보는 것 ────────────────────────────────────


def test_예약과_반영_사이에_전략이_같아지면_안_바꾼다(ㅅ):
    """밤사이 워크플로로 손수 바꿨을 수 있습니다."""
    _누르기(ㅅ, "volume_surge_3d")
    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_3d", 아는것)
    assert 줄 is None
    assert "이미 설정되어" in 까닭


def test_예약한_전략이_목록에서_사라지면_안_바꾼다(ㅅ):
    _누르기(ㅅ, "volume_surge_3d")
    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", ["volume_surge_5d_ma20"])
    assert 줄 is None
    assert "목록에 없습니다" in 까닭


def test_최소_운용기간이_안_지났으면_막는다(ㅅ):
    """이번 달 성적이 나쁘니 바꾸자를 막는 자리입니다."""
    ㅅ.add(
        StrategyChangeRow(
            제안일=오늘 - timedelta(days=10),
            상태=반영,
            이전전략="macd_cross",
            새전략="volume_surge_5d_ma20",
            반영때=datetime(2026, 8, 22),  # noqa: DTZ001 (기록용, tz 무관)
        )
    )
    ㅅ.flush()
    _누르기(ㅅ, "volume_surge_3d")

    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=30)
    assert 줄 is None
    assert "10일이 지났습니다" in 까닭
    assert "최소 운용기간 30일" in 까닭

    # 기간이 지났으면 통과한다.
    줄2, 까닭2 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=5)
    assert 줄2 is not None and 까닭2 == ""


def test_반영_기록이_없으면_최소_운용기간에_안_걸린다(ㅅ):
    """한 번도 안 바꿔 봤으면 막을 근거가 없습니다."""
    _누르기(ㅅ)
    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=30)
    assert 줄 is not None and 까닭 == ""


def test_지난거래일수는_반영_기록이_없으면_None이다():
    assert 지난거래일수(None, 오늘) is None
    줄 = StrategyChangeRow(제안일=오늘, 상태=반영, 반영때=None)
    assert 지난거래일수(줄, 오늘) is None


# ── 막힌 것을 남긴다 ──────────────────────────────────────────


def test_막히면_상태로_남아_다음_회차에_다시_안_한다(ㅅ):
    """매일 같은 이유로 막히는 것을 매일 알리면 알림이 흔해집니다."""
    _누르기(ㅅ)
    막힌줄 = 막힘표시(ㅅ, "시세를 못 받았습니다.")
    assert 막힌줄.상태 == 막힘
    assert 막힌줄.막힌까닭 == "시세를 못 받았습니다."
    assert 지금예약(ㅅ) is None


# ── 이력 ──────────────────────────────────────────────────────


def test_이력에는_반영된_것만_남는다(ㅅ):
    """고르다 만 것과 취소한 것은 판단 과정이지 변경 이력이 아닙니다."""
    _누르기(ㅅ, "volume_surge_3d")
    취소하기(ㅅ)
    _누르기(ㅅ, "macd_cross")
    반영표시(ㅅ, 지금예약(ㅅ))

    줄들 = 이력(ㅅ)
    assert [ㄱ.새전략 for ㄱ in 줄들] == ["macd_cross"]
    assert 줄들[0].상태 == 반영
    assert 줄들[0].반영때 is not None


def test_이력에_왜_바꿨는지가_같이_남는다(ㅅ):
    """바꾼 시각만으로는 그때 왜 바꿨는지에 답할 수 없습니다."""
    누르기(
        ㅅ, 오늘, 오늘, "volume_surge_5d_ma20", "volume_surge_3d", 아는것,
        근거구간="1개월,3개월", 등급="확인필요",
        이전수익률=-8.2, 새수익률=12.4, 거래수=41,
        사유="[3개월] 거래량 급증 3일 +12.40% (거래 41건).",
    )
    반영표시(ㅅ, 지금예약(ㅅ))

    줄 = 이력(ㅅ)[0]
    assert 줄.근거구간 == "1개월,3개월"
    assert 줄.등급 == "확인필요"
    assert 줄.이전수익률 == -8.2 and 줄.새수익률 == 12.4 and 줄.거래수 == 41
    assert "거래 41건" in 줄.사유


def test_되돌린_것도_이력에_남는다(ㅅ):
    _누르기(ㅅ)
    줄 = 지금예약(ㅅ)
    반영표시(ㅅ, 줄)
    줄.상태 = 되돌림
    ㅅ.flush()
    assert [ㄱ.상태 for ㄱ in 이력(ㅅ)] == [되돌림]


# ── 30일 제한은 기계가 낸 후보에만 건다 (2026-09-05) ──────────────
#
# ## 무엇을 막으려던 규칙인가
#
# 매일 저녁 검토가 "오늘은 이 전략이 1위입니다" 하고 후보를 낸다. 그것을
# 매번 받아들이면 그때그때 성적 좋은 것으로 계속 갈아타게 되고, 그 방식은
# 이미 계산해서 기각했다. 기계가 매일 조르는 것을 막으려고 둔 값이다.
#
# ## 그런데 주인까지 막고 있었다
#
# 화면에서 전략을 바꿀 수 있게 하고 나니 그 길도 같은 제한에 걸렸다.
# 주인이 근거를 보고 직접 고른 것까지 30일 막는 것은 규칙의 뜻과 다르다.


def _반영기록(ㅅ, 며칠전):
    ㅅ.add(StrategyChangeRow(
        제안일=오늘 - timedelta(days=며칠전),
        상태=반영,
        이전전략="macd_cross",
        새전략="volume_surge_5d_ma20",
        반영때=datetime(2026, 9, 1) - timedelta(days=며칠전),  # noqa: DTZ001
    ))
    ㅅ.flush()


@pytest.mark.parametrize("경로", ["화면", "대화"])
def test_사람이_직접_고른_것은_30일에_안_걸린다(ㅅ, 경로):
    """목록을 보고 스스로 고른 것이다. 기계가 조르는 것이 아니다."""
    _반영기록(ㅅ, 2)
    누르기(ㅅ, 오늘, 오늘, "volume_surge_5d_ma20", "volume_surge_3d", 아는것,
         승인경로=경로)

    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=30)
    assert 줄 is not None, 까닭
    assert 까닭 == ""


def test_텔레그램_버튼은_30일을_그대로_받는다(ㅅ):
    """저녁 검토가 낸 후보를 받아들이는 길이다. 매일 조르는 것을 막는
    자리라 제한이 그대로 있어야 한다."""
    _반영기록(ㅅ, 2)
    누르기(ㅅ, 오늘, 오늘, "volume_surge_5d_ma20", "volume_surge_3d", 아는것,
         승인경로="텔레그램")

    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=30)
    assert 줄 is None
    assert "최소 운용기간 30일" in 까닭


def test_경로를_안_적으면_텔레그램으로_본다(ㅅ):
    """옛 줄에는 이 칸이 비어 있다. 빈 칸을 제한 없음으로 읽으면 이미
    쌓인 예약이 갑자기 제한을 벗어난다. 막는 쪽으로 기울인다."""
    _반영기록(ㅅ, 2)
    _누르기(ㅅ, "volume_surge_3d")

    줄, 까닭 = 반영할것(ㅅ, 오늘, "volume_surge_5d_ma20", 아는것, 최소운용일=30)
    assert 줄 is None
    assert "최소 운용기간 30일" in 까닭


def test_제한없는경로_목록을_고정한다():
    """여기 값을 늘리는 것은 실제 주문 규칙을 바꾸는 일이다. 한 번 물어야
    한다."""
    from muwon.cloud.strategy_approval import 제한없는경로

    assert set(제한없는경로) == {"화면", "대화"}


# ── 예약 취소가 저장되는가 ─────────────────────────────────────────
#
# 2026-09-06에 실제로 겪었다. 같은 전략을 두 번 예약하면 두 번째는 취소로
# 읽히는데, 스크립트가 그것을 종료 코드 1로 끝냈다. 그러면 워크플로가
# 빨개지고 **상태 DB를 올리는 단계가 건너뛰어져서 취소가 저장되지 않는다.**
# 화면에는 "취소했습니다"라고 찍히는데 다음 날 08:20에 그대로 반영된다.


def test_취소도_성공으로_끝낸다():
    from pathlib import Path

    글 = (Path(__file__).resolve().parent.parent / "scripts" / "switch_strategy.py"
          ).read_text(encoding="utf-8")

    assert "if not 결과.된것:" in 글, "못 한 것과 취소를 갈라야 한다"
    assert "취소했습니다" in 글
    # 취소 갈래가 0으로 끝나야 워크플로가 DB를 올린다.
    자리 = 글.index("취소했습니다")
    뒤 = 글[자리:자리 + 400]
    assert "return 0" in 뒤, "취소는 성공으로 끝내야 상태 DB가 올라간다"


def test_상태DB_올리기가_실패해도_돈다():
    """파이썬이 DB에 쓰고 나서 실패로 끝나는 길이 있다."""
    from pathlib import Path

    import yaml

    길 = (Path(__file__).resolve().parent.parent / ".github" / "workflows"
          / "switch-strategy.yml")
    문서 = yaml.safe_load(길.read_text(encoding="utf-8"))
    올리기 = [
        ㄷ for ㅈ in 문서["jobs"].values() for ㄷ in ㅈ.get("steps", [])
        if "상태 DB 올리기" in str(ㄷ.get("name", ""))
    ]
    assert 올리기, "상태 DB 올리는 단계가 있어야 한다"
    assert "always()" in str(올리기[0].get("if", "")), (
        "앞 단계가 실패해도 DB는 올려야 한다. 안 그러면 쓴 것이 사라진다"
    )
