"""**장 마감 뒤 그날 주문의 최종 체결로 기록을 바로잡는다.**

    09:05  주문 → 그 순간의 스냅샷으로 기록 (부분 체결이면 틀린다)
    17:30  여기: 확정된 총체결수량·평균체결가로 고쳐 쓴다

## 왜 필요한가

주문 직후 체결 조회는 그 순간의 사진이다. 체결은 그 뒤에도 계속된다.
2026-08-25에 12주가 다 체결됐는데 기록에는 4주로 남았고, **그 8주에는
손절이 안 걸린 상태**였다.

부분 체결과 잔여 체결은 비일비재하다. 사고가 아니라 구조의 문제라, 사람이
매번 고치는 것이 아니라 매일 저절로 맞아야 한다.

## 무엇을 고치나

1. **주문 기록**. 체결수량·체결가를 최종값으로. 슬리피지(판단한 값과 실제로
   사진 값의 차이)를 이 두 값으로 재는데, 첫 조각 값이 표본이 되면 통계가
   통째로 기운다.
2. **보유 수량**. 계좌가 사실이다. 우리 기록을 계좌에 맞춘다.
3. **진입가**. 오늘 산 것이고 그날 매수가 한 번뿐이면 최종 평균가로 옮긴다.
   첫 조각 값이 남아 있으면 손절선이 엉뚱한 자리에 걸린다.
4. **현금**. 계좌의 가수도정산금액으로 맞춘다.

## 안 하는 것

**지우지 않는다.** 계좌에 없는 종목이 우리 기록에 있어도 그냥 둔다. 이미
팔린 것일 수도, 조회가 늦은 것일 수도 있다. 매일 자동으로 도는 자리에
지우는 권한까지 주면 위험하다. 그건 `drop_phantom_holdings.py`가 사람의
지시를 받아 한다.

**들이지 않는다.** 계좌에만 있는 종목은 알리기만 한다. 진입일을 사람이
줘야 해서 `adopt_holdings.py`의 몫이다.

사용 예:
    python scripts/settle_fills.py             # 미리보기
    python scripts/settle_fills.py --apply     # 실제로 고친다
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.db.models import OrderRow, PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.execution import state_repository
from muwon.execution.fill_settle import 보유맞추기, 주문맞추기
from muwon.notify import notice_format as 모양
from muwon.notify.telegram import TelegramNotifier
from muwon.settings.service import build_settings_service

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser(description="장 마감 뒤 최종 체결로 기록을 바로잡는다")
    parser.add_argument("--apply", action="store_true",
                        help="실제로 DB에 쓴다. 없으면 미리보기만 한다.")
    parser.add_argument("--date", default="",
                        help="맞출 날짜 YYYY-MM-DD (비우면 한국시간 오늘)")
    parser.add_argument("--quiet", action="store_true",
                        help="고칠 것이 없으면 텔레그램을 안 보낸다")
    args = parser.parse_args()

    # 날짜만 쓰므로 시간대가 없어도 된다. 사람이 "이 날짜로 맞춰라"라고
    # 주는 값이라 그 날짜 그대로가 맞다.
    오늘 = (
        date.fromisoformat(args.date) if args.date else datetime.now(KST).date()
    )

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if creds.is_real:
        raise SystemExit("❌ KIS 환경이 실거래(real)입니다. 이 스크립트는 모의투자 전용입니다.")
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)
    client = KISClient.from_settings(service)

    # 그날 낸 주문을 모은다. created_at은 UTC라 한국 날짜와 하루가 어긋날 수
    # 있어서, 넉넉히 이틀치를 훑고 주문 날짜로 다시 거른다.
    자른때 = datetime.utcnow() - timedelta(days=2)  # noqa: DTZ003 (created_at이 UTC다)
    with session_factory() as session:
        후보 = (
            session.query(OrderRow)
            .filter(OrderRow.created_at >= 자른때, OrderRow.kis_order_id != "")
            .all()
        )
        # 한국 날짜로 다시 거른다. created_at은 UTC라 09:05 주문이 전날로
        # 잡힌다. 그대로 두면 어제 주문까지 KIS에 물어보게 되고, 이 계정은
        # 호출을 자주 하면 403으로 막힌다(2026-08-25에 세 번 겪었다).
        줄들 = [
            (o.kis_order_id, o.symbol, o.quantity, o.price, o.reference_price or 0.0, o.side)
            for o in 후보
            if (o.created_at + timedelta(hours=9)).date() == 오늘
        ]

    print(f"=== 최종 체결로 기록 맞추기 ({오늘}) ===")
    print(f"계좌 {creds.account_no[:4]}**** · 살펴볼 주문 {len(줄들)}건")
    print()

    주문고침들 = 주문맞추기(줄들, lambda oid: client.get_fill(oid, 오늘))

    바뀌는주문 = [ㅈ for ㅈ in 주문고침들 if ㅈ.바뀌나]
    if 바뀌는주문:
        print("■ 주문 기록")
        for ㅈ in 바뀌는주문:
            print(f"  {ㅈ.symbol} · 주문 {ㅈ.order_id}")
            if ㅈ.옛수량 != ㅈ.새수량:
                print(f"     수량 {ㅈ.옛수량}주 → {ㅈ.새수량}주")
            if abs(ㅈ.옛체결가 - ㅈ.새체결가) > 0.5:
                print(f"     체결가 {ㅈ.옛체결가:,.0f}원 → {ㅈ.새체결가:,.0f}원")
            print(f"     슬리피지 {ㅈ.슬리피지:+.2%}  (판단가 {ㅈ.판단가:,.0f}원 대비)")
        print()
    elif 주문고침들:
        print("■ 주문 기록: 전부 처음 적은 대로였습니다(고칠 것 없음)")
        print()

    잔고 = client.get_balance()
    보유 = state_repository.load_positions(session_factory)
    현금, _ = state_repository.load_engine_state(session_factory, 10_000_000.0)
    보유고침들, 모르는종목 = 보유맞추기(보유, 잔고.holdings, 주문고침들, 오늘)

    바뀌는보유 = [ㅂ for ㅂ in 보유고침들 if ㅂ.바뀌나]
    if 바뀌는보유:
        print("■ 보유")
        for ㅂ in 바뀌는보유:
            if ㅂ.옛수량 != ㅂ.새수량:
                print(f"  {ㅂ.symbol} 수량 {ㅂ.옛수량}주 → {ㅂ.새수량}주 (계좌 기준)")
            if abs(ㅂ.옛진입가 - ㅂ.새진입가) > 0.5:
                print(f"  {ㅂ.symbol} 진입가 {ㅂ.옛진입가:,.0f}원 → {ㅂ.새진입가:,.0f}원"
                      ". 손절선이 여기서부터 걸립니다")
        print()

    if 모르는종목:
        print(f"■ 계좌에만 있는 종목 {len(모르는종목)}개: {', '.join(모르는종목)}")
        print("  여기서 들이지 않습니다. 진입일을 사람이 줘야 해서")
        print("  adopt-holdings 워크플로가 할 일입니다.")
        print()

    새현금 = 잔고.cash
    현금바뀌나 = abs(새현금 - 현금) > 0.5
    if 현금바뀌나:
        print(f"■ 현금 {현금:,.0f}원 → {새현금:,.0f}원 ({새현금 - 현금:+,.0f}원)")
        print()

    할일 = bool(바뀌는주문 or 바뀌는보유 or 현금바뀌나)
    if not 할일:
        print("고칠 것이 없습니다. 기록과 계좌가 이미 같습니다.")
        return 0

    if not args.apply:
        print("미리보기입니다. 아무것도 쓰지 않았습니다.")
        print("실제로 고치려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    with session_factory() as session:
        for ㅈ in 바뀌는주문:
            row = session.query(OrderRow).filter(OrderRow.kis_order_id == ㅈ.order_id).first()
            if row is not None:
                row.quantity, row.price, row.fill_confirmed = ㅈ.새수량, ㅈ.새체결가, True
        for ㅂ in 바뀌는보유:
            pos = session.get(PositionRow, ㅂ.symbol)
            if pos is not None:
                pos.quantity, pos.entry_price = ㅂ.새수량, ㅂ.새진입가
        session.commit()

    # 기준평가금도 같이 옮긴다. 안 옮기면 수량이 늘어난 만큼 손실이 난 것처럼
    # 보여서, 아무 일도 안 했는데 다음 날 일일 손실한도에 걸릴 수 있다.
    평가금 = 새현금 + sum(h.quantity * h.current_price for h in 잔고.holdings)
    state_repository.save_engine_state(session_factory, 새현금, 평가금)

    print(f"✅ 주문 {len(바뀌는주문)}건 · 보유 {len(바뀌는보유)}건을 최종 체결로 맞췄습니다.")

    if not args.quiet:
        try:
            정책 = service.get_risk_policy()
            TelegramNotifier(service).send(
                _알림글(
                    오늘, 바뀌는주문, 바뀌는보유,
                    손절비율=정책.stop_loss_pct,
                    atr손절=getattr(정책, "atr_stop_enabled", False),
                )
            )
        except Exception as e:  # noqa: BLE001 (알림 실패가 기록 수정을 지우면 안 된다)
            print(f"알림 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


_요일 = ("월", "화", "수", "목", "금", "토", "일")


def _날짜글(날짜) -> str:
    try:
        return f"{날짜.month}월 {날짜.day}일({_요일[날짜.weekday()]})"
    except AttributeError:
        return str(날짜)


def _이름들(주문들, 보유들) -> dict:
    """종목코드 → 종목명. 보유 쪽에는 이름이 안 실려 와서 주문에서 가져온다."""
    return {ㅈ.symbol: ㅈ.종목명 for ㅈ in 주문들 if ㅈ.종목명}


def _알림글(날짜, 주문들, 보유들, 손절비율: float | None = None, atr손절: bool = False) -> str:
    """장 마감 정산 결과를 사람 말로.

    **예전 글은 읽을 수가 없었다.**

        066970 4주 → 12주 @ 118,241원
           슬리피지 -2.76% (판단가 121,600원)

    무슨 주식인지(종목명 없음), 산 건지 판 건지, 4→12가 좋은 일인지 나쁜
    일인지, -2.76%가 유리한 건지 불리한 건지. 한 줄도 알 수 없었다.
    슬리피지 부호가 특히 나빴다. 살 때 음수는 **싸게 산 것이라 유리한데**
    빨간 숫자처럼 보였다."""
    이름 = _이름들(주문들, 보유들)
    줄 = [
        f"🧾 {_날짜글(날짜)} 장 마감 정산",
        "",
        "주문을 낼 때 적어 둔 기록을, 오늘 확정된 체결 내역으로 다시 맞췄습니다.",
        "새로 사거나 판 것은 없습니다.",
    ]

    for ㅈ in 주문들:
        방향글 = "매수" if ㅈ.산건가 else "매도"
        줄 += ["", f"■ {ㅈ.부른이름} {방향글}"]

        if ㅈ.옛수량 != ㅈ.새수량:
            차 = ㅈ.새수량 - ㅈ.옛수량
            무슨일 = (
                f"{abs(차)}주가 기록에서 빠져 있었습니다"
                if 차 > 0
                else f"{abs(차)}주가 실제로는 체결되지 않았습니다"
            )
            줄.append(모양.칸("수량", f"{모양.주(ㅈ.옛수량)} → {모양.주(ㅈ.새수량)}"))
            줄.append(f"       {무슨일}.")
        else:
            줄.append(모양.칸("수량", f"{모양.주(ㅈ.새수량)} (기록한 그대로)"))

        줄 += [
            모양.칸("단가", 모양.단가(ㅈ.새체결가)),
            모양.칸(f"{방향글}총액", 모양.돈(ㅈ.새수량 * ㅈ.새체결가)),
        ]
        if ㅈ.판단가 > 0:
            줄 += [
                모양.칸("슬리피지", ㅈ.슬리피지글),
                모양.칸("판단가", 모양.단가(ㅈ.판단가)),
            ]

    for ㅂ in 보유들:
        부른이름 = f"{이름[ㅂ.symbol]}({ㅂ.symbol})" if ㅂ.symbol in 이름 else ㅂ.symbol
        줄 += ["", f"■ {부른이름}", "지금부터 지켜지는 기준이 바뀝니다."]
        if ㅂ.옛수량 != ㅂ.새수량:
            줄.append(
                모양.칸("보유수량", f"{모양.주(ㅂ.옛수량)} → {모양.주(ㅂ.새수량)} (증권사 계좌 기준)")
            )
            if ㅂ.새수량 > ㅂ.옛수량:
                줄.append(
                    f"       늘어난 {모양.주(ㅂ.새수량 - ㅂ.옛수량)}는 그동안 기록에 없어서 "
                    "손절이 안 걸려 있었습니다. 이제 같이 걸립니다."
                )
        if abs(ㅂ.옛진입가 - ㅂ.새진입가) > 0.5:
            줄.append(
                모양.칸("매수단가", f"{모양.단가(ㅂ.옛진입가)} → {모양.단가(ㅂ.새진입가)}")
            )
        if 손절비율 and not atr손절:
            손절가 = ㅂ.새진입가 * (1 + 손절비율)
            줄.append(모양.칸("매도전략", f"{손절비율:.0%} 손절 ({모양.돈(손절가)}에서 매도)"))
        elif atr손절:
            줄.append(모양.칸("매도전략", "변동성 손절 (하루 평균 변동폭을 기준으로 걸립니다)"))

    줄 += [
        "",
        "왜 이런 일이 생기나",
        "  시장가 주문은 내는 순간 다 체결되지 않을 때가 있습니다. 주문 직후에",
        "  적은 기록은 그 순간의 사진이라, 뒤늦게 채워진 몫이 빠져 있습니다.",
        "  그래서 장이 끝난 뒤 확정된 값으로 매일 한 번 다시 맞춥니다.",
    ]
    return "\n".join(줄)


if __name__ == "__main__":
    raise SystemExit(main())
