"""30분봉을 담는 곳: 운영 DB와 **일부러 분리한다.**

왜 따로 두나. 대시보드는 `muwon.db`를 30초마다 구글드라이브에서 통째로
다시 받는다. 30분봉은 60종목 × 13칸 × 250거래일 = 연 19만 5천 줄로 불어나는데,
그걸 같은 파일에 넣으면 **화면을 볼 때마다 수십 MB를 내려받게 된다.**
매매 기록과 시세 수집은 쓰임도 다르고 커지는 속도도 다르다.

그래서 `intraday.db`를 따로 쓰고, 대시보드는 이 파일을 건드리지 않는다.

## 같은 날을 두 번 받아도 안전하다

(종목, 날짜, 칸)이 기본키다. 다시 받으면 덮어쓴다. 수집이 도중에 끊겨서
다시 실행하는 일이 잦을 텐데, 그때마다 줄이 두 배로 늘면 나중에 쓸 수가 없다.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from muwon.data.intraday import SLOT_ENDS, SlotBar

DEFAULT_PATH = Path("intraday.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intraday_bars (
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    slot        TEXT NOT NULL,
    open        REAL NOT NULL,
    high        REAL NOT NULL,
    low         REAL NOT NULL,
    close       REAL NOT NULL,
    volume      INTEGER NOT NULL,
    bars        INTEGER NOT NULL,
    PRIMARY KEY (symbol, trade_date, slot)
);
CREATE INDEX IF NOT EXISTS idx_intraday_date ON intraday_bars (trade_date);
"""


@dataclass(frozen=True)
class Coverage:
    """하루치가 얼마나 채워졌나."""

    trade_date: date
    종목수: int
    칸수: int
    빈칸: dict[str, list[str]]  # 종목 -> 빠진 칸들

    @property
    def 기대칸수(self) -> int:
        return self.종목수 * len(SLOT_ENDS)

    @property
    def 채움률(self) -> float:
        return self.칸수 / self.기대칸수 * 100 if self.기대칸수 else 0.0


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def save(bars: list[SlotBar], path: Path = DEFAULT_PATH) -> int:
    """받아 온 칸을 넣는다. 같은 칸이 이미 있으면 덮어쓴다."""
    if not bars:
        return 0
    with closing(_connect(path)) as conn, conn:
        conn.executemany(
            "INSERT OR REPLACE INTO intraday_bars "
            "(symbol, trade_date, slot, open, high, low, close, volume, bars) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (b.symbol, b.trade_date.isoformat(), b.slot, b.open, b.high, b.low, b.close, b.volume, b.bars)
                for b in bars
            ],
        )
    return len(bars)


def coverage(trade_date: date, symbols: list[str], path: Path = DEFAULT_PATH) -> Coverage:
    """그날 무엇이 빠졌는지.

    **이 API는 당일치만 준다.** 오늘 못 받은 칸은 내일 받을 수 없다.
    영영 없는 것이다. 그래서 얼마나 찼는지를 매번 소리 내어 말해야 한다.
    조용히 넘어가면 몇 달 뒤에 구멍투성이 데이터를 발견하게 된다."""
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT symbol, slot FROM intraday_bars WHERE trade_date = ?",
            (trade_date.isoformat(),),
        ).fetchall()

    있는것: dict[str, set[str]] = defaultdict(set)
    for symbol, slot in rows:
        있는것[symbol].add(slot)

    빈칸 = {}
    for symbol in symbols:
        빠진것 = [s for s in SLOT_ENDS if s not in 있는것.get(symbol, set())]
        if 빠진것:
            빈칸[symbol] = 빠진것
    return Coverage(trade_date=trade_date, 종목수=len(symbols), 칸수=len(rows), 빈칸=빈칸)


def format_coverage(cov: Coverage) -> str:
    lines = [
        f"■ {cov.trade_date} 수집 결과",
        (f"  {cov.종목수}종목 × {len(SLOT_ENDS)}칸 = {cov.기대칸수}칸 중 "
        f"**{cov.칸수}칸** ({cov.채움률:.1f}%)"),
    ]
    if not cov.빈칸:
        lines.append("  빠진 칸 없음.")
        return "\n".join(lines)

    lines += [
        "",
        f"  빠진 종목 {len(cov.빈칸)}개: **오늘 못 받은 칸은 내일 받을 수 없습니다.**",
        "  (마지막 칸 1530은 종가 단일가 구간이라 비는 게 정상일 수 있습니다)",
    ]
    for symbol, 빠진것 in sorted(cov.빈칸.items())[:20]:
        lines.append(f"    {symbol}: {', '.join(빠진것)}")
    if len(cov.빈칸) > 20:
        lines.append(f"    … 외 {len(cov.빈칸) - 20}종목")
    return "\n".join(lines)


def stored_days(path: Path = DEFAULT_PATH) -> list[date]:
    """지금까지 며칠치가 쌓였나."""
    with closing(_connect(path)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM intraday_bars ORDER BY trade_date"
        ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]
