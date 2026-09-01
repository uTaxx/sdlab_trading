"""유니버스 자동 갱신 로직 검증.

핵심은 두 가지다: (1) 개별 종목 전략에 안 맞는 것(ETF·우선주·스팩)을
확실히 걸러내는가, (2) 갱신이 실패해도 매매가 멈추지 않는가."""

from unittest.mock import MagicMock

from muwon.data.universe import Ticker
from muwon.data.universe_builder import (
    active_universe,
    build_universe,
    build_volume_universe,
    diff_universe,
    is_tradable_stock,
    load_latest_universe,
    save_snapshot,
    to_ticker,
)
from muwon.db.session import make_session_factory


def test_accepts_ordinary_stocks():
    for name in ["삼성전자", "SK하이닉스", "NAVER", "에코프로비엠", "LG에너지솔루션"]:
        assert is_tradable_stock(name) is True, name


def test_rejects_etf_etn_spac_and_preferred_stocks():
    """개별 종목 전략(이동평균·RSI)의 전제가 안 맞거나, 추세 자체가 없는
    상품들: 유니버스에 섞이면 신호가 오염된다."""
    rejected = [
        "KODEX 200",  # ETF
        "TIGER 미국나스닥100",
        "KBSTAR 200",
        "삼성 레버리지 WTI원유 ETN",
        "교보10호스팩",  # 스팩: 합병 전까지 가격이 거의 고정
        "삼성전자우",  # 우선주: 같은 회사 중복 + 거래량 적음
        "현대차2우B",
        "LG화학우",
    ]
    for name in rejected:
        assert is_tradable_stock(name) is False, name


def test_rejects_empty_name():
    assert is_tradable_stock("") is False


def test_to_ticker_assigns_market_specific_yahoo_suffix():
    """백테스트는 야후 티커를 쓰는데 코스피/코스닥 접미사가 다르다.
    틀리면 그 종목만 조용히 시세가 안 잡힌다."""
    assert to_ticker("005930", "삼성전자", "KOSPI").yahoo_symbol == "005930.KS"
    assert to_ticker("247540", "에코프로비엠", "KOSDAQ").yahoo_symbol == "247540.KQ"


def make_client(kospi_rows, kosdaq_rows) -> MagicMock:
    client = MagicMock()

    def fake_ranking(market: str, limit: int):
        return {"kospi": kospi_rows, "kosdaq": kosdaq_rows}[market][:limit]

    client.get_top_market_cap.side_effect = lambda market, limit: fake_ranking(market, limit)
    return client


def test_build_universe_reserves_slots_for_kosdaq():
    """시가총액만으로 줄을 세우면 코스피 대형주가 자리를 전부 차지해
    코스닥이 0종목이 된다(실제 실행에서 상위 30이 전부 코스피였다).
    단타 기회가 많은 코스닥이 통째로 빠지지 않도록 시장별로 자리를 나눈다."""
    client = make_client(
        kospi_rows=[(f"KP{i}", f"코스피{i}", 10_000_000 - i) for i in range(10)],
        kosdaq_rows=[(f"KQ{i}", f"코스닥{i}", 100 - i) for i in range(10)],  # 시총 훨씬 작음
    )

    universe, _metrics = build_universe(client, size=10, kosdaq_ratio=0.3)

    markets = [t.market for t in universe]
    assert markets.count("KOSDAQ") == 3  # 시총이 작아도 자리를 확보한다
    assert markets.count("KOSPI") == 7


def test_build_universe_fills_from_other_market_when_quota_unmet():
    """한쪽 시장에 조건 맞는 종목이 모자라면 다른 쪽에서 채워 요청 개수를
    맞춘다. 코스닥 후보가 적다고 유니버스가 쪼그라들면 안 된다."""
    client = make_client(
        kospi_rows=[(f"KP{i}", f"코스피{i}", 10_000_000 - i) for i in range(10)],
        kosdaq_rows=[("KQ0", "코스닥0", 100)],  # 1종목뿐
    )

    universe, _metrics = build_universe(client, size=10, kosdaq_ratio=0.3)

    assert len(universe) == 10
    assert [t.market for t in universe].count("KOSDAQ") == 1


def test_build_universe_filters_then_respects_size():
    """걸러낼 종목이 섞여 있어도 요청한 개수를 채워야 한다. 그래서 넉넉히
    받아 온 뒤 거르는 순서가 중요하다."""
    client = make_client(
        kospi_rows=[
            ("069500", "KODEX 200", 9_000_000),  # 제외 대상
            ("005935", "삼성전자우", 8_000_000),  # 제외 대상
            ("005930", "삼성전자", 5_000_000),
            ("000660", "SK하이닉스", 1_000_000),
        ],
        kosdaq_rows=[],
    )

    universe, _metrics = build_universe(client, size=2, kosdaq_ratio=0.0)

    assert [t.name for t in universe] == ["삼성전자", "SK하이닉스"]


def test_build_universe_rejects_invalid_ratio():
    client = make_client(kospi_rows=[], kosdaq_rows=[])
    for bad_ratio in (-0.1, 1.5):
        try:
            build_universe(client, size=10, kosdaq_ratio=bad_ratio)
            raise AssertionError("ValueError가 발생해야 한다")
        except ValueError:
            pass


def test_build_universe_deduplicates_symbols_across_markets():
    client = make_client(
        kospi_rows=[("005930", "삼성전자", 5_000_000)],
        kosdaq_rows=[("005930", "삼성전자", 5_000_000)],
    )
    universe, _metrics = build_universe(client, size=5)
    assert len(universe) == 1


def test_snapshot_roundtrip_preserves_rank_order():
    session_factory = make_session_factory("sqlite:///:memory:")
    tickers = [
        to_ticker("005930", "삼성전자", "KOSPI"),
        to_ticker("247540", "에코프로비엠", "KOSDAQ"),
    ]

    save_snapshot(session_factory, tickers, {"005930": 5_000_000, "247540": 2_000_000})
    loaded = load_latest_universe(session_factory)

    assert [t.symbol for t in loaded] == ["005930", "247540"]
    assert loaded[1].yahoo_symbol == "247540.KQ"


def test_load_latest_returns_only_most_recent_snapshot():
    """스냅샷은 덮어쓰지 않고 쌓으므로, 매매에는 가장 최근 것만 써야 한다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    save_snapshot(session_factory, [to_ticker("005930", "삼성전자", "KOSPI")], {})
    save_snapshot(
        session_factory,
        [to_ticker("000660", "SK하이닉스", "KOSPI"), to_ticker("035720", "카카오", "KOSPI")],
        {},
    )

    loaded = load_latest_universe(session_factory)
    assert [t.symbol for t in loaded] == ["000660", "035720"]


def test_active_universe_falls_back_when_no_snapshot():
    """갱신이 한 번도 안 됐거나 실패해도 매매가 멈추면 안 된다."""
    session_factory = make_session_factory("sqlite:///:memory:")
    fallback = [Ticker("005930", "삼성전자", "KOSPI", "005930.KS")]

    assert active_universe(session_factory, fallback) == fallback


def test_active_universe_prefers_snapshot_over_fallback():
    session_factory = make_session_factory("sqlite:///:memory:")
    save_snapshot(session_factory, [to_ticker("000660", "SK하이닉스", "KOSPI")], {})
    fallback = [Ticker("005930", "삼성전자", "KOSPI", "005930.KS")]

    assert [t.symbol for t in active_universe(session_factory, fallback)] == ["000660"]


def test_diff_reports_added_and_removed():
    """성과가 나빠졌을 때 전략 탓인지 종목이 바뀐 탓인지 구분하려면
    무엇이 들고 났는지 보여야 한다."""
    previous = [to_ticker("005930", "삼성전자", "KOSPI"), to_ticker("035720", "카카오", "KOSPI")]
    current = [to_ticker("005930", "삼성전자", "KOSPI"), to_ticker("000660", "SK하이닉스", "KOSPI")]

    added, removed = diff_universe(previous, current)

    assert added == ["SK하이닉스(000660)"]
    assert removed == ["카카오(035720)"]


# ── 거래대금 유니버스 ────────────────────────────────────────────


class FakeVolumeClient:
    """거래대금 순위 API를 흉내낸다."""

    def __init__(self, kospi, kosdaq):
        self._rows = {"kospi": kospi, "kosdaq": kosdaq}
        self.calls = []

    def get_top_volume(self, market, limit, basis="amount", min_price=0):
        self.calls.append((market, limit, basis, min_price))
        return self._rows[market][:limit]


def test_volume_universe_keeps_a_kosdaq_quota():
    """거래대금으로 줄을 세워도 시장별 자리 배분은 그대로 지켜야 한다.

    합쳐서 자르면 한쪽 시장이 0종목이 되는 문제는 기준을 바꿔도 그대로다."""
    client = FakeVolumeClient(
        kospi=[(f"00{i:04d}", f"코스피{i}", 100_000 - i) for i in range(20)],
        kosdaq=[(f"90{i:04d}", f"코스닥{i}", 50_000 - i) for i in range(20)],
    )

    universe, _metrics = build_volume_universe(client, size=10, kosdaq_ratio=0.3)

    assert len(universe) == 10
    assert sum(1 for t in universe if t.market == "KOSDAQ") == 3
    assert client.calls[0][2] == "amount", "기본 기준은 거래대금이어야 한다"
    assert client.calls[0][3] == 1000, "기본 최저가 필터가 걸려야 한다"


def test_volume_universe_filters_etf_and_preferred():
    client = FakeVolumeClient(
        kospi=[
            ("069500", "KODEX 200", 900),
            ("005935", "삼성전자우", 800),
            ("005930", "삼성전자", 700),
        ],
        kosdaq=[("900001", "코스닥종목", 100)],
    )

    universe, _metrics = build_volume_universe(client, size=3, kosdaq_ratio=0.34)

    assert [t.symbol for t in universe] == ["005930", "900001"]


def test_volume_snapshot_does_not_change_what_live_trading_reads(tmp_path):
    """실험용 거래대금 목록을 저장해도 실거래 대상은 그대로여야 한다."""
    from muwon.data.universe_builder import KIND_MARKET_CAP, KIND_VOLUME

    factory = make_session_factory(f"sqlite:///{tmp_path / 'test.db'}")
    core = [to_ticker("005930", "삼성전자", "KOSPI")]
    aggressive = [to_ticker("900001", "어떤코스닥", "KOSDAQ")]

    save_snapshot(factory, core, {"005930": 5_000_000}, kind=KIND_MARKET_CAP)
    save_snapshot(factory, aggressive, {"900001": 9_000}, kind=KIND_VOLUME)

    assert [t.symbol for t in load_latest_universe(factory)] == ["005930"]
    assert [t.symbol for t in load_latest_universe(factory, KIND_VOLUME)] == ["900001"]


def test_legacy_rows_without_kind_still_load_as_market_cap(tmp_path):
    """kind 컬럼이 붙기 전에 저장된 행이 안 보이게 되면 안 된다.
    컬럼 하나 추가한 순간 운영 DB의 유니버스가 통째로 사라진다."""
    from sqlalchemy import update

    from muwon.db.models import UniverseSnapshotRow

    factory = make_session_factory(f"sqlite:///{tmp_path / 'legacy.db'}")
    save_snapshot(factory, [to_ticker("005930", "삼성전자", "KOSPI")], {})
    with factory() as session:  # 옛 행을 흉내내 kind를 비운다
        session.execute(update(UniverseSnapshotRow).values(kind=None))
        session.commit()

    assert [t.symbol for t in load_latest_universe(factory)] == ["005930"]


def test_builder_returns_the_ranking_metric_so_snapshots_can_keep_it():
    """순위 지표를 버리면 스냅샷에 남길 수 없고, 나중에 '왜 이 종목이
    들어왔나'를 되짚을 근거가 사라진다."""
    client = FakeVolumeClient(
        kospi=[("005930", "삼성전자", 987_654)],
        kosdaq=[("196170", "알테오젠", 123_456)],
    )

    universe, metrics = build_volume_universe(client, size=2, kosdaq_ratio=0.5)

    assert metrics == {"005930": 987_654, "196170": 123_456}
    assert set(metrics) == {t.symbol for t in universe}
