"""**섹터를 골라서 사는 게 정말 나은가**. 되돌려 검증.

1차 섹터 선정(`src/muwon/sector/selection.py`)의 전제를 시험한다.

    "최근 시장을 이기고 있던 섹터가 그 뒤에도 이긴다"

**이 스크립트는 아무것도 사지 않는다.** 숫자를 내고 판정만 한다.

## 무엇과 비교하나. 기준선이 없으면 숫자는 아무 말도 안 한다

고른 섹터의 그 뒤 수익률을 **전 섹터 평균**과 비교한다. 섹터를 안 고르고
다 사는 것이 기준선이다. 이걸 안 놓으면 "+8%"를 보고 발견인 줄 안다.
그 기간에 전 섹터 평균이 +9%였을 수도 있다.

## 이 검증의 한계: 먼저 적어 둔다

**생존편향이 남아 있다.** 섹터 지수를 오늘의 종목 목록으로 만들었으므로
상장폐지·편입 탈락한 종목이 빠져 있고, 그만큼 모든 섹터가 부풀려져 있다.
다만 **고른 섹터와 전 섹터 평균이 같은 편향을 공유**하므로, 둘의 **차이**는
절대 수익률보다 훨씬 덜 오염돼 있다. 그래서 차이만 본다.

**종목 선택은 안 들어 있다.** 여기서 재는 것은 "섹터 지수를 통째로 샀다면"
이다. 실제로는 그 안에서 다시 종목을 고르므로 결과가 달라질 수 있다.
이 검증이 말해 주는 것은 **1차 선정이 방향을 잡아 주는가** 하나뿐이다.

사용 예:
    python scripts/verify_sector_rotation.py
    python scripts/verify_sector_rotation.py --lookback 60 --hold 20 --top 2
"""

import argparse
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.market.sector_index import build_index
from muwon.sector.catalog import CATALOG
from muwon.sector.selection import pick, rank

KST = ZoneInfo("Asia/Seoul")
#: 이만큼은 있어야 그 회차를 센다. 섹터 절반이 빠진 날은 비교가 안 된다.
MIN_SECTORS = 4


def _섹터지수(cache, source, 시작: date, 끝: date):
    지수들, 이름표 = {}, {}
    for s in CATALOG:
        if not s.활성 or s.전망출처 != "섹터지수":
            continue
        모음 = {}
        for m in s.활성종목:
            야후 = f"{m.symbol}.KS" if m.market == "KOSPI" else f"{m.symbol}.KQ"
            try:
                df = cache.fetch(source, m.symbol, 야후, 시작, 끝, 최소일수=250)
            except (requests.RequestException, ValueError, KeyError):
                continue
            if df is not None and len(df):
                모음[m.symbol] = df
        if len(모음) >= 3:
            지수들[s.코드] = build_index(모음)["close"]
            이름표[s.코드] = s.이름
        else:
            print(f"  {s.이름}: 종목 {len(모음)}개뿐이라 뺍니다", file=sys.stderr)
    return 지수들, 이름표


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback", type=int, default=20, help="며칠을 돌아보고 고를 것인가")
    parser.add_argument("--hold", type=int, default=20, help="고른 뒤 며칠을 들 것인가")
    parser.add_argument("--top", type=int, default=3, help="몇 개 섹터를 고를 것인가")
    parser.add_argument("--step", type=int, default=10, help="며칠마다 다시 고를 것인가")
    parser.add_argument("--out", default="", help="결과를 남길 파일")
    args = parser.parse_args()

    쓴것: list[str] = []

    def emit(글: str = "") -> None:
        print(글)
        쓴것.append(글)

    source, cache = YahooFinanceDataSource(), PriceCache()
    오늘 = datetime.now(KST).date()
    print("■ 섹터 종목 시세 받는 중…", file=sys.stderr)
    지수들, 이름표 = _섹터지수(cache, source, date(2010, 1, 1), 오늘)
    if len(지수들) < MIN_SECTORS:
        raise SystemExit(f"섹터가 {len(지수들)}개뿐입니다. 비교가 안 됩니다")

    코스피 = cache.fetch(source, "^KS11", "^KS11", date(2010, 1, 1), 오늘, 최소일수=2000)
    시장 = 코스피.set_index("trade_date")["close"].astype(float)

    # 모든 섹터에 값이 있는 날만 쓴다. 섹터마다 시작일이 다른데 섞으면
    # "그날 있던 섹터끼리"가 아니라 "아무 섹터끼리" 비교가 된다.
    공통 = None
    for 지수 in 지수들.values():
        공통 = set(지수.index) if 공통 is None else (공통 & set(지수.index))
    날들 = sorted(공통 & set(시장.index))
    emit(f"■ 섹터 {len(지수들)}개 · 모두 값이 있는 날 {len(날들)}일 "
         f"({날들[0]} ~ {날들[-1]})")
    emit(f"  고르는 기준: 최근 {args.lookback}일 시장 대비 강도 상위 {args.top}개")
    emit(f"  들고 있는 기간: {args.hold}일 · {args.step}일마다 다시 고름")
    emit()

    회차 = []
    for i in range(args.lookback, len(날들) - args.hold, args.step):
        기준일 = 날들[i]
        결과 = pick(rank(지수들, 이름표, 시장, 기준일=기준일, lookback=args.lookback),
                    top_n=args.top)
        뽑힌것 = [p for p in 결과 if p.뽑힘]

        앞 = 날들[i]
        뒤 = 날들[i + args.hold]

        def 수익(코드, 앞=앞, 뒤=뒤):
            s = 지수들[코드]
            return (float(s.loc[뒤]) / float(s.loc[앞]) - 1) * 100

        전체 = [수익(코드) for 코드 in 지수들]
        고른것 = [수익(p.코드) for p in 뽑힌것]
        회차.append({
            "날": 기준일,
            "고름": statistics.mean(고른것) if 고른것 else 0.0,  # 안 고른 회차는 현금
            "전체": statistics.mean(전체),
            "고른수": len(고른것),
            "이름": [p.이름 for p in 뽑힌것],
        })

    emit(f"■ 회차 {len(회차)}번")
    emit()

    차이 = [r["고름"] - r["전체"] for r in 회차]
    이긴수 = sum(1 for d in 차이 if d > 0)
    안고른수 = sum(1 for r in 회차 if r["고른수"] == 0)

    emit("■ 골라서 사는 것이 다 사는 것보다 나았나")
    emit()
    emit(f"  고른 섹터 평균 수익      {statistics.mean(r['고름'] for r in 회차):>+7.2f}%")
    emit(f"  다 샀다면 (기준선)       {statistics.mean(r['전체'] for r in 회차):>+7.2f}%")
    emit(f"  차이                     {statistics.mean(차이):>+7.2f}%p")
    emit(f"  이긴 회차                {이긴수}/{len(회차)} ({이긴수 / len(회차) * 100:.0f}%)")
    emit(f"  아무 섹터도 안 고른 회차  {안고른수}번 (그때는 현금으로 봤습니다)")
    emit()

    # 평균이 아니라 **가장 나빴던 해**가 1순위 기준이다.
    연도별: dict[int, list[float]] = {}
    for r in 회차:
        연도별.setdefault(r["날"].year, []).append(r["고름"] - r["전체"])
    emit("■ 해마다. **평균이 아니라 이쪽이 1순위 기준이다**")
    emit()
    emit(f"  {'해':<8}{'회차':>5}{'차이(평균)':>12}{'차이(최악)':>12}")
    for 해 in sorted(연도별):
        값 = 연도별[해]
        emit(f"  {해:<8}{len(값):>5}{statistics.mean(값):>+11.2f}%p{min(값):>+11.2f}%p")
    emit()

    나쁜해 = min(연도별, key=lambda 해: statistics.mean(연도별[해]))
    emit(f"  가장 나빴던 해: {나쁜해}: 평균 {statistics.mean(연도별[나쁜해]):+.2f}%p")
    emit()

    # 우연히 이만큼 벌어질 수 있는 폭. 회차가 적으면 큰 차이도 우연이다.
    표준편차 = statistics.stdev(차이) if len(차이) > 1 else 0.0
    우연폭 = 1.96 * 표준편차 / (len(차이) ** 0.5)
    emit("■ 우연인가")
    emit()
    emit(f"  차이의 평균  {statistics.mean(차이):+.2f}%p")
    emit(f"  우연 폭      ±{우연폭:.2f}%p  (회차 {len(회차)}번 기준)")
    emit()
    if abs(statistics.mean(차이)) <= 우연폭:
        emit("  → **우연 폭 안입니다.** 이 정도 차이는 아무 의미 없는 규칙으로도 나옵니다.")
        판정 = "우연 폭 안: 근거로 쓸 수 없습니다"
    elif statistics.mean(차이) > 0:
        emit("  → 우연 폭을 넘었습니다. 골라서 사는 쪽이 나았습니다.")
        판정 = "골라 사는 쪽이 나음"
    else:
        emit("  → 우연 폭을 넘었는데 **방향이 반대입니다.** 골라서 사는 쪽이 더 나빴습니다.")
        판정 = "**골라 사는 쪽이 더 나쁨: 이 기준은 버립니다**"

    emit()
    emit(f"■ 판정: {판정}")
    emit()
    emit("※ 생존편향이 남아 있습니다. 오늘의 종목 목록으로 과거 지수를 만들었습니다.")
    emit("   고른 섹터와 전 섹터 평균이 같은 편향을 공유하므로 **차이**만 봅니다.")
    emit("※ 종목 선택은 안 들어 있습니다. 여기서 잰 것은 '섹터 지수를 통째로 샀다면'입니다.")

    if args.out:
        Path(args.out).write_text("\n".join(쓴것) + "\n", encoding="utf-8")
        print(f"\n결과를 {args.out}에 남겼습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
