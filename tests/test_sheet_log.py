"""기록을 시트에 쌓는 자리.

제일 중요한 시험은 **두 번 돌려도 두 줄이 되지 않는다**는 것이다. 워크플로
재실행은 정상적인 수단이고, 그때마다 줄이 늘면 시트를 세어 만든 숫자가
전부 틀린다."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from muwon.cloud.sheet_log import (
    MAX_CELL,
    append,
    daily_rows,
    forecast_rows,
    only_new,
    trade_rows,
    매매머리,
)


@dataclass
class 가짜매매:
    id: int = 1
    symbol: str = "005930"
    strategy_key: str = "volume_surge_5d"
    quantity: int = 10
    entry_price: float = 70000.0
    exit_price: float = 72000.0
    entry_reason: str = "거래량 급증"
    exit_reason: str = "보유기간 만료"
    pnl_amount: float = 20000.0
    pnl_pct: float = 2.86
    is_paper: bool = True
    entered_at: datetime = datetime(2026, 8, 10, 9, 5, tzinfo=UTC)
    exited_at: datetime = datetime(2026, 8, 14, 9, 5, tzinfo=UTC)


@dataclass
class 가짜전망:
    기준일: date = date(2026, 8, 19)
    대상: str = "반도체"
    지평: int = 20
    중앙값: float | None = 2.1
    상승확률: float | None = 62.0
    하위10: float | None = -11.4
    구간수: int = 34
    우연을_넘었나: bool = False

    @property
    def 낼수있나(self):
        return self.중앙값 is not None


def test_매매줄의_열쇠는_id에서_나온다():
    줄 = trade_rows([가짜매매(id=31)])[0]
    assert 줄[0] == "T31"
    assert 줄[2] == "005930"
    assert 줄[-1] == "모의"


def test_실거래와_모의를_구분해_적는다():
    """섞이면 나중에 슬리피지를 잴 때 모의 숫자를 실측으로 착각한다."""
    assert trade_rows([가짜매매(is_paper=False)])[0][-1] == "실거래"


def test_전망줄의_열쇠는_낸날_대상_지평():
    줄 = forecast_rows([가짜전망()])[0]
    assert 줄[0] == "F2026-08-19|반도체|20"
    assert 줄[-1] == ""  # 실제 결과는 지평이 지난 뒤에 채운다


def test_못낸_전망은_숫자칸을_비운다():
    """0으로 채우면 '0%로 전망했다'로 읽힌다 — 안 낸 것과 다르다."""
    줄 = forecast_rows([가짜전망(중앙값=None)])[0]
    assert 줄[4:9] == ["", "", "", "", ""]


def test_하루요약은_날짜가_열쇠라_두번_돌아도_한줄():
    줄들 = daily_rows(date(2026, 8, 19), 매수=2, 매도=1, 거부=3)
    assert 줄들[0][0] == "D2026-08-19"
    assert only_new({"D2026-08-19"}, 줄들) == []


def test_이미_있는_열쇠는_안_올린다():
    후보 = trade_rows([가짜매매(id=1), 가짜매매(id=2)])
    남은것 = only_new(["T1"], 후보)
    assert [줄[0] for 줄 in 남은것] == ["T2"]


def test_한번에_올리는_묶음_안의_중복도_거른다():
    후보 = trade_rows([가짜매매(id=7), 가짜매매(id=7)])
    assert len(only_new([], 후보)) == 1


def test_긴_이유는_잘라_넣는다():
    긴것 = "가" * 500
    줄 = trade_rows([가짜매매(exit_reason=긴것)])[0]
    assert len(줄[10]) == MAX_CELL
    assert 줄[10].endswith("…")


class 가짜시트:
    """구글 대신. 호출된 것을 기록만 한다."""

    def __init__(self, 탭들=(), 열쇠들=()):
        self.탭들 = list(탭들)
        self.열쇠들 = list(열쇠들)
        self.올린것 = []
        self.만든탭 = []

    def get(self, spreadsheetId):
        return _즉시({"sheets": [{"properties": {"title": t}} for t in self.탭들]})

    def batchUpdate(self, spreadsheetId, body):
        for 요청 in body["requests"]:
            제목 = 요청["addSheet"]["properties"]["title"]
            self.탭들.append(제목)
            self.만든탭.append(제목)
        return _즉시({})

    def values(self):
        return _값들(self)


class _값들:
    def __init__(self, 시트):
        self.시트 = 시트

    def get(self, spreadsheetId, range):
        return _즉시({"values": [[k] for k in self.시트.열쇠들]})

    def update(self, **kw):
        return _즉시({})

    def append(self, spreadsheetId, range, valueInputOption, insertDataOption, body):
        self.시트.올린것.extend(body["values"])
        return _즉시({})


class _즉시:
    def __init__(self, 값):
        self.값 = 값

    def execute(self, num_retries=0):
        return self.값


def test_탭이_없으면_만들고_머리줄을_넣는다():
    시트 = 가짜시트(탭들=["섹터"])
    올린수 = append("id", "매매기록", 매매머리, trade_rows([가짜매매(id=1)]), svc=시트)
    assert 시트.만든탭 == ["매매기록"]
    assert 올린수 == 1


def test_재실행해도_같은_줄을_또_올리지_않는다():
    시트 = 가짜시트(탭들=["매매기록"], 열쇠들=["열쇠", "T1"])
    올린수 = append("id", "매매기록", 매매머리, trade_rows([가짜매매(id=1)]), svc=시트)
    assert 올린수 == 0
    assert 시트.올린것 == []


def test_새_줄만_올린다():
    시트 = 가짜시트(탭들=["매매기록"], 열쇠들=["열쇠", "T1"])
    올린수 = append(
        "id", "매매기록", 매매머리, trade_rows([가짜매매(id=1), 가짜매매(id=2)]), svc=시트
    )
    assert 올린수 == 1
    assert [줄[0] for 줄 in 시트.올린것] == ["T2"]


def test_올릴것이_없으면_구글에_붙지도_않는다():
    assert append("id", "매매기록", 매매머리, [], svc=None) == 0


# ── 화면이 쓰는 네 탭 ────────────────────────────────────────────────────
#
# 2026-08-26까지 이 넷은 muwon.db에만 있었고, 화면은 n8n을 거쳐 구글 시트만
# 볼 수 있어서 넷 다 예시 자료로 그려졌다. 여기서 시트로 내보낸다.


@dataclass
class 가짜주문:
    id: int = 7
    symbol: str = "066970"
    side: str = "buy"
    quantity: int = 12
    price: float = 118241.0
    is_paper: bool = True
    kis_order_id: str = "0000123456"
    fill_confirmed: bool | None = True
    created_at: datetime = datetime(2026, 8, 25, 9, 5, tzinfo=UTC)


@dataclass
class 가짜회차:
    id: int = 3
    strategy_key: str = "volume_surge_5d"
    universe_size: int = 45
    checked_symbols: int = 45
    buy_signals: int = 2
    sell_signals: int = 0
    orders: int = 0
    rejections: str = "비중 상한 2건\n현금 부족"
    cash: float = 6998455.0
    equity: float = 10043405.0
    created_at: datetime = datetime(2026, 8, 26, 9, 5, tzinfo=UTC)


@dataclass
class 가짜변경:
    id: int = 11
    key: str = "stop_loss_pct"
    old_value: str = "-5"
    new_value: str = "-7"
    is_secret: bool = False
    changed_at: datetime = datetime(2026, 8, 26, 21, 10, tzinfo=UTC)


def test_주문은_체결로_짝지어지기_전에도_남는다():
    """완결된 매매는 팔아야 생긴다. 산 날에 아무것도 안 남으면 화면이
    '오늘 아무 일도 없었다'로 보인다."""
    from muwon.cloud.sheet_log import order_rows

    (줄,) = order_rows([가짜주문()])

    assert 줄[0] == "O7"
    assert 줄[2] == "066970"
    assert 줄[3] == "매수", "buy를 그대로 두면 화면에서 못 읽는다"
    assert 줄[6] == "체결"


def test_값이_진짜_체결가인지_구분해서_적는다():
    """조회에 실패해 기준가를 적어 놓고 '체결'이라고 하면, 슬리피지 통계에
    차이 0인 가짜 표본이 섞인다. 화면에서도 같은 문제다."""
    from muwon.cloud.sheet_log import order_rows

    확인됨, 미확인, 아직 = order_rows([
        가짜주문(id=1, fill_confirmed=True),
        가짜주문(id=2, fill_confirmed=False),
        가짜주문(id=3, fill_confirmed=None),
    ])

    assert 확인됨[6] == "체결"
    assert 미확인[6] == "값 미확인"
    assert 아직[6] == "기록 전"


def test_회차는_주문이_없어도_한_줄_남는다():
    """빈 화면이 '살 게 없었다'인지 '아예 안 돌았다'인지 여기서만 갈린다."""
    from muwon.cloud.sheet_log import runlog_rows

    (줄,) = runlog_rows([가짜회차()])

    assert 줄[0] == "R3"
    assert 줄[7] == "0", "주문 0건도 줄로 남아야 한다"
    assert "비중 상한 2건" in 줄[8] and "현금 부족" in 줄[8]
    assert "\n" not in 줄[8], "시트 한 칸에 줄바꿈이 있으면 표가 무너진다"


def test_비밀값은_시트에_안_적는다():
    """토큰과 API 키가 같은 표에 있고, 시트는 사람이 열어 보는 곳이다."""
    from muwon.cloud.sheet_log import history_rows

    보통, 비밀 = history_rows([
        가짜변경(id=1, key="stop_loss_pct", old_value="-5", new_value="-7"),
        가짜변경(id=2, key="kis_app_secret", old_value="옛토큰",
                 new_value="새토큰", is_secret=True),
    ])

    assert 보통[3] == "-5" and 보통[4] == "-7"
    assert "옛토큰" not in 비밀 and "새토큰" not in 비밀
    assert 비밀[2] == "kis_app_secret", "무엇이 바뀌었다는 사실은 남는다"


def test_알림은_주문과_매매와_회차에서_만든다():
    """알림 표를 DB에 따로 두지 않는다. 원본이 둘이 되면 어느 쪽이 맞는지
    알 수 없는 날이 온다."""
    from muwon.cloud.sheet_log import notice_rows

    줄들 = notice_rows([가짜주문()], [가짜매매()], [가짜회차()])
    글들 = " ".join(줄[3] for 줄 in 줄들)

    assert "066970 12주를 118,241원에 샀습니다." in 글들
    assert "+20,000원" in 글들
    assert "신호 2건이 났지만 주문은 없었습니다" in 글들


def test_주문이_있던_회차는_알림에_또_적지_않는다():
    """주문 줄이 같은 말을 더 자세히 하고 있다. 두 번 적으면 표가 길어지고,
    길어진 표는 안 읽힌다."""
    from muwon.cloud.sheet_log import notice_rows

    줄들 = notice_rows(runs=[가짜회차(orders=2)])

    assert 줄들 == []


def test_알림은_시간_순으로_선다():
    from muwon.cloud.sheet_log import notice_rows

    줄들 = notice_rows(
        [가짜주문(id=1, created_at=datetime(2026, 8, 26, 15, 0, tzinfo=UTC))],
        [가짜매매(id=1, exited_at=datetime(2026, 8, 24, 9, 5, tzinfo=UTC))],
    )

    assert [줄[1] for 줄 in 줄들] == ["2026-08-24 09:05", "2026-08-26 15:00"]


def test_네_탭도_두_번_돌려도_두_줄이_되지_않는다():
    """열쇠가 겹치면 안 올린다. 이게 이 파일에서 제일 중요한 성질이고,
    새로 만든 탭에도 똑같이 걸려 있어야 한다."""
    from muwon.cloud.sheet_log import history_rows, order_rows, runlog_rows

    for 만들기, 것 in (
        (order_rows, 가짜주문()), (runlog_rows, 가짜회차()), (history_rows, 가짜변경()),
    ):
        줄들 = 만들기([것])
        assert only_new([줄[0] for 줄 in 줄들], 줄들) == []
