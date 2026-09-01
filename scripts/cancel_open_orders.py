"""**낸 주문을 되돌린다.** 이 저장소에 지금까지 없던 길이다.

## 왜 필요한가

시장가 주문은 대개 즉시 다 체결되지만, 호가에 물량이 모자라면 일부만 되고
나머지(**잔여**)가 장 마감까지 남는다. 남아 있는 동안 그 주문은 **아직
살아 있다**. 몇 분 뒤에 값이 크게 움직인 자리에서 마저 체결될 수 있다.

지금까지는 잘못 나간 주문을 알아채도 되돌릴 방법이 없었다. 한국투자증권에
직접 로그인하는 수밖에 없었고, 그 사이에도 주문은 살아 있었다. 이게 그
버튼이다.

## 언제 쓰나

- 승인하지 않은 종목이 나갔을 때
- 매도 스위치를 껐는데 이미 매도 주문이 나가 있을 때
- 잔여가 남아 있는데 지금 값이 판단했던 값과 너무 벌어졌을 때
- 그냥 오늘은 여기서 멈추고 싶을 때

## 취소만 한다. 정정은 안 한다

한국투자증권 API는 정정(값을 바꿔 다시 냄)도 준다. 안 붙였다. 되돌리는
상황은 "이 주문이 잘못됐다"는 판단이고, 그 자리에서 "그럼 얼마에 다시
낼까"를 정하는 건 **새 판단**이지 되돌리기가 아니다. 새 판단은 다음
실행에서 전략이 처음부터 다시 하는 편이 낫다.

같은 이유로 일부만 취소하는 길도 없다. 전량 아니면 전무다.

## 실거래 계좌도 막지 않는다

이 저장소의 다른 스크립트들(`verify_kis_order`, `run_paper_trading`,
`drop_phantom_holdings`)은 실거래 계좌면 실행을 거부한다. **여기는 거부하지
않는다.** 그 스크립트들은 전부 돈을 쓰는 쪽이고, 이건 돈이 나가는 것을
멈추는 쪽이기 때문이다. 끄는 길은 넓히고 켜는 길은 좁힌다.

## 안전장치

- 미리보기가 기본이다. `--apply` 없이는 **주문을 건드리지 않는다.**
- 이미 다 체결된 주문은 증권사가 거부한다. 그게 맞다. 산 것을 안 산 것으로
  되돌릴 수는 없다.
- DB는 안 건드린다. 우리 기록에는 **체결된 수량만** 들어 있고 잔여는 애초에
  없다. 취소해도 고칠 것이 없다.

사용 예:
    python scripts/cancel_open_orders.py                      # 미체결 목록만 본다
    python scripts/cancel_open_orders.py --apply              # 전부 취소
    python scripts/cancel_open_orders.py --symbol 066970 --apply
    python scripts/cancel_open_orders.py --order-id 0000012345 --apply
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.data.kis_client import KISClient, KISOrderRejected
from muwon.domain.types import OpenOrder
from muwon.notify.telegram import TelegramNotifier
from muwon.settings.service import build_settings_service


def 고르기(
    주문들: list[OpenOrder], symbols: list[str], order_ids: list[str]
) -> tuple[list[OpenOrder], list[str]]:
    """사람이 준 이름으로 걸러 낸다. 못 찾은 이름은 따로 돌려준다."""
    if not symbols and not order_ids:
        return list(주문들), []

    맞는것 = [
        o
        for o in 주문들
        if o.symbol in symbols or o.order_id.lstrip("0") in {i.lstrip("0") for i in order_ids}
    ]
    본것 = {o.symbol for o in 맞는것} | {o.order_id.lstrip("0") for o in 맞는것}
    못찾음 = [이름 for 이름 in symbols if 이름 not in 본것]
    못찾음 += [i for i in order_ids if i.lstrip("0") not in 본것]
    return 맞는것, 못찾음


def _알림글(취소됨: list[tuple[OpenOrder, str]], 거부됨: list[tuple[OpenOrder, str]]) -> str:
    줄 = ["🧹 미체결 주문 취소"]
    for 주문, 새번호 in 취소됨:
        줄.append(f"• {주문.한줄}\n  → 잔여 {주문.remaining}주 취소 (취소주문 {새번호})")
    for 주문, 사유 in 거부됨:
        줄.append(f"• {주문.한줄}\n  ⚠️ 취소 거부: {사유}")
    if not 취소됨 and not 거부됨:
        줄.append("취소할 미체결 주문이 없습니다.")
    return "\n".join(줄)


def main() -> int:
    parser = argparse.ArgumentParser(description="미체결 주문의 잔여를 취소한다")
    parser.add_argument(
        "--symbol", action="append", default=[],
        help="이 종목의 미체결만 취소한다. 여러 번 줄 수 있다. 없으면 전부가 대상이다.",
    )
    parser.add_argument(
        "--order-id", action="append", default=[],
        help="이 주문번호만 취소한다. 여러 번 줄 수 있다.",
    )
    parser.add_argument(
        "--date", default="", help="조회할 날짜(YYYY-MM-DD). 기본은 오늘."
    )
    parser.add_argument(
        "--apply", action="store_true", help="실제로 취소한다. 없으면 목록만 보여준다."
    )
    parser.add_argument("--no-notify", action="store_true", help="텔레그램 알림을 보내지 않는다")
    args = parser.parse_args()

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    조회일 = date.fromisoformat(args.date) if args.date else None
    client = KISClient.from_settings(service)

    print("=== 미체결 주문 취소 ===")
    print(f"계좌 {creds.account_no[:4]}**** · {'실거래' if creds.is_real else '모의투자'}")
    if creds.is_real:
        print("⚠️ 실거래 계좌입니다. 취소는 돈이 나가는 것을 **멈추는** 쪽이라 막지 않습니다.")
    print()

    미체결 = client.get_open_orders(조회일)
    if not 미체결:
        print("미체결 주문이 없습니다. 되돌릴 것이 없습니다.")
        return 0

    print(f"■ 미체결 주문 {len(미체결)}건")
    for 주문 in 미체결:
        print(f"   {주문.한줄}  [주문번호 {주문.order_id}]")
    print()

    대상, 못찾음 = 고르기(미체결, args.symbol, args.order_id)
    for 이름 in 못찾음:
        print(f"· {이름}: 미체결 주문에 없습니다. 이미 다 체결됐거나 취소된 것입니다.")
    if 못찾음:
        print()

    if not 대상:
        print("취소할 대상이 없습니다.")
        return 1 if 못찾음 else 0

    print(f"■ 취소 대상 {len(대상)}건 (각각 잔여 전량)")
    for 주문 in 대상:
        print(f"   {주문.한줄}")
    print()

    if not args.apply:
        print("미리보기입니다. 주문을 건드리지 않았습니다.")
        print("실제로 취소하려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    취소됨: list[tuple[OpenOrder, str]] = []
    거부됨: list[tuple[OpenOrder, str]] = []
    for 주문 in 대상:
        try:
            새번호 = client.cancel_order(주문)
        except KISOrderRejected as e:
            # 한 건이 거부됐다고 나머지를 안 건드리면, 정작 급한 주문이
            # 살아남는다. 사유만 남기고 다음으로 간다.
            거부됨.append((주문, e.msg1))
            print(f"⚠️ {주문.symbol} 취소 거부: {e.msg1}")
            continue
        except Exception as e:  # noqa: BLE001 (한 건의 통신 실패가 나머지를 막아선 안 된다)
            거부됨.append((주문, str(e)))
            print(f"⚠️ {주문.symbol} 취소 실패: {e}")
            continue
        취소됨.append((주문, 새번호))
        print(f"✅ {주문.symbol} 잔여 {주문.remaining}주 취소 접수 (취소주문 {새번호})")

    print()
    print(f"취소 {len(취소됨)}건 · 거부/실패 {len(거부됨)}건")
    print("DB는 건드리지 않았습니다. 우리 기록에는 체결된 수량만 들어 있어 고칠 것이 없습니다.")

    if not args.no_notify:
        TelegramNotifier(service).send(_알림글(취소됨, 거부됨))

    return 1 if 거부됨 else 0


if __name__ == "__main__":
    raise SystemExit(main())
