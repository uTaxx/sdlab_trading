"""전략 가설을 등록하고 이름으로 찾아 쓰는 곳.

"가설을 검증하고 진화시킨다"는 걸 코드 흐름으로 풀면:
  1. 여기에 새 StrategyDefinition을 하나 추가한다(파라미터만 다르거나,
     아예 다른 Strategy 구현체일 수도 있다. 둘 다 지원한다)
  2. scripts/run_hypothesis_sweep.py로 과거 데이터에 대해 백테스트하고
     결과를 DB(backtest_runs 테이블)에 남긴다
  3. 결과가 괜찮으면 대시보드나 configure.py로 "지금 실거래에 쓸 키"만
     바꾼다. 코드 배포 없이 설정값 하나로 전환되고, 이 변경 자체가
     대시보드 "변경 이력"에 자동으로 남는다
  4. 실거래(TradingEngine/RealtimeTradingEngine)가 만든 매매 기록에도
     strategy_key가 찍혀서(trades 테이블), 나중에 "이 가설이 실전에서
     어떻게 됐는지"를 가설별로 나눠 볼 수 있다

category는 전략의 성격(추세추종/평균회귀/돌파/복합)을 나타낸다. 계열이
다르면 잘 맞는 시장 국면도 다르므로, 대시보드에서 계열별로 묶어 보면
"지금 장에는 어떤 계열이 통하는가"를 읽을 수 있다.

status는 순수 메타데이터(코드가 강제하지 않음): 사람이나 미래의 AI 제언이
"이건 아직 실험 중", "이건 검증 끝났다" 같은 걸 구분해 적어두는 용도다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from muwon.domain.interfaces import Strategy
from muwon.scoring.engine import FactorScoreStrategy, load_strategy_config
from muwon.strategy.breakout import (
    BollingerBreakoutParams,
    BollingerBreakoutStrategy,
    PriceChannelBreakoutStrategy,
    PriceChannelParams,
    VolumeSurgeParams,
    VolumeSurgeStrategy,
)
from muwon.strategy.gap import (
    GapParams,
    GapStrategy,
    VolatilityBreakoutParams,
    VolatilityBreakoutStrategy,
)
from muwon.strategy.reversion import (
    BollingerReversionParams,
    BollingerReversionStrategy,
    RsiReversionParams,
    RsiReversionStrategy,
    StochasticParams,
    StochasticStrategy,
)
from muwon.strategy.rule_based import MovingAverageRsiParams, MovingAverageRsiStrategy
from muwon.strategy.trend import (
    DonchianBreakoutParams,
    DonchianBreakoutStrategy,
    EmaCrossParams,
    EmaCrossStrategy,
    GoldenCrossParams,
    GoldenCrossStrategy,
    MacdCrossParams,
    MacdCrossStrategy,
)
from muwon.strategy.us_sector import USSectorFollowStrategy, USSectorGateStrategy

CATEGORY_TREND = "추세추종"
CATEGORY_REVERSION = "평균회귀"
CATEGORY_BREAKOUT = "돌파·모멘텀"
CATEGORY_HYBRID = "복합"

CATEGORY_SCORE = "점수합산"
CATEGORIES = [
    CATEGORY_TREND,
    CATEGORY_REVERSION,
    CATEGORY_BREAKOUT,
    CATEGORY_HYBRID,
    CATEGORY_SCORE,
]


@dataclass(frozen=True)
class StrategyDefinition:
    key: str  # 고유 식별자: trades/backtest_runs 테이블에 그대로 저장됨
    display_name: str
    description: str
    #: PortfolioStrategy를 돌려줘도 된다. 엔진이 알아서 통일한다
    factory: Callable[[], Strategy]
    category: str = CATEGORY_HYBRID
    status: str = "hypothesis"  # "hypothesis" | "backtested" | "live" | "retired"
    #: 화면에 쓰는 짧은 한글 이름. display_name은 파라미터까지 달고 있어서
    #: 폰 폭에서 두 줄로 넘치고, 목록에 늘어놓으면 서로 구별이 안 된다.
    #: 비워 두면 display_name을 쓴다. 새 전략을 등록할 때 안 적어도
    #: 화면이 비지는 않는다.
    짧은이름: str = ""
    #: 주식을 처음 하는 사람이 읽을 설명. **전문용어를 쓰지 않는다.**
    #:
    #: `description`은 이미 있지만 "단기선 상향돌파+거래량급증 또는 RSI
    #: 과매도반등 매수"처럼 적혀 있어서, 처음 보는 사람은 한 단어도 못
    #: 읽는다. 뜻을 모르는 줄은 안 읽는 줄이고, 안 읽는 줄은 없는 줄이다.
    #:
    #: **숫자를 적지 않는다.** 20일이냐 60일이냐는 파라미터에서 자동으로
    #: 만들어지는 `strategy_rules.describe()`가 정확히 적는다. 여기는
    #: 그 전략이 무엇을 노리는 생각인지만 적는 자리라, 파라미터를 바꿔도
    #: 이 글은 그대로 맞아야 한다.
    쉬운설명: str = ""
    #: 그래서 이걸 볼 때 무엇을 알고 있어야 하나. 조심할 점, 어떤 장에서
    #: 잘 맞는지. 없으면 빈 값으로 둔다. 억지로 채우면 안 읽힌다.
    쉬운참고: str = ""
    #: 이 전략으로 산 종목의 익절선. 0이면 안 정한 것이고, 그때는 기초설정을
    #: 따른다(기초설정도 0이면 익절을 안 건다).
    #:
    #: ## 왜 전략마다 두나 (2026-09-02)
    #:
    #: 익절은 오르는 구간에서 수익을 깎고 빠지는 구간에서 손실을 막는다.
    #: 세 국면에 +10%를 걸어 재 보니 상승장에서는 다섯 규칙 중 넷이 나빠졌고
    #: 하락장에서는 전부 좋아졌다. 전략마다 노리는 것이 다르므로 한 값을
    #: 전부에 들이대는 것이 맞지 않는다.
    #:
    #: **보유기간과 같은 자리다.** 보유기간은 전략이 정하고 기초설정이 덮는
    #: 구조가 이미 있었는데 익절만 기초설정 하나뿐이었다.
    익절: float = 0.0

    @property
    def 화면이름(self) -> str:
        """화면에 내보낼 이름. 짧은 것이 있으면 그것을 쓴다."""
        return self.짧은이름 or self.display_name


REGISTRY: list[StrategyDefinition] = [
    # ── 복합: 이동평균 추세 + RSI 반등을 한 전략에 섞은 것 (이 프로젝트의 출발점) ──
    StrategyDefinition(
        key="ma_rsi_v1",
        짧은이름="이평+RSI 20/60",
        display_name="이동평균+RSI 복합 (20/60/14)",
        description="단기선 상향돌파+거래량급증 또는 RSI 과매도반등 매수, 단기선 하향이탈/RSI 과매수 매도.",
        factory=lambda: MovingAverageRsiStrategy(MovingAverageRsiParams(), name="ma_rsi_v1"),
        category=CATEGORY_HYBRID,
        status="live",
        쉬운설명=(
            "두 가지 중 하나만 맞으면 삽니다. 주가가 최근 평균을 넘어서면서 거래량이 "
            "평소보다 크게 늘었을 때가 하나이고, 많이 빠졌다가 방향을 돌렸는데 더 긴 "
            "기간으로 보면 아직 평균 위일 때가 다른 하나입니다."
        ),
        쉬운참고=(
            "오르기 시작한 것과 눌렸다 돌아선 것을 둘 다 잡으려는 방법입니다. 조건이 둘이라 "
            "한쪽만 보는 전략보다 사는 횟수가 많습니다."
        ),
    ),
    StrategyDefinition(
        key="ma_rsi_fast5_20",
        짧은이름="이평+RSI 5/20",
        display_name="이동평균+RSI 단타형 (5/20/7)",
        description="같은 규칙, 창을 5/20/7로 좁혀 더 짧은 주기에 반응. 신호가 잦아지는 대신 헛신호도 늘어난다.",
        factory=lambda: MovingAverageRsiStrategy(
            MovingAverageRsiParams(sma_short=5, sma_long=20, rsi_period=7, volume_ma_window=5),
            name="ma_rsi_fast5_20",
        ),
        category=CATEGORY_HYBRID,
        쉬운설명=(
            "이평+RSI 20/60과 규칙은 같고 보는 기간만 짧습니다."
        ),
        쉬운참고=(
            "짧게 보면 신호가 자주 나오는 대신 잘못된 신호도 같이 늘어납니다."
        ),
    ),
    StrategyDefinition(
        key="ma_rsi_loose_volume",
        짧은이름="이평+RSI 완화",
        display_name="이동평균+RSI 거래량완화 (1.2배)",
        description="거래량 급증 기준을 1.5배→1.2배로 낮춰 진입 빈도/승률 트레이드오프 확인.",
        factory=lambda: MovingAverageRsiStrategy(
            MovingAverageRsiParams(volume_surge_ratio=1.2), name="ma_rsi_loose_volume"
        ),
        category=CATEGORY_HYBRID,
        쉬운설명=(
            "이평+RSI 20/60과 규칙은 같고, 거래량이 얼마나 늘어야 하는지를 덜 "
            "까다롭게 봅니다."
        ),
        쉬운참고=(
            "기준을 낮추면 더 자주 사게 되는데 맞을 확률은 떨어질 수 있습니다. 그 맞바꿈이 "
            "어떻게 되는지 보려고 둔 것입니다."
        ),
    ),
    # ── 추세추종 ──
    StrategyDefinition(
        key="golden_cross_20_60",
        짧은이름="골든크로스 20/60",
        display_name="골든크로스 (20/60)",
        description="단기 이동평균선이 장기선을 상향돌파하면 매수, 하향이탈하면 매도. 가장 고전적인 추세추종.",
        factory=lambda: GoldenCrossStrategy(GoldenCrossParams(20, 60), name="golden_cross_20_60"),
        category=CATEGORY_TREND,
        쉬운설명=(
            "짧은 기간의 평균 가격이 긴 기간의 평균 가격을 넘어서면 삽니다. 주가가 오르는 "
            "흐름으로 접어들었다고 보는 것이고, 반대로 내려오면 팝니다."
        ),
        쉬운참고=(
            "가장 오래된 방법입니다. 한 방향으로 길게 갈 때 잘 맞고, 오르내림만 반복하는 "
            "구간에서는 샀다 팔았다를 되풀이합니다."
        ),
    ),
    StrategyDefinition(
        key="golden_cross_5_20",
        짧은이름="골든크로스 5/20",
        display_name="골든크로스 단타형 (5/20)",
        description="같은 규칙을 5/20으로 좁힌 단기 버전. 신호가 훨씬 잦다.",
        factory=lambda: GoldenCrossStrategy(GoldenCrossParams(5, 20), name="golden_cross_5_20"),
        category=CATEGORY_TREND,
        쉬운설명=(
            "골든크로스 20/60과 같은데 보는 기간이 훨씬 짧습니다."
        ),
        쉬운참고=(
            "신호가 자주 나옵니다. 변동성이 큰 구간에서는 사고파는 횟수가 크게 늡니다."
        ),
    ),
    StrategyDefinition(
        key="ema_cross_12_26",
        짧은이름="EMA 교차",
        display_name="EMA 교차 (12/26)",
        description="지수이동평균 교차: 최근 가격에 가중치를 줘 단순이동평균보다 빠르게 반응.",
        factory=lambda: EmaCrossStrategy(EmaCrossParams(12, 26), name="ema_cross_12_26"),
        category=CATEGORY_TREND,
        쉬운설명=(
            "평균을 낼 때 최근 가격에 비중을 더 둡니다. 그래서 방향이 바뀌는 것을 조금 더 "
            "빨리 알아챕니다."
        ),
        쉬운참고=(
            "빨리 반응하는 만큼 잠깐 움직인 것에도 반응합니다."
        ),
    ),
    StrategyDefinition(
        key="macd_cross",
        짧은이름="MACD 교차",
        display_name="MACD 신호선 교차 (12/26/9)",
        description="MACD선이 신호선을 상향돌파하면 매수, 하향이탈하면 매도. 가장 널리 쓰이는 추세 지표.",
        factory=lambda: MacdCrossStrategy(MacdCrossParams(), name="macd_cross"),
        category=CATEGORY_TREND,
        쉬운설명=(
            "짧은 기간 평균과 긴 기간 평균의 차이를 봅니다. 그 차이가 벌어지기 시작하면 "
            "사고, 좁아지기 시작하면 팝니다."
        ),
        쉬운참고=(
            "주가가 오르는 쪽으로 힘이 붙는 순간을 잡으려는 방법입니다."
        ),
    ),
    StrategyDefinition(
        key="macd_cross_positive",
        짧은이름="MACD 교차 (0선 위)",
        display_name="MACD 교차 + 0선 위 필터",
        description="MACD가 0보다 클 때(이미 상승 국면)의 교차만 매수: 하락장 중 반짝 반등을 걸러낸다.",
        factory=lambda: MacdCrossStrategy(
            MacdCrossParams(require_positive_macd=True), name="macd_cross_positive"
        ),
        category=CATEGORY_TREND,
        쉬운설명=(
            "MACD 교차와 같은 신호 중에서, 이미 오름세일 때 나온 것만 삽니다."
        ),
        쉬운참고=(
            "주가가 계속 하락하는 중에 잠깐 반등한 것을 걸러 내려는 것입니다."
        ),
    ),
    StrategyDefinition(
        key="donchian_20_10",
        짧은이름="돈치안 20/10",
        display_name="돈치안 돌파 (20일 진입/10일 청산)",
        description="20일 신고가 돌파 매수, 10일 신저가 이탈 매도. 이른바 터틀 트레이딩 규칙.",
        factory=lambda: DonchianBreakoutStrategy(
            DonchianBreakoutParams(20, 10), name="donchian_20_10"
        ),
        category=CATEGORY_TREND,
        쉬운설명=(
            "최근 며칠 사이 가장 높았던 주가를 넘어서면 삽니다. 새 고점을 찍었다는 것은 "
            "흐름이 위로 잡혔다는 뜻으로 봅니다. 가장 낮았던 가격 아래로 내려가면 팝니다."
        ),
        쉬운참고=(
            "오래 검증된 방법입니다. 넘어섰다가 바로 되돌아오는 구간에서는 손실이 쌓입니다."
        ),
    ),
    StrategyDefinition(
        key="donchian_adx_filter",
        짧은이름="돈치안 + 추세강도",
        display_name="돈치안 돌파 + 추세강도(ADX) 필터",
        description="ADX 25 이상(추세장)일 때만 돌파 매수: 횡보장의 가짜 돌파를 걸러낸다.",
        factory=lambda: DonchianBreakoutStrategy(
            DonchianBreakoutParams(20, 10, adx_filter=25), name="donchian_adx_filter"
        ),
        category=CATEGORY_TREND,
        쉬운설명=(
            "돈치안 20/10과 같은데, 주가가 한 방향으로 꾸준히 움직일 때만 삽니다."
        ),
        쉬운참고=(
            "오르내림만 반복하는 구간에서 잠깐 고점을 넘는 것을 걸러 내려는 것입니다."
        ),
    ),
    # ── 평균회귀 ──
    StrategyDefinition(
        key="rsi_reversion",
        짧은이름="RSI 반등 30/70",
        display_name="RSI 평균회귀 교과서형 (30/70 + 장기선 필터)",
        description=(
            "RSI 30 반등 매수 + 60일선 위에서만 진입. "
            "⚠️ 2023~2024 백테스트에서 거래 0건: RSI가 30까지 빠질 만큼 하락하면 "
            "이미 60일선 아래인 경우가 대부분이라 두 조건이 사실상 공존하지 않는다. "
            "교과서 조합이 실전에서 왜 안 되는지를 보여주는 대조군으로 남겨 둔다."
        ),
        factory=lambda: RsiReversionStrategy(RsiReversionParams(), name="rsi_reversion"),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "많이 빠졌다가 방향을 돌린 종목을 삽니다. 다만 더 긴 기간으로 봤을 때 아직 "
            "평균 위인 것만 삽니다."
        ),
        쉬운참고=(
            "이 두 조건은 실제로 거의 같이 성립하지 않습니다. 그만큼 빠지면 긴 기간 평균도 "
            "이미 무너져 있기 때문입니다. 과거 시세로 계산했을 때 매수가 한 건도 "
            "없었습니다. 책에 나오는 조합이 실제로는 왜 안 되는지 보여 주려고 남겨 둔 "
            "것이므로, 매매에 쓰려고 만든 것이 아닙니다."
        ),
    ),
    StrategyDefinition(
        key="rsi2_pullback",
        짧은이름="RSI(2) 눌림목",
        display_name="RSI(2) 눌림목 매수 (10/70 + 장기선 필터)",
        description=(
            "RSI 기간을 2일로 짧게 잡아 '상승추세 중 짧은 조정'을 잡는다. "
            "RSI(14)로는 장기선 필터와 공존이 불가능했던 문제를 실제로 해결한 버전 "
            "(종목당 5~13회 진입 확인)."
        ),
        factory=lambda: RsiReversionStrategy(
            RsiReversionParams(rsi_period=2, oversold=10, overbought=70), name="rsi2_pullback"
        ),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "오르고 있는 종목이 잠깐 쉬어 갈 때 삽니다."
        ),
        쉬운참고=(
            "아주 짧은 기간만 보기 때문에 크게 하락하기를 기다리지 않고 조정 폭이 작은 것을 "
            "잡습니다. RSI 반등 30/70이 못 산 문제를 실제로 푼 쪽입니다."
        ),
    ),
    StrategyDefinition(
        key="rsi_reversion_aggressive",
        짧은이름="RSI 반등 공격형",
        display_name="RSI 평균회귀 공격형 (35/65, 필터 없음)",
        description="기준을 완화하고 장기 이동평균 필터도 뺀 버전: 진입은 늘지만 하락장에서 물릴 위험이 커진다.",
        factory=lambda: RsiReversionStrategy(
            RsiReversionParams(oversold=35, overbought=65, require_above_long_ma=False),
            name="rsi_reversion_aggressive",
        ),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "많이 빠진 것을 사되 기준을 느슨하게 두고, 긴 기간 흐름은 보지 않습니다."
        ),
        쉬운참고=(
            "더 자주 사게 되는 대신, 계속 빠지는 종목을 사서 오래 물릴 위험이 큽니다."
        ),
    ),
    StrategyDefinition(
        key="bollinger_reversion",
        짧은이름="볼린저 하단 반등",
        display_name="볼린저 하단 반등 (20/2σ)",
        description="하단 밴드를 벗어났다 복귀하면 매수, 중심선 도달하면 청산. 박스권에서 잘 통하는 전형적 평균회귀.",
        factory=lambda: BollingerReversionStrategy(
            BollingerReversionParams(), name="bollinger_reversion"
        ),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "주가가 평소 움직이던 폭보다 아래로 벗어났다가 다시 그 안으로 돌아오면 삽니다. "
            "너무 많이 빠졌으니 제자리로 돌아올 것으로 보는 것이고, 가운데까지 올라오면 "
            "팝니다."
        ),
        쉬운참고=(
            "한 방향으로 크게 가지 않고 오르내리는 구간에서 잘 맞습니다. 반대로 계속 빠지는 "
            "구간에서는 빠지는 것을 계속 사게 됩니다."
        ),
    ),
    StrategyDefinition(
        key="bollinger_reversion_wide",
        짧은이름="볼린저 하단 넓게",
        display_name="볼린저 하단 반등 넓은밴드 (20/2.5σ)",
        description="밴드를 2.5σ로 넓혀 더 극단적으로 빠졌을 때만 진입: 빈도는 줄고 한 건당 기대값은 커진다.",
        factory=lambda: BollingerReversionStrategy(
            BollingerReversionParams(num_std=2.5, exit_at_middle=False),
            name="bollinger_reversion_wide",
        ),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "볼린저 하단 반등과 같은데 더 크게 벗어났을 때만 삽니다."
        ),
        쉬운참고=(
            "사는 횟수는 줄고 한 번 살 때 기대하는 폭은 커집니다. 횟수가 적으면 성적이 몇 "
            "건에 좌우되므로 숫자를 볼 때 거래 수를 같이 봐야 합니다."
        ),
    ),
    StrategyDefinition(
        key="stochastic_20_80",
        짧은이름="스토캐스틱 교차",
        display_name="스토캐스틱 교차 (14/3, 20·80)",
        description="과매도 구간에서 %K가 %D 상향돌파 시 매수, 과매수 구간 하향이탈 시 매도.",
        factory=lambda: StochasticStrategy(StochasticParams(), name="stochastic_20_80"),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "최근 며칠의 고가와 저가 사이에서 지금 주가가 어디쯤인지를 봅니다. 아래쪽에 "
            "있다가 위로 돌아서면 사고, 위쪽에 있다가 내려오면 팝니다."
        ),
        쉬운참고=(
            "오르내림이 반복되는 구간에서 잘 맞습니다. 한 방향으로 크게 갈 때는 "
            "너무 일찍 팔게 됩니다."
        ),
    ),
    # ── 돌파·모멘텀 ──
    StrategyDefinition(
        key="bollinger_breakout",
        짧은이름="볼린저 상단 돌파",
        display_name="볼린저 상단 돌파 (거래량 확인)",
        description="상단 밴드를 거래량 급증과 함께 뚫으면 매수. 같은 지표를 평균회귀와 정반대로 해석한 가설.",
        factory=lambda: BollingerBreakoutStrategy(
            BollingerBreakoutParams(), name="bollinger_breakout"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "주가가 평소 움직이던 폭보다 위로 벗어나면서 거래량도 크게 늘면 삽니다."
        ),
        쉬운참고=(
            "볼린저 하단 반등과 똑같은 것을 보면서 정반대로 해석합니다. 벗어나면 돌아온다가 "
            "아니라 벗어났으니 더 간다는 쪽입니다. 어느 쪽이 맞는지 재려고 둘 다 등록해 "
            "두었습니다."
        ),
    ),
    StrategyDefinition(
        key="volume_surge_5d",
        짧은이름="거래량 급증 5일",
        display_name="거래량 급증 단타 (2배, 5일 보유)",
        description="거래량 2배 급증 + 2% 이상 상승 시 매수, 5거래일 뒤 무조건 청산. 시간 기준 청산이 특징.",
        factory=lambda: VolumeSurgeStrategy(VolumeSurgeParams(), name="volume_surge_5d"),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "평소보다 거래량이 훨씬 크게 늘면서 주가도 오른 날 삽니다. 관심이 갑자기 몰렸으니 "
            "며칠 더 갈 것으로 보는 것입니다. 정해진 날이 지나면 주가와 관계없이 팝니다."
        ),
        쉬운참고=(
            "언제 팔지가 주가가 아니라 날짜로 정해져 있는 것이 특징입니다."
        ),
    ),
    StrategyDefinition(
        key="volume_surge_5d_ma20",
        짧은이름="거래량 급증 + 20일선",
        display_name="거래량 급증 + 20일선 매도 (최대 5일 보유)",
        description=(
            "사는 조건은 거래량 급증 5일과 같고, 파는 조건만 다르다. "
            "5거래일을 기다리지 않고 종가가 20일 평균선 아래로 내려온 날 판다. "
            "'정해진 날짜가 아니라 추세가 깨질 때 판다'는 가설. "
            "5일 보유는 상한으로 남는다."
        ),
        factory=lambda: VolumeSurgeStrategy(
            VolumeSurgeParams(exit_sma=20), name="volume_surge_5d_ma20"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "사는 조건은 거래량 급증 5일과 같습니다. 파는 방법만 다릅니다. 정해진 날짜를 "
            "기다리지 않고, 주가가 최근 평균 아래로 내려온 날 팝니다."
        ),
        쉬운참고=(
            "흐름이 깨지면 판다는 생각입니다. 정해진 날짜는 더 들고 있지 않는 상한으로만 "
            "남습니다."
        ),
    ),
    StrategyDefinition(
        key="volume_surge_5d_ma10",
        짧은이름="거래량 급증 + 10일선",
        display_name="거래량 급증 + 10일선 매도 (최대 5일 보유)",
        description=(
            "위와 같은데 매도선이 10일 평균이라 더 빨리 판다. "
            "20일선과 나란히 놓고 '얼마나 빨리 파는 것이 나은가'를 본다."
        ),
        factory=lambda: VolumeSurgeStrategy(
            VolumeSurgeParams(exit_sma=10), name="volume_surge_5d_ma10"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "거래량 급증 + 20일선과 같은데 더 짧은 기간의 평균을 봅니다. 그래서 더 빨리 "
            "팝니다."
        ),
        쉬운참고=(
            "얼마나 빨리 파는 것이 나은지 견주려고 나란히 둔 것입니다."
        ),
    ),
    StrategyDefinition(
        key="volume_surge_3d",
        짧은이름="거래량 급증 3일",
        display_name="거래량 급증 초단타 (3배, 3일 보유)",
        description="더 강한 급증(3배)만 잡고 3일 만에 청산: 재료 소멸 전에 빠지는 걸 노린 가설.",
        factory=lambda: VolumeSurgeStrategy(
            VolumeSurgeParams(volume_surge_ratio=3.0, holding_days=3), name="volume_surge_3d"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "거래량이 훨씬 더 크게 늘어난 날만 삽니다. 그리고 더 빨리 팝니다."
        ),
        쉬운참고=(
            "관심이 식기 전에 빠져나오려는 생각입니다."
        ),
    ),
    StrategyDefinition(
        key="price_channel_20",
        짧은이름="20일 신고가 돌파",
        display_name="종가 신고가 돌파 (20일)",
        description="20일 종가 신고가를 넘으면 매수, 20일선 이탈 시 매도. 장중 변동성에 덜 반응하는 돌파.",
        factory=lambda: PriceChannelBreakoutStrategy(
            PriceChannelParams(), name="price_channel_20"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "최근 며칠 중 가장 높았던 종가를 넘어서면 삽니다."
        ),
        쉬운참고=(
            "장중에 잠깐 움직인 가격은 보지 않고 종가만 보기 때문에, 변동성에 덜 "
            "반응합니다."
        ),
    ),
    StrategyDefinition(
        key="price_channel_60_strict",
        짧은이름="60일 신고가 돌파",
        display_name="종가 신고가 돌파 장기·엄격 (60일, +1%)",
        description="60일 신고가를 1% 이상 확실히 뚫어야 진입: 가짜 돌파를 최대한 걸러낸 버전.",
        factory=lambda: PriceChannelBreakoutStrategy(
            PriceChannelParams(lookback=60, breakout_pct=1.0, exit_sma=20),
            name="price_channel_60_strict",
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "20일 신고가 돌파와 같은데 더 긴 기간의 고점을 넘어야 하고, 살짝 넘은 "
            "정도로는 안 삽니다."
        ),
        쉬운참고=(
            "넘는 척하다 마는 경우를 걸러 내려는 것입니다. 그만큼 사는 횟수가 적습니다."
        ),
    ),
    # ── 싸게 재서 기각하려고 만든 것들 (docs/단타전략조사.md) ──
    #
    # 셋 다 유명한데 근거가 얇다. 변동성 돌파는 동료심사 논문을 하나도
    # 못 찾았고, 갭은 문헌 결론이 반반이다. 그래도 등록하는 이유는 이
    # 저장소가 기각된 가설을 자산으로 취급하기 때문이다. 싸게 재서
    # 기각해 두면 같은 걸 두 번 시험하지 않는다.
    StrategyDefinition(
        key="volatility_breakout_k05",
        짧은이름="변동성 돌파",
        display_name="변동성 돌파 (K=0.5, 1일 보유)",
        description=(
            "오늘 시가 + (어제 고가−저가)×0.5 를 넘으면 매수, 다음 날 청산. "
            "래리 윌리엄스 규칙으로 한국에서 가장 널리 알려진 단타 공식인데 "
            "동료심사 논문이 없다. **일봉 근사다**. 돌파선 가격이 아니라 "
            "돌파가 일어난 날 종가로 따라 사므로 원 규칙의 성적이 아니다."
        ),
        factory=lambda: VolatilityBreakoutStrategy(
            VolatilityBreakoutParams(), name="volatility_breakout_k05"
        ),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "오늘 시가에 어제 하루 동안 움직인 폭의 절반을 더합니다. 그 가격을 "
            "넘어서면 삽니다. 하루 안에 크게 움직이기 시작하면 그날은 그 방향으로 계속 "
            "간다고 보는 것이고, 다음 날 팝니다."
        ),
        쉬운참고=(
            "한국에서 널리 알려진 방법이지만 검증된 논문은 없습니다. 그리고 여기서는 하루 "
            "단위 시세로만 계산하므로, 넘어선 그 가격이 아니라 그날 종가에 산 것으로 "
            "칩니다. 실제로 이 방법을 그대로 썼을 때의 성적과는 다릅니다."
        ),
    ),
    StrategyDefinition(
        key="gap_up_go",
        짧은이름="갭 상승 따라가기",
        display_name="갭 상승 따라가기 (2% 이상, 1일 보유)",
        description=(
            "어제 종가보다 2% 이상 높게 시작한 날 매수. '갭 방향으로 계속 간다'는 쪽. "
            "문헌은 갭이 이어질 확률과 되돌아올 확률이 비슷하다고 본다."
        ),
        factory=lambda: GapStrategy(GapParams(direction="up"), name="gap_up_go"),
        category=CATEGORY_BREAKOUT,
        쉬운설명=(
            "전날 종가보다 훨씬 높은 가격으로 장이 시작한 날 삽니다. 위로 벌어졌으면 그 "
            "방향으로 더 간다고 보는 것입니다."
        ),
        쉬운참고=(
            "벌어진 것이 이어질 확률과 되돌아올 확률이 비슷하다는 연구가 많습니다."
        ),
    ),
    StrategyDefinition(
        key="gap_down_fill",
        짧은이름="갭 하락 메우기",
        display_name="갭 하락 메우기 (2% 이상, 1일 보유)",
        description=(
            "어제 종가보다 2% 이상 낮게 시작한 날 매수. '갭은 메워진다'는 쪽. "
            "위와 정반대 가설이라 둘 다 등록한다. 한쪽만 재면 결과를 미리 "
            "정해 놓고 재는 셈이 된다."
        ),
        factory=lambda: GapStrategy(GapParams(direction="down"), name="gap_down_fill"),
        category=CATEGORY_REVERSION,
        쉬운설명=(
            "전날 종가보다 훨씬 낮은 가격으로 장이 시작한 날 삽니다. 벌어진 만큼 다시 "
            "메워진다고 보는 것입니다."
        ),
        쉬운참고=(
            "갭 상승 따라가기와 정반대 생각입니다. 한쪽만 계산하면 답을 정해 놓고 재는 셈이 "
            "되어 둘 다 등록해 두었습니다."
        ),
    ),
    # ── 점수 합산: 조건 하나의 참/거짓이 아니라 여러 관점을 점수로 더한다 ──
    StrategyDefinition(
        key="factor_score_v1",
        짧은이름="종합점수 합산",
        display_name="Factor 점수 합산 V1 (추세+모멘텀+상대강도+눌림+거래량+국면)",
        description=(
            "6개 관점을 각각 0~100점으로 매기고 가중 합산해 75점 이상이면 매수. "
            "가중치와 ON/OFF를 설정에서 바꿀 수 있어, 코드 수정 없이 전략을 실험한다. "
            "상대강도(유니버스 내 순위)와 시장국면(Breadth)은 종목 하나만 보던 "
            "기존 구조에서는 만들 수 없던 변수다."
        ),
        factory=lambda: FactorScoreStrategy(load_strategy_config()),
        category=CATEGORY_SCORE,
        쉬운설명=(
            "여섯 가지 관점에서 각각 점수를 매기고 합쳐서, 기준을 넘으면 삽니다."
        ),
        쉬운참고=(
            "어느 관점을 얼마나 볼지 설정에서 바꿀 수 있어서 코드를 고치지 않고 시험해 볼 "
            "수 있습니다."
        ),
    ),
    StrategyDefinition(
        key="us_sector_follow_60_2",
        짧은이름="미국 섹터 따라가기",
        display_name="미국 섹터 따라가기 (60일 상대강도 상위 2, 20일 보유)",
        description="미국 섹터 ETF의 60일 상대강도 상위 2개 중 60일선 위인 섹터의 국내 종목을 20일선 위·60일 수익률 플러스일 때 매수. 섹터 약해지거나 20일선 아래면 매도.",
        factory=lambda: USSectorFollowStrategy(N=60, k=2, 지연=1, 보유상한=20,
                                               name="us_sector_follow_60_2"),
        category=CATEGORY_HYBRID,
        status="backtested",
        쉬운설명=(
            "미국에서 요즘 잘 오르는 업종을 먼저 보고, 우리나라의 같은 업종 회사 중 "
            "주가가 오르고 있는 것을 삽니다."
        ),
        쉬운참고=(
            "미국 시세는 하루 늦게 봅니다. 미국 시세를 못 받는 날은 아무것도 사지 않습니다."
        ),
    ),
    StrategyDefinition(
        key="volume_surge_3d_us60_2",
        짧은이름="거래량 급증 3일 + 미국 섹터",
        display_name="거래량 급증 초단타 (3배, 3일 보유) + 미국 섹터 필터 (60일 상위 2)",
        description="거래량 급증 3일의 매수 신호 중 미국 섹터 ETF 60일 상대강도 상위 2개(60일선 위)에 속한 섹터 종목만 매수. 매도는 거래량 급증 3일 규칙 그대로.",
        factory=lambda: USSectorGateStrategy(
            VolumeSurgeStrategy(VolumeSurgeParams(volume_surge_ratio=3.0, holding_days=3),
                                name="volume_surge_3d"),
            원래키="volume_surge_3d", N=60, k=2, 지연=1, name="volume_surge_3d_us60_2",
        ),
        category=CATEGORY_BREAKOUT,
        status="backtested",
        쉬운설명=(
            "거래량이 훨씬 크게 늘어난 날 사는 규칙은 그대로 두고, 그중에서 미국에서 "
            "요즘 잘 오르는 업종에 속한 회사만 삽니다. 파는 규칙은 원래 전략 그대로입니다."
        ),
        쉬운참고=(
            "미국 업종 흐름은 하루 늦게 봅니다. 미국 시세를 못 받는 날은 아무것도 사지 않습니다."
        ),
    ),
]


def get_definition(key: str) -> StrategyDefinition:
    for definition in REGISTRY:
        if definition.key == key:
            return definition
    known = ", ".join(d.key for d in REGISTRY)
    raise KeyError(f"등록되지 않은 전략 키: '{key}' (등록된 키: {known})")


def build_strategy(key: str) -> Strategy:
    """전략 객체를 만든다. **정의에 적힌 익절선을 객체에 붙여 준다.**

    청산 기준을 읽는 쪽(`risk/exits.익절기준`)은 전략 객체 하나만 받는다.
    보유기간은 원래 객체에 있는데 익절만 정의 쪽에 있으면, 부르는 자리마다
    둘을 따로 찾아야 하고 한쪽을 빠뜨리면 조용히 안 걸린다."""
    정의 = get_definition(key)
    전략 = 정의.factory()
    if 정의.익절 and getattr(전략, "take_profit_pct", 0.0) in (0.0, None):
        try:
            전략.take_profit_pct = 정의.익절
        except AttributeError:
            # 값을 못 붙이는 전략(frozen)은 조용히 넘어가면 안 된다.
            # 익절을 정해 뒀는데 안 걸리는 상태가 된다.
            raise ValueError(
                f"{key}: 익절선을 붙일 수 없는 전략입니다. "
                "전략 클래스에 take_profit_pct를 직접 두세요."
            ) from None
    return 전략


def build_strategies(keys, combine: str = "OR", sell_keys=()):
    """전략 키 여러 개를 하나로 묶어 돌려준다.

    하나뿐이면 그대로 돌려준다. 굳이 감싸면 기록에 남는 전략 이름이
    바뀌어서, 지금까지 쌓인 매매 기록과 이어지지 않는다.

    `sell_keys`를 주면 **파는 쪽을 따로 굴린다.** 매수 신호는 `keys`에서만,
    매도 신호는 `sell_keys`에서만 나온다. 안 주면 지금까지와 똑같이 한 묶음이
    양쪽을 다 맡는다. 기본값을 바꾸면 지금 돌고 있는 설정의 뜻이 달라진다."""
    from muwon.strategy.combined import CombinedStrategy
    from muwon.strategy.split import SplitStrategy

    def 묶기(것들):
        것들 = [k for k in 것들 if k]
        if not 것들:
            raise ValueError("전략을 하나 이상 지정하세요")
        if len(것들) == 1:
            return build_strategy(것들[0])
        return CombinedStrategy([build_strategy(k) for k in 것들], mode=combine)

    사는쪽 = 묶기(keys)
    파는키 = [k for k in (sell_keys or ()) if k]
    if not 파는키:
        return 사는쪽
    # 파는 쪽이 사는 쪽과 같으면 굳이 감싸지 않는다. 감싸면 기록에 남는
    # 전략 이름이 바뀌어서 지금까지 쌓인 매매와 이어지지 않는다.
    if list(파는키) == [k for k in keys if k]:
        return 사는쪽
    한글 = " + ".join(get_definition(k).화면이름 for k in 파는키)
    return SplitStrategy(사는쪽, 묶기(파는키), 매도한글=한글)


def list_definitions(category: str | None = None) -> list[StrategyDefinition]:
    if category is None:
        return list(REGISTRY)
    return [d for d in REGISTRY if d.category == category]
