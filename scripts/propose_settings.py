"""**기준을 이렇게 바꾸는 게 어떻겠냐고 제안한다.** 아무것도 안 바꾼다.

제안은 텔레그램으로 가고, 답도 텔레그램으로 `/설정 <이름> <값>` 하면 된다.
제안과 고치는 수단이 같은 곳에 있어야 실제로 고쳐진다.

## 근거 없는 제안은 하지 않는다

제안하는 것은 두 종류뿐이다.

- **산수로 확인되는 것**. 한 종목 15% × 8종목 = 120%인데 현금은 100%다
- **세어 보면 나오는 것**. 유니버스 45종목 중 몇이 거래대금 문턱에 걸리나

손절선을 얼마로 할지, 익절을 켤지 같은 것은 **모의투자 표본이 쌓여야**
말할 수 있다. 그때까지는 "모른다"고 말한다. 그게 정확한 상태다.

사용 예:
    python scripts/propose_settings.py               # 화면 + 텔레그램
    python scripts/propose_settings.py --no-telegram # 화면만
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests
from sqlalchemy import select

from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create, read
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.models import TradeRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.from_sheet import parse_settings
from muwon.settings.proposals import (
    도달할_수_없는_수인가,
    모아서,
    아직_모르는것,
    완전자동인가,
    최소기준에_걸린_종목,
    현금이_모자라나,
)
from muwon.settings.service import build_settings_service

KST = ZoneInfo("Asia/Seoul")
#: 거래대금은 최근 20거래일 평균으로 본다. 하루치는 그날 일이 있었는지에 휘둘린다.
TURNOVER_DAYS = 20


def _거래대금_억(cache, source, 섹터들) -> tuple[dict[str, float], dict[str, str]]:
    """종목별 최근 20일 평균 거래대금(억원). 못 받은 것은 뺀다. **0으로
    채우면 '거래가 없다'로 읽혀 멀쩡한 종목을 끄라고 제안하게 된다.**"""
    오늘 = datetime.now(KST).date()
    시작 = 오늘 - timedelta(days=120)
    억, 이름표 = {}, {}
    for s in 섹터들:
        if not s.활성:
            continue
        for m in s.활성종목:
            야후 = f"{m.symbol}.KS" if m.market == "KOSPI" else f"{m.symbol}.KQ"
            try:
                df = cache.fetch(source, m.symbol, 야후, 시작, 오늘, 최소일수=TURNOVER_DAYS)
            except (requests.RequestException, ValueError, KeyError):
                continue
            if df is None or len(df) < TURNOVER_DAYS:
                continue
            뒤 = df.tail(TURNOVER_DAYS)
            억[m.symbol] = float((뒤["close"] * 뒤["volume"]).mean()) / 1e8
            이름표[m.symbol] = m.name
    return 억, 이름표


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    parser.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    parser.add_argument("--no-telegram", action="store_true", help="화면에만 찍는다")
    parser.add_argument("--skip-turnover", action="store_true", help="거래대금은 안 본다(빠름)")
    args = parser.parse_args()

    sheet_id = args.sheet_id
    if not sheet_id:
        if not args.folder_id:
            raise SystemExit("MUWON_SHEET_ID도 GDRIVE_FOLDER_ID도 없습니다.")
        sheet_id, _ = find_or_create(args.folder_id, DEFAULT_TITLE)

    내용 = read(sheet_id)
    시트 = parse_settings(내용.설정)

    ensure_schema(bootstrap_settings.database_url)
    with make_session_factory(bootstrap_settings.database_url)() as session:
        매매수 = len(list(session.scalars(select(TradeRow))))

    비중 = float(시트.가져오기("max_position_weight"))
    동시보유 = int(시트.가져오기("max_concurrent_positions"))
    섹터당 = int(시트.가져오기("max_per_sector"))
    섹터수 = sum(1 for s in 내용.섹터 if s.활성)
    문턱 = float(시트.가져오기("min_turnover_eok"))

    제안들 = [
        현금이_모자라나(비중, 동시보유),
        도달할_수_없는_수인가(동시보유, 섹터수, 섹터당),
        완전자동인가(bool(시트.가져오기("trading_enabled")), 시트.승인필요),
        *아직_모르는것(매매수),
    ]

    if not args.skip_turnover:
        print("■ 거래대금 세는 중…", file=sys.stderr)
        억, 이름표 = _거래대금_억(PriceCache(), YahooFinanceDataSource(), 내용.섹터)
        제안들.append(최소기준에_걸린_종목(문턱, 억, 이름표))

    글 = 모아서(제안들)
    print(글)

    if not args.no_telegram:
        try:
            from muwon.notify.telegram import TelegramNotifier

            TelegramNotifier(build_settings_service()).send(글)
            print("\n텔레그램으로 보냈습니다.", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 (알림 실패가 제안을 지우면 안 된다)
            print(f"\n텔레그램 전송 실패: {type(e).__name__}: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
