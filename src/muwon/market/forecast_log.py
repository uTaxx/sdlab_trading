"""낸 전망과 실제로 일어난 일을 짝지어 쌓는다.

**이 시스템에서 제일 중요한 기록이다.**

전망을 내놓고 결과를 안 남기면, **전망이 쓸모없다는 것도 영영 모른다.**
그러면 근거 없는 숫자를 근거인 줄 알고 돈을 걸게 된다.

## 어떻게 재나

매일 전망을 한 줄씩 쌓는다. 그리고 20거래일이 지나면 그 줄에
**실제로 무슨 일이 있었는지**를 채워 넣는다.

한 달쯤 지나면 이 물음에 답할 수 있다.

> **이 전망이 동전 던지기보다 나은가?**

나으면 계속 쓰고, 아니면 버린다. 그게 이 파일의 존재 이유다.

## 무엇으로 판정하나. 두 가지를 본다

**① 방향을 맞혔나 (적중률)**
전망이 "오를 확률 70%"라고 한 날들 중 실제로 오른 날의 비율. 그런데 이것만
보면 안 된다. 원래 오르는 게 흔한 대상이면 아무 말이나 해도 잘 맞는다.
그래서 **기준선(아무 조건 없이 아무 날에나)** 과 나란히 본다.

**② 하위 10%가 실제 하락을 감쌌나 (커버리지)**
이게 더 중요하다. 우리는 이 칸을 보고 **비중을 정할** 참이기 때문이다.
"아주 나빴을 때 -8%"라고 했으면, 실제 결과의 약 10%만 -8%보다 나빠야 한다.
**실제로 30%가 그 아래로 떨어졌다면 그 전망은 위험을 심하게 낮잡은 것이고,
그걸 믿고 비중을 키우면 다친다.**

## 판정을 서두르지 않는다

30건으로 "적중률 60%"를 말할 수 없다. 최소 표본을 못 채우면 숫자 대신
**"아직 판단할 수 없음. 12건뿐"** 이라고 쓴다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

DEFAULT_PATH = Path("forecasts.db")

#: 이만큼 안 쌓이면 적중을 판정하지 않는다.
MIN_SCORED = 30
#: 하위10% 칸이 감싸야 할 비율. 실제로 이보다 훨씬 많이 뚫리면 위험을 낮잡은 것이다.
TAIL_TARGET = 10.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
    기준일        TEXT NOT NULL,
    대상          TEXT NOT NULL,
    지평          INTEGER NOT NULL,
    구간수        INTEGER NOT NULL,
    총일수        INTEGER NOT NULL,
    중앙값        REAL,
    하위10        REAL,
    상승확률      REAL,
    기준선_중앙값  REAL,
    기준선_상승확률 REAL,
    우연폭        REAL,
    렌즈          TEXT NOT NULL DEFAULT '',
    실제수익      REAL,
    실제일        TEXT,
    PRIMARY KEY (기준일, 대상, 지평)
);
"""


@dataclass(frozen=True)
class LogRow:
    기준일: str
    대상: str
    지평: int
    구간수: int
    총일수: int
    중앙값: float | None
    하위10: float | None
    상승확률: float | None
    기준선_중앙값: float | None
    기준선_상승확률: float | None
    우연폭: float | None
    렌즈: str = ""
    실제수익: float | None = None
    실제일: str | None = None

    @property
    def 채워졌나(self) -> bool:
        return self.실제수익 is not None

    @property
    def 방향맞췄나(self) -> bool | None:
        """전망이 '오른다' 쪽이었고 실제로 올랐나 (또는 그 반대)."""
        if not self.채워졌나 or self.상승확률 is None:
            return None
        올랐다고봄 = self.상승확률 >= 50
        실제로올랐다 = self.실제수익 > 0
        return 올랐다고봄 == 실제로올랐다

    @property
    def 꼬리뚫렸나(self) -> bool | None:
        """실제 결과가 '아주 나빴을 때' 칸보다 더 나빴나."""
        if not self.채워졌나 or self.하위10 is None:
            return None
        return self.실제수익 < self.하위10


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def row_from_forecast(f, 렌즈: str = "") -> LogRow:
    """Forecast → 기록 한 줄. 숫자를 못 낸 전망도 남긴다.
    **못 낸 날이 얼마나 많았나**도 판단에 필요하다."""
    기준선 = getattr(f, "기준선", None)
    return LogRow(
        기준일=str(f.기준일),
        대상=f.대상,
        지평=f.지평,
        구간수=f.구간수,
        총일수=f.총일수,
        중앙값=f.중앙값,
        하위10=f.하위10,
        상승확률=f.상승확률,
        기준선_중앙값=기준선.중앙값 if 기준선 else None,
        기준선_상승확률=기준선.상승확률 if 기준선 else None,
        우연폭=f.우연폭,
        렌즈=렌즈,
    )


def save(rows: list[LogRow], path: Path = DEFAULT_PATH) -> int:
    """같은 (기준일, 대상, 지평)이 이미 있으면 덮어쓴다.

    다만 **이미 채워 넣은 실제 결과는 지우지 않는다**. 다시 실행했다고
    답이 사라지면 적중 기록이 통째로 날아간다."""
    if not rows:
        return 0
    이름 = [f.name for f in fields(LogRow)]
    with closing(_connect(path)) as conn, conn:
        for r in rows:
            기존 = conn.execute(
                "SELECT 실제수익, 실제일 FROM forecasts WHERE 기준일=? AND 대상=? AND 지평=?",
                (r.기준일, r.대상, r.지평),
            ).fetchone()
            값 = asdict(r)
            if 기존 and 기존[0] is not None:
                값["실제수익"], 값["실제일"] = 기존[0], 기존[1]
            conn.execute(
                f"INSERT OR REPLACE INTO forecasts ({', '.join(이름)}) "
                f"VALUES ({', '.join('?' * len(이름))})",
                [값[n] for n in 이름],
            )
    return len(rows)


def load(path: Path = DEFAULT_PATH, 대상: str | None = None) -> list[LogRow]:
    with closing(_connect(path)) as conn:
        이름 = [f.name for f in fields(LogRow)]
        조건 = " WHERE 대상 = ?" if 대상 else ""
        줄들 = conn.execute(
            f"SELECT {', '.join(이름)} FROM forecasts{조건} ORDER BY 기준일, 대상",
            (대상,) if 대상 else (),
        ).fetchall()
    return [LogRow(**dict(zip(이름, 줄, strict=True))) for 줄 in 줄들]


def fill_actuals(price_lookup, path: Path = DEFAULT_PATH, today: date | None = None) -> int:
    """지평이 지난 줄에 실제 결과를 채운다.

    price_lookup(대상, 기준일, 지평) → 수익률(%) 또는 None.
    아직 안 지났으면 None을 주면 된다."""
    채운수 = 0
    with closing(_connect(path)) as conn, conn:
        줄들 = conn.execute(
            "SELECT 기준일, 대상, 지평 FROM forecasts WHERE 실제수익 IS NULL"
        ).fetchall()
        for 기준일, 대상, 지평 in 줄들:
            수익 = price_lookup(대상, date.fromisoformat(기준일), 지평)
            if 수익 is None:
                continue
            conn.execute(
                "UPDATE forecasts SET 실제수익=?, 실제일=? WHERE 기준일=? AND 대상=? AND 지평=?",
                (float(수익), str(today or date.today()), 기준일, 대상, 지평),  # noqa: DTZ011
            )
            채운수 += 1
    return 채운수


@dataclass(frozen=True)
class Scorecard:
    """전망이 쓸모 있었나."""

    대상: str
    채워진수: int
    적중률: float | None
    기준선적중률: float | None
    꼬리뚫린비율: float | None
    사유: str = ""

    @property
    def 판정할수있나(self) -> bool:
        return self.적중률 is not None

    @property
    def 더나은가(self) -> bool | None:
        if self.적중률 is None or self.기준선적중률 is None:
            return None
        return self.적중률 > self.기준선적중률

    @property
    def 위험을낮잡았나(self) -> bool | None:
        """하위 10% 칸이 실제 하락을 못 감쌌나. **이게 다치는 쪽 실패다.**"""
        if self.꼬리뚫린비율 is None:
            return None
        return self.꼬리뚫린비율 > TAIL_TARGET * 2


def score(rows: list[LogRow], 대상: str = "전체", min_scored: int = MIN_SCORED) -> Scorecard:
    채워진것 = [r for r in rows if r.채워졌나 and r.상승확률 is not None]
    if len(채워진것) < min_scored:
        return Scorecard(
            대상, len(채워진것), None, None, None,
            f"아직 판단할 수 없습니다. {len(채워진것)}건뿐 (최소 {min_scored}건)",
        )

    맞춘것 = [r for r in 채워진것 if r.방향맞췄나]
    # 기준선도 같은 방식으로 채점한다. 기준선이 '오른다'고 본 날 중 맞은 비율.
    기준선맞춘것 = [
        r for r in 채워진것
        if r.기준선_상승확률 is not None
        and (r.기준선_상승확률 >= 50) == (r.실제수익 > 0)
    ]
    꼬리있는것 = [r for r in 채워진것 if r.하위10 is not None]
    뚫린것 = [r for r in 꼬리있는것 if r.꼬리뚫렸나]

    return Scorecard(
        대상=대상,
        채워진수=len(채워진것),
        적중률=len(맞춘것) / len(채워진것) * 100,
        기준선적중률=len(기준선맞춘것) / len(채워진것) * 100 if 기준선맞춘것 or 채워진것 else None,
        꼬리뚫린비율=len(뚫린것) / len(꼬리있는것) * 100 if 꼬리있는것 else None,
    )


#: 하위10% 예측값을 나눌 구간(%).
꼬리구간 = [(-999.0, -15.0), (-15.0, -10.0), (-10.0, -6.0), (-6.0, -3.0), (-3.0, 999.0)]


def calibration(rows: list[LogRow], min_per_bucket: int = 20) -> str:
    """**'아주 나빴을 때'를 크게 잡은 날이 실제로 더 나빴나.**

    방향을 못 맞혀도 이건 맞힐 수 있다. 그리고 우리가 실제로 필요한 것은
    이쪽이다. 설계상 이 칸을 보고 **비중**을 정하기 때문이다.

    "하위10%를 -20%로 잡은 날"과 "-3%로 잡은 날"의 실제 결과가 똑같다면,
    그 칸은 아무것도 구별하지 못하는 것이고 비중 조절의 근거가 될 수 없다."""
    채워진것 = [r for r in rows if r.채워졌나 and r.하위10 is not None]
    if not 채워진것:
        return "■ 위험 크기를 구별했나\n\n  결과가 나온 전망이 없습니다."

    lines = [
        "■ 위험 크기를 구별했나",
        "",
        "  '아주 나빴을 때'를 크게 잡은 날이 실제로 더 나빴는가.",
        "  방향을 못 맞혀도 이건 맞힐 수 있고, **비중을 정하는 데 필요한 건 이쪽**이다.",
        "",
        f"  {'예측한 하위10%':<16}{'건수':>6}{'실제 중앙':>11}{'실제 하위10':>12}{'뚫린 비율':>10}",
    ]
    쓸만한칸 = 0
    칸별중앙: list[float] = []
    칸별뚫림: list[float] = []
    for 하한, 상한 in 꼬리구간:
        묶음 = [r for r in 채워진것 if 하한 <= r.하위10 < 상한]
        if len(묶음) < min_per_bucket:
            continue
        수익들 = sorted(r.실제수익 for r in 묶음)
        중앙 = 수익들[len(수익들) // 2]
        하위10 = 수익들[max(int(len(수익들) * 0.1) - 1, 0)]
        뚫린것 = sum(1 for r in 묶음 if r.꼬리뚫렸나) / len(묶음) * 100
        이름 = f"{하한:g}~{상한:g}%" if 하한 > -900 else f"{상한:g}% 아래"
        if 상한 > 900:
            이름 = f"{하한:g}% 위"
        lines.append(f"  {이름:<16}{len(묶음):>6}{중앙:>+10.1f}%{하위10:>+11.1f}%{뚫린것:>9.0f}%")
        쓸만한칸 += 1
        칸별중앙.append(중앙)
        칸별뚫림.append(뚫린것)

    if 쓸만한칸 < 2:
        lines.append("  (구간마다 표본이 모자라 비교할 수 없습니다)")
        return "\n".join(lines)

    lines += [
        "",
        (
            "  읽는 법: 위험을 크게 잡은 날(맨 위)이 실제로 더 나빠야 이 칸이 뜻을 갖습니다."
            " 그리고 '뚫린 비율'은 어느 칸에서나 10% 근처여야 합니다."
        ),
    ]

    # 뒤죽박죽인 것과 **거꾸로** 인 것은 전혀 다른 이야기다. 뒤죽박죽이면
    # 정보가 없는 것이고, 거꾸로면 정보는 있는데 부호가 반대인 것이다.
    위험크게본날 = 칸별중앙[0]
    안전하다고본날 = 칸별중앙[-1]
    차이 = 위험크게본날 - 안전하다고본날
    if 차이 > 1.0:
        lines += [
            "",
            f"  → **거꾸로입니다.** 위험을 크게 잡은 날이 오히려 {차이:+.1f}%p 더 좋았습니다.",
            "     이 칸을 보고 비중을 줄이면, **줄여야 할 때가 아니라 늘려야 할 때 줄입니다.**",
            "     설계대로 썼다면 손해였습니다.",
        ]
    elif 차이 < -1.0:
        lines.append("  → 순서가 지켜졌습니다. 위험 크기를 구별하고 있습니다.")
    else:
        lines.append("  → 칸마다 거의 같습니다. 이 칸은 위험 크기를 구별하지 못합니다.")

    if 칸별뚫림 and max(칸별뚫림) - min(칸별뚫림) > 15:
        lines += [
            "",
            (
                f"  뚫린 비율이 {min(칸별뚫림):.0f}%에서 {max(칸별뚫림):.0f}%까지 벌어집니다. "
                "10% 근처로 고르게 나와야 하는 값입니다."
            ),
            "  위험을 작게 잡은 날일수록 더 자주 뚫렸습니다. 안전하다고 본 날이 실제로 덜 안전했습니다.",
        ]
    return "\n".join(lines)


def format_scorecard(s: Scorecard) -> str:
    머리 = f"■ 전망이 쓸모 있었나. {s.대상}"
    if not s.판정할수있나:
        return f"{머리}\n\n  {s.사유}\n  (전망은 계속 쌓이고 있습니다. 결과가 나오는 데 지평만큼 걸립니다)"

    lines = [
        머리,
        "",
        f"  결과가 나온 전망 {s.채워진수}건",
        "",
        f"  방향 적중률      {s.적중률:>5.1f}%",
        f"  그냥 찍었다면    {s.기준선적중률:>5.1f}%",
        f"  **더 나은가**    {'예' if s.더나은가 else '아니오. 이 전망은 아무것도 안 알려 주고 있습니다'}",
    ]
    if s.꼬리뚫린비율 is not None:
        lines += [
            "",
            f"  '아주 나빴을 때' 칸보다 더 나빴던 비율  {s.꼬리뚫린비율:>5.1f}%  (목표 {TAIL_TARGET:.0f}%)",
        ]
        if s.위험을낮잡았나:
            lines.append(
                "  ⚠ **위험을 심하게 낮잡고 있습니다.** 이 칸을 보고 비중을 키우면 다칩니다."
            )
        else:
            lines.append("  하위 10% 칸이 실제 하락을 대체로 감쌌습니다.")
        # 이 한 줄이 실제로 거짓말을 했다. 전체로는 14.9%라 괜찮아 보였는데
        # 구간을 나눠 보니 6%에서 33%까지 벌어져 있었다. 평균이 문제를
        # 가린 것이다. 그래서 여기서 다음 표를 반드시 보라고 말한다.
        lines.append(
            "  ⚠ 이 한 줄은 **평균이라 문제를 가릴 수 있습니다**. 아래 "
            "'위험 크기를 구별했나' 표를 같이 보세요."
        )
    lines += [
        "",
        "  적중률이 '그냥 찍었다면'보다 낮으면 **이 전망은 버립니다.**",
        "  숫자가 있다는 것과 쓸모가 있다는 것은 다릅니다.",
    ]
    return "\n".join(lines)
