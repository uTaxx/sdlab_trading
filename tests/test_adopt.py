"""증권사에만 있는 종목을 들일 때의 판단.

여기가 틀리면 엔진이 실제와 다른 보유 상태 위에서 손절을 건다. 없는
종목을 팔려 하거나, 있는 종목을 안 지킨다."""

from datetime import date

from muwon.db.models import PositionRow
from muwon.domain.types import Holding
from muwon.execution.adopt import ADOPTED, plan, 맞출평가금

진입일 = date(2026, 8, 24)


def 잔고종목(symbol="403870", name="HPSP", quantity=2, avg=45_050.0, now=45_600.0) -> Holding:
    return Holding(
        symbol=symbol,
        name=name,
        quantity=quantity,
        avg_buy_price=avg,
        current_price=now,
        eval_amount=quantity * now,
        pnl_amount=quantity * (now - avg),
    )


def db종목(symbol="403870", quantity=2) -> PositionRow:
    return PositionRow(
        symbol=symbol, quantity=quantity, entry_price=45_050.0, entry_date=진입일
    )


def test_우리가_모르는_종목을_들인다():
    계획 = plan([잔고종목()], {}, 진입일)

    assert 계획.할일있나
    (pos,) = 계획.들일것
    assert pos.symbol == "403870"
    assert pos.quantity == 2
    assert pos.entry_price == 45_050.0  # 증권사의 평균매입가가 유일한 사실이다
    assert pos.entry_date == 진입일
    assert 계획.이름표["403870"] == "HPSP"


def test_들인_종목은_전략_성적에_섞이지_않는다():
    """우리가 산 게 아니니 어느 가설의 성적으로도 잡히면 안 된다."""
    (pos,) = plan([잔고종목()], {}, 진입일).들일것

    assert pos.strategy_key == ADOPTED
    assert pos.strategy_key != ""  # 빈 값이면 '전략 미상'과 구분이 안 된다


def test_이미_아는_종목은_건드리지_않는다():
    계획 = plan([잔고종목()], {"403870": db종목()}, 진입일)

    assert not 계획.할일있나
    assert not 계획.수량다른것


def test_수량이_다르면_알리기만_하고_덮지_않는다():
    """부분 체결일 수도, 우리 버그일 수도 있다. 자동으로 고치면 사고다."""
    계획 = plan([잔고종목(quantity=5)], {"403870": db종목(quantity=2)}, 진입일)

    assert not 계획.할일있나  # 덮어쓰지 않는다
    (다름,) = 계획.수량다른것
    assert (다름.db_quantity, 다름.account_quantity) == (2, 5)


def test_우리에게만_있는_종목은_여기서_다루지_않는다():
    """이미 팔린 걸 못 지운 경우다. 지우는 판단이라 들이기와 반대다."""
    계획 = plan([], {"005930": db종목("005930")}, 진입일)

    assert not 계획.할일있나
    assert not 계획.수량다른것


def test_여러_종목이_섞여_있어도_각각_제자리로():
    계획 = plan(
        [
            잔고종목("403870", "HPSP", 2),
            잔고종목("005930", "삼성전자", 10),
            잔고종목("000660", "SK하이닉스", 7),
        ],
        {"005930": db종목("005930", 10), "000660": db종목("000660", 3)},
        진입일,
    )

    assert [p.symbol for p in 계획.들일것] == ["403870"]
    assert [d.symbol for d in 계획.수량다른것] == ["000660"]


def test_기준평가금은_현금과_보유평가액을_더한다():
    """안 맞추면 들이는 순간 손실이 난 것처럼 보여 일일 손실한도가 헛돈다."""
    assert 맞출평가금(9_910_035.0, [잔고종목(now=45_600.0)]) == 9_910_035.0 + 91_200.0


def test_보유가_없으면_기준평가금은_현금_그대로():
    assert 맞출평가금(10_000_000.0, []) == 10_000_000.0


# ── 수량 맞추기 (2026-08-25) ──────────────────────────────────
#
# 12주 매수가 전부 체결됐는데 DB엔 4주만 적혔다. 체결 조회가 1초 간격
# 3번만 보고 끝나서, 그 시점의 부분 체결을 최종값으로 기록한 것이다.
# DB에 없는 8주에는 손절이 안 걸린다.

from muwon.execution.adopt import 수량맞추기


def _보유행(symbol="066970", quantity=4, entry_price=118300.0):
    from datetime import date as _date
    return PositionRow(
        symbol=symbol, quantity=quantity, entry_price=entry_price,
        entry_date=_date(2026, 8, 25), entry_reason="거래량 2배 급증",
        strategy_key="volume_surge_5d",
    )


def _계좌행(symbol="066970", quantity=12, avg=118300.0, cur=119000.0):
    return Holding(symbol=symbol, name=f"이름{symbol}", quantity=quantity,
                   avg_buy_price=avg, current_price=cur,
                   eval_amount=quantity * cur, pnl_amount=quantity * (cur - avg))


def test_이름을_주면_계좌_수량으로_맞춘다():
    나온것 = 수량맞추기(["066970"], [_계좌행()], {"066970": _보유행()})

    assert [(p.symbol, p.quantity) for p in 나온것] == [("066970", 12)]


def test_이름을_안_주면_안_건드린다():
    """수량이 달라도 사람이 이름을 줘야 한다. 원인이 여럿이고 겉이 같다."""
    assert 수량맞추기([], [_계좌행()], {"066970": _보유행()}) == []


def test_진입가와_진입일은_DB_것을_지킨다():
    """계좌 평균매입가로 덮으면 예전 매수가 섞여 이번 회차의
    슬리피지(판단가 대비 체결가)를 되짚을 근거가 사라진다."""
    나온것 = 수량맞추기(["066970"], [_계좌행(avg=99999.0)], {"066970": _보유행()})

    assert 나온것[0].entry_price == 118300.0
    assert 나온것[0].entry_reason == "거래량 2배 급증"
    assert 나온것[0].strategy_key == "volume_surge_5d"


def test_수량이_같으면_할_일이_없다():
    assert 수량맞추기(["066970"], [_계좌행(quantity=4)], {"066970": _보유행(quantity=4)}) == []


def test_계좌에_없는_종목은_건너뛴다():
    """지우는 판단은 여기 것이 아니다. drop_phantom_holdings가 한다."""
    assert 수량맞추기(["066970"], [], {"066970": _보유행()}) == []


def test_DB에_없는_종목은_건너뛴다():
    """들이는 판단은 plan()이 한다."""
    assert 수량맞추기(["066970"], [_계좌행()], {}) == []
