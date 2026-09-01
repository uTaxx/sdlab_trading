"""장이 열려 있는 동안 손절선을 지켜본다.

## 왜 필요한가

하루 한 번 도는 엔진은 09:05에만 값을 본다. 그 뒤로 다음 날 09:05까지는
아무도 안 본다. 그 사이에 20% 빠져도 그날은 아무 일도 안 일어나고, 다음
날 아침에야 팔린다.

손절은 "지금 이 값에 팔아야 하나"를 묻는 것이라, 하루에 한 번 묻는 것으로는
답이 안 된다. 이 스크립트를 장중에 여러 번 불러서 그 물음을 계속 묻는다.

## 손절만 본다

**사는 판단은 여기서 안 한다.** 사는 조건은 "거래량이 20일 평균의 2배"처럼
하루치 거래량을 다 세야 성립하는 것들이라, 장중에는 그 값이 아직 안 찼다.
미완성 봉으로 사면 오후에 조건이 깨질 종목을 아침에 사게 된다.

**보유 기간 만료와 전략 매도 신호도 여기서 안 본다.** 둘 다 완성된 일봉을
보고 판단하는 것이라 09:05 회차의 몫이다. 여기서 보는 것은 손절과 트레일링
스톱뿐이다. 값이 지금 얼마인지만 알면 판단할 수 있는 것들이다.

## 두 번 팔지 않기 위해

이 스크립트는 상태 DB를 고친다(보유를 지우고 매매를 기록한다). 그래서
같은 DB를 고치는 다른 실행과 겹치면 안 된다. 워크플로에서 매수 실행과
같은 concurrency 그룹에 묶어 두었다.

한 번 더 막는다. 팔기 전에 **증권사 잔고에 그 종목이 실제로 있는지** 본다.
우리 기록에는 있는데 증권사에는 없으면 이미 팔린 것이므로 주문을 내지
않는다.

## 장이 닫혀 있으면 아무 일도 안 한다

5분마다 부르면 밤에도 불린다. 장 시간이 아니면 그 사실만 찍고 0으로 끝난다.
실패가 아니므로 빨간불이 되면 안 된다.

사용 예:
    python scripts/watch_stops.py            # 지금 값으로 손절선만 점검
    python scripts/watch_stops.py --dry-run  # 팔지 않고 판단만 보여 준다
"""

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.db.models import PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.domain.types import OrderSide
from muwon.execution import state_repository
from muwon.execution.engine import KST, today_kst, 매도알림
from muwon.execution.kis_order_executor import KISOrderExecutor
from muwon.notify.telegram import TelegramNotifier
from muwon.risk.exits import atr_series, evaluate_exit
from muwon.settings.service import build_settings_service

#: 장 시간. 15:20에 멈추는 이유는 동시호가(15:20~15:30) 때문이다. 그 구간의
#: 시장가 주문은 예상체결가로 접수돼 우리가 본 값과 다르게 체결된다.
장열림 = time(9, 0)
장닫힘 = time(15, 20)


def 장중인가(지금: datetime) -> tuple[bool, str]:
    """(도는가, 이유). 이유는 안 돌 때만 쓴다."""
    if 지금.weekday() >= 5:
        return False, "주말입니다."
    if 지금.time() < 장열림:
        return False, f"아직 장 전입니다 ({지금:%H:%M} < {장열림:%H:%M})."
    if 지금.time() > 장닫힘:
        return False, f"장이 끝났습니다 ({지금:%H:%M} > {장닫힘:%H:%M})."
    return True, ""


def _일봉이필요한가(정책) -> bool:
    """고정 비율 손절만 켜져 있으면 지금 값 하나로 판단이 끝난다."""
    return bool(
        getattr(정책, "atr_stop_enabled", False)
        or getattr(정책, "trailing_stop_enabled", False)
    )


def _일봉모으기(종목들: list[str], 정책) -> dict:
    """ATR·트레일링에 쓸 일봉. 못 받으면 빈 사전을 돌려준다.

    시세 캐시를 쓴다. 5분마다 도는 자리라 같은 일봉을 하루에 수십 번 새로
    받으면 그만큼 시간과 요청을 버린다."""
    from datetime import timedelta

    from muwon.analysis.market_data import load_histories
    from muwon.data.price_cache import PriceCache
    from muwon.data.universe import UNIVERSE
    from muwon.data.yahoo_client import YahooFinanceDataSource

    표 = {t.symbol: t for t in UNIVERSE}
    받을것 = [표[ㅅ] for ㅅ in 종목들 if ㅅ in 표]
    if not 받을것:
        return {}
    끝 = today_kst()
    시작 = 끝 - timedelta(days=max(정책.atr_window * 4, 120))
    try:
        나온것 = load_histories(
            YahooFinanceDataSource(), 받을것, 시작, 끝, cache=PriceCache()
        )
    except Exception as e:  # noqa: BLE001 (일봉을 못 받아도 손절은 걸려야 한다)
        print(f"⚠ 일봉을 못 받았습니다: {type(e).__name__}: {e}")
        return {}
    return dict(_종목코드로(나온것, 받을것))


def _종목코드로(나온것: dict, 받을것: list):
    """load_histories가 야후 기호로 돌려주면 종목코드로 되돌린다."""
    for t in 받을것:
        for 열쇠 in (t.symbol, getattr(t, "yahoo_symbol", "")):
            if 열쇠 and 열쇠 in 나온것:
                yield t.symbol, 나온것[열쇠]
                break


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="팔지 않고 판단만 보여 준다")
    parser.add_argument(
        "--ignore-clock", action="store_true",
        help="장 시간이 아니어도 판단만 해 본다. 이 경우 주문은 절대 안 나간다",
    )
    parser.add_argument(
        "--changed-flag", default="db-changed",
        help="상태 DB를 고쳤을 때 만들 표시 파일. 워크플로가 이걸 보고 올릴지 정한다",
    )
    args = parser.parse_args()

    # 어느 쪽으로 도는지 맨 위에 찍는다. 초록불인데 아무것도 안 한 실행이
    # 이 저장소에서 제일 비싼 실패였다.
    print("■ 모드: " + ("판단만(--dry-run)" if args.dry_run else "손절선에 닿으면 실제로 판다"))

    지금 = datetime.now(KST)
    돌까, 까닭 = 장중인가(지금)
    print(f"■ 한국시간 {지금:%Y-%m-%d %H:%M}")
    if not 돌까:
        if not args.ignore_clock:
            print(f"■ {까닭} 아무것도 안 합니다.")
            return 0
        print(f"■ {까닭} --ignore-clock이라 판단만 해 봅니다. 주문은 안 냅니다.")
        args.dry_run = True

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if creds.is_real:
        # 다른 스크립트와 같은 자리에 같은 문장으로 막는다.
        raise SystemExit("kis.env가 'real'입니다. 이 스크립트는 모의투자에서만 돕니다.")
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    정책 = service.get_risk_policy()
    if not 정책.sell_enabled:
        # 조용히 넘어가지 않는다. 손절이 멈춰 있다는 것은 알아야 할 일이다.
        print("🛑 매도 스위치가 꺼져 있습니다. 손절이 걸리지 않습니다.")
        return 0

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)
    with session_factory() as session:
        보유 = list(session.scalars(select(PositionRow).order_by(PositionRow.symbol)))
    if not 보유:
        print("■ 들고 있는 종목이 없습니다.")
        return 0

    client = KISClient.from_settings(service)
    잔고 = client.get_balance()
    증권사 = {h.symbol: h for h in 잔고.holdings}

    손절선글 = (
        f"변동성(ATR {정책.atr_window}일 × {정책.atr_stop_multiple:g})"
        if 정책.atr_stop_enabled
        else f"{정책.stop_loss_pct:.0%}"
    )
    print(f"■ 보유 {len(보유)}종목 · 손절 기준 {손절선글}")

    # ATR 손절이나 트레일링을 켜 뒀으면 일봉이 있어야 판단이 된다. 안 주면
    # evaluate_exit가 고정 % 손절로 되돌아가는데, 그건 09:05 회차와 **다른
    # 규칙으로 파는 것**이다. 조용히 다르게 파는 것이 제일 나쁘다.
    #
    # 들고 있는 종목만 받으므로 많아야 여덟 개다(동시보유 상한).
    일봉 = _일봉모으기([p.symbol for p in 보유], 정책) if _일봉이필요한가(정책) else {}
    if _일봉이필요한가(정책) and not 일봉:
        print("⚠ 일봉을 못 받아서 이번에는 고정 비율 손절로만 봅니다.")
        print("  09:05 회차와 다른 규칙입니다. 이 줄이 계속 나오면 고쳐야 합니다.")

    executor = KISOrderExecutor(client)
    notifier = TelegramNotifier(service)
    오늘 = today_kst()
    판것 = 0
    못본것 = 0

    for p in 보유:
        가진것 = 증권사.get(p.symbol)
        if 가진것 is None:
            # 우리 기록에는 있는데 증권사에는 없다. 이미 팔린 것이다.
            # 여기서 주문을 내면 없는 것을 판다. drop_phantom.py가 정리한다.
            print(f"  {p.symbol}: 증권사 잔고에 없습니다. 건드리지 않습니다.")
            못본것 += 1
            continue
        지금값 = float(가진것.current_price or 0)
        if 지금값 <= 0:
            print(f"  {p.symbol}: 지금 값을 못 읽었습니다. 이번에는 건너뜁니다.")
            못본것 += 1
            continue

        # 09:05 회차와 같은 규칙으로 판단한다. 일봉은 ATR·트레일링을 켜
        # 뒀을 때만 있고, 고정 비율 손절만 켜져 있으면 None이라 안 쓴다.
        기록 = 일봉.get(p.symbol)
        판정 = evaluate_exit(
            entry_price=p.entry_price,
            entry_date=p.entry_date,
            current_price=지금값,
            as_of=오늘,
            policy=정책,
            atr=atr_series(기록, 정책.atr_window) if 기록 is not None else None,
            history=기록,
        )
        수익률 = 지금값 / p.entry_price - 1 if p.entry_price else 0.0
        표시 = f"  {p.symbol} {p.quantity:,}주 · 산 값 {p.entry_price:,.0f}원 · 지금 {지금값:,.0f}원 ({수익률:+.1%})"
        if not 판정.should_exit:
            print(f"{표시} → 그대로")
            continue

        print(f"{표시} → {판정.reason}")
        if args.dry_run:
            print("      (판단만 하는 모드라 주문은 안 냅니다)")
            continue

        # 증권사에 실제로 있는 수량만큼만 판다. 우리 기록이 더 많으면
        # 없는 것을 파는 주문이 되어 거부당한다.
        수량 = min(p.quantity, 가진것.quantity)
        order = executor.submit_order(p.symbol, OrderSide.SELL, 수량, 지금값)
        state_repository.record_order(session_factory, order, 판정.reason)
        state_repository.record_trade(session_factory, p, order, 판정.reason)
        state_repository.delete_position(session_factory, p.symbol)
        판것 += 1
        notifier.send(
            매도알림(
                가진것.name or p.symbol, p.symbol, order, 판정.reason,
                진입가=p.entry_price, 진입일=p.entry_date, 판날=오늘, 장중=True,
            )
        )

    print(f"\n■ 판 것 {판것}건 · 못 본 것 {못본것}건")
    if 판것:
        print("  판 만큼 현금이 늘었습니다. 다음 회차가 계좌를 다시 읽어 맞춥니다.")
        # 상태 DB를 고쳤을 때만 표시를 남긴다. 워크플로가 이 파일을 보고
        # 올릴지 정한다. 하루에 수십 번 도는 자리라, 안 바뀐 DB를 매번
        # 올리면 그 사이 다른 실행이 쓴 것을 옛 것으로 덮을 수 있다.
        Path(args.changed_flag).write_text("1", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
