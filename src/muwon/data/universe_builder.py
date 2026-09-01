"""시가총액 상위 종목으로 매매 대상 목록(유니버스)을 다시 뽑는다.

data/universe.py의 UNIVERSE는 사람이 골라 고정해 둔 18종목이라 시간이
지나면 낡는다. 상장폐지되거나, 순위가 뒤집히거나, 새로 커진 종목이
빠져 있게 된다. 여기서는 KIS 시가총액 순위 API로 현재 상위 종목을 받아
매매에 부적절한 것들을 걸러낸 유니버스를 만든다.

덮어쓰지 않고 스냅샷으로 쌓는 이유: 어느 날 성과가 나빠졌을 때 그게 전략
탓인지 종목이 바뀐 탓인지 구분하려면, 그날 무엇을 대상으로 삼았는지가
남아 있어야 한다.
"""

from __future__ import annotations

import re
from datetime import datetime

from loguru import logger
from sqlalchemy import or_, select

from muwon.data.universe import Ticker
from muwon.db.models import UniverseSnapshotRow

# ETF·ETN은 주식이 아니라 바스켓 상품이라 개별 종목 전략(이동평균·RSI 등)의
# 전제가 맞지 않고, 스팩은 합병 전까지 가격이 거의 고정이라 추세가 없다.
# 시총 순위 API가 이들을 섞어 줄 수 있어 이름으로 한 번 더 걸러낸다.
_ETF_BRAND_PATTERN = re.compile(
    r"(KODEX|TIGER|KBSTAR|ARIRANG|HANARO|KOSEF|SOL |ACE |PLUS |RISE |TIMEFOLIO|"
    r"마이다스|파워|마이티|WOORI|히어로즈|BNK|VITA|UNICORN|에셋플러스)"
)
_ETN_PATTERN = re.compile(r"(ETN|선물|레버리지|인버스|\dX)")
_SPAC_PATTERN = re.compile(r"스팩")
# 우선주는 보통 이름이 "…우", "…우B", "…3우B"로 끝난다. API에서 보통주만
# 요청하지만(fid_div_cls_code=1), 응답이 섞여 오는 경우를 대비한 이중 방어다.
_PREFERRED_PATTERN = re.compile(r"(\d?우[B]?)$")


def is_tradable_stock(name: str) -> bool:
    """개별 종목 전략의 대상으로 적절한 보통주인지."""
    if not name:
        return False
    return not (
        _ETF_BRAND_PATTERN.search(name)
        or _ETN_PATTERN.search(name)
        or _SPAC_PATTERN.search(name)
        or _PREFERRED_PATTERN.search(name)
    )


def to_ticker(symbol: str, name: str, market: str) -> Ticker:
    """백테스트용 야후 티커까지 붙인 Ticker를 만든다 (코스피 .KS / 코스닥 .KQ)."""
    suffix = ".KQ" if market == "KOSDAQ" else ".KS"
    return Ticker(symbol=symbol, name=name, market=market, yahoo_symbol=f"{symbol}{suffix}")


# 코스닥에 할당할 비율. 시가총액만으로 줄을 세우면 코스닥은 한 종목도 못
# 남는다. 실제로 상위 30을 뽑았더니 전부 코스피였고, 기존 유니버스에 있던
# 에코프로비엠·에코프로가 빠졌다. 단타는 변동성이 큰 코스닥에서 기회가
# 나오는 경우가 많아 통째로 빠지면 전략 자체가 좁아지므로, 시장별로 자리를
# 따로 할당한다.
DEFAULT_KOSDAQ_RATIO = 0.3


def build_universe(
    client, size: int = 30, kosdaq_ratio: float = DEFAULT_KOSDAQ_RATIO
) -> tuple[list[Ticker], dict[str, int]]:
    """코스피·코스닥 시총 상위에서 매매 대상 종목을 골라 온다.

    (종목 목록, 종목코드→시가총액)을 함께 돌려준다. 순위 지표를 버리면
    스냅샷에 남길 수가 없고, 나중에 '왜 이 종목이 들어왔나'를 되짚을 근거가
    사라진다."""
    return _build_by_ranking(
        lambda market_key, limit: client.get_top_market_cap(market=market_key, limit=limit),
        "시총 상위",
        size,
        kosdaq_ratio,
    )


def build_volume_universe(
    client,
    size: int = 30,
    kosdaq_ratio: float = DEFAULT_KOSDAQ_RATIO,
    basis: str = "amount",
    min_price: int = 1000,
) -> tuple[list[Ticker], dict[str, int]]:
    """거래가 몰린 상위 종목으로 별도 유니버스를 만든다.

    시총 상위와 다른 종목군이 목적이다. 단기 전략(눌림목·거래량 급증)은
    하루 1~2% 움직이는 대형주에서는 전제가 성립하지 않는다. 그 전략들을
    시총 상위에서 시험하는 건 틀린 운동장에서 재는 것이다.

    min_price 기본 1000원. 저가주는 호가 단위가 가격 대비 커서(100원짜리의
    1호가는 1%다) 백테스트의 종가 체결 가정이 실제와 크게 벌어진다."""
    return _build_by_ranking(
        lambda market_key, limit: client.get_top_volume(
            market=market_key, limit=limit, basis=basis, min_price=min_price
        ),
        f"거래 상위({basis})",
        size,
        kosdaq_ratio,
    )


def _build_by_ranking(
    fetch, label: str, size: int, kosdaq_ratio: float
) -> tuple[list[Ticker], dict[str, int]]:
    """순위 API 하나를 받아 시장별 할당·필터·부족분 보충까지 처리한다.

    두 시장을 하나로 합쳐 순위대로 자르지 않고 **시장별로 자리를 나눠 각각
    상위를 뽑는다**. 합쳐서 자르면 코스피 대형주가 자리를 전부 차지해
    코스닥이 0종목이 되기 때문이다(실측으로 확인).

    kosdaq_ratio=0.3, size=30이면 코스피 21 + 코스닥 9종목이 된다.
    한쪽 시장에서 조건에 맞는 종목이 모자라면 다른 쪽에서 채운다.

    시총 기준과 거래 기준이 이 로직을 공유한다. 같은 규칙을 두 벌 두면
    한쪽만 고쳐져 갈라진다."""
    if not 0.0 <= kosdaq_ratio <= 1.0:
        raise ValueError(f"kosdaq_ratio는 0~1 사이여야 합니다: {kosdaq_ratio}")

    quotas = {
        "KOSDAQ": round(size * kosdaq_ratio),
        "KOSPI": size - round(size * kosdaq_ratio),
    }

    picked: dict[str, list[Ticker]] = {}
    leftovers: list[tuple[str, str, str, int]] = []  # 할당량을 넘어 남은 후보
    metrics: dict[str, int] = {}  # 종목코드 → 순위 지표(시총 또는 거래대금)

    for market_key, market_name in (("kospi", "KOSPI"), ("kosdaq", "KOSDAQ")):
        # 걸러낼 종목(ETF·우선주 등)을 감안해 넉넉히 받아 온다
        rows = fetch(market_key, size * 2)
        logger.info(f"{market_name} {label} {len(rows)}종목 수신")

        tradable = [(s, n, market_name, v) for s, n, v in rows if is_tradable_stock(n)]
        metrics.update({s: v for s, _n, _m, v in tradable})
        quota = quotas[market_name]
        picked[market_name] = [to_ticker(s, n, m) for s, n, m, _c in tradable[:quota]]
        leftovers.extend(tradable[quota:])

    universe: list[Ticker] = []
    seen: set[str] = set()
    for market_name in ("KOSPI", "KOSDAQ"):
        for ticker in picked.get(market_name, []):
            if ticker.symbol not in seen:
                seen.add(ticker.symbol)
                universe.append(ticker)

    # 한쪽 시장이 할당량을 못 채웠으면 남은 후보 중 순위 지표가 큰 순으로 채운다
    if len(universe) < size:
        leftovers.sort(key=lambda row: row[3], reverse=True)
        for symbol, name, market, _metric in leftovers:
            if len(universe) >= size:
                break
            if symbol not in seen:
                seen.add(symbol)
                universe.append(to_ticker(symbol, name, market))

    universe = universe[:size]
    return universe, {t.symbol: metrics.get(t.symbol, 0) for t in universe}


KIND_MARKET_CAP = "market_cap"
KIND_VOLUME = "volume"


def _kind_filter(kind: str):
    """kind가 붙기 전에 저장된 행은 kind가 NULL이다. 그건 전부 시총 기준이었으니
    market_cap을 물을 때 함께 잡아 준다. 안 그러면 컬럼 하나 추가한 순간
    운영 DB의 기존 유니버스가 통째로 안 보이게 된다."""
    if kind == KIND_MARKET_CAP:
        return or_(UniverseSnapshotRow.kind == kind, UniverseSnapshotRow.kind.is_(None))
    return UniverseSnapshotRow.kind == kind


def save_snapshot(
    session_factory,
    tickers: list[Ticker],
    metrics: dict[str, int],
    kind: str = KIND_MARKET_CAP,
) -> datetime:
    """유니버스 스냅샷을 저장하고 그 시각을 돌려준다.

    metrics는 kind에 따라 뜻이 다르다. 시총 기준이면 시가총액(억원),
    거래 기준이면 누적거래대금(백만원). 컬럼을 나눠 담아 나중에 표를 읽는
    사람이 어느 쪽 숫자인지 헷갈리지 않게 한다."""
    snapshot_at = datetime.utcnow()  # noqa: DTZ003 (기록용, tz 무관)
    with session_factory() as session:
        for rank, ticker in enumerate(tickers, start=1):
            value = metrics.get(ticker.symbol, 0)
            session.add(
                UniverseSnapshotRow(
                    snapshot_at=snapshot_at,
                    symbol=ticker.symbol,
                    name=ticker.name,
                    market=ticker.market,
                    market_cap=value if kind == KIND_MARKET_CAP else 0,
                    turnover=value if kind == KIND_VOLUME else 0,
                    rank=rank,
                    kind=kind,
                )
            )
        session.commit()
    return snapshot_at


def load_latest_universe(session_factory, kind: str = KIND_MARKET_CAP) -> list[Ticker]:
    """가장 최근 스냅샷의 유니버스를 돌려준다. 스냅샷이 없으면 빈 목록."""
    with session_factory() as session:
        latest_at = session.scalar(
            select(UniverseSnapshotRow.snapshot_at)
            .where(_kind_filter(kind))
            .order_by(UniverseSnapshotRow.snapshot_at.desc())
        )
        if latest_at is None:
            return []
        rows = session.scalars(
            select(UniverseSnapshotRow)
            .where(UniverseSnapshotRow.snapshot_at == latest_at, _kind_filter(kind))
            .order_by(UniverseSnapshotRow.rank)
        ).all()
        return [to_ticker(r.symbol, r.name, r.market) for r in rows]


def active_universe(
    session_factory, fallback: list[Ticker], kind: str = KIND_MARKET_CAP
) -> list[Ticker]:
    """실제 매매에 쓸 유니버스: 스냅샷이 있으면 그걸, 없으면 fallback을 쓴다.

    kind 기본값이 market_cap인 것이 중요하다. 실험용으로 거래 기준 목록을
    저장해도 실거래가 그걸 집어 가면 안 된다. 실계좌의 매매 대상이 실험
    한 번에 바뀌는 일은 없어야 한다.

    갱신이 한 번도 안 됐거나 실패한 상태에서 매매가 멈추면 안 되므로,
    손으로 고른 기존 목록을 안전망으로 남겨 둔다."""
    latest = load_latest_universe(session_factory, kind)
    if latest:
        return latest
    logger.info("저장된 유니버스 스냅샷이 없어 기본 목록을 사용합니다.")
    return fallback


def diff_universe(previous: list[Ticker], current: list[Ticker]) -> tuple[list[str], list[str]]:
    """이전/현재 유니버스의 차이를 (편입, 제외) 종목명 목록으로 돌려준다."""
    prev_symbols = {t.symbol: t.name for t in previous}
    cur_symbols = {t.symbol: t.name for t in current}
    added = [f"{name}({sym})" for sym, name in cur_symbols.items() if sym not in prev_symbols]
    removed = [f"{name}({sym})" for sym, name in prev_symbols.items() if sym not in cur_symbols]
    return added, removed
