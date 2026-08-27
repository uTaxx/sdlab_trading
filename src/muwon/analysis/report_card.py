"""전략 성적표 — 지금까지 잰 것을 한 장에 모아 둔다.

왜 필요한가 — 실험 결과가 세 군데에 흩어져 있다. 숫자는 GitHub Actions
로그(만료된다)와 아티팩트에, 판단은 설계안 문서에, 가설의 채택·기각은 구글
시트에. 그래서 "이 전략 써도 되나"를 물으면 세 곳을 다 뒤져야 하고, 코드를
안 보는 사람은 애초에 접근할 수가 없다.

이 파일은 **재계산하지 않는다.** 실제로 돌린 실험의 결과를 그대로 적어 둔
기록이다. 그래서 항목마다 어느 실행에서 나온 숫자인지(`실행`, `커밋`)를
같이 둔다 — 출처 없는 숫자는 나중에 검증할 수가 없다.

숫자를 새로 재면 `docs/전략평가.json`을 고쳐야 한다. 자동으로 따라가지
않는다는 것이 이 설계의 약점이고, 그래서 `기준.측정일`을 화면에 항상 띄워
"언제 잰 것인지"가 보이게 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "전략평가.json"

#: 판정별 (색, 한 줄 뜻). 화면과 문서가 같은 말을 쓰게 한 군데에 둔다.
VERDICTS = {
    "쓸만함": ("green", "5년 내내 플러스였고 최악의 해도 견딜 만했다"),
    "조건부": ("blue", "평균은 괜찮지만 나쁜 해가 뚜렷하다 — 그 해를 버틸 수 있어야 쓴다"),
    "보류": ("orange", "판단할 만큼의 근거가 아직 없다"),
    "안씀": ("red", "어느 해엔가 크게 잃었다. 지금 기준으로는 쓰지 않는다"),
}


@dataclass(frozen=True)
class Row:
    """전략 하나(또는 조합 하나)의 성적 한 줄."""

    키: str
    이름: str
    계열: str
    평균: float
    최악: float
    샤프: float
    낙폭: float
    손익비: float
    거래: int
    판정: str
    한줄평: str

    @property
    def 최악의해(self) -> str:
        return f"{self.최악:+.1f}%"


@dataclass(frozen=True)
class ReportCard:
    기준: dict
    전략: list[Row]
    조합: list[Row]
    배운것: list[dict]
    # 매수와 매도를 다른 전략으로 걸었을 때. 키는 "사는키>파는키"다.
    # 전략 목록에는 이 이름이 없으므로(등록된 전략이 아니다) 따로 둔다.
    매수매도분리: list[Row] = field(default_factory=list)

    @property
    def 측정일(self) -> str:
        return self.기준.get("측정일", "")

    def 오래됐나(self, today: date | None = None, 일수: int = 30) -> bool:
        """마지막으로 잰 지 오래됐으면 화면이 그렇다고 말해야 한다.

        재계산하지 않는 기록이라, 오래된 숫자를 최신인 척 보여 주는 것이
        이 설계에서 가장 위험한 실패 방식이다."""
        if not self.측정일:
            return True
        try:
            잰날 = date.fromisoformat(self.측정일)
        except ValueError:
            return True
        return ((today or datetime.now(UTC).date()) - 잰날).days > 일수


#: 판정 규칙. 코드에 두는 이유는 손으로 항목마다 매기면 기준이 흔들리기
#: 때문이다 — "이건 평균이 높으니까 봐주자"가 한 번 들어가면 표 전체를
#: 믿을 수 없게 된다. 규칙을 화면에도 그대로 띄운다.
MIN_TRADES = 50
BAD_YEAR_LIMIT = -20.0


def 판정하기(최악: float, 거래: int) -> str:
    """성적을 판정으로 바꾼다.

    **1순위 기준은 평균이 아니라 최악의 해다.** 평균이 아무리 높아도 한 해에
    30% 잃으면 대부분 중간에 그만둔다 — 그러면 평균은 받아 보지도 못한다."""
    if 거래 < MIN_TRADES:
        return "보류"
    if 최악 >= 0:
        return "쓸만함"
    if 최악 > BAD_YEAR_LIMIT:
        return "조건부"
    return "안씀"


def _row(item: dict) -> Row:
    빠진것 = {f for f in Row.__annotations__ if f not in item}
    if 빠진것:
        raise ValueError(f"성적표 항목에 빠진 칸: {sorted(빠진것)} ({item.get('키', '?')})")
    if item["판정"] not in VERDICTS:
        raise ValueError(f"모르는 판정: {item['판정']} — {sorted(VERDICTS)} 중 하나여야 합니다")
    return Row(**{f: item[f] for f in Row.__annotations__})


def load(path: Path | None = None) -> ReportCard:
    """성적표를 읽는다. 형식이 틀리면 조용히 넘어가지 않고 터뜨린다 —
    반쯤 비어 있는 표는 없는 것보다 나쁘다."""
    data = json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return ReportCard(
        기준=data["기준"],
        전략=[_row(x) for x in data.get("전략", [])],
        조합=[_row(x) for x in data.get("조합", [])],
        배운것=list(data.get("배운것", [])),
        매수매도분리=[_row(x) for x in data.get("매수매도분리", [])],
    )


def 판정색(판정: str) -> str:
    return VERDICTS.get(판정, ("orange", ""))[0]


def 판정뜻(판정: str) -> str:
    return VERDICTS.get(판정, ("", "알 수 없는 판정"))[1]
