"""텔레그램에서 온 글을 명령으로 읽는 자리.

**여기서 느슨하면 봇 이름을 아는 누구나 매매 기준을 바꾼다.** 그래서
시험도 '무엇이 되나'보다 '무엇이 안 되나'가 더 많다."""

import pytest

from muwon.notify.telegram_control import parse_command, 도움말, 바꾼말


def test_상태를_여러_말로_부를_수_있다():
    for 글 in ("/상태", "/status", "/기준", "  /상태  "):
        assert parse_command(글).종류 == "상태"


def test_기준_하나를_바꾼다():
    c = parse_command("/설정 max_position_weight 12")
    assert c.종류 == "설정"
    assert c.이름 == "max_position_weight"
    assert c.값 == pytest.approx(12.0)


def test_매매를_켜는_것은_텔레그램에서_안_된다():
    """폰에서 손가락이 미끄러져 매매가 켜지면 안 된다."""
    c = parse_command("/설정 trading_enabled true")
    assert c.종류 == "모름"
    assert "못 바꿉니다" in c.말
    assert "/끄기" in c.말


def test_끄는_것은_언제든_된다():
    """끄는 길은 넓히고 켜는 길은 좁힌다."""
    assert parse_command("/끄기").종류 == "끄기"
    assert parse_command("/stop").종류 == "끄기"


def test_켜기는_명령으로_읽되_실행은_거부할_몫이다():
    """읽기는 읽는다. 그래야 '왜 안 되는지'를 답해 줄 수 있다."""
    assert parse_command("/켜기").종류 == "켜기"


def test_범위를_벗어난_값은_거부한다():
    c = parse_command("/설정 max_position_weight 80")
    assert c.종류 == "모름"
    assert "50" in c.말


def test_손절선을_양수로_적으면_거부한다():
    c = parse_command("/설정 stop_loss_pct 5")
    assert c.종류 == "모름"
    assert "음수" in c.말


def test_모르는_기준은_추측하지_않는다():
    """'아마 이 뜻이겠지'가 매매 기준을 바꾸면 안 된다."""
    c = parse_command("/설정 stop_loss 5")
    assert c.종류 == "모름"
    assert "모르는 기준" in c.말


def test_모르는_명령에는_안내만_한다():
    c = parse_command("/아무말")
    assert c.종류 == "모름"
    assert "/도움" in c.말


def test_값을_안_적으면_안내한다():
    assert "이름과 값을" in parse_command("/설정 max_position_weight").말


def test_승인은_여섯자리_종목코드만_받는다():
    c = parse_command("/승인 005930 000660")
    assert c.종류 == "승인"
    assert c.종목들 == ("005930", "000660")


def test_승인에_이상한_것이_섞이면_통째로_거부한다():
    """절반만 알아듣고 실행하면 무엇이 승인됐는지 헷갈린다."""
    c = parse_command("/승인 005930 삼성전자")
    assert c.종류 == "모름"
    assert "삼성전자" in c.말


def test_종목코드가_없으면_안내한다():
    assert "여섯 자리" in parse_command("/승인").말


def test_빈_글도_죽지_않는다():
    for 글 in ("", "   ", None):
        assert parse_command(글).종류 == "모름"


def test_도움말에_바꿀_수_있는_이름이_들어간다():
    글 = 도움말()
    assert "max_position_weight" in 글
    assert "trading_enabled" not in 글  # 텔레그램에서 못 바꾸는 것은 안 적는다
    assert "켜는 것은 여기서 안 됩니다" in 글


def test_바꾼말은_왜_중요한지까지_말한다():
    """몇 달 뒤에 '이걸 왜 12로 뒀지'를 답할 수 있어야 한다."""
    글 = 바꾼말("max_position_weight", 15.0, 12.0)
    assert "15.0%" in 글 and "12.0%" in 글
    assert "반토막" in 글  # 기준표의 '왜'가 같이 나온다


def test_읽은_위치를_기억한다(tmp_path):
    """안 남기면 워크플로가 돌 때마다 어제 명령이 다시 실행된다."""
    from muwon.settings.service import build_settings_service

    service = build_settings_service(f"sqlite:///{tmp_path / 'x.db'}", master_key="")
    assert service.get_telegram_offset() == 0
    service.set_telegram_offset(1234)
    assert service.get_telegram_offset() == 1234


def test_읽은_위치가_망가져_있어도_죽지_않는다(tmp_path):
    """0으로 돌아가면 옛 명령이 다시 도는데, 터지는 것보다는 낫다."""
    from muwon.settings.service import build_settings_service

    service = build_settings_service(f"sqlite:///{tmp_path / 'y.db'}", master_key="")
    service._store.set("telegram.update_offset", "이상한값")
    assert service.get_telegram_offset() == 0
