"""KIS 모의투자 계좌에 실제로 주문을 넣어 "주문 실행 경로"를 검증하는 스크립트.

왜 필요한가: 지금까지 자동매매 실행은 매번 "체결 없음"이었다. 시세조회는
매일 검증되지만 **주문을 넣는 코드(place_cash_order)는 한 번도 실행된 적이
없다**. 엔드포인트·TR_ID·요청 바디 형식이 틀렸어도 알 수가 없고, 첫 매수
신호가 뜨는 날 처음 알게 된다. 그날 실패하면 그 기회를 통째로 놓친다.
그래서 신호와 무관하게 강제로 한 번 주문을 넣어보고 결과를 분류한다.

결과는 세 가지로 나뉘며, 두 번째(거부)도 "검증 성공"이다:

  ✅ 체결 접수     주문번호를 받음. 경로 완전 검증
  ✅ 정상 거부     KIS가 업무 규칙으로 거부(장 시간 아님·잔고 부족 등).
                  요청 형식·인증·엔드포인트·TR_ID가 전부 맞다는 뜻이므로
                  이것도 경로 검증 성공으로 본다. 장 시간 밖에 실행하면
                  실제 체결 없이 안전하게 확인할 수 있다.
  ❌ 요청 실패     HTTP/인증/네트워크 오류: 진짜 문제이며 고쳐야 한다.

안전장치: 모의투자(paper) 계좌가 아니면 실행을 거부하고, --confirm 없이는
주문을 넣지 않는다(기본은 준비 상태만 점검하는 예행연습).

사용 예:
    python scripts/verify_kis_order.py                      # 예행연습(주문 안 넣음)
    python scripts/verify_kis_order.py --confirm            # 삼성전자 1주 매수 시도
    python scripts/verify_kis_order.py --confirm --side sell --symbol 005930
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.data.kis_client import KISClient, KISOrderRejected
from muwon.data.universe import find_by_symbol
from muwon.domain.types import OrderSide
from muwon.settings.service import build_settings_service


def _시트이름(symbol: str) -> str:
    """시트의 섹터·종목 목록에서 이름을 찾는다. 못 찾으면 빈 값.

    이름 하나 때문에 검증이 멈추면 안 되므로 어떤 실패든 조용히 넘긴다."""
    import os

    if not os.environ.get("GDRIVE_SA_KEY_JSON"):
        return ""
    try:
        from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read

        sheet_id = os.environ.get("MUWON_SHEET_ID") or find_or_create(
            os.environ["GDRIVE_FOLDER_ID"], DEFAULT_TITLE
        )[0]
        for s in read(sheet_id).섹터:
            for m in s.종목:
                if m.symbol == symbol:
                    return m.name
    except Exception:  # noqa: BLE001 (이름 때문에 검증이 멈추면 안 된다)
        return ""
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS 모의투자 주문 실행 경로 검증")
    parser.add_argument("--symbol", default="005930", help="종목코드 6자리 (기본: 삼성전자)")
    parser.add_argument("--quantity", type=int, default=1, help="주문 수량 (기본: 1주)")
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="실제로 주문을 넣는다. 없으면 준비 상태만 점검하고 종료(예행연습).",
    )
    args = parser.parse_args()

    settings_service = build_settings_service()
    creds = settings_service.get_kis_credentials()

    if creds.is_real:
        raise SystemExit(
            "❌ KIS 환경이 실거래(real)입니다. 이 스크립트는 모의투자 전용입니다. "
            "실제 돈으로 검증 주문을 넣지 않도록 막습니다."
        )
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit(
            "❌ KIS 인증정보가 없습니다. 앱키/시크릿/계좌번호를 먼저 설정하세요 "
            "(configure.py kis ... 또는 대시보드)."
        )

    # 이름은 기본 목록에만 있었다. 지금 매매 대상은 시트의 섹터·종목이라,
    # 시트에 있는 종목을 시험하면 로그에 코드만 두 번 찍혔다.
    # 무엇을 주문했는지 로그로 못 읽으면 나중에 되짚을 수가 없다.
    ticker = find_by_symbol(args.symbol)
    name = ticker.name if ticker else _시트이름(args.symbol) or args.symbol
    side = OrderSide.BUY if args.side == "buy" else OrderSide.SELL

    print("=== KIS 주문 실행 경로 검증 ===")
    print(f"환경    : 모의투자(paper) · 계좌 {creds.account_no[:4]}****")
    print(f"주문    : {name}({args.symbol}) {args.quantity}주 {'매수' if side == OrderSide.BUY else '매도'} (시장가)")

    client = KISClient.from_settings(settings_service)

    # 1단계, 인증 토큰. 여기서 실패하면 주문 이전에 앱키 문제다
    try:
        client._ensure_token()
        print("1) 인증 토큰 발급: ✅ 성공")
    except requests.HTTPError as e:
        print(f"1) 인증 토큰 발급 ❌ 실패: {e}")
        raise SystemExit("앱키/시크릿이 모의투자용이 맞는지 확인하세요.") from e

    # 2단계, 기준가 확보. 주문 자체엔 안 쓰이지만(시장가), 기록·알림용 값이라
    # 실제 매매와 같은 경로를 그대로 태워 본다
    from datetime import date, timedelta

    end = date.today()  # noqa: DTZ011 (날짜만 필요)
    df = client.get_daily_ohlcv(args.symbol, end - timedelta(days=30), end)
    if len(df) == 0:
        raise SystemExit("❌ 시세 조회 결과가 비어 있습니다. 종목코드를 확인하세요.")
    reference_price = float(df["close"].iloc[-1])
    print(f"2) 시세 조회: ✅ 성공 (직전 종가 {reference_price:,.0f}원)")

    if not args.confirm:
        print()
        print("예행연습 모드입니다. 주문을 넣지 않고 종료합니다.")
        print("실제로 주문 경로를 검증하려면 --confirm 을 붙여 다시 실행하세요.")
        return

    # 3단계, 실제 주문. 이 프로젝트에서 한 번도 실행된 적 없는 경로
    print("3) 주문 전송 중...")
    try:
        result = client.place_cash_order(args.symbol, side, args.quantity, reference_price)
    except KISOrderRejected as e:
        print(f"   ✅ 경로 검증 성공 (KIS가 업무 규칙으로 거부: {e.msg1})")
        print(f"      rt_cd={e.rt_cd} msg_cd={e.msg_cd}")
        print()
        print("거부됐지만 엔드포인트·TR_ID·인증·요청 형식은 모두 정상입니다.")
        print("장 시간(09:00~15:30 KST)에 다시 실행하면 실제 체결까지 확인할 수 있습니다.")
        return
    except requests.HTTPError as e:
        body = e.response.text[:500] if e.response is not None else "(응답 없음)"
        print(f"   ❌ 요청 실패: {e}")
        print(f"      응답 본문: {body}")
        raise SystemExit("주문 요청 형식이나 인증에 문제가 있습니다. 위 응답을 확인하세요.") from e

    print(f"   ✅ 주문 접수 성공: 주문번호 {result.order_id}")
    print()
    print("주문 실행 경로가 완전히 검증되었습니다.")
    print("※ 이 주문은 실제 모의투자 계좌에 들어갔습니다. 필요하면 KIS 앱에서 확인/취소하세요.")


if __name__ == "__main__":
    main()
