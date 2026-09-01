"""과거 날짜마다 전망을 내 보고, 실제로 어떻게 됐는지 채운다.

## 왜 이걸 하나

전망 기록을 오늘부터 쌓으면 **판정에 몇 달이 걸린다.** 그런데 과거
날짜에 대해서는 답을 이미 알고 있다. 그날 전망을 내고, 그 뒤 20일에
실제로 무슨 일이 있었는지 보면 된다.

**이건 반칙이 아니다.** 전망을 낼 때 그날 이후 데이터를 안 쓰기 때문이다.

- 상태 표준화는 그 시점까지의 과거로만 한다(shift + rolling)
- 비슷했던 과거는 **기준일에서 지평만큼 이전까지만** 고른다
- 기준선도 기준일까지만 본다

즉 **그날 실제로 알 수 있었던 것만으로** 전망을 내고, 결과는 나중에
채운다. 이걸 흔히 '미래를 모르는 척 검증'이라고 한다.

## 무엇을 알게 되나

    이 전망이 동전 던지기보다 나은가?

나으면 계속 쓰고, 아니면 버린다. **숫자가 있다는 것과 쓸모가 있다는 것은
다르다.**

사용 예:
    python scripts/backfill_forecasts.py --from-year 2010 --every 5
    python scripts/backfill_forecasts.py --targets "코스피 전체,반도체"
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.market import forecast_log
from muwon.market import series as 시계열
from muwon.market.analog import forecast
from muwon.market.sector_index import build_index
from muwon.market.state import build_state
from muwon.sector.catalog import CATALOG, 국제시세

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lens", default=시계열.DEFAULT_LENS, choices=list(시계열.LENSES))
    parser.add_argument("--from-year", type=int, default=2010)
    parser.add_argument("--horizon", type=int, default=20)
    parser.add_argument("--top-pct", type=float, default=5.0)
    parser.add_argument(
        "--every", type=int, default=5,
        help="며칠에 한 번 전망을 낼 것인가. 매일 내면 이웃한 날들이 거의 같아 표본이 부풀려진다",
    )
    parser.add_argument("--targets", default="", help="이 대상만 (쉼표 구분). 비우면 전부")
    parser.add_argument("--db", default="forecasts_backfill.db", help="어디에 쌓을 것인가")
    parser.add_argument("--out", default="", help="결과를 남길 파일")
    args = parser.parse_args()

    db = Path(args.db)
    쓴것: list[str] = []

    def emit(글: str = "") -> None:
        print(글)
        쓴것.append(글)

    source, cache = YahooFinanceDataSource(), PriceCache()
    오늘 = datetime.now(KST).date()
    시작 = date(1990, 1, 1)

    print("■ 바깥 시계열 받는 중…", file=sys.stderr)
    바깥 = 시계열.load(
        [s.키 for s in 시계열.lens_series(args.lens)], 시작, 오늘, source=source, cache=cache
    )
    상태 = build_state(바깥)
    코스피 = 바깥["kospi"].set_index("trade_date")["close"].astype(float)

    # ── 대상별 가격 시계열 모으기 ──────────────────────────────────
    가격표: dict[str, object] = {"코스피 전체": 코스피}
    print("■ 섹터 시세 받는 중…", file=sys.stderr)
    for sector in CATALOG:
        if not sector.활성:
            continue
        if sector.전망출처 == "국제시세":
            for 심볼, 이름 in 국제시세.get(sector.코드, []):
                try:
                    df = cache.fetch(source, 심볼, 심볼, 시작, 오늘)
                except (requests.RequestException, ValueError, KeyError):
                    continue
                가격표[f"{sector.이름}: {이름}"] = df.set_index("trade_date")["close"].astype(float)
            continue
        모음 = {}
        for m in sector.활성종목:
            try:
                df = cache.fetch(source, m.symbol, m.yahoo_symbol, 시작, 오늘)
            except (requests.RequestException, ValueError, KeyError):
                continue
            if df is not None and len(df):
                모음[m.symbol] = df
        지수 = build_index(모음)
        if len(지수):
            가격표[sector.이름] = 지수["close"]

    고른대상 = [t.strip() for t in args.targets.split(",") if t.strip()] or list(가격표)
    없는것 = [t for t in 고른대상 if t not in 가격표]
    if 없는것:
        raise SystemExit(f"모르는 대상: {없는것} (있는 것: {list(가격표)})")

    # ── 전망을 낼 날짜들 ─────────────────────────────────────────
    #
    # 마지막 지평만큼은 결과를 모르므로 뺀다. 그리고 --every로 띄엄띄엄
    # 고른다. 이웃한 날들은 상태가 거의 같아서 표본만 부풀린다.
    쓸수있는날 = [d for d in 상태.index if d.year >= args.from_year]
    낼날들 = 쓸수있는날[: -args.horizon or None][:: args.every]
    emit(f"■ 되돌려 검증: {len(낼날들)}개 날짜 × 대상 {len(고른대상)}개")
    emit(f"  {낼날들[0]} ~ {낼날들[-1]} · {args.every}일 간격 · 지평 {args.horizon}일 · 렌즈 {args.lens}")
    emit("  전망을 낼 때 그날 이후 데이터는 쓰지 않습니다. 미래를 모르는 척 검증입니다.")
    emit()

    줄들 = []
    for 번호, 낸날 in enumerate(낼날들, 1):
        if 번호 % 50 == 0:
            print(f"  {번호}/{len(낼날들)} ({낸날})", file=sys.stderr)
        for 대상 in 고른대상:
            f = forecast(
                상태, 가격표[대상], 대상, 기준일=낸날,
                top_pct=args.top_pct, horizon=args.horizon,
            )
            줄들.append(forecast_log.row_from_forecast(f, 렌즈=args.lens))
    forecast_log.save(줄들, db)

    def 실제결과(대상: str, 낸날: date, 지평: int):
        가격 = 가격표.get(대상)
        if 가격 is None or 낸날 not in 가격.index:
            return None
        i = 가격.index.get_loc(낸날)
        j = i + 지평
        if j >= len(가격):
            return None
        앞 = float(가격.iloc[i])
        return (float(가격.iloc[j]) / 앞 - 1) * 100 if 앞 > 0 else None

    채운수 = forecast_log.fill_actuals(실제결과, db, today=오늘)
    emit(f"전망 {len(줄들)}줄 저장, {채운수}줄에 실제 결과를 채웠습니다.")
    emit()

    # ── 채점 ───────────────────────────────────────────────────
    전체 = forecast_log.load(db)
    emit(forecast_log.format_scorecard(forecast_log.score(전체, "전부 합쳐")))
    emit()
    emit(forecast_log.calibration(전체))
    emit()
    emit("■ 대상별")
    emit()
    for 대상 in 고른대상:
        s = forecast_log.score(forecast_log.load(db, 대상=대상), 대상)
        if not s.판정할수있나:
            emit(f"  {대상:<24} {s.사유}")
            continue
        판정 = "나음" if s.더나은가 else "**못함**"
        꼬리 = f" · 꼬리 {s.꼬리뚫린비율:.0f}%" if s.꼬리뚫린비율 is not None else ""
        위험 = "  ⚠위험낮잡음" if s.위험을낮잡았나 else ""
        emit(
            f"  {대상:<24}{s.채워진수:>5}건  적중 {s.적중률:>5.1f}%  "
            f"기준선 {s.기준선적중률:>5.1f}%  {판정}{꼬리}{위험}"
        )

    emit()
    emit("※ 적중률이 '기준선'보다 낮으면 그 대상의 전망은 버립니다.")
    emit("※ 꼬리 비율이 20%를 넘으면 '아주 나빴을 때' 칸이 위험을 낮잡고 있는 것입니다.")
    emit("   그 칸을 믿고 비중을 키우면 다칩니다.")

    if args.out:
        Path(args.out).write_text("\n".join(쓴것) + "\n", encoding="utf-8")
        print(f"\n결과를 {args.out}에 남겼습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
