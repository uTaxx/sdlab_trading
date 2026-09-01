"""섹터 초안의 종목코드가 진짜인지, 그리고 거래가 충분한지 검증한다.

**코드 한 자리를 잘못 적으면 엉뚱한 회사를 산다.** 그리고 그 사실은 주문이
나간 뒤에야 드러난다. 그래서 매매에 쓰기 전에 전부 실제 시세로 조회한다.

두 가지를 본다.

1. **그 코드가 존재하나**. 시세가 안 나오면 코드가 틀렸거나 상장 폐지다
2. **거래대금이 충분한가**. 거래가 적으면 슬리피지(사겠다고 판단한 값과
   실제로 사진 값의 차이)가 커진다. 우리는 슬리피지 실측이 0건이라
   그 위험을 감당할 근거가 없다

**시장 구분(KOSPI/KOSDAQ)이 틀려도 여기서 잡힌다**. 야후 티커 접미사가
달라서 조회가 실패한다. 실패하면 반대쪽으로 한 번 더 시도해 알려 준다.

사용 예:
    python scripts/verify_sector_catalog.py
    python scripts/verify_sector_catalog.py --min-turnover 50
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import requests

from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.sector.catalog import CATALOG

#: 최근 며칠의 거래대금을 볼 것인가
창 = 20


def _조회(src, symbol: str, market: str, start, end):
    """맞는 시장으로 먼저 시도하고, 실패하면 반대쪽도 해 본다."""
    순서 = ["KS", "KQ"] if market == "KOSPI" else ["KQ", "KS"]
    for 접미사 in 순서:
        try:
            df = src.get_daily_ohlcv(f"{symbol}.{접미사}", start, end)
        except (requests.RequestException, ValueError, KeyError):
            # 없는 코드면 야후가 404/400을 준다. 반대 시장도 시도해야 하므로
            # 여기서 멈추지 않는다. 무엇이 실패했는지는 아래에서 모아 보고한다.
            continue
        if len(df):
            바른시장 = "KOSPI" if 접미사 == "KS" else "KOSDAQ"
            return df, 바른시장
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-turnover",
        type=float,
        default=50.0,
        help="최근 20일 평균 거래대금 문턱 (억원, 기본 50)",
    )
    args = parser.parse_args()

    src = YahooFinanceDataSource()
    end = datetime.now(ZoneInfo("Asia/Seoul")).date()
    start = end - timedelta(days=120)

    문제 = {"없는코드": [], "시장틀림": [], "거래부족": [], "역사짧음": []}

    for sector in CATALOG:
        print(f"\n■ {sector.코드} {sector.이름}")
        for m in sector.종목:
            df, 바른시장 = _조회(src, m.symbol, m.market, start, end)
            if df is None:
                print(f"  ❌ {m.symbol} {m.name}: 시세를 못 받았습니다 (코드 확인 필요)")
                문제["없는코드"].append(f"{sector.코드}/{m.symbol} {m.name}")
                continue

            최근 = df.tail(창)
            # 거래대금 = 종가 × 거래량. 억원 단위로 환산한다.
            거래대금 = float((최근["close"] * 최근["volume"]).mean()) / 1e8
            표시 = f"  {m.symbol} {m.name:<20} {거래대금:>8.0f}억"

            if 바른시장 != m.market:
                표시 += f"  ⚠ 시장 {m.market}→{바른시장}"
                문제["시장틀림"].append(f"{sector.코드}/{m.symbol} {m.name}: {m.market}→{바른시장}")
            if 거래대금 < args.min_turnover:
                # 이미 꺼 둔 종목은 문제가 아니다. 그 판단이 이 검증에서
                # 나왔기 때문이다. 다시 올리면 매번 같은 경고를 보게 되고,
                # 그러면 진짜 새 문제가 묻힌다.
                표시 += "  · 꺼둠(거래 부족)" if not m.활성 else f"  ⚠ 거래 부족(<{args.min_turnover:g}억)"
                if m.활성:
                    문제["거래부족"].append(f"{sector.코드}/{m.symbol} {m.name} ({거래대금:.0f}억)")
            elif not m.활성:
                # 껐는데 이제는 거래가 충분하다. 다시 켤지 물어볼 일이다.
                표시 += "  ↑ 꺼져 있는데 이제 거래대금이 문턱을 넘습니다"
            print(표시)

    print("\n" + "=" * 60)
    if not any(문제.values()):
        print("문제 없음. 전부 조회되고 거래대금도 충분합니다.")
        return 0

    이름 = {
        "없는코드": "❌ 시세를 못 받은 종목: 코드가 틀렸거나 상장폐지",
        "시장틀림": "⚠ 시장 구분이 틀린 종목: 카탈로그를 고쳐야 합니다",
        "거래부족": "⚠ 거래대금 부족: 슬리피지 위험. 활성=N을 권합니다",
    }
    for 열쇠, 목록 in 문제.items():
        if not 목록:
            continue
        print(f"\n{이름.get(열쇠, 열쇠)} ({len(목록)}건)")
        for 줄 in 목록:
            print(f"  · {줄}")

    print("\n**검증을 통과 못 한 종목은 매매에 쓰지 않습니다.**")
    print("목록에서 지우지 말고 활성=N으로 두세요. 지우면 왜 뺐는지가 사라집니다.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
