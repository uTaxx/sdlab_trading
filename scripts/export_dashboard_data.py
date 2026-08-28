"""용어 사전, 전략 설명, 기준 이름을 대시보드가 읽을 JSON으로 뽑는다.

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
from muwon.settings.from_sheet import 기준들
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


#: 변경 이력에 값 자체가 전략 키로 적히는 칸들. 화면은 이 칸의 값을
#: 전략 이름으로 바꿔서 보여 준다. 나머지 칸은 숫자나 true/false라
#: 바꾸면 오히려 틀린 말이 된다.
전략값칸 = (
    "strategy", "sell_strategy",
    "strategy.active_key", "strategy.active_keys", "strategy.sell_keys",
)

#: 시트 기준표에 없는, DB에만 있는 열쇠말. 변경 이력은 DB에서 나오므로
#: 이쪽 이름이 그대로 화면에 뜬다.
_디비칸 = {
    "strategy.active_keys": "쓰는 전략",
    "strategy.active_key": "쓰는 전략(옛 칸)",
    "strategy.sell_keys": "파는 쪽 전략",
    "strategy.combine": "전략 합치는 방식(AND/OR)",
    "strategy.factor_config": "종합점수 가중치",
    "risk.atr_stop_enabled": "변동성 손절 켜기",
    "risk.atr_stop_multiple": "변동성 손절 배수",
    "risk.atr_window": "변동성 재는 기간(일)",
    "risk.trailing_stop_enabled": "트레일링 손절 켜기",
    "risk.trailing_stop_multiple": "트레일링 손절 배수",
    "kis.account_no": "증권사 계좌번호",
    "kis.account_product_cd": "증권사 상품코드",
    "kis.app_key": "증권사 앱키",
    "kis.app_secret": "증권사 앱시크릿",
    "kis.env": "증권사 환경(모의/실거래)",
    "telegram.bot_token": "텔레그램 봇 토큰",
    "telegram.chat_id": "텔레그램 대화방",
}


def 기준이름() -> list[dict]:
    """설정 열쇠말 → 사람이 읽는 이름.

    변경 이력 표가 `stop_loss_pct` 같은 열쇠말을 그대로 보여 주고 있었다.
    무엇이 바뀌었는지 알아보려면 머릿속에서 한 번 옮겨야 하는데, 그 표는
    성적이 달라졌을 때 제일 먼저 보는 곳이라 그러면 안 된다.

    원본은 `from_sheet.기준들` 하나다. 시트 초안·검증·화면이 이미 그것을
    읽고 있어서, 여기서 한 벌 더 적으면 언젠가 어긋난다.

    같은 값이 시트에서는 `stop_loss_pct`, DB에서는 `risk.stop_loss_pct`로
    적힌다. 변경 이력은 DB에서 나오므로 **둘 다 넣는다.**
    """
    표: dict[str, str] = {}
    for b in 기준들:
        표[b.이름] = b.표시
        표.setdefault(f"risk.{b.이름}", b.표시)
        if b.정책필드:
            표.setdefault(f"risk.{b.정책필드}", b.표시)
    표 |= _디비칸
    return [
        {"열쇠": 열쇠, "이름": 이름, "전략값": 열쇠 in 전략값칸}
        for 열쇠, 이름 in sorted(표.items())
    ]


자료들 = {"용어사전.json": 용어사전, "전략설명.json": 전략설명,
         "기준설명.json": 기준이름}


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
        print("용어 사전·전략 설명·기준 이름이 파이썬 원본과 같습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
