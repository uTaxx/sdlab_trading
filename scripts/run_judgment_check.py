"""판단 지침을 전략과 같은 방식으로 검증한다.

    python scripts/run_judgment_check.py
    python scripts/run_judgment_check.py --상한 5,20 --나온곳 dashboard/자료/지침검증.json

**전략과 같은 기간 기준으로 잽니다**(2026-09-07에 주인이 정함). 구간
길이를 보유 상한과 같게 두고, 전략을 줄 세울 때 쓰는 동점 범위를 지침에도
그대로 씁니다. 재는 단위가 다르면 "전략은 이 기준으로 골랐는데 지침은 저
기준으로 골랐다"가 되어 둘을 같이 읽을 수 없습니다.

**주문은 나가지 않습니다.** 과거 시세로 계산만 하고 상태 DB도 시트도
고치지 않습니다. 여기 1위로 나온 지침이 저절로 걸리지도 않습니다.

지침을 바꾸면 저녁 검토가 내는 후보가 바뀌고 그 후보가 텔레그램 버튼으로
나갑니다. 실제 주문에 영향을 주는 결정이라 사람이 정합니다.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import judgment_check as 검증
from muwon.analysis.market_data import load_histories
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE, Ticker
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.sector.catalog import CATALOG
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")


def 매매대상() -> list[Ticker]:
    아는것 = {ㅌ.symbol: ㅌ for ㅌ in UNIVERSE}
    본것: dict[str, Ticker] = {}
    for 섹터 in CATALOG:
        for 종목 in 섹터.활성종목:
            if 종목.symbol in 본것:
                continue
            본것[종목.symbol] = 아는것.get(종목.symbol) or Ticker(
                종목.symbol, 종목.name, 종목.market,
                f"{종목.symbol}.{'KQ' if 종목.market == 'KOSDAQ' else 'KS'}")
    return list(본것.values())


def 이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001
        return 키


def 인자들():
    ㄱ = argparse.ArgumentParser(description=__doc__)
    ㄱ.add_argument("--시작", default="2021-01-04")
    ㄱ.add_argument("--상한", default="5,20",
                   help="보유 상한이자 구간 길이. 쉼표로 여럿")
    ㄱ.add_argument("--돌아볼구간", type=int, default=검증.돌아볼구간)
    ㄱ.add_argument("--섹터당", type=int, default=3)
    ㄱ.add_argument("--동시보유", type=int, default=6)
    ㄱ.add_argument("--지금전략", default="volume_surge_3d_us60_2")
    ㄱ.add_argument("--지금지침", default="worst",
                   help="지금 걸린 지침 키. 바꿀지 판단하는 기준점")
    ㄱ.add_argument("--나온곳", default="",
                   help="화면이 읽을 JSON을 남길 자리")
    return ㄱ.parse_args()


def 한상한재기(histories, 전략들, 섹터표, 인자, 상한):
    """상한 하나로 자산 곡선을 얻고 지침을 견준다.

    **보유 상한이 곧 구간 길이다.** 전략이 5영업일까지 들고 있으면 계좌를
    5영업일로 잘라 본 분포가 실제 운용에 가깝다. 상한이 바뀌면 전략의
    자산 곡선 자체가 달라지므로 상한마다 다시 굴린다."""
    시작 = datetime.fromisoformat(인자.시작).date()
    정책 = RiskPolicy(max_holding_days=상한,
                    max_concurrent_positions=인자.동시보유)
    곡선들, 거래수, 진입일들 = 검증.전략곡선들(
        histories, 전략들, 정책, 시작, 섹터표=섹터표, 섹터상한=인자.섹터당)
    구간표 = 검증.구간표만들기(곡선들, 상한, 진입일들)
    칸수 = min(len(v) for v in 구간표.values()) if 구간표 else 0
    성적들 = 검증.모두재기(구간표, 인자.돌아볼구간)
    대조들 = [ㄷ for ㄷ in (
        검증.무작위대조군(구간표, 인자.돌아볼구간),
        검증.전체평균대조군(구간표, 인자.돌아볼구간),
        검증.안바꾼대조군(구간표, 인자.지금전략, 인자.돌아볼구간),
    ) if ㄷ]
    판단 = 검증.지침변경판단(성적들, 대조들, 인자.지금지침, 상한)
    return {
        "상한": 상한, "전략수": len(곡선들), "구간수": 칸수,
        "성적들": 성적들, "대조들": 대조들, "판단": 판단, "거래수": 거래수,
    }


def 한판보이기(잰것):
    """사람이 읽는 표 하나."""
    print(f"\n■ 구간 {잰것['상한']}영업일 · 전략 {잰것['전략수']}개 · "
          f"구간 {잰것['구간수']}개")
    성적들 = 잰것["성적들"]
    if not 성적들:
        print("   견줄 수 있는 지침이 없습니다.")
        return
    print(f"   {'':>2} {'지침':22} {'최악':>7} {'중앙값':>7} {'플러스':>7}"
          f" {'바꾼 비율':>9}")
    for 자리, ㅅ in enumerate(성적들, 1):
        붙임 = ㅅ.이름[:18]
        표시 = " ←지금" if ㅅ.키 == 잰것["판단"].지금지침 else ""
        print(f"   {자리:>2} {붙임}{' ' * max(0, 20 - len(붙임) * 2)}"
              f" {ㅅ.최악:>7.1f} {ㅅ.중앙값:>7.2f} {ㅅ.플러스비율:>6.1f}%"
              f" {ㅅ.바꾼비율:>8.1f}%{표시}")
    print("\n   대조군")
    for ㄷ in 잰것["대조들"]:
        print(f"      {ㄷ.이름[:30]:32} {ㄷ.최악:>7.1f} {ㄷ.중앙값:>7.2f} "
              f"{ㄷ.플러스비율:>6.1f}%")
    print(f"\n   판단: {잰것['판단'].한줄()}")


def main() -> int:
    인자 = 인자들()
    시작 = datetime.fromisoformat(인자.시작).date()
    끝 = datetime.now(tz=서울).date()
    상한들 = [int(ㄱ) for ㄱ in str(인자.상한).split(",") if ㄱ.strip()]
    종목들 = 매매대상()

    print(f"■ 판단 지침 검증 · {len(종목들)}종목 · 구간 {상한들}영업일 · "
          f"{시작} ~ {끝}")
    print(f"  판정할 때 앞의 {인자.돌아볼구간}구간만 봅니다. 미래는 안 봅니다.")
    print(f"  앞 구간에 {검증.최소매매}건도 안 산 전략은 후보에서 뺍니다.")
    print(f"  지금 걸린 지침: {인자.지금지침}")

    histories = load_histories(
        YahooFinanceDataSource(), 종목들,
        시작 - timedelta(days=420), 끝, cache=PriceCache())
    if not histories:
        print("  시세를 하나도 못 받았습니다.")
        return 1

    전략들 = {ㄷ.key: (lambda k=ㄷ.key: build_strategy(k))
            for ㄷ in list_definitions()}
    섹터표 = {종목.symbol: 섹터.코드
           for 섹터 in CATALOG for 종목 in 섹터.활성종목}

    잰것들 = [한상한재기(histories, 전략들, 섹터표, 인자, ㅅ) for ㅅ in 상한들]
    for 잰것 in 잰것들:
        한판보이기(잰것)

    # 상한마다 답이 갈리면 그 사실을 그대로 적는다. 하나를 골라 적으면
    # 구간 길이를 우연히 잘 잡은 것을 결론으로 읽게 된다.
    바꾸자 = [ㅈ for ㅈ in 잰것들 if ㅈ["판단"].바꿀까]
    print("\n■ 종합")
    if not 바꾸자:
        print("   어느 상한에서도 지침을 바꿀 근거가 없습니다. 그대로 둡니다.")
    elif len(바꾸자) == len(잰것들):
        고른것 = {ㅈ["판단"].고른지침 for ㅈ in 바꾸자}
        if len(고른것) == 1:
            print(f"   모든 상한이 같은 지침({고른것.pop()})을 가리킵니다.")
        else:
            print("   상한마다 다른 지침을 가리킵니다. 하나로 모이지 "
                  "않으므로 그대로 두는 것이 안전합니다.")
    else:
        print("   일부 상한에서만 바꾸자고 나옵니다. 구간 길이를 바꾸면 "
              "답이 달라진다는 뜻이라 그대로 두는 것이 안전합니다.")

    if 인자.나온곳:
        자리 = Path(인자.나온곳)
        자리.parent.mkdir(parents=True, exist_ok=True)
        자리.write_text(json.dumps({
            "잰날": str(끝),
            "종목수": len(종목들),
            "돌아볼구간": 인자.돌아볼구간,
            "지금지침": 인자.지금지침,
            "지금전략": 인자.지금전략,
            "최소매매": 검증.최소매매,
            "최소판정": 검증.최소판정,
            "동점범위": 검증.동점범위,
            "설명": ("지침으로 고른 전략이 그다음 구간에서 낸 성적입니다. "
                   "판정할 때 그 앞 구간만 봅니다."),
            "잰것들": [{
                "상한": ㅈ["상한"], "전략수": ㅈ["전략수"], "구간수": ㅈ["구간수"],
                "바꿀까": ㅈ["판단"].바꿀까, "까닭": ㅈ["판단"].까닭,
                "고른지침": ㅈ["판단"].고른지침,
                "판정수": ㅈ["판단"].판정수,
                "성적들": [{
                    "키": ㅅ.키, "이름": ㅅ.이름, "최악": round(ㅅ.최악, 2),
                    "중앙값": round(ㅅ.중앙값, 3),
                    "플러스비율": round(ㅅ.플러스비율, 1),
                    "바꾼비율": round(ㅅ.바꾼비율, 1),
                    "판정수": ㅅ.판정수, "못한판정": ㅅ.못한판정,
                } for ㅅ in ㅈ["성적들"]],
                "대조들": [{
                    "이름": ㄷ.이름, "최악": round(ㄷ.최악, 2),
                    "중앙값": round(ㄷ.중앙값, 3),
                    "플러스비율": round(ㄷ.플러스비율, 1),
                } for ㄷ in ㅈ["대조들"]],
            } for ㅈ in 잰것들],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n   {자리}에 남겼습니다.")

    print("\n  이 결과로 지침이 바뀌지 않습니다. 바꾸는 것은 사람이 정합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
