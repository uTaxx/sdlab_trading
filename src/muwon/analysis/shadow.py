"""검토가 낸 순위를 남겨 두고, 지평이 지난 뒤에 실제로 어떻게 됐는지 다시 잰다.

**고른 것만이 아니라 안 고른 것도 잰다.** 그래서 이름이 그림자다. 사람이
바꾸지 않기로 한 날에도 "그때 바꿨으면 어땠나"가 숫자로 남는다.

## 왜 필요한가

지금 구조에서는 버튼을 누른 것만 기록에 남는다. 그러면 시간이 지나도
답할 수 없는 질문이 생긴다.

- 이 검토가 내는 후보를 매번 따랐으면 지금보다 나았을까
- 안 바꾸고 버틴 판단이 맞았을까
- 순위 1위라는 숫자가 다음 한 달을 실제로 맞히기는 하나

이 저장소는 기각된 가설을 자산으로 취급한다(`docs/가설기록.json`).
안 고른 쪽을 안 재면 그 자산이 한쪽만 쌓인다.

## 어떻게 재나

    제안일          지평이 지난 날
      │                  │
      ├── 후보 전략 ─────┤   이 구간을 후보 전략으로 실행했으면 몇 %
      └── 그때 전략 ─────┘   같은 구간을 그때 설정돼 있던 전략으로 실행하면 몇 %
                             둘의 차이가 이 표가 남기는 값이다

같은 종목, 같은 기간, 같은 기초설정으로 실행한다. 다른 것은 전략 하나뿐이다.
그래야 차이를 전략 탓으로 읽을 수 있다.

## 이 숫자로 무엇을 하나

**아무것도 자동으로 바꾸지 않는다.** 사실만 화면과 알림에 적는다. 이 값이
좋다고 전략을 자동으로 갈아 끼우면 그건 결국 최근 구간에 맞추는 것이고,
이 저장소가 이미 여러 번 기각한 방식이다(설계안 §36).

표본이 `최소표본`에 못 미치면 숫자를 내되 판단하지 않는다고 적는다. 몇 건으로
"신호가 맞더라"를 말하면 그다음부터 아무도 그 문장을 안 읽는다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from muwon.db.models import StrategyChangeRow, StrategyShadowRow

열림, 닫힘, 계산못함 = "열림", "닫힘", "계산못함"

#: 제안일부터 이만큼 지난 뒤에 뒤 수익률을 잰다. 30일은 매매가 몇 건은 나오는
#: 길이이면서, 다음 검토까지 기다리기에 너무 길지 않은 값으로 골랐다. 거래
#: 수는 여전히 적다. 한 줄로 판단하지 말고 쌓인 것으로 본다.
추적일수 = 30

#: 구간마다 순위 위쪽 몇 개를 남기나. 1위만 남기면 "2위였으면 어땠나"를
#: 나중에 물을 수가 없다.
기록자리 = 3

#: 이만큼 쌓이기 전에는 판단하지 않는다. 기간 검증의 거래 수 문턱과 같은 값이다.
최소표본 = 20


@dataclass(frozen=True)
class 비교:
    """한 날 한 구간에서 '따랐을 때'와 '안 따랐을 때'를 나란히 둔 것."""

    제안일: date
    구간: str
    지금전략: str
    후보전략: str
    지금뒤: float
    후보뒤: float
    #: 그 구간에 실제로 몇 건 샀나. **0건이면 수익률 0%는 지킨 것이 아니라
    #: 아무것도 안 산 것이다.** 숫자만 보면 둘이 구별되지 않는다.
    지금거래수: int
    후보거래수: int
    지난날수: int
    등급: str
    #: 그날 실제로 버튼이 나갔나. 안 나간 날도 순위 1위로 비교한다.
    버튼있었나: bool
    #: 사람이 실제로 이 후보를 골라서 반영했나.
    골랐나: bool

    @property
    def 차이(self) -> float:
        """후보에서 지금 것을 뺀 값. 플러스면 바꿨을 때가 나았다는 뜻이다."""
        return self.후보뒤 - self.지금뒤

    @property
    def 매수없었나(self) -> bool:
        """한쪽이라도 한 건도 안 샀나. 그러면 이 차이는 전략끼리의 차이가
        아니라 한쪽이 쉬었다는 뜻이다."""
        return self.지금거래수 == 0 or self.후보거래수 == 0


@dataclass(frozen=True)
class 요약:
    표본수: int
    나은수: int
    못한수: int
    같은수: int
    평균차이: float
    중앙값차이: float
    구간별: dict[str, tuple[int, float]]
    #: 사람이 실제로 따라간 회차 수. 표본수와 같으면 매번 따라갔다는 뜻이다.
    따라간수: int
    #: 한쪽이 한 건도 안 사서 수익률이 0%로 나온 회차 수.
    매수없던수: int = 0

    @property
    def 믿을만한가(self) -> bool:
        return self.표본수 >= 최소표본


def 기록하기(
    session: Session,
    제안일: date,
    순위들: dict,
    지금키: str,
    등급: str,
    제안키들: dict[str, str],
    자리수: int = 기록자리,
) -> int:
    """오늘 검토가 낸 순위를 남긴다. 남긴 줄 수를 돌려준다.

    `순위들`은 {구간이름: 구간순위}, `제안키들`은 {구간이름: 버튼으로 나간 키}다.
    후보가 없던 구간은 빈 문자열을 넣는다.

    **같은 날 두 번 실행하면 앞의 것을 지우고 다시 쓴다.** 두 벌이 남으면
    나중에 비교할 때 같은 날이 두 번 세어진다."""
    아직안잰것 = session.scalars(
        select(StrategyShadowRow).where(
            StrategyShadowRow.제안일 == 제안일,
            StrategyShadowRow.상태 == 열림,
        )
    ).all()
    for 낡은 in 아직안잰것:
        session.delete(낡은)

    남긴수 = 0
    for 구간이름, 순위 in 순위들.items():
        제안키 = 제안키들.get(구간이름, "")
        # 순위 위쪽 몇 개에 더해, 그때 설정돼 있던 전략과 버튼으로 나간 후보는
        # 순위가 아래여도 반드시 남긴다. 비교하는 짝이 둘 다 있어야 한다.
        남길키 = {ㄱ.키 for ㄱ in 순위.차례[:자리수]}
        if 지금키:
            남길키.add(지금키)
        if 제안키:
            남길키.add(제안키)

        for 자리, ㄱ in enumerate(순위.차례, 1):
            if ㄱ.키 not in 남길키:
                continue
            session.add(
                StrategyShadowRow(
                    제안일=제안일,
                    구간=구간이름,
                    전략=ㄱ.키,
                    자리=자리,
                    지금것=(ㄱ.키 == 지금키),
                    제안것=(ㄱ.키 == 제안키 and bool(제안키)),
                    등급=등급,
                    제안시수익률=ㄱ.수익률,
                    제안시거래수=ㄱ.거래수,
                    상태=열림,
                )
            )
            남긴수 += 1
    return 남긴수


def 잴것(session: Session, 오늘: date, 일수: int = 추적일수) -> list[StrategyShadowRow]:
    """지평이 지나 이제 뒤 수익률을 잴 수 있는 줄들."""
    기한 = 오늘 - timedelta(days=일수)
    return list(
        session.scalars(
            select(StrategyShadowRow)
            .where(
                StrategyShadowRow.상태 == 열림,
                StrategyShadowRow.제안일 <= 기한,
            )
            .order_by(StrategyShadowRow.제안일, StrategyShadowRow.구간)
        )
    )


def 골랐나확인(session: Session, 제안일: date, 전략: str) -> bool:
    """그날 그 전략이 실제로 반영됐나. 버튼을 눌러 반영까지 간 것만 센다."""
    if not 전략:
        return False
    return session.scalars(
        select(StrategyChangeRow.id)
        .where(
            StrategyChangeRow.제안일 == 제안일,
            StrategyChangeRow.새전략 == 전략,
            StrategyChangeRow.상태 == "반영",
        )
        .limit(1)
    ).first() is not None


def 재기(session: Session, 줄들, 재기함수, 오늘: date) -> tuple[int, int]:
    """뒤 수익률을 채운다. (잰 줄, 못 잰 줄)을 돌려준다.

    `재기함수(전략키, 시작, 끝)`는 `period_check.기간성적` 또는 None을 준다.
    같은 (전략, 시작)을 여러 줄이 쓰므로 여기서 한 번만 재고 나눠 쓴다.

    **못 잰 것을 0%로 채우면 안 된다.** 못 잰 것과 안 움직인 것은 다르다."""
    잰것: dict[tuple[str, date], object] = {}
    센것 = 못센것 = 0
    for 줄 in 줄들:
        열쇠 = (줄.전략, 줄.제안일)
        if 열쇠 not in 잰것:
            try:
                잰것[열쇠] = 재기함수(줄.전략, 줄.제안일, 오늘)
            except Exception as 탈:  # noqa: BLE001 (하나가 터져도 나머지는 재야 한다)
                잰것[열쇠] = f"{type(탈).__name__}: {탈}"
        성적 = 잰것[열쇠]

        줄.잰날 = 오늘
        줄.지난날수 = (오늘 - 줄.제안일).days
        줄.바뀐때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
        줄.골랐나 = 골랐나확인(session, 줄.제안일, 줄.전략)

        if 성적 is None or isinstance(성적, str):
            줄.상태 = 계산못함
            줄.못한까닭 = 성적 if isinstance(성적, str) else "시세가 모자랍니다"
            못센것 += 1
            continue

        줄.상태 = 닫힘
        줄.뒤수익률 = 성적.metrics.total_return_pct
        줄.뒤거래수 = 성적.metrics.num_trades
        줄.뒤최대낙폭 = 성적.metrics.max_drawdown_pct
        줄.못한까닭 = 성적.모자람
        센것 += 1
    return 센것, 못센것


def 잰줄들(session: Session, 몇줄: int = 300) -> list[StrategyShadowRow]:
    """다 잰 줄들. 최근 것부터."""
    return list(
        session.scalars(
            select(StrategyShadowRow)
            .where(StrategyShadowRow.상태 == 닫힘)
            .order_by(StrategyShadowRow.제안일.desc(), StrategyShadowRow.구간)
            .limit(몇줄)
        )
    )


def 비교하기(줄들) -> list[비교]:
    """줄들을 (제안일, 구간)으로 묶어 '따랐을 때 vs 안 따랐을 때'를 만든다.

    버튼이 안 나간 날도 순위 1위를 후보 자리에 놓고 비교한다. 그날 후보를
    안 낸 판단이 맞았는지도 같은 자로 재야 한다."""
    묶음: dict[tuple[date, str], list] = {}
    for 줄 in 줄들:
        묶음.setdefault((줄.제안일, 줄.구간), []).append(줄)

    나온것: list[비교] = []
    for (제안일, 구간이름), 안에것 in sorted(묶음.items()):
        지금줄 = next((ㅈ for ㅈ in 안에것 if ㅈ.지금것), None)
        후보줄 = next((ㅈ for ㅈ in 안에것 if ㅈ.제안것), None)
        버튼있었나 = 후보줄 is not None
        if 후보줄 is None:
            후보줄 = min(안에것, key=lambda ㅈ: ㅈ.자리)
        if 지금줄 is None or 후보줄 is 지금줄:
            continue
        if 지금줄.뒤수익률 is None or 후보줄.뒤수익률 is None:
            continue
        나온것.append(
            비교(
                제안일=제안일,
                구간=구간이름,
                지금전략=지금줄.전략,
                후보전략=후보줄.전략,
                지금뒤=지금줄.뒤수익률,
                후보뒤=후보줄.뒤수익률,
                지금거래수=int(지금줄.뒤거래수 or 0),
                후보거래수=int(후보줄.뒤거래수 or 0),
                지난날수=후보줄.지난날수,
                등급=후보줄.등급,
                버튼있었나=버튼있었나,
                골랐나=bool(후보줄.골랐나),
            )
        )
    return 나온것


def 모아보기(비교들: list[비교]) -> 요약:
    차이들 = [ㅂ.차이 for ㅂ in 비교들]
    구간별: dict[str, tuple[int, float]] = {}
    for 이름 in sorted({ㅂ.구간 for ㅂ in 비교들}):
        안에것 = [ㅂ.차이 for ㅂ in 비교들 if ㅂ.구간 == 이름]
        구간별[이름] = (len(안에것), statistics.median(안에것))
    return 요약(
        표본수=len(비교들),
        나은수=sum(1 for ㄷ in 차이들 if ㄷ > 0),
        못한수=sum(1 for ㄷ in 차이들 if ㄷ < 0),
        같은수=sum(1 for ㄷ in 차이들 if ㄷ == 0),
        평균차이=statistics.fmean(차이들) if 차이들 else 0.0,
        중앙값차이=statistics.median(차이들) if 차이들 else 0.0,
        구간별=구간별,
        따라간수=sum(1 for ㅂ in 비교들 if ㅂ.골랐나),
        매수없던수=sum(1 for ㅂ in 비교들 if ㅂ.매수없었나),
    )


def 학습글(ㅇ: 요약, 일수: int = 추적일수) -> str:
    """쌓인 것을 한 문단으로 적는다. **바꾸라는 말은 하지 않는다.**

    이 문장은 텔레그램과 시트와 화면 세 곳에 그대로 간다. 사람에게 가는
    글이므로 재다·잰다 같은 말을 쓰지 않는다. 계산한다로 적는다.

    한 구간에서 좋았던 쪽으로 옮기는 것이 곧 과최적화라, 여기서는 무엇을
    하라는 문장을 아예 만들지 않는다. 사실만 적고 판단은 사람이 한다."""
    if ㅇ.표본수 == 0:
        return (
            f"지난 검토의 후보를 {일수}일 뒤에 다시 계산한 기록이 아직 "
            f"없습니다. 오늘 산출한 후보는 {일수}일 뒤에 이 자리에 표시됩니다."
        )

    앞 = (
        f"지난 검토의 후보를 {일수}일 뒤에 다시 계산했습니다. "
        f"표본 {ㅇ.표본수}건 가운데 후보가 더 나았던 회차가 {ㅇ.나은수}건, "
        f"못했던 회차가 {ㅇ.못한수}건입니다. "
        f"차이의 중앙값은 {ㅇ.중앙값차이:+.2f}%p입니다."
    )
    if ㅇ.매수없던수:
        # 0%가 "지켰다"로 읽히면 안 된다. 이 저장소가 순위에서 거래 0건을
        # 빼는 것과 같은 이유다.
        앞 += (
            f" 이 가운데 {ㅇ.매수없던수}건은 한쪽이 그 기간에 한 종목도 "
            "매수하지 않아 수익률이 0%로 나온 것입니다."
        )
    if not ㅇ.믿을만한가:
        return (
            앞 + f" 표본이 {최소표본}건에 못 미쳐 이 숫자로는 아직 판단하지 "
            "않습니다."
        )
    return 앞 + f" 사람이 실제로 따라간 회차는 {ㅇ.따라간수}건입니다."
