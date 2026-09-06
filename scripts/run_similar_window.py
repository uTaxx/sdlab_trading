"""2단계. 지금과 비슷했던 과거 구간을 찾고, 그때 좋았던 전략을 보여 준다.

    python scripts/run_similar_window.py
    python scripts/run_similar_window.py --기준일 2026-06-30 --길이 20

**주문은 나가지 않습니다.** 과거 시세로 계산만 하고 상태 DB도 시트도
고치지 않습니다. 여기서 1위로 나온 전략이 저절로 걸리지도 않습니다.

매매 대상은 섹터 목록(`sector/catalog.py`)의 활성 종목입니다. 그것이 곧
실거래 시트에 올리는 목록입니다. 시트를 직접 읽지 않는 것은 이 스크립트가
아무것도 안 고치므로 구글 인증 없이 돌 수 있게 하기 위해서입니다.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import similar_window as 이단계
from muwon.analysis.market_data import load_histories
from muwon.data.investor_flow import 순매매캐시
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE, Ticker
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.sector.catalog import CATALOG
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")


def 매매대상() -> list[Ticker]:
    """섹터 목록의 활성 종목. 야후 기호는 등록된 목록에서 가져오고,
    거기 없으면 시장에 맞춰 만든다(코스피 .KS, 코스닥 .KQ)."""
    아는것 = {ㅌ.symbol: ㅌ for ㅌ in UNIVERSE}
    본것: dict[str, Ticker] = {}
    for 섹터 in CATALOG:
        for 종목 in 섹터.활성종목:
            if 종목.symbol in 본것:
                continue
            있는것 = 아는것.get(종목.symbol)
            본것[종목.symbol] = 있는것 or Ticker(
                종목.symbol, 종목.name, 종목.market,
                f"{종목.symbol}.{'KQ' if 종목.market == 'KOSDAQ' else 'KS'}")
    return list(본것.values())


def 섹터표만들기() -> dict[str, str]:
    return {종목.symbol: 섹터.코드
            for 섹터 in CATALOG for 종목 in 섹터.활성종목}


def 이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001
        return 키


def 인자들():
    ㄱ = argparse.ArgumentParser(description=__doc__)
    ㄱ.add_argument("--기준일", default="", help="비우면 시세의 마지막 날")
    ㄱ.add_argument("--길이", type=int, default=이단계.구간길이)
    ㄱ.add_argument("--앞으로", type=int, default=이단계.지평)
    ㄱ.add_argument("--시작", default="2019-01-02", help="시세를 언제부터 받을지")
    ㄱ.add_argument("--보유상한", type=int, default=5)
    ㄱ.add_argument("--섹터당", type=int, default=3)
    ㄱ.add_argument("--동시보유", type=int, default=6)
    ㄱ.add_argument("--몇개", type=int, default=10, help="순위를 몇 개까지 볼지")
    return ㄱ.parse_args()


def main() -> int:
    인자 = 인자들()
    시작 = datetime.fromisoformat(인자.시작).date()
    끝 = datetime.now(tz=서울).date()
    기준일 = datetime.fromisoformat(인자.기준일).date() if 인자.기준일 else None

    종목들 = 매매대상()
    print(f"■ 2단계 · 비슷했던 과거 찾기 · {len(종목들)}종목 · "
          f"구간 {인자.길이}거래일 · 이후 {인자.앞으로}거래일")

    histories = load_histories(
        YahooFinanceDataSource(), 종목들,
        # 예열을 넉넉히 준다. 200일 이동평균을 보는 전략이 있고, z점수를
        # 내려면 그보다 더 긴 과거가 필요하다.
        시작 - timedelta(days=420), 끝, cache=PriceCache())
    print(f"  시세 {len(histories)}종목")
    if not histories:
        print("  시세를 하나도 못 받았습니다.")
        return 1

    흐름캐시 = 순매매캐시()
    수급 = {}
    for ㅌ in 종목들:
        표 = 흐름캐시.읽기(ㅌ.symbol, 시작, 끝)
        if not 표.empty:
            수급[ㅌ.symbol] = 표
    print(f"  외국인 수급 {len(수급)}종목"
          + ("" if len(수급) == len(종목들)
             else f" (없는 종목 {len(종목들) - len(수급)}개는 빼고 잽니다)"))

    정책 = RiskPolicy(
        max_holding_days=인자.보유상한,
        max_concurrent_positions=인자.동시보유,
    )
    전략들 = {ㄷ.key: (lambda k=ㄷ.key: build_strategy(k))
            for ㄷ in list_definitions()}

    결과 = 이단계.찾기(
        histories, 전략들, 정책,
        수급=수급, 기준일=기준일,
        길이=인자.길이, 앞으로=인자.앞으로,
        섹터표=섹터표만들기(), 섹터상한=인자.섹터당,
    )

    print()
    if 결과.지금:
        print(f"■ {결과.기준일} 기준 최근 {인자.길이}거래일의 상태")
        print(f"  {결과.지금.한줄()}")
        print(f"  비슷한지 잰 기준: {', '.join(결과.쓴특징)}")
    if 결과.사유:
        print(f"\n  {결과.사유}")
        return 1

    print()
    print(f"■ {결과.표본글()}")
    print(f"  {'대표일':>12}{'묶인 날':>9}{'거리':>8}   그 구간")
    for ㄱ in 결과.구간들:
        print(f"  {ㄱ.대표일!s:>12}{ㄱ.묶인일수:>9}{ㄱ.거리:>8.2f}   {ㄱ.시작} ~ {ㄱ.끝}")

    if not 결과.낼수있나:
        print("\n  매수가 발생한 전략이 없습니다.")
        return 1

    print()
    print(f"■ 그 구간 다음 {인자.앞으로}거래일에 좋았던 전략")
    print("  가장 나빴던 구간을 먼저 보고, 비슷하면 중앙값으로 순서를 정합니다.")
    print()
    print(f"  {'':>2} {'전략':26} {'최악':>7} {'중앙값':>7} {'최고':>7}"
          f" {'이긴 구간':>9} {'거래':>6}")
    for 자리, ㄱ in enumerate(결과.순위[: 인자.몇개], 1):
        # 한글은 글자 폭이 두 배라 폭을 맞추려면 실제 글자 수를 세야 한다.
        붙임 = 이름(ㄱ.키)[:20]
        print(f"  {자리:>2} {붙임}{' ' * max(0, 22 - len(붙임) * 2)}"
              f" {ㄱ.최악:>7.1f} {ㄱ.중앙값:>7.1f} {ㄱ.최고:>7.1f}"
              f" {ㄱ.이긴구간:>5}/{ㄱ.구간수:<3} {ㄱ.거래합:>6}")

    print()
    print("  이 순위로 매매가 바뀌지 않습니다. 전략을 바꾸는 것은 예약을 거쳐야 "
          "합니다.")
    if not 결과.표본충분:
        print(f"  구간이 {결과.구간수}개뿐입니다. 우연일 수 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
