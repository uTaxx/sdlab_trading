"""실시간 매매: 무엇을 검증 중이고 무엇이 막혀 있나.

전략 전략 평가 결과(`report_card.py`)가 **이미 잰 것**을 담는다면, 이 파일은
**아직 못 잰 것**을 담는다. 실시간(장중) 매매는 지금 성적이 하나도 없는
상태라, 화면에 전략 평가 결과를 띄우면 빈 표가 나오고 "고장인가"로 읽힌다.

그래서 대신 이걸 띄운다.

1. **지금 단계**. 조사 중인가, 검증 중인가, 도는 중인가
2. **막고 있는 것**. 왜 아직 성적이 없나
3. **후보**. 무엇을 재 볼 것이며 근거가 얼마나 단단한가
4. **끝난 검증**. 실제로 잰 것과 그래서 내린 판단

`docs/실시간계획.json`에 적어 두고 여기서 읽는다. 전략 평가 결과와 같은 이유로
**재계산하지 않는다**. 조사 결과는 실험이 아니라 읽은 것이기 때문이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "실시간계획.json"

#: 근거 등급별 (색, 뜻). "다들 그렇게 한다"와 "학술지에 실렸다"를 같은
#: 칸에 두면 표 전체가 쓸모없어진다.
GRADES = {
    "A": ("green", "동료심사 논문: 다른 연구자들이 방법론을 검토했다"),
    "B": ("blue", "워킹페이퍼: 논문 형식이지만 심사를 안 거쳤다"),
    "C": ("orange", "독립 재현: 제3자가 코드로 다시 실행해 봤다"),
    "D": ("red", "업계 관행·블로그: 널리 쓰이지만 공개된 검증이 없다"),
}

#: 단계별 (색, 뜻). 지금 어디쯤인지가 화면 맨 위에 있어야 한다.
STAGES = {
    "조사": ("blue", "무엇을 재 볼지 고르는 중입니다. 아직 아무것도 매매하지 않습니다"),
    "검증": ("orange", "후보를 실제로 재는 중입니다. 아직 실거래에 쓰지 않습니다"),
    "운영": ("green", "검증을 통과해 실제로 돌고 있습니다"),
}


@dataclass(frozen=True)
class Candidate:
    """재 볼 후보 하나."""

    키: str
    이름: str
    한줄: str  # 무엇을 주장하는가
    등급: str
    한국증거: str
    데이터: str  # 검증하려면 무엇이 필요한가
    지금가능: bool  # 새 데이터 없이 지금 잴 수 있나
    비용민감도: str
    한줄평: str
    출처: str

    @property
    def 등급뜻(self) -> str:
        return GRADES[self.등급][1]


@dataclass(frozen=True)
class Finding:
    """실제로 재고 나서 내린 판단 하나."""

    제목: str
    잰것: str
    결과: str
    판단: str
    측정일: str


@dataclass(frozen=True)
class RealtimePlan:
    단계: str
    한줄: str
    막는것: list[dict]
    후보: list[Candidate]
    검증: list[Finding]

    @property
    def 단계색(self) -> str:
        return STAGES[self.단계][0]

    @property
    def 단계뜻(self) -> str:
        return STAGES[self.단계][1]

    @property
    def 지금가능한후보(self) -> list[Candidate]:
        """새 데이터를 모으지 않고 지금 잴 수 있는 것들.

        여기가 비면 다음에 할 일이 '실험'이 아니라 '데이터 확보'다."""
        return [c for c in self.후보 if c.지금가능]


def _rows(items, cls):
    결과 = []
    for item in items:
        빠진것 = {f for f in cls.__annotations__ if f not in item}
        if 빠진것:
            raise ValueError(
                f"{cls.__name__} 항목에 빠진 칸: {sorted(빠진것)} ({item.get('키') or item.get('제목', '?')})"
            )
        결과.append(cls(**{f: item[f] for f in cls.__annotations__}))
    return 결과


def load(path: Path | None = None) -> RealtimePlan:
    """계획을 읽는다. 형식이 틀리면 조용히 넘어가지 않고 터뜨린다."""
    data = json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    단계 = data.get("단계", "")
    if 단계 not in STAGES:
        raise ValueError(f"모르는 단계: {단계}: {sorted(STAGES)} 중 하나여야 합니다")

    후보 = _rows(data.get("후보", []), Candidate)
    모르는등급 = {c.등급 for c in 후보} - set(GRADES)
    if 모르는등급:
        raise ValueError(f"모르는 근거 등급: {sorted(모르는등급)}: {sorted(GRADES)} 중 하나여야 합니다")

    return RealtimePlan(
        단계=단계,
        한줄=data.get("한줄", ""),
        막는것=list(data.get("막는것", [])),
        후보=후보,
        검증=_rows(data.get("검증", []), Finding),
    )
