"""**텔레그램에 안 먹는 기호가 알림에 다시 들어오는 것**을 막는다.

`TelegramNotifier.send`는 HTML 모드로 보낸다. 그건 대시보드 링크 하나를
걸기 위한 것이고, 마크다운은 여전히 안 먹는다. 그래서 `**굵게**`가 강조가
아니라 **별 네 개 그대로** 도착한다. `★`도 마찬가지 — 무슨 뜻인지 아무도
모른다.

이 실수를 2026-08-25~26에 **세 번** 했다. 시장 리포트에서 한 번, 매매
알림에서 한 번, 매수 결과에서 또 한 번. 사람이 매번 기억하는 것으로는
안 막힌다는 뜻이라, 알림을 만드는 함수를 전부 여기서 한꺼번에 훑는다.

새 알림을 만들면 아래 목록에 한 줄 넣는 것으로 같이 지켜진다.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from muwon.cloud.approval import 알림글 as 승인알림글
from muwon.cloud.approval import 후보
from muwon.domain.types import OrderResult, OrderSide
from muwon.execution.engine import ExecutedAction, 매도알림, 매수알림
from muwon.execution.fill_settle import 보유고침, 주문고침
from muwon.market.analog import Baseline, Forecast
from muwon.market.digest import summarize
from scripts.execute_approved import _알림글 as 매수결과글
from scripts.settle_fills import _알림글 as 정산글

#: 텔레그램이 그대로 흘려보내는 것들. 화면에 기호가 그냥 찍힌다.
안먹는것 = ("**", "__", "★", "`")

오늘 = date(2026, 8, 26)


def _주문(방향=OrderSide.BUY):
    return OrderResult(
        symbol="066970", side=방향, quantity=12, price=118300.0, order_id="1",
        is_paper=True, reference_price=121600.0, fill_confirmed=True,
    )


def _후보():
    return 후보(symbol="103140", name="풍산", strategy="volume_surge_5d",
               quantity=9, price=150000, reason="거래량 2배 급증 + 3.4% 상승",
               sector_name="철강")


def _전망():
    return Forecast(
        기준일=오늘, 대상="반도체", 구간수=16, 총일수=200, 지평=20,
        중앙값=2.0, 상위25=6.0, 하위25=-1.0, 하위10=-8.0, 상승확률=70.0,
        구간들=[], 기준선=Baseline(표본수=1000, 중앙값=1.0, 하위10=-9.0, 상승확률=55.0),
    )


def _상태():
    return pd.DataFrame(
        {"kospi_추세20": [0.8], "kospi_고점대비": [-2.8], "kospi_변동성": [1.3]},
        index=[오늘],
    )


#: (이름, 그 알림을 실제로 만들어 내는 함수)
알림들 = [
    ("매수 체결", lambda: 매수알림("엘앤에프", "066970", _주문(), "거래량 급증", 손절비율=-0.05)),
    ("매수 체결(ATR)", lambda: 매수알림("엘앤에프", "066970", _주문(), "거래량 급증", atr손절=True)),
    ("매도 체결", lambda: 매도알림("엘앤에프", "066970", _주문(OrderSide.SELL), "손절",
                              진입가=125000.0, 진입일=오늘, 판날=오늘)),
    ("매수 후보 제안", lambda: 승인알림글([_후보()], 오늘, "https://예시", 살펴본수=45,
                                 전략="volume_surge_5d", 섹터요약="철강 강")),
    ("후보 없는 날", lambda: 승인알림글([], 오늘, "https://예시", 살펴본수=45)),
    ("후보 없고 시세도 없는 날", lambda: 승인알림글([], 오늘, "https://예시", 살펴본수=0)),
    ("매수 결과 — 다 삼", lambda: 매수결과글(
        오늘, [_후보()],
        [ExecutedAction("103140", "풍산", OrderSide.BUY, 9, 150000, "거래량 급증")],
        [], [], True)),
    ("매수 결과 — 못 삼", lambda: 매수결과글(
        오늘, [_후보()], [], [], [_후보()], True,
        거부사유={"103140": "주문가능현금이 모자랍니다"})),
    ("매수 결과 — 스위치 꺼짐", lambda: 매수결과글(오늘, [_후보()], [], [], [_후보()], False)),
    ("장 마감 정산", lambda: 정산글(
        오늘,
        [주문고침(order_id="1", symbol="066970", 옛수량=4, 새수량=12,
                옛체결가=118300.0, 새체결가=118241.0, 판단가=121600.0,
                종목명="엘앤에프", 방향="BUY")],
        [보유고침(symbol="066970", 옛수량=4, 새수량=12,
                옛진입가=118300.0, 새진입가=118241.0)],
        손절비율=-0.05)),
    ("시장 리포트", lambda: summarize(_상태(), [_전망()], 오늘, 렌즈="확장")),
]


@pytest.mark.parametrize(("이름", "만들기"), 알림들, ids=[ㄱ for ㄱ, _ in 알림들])
def test_텔레그램에_안_먹는_기호가_없다(이름, 만들기):
    글 = 만들기()
    있는것 = [ㄱ for ㄱ in 안먹는것 if ㄱ in 글]

    assert not 있는것, (
        f"'{이름}' 알림에 {있는것}가 들어 있습니다. "
        "텔레그램은 마크다운을 안 읽으므로 기호가 그대로 찍힙니다 — 말로 쓰세요."
    )


@pytest.mark.parametrize(("이름", "만들기"), 알림들, ids=[ㄱ for ㄱ, _ in 알림들])
def test_줄표를_쓰지_않는다(이름, 만들기):
    """줄표(—)는 영어 글의 습관이다. 한국어 문장에 자주 넣으면 번역체가 된다.

    2026-08-26에 커밋 12개(338줄)에서 46개가 나왔고, 시장 리포트 한 통에만
    7개가 있었다. 쉼표와 마침표로 끊으면 그대로 읽힌다."""
    있는줄 = [ㄹ for ㄹ in 만들기().splitlines() if "—" in ㄹ]

    assert not 있는줄, (
        f"'{이름}' 알림에 줄표가 있습니다: {있는줄[:2]}. "
        "쉼표나 마침표로 끊거나, 이름표 뒤라면 쌍점(:)을 쓰세요."
    )


@pytest.mark.parametrize(("이름", "만들기"), 알림들, ids=[ㄱ for ㄱ, _ in 알림들])
def test_영어_코드_이름이_사용자에게_안_나간다(이름, 만들기):
    """`volume_surge_5d` 같은 내부 이름은 처음 보는 사람에게 아무 뜻도 없다."""
    글 = 만들기()

    assert "volume_surge" not in 글
    assert "_" not in 글.replace("─", ""), "밑줄이 들어간 내부 이름이 샜을 수 있습니다"


# ── 체결 알림의 모양 ────────────────────────────────────────────────────
#
# 라벨과 값을 콜론으로 가른다. 문장으로 풀어 쓰면 찾는 숫자가 매번 다른
# 자리에 있어서, 훑어보는 글로는 못 쓴다. 알림을 만드는 자리가 넷이라
# 하나만 문장으로 돌아가도 알아채기 어렵다.

체결알림들 = [ㄱ for ㄱ in 알림들 if "체결" in ㄱ[0] or "정산" in ㄱ[0] or "매수 결과 — 다 삼" == ㄱ[0]]


@pytest.mark.parametrize(("이름", "만들기"), 체결알림들, ids=[ㄱ for ㄱ, _ in 체결알림들])
def test_체결_알림은_라벨과_값을_콜론으로_가른다(이름, 만들기):
    글 = 만들기()

    assert "수량 : " in 글, f"'{이름}'에 수량 칸이 없습니다"
    assert "단가 : " in 글, f"'{이름}'에 단가 칸이 없습니다"


@pytest.mark.parametrize(("이름", "만들기"), 체결알림들, ids=[ㄱ for ㄱ, _ in 체결알림들])
def test_체결_알림에_1주값과_총액이_둘_다_있다(이름, 만들기):
    """`가격: 118,300원` 하나만 있으면 1주 값인지 합친 값인지 알 수 없다.
    실제로 그렇게 보냈고, 받아 보는 사람이 그 질문을 했다."""
    글 = 만들기()

    assert "원/주" in 글, f"'{이름}'의 단가에 단위가 없습니다"
    assert "총액 : " in 글, f"'{이름}'에 총액 칸이 없습니다"
