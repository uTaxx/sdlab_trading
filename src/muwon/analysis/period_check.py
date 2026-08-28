"""지금 걸린 전략을 최근 3개월·12개월·5년에 돌려 본다.

## 성적표와 무엇이 다른가

`docs/전략평가.json`의 성적표는 **2021~2025년을 해마다** 돌린 숫자다.
전략을 고를 때 쓰는 자료라 그렇게 잰다. 그런데 화면을 여는 사람이 묻는
것은 다른 질문이다.

    지금 걸어 둔 이 전략, 최근에는 어땠나.

이 두 질문의 답은 다르다. 성적표는 다섯 해를 각각 재고, 여기서는
**한 구간을 통째로 이어서** 잰다. 3개월을 잰다는 것은 그 3개월을 처음부터
끝까지 굴렸다는 뜻이지, 3개월씩 여러 번 잰 평균이 아니다.

## 짧은 구간의 숫자는 거의 못 믿는다

3개월이면 거래가 스무 건 안팎이다(2026-08-28에 실제로 21건). 그중 크게
움직인 한두 종목이 숫자의 대부분을 만든다. 그것이 실력인지 운인지 구별할
방법이 없다. 그래서 이 모듈은 **거래 수를 언제나 같이 돌려준다.**
수익률만 보고 판단하면 안 되는 자리다.

거래 수 문턱(20건)은 **정말 표본이 없는 경우를 걸러 내는 값이지, 넘었다고
믿어도 된다는 값이 아니다.** 3개월은 그 문턱을 겨우 넘는 일이 잦다.

## 평균이 아니라 제일 나빴던 구간을 같이 낸다

이 저장소의 1순위 판단 기준이다. 5년을 통째로 재면 +80%인데 그 안의 한
해가 -30%였을 수 있다. 그 해에 대부분 그만두고, 그러면 +80%는 받아 보지도
못한다. 그래서 구간마다 안을 쪼개서 제일 나빴던 토막을 같이 낸다.
3개월과 12개월은 달로, 5년은 해로 쪼갠다.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from muwon.analysis.experiment import WARMUP_DAYS
from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.backtest.metrics import BacktestMetrics, compute_metrics
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy


@dataclass(frozen=True)
class 기간정의:
    """화면에서 고를 수 있는 구간 하나."""

    이름: str
    달수: int
    #: 안을 무엇으로 쪼개 제일 나빴던 토막을 찾을 것인가.
    쪼갬: str  # "달" | "해"
    설명: str


#: **여기가 한 군데다.** 화면의 목록, 워크플로 입력, 스크립트 인자가 전부
#: 이 표를 읽는다. 세 곳에 따로 적으면 하나만 고치고 둘을 잊는다.
기간들: tuple[기간정의, ...] = (
    기간정의(
        "3개월", 3, "달",
        "한 분기 한 번뿐입니다. 거래는 스무 건 안팎이고, 크게 움직인 한두 종목이 "
        "숫자의 대부분을 만듭니다. 방금 바꾼 것이 돌고는 있는지 보는 용도입니다",
    ),
    기간정의(
        "12개월", 12, "달",
        "한 해치입니다. 상승장 한 번과 조정 한 번쯤은 들어갑니다",
    ),
    기간정의(
        "5년", 60, "해",
        "성적표와 같은 길이입니다. 판단은 이 숫자로 합니다",
    ),
)

기간표: dict[str, 기간정의] = {ㄱ.이름: ㄱ for ㄱ in 기간들}


def 달빼기(끝: date, 달수: int) -> date:
    """달 단위로 거슬러 올라간다. 31일에서 한 달 빼면 그 달의 마지막 날."""
    해 = 끝.year
    달 = 끝.month - 달수
    while 달 <= 0:
        달 += 12
        해 -= 1
    return date(해, 달, min(끝.day, calendar.monthrange(해, 달)[1]))


def 구간(정의: 기간정의, 끝: date) -> tuple[date, date]:
    return 달빼기(끝, 정의.달수), 끝


def slice_for_range(
    histories: dict[str, pd.DataFrame], 시작: date, 끝: date
) -> dict[str, pd.DataFrame]:
    """매매할 구간 앞에 예열을 붙여 잘라 준다.

    예열이 모자라면 200일 이동평균 같은 것이 안 채워진 채로 돈다. 그러면
    "전략이 나빴다"인지 "전략이 켜지지도 않았다"인지 구별이 안 된다."""
    처음 = 시작 - timedelta(days=WARMUP_DAYS)
    잘린것 = {}
    for symbol, df in histories.items():
        창 = df[(df["trade_date"] >= 처음) & (df["trade_date"] <= 끝)]
        if len(창):
            잘린것[symbol] = 창
    return 잘린것


def 토막수익률(equity_curve: pd.DataFrame, 쪼갬: str) -> list[tuple[str, float]]:
    """평가금액 곡선을 달(또는 해)로 쪼개 토막마다의 수익률을 낸다.

    토막의 시작값은 **앞 토막의 끝값**이다. 토막 안의 첫 값을 쓰면 토막이
    바뀌는 사이에 난 움직임이 어느 쪽에도 안 잡힌다."""
    if equity_curve is None or len(equity_curve) < 2:
        return []
    if "equity" not in equity_curve or "trade_date" not in equity_curve:
        return []

    def 이름(ㄴ) -> str:
        해, 달 = ㄴ.year, ㄴ.month
        return f"{해}년" if 쪼갬 == "해" else f"{해}-{달:02d}"

    키 = [이름(ㄴ) for ㄴ in equity_curve["trade_date"]]
    나온것: list[tuple[str, float]] = []
    앞끝: float | None = None
    for 이름값, 묶음 in equity_curve.groupby(키, sort=True):
        시작값 = 앞끝 if 앞끝 is not None else float(묶음["equity"].iloc[0])
        끝값 = float(묶음["equity"].iloc[-1])
        if 시작값 > 0:
            나온것.append((str(이름값), (끝값 / 시작값 - 1) * 100))
        앞끝 = 끝값
    return 나온것


@dataclass(frozen=True)
class 기간성적:
    """한 구간을 통째로 돌린 결과."""

    이름: str
    시작: date
    끝: date
    metrics: BacktestMetrics
    #: 안을 쪼갠 토막들. 제일 나빴던 것을 찾는 데 쓴다.
    토막들: list[tuple[str, float]] = field(default_factory=list)
    #: 시세가 모자라 실제로는 이만큼밖에 못 돌린 경우 그 사실을 적는다.
    모자람: str = ""

    @property
    def 수익률(self) -> float:
        return self.metrics.total_return_pct

    @property
    def 최악토막(self) -> tuple[str, float] | None:
        """제일 나빴던 토막. **평균보다 이걸 먼저 본다.**"""
        return min(self.토막들, key=lambda ㅌ: ㅌ[1]) if self.토막들 else None

    @property
    def 믿을만한가(self) -> bool:
        """거래가 스무 건은 넘어야 숫자를 놓고 이야기할 수 있다.

        그 아래면 크게 오른 한 종목이 결과의 대부분을 만든다. 그것이 실력인지
        운인지 구별할 방법이 없다."""
        return self.metrics.num_trades >= 20


def 돌려보기(
    정의: 기간정의,
    전략만들기,
    histories: dict[str, pd.DataFrame],
    끝: date,
    policy: RiskPolicy,
    costs: TransactionCosts | None = None,
    entry_at_open: bool = True,
    exit_at_open: bool = True,
) -> 기간성적 | None:
    """한 구간을 통째로 돌린다. 시세가 없으면 None.

    **체결은 다음 날 시가가 기본이다.** 실거래 엔진이 실제로 그렇게 한다.
    종가 체결로 재면 그날 종가를 보고 그날 종가에 산 것이 되어, 실제로는
    낼 수 없는 성적이 나온다."""
    시작, 마지막 = 구간(정의, 끝)
    잘린것 = slice_for_range(histories, 시작, 마지막)
    if not 잘린것:
        return None

    있는날 = [df["trade_date"].max() for df in 잘린것.values()]
    처음날 = min(df["trade_date"].min() for df in 잘린것.values())
    모자람 = ""
    if 처음날 > 시작:
        모자람 = f"시세가 {처음날}부터라 그 앞은 못 돌렸습니다"

    결과 = BacktestEngine(
        strategy=전략만들기(),
        risk_manager=RiskManager(policy_provider=lambda p=policy: p),
        costs=costs,
        exit_at_open=exit_at_open,
        entry_at_open=entry_at_open,
    ).run(잘린것, trade_from=시작)

    return 기간성적(
        이름=정의.이름,
        시작=시작,
        끝=max(있는날) if 있는날 else 마지막,
        metrics=compute_metrics(결과),
        토막들=토막수익률(결과.equity_curve, 정의.쪼갬),
        모자람=모자람,
    )


def 검증용정책(정책: RiskPolicy) -> RiskPolicy:
    """스위치 둘을 켠 채로 돌린다. **이건 무시가 아니라 질문이 다른 것이다.**

    킬스위치는 "오늘 실제로 주문을 낼 것인가"를 정하는 값이다. 여기서 묻는
    것은 "이 전략을 돌렸으면 어땠을까"라 그 값과 상관이 없다.

    끄고 돌리면 한 주도 안 사서 수익률이 0.0%으로 나오는데, 화면에는 그것이
    "이 전략은 아무것도 못 번다"로 보인다. 조용히 틀린 답이라 막아야 한다.

    나머지 값(손절·익절·보유기간·비중·동시보유·하루손실)은 그대로 쓴다.
    지금 걸어 둔 기준으로 돌렸을 때의 숫자를 보는 것이 이 기능의 목적이다."""
    from dataclasses import replace

    return replace(정책, trading_enabled=True, sell_enabled=True)


def 기준글(정책: RiskPolicy, 유니버스수: int, 유니버스종류: str) -> str:
    """이 숫자가 어느 조건에서 나온 것인지. **조건 없는 숫자는 검증이 안 된다.**"""
    익절 = f"{정책.take_profit_pct * 100:.0f}%" if 정책.take_profit_pct else "끔"
    보유 = f"{정책.max_holding_days}일" if 정책.max_holding_days else "전략이 정한 대로"
    return (
        f"손절 {정책.stop_loss_pct * 100:.0f}% · 익절 {익절} · 보유 {보유} · "
        f"비중 {정책.max_position_weight * 100:.0f}% · "
        f"동시보유 {정책.max_concurrent_positions}종목 · "
        f"유니버스 {유니버스종류} {유니버스수}종목 · 다음 날 시가 체결 · 슬리피지 0"
    )
