"""전략 변경 예약을 읽고 쓴다. **여기가 규칙을 지키는 자리다.**

버튼을 그리는 곳(`notify/telegram_buttons.py`)과 버튼을 받는 곳
(`scripts/telegram_control.py`)과 반영하는 곳(`scripts/apply_strategy_change.py`)
이 전부 이 파일을 통해서만 상태를 바꾼다. 세 곳이 각자 DB를 만지면 규칙이
세 벌이 되고, 셋이 어긋나도 아무것도 안 빨개진다.

## 규칙 다섯

**① 예약은 동시에 하나만 있다.** 새로 고르면 앞의 것은 취소된다. 둘을
동시에 예약하면 다음 날 무엇이 반영되는지 알 수 없다.

**② 한 번 누르면 예약, 같은 것을 다시 누르면 취소다(2026-09-01에 바꿈).**
전에는 고름과 확정 두 단계였다. 사람이 매번 두 번 누르는 것이 번거롭다고 해서
한 번으로 줄였다.

손가락이 스치는 것에 대한 방어는 그대로 남는다. **예약은 그날 아무것도
바꾸지 않고 다음 거래일 08:20에 반영한다.** 그 사이에 같은 버튼을 다시 누르면
없던 일이 된다. 저녁 내내 되돌릴 시간이 있다.

**③ 지난 날짜 버튼은 안 듣는다.** 어제 온 메시지의 버튼이 그대로 살아
있다. 어제 판단으로 오늘 전략을 바꾸면 안 된다.

**④ 확정된 것만 반영한다.** 버튼을 누르면 바로 `확정`으로 적는다. `고름`은
두 단계였을 때 쓰던 상태이고, 옛 줄이 남아 있을 수 있어서 그대로 둔다.
`고름`인 줄은 반영하지 않는다.

**⑤ 등록되지 않은 전략은 예약도 안 된다.** 버튼 자료가 손으로 만들어질
수 있다. 목록에 없는 키를 받으면 거절한다.

## 반영이 막히는 자리

반영할 때 다시 한 번 본다. 예약과 반영 사이에 밤이 하나 있어서, 그 사이에
상황이 바뀔 수 있다.

- 최소 운용기간이 안 지났다
- 예약한 전략이 지금 걸린 것과 같아졌다
- 전략 목록에서 사라졌다

막히면 `막힘`으로 적고 까닭을 남긴다. **조용히 안 바뀌면 원인을 못 찾는다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from muwon.db.models import StrategyChangeRow

고름, 확정, 반영, 취소, 되돌림, 막힘 = (
    "고름", "확정", "반영", "취소", "되돌림", "막힘",
)

#: 아직 반영되지 않은 상태들. 이 중 하나라도 있으면 새로 고를 때 취소된다.
살아있는것 = (고름, 확정)


@dataclass(frozen=True)
class 예약결과:
    """무엇을 했나. `된것`이 False면 `말`에 왜 안 됐는지가 있다."""

    된것: bool
    말: str
    줄: StrategyChangeRow | None = None


def 지금예약(session: Session) -> StrategyChangeRow | None:
    """아직 반영되지 않은 예약. 없으면 None.

    둘 이상 있으면 제일 최근 것을 돌려준다. 규칙상 그럴 수 없지만, 그렇게
    됐을 때 옛것을 반영하는 쪽이 더 나쁘다."""
    return session.scalars(
        select(StrategyChangeRow)
        .where(StrategyChangeRow.상태.in_(살아있는것))
        .order_by(StrategyChangeRow.id.desc())
        .limit(1)
    ).first()


def 마지막반영(session: Session) -> StrategyChangeRow | None:
    """제일 최근에 실제로 바꾼 줄. 최소 운용기간을 세는 데 쓴다."""
    return session.scalars(
        select(StrategyChangeRow)
        .where(StrategyChangeRow.상태 == 반영)
        .order_by(StrategyChangeRow.반영때.desc())
        .limit(1)
    ).first()


#: 상태 DB의 시각 칸은 `datetime.utcnow()`로 적힌다. 한국 시간과 아홉 시간
#: 차이라, 08:20에 반영한 줄은 UTC로 **전날 23:20**이다. 날짜를 그냥 비교하면
#: 오늘 바꾼 것을 어제 것으로 읽고 "변경 없음"이라고 알린다.
_서울시차 = timedelta(hours=9)


def 오늘변경(session: Session, 오늘: date) -> StrategyChangeRow | None:
    """오늘 아침 반영이 무엇을 했나. 아무 일도 없었으면 None.

    `오늘`은 한국 날짜다.

    바꾼 줄(`반영`)과 막힌 줄(`막힘`) 둘 다 본다. 막힌 날은 전략이 안 바뀐
    채로 매수 후보가 나오므로, 그 사실을 아는 것이 바꾼 날보다 더 중요하다.

    시각 칸이 둘이라 각각 본다. 반영한 줄은 `반영때`에, 막힌 줄은 `바뀐때`에
    적힌다. 한쪽만 보면 다른 쪽을 통째로 놓친다."""
    for 상태, 칸이름 in ((반영, "반영때"), (막힘, "바뀐때")):
        칸 = getattr(StrategyChangeRow, 칸이름)
        줄 = session.scalars(
            select(StrategyChangeRow)
            .where(StrategyChangeRow.상태 == 상태, 칸.is_not(None))
            .order_by(칸.desc())
            .limit(1)
        ).first()
        if 줄 is None:
            continue
        때 = getattr(줄, 칸이름, None)
        if 때 is not None and (때 + _서울시차).date() == 오늘:
            return 줄
    return None


def 이력(session: Session, 몇줄: int = 20) -> list[StrategyChangeRow]:
    """화면에 그릴 변경 이력. 반영된 것과 되돌린 것만 본다.

    고르다 만 것과 취소한 것은 뺀다. 그건 판단 과정이지 변경 이력이 아니다."""
    return list(
        session.scalars(
            select(StrategyChangeRow)
            .where(StrategyChangeRow.상태.in_((반영, 되돌림)))
            .order_by(StrategyChangeRow.id.desc())
            .limit(몇줄)
        )
    )


def 누르기(
    session: Session,
    제안일: date,
    오늘: date,
    이전전략: str,
    새전략: str,
    아는전략들,
    근거구간: str = "",
    등급: str = "",
    이전수익률: float | None = None,
    새수익률: float | None = None,
    거래수: int = 0,
    사유: str = "",
    승인경로: str = "텔레그램",
) -> 예약결과:
    """버튼 한 번. **같은 것을 다시 누르면 취소된다.**

    누르는 순간 `확정`으로 적는다. 그래도 그날 전략은 안 바뀐다. 다음 거래일
    08:20에 반영하므로 그 전까지 같은 버튼을 다시 눌러 되돌릴 수 있다.

    2026-09-01에 두 단계에서 한 단계로 줄였다. 두 번 누르는 것이 번거롭다는
    지적을 받았고, 되돌릴 시간이 하룻밤 있으므로 방어가 사라지지는 않는다."""
    if 새전략 not in set(아는전략들):
        return 예약결과(False, f"등록되지 않은 전략입니다: {새전략}")
    if 제안일 != 오늘:
        # 규칙 ③. 어제 온 메시지의 버튼이 대화방에 그대로 살아 있다. 어제
        # 판단으로 오늘 전략을 바꾸면 안 된다. 두 단계였을 때는 확정하기가
        # 이걸 봤다. 한 단계가 되면서 여기로 옮겼다.
        return 예약결과(
            False,
            f"{제안일}에 낸 제안이라 이제 못 씁니다. 오늘 온 목록에서 눌러 주세요.",
        )

    앞 = 지금예약(session)

    # 같은 것을 다시 눌렀다. 취소로 읽는다. 이 자리가 취소 버튼을 대신한다.
    if 앞 is not None and 앞.새전략 == 새전략:
        앞.상태 = 취소
        앞.막힌까닭 = "같은 버튼을 다시 눌러 취소되었습니다."
        앞.바뀐때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
        session.flush()
        return 예약결과(True, "예약을 취소했습니다. 전략은 그대로입니다.", None)

    if 새전략 == 이전전략:
        return 예약결과(False, "이미 설정되어 있는 전략입니다.")

    # 규칙 ①. 다른 예약이 남아 있으면 취소한다. 둘을 동시에 두면 안 된다.
    if 앞 is not None:
        앞.상태 = 취소
        앞.막힌까닭 = "다른 전략을 새로 선택해 취소되었습니다."
        앞.바뀐때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
    줄 = StrategyChangeRow(
        제안일=제안일,
        상태=확정,
        이전전략=이전전략,
        새전략=새전략,
        근거구간=근거구간,
        등급=등급,
        이전수익률=이전수익률,
        새수익률=새수익률,
        거래수=거래수,
        사유=사유,
        승인경로=승인경로,
    )
    session.add(줄)
    session.flush()
    return 예약결과(
        True,
        "다음 거래일 매수 후보 산출 전에 반영합니다. 같은 버튼을 다시 누르면 "
        "취소됩니다.",
        줄,
    )


def 취소하기(session: Session, 까닭: str = "사람이 취소했습니다.") -> 예약결과:
    """반영 전까지는 언제든 되돌릴 수 있다."""
    줄 = 지금예약(session)
    if 줄 is None:
        return 예약결과(False, "취소할 예약이 없습니다.")
    줄.상태 = 취소
    줄.막힌까닭 = 까닭
    줄.바뀐때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
    session.flush()
    return 예약결과(True, "예약을 취소했습니다. 전략은 그대로입니다.", 줄)


def 지난거래일수(마지막: StrategyChangeRow | None, 오늘: date) -> int | None:
    """마지막 반영으로부터 며칠 지났나(달력 기준). 반영 기록이 없으면 None.

    **거래일이 아니라 달력 날짜다.** 거래일로 세려면 시세가 있어야 하는데,
    반영 스크립트는 시세를 안 받는다. 달력으로 세면 실제 거래일보다 길게
    잡히므로 **막는 쪽이 아니라 푸는 쪽으로 틀린다.** 그래서 부르는 쪽이
    최소 운용기간을 달력 기준으로 정해야 한다."""
    if 마지막 is None or 마지막.반영때 is None:
        return None
    return (오늘 - 마지막.반영때.date()).days


def 반영할것(
    session: Session,
    오늘: date,
    지금전략: str,
    아는전략들,
    최소운용일: int = 0,
) -> tuple[StrategyChangeRow | None, str]:
    """(반영할 줄, 못 하는 까닭). 둘 중 하나만 찬다.

    **여기서 다시 검사한다.** 예약과 반영 사이에 밤이 하나 있어서 그 사이에
    상황이 바뀔 수 있다."""
    줄 = 지금예약(session)
    if 줄 is None:
        return None, ""
    if 줄.상태 != 확정:
        return None, (
            f"{줄.새전략} 예약이 확정되지 않았습니다. 확인 버튼을 누르지 않은 상태입니다."
        )
    if 줄.새전략 not in set(아는전략들):
        return None, f"예약한 전략이 목록에 없습니다: {줄.새전략}"
    if 줄.새전략 == 지금전략:
        return None, f"예약한 전략이 이미 설정되어 있습니다: {줄.새전략}"

    if 최소운용일 > 0:
        지난 = 지난거래일수(마지막반영(session), 오늘)
        if 지난 is not None and 지난 < 최소운용일:
            return None, (
                f"직전 변경으로부터 {지난}일이 지났습니다. "
                f"최소 운용기간 {최소운용일}일에 못 미칩니다."
            )
    return 줄, ""


def 반영표시(session: Session, 줄: StrategyChangeRow) -> None:
    줄.상태 = 반영
    줄.반영때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
    session.flush()


def 막힘표시(session: Session, 까닭: str) -> StrategyChangeRow | None:
    """반영이 막혔다는 사실을 줄에 남긴다.

    상태를 `막힘`으로 바꾸므로 다음 회차에 다시 시도하지 않는다. 매일 같은
    이유로 막히는 것을 매일 알리면 알림이 흔해진다."""
    줄 = 지금예약(session)
    if 줄 is None:
        return None
    줄.상태 = 막힘
    줄.막힌까닭 = 까닭
    줄.바뀐때 = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
    session.flush()
    return 줄
