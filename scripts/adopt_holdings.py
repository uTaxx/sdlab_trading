"""증권사에는 있는데 우리 기록에 없는 종목을 **우리 기록으로 들인다.**

## 왜 필요한가

검증 스크립트나 손매매로 산 종목은 증권사 계좌에만 있고 우리 DB엔 없다.
그런데 **엔진은 자기가 모르는 종목을 팔지 않는다** — 손절도, 보유일수
청산도, 매도 신호도 전부 `positions` 테이블에 있는 종목에만 걸린다.
그래서 그 주식은 아무도 안 지키는 채로 남는다. 값이 반토막 나도 아무 일도
일어나지 않는다.

들여놓으면 다음 실행부터 그 종목도 손절 대상이 된다.

## 무엇을 사실로 삼나

**수량과 평균매입가는 증권사 값을 그대로 쓴다.** 우리 쪽엔 주문 기록이
없으니 증권사가 유일한 사실이다.

**진입일은 우리가 줘야 한다.** 잔고조회는 "언제 샀는지"를 안 알려주는데,
보유일수 청산이 그 날짜를 센다. 기본값은 오늘이고, 오늘 산 게 아니면
`--entry-date`로 실제 매수일을 줘야 한다 — 안 그러면 보유일수가 짧게
잡혀서 청산이 늦어진다.

**현금도 같이 맞춘다.** 종목만 들이고 현금을 그대로 두면 엔진이 없는 돈을
있다고 믿고 그 위에서 비중 상한을 계산한다. 증권사의 가수도정산금액
(결제까지 반영된 현금)으로 덮는다.

## 전략은 'adopted'로 남긴다

우리가 산 게 아니니 어느 전략의 성적으로도 잡히면 안 된다. 나중에
`trades`를 전략별로 볼 때 이 종목이 `volume_surge_5d`의 성적을 흔들면
가설 판단이 오염된다.

## 안전장치

- 미리보기가 기본이다. `--apply` 없이는 **아무것도 쓰지 않는다.**
- 실거래 계좌면 실행을 거부한다.
- 이미 DB에 있는 종목은 건드리지 않는다(수량이 달라도 덮어쓰지 않는다 —
  그건 부분 체결일 수도 있어서 사람이 봐야 한다).

사용 예:
    python scripts/adopt_holdings.py                      # 미리보기
    python scripts/adopt_holdings.py --apply              # 오늘 산 것으로 들임
    python scripts/adopt_holdings.py --apply --entry-date 2026-08-20
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.db.session import ensure_schema, make_session_factory
from muwon.execution import state_repository
from muwon.execution.adopt import ADOPTED, plan, 맞출평가금, 수량맞추기
from muwon.settings.service import build_settings_service

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser(description="증권사에만 있는 보유 종목을 우리 기록으로 들인다")
    parser.add_argument(
        "--apply", action="store_true", help="실제로 DB에 쓴다. 없으면 미리보기만 한다."
    )
    parser.add_argument(
        "--fix-quantity", action="append", default=[], metavar="SYMBOL",
        help="이 종목의 수량을 **계좌 값으로 덮는다.** 여러 번 줄 수 있다. "
             "원인을 확인한 뒤에만 쓸 것 — 부분 체결·손매매·버그가 겉모습이 같다.",
    )
    parser.add_argument(
        "--entry-date",
        type=date.fromisoformat,
        default=None,
        help="매수일 (YYYY-MM-DD). 기본은 오늘 — 보유일수 청산이 이 날짜부터 센다.",
    )
    args = parser.parse_args()

    진입일 = args.entry_date or datetime.now(KST).date()

    service = build_settings_service()
    creds = service.get_kis_credentials()
    if creds.is_real:
        raise SystemExit(
            "❌ KIS 환경이 실거래(real)입니다. 이 스크립트는 모의투자 전용입니다."
        )
    if not creds.app_key or not creds.app_secret or not creds.account_no:
        raise SystemExit("❌ KIS 인증정보가 없습니다.")

    ensure_schema(bootstrap_settings.database_url)
    session_factory = make_session_factory(bootstrap_settings.database_url)

    잔고 = KISClient.from_settings(service).get_balance()
    보유 = state_repository.load_positions(session_factory)
    현금, 기준평가금 = state_repository.load_engine_state(session_factory, 10_000_000.0)

    계획 = plan(잔고.holdings, 보유, 진입일)
    맞출것 = 수량맞추기(args.fix_quantity, 잔고.holdings, 보유)

    print("=== 증권사에만 있는 보유 종목 들이기 ===")
    print(f"계좌 {creds.account_no[:4]}**** · 증권사 보유 {len(잔고.holdings)}종목 / DB 기록 {len(보유)}종목")
    print()

    맞출심볼 = {p.symbol for p in 맞출것}
    for 다름 in 계획.수량다른것:
        if 다름.symbol in 맞출심볼:
            continue
        # 덮어쓰지 않는다 — 부분 체결일 수도 있고 우리 버그일 수도 있다.
        print(f"⚠️ {다름.name}({다름.symbol}) 수량이 다릅니다 — "
              f"DB {다름.db_quantity}주 vs 계좌 {다름.account_quantity}주")
        print("   자동으로 고치지 않습니다. 원인을 확인하고 --fix-quantity로 이름을 주세요.")

    for pos in 맞출것:
        옛것 = 보유[pos.symbol].quantity
        print(f"■ {pos.symbol} 수량을 계좌 값으로 맞춥니다 — DB {옛것}주 → {pos.quantity}주")
        print(f"   진입가 {pos.entry_price:,.0f}원·진입일 {pos.entry_date}는 그대로 둡니다")
        print("   (계좌 평균매입가는 예전 매수까지 섞인 값이라 이번 회차의 슬리피지를 못 되짚습니다)")
        print()

    if not 계획.할일있나 and not 맞출것:
        print("들일 종목이 없습니다 — 증권사 보유가 전부 DB에도 있습니다.")
        return 0

    현재가 = {h.symbol: h for h in 잔고.holdings}
    for pos in 계획.들일것:
        h = 현재가[pos.symbol]
        print(f"■ {h.name}({pos.symbol})")
        print(f"   수량      {pos.quantity}주")
        print(f"   평균매입가 {pos.entry_price:,.0f}원  (원가 {pos.quantity * pos.entry_price:,.0f}원)")
        print(f"   현재가    {h.current_price:,.0f}원  (평가손익 {h.pnl_amount:+,.0f}원)")
        print(f"   진입일    {pos.entry_date}  ← 보유일수 청산이 이 날부터 셉니다")
        print()

    새현금 = 잔고.cash
    새평가금 = 맞출평가금(새현금, 잔고.holdings)
    print("■ 엔진 상태도 같이 맞춥니다")
    print(f"   현금       {현금:,.0f}원 → {새현금:,.0f}원 ({새현금 - 현금:+,.0f}원)")
    print(f"   기준평가금 {기준평가금:,.0f}원 → {새평가금:,.0f}원")
    print("   (기준평가금은 일일 손실한도가 '오늘 얼마나 잃었나'를 재는 기준점입니다.")
    print("    안 맞추면 들이는 순간 손실이 난 것처럼 보여 한도가 헛돕니다)")
    print()

    if not args.apply:
        print("미리보기입니다 — 아무것도 쓰지 않았습니다.")
        print("실제로 들이려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    for pos in 계획.들일것 + 맞출것:
        state_repository.save_position(session_factory, pos)
    state_repository.save_engine_state(session_factory, 새현금, 새평가금)

    if 맞출것:
        print(f"✅ {len(맞출것)}종목의 수량을 계좌 값으로 맞췄습니다.")
    if not 계획.들일것:
        print("   계좌 대조(state-check)를 다시 돌려 맞춰졌는지 확인하세요.")
        return 0
    print(f"✅ {len(계획.들일것)}종목을 들였습니다. **다음 실행부터 손절 대상이 됩니다.**")
    print(f"   전략은 '{ADOPTED}'로 남깁니다 — 우리가 산 게 아니니 어느 가설의")
    print("   성적으로도 잡히면 안 됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
