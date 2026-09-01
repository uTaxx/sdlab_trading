"""다음에 무엇이 언제 자동으로 도는지.

왜 필요한가. 화면을 봐도 "이게 언제 또 도는지"를 알 수 없었다. 그래서
아무 일도 안 일어난 화면을 보고 고장인지 아직 시간이 안 된 건지 판단할 수
없었다.

**시각을 화면에 손으로 적지 않는다.** 워크플로 파일의 cron을 직접 읽어서
계산한다. 스케줄을 바꿨는데 화면이 옛 시각을 말하면, 그건 안내가 아니라
거짓말이다. 실제로 오늘 cron을 한 번 바꿨다.

cron은 UTC 기준이라 한국시간으로 바꿔서 보여 준다. 09:05 KST를 00:05 UTC로
적어 둔 것을 그대로 화면에 띄우면 아무도 못 읽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

#: 워크플로 파일 → (화면에 쓸 이름, 무엇을 하는지)
WATCHED = {
    "paper-trading.yml": ("자동매매", "종목을 판단하고 주문을 냅니다"),
    "update-universe.yml": ("매매 대상 종목 갱신", "시가총액 상위로 목록을 다시 뽑습니다"),
    "collect-intraday.yml": ("30분봉 수집", "장중 전략 검증에 쓸 시세를 쌓습니다 (주문은 안 냅니다)"),
    "market-report.yml": ("시장·섹터 리포트", "장 상태와 섹터별 전망을 냅니다 (주문은 안 냅니다)"),
}


@dataclass(frozen=True)
class Job:
    이름: str
    설명: str
    cron: str
    다음실행: datetime | None  # KST
    설명문: str

    def 남은시간(self, now: datetime) -> str:
        if self.다음실행 is None:
            return "예정 없음"
        delta = self.다음실행 - now
        total = int(delta.total_seconds())
        if total < 0:
            return "곧"
        days, rest = divmod(total, 86400)
        hours, rest = divmod(rest, 3600)
        minutes = rest // 60
        if days:
            return f"{days}일 {hours}시간 뒤"
        if hours:
            return f"{hours}시간 {minutes}분 뒤"
        return f"{minutes}분 뒤"


def _parse_field(field: str, low: int, high: int) -> set[int]:
    """cron 한 칸을 실제 숫자 집합으로 편다 (`*`, `1-5`, `*/4`, `0,3`)."""
    values: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part in ("*", ""):
            start, end = low, high
        elif "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(part)
        values |= set(range(start, end + 1, step))
    return {v for v in values if low <= v <= high}


def next_fire(cron: str, after: datetime) -> datetime | None:
    """cron(UTC 5칸)의 다음 실행 시각을 한국시간으로 돌려준다.

    분 단위로 최대 40일을 앞으로 훑는다. 주 1회(일요일)까지 잡으려면 최소
    8일이 필요하고, 월 단위 스케줄까지 여유를 뒀다. 라이브러리를 하나 더
    들이는 것보다 이 40줄이 낫다고 봤다. 우리가 쓰는 cron은 다섯 칸의
    기본 문법뿐이다."""
    parts = cron.split()
    if len(parts) != 5:
        return None
    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    days = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    # cron의 요일은 일요일이 0이다. 파이썬 weekday()는 월요일이 0이라
    # 그대로 비교하면 하루씩 밀린다. 실제로 이걸 놓치면 '내일 아침'이
    # '오늘 저녁'으로 나온다.
    weekdays = _parse_field(parts[4], 0, 7)
    if 7 in weekdays:
        weekdays.add(0)

    cursor = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = cursor + timedelta(days=40)
    while cursor < limit:
        if (
            cursor.minute in minutes
            and cursor.hour in hours
            and cursor.month in months
            and cursor.day in days
            and ((cursor.weekday() + 1) % 7) in weekdays
        ):
            return cursor.astimezone(KST)
        cursor += timedelta(minutes=1)
    return None


def describe_cron(cron: str) -> str:
    """cron을 한국시간 기준 사람 말로. '5 0 * * 1-5' → '평일 09:05'."""
    parts = cron.split()
    if len(parts) != 5:
        return cron
    minutes, hours, _day, _month, dow = parts
    if not minutes.isdigit() or not hours.isdigit():
        return f"cron `{cron}` (UTC)"

    # UTC → KST는 +9시간. 자정을 넘기면 요일도 하루 밀린다.
    total = int(hours) * 60 + int(minutes) + 9 * 60
    shift, total = divmod(total, 24 * 60)
    hour, minute = divmod(total, 60)
    시각 = f"{hour:02d}:{minute:02d}"

    if dow == "*":
        return f"매일 {시각}"
    days = sorted(_parse_field(dow, 0, 7) - {7})
    shifted = [(d + shift) % 7 for d in days]
    if shifted == [1, 2, 3, 4, 5]:
        return f"평일 {시각}"
    # cron 요일(일=0)을 화면용 이름(월=0)으로
    이름 = ", ".join(WEEKDAYS[(d + 6) % 7] for d in shifted)
    return f"매주 {이름} {시각}"


def _crons_in(text: str) -> list[str]:
    """워크플로 글에서 **살아 있는** cron만 뽑는다.

    주석 처리된 cron까지 세면, 자동 실행을 꺼 둔 뒤에도 화면은 "내일 09:05에
    돕니다"라고 말한다. 안내가 아니라 거짓말이 된다. 실제로 오늘 자동
    실행을 멈추면서 이걸 잡았다."""
    found = []
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        match = re.search(r'cron:\s*["\']([^"\']+)["\']', code)
        if match:
            found.append(match.group(1))
    return found


def upcoming(now: datetime | None = None, workflow_dir: Path | None = None) -> list[Job]:
    """워크플로 파일에서 cron을 읽어 다음 실행 예정을 만든다."""
    now = now or datetime.now(KST)
    directory = workflow_dir or Path(__file__).resolve().parents[3] / ".github" / "workflows"

    jobs: list[Job] = []
    for filename, (이름, 설명) in WATCHED.items():
        path = directory / filename
        if not path.exists():
            continue
        for cron in _crons_in(path.read_text(encoding="utf-8")):
            jobs.append(
                Job(
                    이름=이름,
                    설명=설명,
                    cron=cron,
                    다음실행=next_fire(cron, now),
                    설명문=describe_cron(cron),
                )
            )
    # 가까운 것부터. 예정을 못 구한 건 뒤로: 화면 맨 위는 '다음에 일어날 일'이어야 한다.
    jobs.sort(key=lambda j: (j.다음실행 is None, j.다음실행 or now))
    return jobs


def automation_state(policy, now: datetime | None = None) -> tuple[str, str, str]:
    """자동매매가 **실제로** 도는 상태인가. (뱃지, 색, 설명)

    두 개가 따로 논다.

    - **스케줄**(워크플로 cron): 아예 실행이 예약돼 있는가
    - **킬스위치**(trading_enabled): 실행되면 매수를 하는가

    스케줄을 꺼 놨는데 킬스위치만 보고 'LIVE'라고 띄우면 화면이 거짓말을
    한다. 실제로 오늘 그렇게 떴다. 둘 중 하나라도 꺼져 있으면 자동매매는
    일어나지 않는다."""
    now = now or datetime.now(KST)
    예약됨 = any(j.이름 == "자동매매" and j.다음실행 for j in upcoming(now))

    if not 예약됨:
        return (
            "자동 실행 꺼짐",
            "orange",
            "자동 실행 일정이 꺼져 있습니다. 손으로 실행하지 않는 한 아무 일도 일어나지 않습니다.",
        )
    if not policy.trading_enabled:
        return (
            "중지됨",
            "orange",
            "일정은 살아 있지만 킬스위치가 꺼져 있어 새로 사지 않습니다 (보유분 손절은 작동).",
        )
    return ("LIVE", "purple", "예정 시각에 자동으로 돌고, 조건이 맞으면 실제로 주문이 나갑니다.")
