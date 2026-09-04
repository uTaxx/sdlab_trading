"""전략 설명이 주식을 처음 하는 사람도 읽을 수 있는가.

## 왜 필요한가

전략마다 이미 한 줄 설명(`description`)이 있다. 그런데 이렇게 적혀 있다.

    단기선 상향돌파+거래량급증 또는 RSI 과매도반등 매수,
    단기선 하향이탈/RSI 과매수 매도.

처음 보는 사람은 한 단어도 못 읽는다. 뜻을 모르는 줄은 안 읽는 줄이고,
안 읽는 줄은 없는 줄이다. 그래서 `쉬운설명`을 따로 둔다.

## 여기서 막는 것 둘

**하나, 전문용어가 다시 들어오는 것.** 쉽게 쓰기는 어렵고 어렵게 쓰기는
쉽다. 전략을 하나 더할 때 원래 설명을 복사해 오면 그 자리만 조용히
어려워진다.

**둘, 새 전략에 설명을 안 붙이는 것.** 안 붙이면 화면의 그 자리가 빈다.
빈 것은 눈에 잘 안 띄고, 안 띄면 영영 안 채워진다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muwon.strategy.registry import REGISTRY, list_definitions

뿌리 = Path(__file__).resolve().parent.parent

#: 이 말이 나오면 처음 보는 사람은 못 읽는다. 정확한 조건을 적는
#: `strategy_rules.describe()`는 이 말을 써도 된다. 거기는 이미 뜻을
#: 아는 사람이 값을 확인하는 자리다.
#:
#: ## 목록에서 뺀 것 (2026-09-03)
#:
#: 종가·시가·고가·저가·거래량을 뺐다. 주인이 "신문 기사와 대조해서 고치라"고
#: 했고, 이 다섯은 증시 기사가 그대로 쓰는 말이다. 막아 두었더니 그 자리를
#: "마감 값", "사고팔린 양" 같은 말로 채우게 됐는데, 그것이 오히려 사전에도
#: 없고 기사에도 없는 말이었다. 주인이 지적한 것이 정확히 그런 말이다.
#:
#: 다섯 다 용어 사전에 뜻이 있다(`dashboard/glossary.py`). 화면에서 용어
#: 안내 탭으로 찾을 수 있으므로, 모르는 채로 남지 않는다.
#:
#: **나머지는 그대로 막는다.** RSI, 과매수, 다이버전스처럼 기사에도 잘 안
#: 나오고 뜻을 모르면 문장 전체가 안 읽히는 말이다.
어려운말 = [
    "이동평균", "이평", "단기선", "장기선", "20일선", "60일선", "10일선",
    "RSI", "MACD", "EMA", "ADX", "볼린저", "스토캐스틱", "밴드",
    "과매수", "과매도", "상향돌파", "하향이탈", "골든크로스", "데드크로스",
    "평균회귀", "모멘텀", "추세추종", "돌파 매수", "신고가", "신저가",
    "갭", "눌림목", "횡보",
    "지표", "시그널", "다이버전스", "오실레이터",
]


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda ㄷ: ㄷ.key)
def test_전략마다_쉬운_설명이_있다(정의):
    """없으면 화면의 그 자리가 빈다. 빈 것은 눈에 안 띈다."""
    assert 정의.쉬운설명.strip(), f"{정의.key}에 쉬운설명이 없습니다"


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda ㄷ: ㄷ.key)
def test_쉬운_설명에_전문용어를_안_쓴다(정의):
    글 = 정의.쉬운설명 + " " + 정의.쉬운참고
    # 전략 이름을 가리키는 것은 봐준다. "골든크로스 20/60과 같은데"처럼
    # 다른 전략을 짚는 자리가 있고, 그건 이름이지 용어가 아니다.
    이름들 = [ㄷ.화면이름 for ㄷ in REGISTRY]
    남은 = 글
    for 이름 in sorted(이름들, key=len, reverse=True):
        남은 = 남은.replace(이름, "")

    나온것 = [ㅁ for ㅁ in 어려운말 if ㅁ in 남은]
    assert not 나온것, f"{정의.key}: {나온것}\n  {글}"


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda ㄷ: ㄷ.key)
def test_쉬운_설명에_숫자를_안_박는다(정의):
    """20일이냐 60일이냐는 파라미터에서 자동으로 만들어지는 설명이 정확히
    적는다. 여기에 손으로 적으면 파라미터를 바꿨을 때 조용히 어긋난다."""
    import re

    # 전략 이름에 든 숫자(골든크로스 20/60)는 빼고 본다.
    남은 = 정의.쉬운설명 + " " + 정의.쉬운참고
    for 이름 in sorted([ㄷ.화면이름 for ㄷ in REGISTRY], key=len, reverse=True):
        남은 = 남은.replace(이름, "")

    # 정의에 적힌 익절선도 빼고 본다. 그 값으로 문장을 만들기 때문에
    # (`registry._익절설명`) 손으로 적은 숫자와 달리 어긋날 수 없다.
    # 값을 고치면 문장이 같이 바뀐다.
    if 정의.익절:
        남은 = 남은.replace(f"{정의.익절 * 100:g}%", "")

    assert not re.search(r"\d+\s*(일|배|%|퍼센트)", 남은), 남은


def test_사람에게_가는_글에_줄표를_안_쓴다():
    for 정의 in REGISTRY:
        for 글 in (정의.쉬운설명, 정의.쉬운참고):
            assert "—" not in 글, f"{정의.key}: {글}"
            assert "–" not in 글, f"{정의.key}: {글}"


def test_설명이_존댓말_평서문이다():
    """화면에 나가는 글이다. 구어체로 흐르면 돈을 다루는 화면의 신뢰가
    떨어진다."""
    for 정의 in REGISTRY:
        assert 정의.쉬운설명.rstrip().endswith("다."), 정의.key
        if 정의.쉬운참고:
            assert 정의.쉬운참고.rstrip().endswith("다."), 정의.key


# ── 화면까지 흘러가는가 ────────────────────────────────────────


def test_뽑아_둔_자료에_쉬운_설명이_들어_있다():
    """`export_dashboard_data.py`를 다시 안 돌리면 화면은 옛 자료를 읽는다."""
    ㅈ = json.loads(
        (뿌리 / "dashboard" / "자료" / "전략설명.json").read_text(encoding="utf-8")
    )
    표 = {ㄱ["키"]: ㄱ.get("쉬운설명", "") for ㄱ in ㅈ}
    for 정의 in list_definitions():
        assert 표.get(정의.key) == 정의.쉬운설명, f"{정의.key}가 안 맞습니다"


def test_용어집에서_전략_이름으로_찾을_수_있다():
    """표에서 '변동성 돌파'를 보고 그게 뭔지 찾을 때, 전략 이름이 용어집에
    없으면 찾을 곳이 없다."""
    용 = json.loads(
        (뿌리 / "dashboard" / "자료" / "용어사전.json").read_text(encoding="utf-8")
    )
    전략줄 = {ㄱ["이름"]: ㄱ for ㄱ in 용 if ㄱ["열쇠"].startswith("전략:")}

    for 정의 in list_definitions():
        assert 정의.화면이름 in 전략줄, f"{정의.화면이름}이 용어집에 없습니다"
        assert 전략줄[정의.화면이름]["뜻"] == 정의.쉬운설명
        # 영문 칸에 키를 넣어 두면 volume_surge_3d로도 찾을 수 있다.
        assert 전략줄[정의.화면이름]["영문"] == 정의.key


def test_화면이_쉬운_설명을_그린다():
    글 = (뿌리 / "dashboard" / "app.js").read_text(encoding="utf-8")
    씨 = (뿌리 / "dashboard" / "style.css").read_text(encoding="utf-8")

    assert "쉬운설명그리기" in 글
    assert 글.count("쉬운설명그리기(ㅈ)") >= 2, "매수와 매도 두 자리에 다 있어야 합니다"
    assert ".쉬운말" in 씨


@pytest.mark.parametrize("정의", REGISTRY, ids=lambda ㄷ: ㄷ.key)
def test_전략마다_쉬운_참고도_있다(정의):
    """용어집은 항목마다 `읽는법`을 요구한다. 뜻만 있고 그것으로 무엇을
    판단하는지가 없으면 화면을 못 읽기 때문이다. 전략도 용어집에 들어가므로
    같은 규칙이 걸린다.

    2026-09-01에 스토캐스틱 교차 하나만 비워 뒀다가 `test_dashboard_export`
    가 잡았다. 그쪽은 뽑아 둔 자료를 보는 시험이라 한 단계 늦게 걸린다.
    여기서 원본을 보고 먼저 막는다."""
    assert 정의.쉬운참고.strip(), f"{정의.key}에 쉬운참고가 없습니다"
