from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AppSettingRow(Base):
    """KIS 인증정보/텔레그램/리스크 정책 등, 재시작 없이 바꿀 수 있어야 하는
    설정값 저장소. muwon.settings.store.SettingsStore가 이 테이블을 통해
    읽고 쓴다 — CLI와 (Phase 2+) 대시보드가 공유하는 단일 소스."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AppSettingHistoryRow(Base):
    """AppSettingRow 값이 바뀔 때마다 이전/이후 값을 남기는 append-only 로그.
    대시보드의 '변경 이력' 탭이 이 테이블을 읽는다. 비밀값은 원문(is_secret=True
    이면 암호문)이 그대로 저장되므로, 조회 시 AppSettingRow와 같은 마스터키로
    복호화해야 한다."""

    __tablename__ = "app_settings_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PriceBarRow(Base):
    __tablename__ = "price_bars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    strategy_name: Mapped[str] = mapped_column(String(50))
    signal_type: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    side: Mapped[str] = mapped_column(String(4))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    is_paper: Mapped[bool] = mapped_column(default=True)
    kis_order_id: Mapped[str] = mapped_column(String(50), default="")
    reason: Mapped[str] = mapped_column(String(100), default="")
    #: 판단 근거가 된 가격(전략이 본 마지막 종가). price와 함께 있어야
    #: "결정한 가격과 실제로 산 가격이 얼마나 벌어졌나"를 잴 수 있다.
    #: 나중에 추가된 컬럼이라 nullable — 이전 주문에는 값이 없다.
    reference_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: price가 실제 체결가인지(True), 조회 실패로 기준가를 쓴 것인지(False).
    #: 구분이 없으면 슬리피지 통계에 '차이 0'인 가짜 표본이 섞인다.
    fill_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class EngineStateRow(Base):
    """TradingEngine이 회차 사이에 이어가야 하는 내부 상태(가상 현금,
    당일 시작 평가금액 등). 사용자가 만지는 app_settings와는 다른
    성격이라 별도 테이블로 둔다."""

    __tablename__ = "engine_state"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class PositionRow(Base):
    """실거래/모의투자 엔진(TradingEngine)이 회차마다 새로 뜨지 않고도 보유
    종목을 이어서 추적할 수 있도록 남기는 상태. 백테스트의 OpenPosition과
    같은 정보를 갖지만, 여긴 프로세스 재시작에도 살아남아야 해서 DB에 둔다."""

    __tablename__ = "positions"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[date] = mapped_column(Date)
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    entry_reason: Mapped[str] = mapped_column(String(100), default="")
    strategy_key: Mapped[str] = mapped_column(String(50), default="")


class TradeRow(Base):
    """진입~청산이 하나로 묶인 '완결된 매매' 기록 — OrderRow는 체결 하나하나를
    남기지만(매수/매도가 서로 안 엮여 있음), 이건 손익까지 계산된 라운드트립
    이라 "이 전략/가설이 실전에서 어떻게 됐는지"를 바로 분석할 수 있다.
    사람이든, 나중에 붙을 AI 제언 로직이든, 전략을 고치자는 판단은 결국 이
    테이블을 근거로 한다 — 그래서 strategy_key를 반드시 채워서 가설별로
    묶어 볼 수 있게 한다."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    strategy_key: Mapped[str] = mapped_column(String(50), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    entry_reason: Mapped[str] = mapped_column(String(100), default="")
    exit_reason: Mapped[str] = mapped_column(String(100), default="")
    pnl_amount: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime)
    exited_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class BacktestRunRow(Base):
    """가설 스윕(scripts/run_hypothesis_sweep.py)이 남기는 백테스트 실행
    기록. 콘솔에 찍고 끝나면 다음 실행과 비교할 방법이 없어서, 같은 스키마로
    누적 저장해 시간이 지나도(파라미터를 바꿔가며 여러 번 돌려도) 가설별
    성과를 추적할 수 있게 한다."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_key: Mapped[str] = mapped_column(String(50), index=True)
    params_json: Mapped[str] = mapped_column(Text, default="")  # 재현 가능하도록 실제 파라미터 스냅샷
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    total_return_pct: Mapped[float] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float)
    win_rate_pct: Mapped[float] = mapped_column(Float)
    num_trades: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UniverseSnapshotRow(Base):
    """유니버스(매매 대상 종목 목록)를 갱신할 때마다 남기는 스냅샷.

    손으로 고른 고정 목록은 시간이 지나면 낡는다(상장폐지·순위 역전 등).
    시가총액 상위로 주기적으로 다시 뽑되, 덮어쓰지 않고 스냅샷으로 쌓는다 —
    "어제와 오늘 종목이 뭐가 달라졌는지"를 볼 수 있어야 성과 변화를 종목
    교체 탓인지 전략 탓인지 구분할 수 있기 때문이다."""

    __tablename__ = "universe_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(100))
    market: Mapped[str] = mapped_column(String(20))  # KOSPI | KOSDAQ
    market_cap: Mapped[int] = mapped_column(Integer, default=0)  # 억원
    #: 거래대금 상위로 뽑은 스냅샷일 때의 누적거래대금(백만원). 시총 스냅샷은 0.
    #: market_cap 컬럼에 뜻이 다른 값을 같이 담지 않는다 — 나중에 표를 읽는
    #: 사람이 어느 쪽 숫자인지 알 수 없게 된다.
    #: 나중에 추가된 컬럼이라 nullable이다. _add_missing_columns가 기존 DB에
    #: ALTER TABLE ADD COLUMN으로 붙이면 기존 행은 NULL이 된다 — 새로 만든
    #: 스키마만 NOT NULL로 두면 테스트가 운영 DB 상태를 재현하지 못한다.
    turnover: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    #: "market_cap" | "volume" — 어떤 기준으로 뽑은 목록인지.
    #: 이게 없으면 거래량 유니버스를 저장하는 순간 실거래가 그걸 집어 간다.
    #: 실험용 목록 하나가 실계좌의 매매 대상을 바꾸는 일은 없어야 한다.
    kind: Mapped[str | None] = mapped_column(
        String(20), default="market_cap", index=True, nullable=True
    )


class RunLogRow(Base):
    """엔진이 한 번 돌 때마다 남기는 '무엇을 보고 무엇을 했나' 한 줄.

    이게 없으면 빈 대시보드가 서로 다른 두 가지를 동시에 뜻한다 — "오늘은
    살 게 없었다"와 "오늘은 아예 안 돌았다". 둘은 고치는 방법이 정반대다.
    그래서 체결이 없어도 한 줄은 남긴다.

    같은 날 손으로 여러 번 돌리면 줄도 여러 개 남는다. 합치지 않는다 —
    "몇 번 돌았나"도 알아야 할 정보다."""

    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 판단에 쓴 마지막 '완성된' 일봉의 날짜. 시세를 하나도 못 받으면 NULL —
    #: 그 자체가 "데이터 공급이 끊겼다"는 신호다.
    run_date: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    strategy_key: Mapped[str] = mapped_column(String(50), default="")
    #: 매매 대상 종목 수와, 그중 실제로 시세를 받아 판단한 종목 수.
    #: 둘이 벌어지면 데이터 공급 쪽 문제다.
    universe_size: Mapped[int] = mapped_column(Integer, default=0)
    checked_symbols: Mapped[int] = mapped_column(Integer, default=0)
    buy_signals: Mapped[int] = mapped_column(Integer, default=0)
    sell_signals: Mapped[int] = mapped_column(Integer, default=0)
    orders: Mapped[int] = mapped_column(Integer, default=0)
    #: 리스크 매니저가 막은 이유들(줄바꿈 구분). 신호는 났는데 주문이 없으면
    #: 여기에 이유가 있다 — 없으면 애초에 신호가 안 난 것이다.
    rejections: Mapped[str] = mapped_column(Text, default="")
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class StrategyChangeRow(Base):
    """전략을 바꾸자는 제안 하나. 예약부터 반영이나 취소까지 한 줄로 이어진다.

    ## 왜 예약과 이력을 한 테이블에 두나

    "그때 왜 바꿨지"에 답하려면 바꾼 시각만으로는 모자라다. 그때 무슨 숫자를
    보고 골랐는지, 어느 구간이 그 전략을 가리켰는지가 같이 있어야 한다. 예약할
    때 그 근거가 이미 손에 있으므로, 그 줄을 그대로 끌고 가서 반영 표시만
    바꾸는 것이 제일 싸고 어긋날 일도 없다.

    ## 상태

    - `고름`: 버튼을 한 번 눌렀다. 아직 확정이 아니다.
    - `확정`: 확인 버튼까지 눌렀다. 다음 반영 때 실제로 바뀐다.
    - `반영`: 실제로 바꿨다. 여기서부터 이력이다.
    - `취소`: 사람이 되돌렸다. 반영 전이었다.
    - `되돌림`: 반영한 뒤 이전 전략으로 복귀했다.
    - `막힘`: 반영하려 했는데 조건에 걸려 못 했다. 까닭을 같이 적는다.

    **`고름`과 `확정`은 동시에 하나만 있어야 한다.** 둘을 동시에 예약하면
    다음 날 무엇이 반영되는지 알 수 없다. 그 규칙은 여기가 아니라
    `cloud/strategy_approval.py`가 지킨다.
    """

    __tablename__ = "strategy_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 제안을 낸 날. 버튼 콜백에 실려 오는 값과 같아야 한다. 어제 버튼으로
    #: 오늘 바꾸는 것을 막는 자리다.
    제안일: Mapped[date] = mapped_column(Date, index=True)
    상태: Mapped[str] = mapped_column(String(10), default="고름", index=True)
    이전전략: Mapped[str] = mapped_column(String(50), default="")
    새전략: Mapped[str] = mapped_column(String(50), default="")
    #: 어느 구간이 이 전략을 가리켰나. 여럿이면 쉼표로 잇는다.
    근거구간: Mapped[str] = mapped_column(String(100), default="")
    #: 제안을 낼 때의 등급(이상없음 / 살펴볼것 / 확인필요).
    등급: Mapped[str] = mapped_column(String(10), default="")
    #: 그때 두 전략의 수익률과 거래 수. 나중에 "그 숫자가 맞았나"를 되짚는다.
    이전수익률: Mapped[float | None] = mapped_column(Float, nullable=True)
    새수익률: Mapped[float | None] = mapped_column(Float, nullable=True)
    거래수: Mapped[int] = mapped_column(Integer, default=0)
    #: 사람이 읽는 사유. 후보글과 트렌드글을 그대로 담는다.
    사유: Mapped[str] = mapped_column(Text, default="")
    #: 반영이 막혔으면 왜 막혔는지. 조용히 안 바뀌면 원인을 못 찾는다.
    막힌까닭: Mapped[str] = mapped_column(Text, default="")
    #: 어디서 눌렀나. 지금은 텔레그램뿐이고, 나중에 화면이 붙을 수 있다.
    승인경로: Mapped[str] = mapped_column(String(20), default="")
    만든때: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    바뀐때: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    반영때: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StrategyShadowRow(Base):
    """검토가 낸 순위 한 줄을 그대로 남겨 두고, 나중에 실제로 어떻게 됐는지
    되짚는 기록. **고른 것뿐 아니라 안 고른 것도 남긴다.**

    ## 왜 안 고른 것까지 남기나

    바꾸지 않기로 한 판단도 판단이다. 고른 것만 남기면 "바꿨더니 좋아졌다"는
    볼 수 있지만 "안 바꿨더니 좋았다"는 볼 수가 없다. 그러면 이 저장소가
    쌓아 온 기각 기록이 한쪽만 남는다.

    ## 한 줄은 전략 하나다

    같은 날 같은 구간에서 여러 줄이 나온다. 그때 걸려 있던 전략 한 줄과,
    그날 순위 위쪽 몇 줄이다. 견주는 일은 읽을 때 한다 — 저장할 때 미리
    빼 두면 나중에 다른 방식으로 견주고 싶을 때 다시 잴 수가 없다.

    ## 상태

    - `열림`: 아직 지평이 안 지났다. 뒤 숫자가 비어 있다.
    - `닫힘`: 지평이 지나 실제 수익률을 쟀다.
    - `못잼`: 지평은 지났는데 시세가 모자라 못 쟀다. 까닭을 같이 적는다.

    **`못잼`을 `닫힘`과 같이 두면 안 된다.** 못 잰 것을 0%로 세면 신호가
    실제보다 밋밋해진다.
    """

    __tablename__ = "strategy_shadows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 검토가 이 순위를 낸 날. 뒤 수익률을 재는 시작점이기도 하다.
    제안일: Mapped[date] = mapped_column(Date, index=True)
    #: 어느 구간의 순위였나. "1주" / "1개월" / "3개월".
    구간: Mapped[str] = mapped_column(String(10), index=True)
    전략: Mapped[str] = mapped_column(String(50), index=True)
    #: 그날 그 구간 순위에서 몇 번째였나. 1이 제일 좋았던 것이다.
    자리: Mapped[int] = mapped_column(Integer, default=0)
    #: 그때 실제로 걸려 있던 전략인가.
    지금것: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 그날 버튼으로 내보낸 후보인가.
    제안것: Mapped[bool] = mapped_column(Boolean, default=False)
    #: 사람이 실제로 이걸 골라서 반영했나. 지평을 잴 때 채운다.
    골랐나: Mapped[bool] = mapped_column(Boolean, default=False)
    등급: Mapped[str] = mapped_column(String(10), default="")
    #: 그날 순위를 만든 숫자. 이 값이 뒤 수익률을 맞혔는지가 이 표의 질문이다.
    제안시수익률: Mapped[float | None] = mapped_column(Float, nullable=True)
    제안시거래수: Mapped[int] = mapped_column(Integer, default=0)
    상태: Mapped[str] = mapped_column(String(10), default="열림", index=True)
    #: 뒤 수익률을 잰 날. 제안일부터 이 날까지를 잰다.
    잰날: Mapped[date | None] = mapped_column(Date, nullable=True)
    지난날수: Mapped[int] = mapped_column(Integer, default=0)
    #: 제안일부터 잰날까지 이 전략을 걸었다면 나왔을 수익률.
    뒤수익률: Mapped[float | None] = mapped_column(Float, nullable=True)
    뒤거래수: Mapped[int] = mapped_column(Integer, default=0)
    뒤최대낙폭: Mapped[float | None] = mapped_column(Float, nullable=True)
    못잰까닭: Mapped[str] = mapped_column(Text, default="")
    만든때: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    바뀐때: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
