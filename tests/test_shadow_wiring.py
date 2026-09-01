"""검토 스크립트가 그림자 추적에 넘기는 재기함수.

## 왜 따로 시험하나

`shadow.py`는 재기함수를 받아서 쓰기만 한다. 그 함수를 실제로 만드는 곳은
검토 스크립트이고, **거기서 구간을 잘못 만들면 표에는 그럴듯한 숫자가
남는다.** 30일을 재라고 했는데 3개월을 재도 화면에서는 구별할 수 없다.

그래서 여기서는 "부탁한 구간을 실제로 쟀나"를 본다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

from muwon.settings.schema import RiskPolicy
from tests.price_series import breakout_entry_then_dead_cross_exit

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "run_strategy_review.py"
_스펙 = importlib.util.spec_from_file_location("run_strategy_review_for_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["run_strategy_review_for_test"] = _모듈
_스펙.loader.exec_module(_모듈)

뒤재기만들기 = _모듈.뒤재기만들기
추적시트줄 = _모듈.추적시트줄


def _시세():
    df = breakout_entry_then_dead_cross_exit()
    return {"005930": df}


def test_부탁한_구간을_그대로_잰다():
    histories = _시세()
    끝 = histories["005930"]["trade_date"].max()
    시작 = 끝 - timedelta(days=20)

    성적 = 뒤재기만들기(histories, RiskPolicy())("volume_surge_5d", 시작, 끝)

    assert 성적 is not None
    assert 성적.시작 == 시작
    assert 성적.이름 == "20일 뒤"


def test_구간이_거꾸로면_안_잰다():
    """제안일이 잰날보다 뒤에 있으면 잰 것이 아니라 잘못 부른 것이다."""
    histories = _시세()
    끝 = histories["005930"]["trade_date"].max()
    재기 = 뒤재기만들기(histories, RiskPolicy())
    assert 재기("volume_surge_5d", 끝, 끝) is None
    assert 재기("volume_surge_5d", 끝 + timedelta(days=1), 끝) is None


def test_시세가_없는_구간은_None이다():
    """None이면 그림자 표에 '못잼'으로 남는다. 0%로 남으면 안 된다."""
    histories = _시세()
    먼옛날 = date(2000, 1, 1)
    재기 = 뒤재기만들기(histories, RiskPolicy())
    assert 재기("volume_surge_5d", 먼옛날, 먼옛날 + timedelta(days=30)) is None


def test_추적_시트줄은_머리와_칸수가_같다():
    """칸수가 어긋나면 시트의 모든 줄이 한 칸씩 밀린다."""
    from muwon.db.models import StrategyShadowRow

    줄 = StrategyShadowRow(
        제안일=date(2026, 8, 1), 구간="3개월", 전략="volume_surge_5d", 자리=1,
        지금것=False, 제안것=True, 골랐나=False, 등급="살펴볼것",
        제안시수익률=4.0, 제안시거래수=10, 상태="닫힘",
        잰날=date(2026, 8, 31), 지난날수=30, 뒤수익률=-1.5, 뒤거래수=3,
        뒤최대낙폭=-8.0, 못잰까닭="",
    )
    assert len(추적시트줄(줄)) == len(_모듈.추적머리)


def test_안_잰_칸은_빈칸으로_둔다():
    """None을 문자열로 찍으면 시트에 'None'이 남고 그게 숫자 칸에 들어간다."""
    from muwon.db.models import StrategyShadowRow

    줄 = StrategyShadowRow(
        제안일=date(2026, 8, 1), 구간="3개월", 전략="volume_surge_5d", 자리=1,
        상태="못잼", 지난날수=30, 못잰까닭="시세가 모자랍니다",
    )
    칸들 = 추적시트줄(줄)
    assert "None" not in 칸들
    assert 칸들[_모듈.추적머리.index("뒤수익률%")] == ""


# ── 화면과 파이썬이 같은 규칙으로 짝짓는가 ────────────────────


def _화면짝짓기(줄들: list[dict]) -> list[dict]:
    """`dashboard/app.js`의 `추적짝짓기`를 그대로 떼어 node로 돌린다.

    **같은 규칙이 두 곳에 있다.** 화면은 창구가 준 평평한 줄을 받아 짝을
    맞추고, 파이썬은 DB 줄을 받아 같은 일을 한다. 둘이 갈리면 텔레그램과
    화면이 서로 다른 차이를 표시하는데, 그건 어느 쪽이 맞는지 알 방법이
    없는 종류의 어긋남이다."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        import pytest as _pytest

        _pytest.skip("node가 없어 화면 쪽 규칙을 못 돌립니다")

    글 = (Path(__file__).resolve().parent.parent / "dashboard" / "app.js").read_text(
        encoding="utf-8"
    )
    시작 = 글.index("function 추적짝짓기(")
    깊이 = 0
    for i in range(글.index("{", 시작), len(글)):
        if 글[i] == "{":
            깊이 += 1
        elif 글[i] == "}":
            깊이 -= 1
            if 깊이 == 0:
                끝 = i + 1
                break
    조각 = 글[시작:끝]

    결과 = subprocess.run(
        [node, "-e", 조각 + f"\nconsole.log(JSON.stringify(추적짝짓기({json.dumps(줄들, ensure_ascii=False)})));"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(결과.stdout)


def test_화면과_파이썬이_같은_짝을_고른다():
    from muwon.analysis.shadow import 견주기
    from muwon.db.models import StrategyShadowRow

    자료 = [
        # 버튼이 나간 날
        {"제안일": "2026-08-01", "구간": "3개월", "전략": "지금것", "자리": 9,
         "지금것": True, "제안것": False, "골랐나": False, "뒤수익률": -2.0,
         "뒤거래수": 4, "지난날수": 30, "상태": "닫힘"},
        {"제안일": "2026-08-01", "구간": "3개월", "전략": "후보", "자리": 1,
         "지금것": False, "제안것": True, "골랐나": True, "뒤수익률": 5.0,
         "뒤거래수": 2, "지난날수": 30, "상태": "닫힘"},
        # 버튼이 안 나간 날. 1위와 견준다.
        {"제안일": "2026-08-02", "구간": "1개월", "전략": "지금것", "자리": 3,
         "지금것": True, "제안것": False, "골랐나": False, "뒤수익률": 1.0,
         "뒤거래수": 3, "지난날수": 30, "상태": "닫힘"},
        {"제안일": "2026-08-02", "구간": "1개월", "전략": "일등", "자리": 1,
         "지금것": False, "제안것": False, "골랐나": False, "뒤수익률": -4.0,
         "뒤거래수": 0, "지난날수": 30, "상태": "닫힘"},
        # 못 잰 줄. 양쪽 다 빼야 한다.
        {"제안일": "2026-08-03", "구간": "3개월", "전략": "지금것", "자리": 5,
         "지금것": True, "제안것": False, "골랐나": False, "뒤수익률": None,
         "뒤거래수": 0, "지난날수": 30, "상태": "못잼"},
        {"제안일": "2026-08-03", "구간": "3개월", "전략": "후보", "자리": 1,
         "지금것": False, "제안것": True, "골랐나": False, "뒤수익률": 3.0,
         "뒤거래수": 1, "지난날수": 30, "상태": "닫힘"},
    ]

    화면것 = _화면짝짓기(자료)
    파이썬것 = 견주기([
        StrategyShadowRow(
            제안일=date.fromisoformat(ㄱ["제안일"]), 구간=ㄱ["구간"], 전략=ㄱ["전략"],
            자리=ㄱ["자리"], 지금것=ㄱ["지금것"], 제안것=ㄱ["제안것"],
            골랐나=ㄱ["골랐나"], 뒤수익률=ㄱ["뒤수익률"], 뒤거래수=ㄱ["뒤거래수"],
            지난날수=ㄱ["지난날수"], 상태=ㄱ["상태"],
        )
        for ㄱ in 자료 if ㄱ["상태"] == "닫힘"
    ])

    def 요점(ㄱ):
        쓰기 = (lambda 이름: ㄱ[이름]) if isinstance(ㄱ, dict) else (
            lambda 이름: getattr(ㄱ, 이름))
        return (str(쓰기("제안일")), 쓰기("구간"), 쓰기("후보전략"),
                round(쓰기("차이"), 6), bool(쓰기("버튼있었나")),
                int(쓰기("지금거래수")), int(쓰기("후보거래수")))

    assert sorted(map(요점, 화면것)) == sorted(map(요점, 파이썬것))
    assert len(화면것) == 2
