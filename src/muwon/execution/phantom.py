"""기록에만 있고 계좌엔 없는 보유를 지울 때의 판단.

`adopt.py`가 "증권사에 있는데 우리에겐 없는 것"을 들인다면, 여기는 그
반대다. **우리에겐 있는데 증권사엔 없는 것.** adopt의 규칙 3이 "지우는
판단이라 들이는 것과 반대다"라며 남겨 둔 자리다.

## 왜 생겼나

2026-08-25에 `execute_approved.py --dry-run`이 운영 DB에 매수를 기록했다.
주문은 야후 시세로 흉내만 냈는데 엔진이 그 결과를 그대로 저장했다. 계좌엔
아무것도 안 샀는데 DB만 12주·51주를 들고 있다고 말하는 상태가 됐다.

그대로 두면 엔진이 그 종목을 **이미 보유로 보고 안 사거나**, 유령 포지션에
손절이 걸려 **없는 주식에 매도 주문**을 낸다.

## 지우는 것은 위험하다. 그래서 좁게 만든다

"계좌에 없으면 지운다"로 만들면 안 된다. 계좌에 없는 이유가 셋이다.

1. 흉내 낸 주문이 남긴 유령 → 지워야 한다
2. 이미 팔렸는데 우리가 기록을 못 지웠다 → 지워야 한다
3. **방금 낸 주문이 아직 계좌에 안 잡혔다** → 지우면 안 된다.
   지우는 순간 그 종목은 아무도 안 지키는 주식이 되고, 값이 반토막 나도
   손절이 안 걸린다.

3번과 1·2번은 겉모습이 같다. 자동으로 가를 방법이 없으므로 **지울 종목을
사람이 이름으로 준다.** 그리고 계좌에 실제로 있는 종목은 이름을 줘도
거부한다. 실수로 진짜 보유를 지우는 것이 이 도구가 낼 수 있는 제일 나쁜
사고다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from muwon.db.models import PositionRow
from muwon.domain.types import Holding


@dataclass(frozen=True)
class 지우기계획:
    #: 지워도 되는 종목: DB엔 있고 계좌엔 없다.
    지울것: list[PositionRow] = field(default_factory=list)
    #: 계좌에 실제로 있어서 거부한 종목. 이름을 줬어도 안 지운다.
    계좌에있어서거부: list[Holding] = field(default_factory=list)
    #: DB에 아예 없어서 지울 것이 없는 종목.
    이미없음: list[str] = field(default_factory=list)

    @property
    def 할일있나(self) -> bool:
        return bool(self.지울것)

    @property
    def 막힌게있나(self) -> bool:
        return bool(self.계좌에있어서거부)


def plan(
    지울심볼: list[str],
    db_positions: dict[str, PositionRow],
    holdings: list[Holding],
) -> 지우기계획:
    """이름으로 받은 종목 중 무엇을 지워도 되는지 가른다.

    계좌에 있는 종목은 **어떤 경우에도 지우지 않는다.** 이름을 줬다는 것은
    사람이 유령이라고 판단했다는 뜻인데, 계좌가 아니라고 말하면 사람 쪽이
    틀린 것이다. 그 다툼에서는 계좌가 이긴다. 증권사가 유일한 사실이다.
    """
    계좌 = {h.symbol: h for h in holdings}
    지울것, 거부, 이미없음 = [], [], []

    for symbol in dict.fromkeys(지울심볼):  # 같은 것을 두 번 줘도 한 번만
        if symbol in 계좌:
            거부.append(계좌[symbol])
        elif symbol not in db_positions:
            이미없음.append(symbol)
        else:
            지울것.append(db_positions[symbol])

    return 지우기계획(지울것=지울것, 계좌에있어서거부=거부, 이미없음=이미없음)


def 맞출평가금(현금: float, holdings: list[Holding]) -> float:
    """일일 손실한도가 '오늘 얼마나 잃었나'를 재는 기준점.

    안 맞추면 유령을 지우는 순간 평가금이 뚝 떨어져 손실이 난 것처럼 보이고,
    아무 일도 안 했는데 한도에 걸려 그날 매수가 전부 막힌다.
    """
    return 현금 + sum(h.quantity * h.current_price for h in holdings)
