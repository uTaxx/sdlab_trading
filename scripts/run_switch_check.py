"""매일 검토해서 1위로 갈아탔다면 지난달 수익률이 얼마였을까.

전략 검토(17:50)는 구간마다 순위를 내고 후보를 보여 준다. 그 후보를 따라간
경우와 그냥 두었던 경우를 지나간 구간에 대고 비교한다. 그림자 추적(설계안
§39)이 앞으로 재는 것과 같은 질문인데, 이쪽은 이미 지나간 날을 본다.

## 계산 순서

    1. 날마다 그날까지의 시세로 구간 순위를 내고 1위를 뽑는다
    2. 그 답을 다음 거래일부터 쓴다 (실거래의 17:50 → 08:20 → 08:30과 같다)
    3. 전략이 날마다 갈리는 계좌 하나를 처음부터 끝까지 굴린다
    4. 같은 구간을 전략 하나로 고정해서 굴린 것들과 견준다

## 주의

**한 달은 판단할 표본이 아니다.** 거래가 스무 건을 넘기기 어렵고, 크게
움직인 한두 종목이 숫자의 대부분을 만든다. 이 스크립트는 "매일 갈아타는
것이 좋은가"에 답하지 못한다. 지난 한 달에 그렇게 했으면 무슨 일이
일어났는지를 보여 줄 뿐이다.

**주문은 나가지 않는다.** 상태 DB도 고치지 않는다.

사용 예:
    python scripts/run_switch_check.py
    python scripts/run_switch_check.py --구간 1개월 --끝 2026-09-01
    python scripts/run_switch_check.py --슬리피지 0.001
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.market_data import load_histories
from muwon.analysis.period_check import 구간, 기간정의, 기간표, 돌려보기
from muwon.analysis.switching import (
    갈아타기규칙,
    갈아타기전략,
    굴리기,
    규칙적용,
    날마다고르기,
    하루선택,
)
from muwon.backtest.costs import TransactionCosts
from muwon.data.price_cache import PriceCache
from muwon.data.universe import Ticker
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.sector.catalog import CATALOG
from muwon.settings.schema import RiskPolicy
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

서울 = ZoneInfo("Asia/Seoul")

#: 갈아탄 결과를 견줄 상대. 지금 설정된 것과 다음 거래일에 반영될 것이다.
기준전략 = ("volume_surge_5d_ma20", "volatility_breakout_k05")


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).화면이름
    except Exception:  # noqa: BLE001 (이름을 못 찾는다고 계산이 멈추면 안 된다)
        return 키


def 구간고르기(글: str) -> 기간정의:
    """`1주`처럼 기간표에 있는 이름이거나 `14일`처럼 날수다.

    2주는 기간표에 없다. 화면 드롭다운과 워크플로 입력이 그 표를 같이
    읽으므로, 여기서 한 번 재 보려고 표에 넣으면 제품 쪽이 같이 바뀐다.
    그래서 이 스크립트에서만 쓰는 임시 구간을 만든다."""
    if 글 in 기간표:
        return 기간표[글]
    맞음 = re.fullmatch(r"(\d+)일", 글.strip())
    if not 맞음:
        raise SystemExit(f"모르는 구간입니다: {글} (기간표의 이름이나 `14일` 꼴)")
    날수 = int(맞음.group(1))
    if 날수 < 1:
        raise SystemExit("구간은 하루 이상이어야 합니다.")
    return 기간정의(
        이름=글.strip(), 달수=0, 나눔="날", 날수=날수,
        설명="이 스크립트에서만 쓰는 구간입니다. 화면에서 고를 수 있는 구간이 아닙니다",
    )


def 섹터표만들기() -> dict[str, str]:
    """종목코드 → 섹터코드. 섹터 상한을 걸려면 이것이 있어야 한다."""
    return {m.symbol: s.코드 for s in CATALOG for m in s.종목}


def 대상종목() -> list[Ticker]:
    """매매 대상 종목.

    실거래는 구글 시트의 종목 탭을 읽는다. 시트에 못 닿을 때는 시트를 처음
    채운 씨앗 목록(`sector/catalog.py`)을 쓴다. **둘이 갈릴 수 있다.**
    갈리면 여기서 낸 순위가 실제 매매를 설명하지 못하므로, 어느 쪽을 썼는지
    결과에 반드시 적는다."""
    return [
        Ticker(
            symbol=m.symbol,
            name=m.name,
            market=m.market,
            yahoo_symbol=f"{m.symbol}.{'KQ' if m.market == 'KOSDAQ' else 'KS'}",
        )
        for s in CATALOG
        if s.활성
        for m in s.종목
        if m.활성
    ]


def 거래일들(histories) -> list[date]:
    return sorted({ㄴ for df in histories.values() for ㄴ in df["trade_date"]})


def 잴날과적용날(전체거래일: list[date], 시작: date, 끝: date):
    """(잴 날, 적용할 날) 짝. **잰 날은 언제나 적용할 날보다 앞이다.**

    D일 저녁에 D일까지의 시세로 순위를 내고, 그 답을 D+1일부터 쓴다. 같은
    날로 두면 그날 오를 종목을 미리 보고 고른 것이 되어 결과가 통째로 뜻이
    없어진다."""
    쓸날 = [ㄴ for ㄴ in 전체거래일 if 시작 <= ㄴ <= 끝]
    짝 = []
    for 적용날 in 쓸날:
        앞 = [ㄴ for ㄴ in 전체거래일 if ㄴ < 적용날]
        if 앞:
            짝.append((앞[-1], 적용날))
    return [ㄱ for ㄱ, _ in 짝], [ㄴ for _, ㄴ in 짝]


def 고정성적(histories, 정의, 끝, 정책, costs, 제약):
    """전략 하나로 그 구간을 고정해서 굴린 결과. 갈아탄 것과 견줄 상대다."""
    나온것 = []
    for ㅈ in list_definitions():
        try:
            성적 = 돌려보기(정의, (lambda k=ㅈ.key: build_strategy(k)), histories, 끝,
                        정책, costs=costs, **제약)
        except Exception as 탈:  # noqa: BLE001
            print(f"  건너뜀 {ㅈ.key} ({type(탈).__name__})", file=sys.stderr)
            continue
        if 성적 is not None:
            나온것.append((ㅈ.key, 성적))
    return 나온것


def 날마다순위(정의, histories, 시작, 끝, 정책, costs, 처음키, 전략키들, 제약) -> list[하루선택]:
    """날마다 순위를 낸다. **이 계산이 전체 시간의 거의 전부다.**

    전략 27개를 하루마다 다시 굴리므로 한 날에 1분쯤 걸린다. 그래서 결과를
    파일로 남겨 두고, 규칙을 바꿔 볼 때는 순위를 다시 내지 않는다."""
    잴날들, 적용날들 = 잴날과적용날(거래일들(histories), 시작, 끝)
    print(f"\n[{정의.이름}] {시작} ~ {끝} · 매매일 {len(적용날들)}일 · "
          f"전략 {len(전략키들)}개 → 계산 {len(적용날들) * len(전략키들)}회", file=sys.stderr)

    시작때 = time.time()

    def 한줄(선택):
        표시 = "→ " + 전략이름(선택.고른키) if 선택.바꿨나 else "그대로"
        print(f"  {선택.잰날} 저녁 → {선택.적용날} 적용  {표시}"
              f"  ({time.time() - 시작때:.0f}초)", file=sys.stderr)

    return 날마다고르기(정의, histories, 잴날들, 적용날들, 정책, 전략키들,
                    build_strategy, 처음키, costs=costs, 알림=한줄, **제약)


def 순위저장(선택들: list[하루선택], 경로: str) -> None:
    Path(경로).write_text(
        json.dumps(
            [
                {"잰날": str(ㅅ.잰날), "적용날": str(ㅅ.적용날), "앞선전략": ㅅ.앞선키,
                 "거래없음수": ㅅ.거래없음수,
                 "순위": [[ㄱ, ㄴ, ㄷ, ㄹ] for ㄱ, ㄴ, ㄷ, ㄹ in ㅅ.전체]}
                for ㅅ in 선택들
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def 순위읽기(경로: str) -> list[하루선택]:
    """저장해 둔 순위를 그대로 되살린다. 고른 키는 순위에서 다시 뽑는다."""
    나온것 = []
    for ㅈ in json.loads(Path(경로).read_text(encoding="utf-8")):
        전체 = [(ㄱ, ㄴ, ㄷ, ㄹ) for ㄱ, ㄴ, ㄷ, ㄹ in ㅈ["순위"]]
        나온것.append(
            하루선택(
                잰날=date.fromisoformat(ㅈ["잰날"]),
                적용날=date.fromisoformat(ㅈ["적용날"]),
                고른키=전체[0][0] if 전체 else ㅈ["앞선전략"],
                앞선키=ㅈ["앞선전략"],
                위쪽=[(ㄱ, ㄴ, ㄷ) for ㄱ, ㄴ, ㄷ, _ in 전체[:3]],
                거래없음수=ㅈ.get("거래없음수", 0),
                전체=전체,
            )
        )
    return 나온것


#: 견줄 규칙들. 순위는 한 번만 내고 여기에 얹는다.
규칙들 = (
    갈아타기규칙("매일 1위", "그날 1위면 무조건 갈아탄다", 지금없어도바꾼다=True),
    갈아타기규칙("1.15배 앞설 때만", "조금 앞서는 정도로는 안 바꾼다", 우위배수=1.15),
    갈아타기규칙("거래 20건 넘을 때만", "표본이 모자란 1위는 따라가지 않는다",
              우위배수=1.15, 최소거래수=20),
    갈아타기규칙("실제 검토 규칙", "1.15배 + 최소 운용 30일. 지금 설정된 것이다",
              우위배수=1.15, 최소운용일=30),
)


def 시작전_한달1위(histories, 시작, 정책, costs, 제약, 전략키들) -> str:
    """매매를 시작하기 **전날까지의** 시세로 낸 1개월 순위의 1위.

    갈아타기와 1:1로 견줄 상대다. "최근 1개월에 제일 좋았던 것을 골라 그냥
    두었다면"이 그 뜻인데, **지나고 나서 제일 좋았던 것을 고르면 안 된다.**
    그건 미래를 보고 고른 것이라 어떤 규칙도 이길 수 없다.

    그래서 시작일보다 앞선 마지막 거래일을 잰 날로 쓴다. 그날까지의 시세만
    본다."""
    앞선날 = [ㄴ for ㄴ in 거래일들(histories) if ㄴ < 시작]
    if not 앞선날:
        print("  시작일 앞에 거래일이 없어 1개월 1위를 못 고릅니다.", file=sys.stderr)
        return ""
    잰날 = 앞선날[-1]
    print(f"\n[견줄 상대] {잰날}까지의 1개월 성적으로 1위를 고릅니다", file=sys.stderr)
    선택 = 날마다고르기(
        기간표["1개월"], histories, [잰날], [시작], 정책, 전략키들,
        build_strategy, "", costs=costs, **제약,
    )[0]
    if not 선택.전체:
        print("  그 구간에 매수가 발생한 전략이 없습니다.", file=sys.stderr)
        return ""
    키, 수익률, 거래수, _ = 선택.전체[0]
    print(f"  {전략이름(키)} {수익률:+.2f}% ({거래수}건)", file=sys.stderr)
    return 키


def 재보기(이름, 날짜별키, histories, 시작, 끝, 정책, costs, 처음키, 제약):
    전략 = 갈아타기전략(날짜별키, build_strategy, 처음키)
    굴린것 = 굴리기(histories, 전략, 시작, 끝, 정책, costs=costs, **제약)
    if 굴린것 is None:
        return None
    결과, ㅁ = 굴린것
    바꾼수 = sum(1 for ㄱ, ㄴ in zip(list(날짜별키.values())[:-1],
                                 list(날짜별키.values())[1:], strict=False) if ㄱ != ㄴ)
    처음바꿈 = 1 if 날짜별키 and next(iter(날짜별키.values())) != 처음키 else 0
    실현, 평가 = 실현과평가(결과, 예수금=제약.get("예수금", 10_000_000.0))
    return {"이름": 이름, "수익률": ㅁ.total_return_pct, "거래수": ㅁ.num_trades,
            "승률": ㅁ.win_rate_pct, "최대낙폭": ㅁ.max_drawdown_pct,
            "변경횟수": 바꾼수 + 처음바꿈,
            # **끝에 들고 있던 종목 수.** 거래수는 사고판 것만 센다. 짧은
            # 구간에서는 사고 파는 한 바퀴가 안 끝나서 거래수가 0인데 실제로는
            # 여러 종목을 들고 있는 일이 흔하다. 그 칸이 없으면 "아무것도 안
            # 샀다"로 읽는다. 2026-09-01에 그렇게 읽고 한 번 잘못 보고했다.
            "보유수": len(결과.final_positions),
            "실현": 실현, "평가": 평가,
            "쓴전략수": len(set(날짜별키.values()))}


def 실현과평가(결과, 예수금: float) -> tuple[float, float]:
    """수익률을 실현된 것과 아직 안 판 것으로 가른다. 둘 다 비율(%)이다.

    수익률에는 이미 둘이 다 들어 있다. 평가금액 곡선으로 재기 때문에 들고
    있는 종목도 그날 값으로 반영된다. 실제 계좌도 그렇게 계산한다.

    그런데 합쳐 놓으면 **판 돈으로 번 것과 아직 장부에만 있는 것이 구별되지
    않는다.** 구간이 짧으면 대부분이 뒤쪽이고, 그건 하루 뒤에 다시 재면
    달라지는 값이다. 갈라 놓아야 그 사실이 보인다.

    실현은 사고 판 것까지 끝난 매매의 손익 합이다. 평가는 끝에 들고 있던
    종목을 그날 값으로 매겼을 때의 손익이다. 둘의 합이 수익률과 딱 맞지는
    않는다. 아직 안 판 종목을 살 때 낸 수수료가 어느 쪽에도 안 들어가기
    때문이다. 그 차이는 수수료 수준이라 화면에서는 무시한다."""
    if 예수금 <= 0:
        return 0.0, 0.0
    실현 = sum(ㅌ.pnl_amount for ㅌ in 결과.closed_trades)

    곡선 = 결과.equity_curve
    if len(곡선) == 0 or "cash" not in 곡선:
        return 실현 / 예수금 * 100, 0.0
    # 결제가 안 끝난 매도 대금은 평가금액에는 있지만 현금은 아니다. 안 빼면
    # 잠긴 돈이 통째로 "아직 안 판 수익"으로 잡힌다(T+2를 켜고 처음 드러났다).
    잠긴돈 = float(곡선["결제대기"].iloc[-1]) if "결제대기" in 곡선 else 0.0
    보유평가액 = (
        float(곡선["equity"].iloc[-1]) - float(곡선["cash"].iloc[-1]) - 잠긴돈
    )
    보유원가 = sum(ㅍ.quantity * ㅍ.entry_price for ㅍ in 결과.final_positions.values())
    return 실현 / 예수금 * 100, (보유평가액 - 보유원가) / 예수금 * 100


def main() -> int:
    ㅍ = argparse.ArgumentParser(description="매일 검토해서 갈아탔다면 어땠을까")
    ㅍ.add_argument("--구간", default="1개월",
                   help="순위를 낼 때 되돌아볼 길이. 기간표의 이름(1주·1개월·3개월…) "
                        "또는 `14일`처럼 날수로 적는다")
    ㅍ.add_argument("--재는구간", default="",
                   help="성적을 잴 길이. 비우면 --구간과 같다. 되돌아보는 길이만 "
                        "바꿔서 견주려면 이 값을 고정한다")
    ㅍ.add_argument("--끝", default="", help="마지막 날 (YYYY-MM-DD). 비우면 오늘")
    ㅍ.add_argument("--슬리피지", type=float, default=0.0,
                   help="편도 체결 오차. 자주 갈아타면 거래가 늘어 여기에 민감해진다")
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0,
                   help="시작 자금")
    ㅍ.add_argument("--비중", type=float, default=0.15,
                   help="한 종목에 최대 몇 배까지. 0.15면 15%%")
    ㅍ.add_argument("--동시보유", type=int, default=6,
                   help="동시에 몇 종목까지")
    ㅍ.add_argument("--섹터당", type=int, default=3,
                   help="한 섹터에서 몇 종목까지. 0이면 제한 없음")
    ㅍ.add_argument("--섹터상한셈", default="하루후보", choices=["하루후보", "보유전체"],
                   help="하루후보는 그날 새로 사는 것만 센다(실거래가 하는 일). "
                        "보유전체는 이미 들고 있는 것까지 세는 진짜 보유 한도다")
    ㅍ.add_argument("--손절", type=float, default=-0.05, help="예: -0.05면 -5%%")
    # 기본값 0은 "끔"이다. 지금 설정도 0이고, 전략 평가 결과도 전부 0에서
    # 나온 숫자다. 값을 주면 그 순간부터 다른 조건의 계산이 되므로, 앞서
    # 낸 숫자와 견줄 때는 두 번 돌려서 나란히 놓아야 한다.
    ㅍ.add_argument("--익절", type=float, default=0.0,
                   help="목표 수익률에 닿으면 판다. 예: 0.10이면 +10%%. 0이면 끔")
    ㅍ.add_argument("--결제일수", type=int, default=0,
                   help="판 돈이 며칠 뒤에 쓸 수 있게 되나. 한국 주식은 2(T+2)다. "
                        "0이면 판 즉시 다시 살 수 있다(지금까지의 모든 숫자가 이 조건)")
    ㅍ.add_argument("--하루손실", type=float, default=-0.03,
                   help="하루에 이만큼 잃으면 그날 신규 매수 중단")
    ㅍ.add_argument("--점수순", action="store_true", default=True,
                   help="자리가 모자랄 때 신호 점수가 높은 것부터 산다(실거래가 하는 일)")
    ㅍ.add_argument("--점수순끔", dest="점수순", action="store_false",
                   help="시세를 받은 순서대로 산다(옛 백테스트 동작)")
    ㅍ.add_argument("--처음전략", default="1개월1위",
                   help="무엇으로 시작하나. `1개월1위`면 매매 시작 전날까지의 "
                        "1개월 성적 1위로 시작한다(갈아타는 쪽과 안 바꾸는 쪽이 "
                        "같은 자리에서 출발한다). 전략 키를 적으면 그것으로 시작한다")
    ㅍ.add_argument("--순위저장", default="", help="날마다 낸 순위를 남길 경로")
    ㅍ.add_argument("--순위읽기", default="",
                   help="남겨 둔 순위를 다시 쓴다. 규칙만 바꿔 볼 때 쓴다")
    인자 = ㅍ.parse_args()

    끝 = date.fromisoformat(인자.끝) if 인자.끝 else datetime.now(서울).date()
    정의 = 구간고르기(인자.구간)
    # 순위를 내려고 되돌아보는 길이와, 성적을 재는 길이는 다른 것이다. 둘을
    # 묶어 두면 1주로 고른 경우는 엿새치만, 3개월로 고른 경우는 석 달치를
    # 재게 되어 서로 견줄 수가 없다.
    잴정의 = 구간고르기(인자.재는구간) if 인자.재는구간 else 정의
    시작, _ = 구간(잴정의, 끝)
    정책 = RiskPolicy(
        max_position_weight=인자.비중,
        max_concurrent_positions=인자.동시보유,
        stop_loss_pct=인자.손절,
        take_profit_pct=인자.익절,
        daily_loss_limit_pct=인자.하루손실,
    )
    costs = TransactionCosts(slippage_pct=인자.슬리피지)
    전략키들 = [ㅈ.key for ㅈ in list_definitions()]
    제약 = {
        "섹터표": 섹터표만들기(),
        "섹터상한": 인자.섹터당,
        "섹터상한셈": 인자.섹터상한셈,
        "점수순": 인자.점수순,
        "결제일수": 인자.결제일수,
        "예수금": 인자.예수금,
    }

    histories = load_histories(
        YahooFinanceDataSource(), 대상종목(), 시작 - timedelta(days=900), 끝,
        cache=PriceCache(".cache/prices.sqlite"),
    )
    if not histories:
        print("시세를 하나도 못 받았습니다.", file=sys.stderr)
        return 1

    # **무엇으로 시작하나를 먼저 정한다.**
    #
    # 갈아타는 쪽과 안 바꾸는 쪽이 다른 전략에서 출발하면 1:1 비교가 아니다.
    # 둘 다 "매매 시작 전날까지의 1개월 성적 1위"에서 출발시킨다. 그 뒤로
    # 한쪽은 날마다 바꾸고 한쪽은 그대로 둔다.
    한달1위 = 시작전_한달1위(histories, 시작, 정책, costs, 제약, 전략키들)
    if 인자.처음전략 == "1개월1위":
        처음키 = 한달1위 or 기준전략[0]
        if not 한달1위:
            print("  1개월 1위를 못 골라 지금 설정된 전략으로 시작합니다.", file=sys.stderr)
    else:
        처음키 = 인자.처음전략
    print(f"■ 시작 전략: {전략이름(처음키)}", file=sys.stderr)

    if 인자.순위읽기:
        선택들 = 순위읽기(인자.순위읽기)
        print(f"순위 {len(선택들)}일치를 읽었습니다: {인자.순위읽기}", file=sys.stderr)
    else:
        선택들 = 날마다순위(정의, histories, 시작, 끝, 정책, costs, 처음키,
                       전략키들, 제약)
        if 인자.순위저장:
            순위저장(선택들, 인자.순위저장)

    print("\n" + "=" * 72)
    print(f"{정의.이름} 순위를 보고 갈아탔다면   {시작} ~ {끝} ({잴정의.이름}치)")
    # 익절을 켠 실행과 끈 실행이 로그만 보고는 구별이 안 되면, 나중에 두
    # 숫자를 견줄 때 어느 쪽이 어느 조건이었는지 알 수 없다.
    익절글 = f"익절 +{인자.익절 * 100:.0f}%" if 인자.익절 else "익절 끔"
    결제글 = f"결제 T+{인자.결제일수}" if 인자.결제일수 else "결제 즉시(T+0)"
    print(f"대상종목 {len(histories)}개 · 슬리피지 {인자.슬리피지 * 100:.2f}% · "
          f"손절 {인자.손절 * 100:.0f}% · {익절글} · {결제글} · "
          f"검토 {len(선택들)}일 · 되돌아본 길이 {정의.이름}")
    print("=" * 72)

    잰것 = []
    for 규 in 규칙들:
        ㅈ = 재보기(규.이름, 규칙적용(선택들, 처음키, 규),
                 histories, 시작, 끝, 정책, costs, 처음키, 제약)
        if ㅈ:
            ㅈ["설명"] = 규.설명
            잰것.append(ㅈ)

    # **시작 시점에 최근 1개월 1위였던 전략을 그대로 두는 경우.**
    #
    # 갈아타기와 1:1로 견줄 상대다. 지나고 나서 제일 좋았던 것을 고르면
    # 미래를 보고 고른 것이 되어 비교가 안 된다. 그래서 매매를 시작하기 전
    # 마지막 거래일까지의 시세로만 순위를 내고 그 1위를 잡는다.
    if 한달1위:
        ㅈ = 재보기(f"안 바꿈: {전략이름(한달1위)} (시작 시점 1개월 1위)",
                 {}, histories, 시작, 끝, 정책, costs, 한달1위, 제약)
        if ㅈ:
            ㅈ["설명"] = ("매매를 시작하기 전날까지의 1개월 성적으로 고른 1위를 "
                       "그대로 두었다. 미래를 안 보고 고른 값이다")
            잰것.append(ㅈ)

    for 키 in 기준전략:
        ㅈ = 재보기(f"안 바꿈: {전략이름(키)}", {}, histories, 시작, 끝, 정책, costs, 키, 제약)
        if ㅈ:
            ㅈ["설명"] = f"그 전략 하나로 {잴정의.이름}을 그대로 갔다"
            잰것.append(ㅈ)

    print(f"\n  {'규칙':30} {'수익률':>8} {'  판것':>8} {'  안판것':>8} "
          f"{'사고판것':>7} {'끝에보유':>7} {'최대낙폭':>9} {'변경':>5}")
    print("  " + "-" * 94)
    for ㅈ in 잰것:
        print(f"  {ㅈ['이름']:30} {ㅈ['수익률']:+7.2f}% {ㅈ['실현']:+7.2f}% "
              f"{ㅈ['평가']:+7.2f}% {ㅈ['거래수']:6}건 {ㅈ['보유수']:6}개 "
              f"{ㅈ['최대낙폭']:+8.2f}% {ㅈ['변경횟수']:4}회")
    print("\n  수익률은 판것과 안판것을 합친 값입니다. 실제 계좌도 그렇게 셉니다.")
    print("  판것은 사고 판 것까지 끝난 매매의 손익입니다.")
    print("  안판것은 끝에 들고 있던 종목을 그날 값으로 매긴 손익이고, **하루 "
          "뒤에 다시 재면 달라지는 값입니다.**")
    print("  구간이 짧으면 한 바퀴가 안 끝나서 안판것 쪽이 커집니다.")
    print()
    for ㅈ in 잰것:
        print(f"    {ㅈ['이름']:22} {ㅈ['설명']}")

    print("\n" + "-" * 72)
    print("  날마다 1위가 무엇이었나 (매일 1위 규칙이 따라간 것)")
    print("-" * 72)
    앞 = 처음키
    for ㅅ in 선택들:
        위 = " · ".join(f"{전략이름(ㄱ)} {ㄴ:+.1f}%({ㄷ}건)" for ㄱ, ㄴ, ㄷ in ㅅ.위쪽[:2])
        print(f"  {ㅅ.적용날}  {전략이름(ㅅ.고른키):22} "
              f"{'바꿈' if ㅅ.고른키 != 앞 else '    '}  [{위}]")
        앞 = ㅅ.고른키

    print("\n" + "-" * 72)
    print(f"같은 구간을 전략 하나로 고정했을 때 ({잴정의.이름} 통째로)")
    print("-" * 72)
    고정 = 고정성적(histories, 잴정의, 끝, 정책, costs, 제약)
    고정.sort(key=lambda ㅌ: -ㅌ[1].수익률)
    for i, (키, 성적) in enumerate(고정, 1):
        표 = ""
        if 키 == 기준전략[0]:
            표 = "  ← 지금 설정된 것"
        elif 키 == 기준전략[1]:
            표 = "  ← 다음 거래일에 반영될 것"
        print(f"  {i:2}. {전략이름(키):22} {성적.수익률:+7.2f}%  "
              f"{성적.metrics.num_trades:3}건  낙폭 {성적.metrics.max_drawdown_pct:+6.2f}%{표}")

    if 잰것:
        위수 = sum(1 for _, ㅅ in 고정 if ㅅ.수익률 > 잰것[0]["수익률"])
        print(f"\n  '매일 1위'는 이 {len(고정)}개 사이에 놓으면 {위수 + 1}위입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
