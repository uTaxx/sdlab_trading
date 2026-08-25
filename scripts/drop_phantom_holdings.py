"""**기록에만 있고 계좌엔 없는 보유를 지운다.** `adopt_holdings.py`의 반대다.

## 언제 쓰나

계좌 대조(`state-check`)가 이렇게 말할 때다:

    ⚠️ DB 기록과 실제 계좌가 어긋났습니다.
      - 066970: DB엔 12주인데 계좌엔 없음

그대로 두면 엔진이 그 종목을 **이미 보유로 보고 안 사거나**, 유령 포지션에
손절이 걸려 **없는 주식에 매도 주문**을 낸다.

## 지울 종목은 사람이 준다

"계좌에 없으면 다 지운다"로 만들지 않았다. 계좌에 없는 이유가 셋인데
(유령 / 이미 팔림 / **방금 산 게 아직 안 잡힘**) 겉모습이 같아서 기계가
못 가른다. 셋째를 지우면 그 주식은 아무도 안 지키는 채로 남는다.

그래서 `--symbol`로 하나씩 이름을 줘야 하고, **계좌에 실제로 있는 종목은
이름을 줘도 거부한다.** 사람과 계좌가 다투면 계좌가 이긴다.

## 같이 맞추는 것

- **현금** — 유령 매수가 빼 간 돈을 계좌 값으로 되돌린다. 안 되돌리면
  엔진이 없는 돈을 없다고 믿고 그 위에서 비중 상한을 계산한다.
- **기준평가금** — 일일 손실한도가 '오늘 얼마나 잃었나'를 재는 기준점.
  안 맞추면 지우는 순간 손실이 난 것처럼 보여 그날 매수가 전부 막힌다.
- **유령 주문 기록** — `--with-orders`를 주면 그 종목의 오늘 주문도 지운다.
  이 저장소는 주문의 `price`와 `reference_price`로 슬리피지를 재는데,
  흉내 낸 체결이 섞이면 **차이 0인 가짜 표본**이 통계를 눌러 버린다.

## 안전장치

- 미리보기가 기본이다. `--apply` 없이는 **아무것도 쓰지 않는다.**
- 실거래 계좌면 실행을 거부한다.

사용 예:
    python scripts/drop_phantom_holdings.py --symbol 066970 --symbol 411060
    python scripts/drop_phantom_holdings.py --symbol 066970 --apply --with-orders
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.db.models import OrderRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.execution import state_repository
from muwon.execution.phantom import plan, 맞출평가금
from muwon.settings.service import build_settings_service

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser(description="기록에만 있고 계좌엔 없는 보유를 지운다")
    parser.add_argument(
        "--symbol", action="append", default=[], required=True,
        help="지울 종목코드. 여러 번 줄 수 있다. 계좌에 실제로 있으면 거부한다.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="실제로 DB에 쓴다. 없으면 미리보기만 한다."
    )
    parser.add_argument(
        "--with-orders", action="store_true",
        help="그 종목의 최근 주문 기록도 지운다 (흉내 낸 체결이 슬리피지 통계를 오염시킨다)",
    )
    parser.add_argument(
        "--orders-since-hours", type=int, default=24,
        help="--with-orders가 지울 범위. 기본 24시간 — 옛 진짜 주문까지 지우면 안 된다.",
    )
    args = parser.parse_args()

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if creds.is_real:
        raise SystemExit("❌ KIS 환경이 실거래(real)입니다. 이 스크립트는 모의투자 전용입니다.")
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)

    잔고 = KISClient.from_settings(service).get_balance()
    보유 = state_repository.load_positions(session_factory)
    현금, 기준평가금 = state_repository.load_engine_state(session_factory, 10_000_000.0)

    계획 = plan(args.symbol, 보유, 잔고.holdings)

    print("=== 기록에만 있는 보유 지우기 ===")
    print(f"계좌 {creds.account_no[:4]}**** · 계좌 보유 {len(잔고.holdings)}종목 / DB 기록 {len(보유)}종목")
    print()

    for h in 계획.계좌에있어서거부:
        print(f"🛑 {h.name}({h.symbol})는 **계좌에 실제로 {h.quantity}주 있습니다.** 안 지웁니다.")
        print("   유령이 아닙니다. 지우면 그 주식은 손절이 안 걸린 채로 남습니다.")
    for symbol in 계획.이미없음:
        print(f"· {symbol}는 DB에도 없습니다 — 지울 것이 없습니다.")
    if 계획.계좌에있어서거부 or 계획.이미없음:
        print()

    if not 계획.할일있나:
        print("지울 것이 없습니다.")
        return 1 if 계획.막힌게있나 else 0

    지울심볼 = [p.symbol for p in 계획.지울것]
    유령값 = 0.0
    for pos in 계획.지울것:
        원가 = pos.quantity * pos.entry_price
        유령값 += 원가
        print(f"■ {pos.symbol} — DB엔 {pos.quantity}주, 계좌엔 없음")
        print(f"   진입가 {pos.entry_price:,.0f}원 · 원가 {원가:,.0f}원 · 진입일 {pos.entry_date}")
        print(f"   기록된 사유: {pos.entry_reason}")
        print()

    새현금 = 잔고.cash
    새평가금 = 맞출평가금(새현금, 잔고.holdings)
    print("■ 엔진 상태도 같이 맞춥니다")
    print(f"   현금       {현금:,.0f}원 → {새현금:,.0f}원 ({새현금 - 현금:+,.0f}원)")
    print(f"   기준평가금 {기준평가금:,.0f}원 → {새평가금:,.0f}원")
    print(f"   (유령 보유의 원가 합계가 {유령값:,.0f}원이었습니다)")
    print()

    지울주문 = []
    if args.with_orders:
        자른때 = datetime.utcnow() - timedelta(hours=args.orders_since_hours)  # noqa: DTZ003
        with session_factory() as session:
            지울주문 = [
                (o.id, o.symbol, o.side, o.quantity, o.price, o.created_at)
                for o in session.query(OrderRow)
                .filter(OrderRow.symbol.in_(지울심볼), OrderRow.created_at >= 자른때)
                .all()
            ]
        print(f"■ 최근 {args.orders_since_hours}시간 안의 주문 기록 {len(지울주문)}건도 지웁니다")
        for _, symbol, side, qty, price, 때 in 지울주문:
            print(f"   {때:%Y-%m-%d %H:%M} {symbol} {side} {qty}주 @ {price:,.0f}원")
        print("   (흉내 낸 체결은 '결정가=체결가'라 슬리피지 통계에 차이 0인 가짜 표본이 됩니다)")
        print()

    if not args.apply:
        print("미리보기입니다 — 아무것도 쓰지 않았습니다.")
        print("실제로 지우려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    for pos in 계획.지울것:
        state_repository.delete_position(session_factory, pos.symbol)
    if 지울주문:
        with session_factory() as session:
            session.query(OrderRow).filter(OrderRow.id.in_([o[0] for o in 지울주문])).delete(
                synchronize_session=False
            )
            session.commit()
    state_repository.save_engine_state(session_factory, 새현금, 새평가금)

    print(f"✅ 보유 {len(계획.지울것)}건" + (f" · 주문 {len(지울주문)}건" if 지울주문 else "") + "을 지웠습니다.")
    print("   계좌 대조(state-check)를 다시 돌려 맞춰졌는지 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
