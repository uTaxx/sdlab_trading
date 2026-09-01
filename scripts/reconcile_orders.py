"""**우리 주문 기록이 증권사 기록과 같은지 기간으로 대조한다.**

## 왜

이 저장소의 전략 평가 결과: 승률(이긴 거래의 비율), 손익비(이긴 거래의 평균 이익을
진 거래의 평균 손실로 나눈 값), 슬리피지(판단한 값과 실제로 사진 값의 차이)
: 는 전부 `orders` 표 위에서 계산된다. **그 표가 틀리면 그 위의 숫자가 전부
틀리고, 틀렸다는 사실조차 안 보인다.**

2026-08-25에 둘 다 일어났다. 12주가 체결됐는데 기록엔 4주였고, 흉내 실행이
사지도 않은 두 종목을 기록에 써 넣었다. 우리 기록만 봐서는 알 수 없다.
증권사 기록과 나란히 놓아야 보인다.

`settle_fills`는 그날 것만 맞춘다. 여기는 기간을 훑는다.

## 무엇을 보여주나

    ✅ 맞음. 손댈 것 없음
    ⚠️ 어긋남: 양쪽에 있는데 수량이나 체결가가 다름 (증권사가 사실)
    👻 우리만: 기록엔 있는데 증권사엔 없음. 그 주식은 실제로 없다
    🕳️ 증권사만: 증권사엔 체결이 있는데 우리 기록엔 없음.
                     **그 주식에는 손절이 안 걸린다**. 제일 위험한 쪽이다
    ? 대조불가. 주문번호가 없어 짝을 못 찾음 (흉내 실행이 남긴 것)

## 고치는 범위

`--apply`는 **어긋남만** 고친다(수량·체결가를 증권사 값으로).

- **우리만**은 안 지운다. 지우는 판단에는 "방금 산 게 아직 안 잡힌 것"과
  구별이 필요하다. `drop_phantom_holdings.py`가 그 일을 한다.
- **증권사만**은 안 들인다. 들이려면 진입일과 사유를 사람이 줘야 한다.
  `adopt_holdings.py`가 그 일을 한다.

여기는 **이미 짝이 있는 기록을 바로잡는 데까지**만 한다. 없는 것을 만들거나
있는 것을 지우는 판단은 각자의 도구에 맡긴다.

## 왜 "기간별 매매손익"(TTTC8715R)을 안 쓰나

증권사가 계산한 실현손익을 그대로 받아 오면 제일 깔끔하다. **모의투자
계좌에는 그 API가 없다.** 그래서 한 겹 아래인 주문·체결 원본에서 맞춘다.

사용 예:
    python scripts/reconcile_orders.py                    # 최근 30일 미리보기
    python scripts/reconcile_orders.py --days 90
    python scripts/reconcile_orders.py --from 2026-08-01 --to 2026-08-25
    python scripts/reconcile_orders.py --days 30 --apply  # 어긋남을 고친다
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.db.models import OrderRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.execution import state_repository
from muwon.execution.order_audit import 대조하기, 우리주문, 파싱
from muwon.settings.service import build_settings_service

KST = ZoneInfo("Asia/Seoul")

# 주문체결조회(VTTC0081R/TTTC0081R)는 3개월 이내만 본다. 그 밖은 다른
# TR_ID(VTSC9215R)가 필요한데, 아직 붙이지 않았다.
_최대일수 = 90


def 우리기록(session_factory, 시작: date, 끝: date) -> list[우리주문]:
    """기간 안의 우리 주문 기록. created_at은 UTC라 KST 날짜로 비교한다."""
    # 경계는 넉넉히 잡아 DB에서 긁고, 정확한 자르기는 KST 날짜로 파이썬에서 한다.
    처음 = datetime.combine(시작, datetime.min.time()) - timedelta(days=1)
    마지막 = datetime.combine(끝, datetime.max.time()) + timedelta(days=1)
    with session_factory() as session:
        rows = session.scalars(
            select(OrderRow)
            .where(OrderRow.created_at >= 처음, OrderRow.created_at <= 마지막)
            .order_by(OrderRow.created_at)
        ).all()

    나온것 = []
    for r in rows:
        그날 = (r.created_at + timedelta(hours=9)).date()
        if not (시작 <= 그날 <= 끝):
            continue
        나온것.append(
            우리주문(
                row_id=r.id,
                order_id=r.kis_order_id or "",
                symbol=r.symbol,
                side=r.side,
                quantity=r.quantity,
                price=float(r.price),
                when=r.created_at + timedelta(hours=9),
                reference_price=r.reference_price,
                fill_confirmed=r.fill_confirmed,
            )
        )
    return 나온것


def main() -> int:
    parser = argparse.ArgumentParser(description="우리 주문 기록을 증권사 기록과 대조한다")
    parser.add_argument("--days", type=int, default=30, help="최근 며칠을 볼까 (기본 30, 최대 90)")
    parser.add_argument("--from", dest="시작", default="", help="시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="끝", default="", help="종료일 YYYY-MM-DD")
    parser.add_argument(
        "--apply", action="store_true", help="어긋난 기록을 증권사 값으로 고친다"
    )
    args = parser.parse_args()

    오늘 = datetime.now(KST).date()
    끝 = date.fromisoformat(args.끝) if args.끝 else 오늘
    시작 = date.fromisoformat(args.시작) if args.시작 else 끝 - timedelta(days=args.days - 1)
    if 시작 > 끝:
        raise SystemExit(f"❌ 시작일({시작})이 종료일({끝})보다 뒤입니다.")
    if (끝 - 시작).days + 1 > _최대일수:
        raise SystemExit(
            f"❌ 주문체결조회는 3개월 이내만 봅니다({_최대일수}일). "
            f"지금 {(끝 - 시작).days + 1}일을 요청했습니다. 나눠서 부르세요."
        )

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)

    print("=== 주문 기록 대조 ===")
    print(f"계좌 {creds.account_no[:4]}**** · {'실거래' if creds.is_real else '모의투자'}")
    print(f"기간 {시작} ~ {끝} ({(끝 - 시작).days + 1}일)")
    print()

    행들 = KISClient.from_settings(service).get_orders_between(시작, 끝)
    if 행들 is None:
        raise SystemExit("❌ 증권사 주문체결조회를 거부당했습니다. 대조할 수 없습니다.")

    증권사것 = 파싱(행들)
    우리것 = 우리기록(session_factory, 시작, 끝)
    # 보유를 같이 봐야 "놓친 체결"이 위험한지 아닌지를 가를 수 있다.
    # 손절은 보유를 보고 걸리지 주문 기록을 보고 걸리지 않는다.
    보유 = frozenset(state_repository.load_positions(session_factory))
    결과 = 대조하기(우리것, 증권사것, 보유)

    print(f"우리 기록 {len(우리것)}건 · 증권사 체결 {sum(1 for o in 증권사것 if o.체결됐나)}건")
    print(f"(증권사 주문 {len(증권사것)}건 중 {결과.체결없음}건은 체결 0: 취소·미체결이라 대조 대상이 아닙니다)")
    print()

    print(f"✅ 맞음 {len(결과.맞음)}건")
    if 결과.대조불가:
        print(f"?  대조불가 {len(결과.대조불가)}건: 주문번호가 없어 짝을 못 찾았습니다(흉내 실행이 남긴 것)")
        for 우리 in 결과.대조불가:
            print(f"     {우리.한줄}")
    print()

    if 결과.어긋남:
        print(f"⚠️ 어긋남 {len(결과.어긋남)}건: 증권사가 사실입니다")
        for 짝 in 결과.어긋남:
            print(f"   {짝.한줄}")
        print("   이 기록 위에서 계산된 승률·손익비·슬리피지가 전부 틀려 있었습니다.")
        print()

    if 결과.우리만:
        print(f"👻 우리 기록에만 있음 {len(결과.우리만)}건: 그 주식은 실제로 없습니다")
        for 우리 in 결과.우리만:
            print(f"   {우리.한줄} [주문 {우리.order_id}]")
        print("   → 보유까지 유령이면 `drop_phantom_holdings.py --symbol …`로 지웁니다.")
        print("     여기서 안 지웁니다. '방금 산 게 아직 안 잡힌 것'과 구별이 필요합니다.")
        print()

    if 결과.증권사만:
        print(f"🕳️ 증권사엔 체결이 있는데 우리 주문 기록엔 없음 {len(결과.증권사만)}건")
        for ㄴ in 결과.증권사만:
            표 = "🔴" if ㄴ.위험한가 else "·"
            print(f"   {표} {ㄴ.주문.한줄} [주문 {ㄴ.주문.order_id}]")
            print(f"      {ㄴ.뜻}")
        if 결과.위험한것:
            print()
            print(f"   → 위험한 것 {len(결과.위험한것)}건은 `adopt_holdings.py`로 들이세요.")
            print("     진입일과 사유를 사람이 줘야 해서 여기서 안 합니다.")
        print("   → 나머지는 전략 평가 결과에서만 빠집니다. 주문 기록을 되살리는 길은 아직 없습니다")
        print("     (증권사 값만으로는 '무슨 근거로 샀나'를 복원할 수 없습니다).")
        print()

    if not 결과.문제있나:
        print("✅ 어긋난 것이 없습니다. 이 기간의 전략 평가 결과는 증권사 기록 위에 서 있습니다.")
        return 0

    if not args.apply:
        print("미리보기입니다. 아무것도 쓰지 않았습니다.")
        if 결과.어긋남:
            print("어긋난 기록을 고치려면 --apply 를 붙여 다시 실행하세요.")
        return 1

    if not 결과.어긋남:
        print("--apply를 줬지만 고칠 '어긋남'이 없습니다. 나머지는 각자의 도구가 할 일입니다.")
        return 1

    with session_factory() as session:
        for 짝 in 결과.어긋남:
            row = session.get(OrderRow, 짝.우리.row_id)
            if row is None:
                continue
            row.quantity = 짝.증권사.filled_quantity
            row.price = 짝.증권사.avg_price
            # 증권사가 준 값이니 이제 '확인된 체결'이다. 이 표시가 있어야
            # 슬리피지 통계가 이 표본을 센다.
            row.fill_confirmed = True
        session.commit()

    print(f"✅ 어긋난 주문 {len(결과.어긋남)}건을 증권사 값으로 고쳤습니다.")
    print("   보유 수량까지 맞추려면 `settle_fills.py --apply`(그날) 또는 계좌 대조를 보세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
