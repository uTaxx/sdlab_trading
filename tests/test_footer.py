"""모든 텔레그램 글 아래에 붙는 대시보드 링크.

"항상 달려 있다"가 약속이라, 조용히 빠지는 경우가 없어야 한다."""

from muwon.notify import footer


def test_글_아래에_링크가_붙는다():
    결과 = footer.붙이기("오늘 2종목 샀습니다")

    assert 결과.startswith("오늘 2종목 샀습니다")
    assert 결과.rstrip().endswith(footer.주소())
    assert footer.이름 in 결과


def test_여러_번_붙여도_링크는_하나():
    """버튼을 누르면 이미 링크가 붙은 글을 통째로 갈아 끼운다.
    떼지 않고 붙이면 누를 때마다 한 줄씩 쌓인다."""
    한번 = footer.붙이기("후보 3종목")
    세번 = footer.붙이기(footer.붙이기(한번))

    assert 세번 == 한번
    assert 세번.count(footer.표시) == 1


def test_한도를_넘으면_링크가_아니라_본문을_줄인다():
    """링크가 빠지면 약속이 조용히 깨진다. 잘린 본문은 눈에 보이지만
    없는 링크는 안 보인다."""
    결과 = footer.붙이기("가" * 5000, 한도=200)

    assert len(결과) <= 200
    assert 결과.rstrip().endswith(footer.주소())
    assert "…" in 결과


def test_주소는_환경변수로_갈아_끼울_수_있다(monkeypatch):
    """배포 주소는 우리가 정하는 값이 아니라, 코드를 안 고치고 바꿔야 한다."""
    monkeypatch.setenv("MUWON_DASHBOARD_URL", "https://예시.example")

    assert footer.주소() == "https://예시.example"
    assert "https://예시.example" in footer.붙이기("아무 글")


def test_빈_환경변수는_기본주소로_돌아간다(monkeypatch):
    """워크플로에서 빈 값이 넘어오는 일이 흔하다. 그때 주소가 사라지면 안 된다."""
    monkeypatch.setenv("MUWON_DASHBOARD_URL", "   ")

    assert footer.주소() == footer.기본주소


def test_링크가_없는_글에서_떼면_그대로():
    assert footer.떼기("아무 글") == "아무 글"


def test_버튼을_눌러_글을_갈아_끼워도_링크가_남는다():
    """버튼 누름 → 상태 블록 교체 → 다시 보내기 흐름 전체를 태워 본다.

    상태 블록을 갈아 끼울 때 '상태표시 앞부분'만 남기는데, 링크는 그
    뒤에 있어서 같이 떨어져 나간다. 내보내는 쪽에서 다시 붙이지 않으면
    **버튼을 한 번 누르는 순간 링크가 조용히 사라진다.**"""
    from muwon.notify.telegram_buttons import 글에_상태붙이기, 버튼항목

    후보 = [버튼항목(symbol="403870", name="HPSP")]
    보낸글 = footer.붙이기("■ 매수 후보 1종목\n- HPSP")

    갈아낀글 = 글에_상태붙이기(보낸글, 후보, {"403870": "Y"})
    assert footer.표시 not in 갈아낀글  # 여기서는 떨어져 나간 상태다

    내보낼글 = footer.붙이기(갈아낀글)  # telegram_api.edit_text가 하는 일
    assert 내보낼글.rstrip().endswith(footer.주소())
    assert 내보낼글.count(footer.표시) == 1
    assert "HPSP" in 내보낼글


# ── 하이퍼링크 ──────────────────────────────────────────────────────────
#
# 한 번 만들었다가 되돌린 적이 있다(2026-08-24). HTML 모드를 켜면 본문에
# <, & 가 섞였을 때 텔레그램이 글 전체를 거절하는데, 그건 나중에야 알게
# 되는 실패다. 이번에는 평문으로 되돌아갈 길을 같이 뒀다.


def test_하이퍼링크는_이름에_주소를_건다():
    결과 = footer.하이퍼("오늘 2종목 샀습니다")

    assert f'<a href="{footer.주소()}">' in 결과
    assert footer.이름 in 결과
    # 주소가 본문에 날것으로 또 나오면 한 줄이 두 줄이 된다.
    assert 결과.count(footer.주소()) == 1


def test_본문의_꺾쇠와_앰퍼샌드를_이스케이프한다():
    """이걸 안 하면 텔레그램이 글 전체를 거절하고 알림이 통째로 안 간다."""
    결과 = footer.하이퍼("종목명 <주식&회사> 를 샀습니다")

    assert "&lt;주식&amp;회사&gt;" in 결과
    assert "<주식" not in 결과


def test_하이퍼링크를_떼면_이스케이프도_되돌린다():
    """안 되돌리면 다시 붙일 때 &amp;amp; 로 쌓인다."""
    한번 = footer.하이퍼("A & B")
    assert footer.떼기(한번) == "A & B"

    두번 = footer.하이퍼(한번)
    assert 두번.count("<a href=") == 1
    assert "&amp;amp;" not in 두번


def test_텔레그램이_돌려준_모양도_뗀다():
    """버튼을 누르면 화면 글자만 넘어온다. 주소 없이 이름 한 줄이다."""
    돌아온것 = f"오늘 2종목 샀습니다\n\n{footer.누름표시}"

    assert footer.떼기(돌아온것) == "오늘 2종목 샀습니다"


def test_옛_평문_모양도_뗀다():
    """HTML로 바꾸기 전에 보낸 글을 갈아 끼울 때 만난다."""
    옛것 = f"오늘 2종목 샀습니다\n\n{footer.표시}\n{footer.주소()}"

    assert footer.떼기(옛것) == "오늘 2종목 샀습니다"


def test_하이퍼링크도_한도를_넘으면_본문을_줄인다():
    """링크가 빠지면 '항상 달려 있다'는 약속이 조용히 깨진다."""
    결과 = footer.하이퍼("가" * 5000)

    assert len(결과) <= footer.텔레그램한도
    assert footer.앵커() in 결과
    assert "…" in 결과
