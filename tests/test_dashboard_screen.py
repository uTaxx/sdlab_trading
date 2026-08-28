"""화면(dashboard/)이 전략을 한글로 보여 주는지, 표 칸 수가 맞는지 본다.

## 왜 여기까지 시험하나

`volume_surge_5d_ma20`은 처음 보는 사람에게 아무 뜻도 없다. 뜻이 없으면
그 줄을 아예 안 읽게 되고, 안 읽는 줄은 없는 줄과 같다. 그런데 이 값은
DB와 시트에서 그대로 흘러 들어와서, 옮겨 적는 자리를 한 군데만 빠뜨려도
그 탭에서만 조용히 영어가 뜬다.

표의 칸 수도 같다. `<th>`를 하나 더 넣고 `colspan`을 안 고치면, 자료가
있을 때는 멀쩡해 보이다가 **비었을 때만** 표가 어긋난다. 그게 눈에 띌
때쯤이면 왜 그런지 찾기 어렵다.
"""

from __future__ import annotations

import re
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent
쪽 = (뿌리 / "dashboard" / "index.html").read_text(encoding="utf-8")
글 = (뿌리 / "dashboard" / "app.js").read_text(encoding="utf-8")


def _표들() -> dict[str, int]:
    """표마다 `<th>`가 몇 개인지. 열쇠는 tbody의 id다."""
    나온것 = {}
    for 덩이 in re.findall(r"<table>(.*?)</table>", 쪽, re.DOTALL):
        이름 = re.search(r'<tbody id="([^"]+)"', 덩이)
        if 이름:
            나온것[이름.group(1)] = len(re.findall(r"<th[ >]", 덩이))
    return 나온것


def test_표의_빈줄_칸수가_머리_칸수와_같다():
    """안 맞으면 자료가 있을 때는 멀쩡하고 비었을 때만 표가 어긋난다."""
    표 = _표들()
    # 표불러오기를 쓰는 표는 `칸수:`로 적힌다.
    for 몸, 칸수 in re.findall(r'몸: "(\w+)", 칸수: (\d+)', 글):
        assert 표[몸] == int(칸수), f"{몸}: 머리 {표[몸]}칸인데 빈 줄은 {칸수}칸"
    # 직접 그리는 둘은 colspan을 손으로 적는다.
    assert 표["보유몸"] == 7
    assert 표["기록몸"] == 8
    assert 표["검증몸"] == 8
    assert 'colspan="7" class="빔">보유 종목이 없습니다' in 글
    assert 'colspan="8" class="빔">아직 청산까지' in 글
    assert 'colspan="8" class="빔">아직 돌려 본 적이' in 글


def test_전략이_나오는_자리마다_한글로_옮긴다():
    """탭 하나만 빠뜨려도 거기서만 영어가 뜬다. 그 탭은 안 읽게 된다."""
    # 승인 후보 · 완결된 거래 · 실행 기록 · 머리의 전략 띠
    assert 글.count("전략이름들(") >= 6, "전략 이름을 옮기는 자리가 줄었습니다"
    # 변경 이력은 열쇠말(무엇)과 값(이전·이후)을 둘 다 옮긴다.
    assert "기준이름(ㅇ.무엇)" in 글
    assert "이력값(ㅇ.무엇, ㅇ.이전)" in 글
    assert "이력값(ㅇ.무엇, ㅇ.이후)" in 글


def test_전략_띠가_어느_탭에서든_보인다():
    """머리에 있어야 한다. 탭 안에 넣으면 그 탭을 열어야만 보인다."""
    자리 = 쪽.index('id="전략띠"')
    본문 = 쪽.index('<main id="본문"')
    assert 자리 < 본문, "전략 띠가 본문 안에 있으면 한 탭에서만 보입니다"
    assert 'id="띠매수"' in 쪽
    assert 'id="띠매도"' in 쪽
    assert "전략띠그리기(자료)" in 글


def test_새로_고쳐_들어와도_머리를_읽는다():
    """예전에는 열쇠를 처음 넣는 길에서만 기준을 읽어서, 창을 닫았다 다시
    열면 스위치와 전략 띠가 빈 채로 남았다."""
    assert 글.count("기준불러오기();") >= 2


def test_화면에_전략키를_손으로_적어_두지_않는다():
    """예시 자료 말고 화면 글에 코드 이름이 박혀 있으면 그것만 안 바뀐다.

    주석은 뺀다. 주석은 화면에 안 뜨고, 왜 그렇게 했는지를 적는 자리다."""
    본문만 = re.sub(r"<!--.*?-->", "", 쪽, flags=re.DOTALL)
    박힌것 = [줄 for 줄 in 본문만.splitlines() if "volume_surge" in 줄 or "ma_rsi" in 줄]
    assert not 박힌것, f"index.html에 전략 키가 박혀 있습니다: {박힌것}"


def test_기간_목록이_파이썬_워크플로_화면에서_같다():
    """구간 이름이 세 곳에 있다. 파이썬 원본, 워크플로 입력, 화면 드롭다운.
    하나만 고치면 화면에서 고른 값을 워크플로가 모르는 값으로 받는다."""
    import json

    from muwon.analysis.period_check import 기간들

    원본 = [ㄱ.이름 for ㄱ in 기간들]

    뽑아둔것 = json.loads(
        (뿌리 / "dashboard" / "자료" / "기간설명.json").read_text(encoding="utf-8")
    )
    assert [ㄱ["이름"] for ㄱ in 뽑아둔것] == 원본

    일 = (뿌리 / ".github" / "workflows" / "period-check.yml").read_text(encoding="utf-8")
    for 이름 in 원본:
        assert f"          - {이름}\n" in 일, f"워크플로 입력에 {이름}이 없습니다"


def test_돌려보기가_주문을_내지_않는다고_화면이_말한다():
    """이 단추는 아무것도 사지 않는다. 그 말이 화면에 없으면 누르기 무섭다."""
    assert "주문은 나가지 않습니다" in 쪽
    assert "주문은 나가지 않습니다" in 글


def test_검증탭이_기록탭과_섞이지_않는다():
    """실제로 사고판 성적과 '이랬으면 어땠을까'는 다른 표에 있어야 한다.
    같이 두면 번 적 없는 돈을 번 것으로 읽는다."""
    assert "실제로 번 돈이 아닙니다" in 쪽
    검증 = 쪽.index('id="쪽-검증"')
    기록 = 쪽.index('id="쪽-기록"')
    assert 검증 != 기록
