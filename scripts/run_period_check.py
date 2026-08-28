"""지금 걸린 전략을 최근 3개월·12개월·5년에 돌려 보고 시트에 남긴다.

화면에서 단추를 누르면 n8n이 이 워크플로를 부르고, 결과가 시트에 쌓이면
화면이 그것을 읽어 표로 그린다.

    python scripts/run_period_check.py                    # 셋 다, 시트에 올림
    python scripts/run_period_check.py --기간 3개월        # 하나만
    python scripts/run_period_check.py --no-sheet         # 화면에만

    # 등록된 전략 전부를 같은 구간에 돌려 순위를 낸다(비교 모드)
    python scripts/run_period_check.py --기간 3개월 --전략 전부

## 이 스크립트는 주문을 내지 않는다

과거 시세로 돌려 보기만 한다. 증권사에 붙지 않고, 상태 DB도 고치지 않는다.
DB는 **지금 무슨 전략이 걸려 있는지**를 읽으려고 내려받을 뿐이다.

## 어느 조건에서 잰 숫자인가

  - 전략은 지금 DB에 걸린 것. 사는 쪽과 파는 쪽을 따로 걸어 뒀으면 그대로.
  - 기준(손절·익절·보유기간·비중·동시보유)은 지금 시트에 적힌 것.
  - 체결은 다음 날 시가. 실거래 엔진이 실제로 하는 방식이다.
  - 매수·매도 스위치는 켠 채로 돈다(`검증용정책`에 이유를 적어 뒀다).
  - 슬리피지 0. 실측 표본이 아직 0건이라 넣을 값이 없다.

## 실패하면 무엇이 빨개지나

셋이다. 워크플로가 빨개지고, 시트에 상태가 '실패'인 줄이 남고, 화면이
그 줄을 그대로 보여 준다. 조용히 성공한 척하는 실패가 제일 비싸다.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.experiment import WARMUP_DAYS
from muwon.analysis.market_data import load_histories
from muwon.analysis.period_check import (
    검증용정책,
    구간,
    기간들,
    기간표,
    기준글,
    돌려보기,
)
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import KIND_MARKET_CAP, active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.settings.from_sheet import build_policy_provider
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import (
    build_strategies,
    build_strategy,
    get_definition,
    list_definitions,
)

서울 = ZoneInfo("Asia/Seoul")

기간검증머리 = [
    "열쇠", "잰때", "기간", "시작일", "끝일", "매수전략", "매도전략",
    "수익률%", "최대낙폭%", "최악토막", "최악토막%", "승률%", "손익비",
    "거래수", "표본충분", "기준", "상태", "까닭",
]

탭이름 = "기간검증"

#: 비교 모드가 쓰는 탭. 검증과 섞지 않는다 — 저기는 "지금 걸린 전략이
#: 최근에 어땠나"의 기록이고, 여기는 "그때 어느 전략이 제일 덜 잃었나"의
#: 기록이다. 한 표에 두면 어느 줄이 지금 도는 것인지 알 수 없다.
비교탭 = "기간비교"

비교머리 = [
    "열쇠", "잰때", "기간", "시작일", "끝일", "전략", "전략이름",
    "수익률%", "최대낙폭%", "최악토막", "최악토막%", "승률%", "손익비",
    "거래수", "노출%", "기준",
]


def 전략이름(열쇠: str) -> str:
    try:
        return get_definition(열쇠).화면이름
    except Exception:  # noqa: BLE001 — 이름을 못 찾는다고 검증이 죽으면 안 된다
        return 열쇠


def 줄만들기(잰때: datetime, 성적, 사는키: str, 파는키: str, 기준: str) -> list[str]:
    ㅁ = 성적.metrics
    최악 = 성적.최악토막
    return [
        f"P{잰때.strftime('%Y-%m-%dT%H:%M')}|{성적.이름}",
        잰때.strftime("%Y-%m-%d %H:%M"),
        성적.이름,
        성적.시작.isoformat(),
        성적.끝.isoformat(),
        사는키,
        파는키,
        f"{ㅁ.total_return_pct:.2f}",
        f"{ㅁ.max_drawdown_pct:.2f}",
        최악[0] if 최악 else "",
        f"{최악[1]:.2f}" if 최악 else "",
        f"{ㅁ.win_rate_pct:.1f}",
        f"{ㅁ.profit_factor:.2f}",
        str(ㅁ.num_trades),
        "예" if 성적.믿을만한가 else "아니오",
        기준,
        "됨",
        성적.모자람,
    ]


def 실패줄(잰때: datetime, 기간글: str, 까닭: str) -> list[str]:
    줄 = [""] * len(기간검증머리)
    줄[0] = f"P{잰때.strftime('%Y-%m-%dT%H:%M')}|{기간글}"
    줄[1] = 잰때.strftime("%Y-%m-%d %H:%M")
    줄[2] = 기간글
    줄[16] = "실패"
    줄[17] = 까닭[:200]
    return 줄


def 시트찾기(인자) -> str:
    """시트 아이디. 없으면 드라이브 폴더에서 찾고, 그것도 없으면 빈 문자열."""
    if 인자.sheet_id:
        return 인자.sheet_id
    if not 인자.folder_id:
        return ""
    from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

    시트, _ = find_or_create(인자.folder_id, DEFAULT_TITLE)
    return 시트


def 올리기(sheet_id: str, 줄들: list[list[str]]) -> None:
    """시트에 덧붙인다. 시트를 못 찾으면 아무 일도 안 하고 그렇다고 말한다."""
    if not sheet_id:
        print("시트를 못 찾아 결과를 안 올립니다.", file=sys.stderr)
        return
    from muwon.cloud.sheet_log import append

    올린수 = append(sheet_id, 탭이름, 기간검증머리, 줄들)
    print(f"시트 '{탭이름}'에 {올린수}줄 올렸습니다.", file=sys.stderr)


def 화면에(성적들: list, 사는키: str, 파는키: str, 기준: str) -> None:
    print(f"■ 매수 전략: {전략이름(사는키)} ({사는키})")
    print(
        f"■ 매도 전략: {전략이름(파는키)} ({파는키})"
        if 파는키 else "■ 매도 전략: 매수와 같음"
    )
    print(f"■ 기준: {기준}\n")
    for 성적 in 성적들:
        ㅁ = 성적.metrics
        최악 = 성적.최악토막
        print(f"[{성적.이름}] {성적.시작} ~ {성적.끝}")
        print(f"  수익률 {ㅁ.total_return_pct:+.2f}% · 최대낙폭 {ㅁ.max_drawdown_pct:.2f}%")
        if 최악:
            print(f"  제일 나빴던 토막 {최악[0]} {최악[1]:+.2f}%")
        print(
            f"  거래 {ㅁ.num_trades}건 · 승률 {ㅁ.win_rate_pct:.1f}% · "
            f"손익비 {ㅁ.profit_factor:.2f}"
        )
        if not 성적.믿을만한가:
            print("  표본이 적습니다. 거래 20건 아래면 한 종목이 결과를 만듭니다.")
        if 성적.모자람:
            print(f"  {성적.모자람}")
        print()


def 비교줄만들기(잰때: datetime, 열쇠: str, 성적, 기준: str) -> list[str]:
    ㅁ = 성적.metrics
    최악 = 성적.최악토막
    return [
        f"C{잰때.strftime('%Y-%m-%dT%H:%M')}|{성적.이름}|{열쇠}",
        잰때.strftime("%Y-%m-%d %H:%M"),
        성적.이름,
        성적.시작.isoformat(),
        성적.끝.isoformat(),
        열쇠,
        전략이름(열쇠),
        f"{ㅁ.total_return_pct:.2f}",
        f"{ㅁ.max_drawdown_pct:.2f}",
        최악[0] if 최악 else "",
        f"{최악[1]:.2f}" if 최악 else "",
        f"{ㅁ.win_rate_pct:.1f}",
        f"{ㅁ.profit_factor:.2f}",
        str(ㅁ.num_trades),
        f"{ㅁ.exposure_pct:.1f}",
        기준,
    ]


def 비교표(정의, 줄들: list[tuple[str, object]]) -> None:
    """한 구간의 전략별 성적을 순위로 찍는다. **수익률이 높은 순이다.**

    빠지는 구간에서는 이 순위가 곧 방어력 순위다. 다만 **안 산 전략이
    위로 온다.** 거래 0건은 지킨 것이 아니라 아무 일도 안 한 것이라,
    노출과 거래 수를 같은 줄에 찍어서 그 둘을 구별하게 한다."""
    차례 = sorted(줄들, key=lambda ㄱ: -ㄱ[1].metrics.total_return_pct)
    print(f"[{정의.이름}] {차례[0][1].시작} ~ {차례[0][1].끝} · 전략 {len(차례)}개")
    print(f"  {'':2} {'전략':<22} {'수익률':>8} {'최대낙폭':>9} "
          f"{'최악토막':>12} {'거래':>5} {'승률':>6} {'노출':>6}")
    for 번호, (열쇠, 성적) in enumerate(차례, 1):
        ㅁ = 성적.metrics
        최악 = 성적.최악토막
        최악글 = f"{최악[0]} {최악[1]:+.1f}%" if 최악 else "—"
        print(f"  {번호:>2} {전략이름(열쇠)[:22]:<22} {ㅁ.total_return_pct:>+7.2f}% "
              f"{ㅁ.max_drawdown_pct:>8.2f}% {최악글:>12} {ㅁ.num_trades:>5} "
              f"{ㅁ.win_rate_pct:>5.1f}% {ㅁ.exposure_pct:>5.1f}%")

    안산것 = [열쇠 for 열쇠, ㅅ in 차례 if ㅅ.metrics.num_trades == 0]
    적게산것 = [열쇠 for 열쇠, ㅅ in 차례 if 0 < ㅅ.metrics.num_trades < 5]
    if 안산것:
        print(f"\n  거래 0건({len(안산것)}개): "
              + ", ".join(전략이름(ㄱ) for ㄱ in 안산것))
        print("  지킨 것이 아니라 아무것도 안 한 것입니다. 순위에서 빼고 보세요.")
    if 적게산것:
        print(f"  거래 5건 미만({len(적게산것)}개): "
              + ", ".join(전략이름(ㄱ) for ㄱ in 적게산것))
        print("  한 종목이 결과를 통째로 만든 숫자입니다.")
    print()


def main() -> int:
    받은것 = argparse.ArgumentParser(description=__doc__)
    받은것.add_argument(
        "--기간", default="전부",
        help="3개월 / 12개월 / 5년 / 전부 (쉼표로 여럿)",
    )
    받은것.add_argument("--no-sheet", action="store_true", help="시트에 안 올린다")
    받은것.add_argument("--no-cache", action="store_true", help="시세를 새로 받는다")
    받은것.add_argument(
        "--전략", default="지금",
        help="지금 걸린 것(지금) / 등록된 전부(전부) / 쉼표로 적은 전략 키들",
    )
    받은것.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    받은것.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    인자 = 받은것.parse_args()

    골라진것 = (
        list(기간들)
        if 인자.기간.strip() in ("전부", "all", "")
        else [기간표[ㄱ.strip()] for ㄱ in 인자.기간.split(",") if ㄱ.strip() in 기간표]
    )
    if not 골라진것:
        raise SystemExit(f"모르는 기간입니다: {인자.기간} (쓸 수 있는 것: {list(기간표)})")

    잰때 = datetime.now(서울)
    기간글 = ",".join(ㄱ.이름 for ㄱ in 골라진것)
    print(f"■ 잰 때: {잰때:%Y-%m-%d %H:%M} (한국시간)")
    print(f"■ 기간: {기간글}")

    # 시트는 결과를 올릴 곳이자 기준을 읽을 곳이다. 실패해도 그 사실을
    # 시트에 남겨야 하므로 먼저 찾아 둔다.
    sheet_id = "" if 인자.no_sheet else 시트찾기(인자)

    try:
        return 진짜로(골라진것, 잰때, 인자, sheet_id)
    except Exception as 탈:  # noqa: BLE001 — 무엇이 터지든 화면에 남겨야 한다
        traceback.print_exc()
        try:
            올리기(sheet_id, [실패줄(잰때, 기간글, f"{type(탈).__name__}: {탈}")])
        except Exception:  # noqa: BLE001 — 시트까지 막히면 워크플로가 빨개진다
            print("실패한 사실도 시트에 못 남겼습니다.", file=sys.stderr)
        return 1


def 진짜로(골라진것, 잰때: datetime, 인자, sheet_id: str) -> int:
    service = build_settings_service()
    고름 = service.get_strategy_selection()
    사는키 = ",".join(고름.active_keys)
    파는키 = ",".join(고름.sell_keys)

    # 기준은 시트가 원본이다. 매매 스크립트와 같은 길로 읽어야 화면에 뜬
    # 숫자와 실제 매매가 같은 기준 위에 선다.
    if sheet_id:
        정책제공, 설명, _ = build_policy_provider(service, sheet_id)
        정책 = 정책제공()
        print(설명, file=sys.stderr)
    else:
        정책 = service.get_risk_policy()
        print("시트를 못 찾아 DB 기준으로 돕니다.", file=sys.stderr)
    정책 = 검증용정책(정책)

    session_factory = make_session_factory(bootstrap_settings.database_url)
    유니버스 = active_universe(session_factory, list(UNIVERSE), kind=KIND_MARKET_CAP)
    기준 = 기준글(정책, len(유니버스), "시가총액")
    print(f"■ 대상 {len(유니버스)}종목")

    # 제일 긴 구간 하나만 받아 두고 짧은 것은 거기서 잘라 쓴다. 구간마다
    # 따로 받으면 같은 자료를 세 번 받고, 더 나쁘게는 받는 사이에 값이
    # 바뀌어 구간끼리 비교가 안 된다.
    끝 = 잰때.date()
    가장긴것 = max(골라진것, key=lambda ㄱ: ㄱ.달수)
    처음, _ = 구간(가장긴것, 끝)
    histories = load_histories(
        YahooFinanceDataSource(),
        유니버스,
        처음 - timedelta(days=WARMUP_DAYS),
        끝,
        cache=None if 인자.no_cache else PriceCache(),
    )
    print(f"■ 시세 {len(histories)}종목 · {처음} 앞 예열 포함\n")

    # 전략 여러 개를 견주는 모드. 지금 걸린 것 하나만 재는 것과 섞지 않는다.
    고를것 = 인자.전략.strip()
    if 고를것 not in ("지금", ""):
        열쇠들 = (
            [ㅈ.key for ㅈ in list_definitions()]
            if 고를것 in ("전부", "all")
            else [ㄱ.strip() for ㄱ in 고를것.split(",") if ㄱ.strip()]
        )
        return 비교하기(골라진것, 잰때, sheet_id, histories, 끝, 정책, 기준, 열쇠들)

    # 구간마다 새로 만든다. 전략이 예열 결과를 안에 들고 있어서, 같은
    # 객체를 여러 구간에 쓰면 앞 구간 자료가 남는다.
    def 전략만들기():
        return build_strategies(고름.active_keys, 고름.combine, 고름.sell_keys)

    성적들 = []
    못돌린것 = []
    for 정의 in 골라진것:
        성적 = 돌려보기(정의, 전략만들기, histories, 끝, 정책)
        if 성적 is None:
            못돌린것.append(정의.이름)
            continue
        성적들.append(성적)

    if not 성적들:
        raise RuntimeError(f"시세가 모자라 한 구간도 못 돌렸습니다: {', '.join(못돌린것)}")

    화면에(성적들, 사는키, 파는키, 기준)
    if 못돌린것:
        print(f"못 돌린 구간: {', '.join(못돌린것)} (시세가 모자랍니다)")

    올리기(sheet_id, [줄만들기(잰때, ㅅ, 사는키, 파는키, 기준) for ㅅ in 성적들])
    return 0


def 비교하기(골라진것, 잰때: datetime, sheet_id: str, histories, 끝, 정책,
          기준: str, 열쇠들: list[str]) -> int:
    """등록된 전략들을 **같은 구간·같은 기준**으로 돌려 순위를 낸다.

    빠지는 구간에서 어느 전략이 덜 잃었는지를 보는 자리다. 시세를 한 번만
    받아 전부가 나눠 쓰므로 **모든 전략이 정확히 같은 자료를 본다.** 이게
    어긋나면 비교 자체가 성립하지 않는다.

    **여기서 나온 1등을 그대로 걸면 안 된다.** 한 구간에서 제일 좋았던 것을
    고르는 일이 곧 과최적화다. 이 표는 "지금 걸린 것이 유난히 나쁜가"를
    묻는 자리이지 다음 전략을 정하는 자리가 아니다."""
    print(f"■ 견주는 전략 {len(열쇠들)}개\n")
    올릴것: list[list[str]] = []

    for 정의 in 골라진것:
        줄들 = []
        못만든것 = []
        for 열쇠 in 열쇠들:
            try:
                성적 = 돌려보기(정의, (lambda k=열쇠: build_strategy(k)),
                            histories, 끝, 정책)
            except Exception as 탈:  # noqa: BLE001 — 하나가 터져도 나머지는 봐야 한다
                못만든것.append(f"{열쇠} ({type(탈).__name__}: {탈})")
                continue
            if 성적 is None:
                못만든것.append(f"{열쇠} (시세 부족)")
                continue
            줄들.append((열쇠, 성적))
            올릴것.append(비교줄만들기(잰때, 열쇠, 성적, 기준))

        if not 줄들:
            print(f"[{정의.이름}] 한 전략도 못 돌렸습니다.")
            continue
        비교표(정의, 줄들)
        if 못만든것:
            print(f"  못 돌린 전략: {', '.join(못만든것)}\n")

    if not 올릴것:
        raise RuntimeError("견줄 결과가 한 줄도 안 나왔습니다.")

    print("이 표의 1등을 그대로 걸면 안 됩니다. 한 구간에서 제일 좋았던 것을")
    print("고르는 일이 곧 과최적화입니다. 지금 걸린 것이 유난히 나쁜지를")
    print("묻는 자리이지 다음 전략을 정하는 자리가 아닙니다.")

    if sheet_id:
        from muwon.cloud.sheet_log import append

        올린수 = append(sheet_id, 비교탭, 비교머리, 올릴것)
        print(f"\n시트 '{비교탭}'에 {올린수}줄 올렸습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
