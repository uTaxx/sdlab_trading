"""오늘 장이 어떤 상태이고, 비슷했던 과거에는 그 뒤 무슨 일이 있었나.

**이 스크립트는 아무것도 사지 않는다.** 보는 것만 한다.

설계안(`docs/설계_섹터기반.md`)의 1~4단계를 한 번에 돌린다.

1. 지수·원자재·환율 30년치를 받는다
2. 날짜별 장 상태를 z점수로 적는다
3. 섹터별 지수를 만든다(그 시점에 있던 종목만으로)
4. 지금과 비슷했던 과거를 찾아 그 뒤 20거래일 분포를 낸다

사용 예:
    python scripts/market_report.py
    python scripts/market_report.py --lens 기본 --horizon 60
    python scripts/market_report.py --as-of 2022-06-15   # 그날 무엇을 봤을지
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
import requests

from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.market import forecast_log
from muwon.market import series as 시계열
from muwon.market.analog import forecast, format_forecast
from muwon.market.sector_index import build_index, coverage_report, relative_strength
from muwon.market.state import build_state, describe_today, raw_indicators
from muwon.sector.catalog import CATALOG, 국제시세

KST = ZoneInfo("Asia/Seoul")


def _섹터시세(source, cache, start: date, end: date) -> dict[str, dict[str, pd.DataFrame]]:
    """섹터별 종목 일봉. 못 받은 종목은 건너뛰되 몇 개를 건너뛰었는지 찍는다."""
    결과 = {}
    for sector in CATALOG:
        if not sector.활성 or sector.전망출처 != "섹터지수":
            continue
        모음, 실패 = {}, []
        for m in sector.활성종목:
            try:
                # 야후가 간헐적으로 최근 20일치만 준다. 그게 캐시에 굳으면
                # 그 종목은 다음부터 영영 짧게 온다 — 최소일수로 막는다.
                df = cache.fetch(source, m.symbol, m.yahoo_symbol, start, end, 최소일수=250)
            except (requests.RequestException, ValueError, KeyError):
                실패.append(m.symbol)
                continue
            if df is not None and len(df):
                모음[m.symbol] = df
            else:
                실패.append(m.symbol)
        if 실패:
            print(f"  {sector.코드}: {len(실패)}종목 못 받음 ({', '.join(실패)})", file=sys.stderr)
        결과[sector.코드] = 모음
    return 결과


def _오늘한일(그날: date) -> dict:
    """오늘 낸 주문, 오늘 청산한 매매, 지금 들고 있는 종목을 DB에서 읽는다.

    **못 읽어도 리포트를 죽이지 않는다.** 다만 조용히 빈 값으로 넘기지도
    않는다 — 안 산 날과 DB를 못 읽은 날이 화면에서 같아 보이면, 매수가
    통째로 막힌 날을 평범한 날로 읽게 된다. 못 읽으면 그렇게 적는다.

    시세를 다시 부르지 않는다. 이 스크립트는 장 마감 뒤에 돌고, 지금 평가액을
    알려면 증권사에 또 물어야 한다. 보유 종목은 이름과 산 값까지만 적고,
    지금 얼마인지는 대시보드에서 보게 한다."""
    try:
        from sqlalchemy import select

        from muwon.config import bootstrap_settings
        from muwon.db.models import OrderRow, PositionRow, TradeRow
        from muwon.db.session import make_session_factory

        만들기 = make_session_factory(bootstrap_settings.database_url)
        with 만들기() as 세션:
            주문 = 세션.execute(select(OrderRow)).scalars().all()
            매매 = 세션.execute(select(TradeRow)).scalars().all()
            보유 = 세션.execute(select(PositionRow)).scalars().all()
    except Exception as e:  # noqa: BLE001 — DB가 없어도 리포트는 나가야 한다
        print(f"오늘 한 일을 못 읽었습니다: {type(e).__name__}: {e}", file=sys.stderr)
        return {"못읽음": f"{type(e).__name__}"}

    산것 = [
        (o.symbol, int(o.quantity), float(o.price))
        for o in 주문
        if o.created_at and o.created_at.date() == 그날 and str(o.side).upper() == "BUY"
    ]
    판것 = [
        (t.symbol, int(t.quantity), float(t.pnl_amount), float(t.pnl_pct))
        for t in 매매
        if t.exited_at and t.exited_at.date() == 그날
    ]
    가진것 = [(p.symbol, int(p.quantity), float(p.entry_price)) for p in 보유]
    return {"산것": 산것, "판것": 판것, "보유": 가진것}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lens", default=시계열.DEFAULT_LENS, choices=list(시계열.LENSES))
    parser.add_argument("--horizon", type=int, default=20, help="앞으로 며칠을 볼 것인가")
    parser.add_argument("--top-pct", type=float, default=5.0, help="가장 가까운 상위 몇 %%를 볼 것인가")
    parser.add_argument("--as-of", default="", help="이 날짜 기준으로 본다 (비우면 최신)")
    parser.add_argument("--out", default="", help="결과를 남길 파일")
    parser.add_argument("--no-log", action="store_true", help="전망 기록을 쌓지 않는다")
    parser.add_argument("--telegram", action="store_true", help="요약을 텔레그램으로 보낸다")
    parser.add_argument(
        "--no-send", action="store_true",
        help="요약을 만들어 로그에만 찍고 보내지는 않는다 (형식을 확인할 때)",
    )
    parser.add_argument("--sheet", action="store_true", help="낸 전망을 구글 시트에 덧붙인다")
    args = parser.parse_args()

    쓴것: list[str] = []
    낸전망: list = []

    def emit(글: str = "") -> None:
        print(글)
        쓴것.append(글)

    source = YahooFinanceDataSource()
    cache = PriceCache()
    오늘 = datetime.now(KST).date()
    기준일 = date.fromisoformat(args.as_of) if args.as_of else None

    쓰는것 = 시계열.lens_series(args.lens)
    시작 = date(1990, 1, 1)
    print(f"■ 렌즈 '{args.lens}' — {', '.join(s.이름 for s in 쓰는것)}", file=sys.stderr)
    바깥 = 시계열.load([s.키 for s in 쓰는것], 시작, 오늘, source=source, cache=cache)
    for 키, df in 바깥.items():
        print(f"  {시계열.SERIES[키].이름}: {len(df)}일 ({df['trade_date'].min()} ~)", file=sys.stderr)

    상태 = build_state(바깥)
    # z점수는 '흔한 일인가'를 판단하는 데만 쓰고, 사람에게 보여 줄 때는 원래
    # 단위(고점에서 몇 % 빠졌나)를 그대로 쓴다. z점수를 퍼센트로 읽은 사고가
    # 있었고, 그렇다고 숫자를 다 지웠더니 이번엔 "얼마나 깊이?"에 답을 못 했다.
    원시 = raw_indicators(바깥)
    emit(f"■ 장 상태를 잰 날 {len(상태)}일 ({상태.index[0]} ~ {상태.index[-1]})")
    emit(f"  렌즈: {args.lens} — {시계열.LENSES[args.lens][2]}")
    emit()
    emit(describe_today(상태 if 기준일 is None else 상태.loc[:기준일], 원시))
    emit()

    # ── 시장 전체 전망 ────────────────────────────────────────────
    코스피 = 바깥["kospi"].set_index("trade_date")["close"].astype(float)
    시장전망 = forecast(
        상태, 코스피, "코스피 전체", 기준일=기준일, top_pct=args.top_pct, horizon=args.horizon
    )
    낸전망.append(시장전망)
    emit(format_forecast(시장전망))
    emit()

    # ── 섹터 지수 ────────────────────────────────────────────────
    print("■ 섹터 종목 시세 받는 중…", file=sys.stderr)
    섹터시세 = _섹터시세(source, cache, 시작, 오늘)
    지수들 = {코드: build_index(모음) for 코드, 모음 in 섹터시세.items()}
    emit(coverage_report(지수들))
    emit()

    # ── 섹터별 전망 ──────────────────────────────────────────────
    emit("■ 섹터별 전망")
    emit()
    낸것, 못낸것 = 0, []
    for sector in CATALOG:
        if not sector.활성:
            continue
        if sector.전망출처 == "국제시세":
            for 심볼, 이름 in 국제시세.get(sector.코드, []):
                try:
                    df = cache.fetch(source, 심볼, 심볼, 시작, 오늘)
                except (requests.RequestException, ValueError, KeyError):
                    못낸것.append(f"{sector.이름}/{이름}: 시세를 못 받음")
                    continue
                가격 = df.set_index("trade_date")["close"].astype(float)
                f = forecast(상태, 가격, f"{sector.이름} — {이름}", 기준일=기준일,
                             top_pct=args.top_pct, horizon=args.horizon)
                낸전망.append(f)
                emit(format_forecast(f))
                emit()
                낸것 += 1 if f.낼수있나 else 0
                if not f.낼수있나:
                    못낸것.append(f"{sector.이름}/{이름}: {f.사유}")
            continue

        지수 = 지수들.get(sector.코드)
        if 지수 is None or len(지수) == 0:
            못낸것.append(f"{sector.이름}: 지수를 못 만듦(종목 {len(섹터시세.get(sector.코드, {}))}개)")
            continue
        f = forecast(상태, 지수["close"], sector.이름, 기준일=기준일,
                     top_pct=args.top_pct, horizon=args.horizon)
        낸전망.append(f)
        emit(format_forecast(f))
        # 시장 대비 강도는 생존편향에 덜 민감하다 — 같이 보여 준다.
        강도 = relative_strength(지수["close"], 코스피).dropna()
        if len(강도):
            끝 = 강도.index[-1] if 기준일 is None else max(d for d in 강도.index if d <= 기준일)
            emit(f"    (참고) 최근 20일 시장 대비 강도 {강도[끝] * 100:>+.1f}%p")
        emit()
        낸것 += 1 if f.낼수있나 else 0
        if not f.낼수있나:
            못낸것.append(f"{sector.이름}: {f.사유}")

    emit(f"■ 전망을 낸 섹터 {낸것}개")
    if 못낸것:
        emit(f"  못 낸 것 {len(못낸것)}개 — **이유가 있어야 다음에 무엇을 고칠지 안다**")
        for 줄 in 못낸것:
            emit(f"    · {줄}")
    emit()

    # ── 전망을 쌓고, 지평이 지난 것에는 실제 결과를 채운다 ────────────
    #
    # **전망을 내놓고 결과를 안 남기면 전망이 쓸모없다는 것도 영영 모른다.**
    if not args.no_log:
        가격표 = {"코스피 전체": 코스피}
        for sector in CATALOG:
            지수 = 지수들.get(sector.코드)
            if 지수 is not None and len(지수):
                가격표[sector.이름] = 지수["close"]

        줄들 = [forecast_log.row_from_forecast(f, 렌즈=args.lens) for f in 낸전망]
        forecast_log.save(줄들)

        def 실제결과(대상: str, 낸날: date, 지평: int):
            """지평이 지났으면 그때 실제로 어떻게 됐는지. 아직이면 None."""
            가격 = 가격표.get(대상)
            if 가격 is None or 낸날 not in 가격.index:
                return None
            i = 가격.index.get_loc(낸날)
            j = i + 지평
            if j >= len(가격):
                return None
            앞 = float(가격.iloc[i])
            return (float(가격.iloc[j]) / 앞 - 1) * 100 if 앞 > 0 else None

        채운수 = forecast_log.fill_actuals(실제결과, today=오늘)
        전체 = forecast_log.load()
        emit(f"■ 전망 기록 — {len(줄들)}줄 저장, {채운수}줄에 실제 결과를 채움 (누적 {len(전체)}줄)")
        emit()
        emit(forecast_log.format_scorecard(forecast_log.score(전체)))
        emit()

    emit("※ 이 리포트는 아무것도 사지 않습니다. 보는 것만 합니다.")
    emit("※ '아주 나빴을 때' 칸이 비중을 정하는 자리입니다 — 감이 아니라 과거가 정합니다.")

    # ── 텔레그램 요약 ────────────────────────────────────────────
    #
    # 전문은 200줄이 넘는다. 그대로 보내면 아무도 안 읽고, 안 읽는 알림은
    # 진짜 중요한 알림을 묻는다. 그래서 한 통짜리로 줄여 보낸다.
    못보냄 = ""
    보낼요약 = ""
    if args.telegram:
        from muwon.market.digest import summarize
        from muwon.notify.telegram import TelegramNotifier
        from muwon.settings.service import build_settings_service

        요약 = summarize(
            상태 if 기준일 is None else 상태.loc[:기준일],
            낸전망,
            기준일 or (상태.index[-1] if len(상태) else 오늘),
            렌즈=args.lens,
            원시=원시 if 기준일 is None else 원시.loc[:기준일],
            지수=코스피 if 기준일 is None else 코스피.loc[:기준일],
            한일=_오늘한일(기준일 or 오늘),
        )
        # 요약은 맨 마지막에 찍는다(아래 참조). stderr로 찍었더니 stdout이
        # 블록 버퍼링이라 로그에서 리포트 본문보다 한참 위에 붙었고,
        # 확인하려면 로그를 통째로 읽어야 했다.
        보낼요약 = 요약
        # 여기서 return 하지 않는다. 아래에 리포트 파일 쓰기와 시트 올리기가
        # 남아 있어서, 일찍 끊으면 --no-send가 그 둘까지 조용히 건너뛴다.
        # 실제로 그렇게 만들었다가 아티팩트가 안 올라갔다.
        if args.no_send:
            print("--no-send라 실제로 보내지는 않았습니다.", file=sys.stderr)
        else:
            try:
                TelegramNotifier(build_settings_service()).send(요약)
                print("\n텔레그램으로 요약을 보냈습니다.", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 — 알림 실패가 리포트를 죽이면 안 된다
                # 리포트 자체는 끝까지 만든다. 다만 **조용히 넘어가지는 않는다** —
                # 2026-08-19~24에 여기서 여덟 번 내리 실패했는데 워크플로는 여덟 번
                # 다 초록불이었고, 그동안 리포트가 폰에 한 번도 안 왔다.
                # 조용히 성공한 척하는 실패가 이 저장소에서 제일 비싼 종류다.
                못보냄 = f"{type(e).__name__}: {e}"
                print(f"\n텔레그램 전송 실패: {못보냄}", file=sys.stderr)

    # ── 전망을 시트에도 남긴다 ────────────────────────────────────
    #
    # DB는 폰에서 못 본다. 시트에 같은 것을 덧붙여 두면 대시보드를 켜지
    # 않고도 "오늘 뭐라고 전망했나"를 볼 수 있다. 열쇠가 (낸날·대상·지평)
    # 이라 **여러 번 돌려도 줄이 늘지 않는다.**
    if args.sheet:
        import os

        from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create
        from muwon.cloud.sheet_log import append, forecast_rows, 전망머리

        try:
            sheet_id = os.environ.get("MUWON_SHEET_ID", "")
            if not sheet_id:
                sheet_id, _ = find_or_create(os.environ["GDRIVE_FOLDER_ID"], DEFAULT_TITLE)
            올린수 = append(sheet_id, "전망기록", 전망머리, forecast_rows(낸전망))
            print(f"\n시트에 전망 {올린수}줄을 덧붙였습니다.", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 기록 실패가 리포트를 죽이면 안 된다
            print(f"\n시트 기록 실패: {type(e).__name__}: {e}", file=sys.stderr)

    if args.out:
        Path(args.out).write_text("\n".join(쓴것) + "\n", encoding="utf-8")
        print(f"\n결과를 {args.out}에 남겼습니다.", file=sys.stderr)

    # **보내든 못 보내든 요약을 남긴다.** 폰으로 못 받은 날일수록 여기서
    # 읽을 수 있어야 하고, 형식을 고친 뒤에 실제로 어떻게 나가는지 보려고
    # 알림을 한 통 더 보낼 이유도 없다. 맨 마지막에 두는 이유는 로그를
    # 끝에서부터 조금만 읽어도 보이게 하기 위해서다.
    if 보낼요약:
        print("\n─── 텔레그램으로 간 요약 ───")
        print(보낼요약)
        print("───")

    if 못보냄:
        # 리포트는 다 만들었고 파일·아티팩트로도 남겼다. 그래도 실패로
        # 끝낸다 — 보내라고 했는데 못 보냈으면 그건 실패다.
        print(f"\n❌ 요약을 텔레그램으로 보내지 못했습니다: {못보냄}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
