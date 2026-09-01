"""승인 / 거절 버튼.

버튼은 코드를 손으로 치는 것보다 안전하다. 오타로 엉뚱한 종목을 승인할
수가 없다. 대신 **손가락이 스치는 일**이 실제로 일어나므로, 눌러서 될 수
있는 일의 범위를 좁게 잡았는지가 시험의 초점이다."""

from datetime import date

import pytest

from muwon.notify.telegram_buttons import (
    MAX_CALLBACK_BYTES,
    callback_data,
    keyboard,
    parse_callback,
    누른뒤말,
)

오늘 = date(2026, 8, 20)


class 가짜후보:
    def __init__(self, symbol, name):
        self.symbol, self.name = symbol, name


후보들 = [가짜후보("005930", "삼성전자"), 가짜후보("000660", "SK하이닉스")]


def test_승인_버튼을_알아본다():
    c = parse_callback("a|2026-08-20|005930")
    assert c.종류 == "승인" and c.symbol == "005930" and c.날짜 == 오늘


def test_거절_버튼을_알아본다():
    assert parse_callback("r|2026-08-20|005930").종류 == "거절"


def test_전부_버튼에는_종목이_없다():
    c = parse_callback("A|2026-08-20")
    assert c.종류 == "전부승인" and c.symbol == ""
    assert parse_callback("R|2026-08-20").종류 == "전부거절"


def test_모르는_버튼은_추측하지_않는다():
    # `x`는 2026-09-01에 전략취소가 됐다. 안 쓰는 글자로 바꾼다.
    for 값 in ("", "z|2026-08-20|005930", "a", "a|어제|005930", "a|2026-08-20|12", None):
        assert parse_callback(값).종류 == "모름", f"{값!r}가 명령으로 읽혔습니다"


def test_버튼_자료가_64바이트를_넘지_않는다():
    """텔레그램 제한이다. 넘으면 버튼이 통째로 안 만들어진다."""
    for 값 in (callback_data("a", 오늘, "005930"), callback_data("A", 오늘)):
        assert len(값.encode()) <= MAX_CALLBACK_BYTES


def test_종목명이_길어도_버튼_자료는_안_길어진다():
    """버튼 자료에는 이름이 안 들어간다. 한글 몇 자면 64바이트를 넘는다."""
    판 = keyboard([가짜후보("005930", "아주아주긴이름의회사입니다주식회사")], 오늘)
    for 줄 in 판["inline_keyboard"]:
        for 칸 in 줄:
            assert len(칸["callback_data"].encode()) <= MAX_CALLBACK_BYTES


def test_긴_이름은_버튼_글자만_줄인다():
    판 = keyboard([가짜후보("005930", "아주아주긴이름의회사입니다")], 오늘)
    글 = 판["inline_keyboard"][0][0]["text"]
    assert "…" in 글


def test_후보마다_승인과_거절이_한_줄로():
    판 = keyboard(후보들, 오늘)
    assert len(판["inline_keyboard"][0]) == 2
    assert "삼성전자" in 판["inline_keyboard"][0][0]["text"]


def test_후보가_둘_이상이면_전부_버튼이_붙는다():
    판 = keyboard(후보들, 오늘)
    끝줄 = 판["inline_keyboard"][-1]
    assert [칸["text"] for 끝줄칸 in [끝줄] for 칸 in 끝줄칸] == ["✅ 전부 승인", "❌ 전부 거절"]


def test_후보가_하나면_전부_버튼을_안_붙인다():
    """하나뿐인데 '전부'가 나오면 무슨 뜻인지 헷갈린다."""
    판 = keyboard([후보들[0]], 오늘)
    assert len(판["inline_keyboard"]) == 1


def test_이미_누른_것이_버튼에_보인다():
    """방금 누른 게 먹었는지 화면에서 보이지 않으면 또 누르게 된다."""
    판 = keyboard(후보들, 오늘, {"005930": "Y", "000660": "N"})
    assert "승인함" in 판["inline_keyboard"][0][0]["text"]
    assert "거절함" in 판["inline_keyboard"][1][1]["text"]


def test_누른뒤말은_짧다():
    """화면 위에 잠깐 뜨는 한 줄이라 길면 안 읽힌다."""
    말 = 누른뒤말(parse_callback("a|2026-08-20|005930"), "삼성전자")
    assert 말 == "삼성전자 승인"
    assert len(누른뒤말(parse_callback("A|2026-08-20"))) < 20


def test_이름을_모르면_코드로_말한다():
    assert 누른뒤말(parse_callback("r|2026-08-20|005930")) == "005930 거절"


def test_너무_긴_자료는_만들_때_터진다():
    """조용히 잘려 나가면 엉뚱한 종목이 승인된다."""
    with pytest.raises(ValueError, match="64바이트"):
        callback_data("a", 오늘, "0" * 100)


def test_버튼_판을_다시_그리면_결정이_반영된다():
    """누를 때마다 판을 갈아 끼운다. 화면이 지금 상태를 보여 줘야 한다."""
    from muwon.notify.telegram_buttons import 버튼항목

    후보 = [버튼항목("005930", "삼성전자"), 버튼항목("000660", "SK하이닉스")]
    처음 = keyboard(후보, 오늘)
    나중 = keyboard(후보, 오늘, {"005930": "Y"})
    assert 처음["inline_keyboard"][0][0]["text"] != 나중["inline_keyboard"][0][0]["text"]
    # 누르는 자료는 그대로다. 다시 눌러 되돌릴 수 있어야 한다
    assert 처음["inline_keyboard"][0][0]["callback_data"] == 나중["inline_keyboard"][0][0]["callback_data"]


def test_되돌릴_수_있다():
    """잘못 눌렀을 때 반대 버튼으로 고칠 수 있어야 한다. 승인은 되돌릴 수
    없는 일이 아니다(아직 사기 전이므로)."""
    from muwon.notify.telegram_buttons import 버튼항목

    판 = keyboard([버튼항목("005930", "삼성전자")], 오늘, {"005930": "Y"})
    거절칸 = 판["inline_keyboard"][0][1]
    assert parse_callback(거절칸["callback_data"]).종류 == "거절"


def test_받을_종류에_버튼이_들어_있다():
    """여기 빠지면 버튼을 눌러도 아무것도 안 온다. 그리고 그 사실이
    로그에는 '새 메시지 0개'로만 나타나서 원인을 알 수가 없다."""
    from muwon.notify.telegram_api import 받을것

    assert "callback_query" in 받을것
    assert "message" in 받을것


def test_상태블록이_승인_거절_미정을_모두_센다():
    """승인 1건이라는 말만으로는 나머지를 봤는지 알 수 없다."""
    from muwon.notify.telegram_buttons import 버튼항목, 상태블록

    후보 = [버튼항목("005930", "삼성전자"), 버튼항목("000660", "SK하이닉스"),
            버튼항목("403870", "HPSP")]
    글 = 상태블록(후보, {"005930": "Y", "000660": "N"})
    assert "✅ 승인 1종목: 삼성전자" in 글
    assert "❌ 거절 1종목: SK하이닉스" in 글
    assert "아직 안 정한 것 1종목: HPSP" in 글


def test_다_보면_그렇다고_말한다():
    from muwon.notify.telegram_buttons import 버튼항목, 상태블록

    글 = 상태블록([버튼항목("005930", "삼성전자")], {"005930": "Y"})
    assert "다 보셨습니다" in 글


def test_상태블록은_누를때마다_갈아_끼워진다():
    """안 자르면 누를 때마다 글이 길어져 후보 목록이 화면 밖으로 밀려난다."""
    from muwon.notify.telegram_buttons import 글에_상태붙이기, 버튼항목

    후보 = [버튼항목("005930", "삼성전자")]
    원래 = "📋 매수 후보 1종목\n\n  삼성전자(005930) @ 70,000원"
    한번 = 글에_상태붙이기(원래, 후보, {})
    두번 = 글에_상태붙이기(한번, 후보, {"005930": "Y"})
    세번 = 글에_상태붙이기(두번, 후보, {"005930": "N"})

    assert 세번.count("지금 상태") == 1
    assert 세번.startswith(원래)
    assert "❌ 거절 1종목" in 세번
    assert "✅ 승인 1종목: 삼성전자" not in 세번   # 되돌린 것은 안 남는다


def test_아무것도_안_눌렀을_때도_상태가_보인다():
    from muwon.notify.telegram_buttons import 버튼항목, 상태블록

    글 = 상태블록([버튼항목("005930", "삼성전자")], {})
    assert "✅ 승인 0종목" in 글
    assert "아직 안 정한 것 1종목" in 글


def _payload(글):
    """스크립트의 payload 해석기를 가져다 쓴다. 규칙이 거기 있다."""
    import importlib.util
    from pathlib import Path

    경로 = Path(__file__).resolve().parent.parent / "scripts" / "telegram_control.py"
    spec = importlib.util.spec_from_file_location("telegram_control_script", 경로)
    모듈 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(모듈)
    return 모듈._payload_updates(글)


def test_n8n이_넘긴_업데이트를_읽는다():
    import json

    것 = {"update_id": 1, "callback_query": {"id": "x", "data": "a|2026-08-20|005930"}}
    assert _payload(json.dumps(것)) == [것]


def test_body로_감싸_보내도_받는다():
    """n8n 설정 하나 때문에 안 먹으면 원인을 찾기 어렵다."""
    import json

    안 = {"update_id": 2, "message": {"text": "/상태"}}
    assert _payload(json.dumps({"body": 안})) == [안]


def test_목록으로_와도_받는다():
    import json

    것들 = [{"update_id": 1, "message": {"text": "/상태"}}]
    assert _payload(json.dumps(것들)) == 것들


def test_업데이트가_아니면_터진다():
    """조용히 넘어가면 버튼이 먹통인데 워크플로는 성공으로 끝난다."""
    import json

    import pytest

    with pytest.raises(SystemExit, match="업데이트 같지"):
        _payload(json.dumps({"아무": "거나"}))


# ── 전략 변경 버튼 (2026-09-01) ────────────────────────────────
#
# 이 버튼은 누르면 실제 매매 전략이 바뀐다. 매수 승인보다 위험한 자리라
# 안 눌리는 조건을 더 많이 본다.

from muwon.notify.telegram_buttons import (
    MAX_KEY_LEN,
    고른것표시,
    고른표,
    전략버튼,
    전략상태블록,
    전략키보드,
)


def _버튼(키="volume_surge_3d", 이름="거래량 급증 3일", 구간="3개월"):
    return 전략버튼(키=키, 이름=이름, 구간=구간, 수익률=10.0)


def test_전략_버튼과_매수_버튼은_콜백_글자가_안_겹친다():
    """겹치면 어제 메시지의 버튼이 오늘 다른 일을 한다."""
    from muwon.notify import telegram_buttons as ㅌ

    글자 = [ㅌ.승인, ㅌ.거절, ㅌ.전부승인, ㅌ.전부거절,
          ㅌ.전략고름, ㅌ.전략확정, ㅌ.전략취소]
    assert len(글자) == len(set(글자)), f"겹치는 글자가 있습니다: {글자}"


def test_옛_메시지의_확인_버튼도_계속_읽는다():
    """두 단계였을 때 보낸 메시지가 대화방에 남아 있다. 안 읽으면 그 버튼이
    아무 반응도 안 하고, 사람은 왜 안 되는지 알 수가 없다."""
    c = parse_callback("s|2026-08-20|volume_surge_3d")
    assert c.종류 == "전략고름"
    assert c.전략키 == "volume_surge_3d"
    assert parse_callback("c|2026-08-20|volume_surge_3d").종류 == "전략확정"


def test_취소_버튼에는_전략이_없다():
    c = parse_callback("x|2026-08-20")
    assert c.종류 == "전략취소" and c.전략키 == ""


def test_전략_이름이_이상하면_안_읽는다():
    """우리가 만든 버튼은 영문 소문자·숫자·밑줄만 씁니다."""
    for 값 in ("s|2026-08-20", "s|2026-08-20|", "s|2026-08-20|한글",
               "c|2026-08-20|../etc", "s|2026-08-20|a b"):
        assert parse_callback(값).종류 == "모름", f"{값!r}가 명령으로 읽혔습니다"


def test_같은_전략이_여러_구간에서_앞서면_버튼_하나로_합친다():
    판 = 전략키보드(
        [_버튼(구간="1개월"), _버튼(구간="3개월"), _버튼(키="macd_cross", 이름="MACD 교차", 구간="1주")],
        오늘,
    )
    줄들 = 판["inline_keyboard"]
    assert len(줄들) == 2, "같은 전략이 두 줄로 나오면 무엇이 다른지 찾게 됩니다"
    assert "1개월·3개월" in 줄들[0][0]["text"]


def test_후보가_없으면_버튼판을_안_만든다():
    assert 전략키보드([], 오늘) is None


def test_전략_버튼_자료도_64바이트를_넘지_않는다():
    from muwon.strategy.registry import list_definitions

    for d in list_definitions():
        assert len(d.key) <= MAX_KEY_LEN, f"{d.key}가 너무 깁니다"
        판 = 전략키보드([_버튼(키=d.key, 이름=d.화면이름)], 오늘)
        값 = 판["inline_keyboard"][0][0]["callback_data"]
        assert len(값.encode()) <= MAX_CALLBACK_BYTES, f"{d.key}: {값}"


def test_키가_너무_길면_조용히_넘어가지_않는다():
    """버튼을 못 만드는 것이 조용히 넘어가면 그 전략만 승인할 길이 없어집니다."""
    import pytest

    with pytest.raises(ValueError, match="버튼을 못 만듭니다"):
        전략키보드([_버튼(키="a" * (MAX_KEY_LEN + 1))], 오늘)


def test_고른_것에만_표시가_붙는다():
    """무엇을 예약해 두었는지 판만 보고 알 수 있어야 합니다."""
    판 = 전략키보드([_버튼(), _버튼(키="macd_cross", 이름="MACD 교차", 구간="1주")], 오늘)
    표시된것 = 고른것표시(판, "macd_cross")
    글들 = [ㄱ[0]["text"] for ㄱ in 표시된것["inline_keyboard"]]
    붙은것 = [ㄱ for ㄱ in 글들 if ㄱ.startswith(고른표)]
    assert len(붙은것) == 1
    assert "MACD" in 붙은것[0]


def test_취소하면_표시가_사라진다():
    """표시가 남으면 취소했는데도 예약된 것처럼 보입니다."""
    판 = 전략키보드([_버튼()], 오늘)
    표시된것 = 고른것표시(판, "volume_surge_3d")
    되돌린것 = 고른것표시(표시된것, "")
    assert 되돌린것["inline_keyboard"][0][0]["text"] == 판["inline_keyboard"][0][0]["text"]


def test_표시를_붙여도_누를_자료는_그대로다():
    """자료가 바뀌면 그 버튼이 다른 일을 하게 됩니다."""
    판 = 전략키보드([_버튼()], 오늘)
    앞 = 판["inline_keyboard"][0][0]["callback_data"]
    뒤 = 고른것표시(판, "volume_surge_3d")["inline_keyboard"][0][0]["callback_data"]
    assert 앞 == 뒤


def test_같은_것을_두_번_표시해도_표가_겹치지_않는다():
    판 = 전략키보드([_버튼()], 오늘)
    한번 = 고른것표시(판, "volume_surge_3d")
    두번 = 고른것표시(한번, "volume_surge_3d")
    assert 두번["inline_keyboard"][0][0]["text"].count(고른표.strip()) == 1


def test_판이_없으면_None이다():
    assert 고른것표시(None, "volume_surge_3d") is None
    assert 고른것표시({"inline_keyboard": []}, "volume_surge_3d") is None


def test_상태블록이_두_상태를_구별한다():
    """예약 없음과 예약됨은 다른 말이어야 합니다."""
    없음 = 전략상태블록()
    예약 = 전략상태블록("volume_surge_3d", "거래량 급증 3일")
    assert "예약된 전략 변경이 없습니다" in 없음
    assert "예약되었습니다" in 예약
    assert "다음 거래일" in 예약
    assert "다시 누르면 취소" in 예약
    for 글 in (없음, 예약):
        assert "—" not in 글, f"줄표가 있습니다: {글}"


def test_상태블록의_조사가_이름을_따라간다():
    """받침을 안 보면 "변동성 돌파으로"가 나옵니다. 실제로 나왔습니다."""
    assert "변동성 돌파로 변경이" in 전략상태블록("k", "변동성 돌파")
    assert "골든크로스 20/60으로 변경이" in 전략상태블록("k", "골든크로스 20/60")
