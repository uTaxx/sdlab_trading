"""외국인·기관 순매매를 받아 캐시에 쌓고, 받은 것을 요약해 보여 준다.

    python scripts/fetch_investor_flow.py --시작 2021-01-04
    python scripts/fetch_investor_flow.py --종목 005930,000660 --시작 2026-01-02

매매 대상은 섹터 목록(`sector/catalog.py`)에서 가져온다. 그 목록이 곧
실거래 시트에 올리는 목록이다. 시트를 직접 읽지 않는 것은, 이 스크립트가
시세만 받고 아무것도 안 고치므로 구글 인증 없이 돌 수 있게 하기 위해서다.

**주문은 나가지 않는다.** 과거 자료를 받아 캐시에 쌓기만 한다.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.data.investor_flow import 받기, 순매매캐시
from muwon.sector.catalog import CATALOG

서울 = ZoneInfo("Asia/Seoul")


def 매매대상코드() -> list[str]:
    """섹터 목록의 활성 종목. 이것이 곧 실거래 시트에 올리는 목록이다."""
    본것: list[str] = []
    for 섹터 in CATALOG:
        for 종목 in 섹터.활성종목:
            if 종목.symbol not in 본것:
                본것.append(종목.symbol)
    return 본것


def 인자들():
    ㄱ = argparse.ArgumentParser(description=__doc__)
    ㄱ.add_argument("--시작", default="2021-01-04")
    ㄱ.add_argument("--끝", default="")
    ㄱ.add_argument("--종목", default="", help="쉼표로. 비우면 매매 대상 전부")
    ㄱ.add_argument("--쉼", type=float, default=0.35, help="쪽 사이에 쉬는 초")
    return ㄱ.parse_args()


def main() -> int:
    인자 = 인자들()
    시작 = datetime.fromisoformat(인자.시작).date()
    # 서버가 UTC라 그냥 today()를 쓰면 오전 9시 이전에 어제가 나온다.
    끝 = (datetime.fromisoformat(인자.끝).date() if 인자.끝
         else datetime.now(tz=서울).date())
    코드들 = (
        [ㅋ.strip() for ㅋ in 인자.종목.split(",") if ㅋ.strip()]
        if 인자.종목
        else 매매대상코드()
    )
    print(f"■ 외국인·기관 순매매 받기 · {len(코드들)}종목 · {시작} ~ {끝}")

    결과 = 받기(코드들, 시작, 끝, 캐시=순매매캐시(), 쉼=인자.쉼)
    print(f"  {결과.한줄()}")

    if 결과.자료:
        print()
        print(f"  {'종목':>8}{'거래일':>8}{'첫날':>13}{'끝날':>13}"
              f"{'외국인 순매수 합(만주)':>22}")
        for 코드 in sorted(결과.자료):
            표 = 결과.자료[코드]
            합 = 표["외국인순매매"].sum() / 10_000
            print(f"  {코드:>8}{len(표):>8}{표.index.min().date()!s:>13}"
                  f"{표.index.max().date()!s:>13}{합:>22,.0f}")

    if 결과.못받은것:
        print()
        print("  못 받은 종목")
        for 코드, 까닭 in sorted(결과.못받은것.items()):
            print(f"    {코드}  {까닭}")
        # 조용히 성공한 척하지 않는다. 몇 종목이라도 못 받으면 빨갛게 끝난다.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
