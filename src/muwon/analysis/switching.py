"""매일 검토해서 1위로 갈아탔다면 어땠을까.

## 무엇을 재는가

전략 검토(17:50)는 구간마다 순위를 내고 후보를 보여 준다. 그런데 지금까지
**그 후보를 따라갔을 때 실제로 어떻게 됐는지를 재 본 적이 없다.** 그림자
추적(설계안 §39)이 앞으로 그것을 재지만 첫 숫자가 30일 뒤에 나온다. 이
모듈은 같은 질문을 지나간 구간에 대고 묻는다.

    매일 검토해서 그 구간 1위로 갈아탔다면, 지난달 수익률이 얼마였나.

## 두 단계로 나눠서 잰다

    1. 고르기: 날마다 그날까지의 시세로 순위를 내고 1위를 뽑는다
    2. 굴리기: 날마다 전략이 바뀌는 계좌 하나를 처음부터 끝까지 굴린다

**둘을 한 번에 하면 안 된다.** 구간마다 따로 백테스트한 수익률을 이어
붙이면 그럴듯한 곡선이 나오지만 그건 실제로 낼 수 있는 성적이 아니다.
구간이 바뀔 때마다 보유 종목이 사라지고 현금에서 다시 시작하기 때문이다.
실제로는 전략이 바뀌어도 어제 산 종목을 그대로 들고 있다.

## 미래를 보지 않는다

D일 저녁에 내는 순위는 **D일까지의 시세**로만 낸다. 그렇게 고른 전략은
**D+1일부터** 매매한다. 실거래의 시간표와 같다(17:50 검토 → 08:20 반영 →
08:30 후보). 순위를 낼 때 D+1일 시세가 한 칸이라도 들어가면 그날 오를
종목을 미리 보고 고른 것이 되어, 결과가 통째로 뜻이 없어진다.

## 전략이 바뀌면 보유기간도 바뀐다

변동성 돌파는 1일, 거래량 급증 5일은 5일이다. 어제 산 종목을 오늘 다른
전략으로 바꾸면 그 종목은 새 전략의 보유기간을 따른다. 실거래가 그렇게
한다. 청산 판단은 살 때의 전략이 아니라 지금 설정된 전략을 본다.

## 이 숫자를 어디에 쓰나

**아무것도 자동으로 바꾸지 않는다.** 한 달은 표본이 턱없이 모자라고,
좋게 나왔다고 갈아 끼우면 그건 지나간 한 달에 맞추는 것이다. 이 저장소가
이미 여러 번 기각한 방식이다(설계안 §36). 사실만 적는다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from muwon.analysis.period_check import 기간정의, 돌려보기
from muwon.analysis.strategy_fit import 구간순위, 전략줄
from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.backtest.metrics import compute_metrics
from muwon.domain.types import Signal
from muwon.risk.manager import RiskManager
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import (
    MarketContext,
    PortfolioStrategy,
    as_portfolio_strategy,
)


class 갈아타기전략(PortfolioStrategy):
    """날마다 다른 전략으로 신호를 낸다.

    실거래에서 전략을 매일 바꾸면 이 모양이 된다. 계좌와 보유 종목은 그대로
    이어지고 판단하는 규칙만 날마다 갈린다.

    **보유기간을 속성이 아니라 함수로 답한다.** 전략마다 보유기간이 다르고
    (변동성 돌파 1일, 거래량 급증 5일), 오늘 무엇이 걸려 있느냐에 따라 답이
    달라지기 때문이다. 엔진은 하루가 시작될 때마다 이 값을 다시 묻는다."""

    def __init__(self, 날짜별키: dict[date, str], 만들기, 처음키: str):
        self.name = "갈아타기"
        self._날짜별키 = dict(날짜별키)
        self._처음키 = 처음키
        self._만들기 = 만들기
        self._전략들: dict[str, PortfolioStrategy] = {}
        self._오늘키 = 처음키

    def 그날키(self, 날: date) -> str:
        return self._날짜별키.get(날, self._처음키)

    @property
    def max_holding_days(self) -> int | None:
        """엔진이 하루마다 다시 읽는다. 오늘 걸린 전략의 보유기간이다."""
        속 = self._전략들.get(self._오늘키)
        return getattr(속, "max_holding_days", None) if 속 else None

    @property
    def 오늘전략(self) -> PortfolioStrategy | None:
        """지금 걸린 속 전략. 엔진이 **살 때** 이것을 보유 종목에 적어 둔다.

        청산은 산 전략을 따른다(2026-09-02). 이 껍데기를 그대로 적어 두면
        날마다 답이 바뀌어서, 전략을 바꾼 다음 날 옛 종목이 새 규칙으로
        팔린다. 그것이 고치려던 문제다."""
        return self._전략들.get(self._오늘키)

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        """쓰이는 전략을 전부 미리 준비한다.

        날마다 준비하면 같은 신호를 스무 번 넘게 다시 만든다. 여기서 한 번씩만
        만들어 두고 evaluate()는 꺼내 쓰기만 한다."""
        쓸것 = {self._처음키} | set(self._날짜별키.values())
        for 키 in 쓸것:
            속 = as_portfolio_strategy(self._만들기(키))
            속.prepare(histories)
            self._전략들[키] = 속

    def evaluate(self, ctx: MarketContext) -> list[Signal]:
        if not self._전략들:
            self.prepare(ctx.histories)
        self._오늘키 = self.그날키(ctx.as_of)
        return self._전략들[self._오늘키].evaluate(ctx)


@dataclass(frozen=True)
class 하루선택:
    """어느 날 저녁의 검토 결과 한 줄."""

    잰날: date
    적용날: date
    고른키: str
    앞선키: str
    #: 순위 위쪽 셋. (키, 수익률, 거래수)
    위쪽: list[tuple[str, float, int]] = field(default_factory=list)
    #: 거래가 한 건도 없어 순위에서 뺀 전략 수. 이것이 크면 그날의 1위는
    #: 몇 안 되는 전략 중의 1위다.
    거래없음수: int = 0
    #: 그날 순위 전체. (키, 수익률, 거래수, 최대낙폭)를 등수 차례로 담는다.
    #:
    #: 순위를 내는 데 드는 시간이 이 계산의 거의 전부다(전략 27개 × 하루에
    #: 1분). 위쪽 셋만 남기면 "실제 규칙대로였다면" 같은 다른 물음에 답할
    #: 때마다 같은 계산을 처음부터 다시 해야 한다. 한 번 재고 여러 번 쓴다.
    전체: list[tuple[str, float, int, float]] = field(default_factory=list)

    @property
    def 바꿨나(self) -> bool:
        return self.고른키 != self.앞선키


def 날마다고르기(
    정의: 기간정의,
    histories: dict[str, pd.DataFrame],
    잴날들: list[date],
    적용날들: list[date],
    정책: RiskPolicy,
    전략키들: list[str],
    만들기,
    처음키: str,
    costs: TransactionCosts | None = None,
    알림=None,
    섹터표: dict[str, str] | None = None,
    섹터상한: int = 0,
    섹터상한셈: str = "하루후보",
    점수순: bool = False,
    결제일수: int = 0,
    예수금: float = 10_000_000.0,
) -> list[하루선택]:
    """날마다 순위를 내고 1위를 고른다. 미래를 보지 않는다.

    `잴날들[i]`까지의 시세로 순위를 내고, 그 답을 `적용날들[i]`부터 쓴다.
    두 목록의 길이는 같아야 한다.

    **거래가 0건인 전략은 1위가 될 수 없다.** 수익률 0%로 맨 위에 오는데
    그건 지킨 것이 아니라 아무것도 안 한 것이다. `구간순위.산것`과 같은
    규칙이다."""
    if len(잴날들) != len(적용날들):
        raise ValueError("잴 날과 적용할 날의 개수가 다릅니다.")

    나온것: list[하루선택] = []
    앞선키 = 처음키
    for 잰날, 적용날 in zip(잴날들, 적용날들, strict=True):
        줄들: list[전략줄] = []
        for 키 in 전략키들:
            try:
                # 순위를 내는 조건은 실제로 굴리는 조건과 같아야 한다.
                성적 = 돌려보기(
                    정의, (lambda k=키: 만들기(k)), histories, 잰날, 정책, costs=costs,
                    섹터표=섹터표, 섹터상한=섹터상한, 섹터상한셈=섹터상한셈,
                    점수순=점수순, 결제일수=결제일수, 예수금=예수금,
                )
            except Exception as 탈:  # noqa: BLE001 (하나가 터져도 나머지 순위는 봐야 한다)
                # 조용히 넘기면 그날 순위가 몇 개짜리였는지 알 수 없다.
                print(f"  {잰날} {키} 못 돌림 ({type(탈).__name__}: {탈})", file=sys.stderr)
                continue
            if 성적 is not None:
                줄들.append(전략줄(키=키, 이름=키, 성적=성적))

        순위 = 구간순위(구간=정의.이름, 줄들=줄들, 지금키=앞선키)
        차례 = 순위.차례
        고른키 = 차례[0].키 if 차례 else 앞선키
        나온것.append(
            하루선택(
                잰날=잰날,
                적용날=적용날,
                고른키=고른키,
                앞선키=앞선키,
                위쪽=[(ㄱ.키, ㄱ.수익률, ㄱ.거래수) for ㄱ in 차례[:3]],
                거래없음수=len(줄들) - len(순위.산것),
                전체=[
                    (ㄱ.키, ㄱ.수익률, ㄱ.거래수, ㄱ.성적.metrics.max_drawdown_pct)
                    for ㄱ in 차례
                ],
            )
        )
        if 알림 is not None:
            알림(나온것[-1])
        앞선키 = 고른키
    return 나온것


@dataclass(frozen=True)
class 갈아타기규칙:
    """언제 갈아탈 것인가. 순위는 그대로 두고 규칙만 바꿔서 견준다.

    순위를 내는 데 드는 시간이 이 계산의 거의 전부라, 한 번 낸 순위에
    여러 규칙을 얹어 본다. 그래야 "매일 1위를 따라간 것"과 "실제 검토가
    내는 후보를 따라간 것"이 얼마나 다른지 같은 자료 위에서 비교된다."""

    이름: str
    설명: str
    #: 1위가 지금 것보다 이 배수만큼 앞서야 바꾼다. 1.0이면 조금만 앞서도
    #: 바꾼다. 실제 검토는 1.15다(`strategy_fit.기본우위배수`).
    우위배수: float = 1.0
    #: 바꾼 뒤 이만큼 검토일이 지나야 다시 바꾼다. 실제 검토는 30이다.
    최소운용일: int = 0
    #: 후보의 거래가 이보다 적으면 바꾸지 않는다. 실제 검토는 후보를 내되
    #: 등급을 낮춘다. 표본을 아예 요구했을 때 어떻게 달라지는지 보는 값이다.
    최소거래수: int = 0
    #: 지금 걸린 전략이 그 구간에 매수를 안 했을 때도 바꿀 것인가.
    #:
    #: 실제 검토는 이때 후보를 안 낸다. 비교할 짝이 없기 때문이다
    #: (`후보내기`의 "현재 설정된 전략이 이 구간에서 매수를 하지 않아
    #: 비교할 수 없습니다"). 그래서 기본값은 False다.
    #:
    #: **"무조건 1위를 따라간다"는 이 막음이 있으면 안 된다.** 거래를 안 한
    #: 전략에 한 번 걸리면 거기서 멈춰 버려서, 이름과 달리 1위를 안 따라가는
    #: 규칙이 된다. 2026-09-01에 그렇게 잰 숫자를 한 번 보고했다.
    지금없어도바꾼다: bool = False


def 규칙적용(선택들: list[하루선택], 처음키: str, 규칙: 갈아타기규칙) -> dict[date, str]:
    """이미 낸 순위에 규칙을 얹어 날마다 무엇을 걸었을지 정한다.

    막는 조건은 `strategy_fit.후보내기`와 같은 순서로 본다. 1위가 지금
    것이면 그대로, 지금 것이 그 구간에 매수를 안 했으면 비교가 안 되므로
    그대로(`지금없어도바꾼다`가 참이면 이건 건너뛴다), 우위가 모자라면
    그대로다."""
    from muwon.analysis.strategy_fit import _앞서나

    표: dict[date, str] = {}
    키 = 처음키
    지난 = 규칙.최소운용일  # 처음 한 번은 바로 바꿀 수 있게 둔다
    for ㅅ in 선택들:
        최고 = ㅅ.전체[0] if ㅅ.전체 else None
        지금 = next((ㄹ for ㄹ in ㅅ.전체 if ㄹ[0] == 키), None)
        견줄수있나 = 지금 is not None and _앞서나(최고[1], 지금[1], 규칙.우위배수) \
            if 최고 is not None else False
        바꿀까 = (
            최고 is not None
            and 최고[0] != 키
            and 지난 >= 규칙.최소운용일
            and 최고[2] >= 규칙.최소거래수
            and (견줄수있나 or (규칙.지금없어도바꾼다 and 지금 is None))
        )
        if 바꿀까:
            키 = 최고[0]
            지난 = 0
        else:
            지난 += 1
        표[ㅅ.적용날] = 키
    return 표


def 굴리기(
    histories: dict[str, pd.DataFrame],
    전략: PortfolioStrategy,
    시작: date,
    끝: date,
    정책: RiskPolicy,
    costs: TransactionCosts | None = None,
    섹터표: dict[str, str] | None = None,
    섹터상한: int = 0,
    섹터상한셈: str = "하루후보",
    점수순: bool = False,
    결제일수: int = 0,
    예수금: float = 10_000_000.0,
):
    """계좌 하나를 시작부터 끝까지 굴린다.

    **구간을 잘라 이어 붙이지 않는다.** 잘라 붙이면 구간이 바뀔 때마다 보유
    종목이 사라지고 현금에서 다시 시작해서, 실제로는 낼 수 없는 성적이 나온다.

    섹터 상한과 점수순은 실거래가 하는 일인데 백테스트가 안 하던 것이다.
    켜면 지금까지 낸 전략 평가 결과와 비교가 안 되므로 결과에 적어야 한다."""
    from muwon.analysis.period_check import slice_for_range

    잘린것 = slice_for_range(histories, 시작, 끝)
    if not 잘린것:
        return None
    결과 = BacktestEngine(
        strategy=전략,
        risk_manager=RiskManager(policy_provider=lambda p=정책: p),
        costs=costs,
        entry_at_open=True,
        exit_at_open=True,
        섹터표=섹터표,
        섹터상한=섹터상한,
        섹터상한셈=섹터상한셈,
        점수순=점수순,
        결제일수=결제일수,
        initial_cash=예수금,
    ).run(잘린것, trade_from=시작)
    return 결과, compute_metrics(결과)
