"""보유 상한을 바꿔 가며 전략을 재고 결과를 파일로 남긴다.

설계안은 `docs/단기매매_재설계.md`다.

## 무엇을 하나

전략마다, 보유 상한마다, 슬리피지마다 백테스트를 한 번씩 실행한다. 실행
하나에서 세 가지가 나온다.

1. 매매 하나하나 (진입일, 종목, 보유일수, 수익률, 청산 사유)
2. 보유 상한과 같은 길이로 계좌를 자른 구간의 분포
3. 전 기간을 이어 굴린 누적 수익률과 최대낙폭

기본값으로 실행하면 전략 29개 × 상한 6개 × 슬리피지 3벌 = 522번이다.
한 번에 2초 남짓이라 20분 안팎으로 끝난다.

## 주문은 나가지 않는다

과거 시세로 계산만 한다. 상태 DB에 쓰지 않고 시트도 건드리지 않는다.

## 실행

    python scripts/run_window_scan.py --유니버스 시트 --나온곳 docs/자료/상한훑기.json
    python scripts/run_window_scan.py --상한 5,20 --슬리피지 0,0.001 --전략 volume_surge_3d
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import market_regime as ㄲ
from muwon.analysis import window_perf as ㅇ
from muwon.analysis.market_data import load_histories
from muwon.analysis.period_check import 검증용정책
from muwon.analysis.window_report import 요약찍기
from muwon.backtest.costs import TransactionCosts
from muwon.backtest.engine import BacktestEngine
from muwon.config import bootstrap_settings
from muwon.data.price_cache import PriceCache
from muwon.data.universe import UNIVERSE, Ticker
from muwon.data.universe_builder import KIND_MARKET_CAP, active_universe
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.db.session import make_session_factory
from muwon.risk.manager import RiskManager
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import (
    build_strategies,
    get_definition,
    list_definitions,
)

한국 = ZoneInfo("Asia/Seoul")

#: 시세를 얼마나 앞에서부터 받아 둘 것인가. 이동평균 같은 지표가 첫날부터
#: 값을 내려면 앞이 필요하다. 기간 검증과 같은 값을 쓴다.
예열일수 = 400

#: 기본 시작일. 이 앞은 시세가 고르지 않다.
기본시작 = date(2021, 1, 4)

#: 시장 국면을 가르는 데 쓰는 지수. 코스피 하나만 본다.
코스피심볼 = "^KS11"


def 인자읽기() -> argparse.Namespace:
    ㄱ = argparse.ArgumentParser(description="보유 상한별 전략 측정")
    ㄱ.add_argument("--유니버스", default="시트", help="시트 또는 시가총액")
    ㄱ.add_argument("--시작", default=기본시작.isoformat())
    ㄱ.add_argument("--끝", default="")
    ㄱ.add_argument("--상한", default="", help="쉼표로. 비우면 3,5,7,10,15,20")
    ㄱ.add_argument("--슬리피지", default="0,0.001,0.002", help="비율. 0.001이 0.1%")
    ㄱ.add_argument("--전략", default="", help="쉼표로. 비우면 등록된 전부")
    ㄱ.add_argument("--예수금", type=float, default=5_000_000.0)
    ㄱ.add_argument("--나온곳", default="docs/자료/상한훑기.json")
    ㄱ.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    ㄱ.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    ㄱ.add_argument("--no-cache", action="store_true")
    return ㄱ.parse_args()


def 시트찾기(인자) -> str:
    """시트 아이디. 없으면 드라이브 폴더에서 찾고, 그것도 없으면 빈 글자다.

    설정 서비스에는 이 값이 없다. 다른 스크립트도 전부 인자나 환경변수로
    받는다."""
    if 인자.sheet_id:
        return 인자.sheet_id
    if not 인자.folder_id:
        return ""
    from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

    시트, _ = find_or_create(인자.folder_id, DEFAULT_TITLE)
    return 시트


def 매매대상고르기(인자, session_factory, sheet_id: str):
    """무슨 종목으로 잴 것인가. (종목목록, 열쇠, 사람이 읽는 이름).

    **열쇠와 이름을 따로 돌려주는 이유가 있다.** DB와 파일에는 열쇠를
    적는다. "실거래 시트"라고 적어 두면 나중에 그 글자를 고치는 순간 앞서
    쌓은 줄과 새로 쌓는 줄이 다른 측정으로 갈린다.

    **기본값이 시트다.** 실거래가 실제로 보는 목록이 그것이기 때문이다.
    시가총액 상위 목록으로 재면 나온 숫자가 실제 매매를 설명하지 못한다.
    설계안 §44에서 같은 전략이 목록 하나로 34%p 갈리는 것을 확인했다."""
    고른것 = (인자.유니버스 or "시트").strip()
    if 고른것 in ("시가총액", "market_cap"):
        목록 = active_universe(session_factory, list(UNIVERSE), kind=KIND_MARKET_CAP)
        return 목록, "market_cap", "시가총액"
    if 고른것 not in ("시트", "sheet"):
        raise ValueError(f"모르는 매매 대상입니다: {고른것} (시트 / 시가총액)")
    if not sheet_id:
        raise ValueError("시트를 못 찾아 시트 종목으로 잴 수 없습니다.")

    from muwon.cloud.sector_sheet import read as 섹터시트읽기
    from muwon.data.universe import Ticker

    내용 = 섹터시트읽기(sheet_id)
    목록 = [
        Ticker(symbol=ㅈ.symbol, name=ㅈ.name, market=ㅈ.market,
               yahoo_symbol=ㅈ.yahoo_symbol)
        for ㅅ in 내용.섹터 if ㅅ.활성 for ㅈ in ㅅ.활성종목
    ]
    return 목록, "sheet", "실거래 시트"


def 국면표만들기(시작: date, 끝: date, 인자) -> dict:
    """날짜마다 그날 코스피가 어떤 국면이었는지. 못 받으면 빈 표다.

    두 벌을 만든다. `국면`은 상승·조정·하락이고 `전환`은 그 안을 다시
    나눈 여섯이다. 한 날이 둘에 다 든다. 상승인 날은 상승에도 들고
    상승→조정에도 든다.

    **못 받았다고 측정을 멈추지 않는다.** 국면별로 못 나눌 뿐이고 전체
    숫자는 그대로 쓸 수 있다. 대신 못 받았다는 것을 반드시 찍는다. 조용히
    넘기면 국면 표가 빈 화면을 "그 국면에 구간이 없었다"로 읽는다."""
    try:
        시세 = load_histories(
            YahooFinanceDataSource(),
            [Ticker(symbol="KOSPI", name="코스피", market="KOSPI",
                    yahoo_symbol=코스피심볼)],
            시작, 끝, cache=None if 인자.no_cache else PriceCache(),
        ).get("KOSPI")
        표 = ㄲ.세분나누기(시세)
    except Exception as 탈:  # noqa: BLE001
        print(f"::warning::코스피를 못 받아 국면별로 나누지 못합니다: {탈}")
        return {}

    if not 표.get("국면"):
        print("::warning::코스피 자료가 모자라 국면을 판정하지 못했습니다.")
        return {}

    센것 = {
        ㅈ: sum(1 for ㅂ in 표["전환" if "→" in ㅈ else "국면"].values() if ㅂ == ㅈ)
        for ㅈ in ㄲ.세분국면들
    }
    이제, 값 = ㄲ.지금국면(시세)
    print(f"■ 시장 국면 {센것} · {ㄲ.국면글(이제, 값)}")
    print("  전환 이름표는 구간이 끝나야 정해집니다. 진행 중인 마지막 "
          "구간에는 안 붙습니다.")
    return 표


def 한번재기(
    전략키: str, 상한: int, 슬리피지: float, histories, 정책,
    시작: date, 끝: date, 예수금: float, 매매대상: str, 잰날: date,
    종목수: int = 0, 국면표: dict | None = None,
) -> list[ㅇ.잰것]:
    """전략 하나를 상한 하나, 슬리피지 하나로 실행하고 값을 뽑는다.

    **한 번 실행해서 열을 낸다.** 전체와 국면 셋, 전환 여섯이다. 백테스트를
    열 번 돌리는 것이 아니라, 한 번 돌린 결과의 구간과 매매를 국면으로
    나눠 각각 집계한다. 국면 구간은 띄엄띄엄 떨어져 있어서 따로 이어
    굴릴 수가 없다.

    **국면 셋과 전환 여섯은 겹친다.** 상승 41구간이 상승→조정 29개와
    상승→하락 12개로 다시 나뉜다. 같은 구간을 두 이름으로 두 번 담는
    것이라 더해서 읽으면 안 된다.

    **전략 객체를 실행마다 새로 만든다.** 전략이 예열 결과를 안에 들고
    있어서, 같은 객체를 여러 번 쓰면 앞 실행의 자료가 남는다."""
    쓸정책 = replace(정책, max_holding_days=상한)
    결과 = BacktestEngine(
        strategy=build_strategies([전략키]),
        risk_manager=RiskManager(policy_provider=lambda p=쓸정책: p),
        costs=TransactionCosts(slippage_pct=슬리피지),
        entry_at_open=True,
        exit_at_open=True,
        initial_cash=예수금,
    ).run(histories, trade_from=시작)

    안겹친구간 = ㅇ.구간나누기(결과.equity_curve, 상한, 겹치게=False)
    안겹친것 = ㅇ.구간재기(안겹친구간, 상한, 겹침=False)
    겹친것 = ㅇ.구간재기(
        ㅇ.구간나누기(결과.equity_curve, 상한, 겹치게=True), 상한, 겹침=True)

    곡선 = 결과.equity_curve
    누적 = 낙폭 = None
    if len(곡선):
        처음값 = float(곡선["equity"].iloc[0])
        if 처음값 > 0:
            누적 = (float(곡선["equity"].iloc[-1]) / 처음값 - 1) * 100
        꼭지 = 0.0
        낙폭 = 0.0
        for ㄱ in (float(ㅂ) for ㅂ in 곡선["equity"]):
            꼭지 = max(꼭지, ㄱ)
            if 꼭지 > 0:
                낙폭 = min(낙폭, (ㄱ - 꼭지) / 꼭지 * 100)

    공통 = {
        "전략": 전략키, "상한": 상한, "슬리피지": 슬리피지,
        "매매대상": 매매대상, "시작일": 시작, "끝일": 끝, "잰날": 잰날,
        "종목수": 종목수,
    }
    나온것 = [ㅇ.잰것(
        **공통, 국면=ㄲ.전체,
        구간=안겹친것, 겹친구간=겹친것,
        매매=ㅇ.매매재기(결과.closed_trades, 미청산수=len(결과.final_positions)),
        누적수익률=누적, 최대낙폭=낙폭,
    )]

    if not (국면표 or {}).get("국면"):
        return 나온것

    for 국면 in ㄲ.세분국면들:
        쓸표 = 국면표["전환" if "→" in 국면 else "국면"]
        고른구간 = ㅇ.국면고르기(안겹친구간, 쓸표, 국면)
        고른매매 = ㅇ.국면매매(결과.closed_trades, 쓸표, 국면)
        나온것.append(ㅇ.잰것(
            **공통, 국면=국면,
            구간=ㅇ.구간재기(고른구간, 상한, 겹침=False),
            # 겹친 구간은 그림용이라 국면별로는 안 만든다.
            겹친구간=None,
            매매=ㅇ.매매재기(고른매매),
            # **누적 수익률과 최대낙폭은 비운다.** 국면 구간이 띄엄띄엄
            # 떨어져 있어서 이어 붙이면 없던 매매를 만든 셈이 된다.
            누적수익률=None, 최대낙폭=None,
        ))
    return 나온것


def _구간칸(ㄱ: ㅇ.구간성적 | None) -> dict | None:
    if ㄱ is None:
        return None
    return {
        "길이": ㄱ.길이, "겹침": ㄱ.겹침, "구간수": ㄱ.구간수,
        "기하평균": ㄱ.기하평균, "연환산": ㄱ.연환산, "산술평균": ㄱ.산술평균,
        "중앙값": ㄱ.중앙값, "플러스비율": ㄱ.플러스비율,
        "하위10": ㄱ.하위10, "하위25": ㄱ.하위25,
        "최악": ㄱ.최악, "최고": ㄱ.최고, "표준편차": ㄱ.표준편차,
        "하락대비수익": ㄱ.하락대비수익, "구간낙폭중앙값": ㄱ.구간낙폭중앙값,
        "믿을만한가": ㄱ.믿을만한가,
    }


def 줄로(ㄱ: ㅇ.잰것) -> dict:
    """파일에 적을 모양. 조건을 값과 같이 남긴다."""
    이름 = ㄱ.전략
    try:
        이름 = get_definition(ㄱ.전략).화면이름
    except (KeyError, AttributeError):
        # 이름을 못 찾는다고 측정이 멈추면 안 된다. 키를 그대로 쓴다.
        이름 = ㄱ.전략
    return {
        "전략": ㄱ.전략, "이름": 이름, "상한": ㄱ.상한, "슬리피지": ㄱ.슬리피지,
        "매매대상": ㄱ.매매대상, "종목수": ㄱ.종목수, "국면": ㄱ.국면,
        "시작일": ㄱ.시작일.isoformat(), "끝일": ㄱ.끝일.isoformat(),
        "구간": _구간칸(ㄱ.구간), "겹친구간": _구간칸(ㄱ.겹친구간),
        "매매": {
            "매매수": ㄱ.매매.매매수, "승률": ㄱ.매매.승률,
            "손익비": ㄱ.매매.손익비, "기대수익": ㄱ.매매.기대수익,
            "중앙값": ㄱ.매매.중앙값, "평균보유일수": ㄱ.매매.평균보유일수,
            "갈래비율": ㄱ.매매.갈래비율, "미청산수": ㄱ.매매.미청산수,
            "기간만료비율": ㄱ.매매.기간만료비율,
        },
        "누적수익률": ㄱ.누적수익률, "최대낙폭": ㄱ.최대낙폭,
        "이_상한에_맞나": ㄱ.이_상한에_맞나,
    }


def main() -> int:
    인자 = 인자읽기()
    상한들 = ([int(ㄱ) for ㄱ in 인자.상한.split(",") if ㄱ.strip()]
            if 인자.상한 else list(ㅇ.상한들))
    슬리피지들 = [float(ㄱ) for ㄱ in 인자.슬리피지.split(",") if ㄱ.strip()]
    전략들 = ([ㄱ.strip() for ㄱ in 인자.전략.split(",") if ㄱ.strip()]
            if 인자.전략 else [ㅈ.key for ㅈ in list_definitions()])

    시작 = date.fromisoformat(인자.시작)
    끝 = date.fromisoformat(인자.끝) if 인자.끝 else datetime.now(한국).date()
    잰날 = datetime.now(한국).date()

    설정 = build_settings_service()
    # 스위치 둘은 켠 채로 실행한다. 여기서 묻는 것은 "오늘 주문을 낼
    # 것인가"가 아니라 "이 전략을 굴렸으면 어땠나"라 그 값과 상관이 없다.
    정책 = 검증용정책(설정.get_risk_policy())
    sheet_id = 시트찾기(인자)
    session_factory = make_session_factory(bootstrap_settings.database_url)
    매매대상, 대상열쇠, 대상이름 = 매매대상고르기(인자, session_factory, sheet_id)

    print(f"■ 전략 {len(전략들)}개 · 상한 {상한들} · 슬리피지 {슬리피지들}")
    print(f"■ 매매 대상 {len(매매대상)}종목 ({대상이름}) · {시작} ~ {끝}")
    print(f"■ 실행 {len(전략들) * len(상한들) * len(슬리피지들)}번\n")

    histories = load_histories(
        YahooFinanceDataSource(), 매매대상,
        시작 - timedelta(days=예열일수), 끝,
        cache=None if 인자.no_cache else PriceCache(),
    )
    if not histories:
        print("::error::시세를 하나도 못 받았습니다.")
        return 1
    print(f"■ 시세 {len(histories)}종목")

    국면표 = 국면표만들기(시작 - timedelta(days=예열일수), 끝, 인자)
    print()

    줄들 = []
    못한것 = []
    선때 = time.time()
    for 키 in 전략들:
        for 상한 in 상한들:
            for 슬립 in 슬리피지들:
                try:
                    잰것들 = 한번재기(키, 상한, 슬립, histories, 정책, 시작, 끝,
                                  인자.예수금, 대상열쇠, 잰날, len(매매대상),
                                  국면표)
                except Exception as 탈:  # noqa: BLE001
                    # 하나가 죽었다고 나머지를 버리지 않는다. 대신 조용히
                    # 넘기지 않고 못한 것으로 남겨 마지막에 출력한다.
                    못한것.append(f"{키} 상한{상한} 슬립{슬립}: {탈}")
                    continue
                줄들.extend(줄로(ㄱ) for ㄱ in 잰것들)
        마침 = next((ㄹ for ㄹ in 줄들[::-1]
                   if ㄹ["전략"] == 키 and ㄹ.get("국면", ㄲ.전체) == ㄲ.전체), None)
        if 마침:
            연 = 마침["구간"]["연환산"] if 마침["구간"] else None
            print(f"  {마침['이름']:24} 마지막 상한 연환산 "
                  f"{'계산 못 함' if 연 is None else f'{연:+.1f}%'}")

    요약찍기(줄들, 상한들, 슬리피지들)

    나온곳 = Path(인자.나온곳)
    나온곳.parent.mkdir(parents=True, exist_ok=True)
    나온곳.write_text(json.dumps({
        "설명": "보유 상한을 바꿔 가며 전략을 잰 것입니다. 구간 길이는 상한과 같습니다.",
        "잰날": 잰날.isoformat(),
        "기간": f"{시작} ~ {끝}",
        "매매대상": f"{대상이름} {len(매매대상)}종목",
        "매매대상열쇠": 대상열쇠,
        "종목수": len(매매대상),
        "설정": (f"비중 {정책.max_position_weight * 100:.0f}% · "
               f"동시보유 {정책.max_concurrent_positions}종목 · "
               f"손절 {정책.stop_loss_pct * 100:.0f}% · "
               f"예수금 {인자.예수금:,.0f}원 · 다음 날 시가 체결"),
        "주의": ("구간이 겹치지 않는 것을 판단에 씁니다. 겹친구간은 그림용이고 "
               "표본 수가 부풀려져 있습니다. 연환산은 상한이 다른 전략을 같은 "
               "줄에 놓기 위한 값이라 짧은 상한일수록 크게 나옵니다. "
               "매매 대상이 지금 살아 있는 종목이라 과거로 가져가면 살아남은 "
               "회사만 봅니다."),
        "상한들": 상한들, "슬리피지들": 슬리피지들,
        "줄": 줄들,
        "못한것": 못한것,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    걸린때 = time.time() - 선때
    print(f"\n■ {나온곳}: {len(줄들)}줄 · {걸린때 / 60:.1f}분")
    if 못한것:
        print(f"■ 못 잰 것 {len(못한것)}개")
        for ㄱ in 못한것[:10]:
            print(f"   {ㄱ}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
