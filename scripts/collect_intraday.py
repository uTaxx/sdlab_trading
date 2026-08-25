"""오늘의 30분봉을 받아 쌓는다.

**이 스크립트가 오늘 안 돌면 오늘치는 영영 없다.** 한국투자증권 API는
당일 분봉만 주고 과거 날짜는 아예 못 받는다. 그래서 이건 "언젠가 돌리면
되는 것"이 아니라 매 거래일 장 마감 뒤에 반드시 한 번 도는 것이다.

왜 모으는가 — 장중 모멘텀(첫 30분이 마지막 30분을 예측한다)이 우리가
조사한 단타 후보 중 한국 시장 증거가 있는 유일한 것인데(JRFM 15:523),
검증하려면 30분봉이 필요하고 과거 것은 살 수가 없다. 오늘부터 쌓는
수밖에 없다.

**주문은 내지 않는다.** 시세 조회만 한다.

사용 예:
    python scripts/collect_intraday.py                # 오늘치 수집
    python scripts/collect_intraday.py --dry-run      # 무엇을 받을지만 보기
"""

import argparse
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.config import bootstrap_settings
from muwon.data.intraday import SLOT_ENDS, aggregate
from muwon.data.intraday_store import (
    DEFAULT_PATH,
    coverage,
    format_coverage,
    save,
    stored_days,
)
from muwon.data.kis_client import KISClient
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import active_universe
from muwon.db.session import make_session_factory
from muwon.settings.service import build_settings_service

KST = timezone(timedelta(hours=9))

#: 호출 사이 간격(초). KIS는 초당 호출 수를 제한한다 — 60종목 × 13칸이면
#: 780번이라, 아껴 부르는 것보다 안 막히는 게 중요하다.
CALL_INTERVAL = 0.12


def collect(client: KISClient, symbols: list[str], today: date, *, verbose: bool = True) -> int:
    """종목마다 13칸을 각각 한 번씩 불러 받는다.

    칸 하나가 30분이고 이 API가 한 번에 30개를 주므로, **칸 하나 = 호출
    하나**로 딱 맞는다. 예: 093000으로 부르면 09:01~09:30이 온다."""
    총합 = 0
    for 번호, symbol in enumerate(symbols, 1):
        분봉 = []
        실패 = 0
        for 칸 in SLOT_ENDS:
            try:
                분봉 += client.get_minute_bars(symbol, f"{칸}00")
            except requests.RequestException as e:
                # 종목 하나가 죽어도 나머지는 받아야 한다 — 오늘 못 받으면
                # 영영 없기 때문이다.
                실패 += 1
                print(f"  {symbol} {칸} 실패: {type(e).__name__}", file=sys.stderr)
            time.sleep(CALL_INTERVAL)

        칸들 = aggregate(symbol, today, 분봉)
        총합 += save(칸들)
        if verbose:
            꼬리 = f" (호출 실패 {실패})" if 실패 else ""
            print(f"[{번호}/{len(symbols)}] {symbol}: {len(칸들)}칸{꼬리}")
    return 총합


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="받지 않고 대상만 보여 준다")
    parser.add_argument("--date", default="", help="기록할 날짜 (기본: 오늘 KST). 조회는 언제나 당일치다")
    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()

    session_factory = make_session_factory(bootstrap_settings.database_url)
    # SettingsService에 session_factory를 바로 주면 안 된다 — 사이에
    # SettingsStore가 있어야 하고, 그게 비밀값을 푸는 열쇠를 들고 있다.
    # 2026-08-20~25에 이걸 틀려서 30분봉이 일곱 번 내리 실패했다.
    service = build_settings_service()
    # 기본값은 시가총액 상위(실거래 대상)다. 실험용 거래대금 스냅샷이
    # 수집 대상을 바꾸면, 나중에 "그때 무엇을 모았나"를 되짚을 수 없다.
    symbols = [t.symbol for t in active_universe(session_factory, list(UNIVERSE))]

    print(f"■ {today} 30분봉 수집 — {len(symbols)}종목 × {len(SLOT_ENDS)}칸")
    print(f"  저장 위치: {DEFAULT_PATH} (운영 DB와 분리 — 대시보드가 매번 내려받지 않게)")
    if args.dry_run:
        print(f"  대상: {', '.join(symbols[:10])}{' …' if len(symbols) > 10 else ''}")
        print("  --dry-run이라 실제로 받지는 않았습니다.")
        return 0

    client = KISClient.from_settings(service)
    저장수 = collect(client, symbols, today)

    print(f"\n{저장수}칸 저장했습니다.")
    print(format_coverage(coverage(today, symbols)))

    쌓인날 = stored_days()
    print(f"\n지금까지 {len(쌓인날)}거래일치가 쌓였습니다", end="")
    print(f" ({쌓인날[0]} ~ {쌓인날[-1]})." if 쌓인날 else ".")
    print("  장중 모멘텀을 재려면 최소 6개월(약 120거래일)은 있어야 합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
