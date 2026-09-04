"""측정 결과를 상태 DB에 쌓고 다시 읽는다. 설계안 §6이다.

주인이 "모든 결과를 DB에 저장해 계속 학습"이라고 했다. 여기가 그 자리다.

## 무엇을 담고 무엇을 안 담나

구간을 하나하나 담으면 한 번 측정에 10만 줄이 넘는다. 상태 DB는 구글
드라이브의 파일 하나이고 워크플로가 실행마다 통째로 내려받는다. 매일 재면
09:05 매수가 점점 느려진다.

그래서 조건 하나당 요약 한 줄만 담는다. 한 번에 522줄이다. 구간을 하나하나
그리는 그림은 측정이 내놓는 JSON 파일을 쓴다.

매매 하나하나는 **지금 설정된 전략 한 벌만** 담는다. 백테스트와 실거래를
견주는 데 필요한 것이 그것뿐이다.

## 조건 하나에 줄 하나다

열쇠는 매매대상, 실거래, 전략, 보유 상한, 슬리피지, 시작일, 국면 일곱이다.
같은 조건을 다시 재면 그 줄만 갈아 끼운다. 끝일과 잰날은 열쇠가 아니라
값이라, 나중에 다시 재면 새 값으로 덮인다.

**국면이 열쇠에 들어간 이유가 있다.** 한 번 계산하면 전체와 상승·조정·하락
넷이 나온다. 국면을 값으로만 두면 넷이 한 줄을 두고 서로를 덮어쓴다.

**날짜로 지우지 않는다.** 전에는 (잰날, 매매대상, 실거래)로 그날 것을 통째로
지우고 새로 넣었다. 그러면 화면에서 조건 하나만 골라 다시 재는 순간 같은 날
잰 나머지 조건이 전부 사라진다. 화면에서 조건을 바꿔 가며 쌓아 나가는 것이
이 표의 쓰임이므로, 지우는 범위가 딱 그 조건이어야 한다.

**같은 조건의 옛 측정은 안 남는다.** 남기려면 잰날까지 열쇠에 넣어야 하는데,
그러면 날마다 재는 조건이 계속 늘어나 상태 DB가 커진다. 그때그때의 순위는
`strategy_rank`가 날짜별로 따로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from muwon.analysis import window_perf as ㅇ
from muwon.db.models import StrategyRankRow, TradePerfRow, WindowPerfRow


def 쌓기(
    session: Session,
    잰것들: list[ㅇ.잰것],
    *,
    잰날: date,
    매매대상: str,
    실거래: bool = False,
) -> int:
    """측정 결과를 `window_perf`에 넣는다. 같은 조건은 그 줄만 갈아 끼운다.

    **날짜로 지우지 않는다.** 그날 잰 것을 통째로 지우면, 화면에서 조건
    하나만 골라 다시 재는 순간 같은 날 잰 나머지 조건이 전부 사라진다."""
    _국면빈칸채우기(session)
    for ㄱ in 잰것들:
        session.execute(
            delete(WindowPerfRow).where(
                WindowPerfRow.매매대상 == 매매대상,
                WindowPerfRow.실거래 == 실거래,
                WindowPerfRow.전략 == ㄱ.전략,
                WindowPerfRow.상한 == ㄱ.상한,
                WindowPerfRow.슬리피지 == ㄱ.슬리피지,
                WindowPerfRow.시작일 == ㄱ.시작일,
                WindowPerfRow.국면 == ㄱ.국면,
            )
        )
        session.add(_줄로(ㄱ, 잰날=잰날, 매매대상=매매대상, 실거래=실거래))
    session.commit()
    return len(잰것들)


def _국면빈칸채우기(session: Session) -> int:
    """국면 칸이 비어 있는 옛 줄을 "전체"로 채운다.

    **이걸 안 하면 같은 조건이 두 줄이 된다.** 국면을 나누기 전에 쌓은 줄은
    이 칸이 NULL이다. SQL에서 NULL은 "전체"와 같지 않으므로 갈아 끼우는
    조건에 안 걸리고, 새 줄이 그 옆에 하나 더 생긴다. 그러면 표에 같은
    전략이 두 번 뜨고 순위도 두 자리를 차지한다.

    실제로 그랬다. 국면을 붙인 첫 실행 뒤 전체 국면 표에 같은 전략이 두 번
    나왔다.

    한 번 채우고 나면 그다음부터는 아무것도 안 한다."""
    바뀐것 = session.execute(
        update(WindowPerfRow)
        .where(WindowPerfRow.국면.is_(None))
        .values(국면="전체")
    ).rowcount
    if 바뀐것:
        session.commit()
    return 바뀐것 or 0


def _줄로(ㄱ: ㅇ.잰것, *, 잰날: date, 매매대상: str, 실거래: bool) -> WindowPerfRow:
    ㅂ = ㄱ.매매.갈래비율 or {}
    return WindowPerfRow(
        잰날=잰날, 전략=ㄱ.전략, 상한=ㄱ.상한, 슬리피지=ㄱ.슬리피지,
        매매대상=매매대상, 종목수=ㄱ.종목수, 실거래=실거래, 국면=ㄱ.국면,
        시작일=ㄱ.시작일, 끝일=ㄱ.끝일,
        구간수=ㄱ.구간.구간수,
        연환산=ㄱ.구간.연환산, 기하평균=ㄱ.구간.기하평균,
        산술평균=ㄱ.구간.산술평균, 중앙값=ㄱ.구간.중앙값,
        플러스비율=ㄱ.구간.플러스비율, 하위10=ㄱ.구간.하위10, 하위25=ㄱ.구간.하위25,
        최악구간=ㄱ.구간.최악, 최고구간=ㄱ.구간.최고,
        구간흔들림=ㄱ.구간.표준편차, 하락대비수익=ㄱ.구간.하락대비수익,
        구간낙폭중앙값=ㄱ.구간.구간낙폭중앙값,
        매매수=ㄱ.매매.매매수, 승률=ㄱ.매매.승률, 손익비=ㄱ.매매.손익비,
        기대수익=ㄱ.매매.기대수익, 매매중앙값=ㄱ.매매.중앙값,
        평균보유일수=ㄱ.매매.평균보유일수,
        기간만료비율=ㅂ.get("기간만료"), 손절비율=ㅂ.get("손절"),
        익절비율=ㅂ.get("익절"), 매도신호비율=ㅂ.get("매도신호"),
        트레일링비율=ㅂ.get("트레일링"),
        미청산수=ㄱ.매매.미청산수,
        누적수익률=ㄱ.누적수익률, 최대낙폭=ㄱ.최대낙폭,
    )


def 잰것으로(줄: WindowPerfRow) -> ㅇ.잰것:
    """DB 한 줄을 다시 `잰것`으로.

    **판단 기준이 이 모양에서 값을 꺼낸다.** DB에서 읽어 순위를 낼 때와
    파일에서 읽어 순위를 낼 때와 방금 계산해서 순위를 낼 때, 셋이 같은
    자료 모양을 지나가야 세 자리의 순위가 어긋나지 않는다."""
    갈래비율 = {
        이름: 값
        for 이름, 값 in (
            ("손절", 줄.손절비율), ("익절", 줄.익절비율),
            ("트레일링", 줄.트레일링비율), ("매도신호", 줄.매도신호비율),
            ("기간만료", 줄.기간만료비율),
        )
        if 값 is not None
    }
    return ㅇ.잰것(
        전략=줄.전략, 상한=줄.상한, 슬리피지=줄.슬리피지, 매매대상=줄.매매대상,
        시작일=줄.시작일 or date.min, 끝일=줄.끝일 or date.min, 잰날=줄.잰날,
        구간=ㅇ.구간성적(
            길이=줄.상한, 겹침=False, 구간수=줄.구간수,
            기하평균=줄.기하평균, 연환산=줄.연환산, 산술평균=줄.산술평균,
            중앙값=줄.중앙값, 플러스비율=줄.플러스비율,
            하위10=줄.하위10, 하위25=줄.하위25,
            최악=줄.최악구간, 최고=줄.최고구간, 표준편차=줄.구간흔들림,
            하락대비수익=줄.하락대비수익, 구간낙폭중앙값=줄.구간낙폭중앙값,
        ),
        겹친구간=None,
        매매=ㅇ.매매성적(
            매매수=줄.매매수, 승률=줄.승률, 손익비=줄.손익비,
            기대수익=줄.기대수익, 중앙값=줄.매매중앙값,
            평균보유일수=줄.평균보유일수, 갈래비율=갈래비율,
            미청산수=줄.미청산수,
        ),
        누적수익률=줄.누적수익률, 최대낙폭=줄.최대낙폭, 종목수=줄.종목수,
        # 국면을 안 나누던 판에 쌓인 줄은 NULL이다. 그것은 전체로 잰 것이다.
        국면=줄.국면 or "전체",
    )


def 잰날들(session: Session, 매매대상: str = "", 실거래: bool = False) -> list[date]:
    """쌓인 줄들이 언제 잰 것인가. 최근 것부터.

    조건마다 잰 날이 다를 수 있다. 화면이 "이 표는 언제 계산한 것인가"를
    적는 데 쓴다."""
    ㅁ = select(WindowPerfRow.잰날).where(WindowPerfRow.실거래 == 실거래)
    if 매매대상:
        ㅁ = ㅁ.where(WindowPerfRow.매매대상 == 매매대상)
    return sorted({ㄱ for ㄱ in session.scalars(ㅁ.distinct())}, reverse=True)


def 읽기(
    session: Session,
    *,
    매매대상: str = "",
    상한: int | None = None,
    슬리피지: float | None = None,
    시작일: date | None = None,
    국면: str | None = None,
    실거래: bool = False,
) -> list[ㅇ.잰것]:
    """조건에 맞는 줄을 `잰것`으로 읽는다. 비운 조건은 안 거른다.

    **잰 날로 거르지 않는다.** 조건마다 잰 날이 다르다. 화면에서 조건을
    바꿔 가며 하나씩 재기 때문이다. 날짜로 거르면 어제 잰 조건이 오늘
    화면에서 사라진다."""
    ㅁ = select(WindowPerfRow).where(WindowPerfRow.실거래 == 실거래)
    if 매매대상:
        ㅁ = ㅁ.where(WindowPerfRow.매매대상 == 매매대상)
    if 상한 is not None:
        ㅁ = ㅁ.where(WindowPerfRow.상한 == 상한)
    if 슬리피지 is not None:
        ㅁ = ㅁ.where(WindowPerfRow.슬리피지 == 슬리피지)
    if 시작일 is not None:
        ㅁ = ㅁ.where(WindowPerfRow.시작일 == 시작일)
    if 국면 is not None:
        ㅁ = ㅁ.where(WindowPerfRow.국면 == 국면)
    return [잰것으로(ㄱ) for ㄱ in session.scalars(ㅁ)]


def 잰조건들(session: Session, 실거래: bool = False) -> list[tuple]:
    """무엇을 이미 재 두었나. (매매대상, 시작일, 상한, 슬리피지)를 돌려준다.

    **화면이 "이 조건은 아직 계산하지 않았습니다"를 말하려면 이 목록이
    필요하다.** 없는 것을 빈 표로 그리면 "계산했는데 결과가 없다"로 읽힌다."""
    ㅁ = select(
        WindowPerfRow.매매대상, WindowPerfRow.시작일,
        WindowPerfRow.상한, WindowPerfRow.슬리피지,
    ).where(WindowPerfRow.실거래 == 실거래).distinct()
    return sorted(session.execute(ㅁ).all())


# ── 매매 하나하나 ───────────────────────────────────────────────


@dataclass(frozen=True)
class 매매줄:
    """한 매매를 DB에 넣기 좋게 추린 것. 백테스트와 실거래가 같은 모양이다."""

    종목: str
    진입일: date
    청산일: date | None
    보유일수: int
    수익률: float | None
    청산사유: str
    종목명: str = ""
    진입가: float | None = None
    청산가: float | None = None


def 매매쌓기(
    session: Session,
    매매들: list[매매줄],
    *,
    잰날: date,
    전략: str,
    상한: int,
    슬리피지: float,
    매매대상: str,
    실거래: bool = False,
) -> int:
    """`trade_perf`에 매매를 넣는다. 같은 조건의 앞선 줄은 갈아 끼운다.

    청산 사유는 원문을 그대로 두고 다섯 갈래로 묶은 값을 같이 넣는다.
    원문을 버리면 나중에 "ATR 손절과 그냥 손절을 나눠 보고 싶다"에 답할
    수 없고, 갈래를 안 두면 표마다 문자열을 다시 헤아려야 한다."""
    session.execute(
        delete(TradePerfRow).where(
            TradePerfRow.잰날 == 잰날,
            TradePerfRow.전략 == 전략,
            TradePerfRow.상한 == 상한,
            TradePerfRow.슬리피지 == 슬리피지,
            TradePerfRow.매매대상 == 매매대상,
            TradePerfRow.실거래 == 실거래,
        )
    )
    for ㄱ in 매매들:
        session.add(
            TradePerfRow(
                잰날=잰날, 전략=전략, 상한=상한, 슬리피지=슬리피지,
                매매대상=매매대상, 실거래=실거래,
                종목=ㄱ.종목, 종목명=ㄱ.종목명,
                진입일=ㄱ.진입일, 청산일=ㄱ.청산일, 보유일수=ㄱ.보유일수,
                진입가=ㄱ.진입가, 청산가=ㄱ.청산가, 수익률=ㄱ.수익률,
                청산갈래=ㅇ.청산갈래(ㄱ.청산사유), 청산사유원문=ㄱ.청산사유[:100],
            )
        )
    session.commit()
    return len(매매들)


# ── 순위 ────────────────────────────────────────────────────────


def 순위쌓기(
    session: Session,
    잰것들: list[ㅇ.잰것],
    기준,
    *,
    잰날: date,
    상한: int,
    슬리피지: float,
    매매대상: str,
) -> int:
    """그날 낸 순위를 그대로 남긴다.

    **왜 다시 계산할 수 있는 것을 남기나.** 판단 기준을 바꾸면 순위가 통째로
    달라지는데, 그때 "예전에는 무엇이 1위였나"를 계산으로 되살릴 수 없다.
    그 사이 매매 대상 종목이 바뀌기 때문이다.

    `잰것들`은 이미 그 상한과 슬리피지로 걸러 온 것이어야 한다. 여기서 다시
    거르지 않는다. 거르는 자리가 둘이 되면 둘이 어긋난다."""
    from muwon.analysis import window_judgment as ㅈ

    session.execute(
        delete(StrategyRankRow).where(
            StrategyRankRow.잰날 == 잰날,
            StrategyRankRow.상한 == 상한,
            StrategyRankRow.슬리피지 == 슬리피지,
            StrategyRankRow.매매대상 == 매매대상,
        )
    )
    지침들 = list(기준.지침들)
    for 자리, ㄱ in enumerate(ㅈ.줄세우기(잰것들, 기준), start=1):
        값들 = [지침.값(ㄱ) for 지침 in 지침들] + [None, None, None]
        session.add(
            StrategyRankRow(
                잰날=잰날, 상한=상한, 슬리피지=슬리피지, 매매대상=매매대상,
                일순위=기준.일순위, 이순위=기준.이순위, 삼순위=기준.삼순위,
                전략=ㄱ.전략, 자리=자리,
                일순위값=값들[0], 이순위값=값들[1], 삼순위값=값들[2],
                걸린것=", ".join(ㅈ.걸린것(ㄱ))[:200],
            )
        )
    session.commit()
    return len(잰것들)
