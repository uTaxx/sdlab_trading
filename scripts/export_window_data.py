"""상태 DB에 쌓인 상한별 측정을 화면이 읽을 JSON으로 뽑는다.

## 왜 이 길인가

화면은 정적 파일이라 파이썬도 DB도 못 부른다. 시트를 거치는 길(n8n)도
있지만 이 자료는 줄이 천 개가 넘어서 시트에 맞지 않는다. 그래서 파일로
뽑아 저장소에 같이 둔다.

**측정 워크플로가 이 스크립트를 돌리고 결과를 커밋한다.** 사람이 손으로
옮겨 적지 않는다. 연도별 성적표는 실행 로그에서 손으로 옮겨 적었는데,
줄이 스물아홉 개라 가능했던 것이다. 여기는 천 개가 넘는다.

## 줄을 배열로 적는다

칸 이름을 줄마다 되풀이하면 파일이 세 배가 된다. 머리에 칸 이름을 한 번
적고 줄은 값만 배열로 적는다. 화면은 그 머리를 읽어 자리를 찾는다.

**화면이 칸 순서를 손으로 적으면 안 된다.** 여기서 칸을 하나 더하는
순간 화면이 엉뚱한 값을 읽는다. 시험이 막는다.

## 실행

    python scripts/export_window_data.py
    python scripts/export_window_data.py --나온곳 dashboard/자료/상한측정.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import market_regime as ㄲ
from muwon.analysis import window_store as ㅅ
from muwon.analysis.market_data import load_histories
from muwon.config import bootstrap_settings
from muwon.db.session import make_session_factory
from muwon.strategy.registry import get_definition

나온곳기본 = (Path(__file__).resolve().parent.parent
          / "dashboard" / "자료" / "상한측정.json")

#: 줄 하나에 이 순서로 값이 들어간다. 화면은 이 목록을 읽어 자리를 찾는다.
#: **여기 순서를 바꾸면 화면도 따라 바뀐다.** 화면에 순서를 적어 두면 안 된다.
칸들 = (
    "전략", "상한", "슬리피지", "시작일", "국면",
    "연환산", "기하평균", "산술평균", "중앙값", "플러스비율", "하위10", "하위25",
    "최악구간", "최고구간", "구간흔들림", "하락대비수익", "구간낙폭중앙값",
    "구간수",
    "매매수", "승률", "손익비", "기대수익", "매매중앙값", "평균보유일수",
    "기간만료비율", "손절비율", "익절비율", "매도신호비율", "트레일링비율",
    "미청산수",
    "누적수익률", "최대낙폭",
)

#: 화면에서 고를 수 있는 매매 대상. 열쇠와 사람이 읽는 이름이다.
매매대상들 = (("sheet", "실거래 시트"), ("market_cap", "시가총액 상위"))


def _반올림(값):
    """소수 넷째 자리까지만. 파일 크기를 줄이면서 화면이 보여 주는 자리는
    그대로 남는다. **못 잰 값은 null로 둔다. 0으로 채우지 않는다.**"""
    return None if 값 is None else round(float(값), 4)


def 한줄(ㄱ) -> list:
    ㅂ = ㄱ.매매.갈래비율 or {}
    값들 = {
        "전략": ㄱ.전략, "상한": ㄱ.상한, "슬리피지": ㄱ.슬리피지,
        "시작일": ㄱ.시작일.isoformat(), "국면": ㄱ.국면,
        "연환산": ㄱ.구간.연환산, "기하평균": ㄱ.구간.기하평균,
        "산술평균": ㄱ.구간.산술평균, "중앙값": ㄱ.구간.중앙값, "플러스비율": ㄱ.구간.플러스비율,
        "하위10": ㄱ.구간.하위10, "하위25": ㄱ.구간.하위25,
        "최악구간": ㄱ.구간.최악, "최고구간": ㄱ.구간.최고,
        "구간흔들림": ㄱ.구간.표준편차, "하락대비수익": ㄱ.구간.하락대비수익,
        "구간낙폭중앙값": ㄱ.구간.구간낙폭중앙값, "구간수": ㄱ.구간.구간수,
        "매매수": ㄱ.매매.매매수, "승률": ㄱ.매매.승률,
        "손익비": ㄱ.매매.손익비, "기대수익": ㄱ.매매.기대수익,
        "매매중앙값": ㄱ.매매.중앙값, "평균보유일수": ㄱ.매매.평균보유일수,
        "기간만료비율": ㅂ.get("기간만료"), "손절비율": ㅂ.get("손절"),
        "익절비율": ㅂ.get("익절"), "매도신호비율": ㅂ.get("매도신호"),
        "트레일링비율": ㅂ.get("트레일링"), "미청산수": ㄱ.매매.미청산수,
        "누적수익률": ㄱ.누적수익률, "최대낙폭": ㄱ.최대낙폭,
    }
    return [
        값들[이름] if 이름 in ("전략", "상한", "시작일", "국면", "구간수",
                            "매매수", "미청산수")
        else _반올림(값들[이름])
        for 이름 in 칸들
    ]


def 이름찾기(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except (KeyError, AttributeError):
        # 이름을 못 찾는다고 자료를 안 내보내지 않는다. 키를 그대로 쓴다.
        return 키


def 지금국면알아보기() -> dict:
    """오늘 코스피가 어떤 국면인지. 화면 맨 위에 적는다.

    **못 받으면 비운다.** 지어내면 화면이 아는 척을 하게 된다. 국면을
    모른다고 나머지 자료를 못 쓰는 것은 아니다."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    try:
        from muwon.data.price_cache import PriceCache
        from muwon.data.universe import Ticker
        from muwon.data.yahoo_client import YahooFinanceDataSource

        오늘 = datetime.now(ZoneInfo("Asia/Seoul")).date()
        시세 = load_histories(
            YahooFinanceDataSource(),
            [Ticker(symbol="KOSPI", name="코스피", market="KOSPI",
                    yahoo_symbol="^KS11")],
            오늘 - timedelta(days=800), 오늘, cache=PriceCache(),
        ).get("KOSPI")
        국면, 값 = ㄲ.지금국면(시세)
        if 국면 is None:
            return {}
        마지막 = 시세["trade_date"].iloc[-1]
        return {
            "국면": 국면,
            "고점대비": round(float(값), 2) if 값 is not None else None,
            "기준일": str(마지막)[:10],
            "글": ㄲ.국면글(국면, 값),
        }
    except Exception as 탈:  # noqa: BLE001
        print(f"  코스피를 못 받아 지금 국면을 비웁니다: {탈}")
        return {}


def 모으기(session_factory) -> dict:
    """매매 대상마다 쌓인 것을 전부 모은다.

    **잰 날로 거르지 않는다.** 조건마다 잰 날이 다르다. 화면에서 조건을
    바꿔 가며 하나씩 재기 때문이다. 날짜로 거르면 어제 잰 조건이 오늘
    화면에서 사라진다.

    **잰 조건 목록을 따로 담는다.** 화면이 "이 조건은 아직 계산하지
    않았습니다"를 말하려면 무엇이 있는지가 아니라 무엇이 없는지를 알아야
    한다. 빈 표로 그리면 계산했는데 결과가 없다로 읽힌다."""
    묶음 = {}
    전략키들: set[str] = set()
    with session_factory() as 세션:
        for 열쇠, _ in 매매대상들:
            잰것들 = ㅅ.읽기(세션, 매매대상=열쇠)
            if not 잰것들:
                continue
            전략키들.update(ㄱ.전략 for ㄱ in 잰것들)
            잰날들 = sorted({ㄱ.잰날 for ㄱ in 잰것들})
            # 조건마다 종목 수가 다를 수 있다. 시트 종목은 주마다 바뀐다.
            # 가장 최근에 잰 조건의 값을 대표로 적는다.
            최근 = max(잰것들, key=lambda ㄱ: ㄱ.잰날)
            묶음[열쇠] = {
                "잰날": 최근.잰날.isoformat(),
                "처음잰날": 잰날들[0].isoformat(),
                "종목수": 최근.종목수,
                "끝일": 최근.끝일.isoformat(),
                "시작일들": sorted({ㄱ.시작일.isoformat() for ㄱ in 잰것들}),
                "상한들": sorted({ㄱ.상한 for ㄱ in 잰것들}),
                "슬리피지들": sorted({ㄱ.슬리피지 for ㄱ in 잰것들}),
                # 이미 잰 조건. 화면이 없는 조건을 가려내는 데 쓴다.
                "잰조건": sorted({
                    f"{ㄱ.시작일.isoformat()}|{ㄱ.상한}|{ㄱ.슬리피지}"
                    for ㄱ in 잰것들
                }),
                # 어느 국면으로 나눠 둔 것이 있나. 옛 측정에는 전체뿐이다.
                "국면들": sorted({ㄱ.국면 for ㄱ in 잰것들}),
                "줄": [한줄(ㄱ) for ㄱ in sorted(
                    잰것들,
                    key=lambda ㄱ: (ㄱ.시작일, ㄱ.국면, ㄱ.전략, ㄱ.상한,
                                  ㄱ.슬리피지))],
            }

    return {
        "설명": ("보유 상한을 바꿔 가며 전략을 잰 것입니다. 구간 길이는 상한과 "
               "같습니다. 줄은 칸들의 순서대로 값만 담습니다."),
        "주의": ("연환산은 상한이 다른 전략을 같은 줄에 놓기 위한 값이라 짧은 "
               "상한일수록 크게 나옵니다. 그 수익이 실제로 난다는 뜻이 "
               "아닙니다. 매매 대상이 지금 살아 있는 종목이라 과거로 가져가면 "
               "살아남은 회사만 봅니다. 절대 수익률은 부풀려져 있고 전략끼리 "
               "비교하는 데만 씁니다. 국면별로 나눈 값은 구간 수가 크게 "
               "줄어듭니다. 하락 국면은 5년에 몇 달뿐이라 20영업일 구간이 "
               "열 개 남짓입니다. 구간 수를 반드시 같이 보십시오."),
        "칸들": list(칸들),
        "지금국면": 지금국면알아보기(),
        "국면설명": {
            "상승": "코스피가 최근 1년 고점 대비 10% 미만으로 하락한 구간입니다.",
            "조정": "코스피가 최근 1년 고점 대비 10% 이상 20% 미만으로 "
                  "하락한 구간입니다.",
            "하락": "코스피가 최근 1년 고점 대비 20% 이상 하락한 구간입니다.",
            "상승→조정": "상승 구간 가운데, 그 뒤에 조정까지 갔다가 다시 "
                     "상승으로 돌아온 것만 모았습니다.",
            "상승→하락": "상승 구간 가운데, 그 뒤에 하락까지 이어진 것만 "
                     "모았습니다.",
            "조정→상승": "조정 구간 가운데, 그 뒤에 상승으로 회복한 것만 "
                     "모았습니다.",
            "조정→하락": "조정 구간 가운데, 그 뒤에 하락으로 이어진 것만 "
                     "모았습니다.",
            "하락→조정": "하락 구간 가운데, 그 뒤에 조정까지 올라온 것만 "
                     "모았습니다.",
            "하락→상승": "하락 구간 가운데, 그 뒤에 상승까지 회복한 것만 "
                     "모았습니다.",
        },
        # 셋은 부모이고 여섯은 그 안을 다시 나눈 것이다. 화면이 이것을
        # 읽어 목록을 계단처럼 보여 준다. 여기 없으면 화면이 아홉을
        # 나란히 늘어놓게 되고, 그러면 더해서 읽는 사람이 생긴다.
        "국면갈래": {
            "상승": ["상승→조정", "상승→하락"],
            "조정": ["조정→상승", "조정→하락"],
            "하락": ["하락→조정", "하락→상승"],
        },
        "국면주의": (
            "전환 이름표는 구간이 끝난 뒤에야 정해집니다. 오늘이 상승→조정인지 "
            "상승→하락인지는 오늘 알 수 없으므로, 이 숫자로 오늘 무엇을 살지 "
            "정할 수 없습니다. 지나간 구간의 성적을 나눠 보는 데만 씁니다. "
            "상승 구간의 수는 상승→조정과 상승→하락을 더한 것과 같습니다. "
            "같은 구간을 두 이름으로 담은 것이라 더해서 읽으면 안 됩니다."
        ),
        "이름표": {ㄱ: 이름찾기(ㄱ) for ㄱ in sorted(전략키들)},
        "매매대상이름": dict(매매대상들),
        "측정": 묶음,
    }


def main() -> int:
    받은것 = argparse.ArgumentParser(description=__doc__)
    받은것.add_argument("--나온곳", default=str(나온곳기본))
    인자 = 받은것.parse_args()

    자료 = 모으기(make_session_factory(bootstrap_settings.database_url))
    if not 자료["측정"]:
        # 빈 파일을 덮어쓰면 화면에 있던 자료가 사라진다. 아직 안 쌓인 것과
        # 지워 버린 것은 화면에서 구별되지 않는다.
        print("::error::DB에 상한별 측정이 하나도 없습니다. 파일을 안 고칩니다.")
        return 1

    길 = Path(인자.나온곳)
    길.parent.mkdir(parents=True, exist_ok=True)
    길.write_text(json.dumps(자료, ensure_ascii=False, separators=(",", ":")) + "\n",
                 encoding="utf-8")

    for 열쇠, 칸 in 자료["측정"].items():
        print(f"■ {열쇠}: {len(칸['줄'])}줄 · {칸['종목수']}종목 · 잰날 {칸['잰날']}")
    print(f"■ {길}: {길.stat().st_size / 1024:.0f}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
