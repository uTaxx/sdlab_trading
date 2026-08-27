"""용어 사전과 전략 설명을 대시보드가 읽을 JSON으로 뽑는다.

## 왜 뽑아 내나

새 대시보드는 정적 파일이라 파이썬을 못 부른다. 그렇다고 설명을 화면에
손으로 옮겨 적으면 **원본이 둘이 되고 언젠가 어긋난다** — 이 저장소가
이미 겪은 실수다(`strategy_rules.py` 첫 줄에 그 이야기가 적혀 있다).

그래서 원본은 파이썬에 그대로 두고, 여기서 **기계적으로 옮겨 적는다.**
사람이 손대는 곳은 계속 `glossary.py`와 `strategy_rules.py` 하나씩이다.

## 어긋나면 어떻게 아나

배포 워크플로가 이 스크립트를 다시 돌려서 결과가 커밋된 것과 다르면
**배포를 멈춘다.** 파이썬을 고치고 JSON을 안 뽑은 채로 올리면 화면이
옛 설명을 계속 보여 주는데, 그건 조용히 틀리는 쪽이라 막아야 한다.

    python scripts/export_dashboard_data.py            # 뽑아서 덮어쓴다
    python scripts/export_dashboard_data.py --check    # 다르면 1로 끝난다
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from muwon.dashboard.glossary import TERMS
from muwon.dashboard.strategy_rules import describe
from muwon.strategy.registry import REGISTRY, build_strategy

나가는곳 = Path(__file__).resolve().parent.parent / "dashboard" / "자료"


def 용어사전() -> list[dict]:
    """검색하기 쉽게 목록으로 편다. 화면에서 훑어 내리며 찾는 자료다."""
    return [
        {
            "열쇠": 열쇠,
            "이름": 낱말.이름,
            "뜻": 낱말.뜻,
            "읽는법": 낱말.읽는법,
            "영문": 낱말.영문,
        }
        for 열쇠, 낱말 in TERMS.items()
    ]


def 전략설명() -> list[dict]:
    """전략마다 '이럴 때 산다 / 이럴 때 판다'를 붙인다.

    설명은 전략 객체의 **실제 파라미터**에서 만들어진다. 파라미터를 바꾸면
    설명도 같이 바뀐다 — 손으로 적은 글이 코드와 어긋나는 일을 막는 장치다.
    """
    나온것 = []
    for 정의 in REGISTRY:
        줄 = {
            "키": 정의.key,
            "이름": 정의.화면이름,
            "자세한이름": 정의.display_name,
            "한줄": 정의.description,
            "계열": 정의.category,
            "상태": 정의.status,
        }
        try:
            규칙 = describe(build_strategy(정의.key))
            줄 |= {
                "산다": list(규칙.산다),
                "판다": list(규칙.판다),
                "참고": list(규칙.참고),
                "설명있음": bool(규칙.설명있음),
                # 이 전략이 스스로 정한 보유 기간. 화면의 "0이면 전략이
                # 정한 대로"가 몇 일인지 말하려면 이 숫자가 필요하다.
                "보유일": getattr(build_strategy(정의.key), "max_holding_days", None),
            }
        except Exception as 탈:  # noqa: BLE001 — 무엇이 터지든 사전은 나와야 한다
            # 전략 하나가 안 만들어진다고 사전 전체를 못 뽑으면 안 된다.
            # 대신 무엇이 빠졌는지는 화면에 보이게 남긴다.
            줄 |= {"산다": [], "판다": [], "참고": [f"설명을 만들지 못했습니다: {탈}"],
                   "설명있음": False}
        나온것.append(줄)
    return 나온것


자료들 = {"용어사전.json": 용어사전, "전략설명.json": 전략설명}


def 글로(값) -> str:
    # 한글을 \uXXXX로 escape하면 사람이 diff를 못 읽는다.
    return json.dumps(값, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    받은것 = argparse.ArgumentParser(description=__doc__)
    받은것.add_argument("--check", action="store_true",
                       help="덮어쓰지 않고, 커밋된 것과 다르면 1로 끝난다")
    인자 = 받은것.parse_args()

    나가는곳.mkdir(parents=True, exist_ok=True)
    어긋난것 = []

    for 이름, 만들기 in 자료들.items():
        새것 = 글로(만들기())
        길 = 나가는곳 / 이름
        if 인자.check:
            옛것 = 길.read_text(encoding="utf-8") if 길.exists() else ""
            if 옛것 != 새것:
                어긋난것.append(이름)
            continue
        길.write_text(새것, encoding="utf-8")
        print(f"{길.relative_to(나가는곳.parent.parent)} — {len(json.loads(새것))}개")

    if 어긋난것:
        print("파이썬 원본과 뽑아 둔 JSON이 다릅니다: " + ", ".join(어긋난것), file=sys.stderr)
        print("python scripts/export_dashboard_data.py 를 돌리고 결과를 같이 커밋하세요.",
              file=sys.stderr)
        return 1
    if 인자.check:
        print("용어 사전과 전략 설명이 파이썬 원본과 같습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
