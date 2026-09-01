"""매매 기준 설명 검증.

설명이 코드와 어긋나면 아무 설명도 없는 것보다 나쁘다 — 화면을 믿고
판단하게 되기 때문이다."""

import pytest

from muwon.dashboard.strategy_rules import Rules, common_rules, describe, exit_rules
from muwon.settings.schema import RiskPolicy
from muwon.strategy.breakout import VolumeSurgeParams, VolumeSurgeStrategy
from muwon.strategy.registry import build_strategy, list_definitions


def _text(rules: Rules) -> str:
    return " ".join(rules.산다 + rules.판다 + rules.참고)


@pytest.mark.parametrize("definition", list_definitions(), ids=lambda d: d.key)
def test_every_registered_strategy_explains_itself(definition):
    """새 전략을 등록하고 설명을 안 붙이면 여기서 잡힌다.

    안 잡으면 화면에 파라미터 덤프만 뜨는데, 그건 아무도 못 읽는다."""
    strategy = build_strategy(definition.key)
    rules = describe(strategy)
    assert rules.설명있음, f"{definition.key}: strategy_rules.py에 문장을 붙여야 한다"
    assert rules.산다, f"{definition.key}: 사는 조건이 비었다"

    # 파는 조건은 전략·리스크 정책·엔진에 흩어져 있으므로 합친 쪽으로 본다.
    # 손절 말고 다른 청산 수단이 하나도 없는 전략은 '언제 파는가'가 없는
    # 것이라, 그건 설명이 아니라 전략의 결함이다.
    조건, _ = exit_rules(strategy, RiskPolicy())
    assert 조건[0].startswith("**손절"), f"{definition.key}: 손절이 첫 줄이어야 한다"
    assert len(조건) >= 2, f"{definition.key}: 손절 외에 파는 조건이 하나도 없다"


def test_the_sentences_are_built_from_live_parameters():
    """설명을 손으로 적어 두면 파라미터를 바꿔도 옛 글이 남는다.

    같은 전략 클래스에 다른 숫자를 넣었을 때 문장이 따라 바뀌어야 한다."""
    보통 = VolumeSurgeStrategy(VolumeSurgeParams())
    빡센 = VolumeSurgeStrategy(VolumeSurgeParams(volume_surge_ratio=3.0, holding_days=3))

    assert "2배" in _text(describe(보통))
    assert "3배" in _text(describe(빡센))
    assert "2배" not in _text(describe(빡센)), "옛 숫자가 남으면 설명이 거짓말이 된다"

    # 보유 기간은 엔진이 검사하는 값이라 청산 조건 쪽에 나온다
    assert "5거래일" in " ".join(exit_rules(보통, RiskPolicy())[0])
    assert "3거래일" in " ".join(exit_rules(빡센, RiskPolicy())[0])


def test_an_optional_condition_only_shows_when_it_is_on():
    """켜지지도 않은 조건을 설명에 적으면 '왜 안 사지'를 엉뚱한 데서 찾게 된다."""
    켬 = describe(build_strategy("macd_cross_positive"))
    끔 = describe(build_strategy("macd_cross"))
    assert any("0보다 클 때" in line for line in 켬.산다)
    assert not any("0보다 클 때" in line for line in 끔.산다)


def test_an_unknown_strategy_shows_its_settings_instead_of_nothing():
    """설명을 안 붙인 전략이라도 빈 화면을 주면 안 된다."""

    class 낯선파라미터:
        pass

    class 낯선전략:
        params = 낯선파라미터()

    rules = describe(낯선전략())
    assert not rules.설명있음
    assert rules.참고, "최소한 '설명이 없다'는 사실은 보여야 한다"


def test_common_rules_carry_the_actual_policy_numbers():
    policy = RiskPolicy(
        max_position_weight=0.25,
        stop_loss_pct=-0.07,
        daily_loss_limit_pct=-0.03,
        max_concurrent_positions=4,
        trading_enabled=True,
    )
    text = " ".join(common_rules(policy, 60, "market_cap"))
    assert "25%" in text
    assert "7%" in text
    assert "3%" in text
    assert "4종목" in text
    assert "60개" in text


def test_the_kill_switch_wording_says_stops_do_still_run():
    """'꺼짐'을 '방치'로 읽으면 안 된다 — 보유분 손절은 계속 작동한다."""
    off = " ".join(common_rules(RiskPolicy(trading_enabled=False), 60, "market_cap"))
    assert "손절은 계속 동작합니다" in off


def test_selling_conditions_include_the_stop_loss_not_just_the_strategy():
    """"매도는 기간밖에 없냐"는 질문을 받았다.

    실제로는 손절이 항상 먼저 걸리는데, 그게 리스크 정책 쪽에 있다는 이유로
    화면의 다른 칸에 적혀 있었다. 파는 조건은 어디에 설정돼 있든 한자리에
    모여 있어야 한다."""
    policy = RiskPolicy(stop_loss_pct=-0.05)
    조건, _ = exit_rules(build_strategy("volume_surge_5d"), policy)
    합친글 = " ".join(조건)

    assert "손절" in 합친글, "손절이 빠지면 '기간밖에 없다'로 읽힌다"
    assert "5%" in 합친글
    assert "5거래일" in 합친글
    assert 조건[0].startswith("**손절"), "엔진이 손절을 먼저 보므로 순서도 그래야 한다"


def test_it_says_out_loud_that_there_is_no_take_profit():
    """없는 기능을 안 적으면 '있는데 안 보이는 것'과 구분이 안 된다."""
    _, 주의 = exit_rules(build_strategy("volume_surge_5d"), RiskPolicy())
    assert any("익절" in line for line in 주의)


def test_a_strategy_with_its_own_sell_signal_shows_it_too():
    조건, _ = exit_rules(build_strategy("golden_cross_20_60"), RiskPolicy())
    합친글 = " ".join(조건)
    assert "손절" in 합친글
    assert "전략 매도 신호" in 합친글
    assert "데드크로스" in 합친글


def test_turning_on_the_atr_stop_changes_what_is_shown():
    """꺼진 기능을 켜진 것처럼 적으면 '왜 안 팔렸지'를 엉뚱한 데서 찾게 된다."""
    끔 = " ".join(exit_rules(build_strategy("volume_surge_5d"), RiskPolicy())[0])
    켬 = " ".join(
        exit_rules(
            build_strategy("volume_surge_5d"),
            RiskPolicy(atr_stop_enabled=True, trailing_stop_enabled=True),
        )[0]
    )
    assert "ATR" not in 끔
    assert "ATR" in 켬 and "트레일링" in 켬


def test_the_off_switches_are_disclosed_while_off():
    _, 주의 = exit_rules(build_strategy("volume_surge_5d"), RiskPolicy())
    assert any("해제되어 있습니다" in line for line in 주의)


def test_a_combined_strategy_shows_each_members_conditions():
    """묶음 이름만 보여 주면 무엇을 보고 사는지 알 수 없다.

    특히 묶음 안의 전략은 어댑터로 감싸여 들어와서, 껍데기만 보면
    '설명 없음'이 뜬다 — 실제로 그렇게 나왔다."""
    from muwon.strategy.registry import build_strategies

    묶음 = build_strategies(["volume_surge_5d", "golden_cross_20_60"], "AND")
    규칙 = describe(묶음)
    합친글 = _text(규칙)

    assert 규칙.설명있음
    assert "모두" in 규칙.산다[0], "AND라는 것이 첫 줄에 있어야 한다"
    assert "거래량" in 합친글, "묶인 전략 각각의 조건이 보여야 한다"
    assert "골든크로스" in 합친글
    assert "2배" in 합친글, "숫자까지 그대로 나와야 한다"


def test_a_combined_strategy_says_selling_is_always_or():
    from muwon.strategy.registry import build_strategies

    규칙 = describe(build_strategies(["volume_surge_5d", "golden_cross_20_60"], "AND"))
    assert any("하나라도" in line for line in 규칙.참고)


def test_combined_exit_rules_gather_every_members_sell_signal():
    from muwon.strategy.registry import build_strategies

    조건, _ = exit_rules(
        build_strategies(["volume_surge_5d", "golden_cross_20_60"], "OR"), RiskPolicy()
    )
    합친글 = " ".join(조건)
    assert "손절" in 합친글
    assert "보유기간 만료" in 합친글
    assert "데드크로스" in 합친글
