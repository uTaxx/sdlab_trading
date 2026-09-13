"""실거래 목록(구글 시트의 섹터·종목 탭)을 2026-09-06 규칙으로 다시 잰다.

`scripts/update_universe.py`는 시가총액 상위로 **DB 스냅샷**을 갱신한다.
그 스냅샷은 모의투자 엔진과 백테스트가 읽을 뿐, **실거래 매수 후보를
뽑는 `propose_buys.py`는 그 스냅샷을 읽지 않는다.** 실거래는 구글 시트의
섹터·종목 탭만 읽는다(`sector_sheet.read()`). 그래서 그 스냅샷이 갱신돼도
실제로 사고파는 종목은 안 바뀌었고, 알림만 매주 왔다.

이 스크립트는 **실거래가 실제로 읽는 39종목**에서 시작한다. 2026-09-06에
사람이 손으로 적용한 규칙(섹터마다 거래대금 상위 5종목, 최근 20거래일
평균 거래대금 250억 이상)을 지금 시세로 다시 계산해서, 시트와 달라진
자리(편입·제외)를 찾는다.

**새 회사를 찾아내지는 않는다.** 시트에 이미 올라간 종목(활성이든 아니든)
만 다시 줄을 세운다.

사용 예:
    python scripts/rescan_sector_universe.py                    # 미리보기만
    python scripts/rescan_sector_universe.py --apply --notify   # 시트에 반영하고 알림
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.cloud.sector_sheet import (
    DEFAULT_TITLE,
    SheetError,
    find_or_create,
    read,
    write_catalog,
)
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.sector.rescan import (
    거래대금창,
    반영할행,
    섹터당상한,
    우량주기준,
    전체재평가,
)
from muwon.settings.service import build_settings_service


def _거래대금(src, symbol: str, market: str, start, end) -> float | None:
    """최근 `거래대금창` 거래일의 평균 거래대금(억원). 못 받으면 None.

    `verify_sector_catalog.py`와 같은 방식이다. 시장 구분이 틀려도(코스피인데
    코스닥으로 적어 둔 경우 등) 반대쪽으로 한 번 더 시도한다."""
    순서 = ["KS", "KQ"] if market == "KOSPI" else ["KQ", "KS"]
    for 접미사 in 순서:
        try:
            df = src.get_daily_ohlcv(f"{symbol}.{접미사}", start, end)
        except (requests.RequestException, ValueError, KeyError):
            continue
        if len(df):
            최근 = df.tail(거래대금창)
            return float((최근["close"] * 최근["volume"]).mean()) / 1e8
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제로 시트에 반영한다(없으면 미리보기만)")
    parser.add_argument("--notify", action="store_true", help="바뀐 것이 있으면 텔레그램으로 보낸다")
    parser.add_argument("--per-sector", type=int, default=섹터당상한, help="섹터당 활성 상한 (기본 5)")
    parser.add_argument("--min-turnover", type=float, default=우량주기준,
                        help="최근 20거래일 평균 거래대금 문턱 (억원, 기본 250)")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    args = parser.parse_args()

    if not args.folder_id:
        raise SystemExit("GDRIVE_FOLDER_ID가 없습니다 (환경변수 또는 --folder-id).")

    sheet_id, _ = find_or_create(args.folder_id, args.title)
    try:
        내용 = read(sheet_id)
    except SheetError as e:
        raise SystemExit(f"❌ 시트를 읽을 수 없어 다시 잴 수 없습니다\n   {e}") from e

    src = YahooFinanceDataSource()
    end = datetime.now(ZoneInfo("Asia/Seoul")).date()
    start = end - timedelta(days=120)

    거래대금표: dict[str, float | None] = {}
    print(f"■ 활성 섹터 {sum(1 for s in 내용.섹터 if s.활성)}개의 종목 거래대금을 다시 잽니다")
    for s in 내용.섹터:
        if not s.활성:
            continue
        for m in s.종목:
            거래대금표[m.symbol] = _거래대금(src, m.symbol, m.market, start, end)

    재평가들 = 전체재평가(내용, 거래대금표)

    편입전체, 제외전체, 확인못함전체 = [], [], []
    for r in 재평가들:
        print(f"\n■ {r.섹터코드} {r.섹터이름}")
        for e in sorted(r.평가, key=lambda e: (e.거래대금 is None, -(e.거래대금 or 0))):
            표시 = f"  {e.symbol} {e.name:<20}"
            표시 += "  (시세 못 받음)" if e.거래대금 is None else f"  {e.거래대금:>8.0f}억"
            if e.symbol in r.새활성 and not e.지금활성:
                표시 += "  → 편입 후보"
            elif e.symbol not in r.새활성 and e.지금활성 and e.거래대금 is not None:
                표시 += "  → 제외 후보"
            print(표시)
        편입전체 += [(r, e) for e in r.편입]
        제외전체 += [(r, e) for e in r.제외]
        확인못함전체 += [(r, e) for e in r.확인못함]

    print(f"\n편입 후보 {len(편입전체)}종목: "
          + (", ".join(f"{e.name}({e.symbol})" for _, e in 편입전체) or "없음"))
    print(f"제외 후보 {len(제외전체)}종목: "
          + (", ".join(f"{e.name}({e.symbol})" for _, e in 제외전체) or "없음"))
    if 확인못함전체:
        print(f"시세를 못 받아 판단 보류 {len(확인못함전체)}종목: "
              + ", ".join(f"{e.name}({e.symbol})" for _, e in 확인못함전체))

    if not args.apply:
        print("\n미리보기 모드입니다. 시트에 반영하려면 --apply 를 붙이세요.")
        return 0

    if not (편입전체 or 제외전체):
        print("\n바뀐 것이 없어 시트를 다시 쓰지 않습니다.")
        return 0

    섹터행, 종목행 = 반영할행(내용, 재평가들, 섹터당=args.per_sector, 문턱=args.min_turnover)
    write_catalog(sheet_id, 섹터행, 종목행)
    print("\n✅ 시트에 반영했습니다. 다음 매수 후보 산출부터 이 목록을 씁니다.")

    if args.notify and (편입전체 or 제외전체):
        settings_service = build_settings_service()
        from muwon.notify.telegram import TelegramNotifier

        message = (
            "📋 매매 대상 종목 갱신 (실거래 목록, "
            f"{sum(len(s.활성종목) for s in 내용.섹터)}종목)\n"
            f"편입: {', '.join(f'{e.name}({e.symbol})' for _, e in 편입전체) or '없음'}\n"
            f"제외: {', '.join(f'{e.name}({e.symbol})' for _, e in 제외전체) or '없음'}"
        )
        TelegramNotifier(settings_service).send(message)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
