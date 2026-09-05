"""시트의 설정 값이 매매 정책이 되는 자리.

여기서 틀리면 **단위 하나 때문에 자금 전부가 한 종목에 들어간다.**
그래서 규칙 하나마다 시험을 붙였다."""

import pytest

from muwon.settings.from_sheet import (
    SettingsError,
    apply,
    describe,
    parse_settings,
    시트설정,
)
from muwon.settings.schema import RiskPolicy


def 기본설정(**덮개):
    값 = {
        "trading_enabled": "false",
        "max_position_weight": "15",
        "max_concurrent_positions": "8",
        "stop_loss_pct": "-5",
        "daily_loss_limit_pct": "-3",
        "min_turnover_eok": "50",
        "require_approval": "true",
    }
    값.update(덮개)
    return 값


def test_퍼센트를_소수로_바꾼다():
    """시트에 15라고 적으면 0.15여야 한다. 그냥 넘기면 자금의 1,500%다."""
    결과 = parse_settings(기본설정())
    assert 결과.덮개["max_position_weight"] == pytest.approx(0.15)
    assert 결과.덮개["stop_loss_pct"] == pytest.approx(-0.05)
    assert 결과.덮개["daily_loss_limit_pct"] == pytest.approx(-0.03)


def test_킬스위치는_빈칸이면_꺼진다():
    """종목 탭과 규칙이 **반대**다. 매매를 켜려면 명시해야 한다."""
    assert parse_settings(기본설정(trading_enabled="")).덮개["trading_enabled"] is False
    assert parse_settings(기본설정(trading_enabled="아무말")).덮개["trading_enabled"] is False
    assert parse_settings(기본설정(trading_enabled="Y")).덮개["trading_enabled"] is True
    assert parse_settings(기본설정(trading_enabled="true")).덮개["trading_enabled"] is True


def test_비중상한이_범위를_벗어나면_거부한다():
    with pytest.raises(SettingsError, match="분산이 아닙니다"):
        parse_settings(기본설정(max_position_weight="80"))
    with pytest.raises(SettingsError):
        parse_settings(기본설정(max_position_weight="0"))


def test_손절선을_양수로_적으면_거부한다():
    """'5% 빠지면 판다'를 5로 적는 실수를 잡는다. 그대로 두면 손절이 안 걸린다."""
    with pytest.raises(SettingsError, match="-5처럼"):
        parse_settings(기본설정(stop_loss_pct="5"))


def test_동시보유가_정수가_아니면_거부한다():
    with pytest.raises(SettingsError, match="정수"):
        parse_settings(기본설정(max_concurrent_positions="8.5"))
    with pytest.raises(SettingsError):
        parse_settings(기본설정(max_concurrent_positions="0"))


def test_모르는_이름은_막지_않되_알린다():
    """오타 난 줄이 매매를 멈추면 안 되지만, 조용히 넘어가도 안 된다."""
    결과 = parse_settings(기본설정(stop_loss="-5", 메모="아무거나"))
    assert set(결과.모르는이름) == {"stop_loss", "메모"}
    assert "stop_loss_pct" in 결과.덮개  # 제대로 된 이름은 그대로 먹는다


def test_빈칸은_덮지_않는다():
    """빈 칸은 '0으로 하라'가 아니라 '안 적었다'다. DB 값이 살아야 한다."""
    결과 = parse_settings(기본설정(max_position_weight=""))
    assert "max_position_weight" not in 결과.덮개


def test_승인은_빈칸이면_받는다():
    assert parse_settings(기본설정(require_approval="")).승인필요 is True
    assert parse_settings(기본설정(require_approval="N")).승인필요 is False


def test_시트가_DB를_이긴다():
    정책 = RiskPolicy(max_position_weight=0.10, trading_enabled=True)
    새정책, 출처 = apply(정책, parse_settings(기본설정(max_position_weight="20")))
    assert 새정책.max_position_weight == pytest.approx(0.20)
    assert 출처["max_position_weight"] == "시트"
    assert 출처["atr_stop_multiple"] == "DB"  # 시트에 없는 항목은 DB 그대로


def test_시트를_못_읽으면_매매를_끈다():
    """제일 나쁜 고장은 '사람은 껐다고 믿는데 코드는 켜진 채 도는' 것이다."""
    정책 = RiskPolicy(trading_enabled=True)
    새정책, 출처 = apply(정책, None)
    assert 새정책.trading_enabled is False
    assert "못 읽어" in 출처["trading_enabled"]


def test_설명에_출처와_경고가_보인다():
    시트 = parse_settings(기본설정(오타항목="x"))
    정책, 출처 = apply(RiskPolicy(), 시트)
    글 = describe(정책, 출처, 시트)
    assert "꺼짐" in 글
    assert "[시트]" in 글
    assert "오타항목" in 글
    assert "아무 효과가 없습니다" in 글


def test_시트를_못_읽으면_매수는_끄고_매도는_켠다():
    """방향이 반대다. 매수를 못 하면 기회를 놓칠 뿐이지만 **매도를 못 하면
    손실이 그대로 자란다**. 모를 때 기울 쪽이 서로 반대다."""
    정책, 출처 = apply(RiskPolicy(), None)

    assert 정책.trading_enabled is False
    assert 정책.sell_enabled is True
    글 = describe(정책, 출처, None)
    assert "매수를 껐습니다" in 글
    assert "매도는 살려" in 글


def test_최소거래대금이_음수면_거부한다():
    with pytest.raises(SettingsError, match="0 이상"):
        parse_settings(기본설정(min_turnover_eok="-1"))


def test_아무것도_없으면_안전한_기본값():
    빈것 = 시트설정()
    assert 빈것.승인필요 is True
    정책, _ = apply(RiskPolicy(), 빈것)
    assert 정책.max_position_weight == RiskPolicy().max_position_weight


def test_기준표에_있는_항목은_시트에_없어도_기본값이_나온다():
    """새 기준을 추가했는데 시트를 아직 안 고쳤을 때 터지면 안 된다."""
    시트 = parse_settings(기본설정())
    assert 시트.가져오기("max_per_sector") == 2
    assert 시트.가져오기("sector_filter_enabled") is False
    assert 시트.가져오기("sector_lookback") == 20


def test_시트에_적으면_그_값이_나온다():
    시트 = parse_settings(기본설정(max_per_sector="1", sector_filter_enabled="Y"))
    assert 시트.가져오기("max_per_sector") == 1
    assert 시트.가져오기("sector_filter_enabled") is True


def test_섹터_기준도_범위를_본다():
    with pytest.raises(SettingsError, match="250"):
        parse_settings(기본설정(sector_lookback="9999"))
    with pytest.raises(SettingsError, match="정수"):
        parse_settings(기본설정(max_per_sector="1.5"))


def test_기준표와_시트초안이_어긋나지_않는다():
    """초안에 없는 기준이 생기면, 시트를 새로 만들 때 그 줄이 빠진다."""
    from muwon.cloud.sector_sheet import default_settings_rows
    from muwon.settings.from_sheet import 기준표

    초안이름 = {줄[0] for 줄 in default_settings_rows()[1:]}
    assert 초안이름 == set(기준표)


class 가짜서비스:
    def __init__(self, 정책):
        self._정책 = 정책

    def get_risk_policy(self):
        return self._정책


def test_제공자는_한번만_읽는다():
    """한 회차 도는 중간에 기준이 바뀌면 로그를 봐도 왜 그랬는지 모른다."""
    from muwon.settings.from_sheet import build_policy_provider

    센횟수 = {"n": 0}

    def 읽기():
        센횟수["n"] += 1
        return 기본설정(trading_enabled="Y", max_position_weight="12")

    제공자, 글, 시트 = build_policy_provider(가짜서비스(RiskPolicy()), "sheet", reader=읽기)
    for _ in range(5):
        assert 제공자().max_position_weight == pytest.approx(0.12)
    assert 센횟수["n"] == 1
    assert 시트 is not None
    assert "켜짐" in 글


def test_제공자는_읽기가_터져도_죽지_않고_매매를_끈다():
    from muwon.settings.from_sheet import build_policy_provider

    def 터짐():
        raise RuntimeError("구글이 안 받음")

    제공자, 글, 시트 = build_policy_provider(
        가짜서비스(RiskPolicy(trading_enabled=True)), "sheet", reader=터짐
    )
    assert 제공자().trading_enabled is False
    assert 시트 is None
    assert "구글이 안 받음" in 글


def test_제공자는_시트값이_틀리면도_매매를_끈다():
    """검증에 걸린 시트로 그냥 돌면, 틀린 기준으로 실제 주문이 나간다."""
    from muwon.settings.from_sheet import build_policy_provider

    제공자, 글, 시트 = build_policy_provider(
        가짜서비스(RiskPolicy(trading_enabled=True)),
        "sheet",
        reader=lambda: 기본설정(max_position_weight="500"),
    )
    assert 제공자().trading_enabled is False
    assert 시트 is None
    assert "SettingsError" in 글


def test_킬스위치는_둘_다_켜져야_켜진다():
    """시트가 DB의 킬스위치를 무력화하면 안 된다. 끄는 쪽은 어디서 눌러도 먹어야 한다."""
    켠시트 = parse_settings(기본설정(trading_enabled="Y"))
    끈시트 = parse_settings(기본설정(trading_enabled="N"))

    # DB 켬 + 시트 켬 → 켜짐
    정책, 출처 = apply(RiskPolicy(trading_enabled=True), 켠시트)
    assert 정책.trading_enabled is True
    assert 출처["trading_enabled"] == "시트+DB 둘 다 켬"

    # DB 끔 + 시트 켬 → **꺼짐** (예전이라면 시트가 이겨 켜졌다)
    정책, 출처 = apply(RiskPolicy(trading_enabled=False), 켠시트)
    assert 정책.trading_enabled is False
    assert 출처["trading_enabled"] == "DB에서 끔"

    # DB 켬 + 시트 끔 → 꺼짐
    정책, 출처 = apply(RiskPolicy(trading_enabled=True), 끈시트)
    assert 정책.trading_enabled is False
    assert 출처["trading_enabled"] == "시트에서 끔"


def test_시트에_킬스위치_줄이_아예_없으면_꺼진다():
    """줄이 지워졌다고 매매가 켜지면 안 된다."""
    없음 = 기본설정()
    del 없음["trading_enabled"]
    정책, 출처 = apply(RiskPolicy(trading_enabled=True), parse_settings(없음))
    assert 정책.trading_enabled is False
    assert 출처["trading_enabled"] == "시트에 없어 꺼짐"


# ── 매도 스위치 (2026-08-25) ──────────────────────────────────
#
# 매수와 **안전한 방향이 정반대다.** 매수는 둘 다 켜야 켜지고(AND),
# 매도는 둘 다 꺼야 꺼진다(OR). 매수를 못 하면 기회를 놓칠 뿐이지만
# 매도를 못 하면 손실이 그대로 자란다.


def test_매도는_한쪽만_켜도_켜진다():
    """매수와 반대다. 시트를 잊고 안 적어도 손절은 살아 있어야 한다."""
    정책, _ = apply(RiskPolicy(sell_enabled=True), parse_settings(기본설정(sell_enabled="false")))

    assert 정책.sell_enabled is True


def test_매도는_둘_다_꺼야_꺼진다():
    정책, 출처 = apply(
        RiskPolicy(sell_enabled=False), parse_settings(기본설정(sell_enabled="false"))
    )

    assert 정책.sell_enabled is False
    assert "둘 다 꺼서" in 출처["sell_enabled"]


def test_시트에_매도_항목이_없으면_켠_것으로_본다():
    """예전 시트에는 이 칸이 없다. 없다고 손절이 멈추면 안 된다."""
    설정 = parse_settings(기본설정())
    설정.값.pop("sell_enabled", None)
    설정.덮개.pop("sell_enabled", None)

    정책, 출처 = apply(RiskPolicy(sell_enabled=True), 설정)

    assert 정책.sell_enabled is True
    assert 출처["sell_enabled"] == "시트에 없어 켜짐"


def test_매수는_반대로_한쪽만_꺼도_꺼진다():
    """두 스위치의 규칙이 반대라는 것을 나란히 못 박는다."""
    정책, _ = apply(
        RiskPolicy(trading_enabled=True), parse_settings(기본설정(trading_enabled="false"))
    )

    assert 정책.trading_enabled is False


def test_매도가_꺼져_있으면_설명이_크게_말한다():
    """조용히 꺼져 있으면 안 된다. 손절이 안 걸리는 상태다."""
    정책, 출처 = apply(
        RiskPolicy(sell_enabled=False), parse_settings(기본설정(sell_enabled="false"))
    )

    글 = describe(정책, 출처, None)
    assert "매도가 꺼져 있습니다" in 글
    assert "손절" in 글


# ── 비밀값이 로그로 새지 않는지 ────────────────────────────────────
#
# 2026-09-05에 `sync_sector_sheet.py --check`가 설정 탭을 그대로 찍으면서
# `dashboard_key` 값을 공개 로그에 흘렸다(실행 33966305484). 그 값 하나면
# 화면 단추 전부가 남의 손에 넘어간다.


def test_n8n이_읽는_이름은_모르는_이름이_아니다():
    """`dashboard_key`는 시트에 있는 것이 맞다.

    모르는 이름으로 세면 `--check`가 늘 실패로 끝난다. 실제로 그랬다."""
    설정 = parse_settings(기본설정(dashboard_key="820463"))

    assert 설정.모르는이름 == ()


def test_n8n이_읽는_값은_파싱_결과에_안_담긴다():
    """담기면 화면과 로그로 흘러 나갈 자리가 늘어난다."""
    설정 = parse_settings(기본설정(dashboard_key="820463"))

    assert "dashboard_key" not in 설정.값
    assert "dashboard_key" not in 설정.덮개


def test_설명글에_비밀값이_안_나온다():
    정책, 출처 = apply(RiskPolicy(), parse_settings(기본설정(dashboard_key="820463")))

    assert "820463" not in describe(정책, 출처, None)
