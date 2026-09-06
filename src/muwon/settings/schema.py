from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPolicy:
    max_position_weight: float = 0.15
    stop_loss_pct: float = -0.05
    daily_loss_limit_pct: float = -0.03
    max_concurrent_positions: int = 8
    #: **매수 스위치.** False면 신규 진입을 전부 거부한다.
    #: 이름이 trading_enabled인 것은 예전에 이것 하나뿐이었기 때문이다.
    #: 시트 열쇠(`trading_enabled`)와 DB 열쇠를 그대로 두려고 안 바꿨다.
    trading_enabled: bool = True

    #: **매도 스위치.** False면 손절·익절·보유일수 청산·매도 신호가 **전부**
    #: 멈춘다.
    #:
    #: ## 매수와 안전한 방향이 정반대다
    #:
    #: 매수는 못 하면 기회를 놓칠 뿐이지만, **매도를 못 하면 손실이 그대로
    #: 자란다.** 그래서 두 스위치는 고장났을 때 기우는 쪽이 반대다:
    #:
    #:   - 매수: 설정을 못 읽으면 **끈다** (모르면 안 사는 쪽이 안전)
    #:   - 매도: 설정을 못 읽으면 **켠다** (모르면 파는 쪽이 안전)
    #:
    #: 이걸 끄는 것은 "오늘은 손절도 안 걸리게 두겠다"는 뜻이다. 값이
    #: 반토막 나도 아무 일도 일어나지 않는다. 화면과 알림이 그 사실을
    #: 계속 말하도록 해 뒀다.
    sell_enabled: bool = True

    # 변동성 기반 청산. 고정 %는 모든 종목에 같은 자를 들이대는데, 하루 1%
    # 움직이는 종목과 4% 움직이는 종목에 같은 -5%를 적용하면 후자는 이틀치
    # 잡음에 손절당한다. ATR(그 종목이 하루에 보통 움직이는 폭)의 배수로
    # 잡으면 종목 성격에 맞춰진다. 끄면 위의 고정 stop_loss_pct로 돌아간다.
    #: 목표 수익률에 닿으면 판다(익절). 0이면 끔: 지금까지 이 시스템에는
    #: 익절이 아예 없었다. 오르는 중이면 손절이나 보유 기간에 걸릴 때까지
    #: 그대로 들고 갔다. 기본값을 0으로 두는 것은 "익절이 유리한지 아직
    #: 재지 않았다"는 뜻이지, 익절이 나쁘다는 뜻이 아니다.
    take_profit_pct: float = 0.0

    #: 보유 기간 상한을 기준 쪽에서 덮어쓴다. **0이면 전략이 정한 대로 간다.**
    #:
    #: 원래 이 값은 전략 안에 있었다(거래량 급증 5일 → 5일). 그건 전략마다
    #: 다른 것이 맞지만, "며칠까지 들고 있을 것인가"는 전략을 안 바꾸고도
    #: 정하고 싶은 값이다. 그래서 손절·익절과 같은 자리에 덮개를 둔다.
    #:
    #: 기본값이 0인 것은 "안 정했다"가 아니라 **"전략에게 맡긴다"**는 뜻이다.
    #: 지금까지의 전략 평가 결과가 전부 그 상태에서 나온 숫자다.
    max_holding_days: int = 0

    #: 며칠까지는 안 판다. 0이면 안 건다(2026-09-06에 더함).
    #:
    #: **손절은 이것을 무시한다.** 최소 보유기간은 "너무 일찍 이익을
    #: 확정하지 마라"는 뜻이지 "손실을 끝까지 안고 가라"가 아니다. 막는
    #: 것은 익절과 트레일링과 전략의 매도 신호 셋이다.
    #:
    #: 상한보다 크게 적히면 상한이 이긴다. 엔진이 보유 상한을 먼저 보므로
    #: 저절로 그렇게 되고, 파는 쪽으로 기우는 것이 안전하다.
    min_holding_days: int = 0

    #: 승인할 때 본 값보다 이만큼 넘게 비싸면 안 산다(2026-09-06에 더함).
    #: 0.05가 5%다. 0이면 안 막는다.
    #:
    #: 09:05 매수는 어제 종가로 시장가 주문을 낸다. 그 사이에 밤이 하나
    #: 있고, 갭이 크게 뜬 날에는 승인할 때 본 값과 전혀 다른 값에 산다.
    #: 손절선이 -5%인데 +5%에 사면 사자마자 손절 근처다.
    #:
    #: **비싼 쪽만 막는다.** 싸게 시작한 날은 그대로 산다. 그것까지 막는
    #: 것은 슬리피지 방어가 아니라 "신호가 깨졌다"는 다른 판단이다.
    #:
    #: **파는 것은 절대 안 막는다.** 갭 하락한 날 손절을 막으면 이 값이
    #: 손실을 키우는 장치가 된다.
    max_entry_slip_pct: float = 0.05

    atr_stop_enabled: bool = False
    atr_stop_multiple: float = 2.0
    trailing_stop_enabled: bool = False
    trailing_stop_multiple: float = 3.0
    atr_window: int = 14


@dataclass(frozen=True)
class KISCredentials:
    kis_env: str = "paper"  # "paper" | "real"
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    account_product_cd: str = "01"

    @property
    def is_real(self) -> bool:
        return self.kis_env == "real"


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class StrategySelection:
    """지금 실거래(run_paper_trading.py/run_realtime_trading.py)가 쓸
    전략을 가리키는 값. strategy/registry.py에 등록된 키여야 한다. 이걸
    바꾸는 건 곧 "가설을 실거래로 승격"하는 행위라서, 코드 배포 없이
    설정값 하나로 되고 변경 이력에도 자동으로 남는다."""

    #: 실거래에 걸 전략 키들. 개수 제한은 없다.
    active_keys: tuple[str, ...] = ("ma_rsi_v1",)
    #: 여러 개일 때 묶는 방식. "OR"는 하나라도 사라고 하면, "AND"는 전부 사라고 해야.
    #: 파는 쪽은 이 값과 무관하게 언제나 OR다(strategy/combined.py 참고).
    combine: str = "OR"
    #: 파는 쪽을 따로 굴릴 때 쓸 전략 키들. **비어 있으면 active_keys가 양쪽을
    #: 다 맡는다**. 지금까지의 동작이 그것이고, 기본값을 바꾸면 이미 돌고 있는
    #: 설정의 뜻이 달라진다.
    sell_keys: tuple[str, ...] = ()

    @property
    def 매도따로(self) -> bool:
        return bool(self.sell_keys) and tuple(self.sell_keys) != tuple(self.active_keys)

    @property
    def sell_key(self) -> str:
        """파는 쪽 대표 하나. 따로 안 걸었으면 사는 쪽과 같다."""
        if self.sell_keys:
            return self.sell_keys[0]
        return self.active_key

    @property
    def active_key(self) -> str:
        """예전 코드가 쓰던 '전략 하나'. 첫 번째 것을 돌려준다.

        전략을 여러 개 걸 수 있게 바꾸면서도 이 이름을 남겨 둔 것은, 리포트나
        스크립트가 '대표 전략 하나'만 필요로 하는 자리가 아직 있기 때문이다.
        매매 자체는 active_keys 전부를 쓴다."""
        return self.active_keys[0] if self.active_keys else ""

    def describe(self) -> str:
        """사람이 읽을 한 줄. 로그·화면이 같은 말을 쓰게 한 군데에 둔다."""
        if len(self.active_keys) <= 1:
            산다 = self.active_key or "(없음)"
        else:
            묶음 = "모두 동의해야" if self.combine == "AND" else "하나라도 신호나면"
            산다 = f"{len(self.active_keys)}개 · {묶음} · {', '.join(self.active_keys)}"
        if not self.매도따로:
            return 산다
        # 매도를 따로 걸었으면 반드시 같이 적는다. 사는 쪽만 보이면
        # "왜 저 규칙으로 팔렸지"를 설명할 수 없다.
        return f"매수 {산다} / 매도 {', '.join(self.sell_keys)}"
