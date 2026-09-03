"""시장 지표로 매일 전략을 고르는 규칙을 지나간 구간에 대고 계산한다.

주인이 물은 것은 이것이다. "변동성과 공포지수와 거래량을 종합해서 점수를
매기고, 그 점수로 매일 최적 전략을 골라 바꾸면 어떤가."

## 무엇을 재나

날마다 세 가지 시장 지표를 보고 그날 쓸 전략을 고른다. 고른 답을 다음
거래일부터 쓴다. 계좌 하나를 처음부터 끝까지 굴리고, 전략 하나를 그냥
두었을 때와 견준다.

## 미래를 보지 않게 하려고 지킨 것

D일 저녁에 아는 것만 쓴다.

- 코스피 변동성과 거래대금은 D일 종가까지만 본다.
- 공포지수(VIX)는 하루 미룬다. 한국에서 17:50에 검토할 때 그날 미국 장은
  아직 열리지 않았다.
- 표준화(z점수)는 그 시점까지의 과거로만 한다. 전체 구간의 평균과 표준편차를
  쓰면 미래를 보는 것이 된다.
- 어느 전략이 좋았는지를 배울 때도 D일까지 결과가 다 나온 날만 쓴다.

## 채점 기준은 계산하기 전에 정한다

2026-08-19에 유사 구간 전망을 기각할 수 있었던 것이 채점 틀을 먼저 만들어
둔 덕이다. 숫자를 보고 기준을 움직이지 않으려고 여기에 적어 둔다.

1. **1순위는 가장 나빴던 해다.** 평균이 아니다.
2. **무작위 대조군을 못 이기면 기각한다.** 같은 날짜에 같은 횟수만큼
   무작위로 전략을 바꾼 것이 대조군이다. 이것을 못 이기면 지표가 준 정보가
   없다는 뜻이다. 갈아타는 행위 자체의 효과와 지표의 효과를 가르는 자리다.
3. **전략 하나를 그냥 둔 것도 못 이기면 기각한다.** 지금 설정된 갭 상승
   따라가기와, 연 단위 평가 1위인 거래량 급증 3일 둘 다와 견준다.
4. **한 해에 거래가 20건에 못 미치면 그 해는 판단에 쓰지 않는다.**
5. **설정값 여러 벌에서 답이 갈리면 기각한다.** 한 벌에서만 좋은 것은
   그 벌에 맞춘 것이다.

## 이 계산의 한계

**절대 수익률을 전략 평가 결과와 나란히 놓을 수 없다.** 매매 대상이 다르고
(여기는 섹터 목록 전체, 평가는 시가총액 상위 30종목), 계산 방식도 다르다
(여기는 한 구간을 이어서, 평가는 해마다 따로). 같은 계산 안의 비교로만 쓴다.

**살아남은 회사만 본다.** 지금 목록을 과거로 가져가는 것이라 성적이 부풀려
나온다. 같은 편향이 모든 줄에 똑같이 걸리므로 줄끼리의 비교에는 쓸 수 있다.

**공포지수는 미국 VIX다.** 한국 VKOSPI는 받을 수 있는 곳을 찾지 못했다.

사용 예:
    python scripts/run_regime_switch_sim.py
    python scripts/run_regime_switch_sim.py --학습창 750 --앞선날 10 --이웃 50
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_switch_check import 대상종목, 섹터표만들기

from muwon.analysis.market_data import load_histories
from muwon.analysis.switching import 갈아타기전략, 굴리기
from muwon.backtest.costs import TransactionCosts
from muwon.data.price_cache import PriceCache
from muwon.data.yahoo_client import YahooFinanceDataSource
from muwon.settings.schema import RiskPolicy
from muwon.strategy.portfolio import as_portfolio_strategy
from muwon.strategy.registry import build_strategy, get_definition, list_definitions

#: 견줄 상대. 지금 설정된 것과 연 단위 평가 1위다.
기준전략 = ("gap_up_go", "volume_surge_3d")

#: 한 해에 이만큼 거래가 없으면 그 해 숫자는 판단에 쓰지 않는다.
표본최소기준 = 20


def 전략이름(키: str) -> str:
    try:
        return get_definition(키).짧은이름
    except Exception:  # noqa: BLE001
        return 키


# ──────────────────────────────────────────────────────────────────────
# 시장 지표
# ──────────────────────────────────────────────────────────────────────


def _rolling_z(s: pd.Series, 창: int, 최소: int) -> pd.Series:
    """그 시점까지의 과거로만 표준화한다.

    전체 구간의 평균과 표준편차를 쓰면 미래를 보는 것이 된다. shift(1)로
    오늘 값을 평균 계산에서 빼는 것까지 해야 한다."""
    앞 = s.shift(1)
    평균 = 앞.rolling(창, min_periods=최소).mean()
    표준 = 앞.rolling(창, min_periods=최소).std()
    return (s - 평균) / 표준.replace(0.0, np.nan)


def 시장지표(
    histories: dict[str, pd.DataFrame],
    지수: pd.DataFrame,
    공포: pd.DataFrame,
    z창: int = 250,
    z최소: int = 120,
) -> pd.DataFrame:
    """변동성, 공포지수, 거래량을 날짜별 z점수로 만든다.

    셋 다 D일 저녁에 알 수 있는 값이다. 공포지수만 하루 미룬다."""
    ㅈ = 지수.set_index("trade_date")["close"].astype(float)
    변동성 = ㅈ.pct_change().rolling(20).std() * np.sqrt(252) * 100

    # 거래대금 = 종가 × 거래량. 매매 대상 전체를 더한다.
    거래대금 = None
    for df in histories.values():
        s = df.set_index("trade_date")["close"] * df.set_index("trade_date")["volume"]
        거래대금 = s if 거래대금 is None else 거래대금.add(s, fill_value=0.0)
    거래량비 = 거래대금 / 거래대금.rolling(20).mean()

    # **공포지수는 하루 미룬다.** 한국에서 저녁에 검토할 때 그날 미국 장은
    # 아직 열리지 않았다. 미루지 않으면 그날 밤의 결과를 미리 보는 것이 된다.
    ㅍ = 공포.set_index("trade_date")["close"].astype(float).shift(1)

    표 = pd.DataFrame({"변동성": 변동성, "거래량": 거래량비})
    표["공포"] = ㅍ.reindex(표.index).ffill()
    표.index = pd.to_datetime(표.index)

    나온것 = pd.DataFrame(index=표.index)
    for 칸 in ("변동성", "공포", "거래량"):
        나온것[칸] = _rolling_z(표[칸], z창, z최소)
    return 나온것.dropna()


# ──────────────────────────────────────────────────────────────────────
# 전략별 일별 성적
# ──────────────────────────────────────────────────────────────────────


def 전략별곡선(
    histories: dict[str, pd.DataFrame],
    전략키들: list[str],
    시작: date,
    끝: date,
    정책: RiskPolicy,
    costs: TransactionCosts,
    제약: dict,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """전략마다 한 번씩 굴려서 일별 자산 곡선을 모은다.

    이 곡선으로 "그때 어느 전략이 좋았나"를 배운다. 갈아타면서 굴린 계좌와
    똑같지는 않다(보유 종목 상태가 이어지지 않는다). 배우는 신호로만 쓰고,
    최종 성적은 실제로 갈아타는 계좌 하나를 굴려서 낸다."""
    곡선들: dict[str, pd.Series] = {}
    성적들: dict[str, object] = {}
    for i, 키 in enumerate(전략키들, 1):
        t = time.time()
        try:
            나온것 = 굴리기(histories, build_strategy(키), 시작, 끝, 정책, costs=costs, **제약)
        except Exception as 탈:  # noqa: BLE001 (하나가 터져도 나머지는 봐야 한다)
            print(f"  [{i}/{len(전략키들)}] {키} 못 돌림 ({type(탈).__name__}: {탈})",
                  file=sys.stderr)
            continue
        if 나온것 is None:
            continue
        결과, 지표 = 나온것
        곡선 = 결과.equity_curve.set_index("trade_date")["equity"].astype(float)
        곡선.index = pd.to_datetime(곡선.index)
        곡선들[키] = 곡선
        성적들[키] = (결과, 지표)
        print(f"  [{i}/{len(전략키들)}] {전략이름(키):18} "
              f"{지표.total_return_pct:+8.1f}% 낙폭 {지표.max_drawdown_pct:7.1f}% "
              f"거래 {len(결과.closed_trades):5}건 ({time.time()-t:.0f}초)",
              file=sys.stderr)
    return pd.DataFrame(곡선들).sort_index(), 성적들


class 준비캐시:
    """전략 객체를 한 번만 준비해 두고 여러 계좌가 나눠 쓴다.

    갈아타기 계좌는 굴릴 때마다 쓰이는 전략 전부를 다시 준비한다. 27개를
    거치는 계좌 하나에 4분이 걸렸고, 무작위 대조군까지 더하면 몇 시간이 된다.
    2026-09-02에 처음 계산할 때 그 시간에 걸려 중간에 끊겼다.

    **나눠 써도 되는 까닭은 준비가 읽기 전용 표를 만들 뿐이기 때문이다.**
    `prepare()`는 날짜별 신호 표를 만들고 `evaluate()`는 그 표를 조회만 한다.
    계좌 쪽 상태(현금, 보유 종목)는 엔진이 들고 있지 전략이 들고 있지 않다.

    **같은 시세로 만든 것만 나눠 쓸 수 있다.** 굴리는 구간이 달라지면 잘린
    시세가 달라지므로 캐시를 새로 만들어야 한다."""

    def __init__(self, 만들기):
        self._만들기 = 만들기
        self._준비됨: dict[str, object] = {}

    def __call__(self, 키: str):
        """`갈아타기전략`이 만들기 함수 자리에 그대로 넣는다."""
        return self._만들기(키)

    def 준비(self, 키: str, histories: dict[str, pd.DataFrame]):
        if 키 not in self._준비됨:
            속 = as_portfolio_strategy(self._만들기(키))
            속.prepare(histories)
            self._준비됨[키] = 속
        return self._준비됨[키]


class 캐시쓰는갈아타기(갈아타기전략):
    """준비 결과를 캐시에서 꺼내 쓰는 것 말고는 `갈아타기전략`과 같다."""

    def __init__(self, 날짜별키: dict[date, str], 캐시: 준비캐시, 처음키: str):
        super().__init__(날짜별키, 캐시, 처음키)
        self._캐시 = 캐시

    def prepare(self, histories: dict[str, pd.DataFrame]) -> None:
        쓸것 = {self._처음키} | set(self._날짜별키.values())
        for 키 in 쓸것:
            self._전략들[키] = self._캐시.준비(키, histories)


# ──────────────────────────────────────────────────────────────────────
# 고르기
# ──────────────────────────────────────────────────────────────────────


def 앞선성적(수익률: pd.DataFrame, 앞선날: int) -> pd.DataFrame:
    """각 날 t에서 t+1 ~ t+H일 동안의 누적 수익률.

    t일 저녁에는 이 값을 모른다. t+H일이 지나야 안다. 학습에 쓸 때 그 점을
    지켜야 한다."""
    로그 = np.log1p(수익률)
    앞 = 로그.rolling(앞선날).sum().shift(-앞선날)
    return np.expm1(앞)


def 날마다고르기_지표(
    지표: pd.DataFrame,
    앞: pd.DataFrame,
    거래일: list[pd.Timestamp],
    처음키: str,
    학습창: int,
    앞선날: int,
    이웃: int,
    최소학습: int,
) -> tuple[dict[date, str], int]:
    """오늘 시장 상태와 비슷했던 과거를 찾아, 그때 좋았던 전략을 고른다.

    D일에 쓸 수 있는 학습 표본은 t + 앞선날 <= D 인 t뿐이다. 그보다 최근의
    t는 결과가 아직 안 나왔다. 이 줄 하나가 미래를 보느냐 마느냐를 가른다."""
    칸 = ["변동성", "공포", "거래량"]
    표: dict[date, str] = {}
    키 = 처음키
    바뀐횟수 = 0

    for i, 오늘 in enumerate(거래일):
        적용날 = 거래일[i + 1] if i + 1 < len(거래일) else None
        if 적용날 is None:
            break

        if 오늘 not in 지표.index:
            표[적용날.date()] = 키
            continue

        # 결과가 다 나온 날만 학습에 쓴다.
        끝점 = 오늘 - pd.Timedelta(days=1)
        후보 = 지표.loc[:끝점]
        후보 = 후보[후보.index.isin(앞.index)]
        쓸수있는 = 앞.loc[후보.index].dropna(how="all")
        # 앞선날만큼 뒤의 결과가 있어야 한다. rolling+shift가 만든 NaN이
        # 그것을 걸러 주지만, 날짜로도 한 번 더 막는다.
        마감 = 오늘 - pd.Timedelta(days=int(앞선날 * 1.6) + 3)
        쓸수있는 = 쓸수있는.loc[:마감]
        if len(쓸수있는) > 학습창:
            쓸수있는 = 쓸수있는.iloc[-학습창:]

        if len(쓸수있는) < 최소학습:
            표[적용날.date()] = 키
            continue

        v = 지표.loc[오늘, 칸].to_numpy(dtype=float)
        과거 = 지표.loc[쓸수있는.index, 칸].to_numpy(dtype=float)
        거리 = np.sqrt(((과거 - v) ** 2).sum(axis=1))
        고른 = np.argsort(거리)[: min(이웃, len(거리))]
        점수 = 쓸수있는.iloc[고른].mean(axis=0).dropna()
        if 점수.empty:
            표[적용날.date()] = 키
            continue

        새키 = str(점수.idxmax())
        if 새키 != 키:
            바뀐횟수 += 1
        키 = 새키
        표[적용날.date()] = 키

    return 표, 바뀐횟수


def 무작위고르기(
    후보키들: list[str], 표: dict[date, str], 처음키: str, 씨: int
) -> dict[date, str]:
    """같은 날짜에 같은 횟수만큼 무작위로 바꾼다.

    갈아타는 행위 자체의 효과와 지표가 준 정보를 가르는 대조군이다. 바꾸는
    날짜와 횟수를 똑같이 맞춰야 비교가 된다."""
    rng = np.random.default_rng(씨)
    날들 = sorted(표)
    나온것: dict[date, str] = {}
    키 = 처음키
    앞선 = 처음키
    for 날 in 날들:
        if 표[날] != 앞선:
            키 = str(rng.choice(후보키들))
        앞선 = 표[날]
        나온것[날] = 키
    return 나온것


# ──────────────────────────────────────────────────────────────────────
# 성적 내기
# ──────────────────────────────────────────────────────────────────────


def 해마다(결과) -> dict[int, dict]:
    """계좌 곡선을 해마다 나눈다. 그 해 시작 자산 대비 끝 자산이다."""
    곡선 = 결과.equity_curve.set_index("trade_date")["equity"].astype(float)
    곡선.index = pd.to_datetime(곡선.index)
    거래: dict[int, int] = {}
    for t in 결과.closed_trades:
        해 = pd.Timestamp(t.exit_date).year
        거래[해] = 거래.get(해, 0) + 1

    나온것: dict[int, dict] = {}
    for 해, 조각 in 곡선.groupby(곡선.index.year):
        if len(조각) < 2:
            continue
        수익률 = (조각.iloc[-1] / 조각.iloc[0] - 1) * 100
        낙폭 = ((조각 / 조각.cummax()) - 1).min() * 100
        나온것[int(해)] = {
            "수익률": float(수익률),
            "낙폭": float(낙폭),
            "거래": int(거래.get(int(해), 0)),
        }
    return 나온것


def 요약(해별: dict[int, dict], 평가시작해: int = 0) -> dict:
    """표본이 되는 해만 모아 평균과 최악을 낸다.

    `평가시작해` 앞은 학습 예열 구간이라 뺀다. 그때는 배울 과거가 아직
    없어서 지표가 전략을 못 고르고 처음 것에 머문다. 그 구간을 성적에 넣으면
    "안 바꾼 것"의 성적이 "지표로 고른 것"의 성적으로 섞인다."""
    쓸것 = {
        h: v for h, v in 해별.items()
        if v["거래"] >= 표본최소기준 and h >= 평가시작해
    }
    if not 쓸것:
        return {"평균": None, "최악": None, "표본해": 0, "전체해": len(해별)}
    값 = [v["수익률"] for v in 쓸것.values()]
    return {
        "평균": round(float(np.mean(값)), 2),
        "최악": round(float(min(값)), 2),
        "최악해": int(min(쓸것, key=lambda h: 쓸것[h]["수익률"])),
        "표본해": len(쓸것),
        "전체해": len(해별),
        "낙폭": round(float(min(v["낙폭"] for v in 쓸것.values())), 2),
    }


def 설정벌읽기(인자) -> list[tuple[int, int, int]]:
    """계산할 설정값 조합. `--설정벌`이 비어 있으면 인자 하나로 한 벌만 한다.

    `학습창:앞선날:이웃`을 쉼표로 잇는다."""
    if not 인자.설정벌:
        return [(인자.학습창, 인자.앞선날, 인자.이웃)]
    나온것 = []
    for 조각 in 인자.설정벌.split(","):
        ㄱ = 조각.strip().split(":")
        if len(ㄱ) != 3:
            raise ValueError(f"설정 벌은 `학습창:앞선날:이웃` 모양이어야 합니다: {조각}")
        나온것.append((int(ㄱ[0]), int(ㄱ[1]), int(ㄱ[2])))
    return 나온것


def main() -> int:
    ㅍ = argparse.ArgumentParser(description="시장 지표로 매일 전략을 고르면 어떤가")
    ㅍ.add_argument("--시작", default="2020-01-02",
                   help="계좌를 굴리기 시작하는 날. 앞부분은 배울 과거를 쌓는 "
                        "예열 구간이라 성적에 넣지 않는다")
    ㅍ.add_argument("--평가시작해", type=int, default=2021,
                   help="이 해부터의 성적만 판단에 쓴다")
    ㅍ.add_argument("--끝", default="2026-09-02")
    ㅍ.add_argument("--학습창", type=int, default=500,
                   help="며칠치 과거에서 비슷한 날을 찾을 것인가")
    ㅍ.add_argument("--앞선날", type=int, default=20,
                   help="비슷했던 날 뒤 며칠의 성적을 볼 것인가")
    ㅍ.add_argument("--이웃", type=int, default=30,
                   help="가장 비슷한 날 몇 개를 볼 것인가")
    ㅍ.add_argument("--최소학습", type=int, default=200,
                   help="학습 표본이 이보다 적으면 바꾸지 않는다")
    ㅍ.add_argument("--설정벌", default="",
                   help="여러 벌을 한 번에 계산한다. `학습창:앞선날:이웃`을 "
                        "쉼표로 잇는다. 예: 500:20:30,750:10:50,250:40:20")
    ㅍ.add_argument("--무작위횟수", type=int, default=20)
    ㅍ.add_argument("--슬리피지", type=float, default=0.0)
    ㅍ.add_argument("--비중", type=float, default=0.15)
    ㅍ.add_argument("--동시보유", type=int, default=6)
    ㅍ.add_argument("--섹터당", type=int, default=3)
    ㅍ.add_argument("--손절", type=float, default=-0.05)
    ㅍ.add_argument("--예수금", type=float, default=10_000_000.0)
    ㅍ.add_argument("--저장", default="")
    인자 = ㅍ.parse_args()

    시작 = date.fromisoformat(인자.시작)
    끝 = date.fromisoformat(인자.끝)
    정책 = RiskPolicy(
        max_position_weight=인자.비중,
        max_concurrent_positions=인자.동시보유,
        stop_loss_pct=인자.손절,
        take_profit_pct=0.0,
        daily_loss_limit_pct=-0.03,
    )
    costs = TransactionCosts(slippage_pct=인자.슬리피지)
    제약 = {
        "섹터표": 섹터표만들기(), "섹터상한": 인자.섹터당,
        "섹터상한셈": "하루후보", "점수순": True, "결제일수": 0,
        "예수금": 인자.예수금,
    }
    전략키들 = [ㅈ.key for ㅈ in list_definitions()]

    source = YahooFinanceDataSource()
    cache = PriceCache(".cache/prices.sqlite")
    histories = load_histories(source, 대상종목(), 시작 - timedelta(days=800), 끝, cache=cache)
    if not histories:
        print("시세를 하나도 못 받았습니다.", file=sys.stderr)
        return 1
    지수 = source.get_daily_ohlcv("^KS11", 시작 - timedelta(days=800), 끝)
    공포 = source.get_daily_ohlcv("^VIX", 시작 - timedelta(days=800), 끝)
    print(f"코스피 {len(지수)}일 · 공포지수 {len(공포)}일", file=sys.stderr)

    print("■ 전략마다 한 번씩 굴린다", file=sys.stderr)
    곡선표, 성적들 = 전략별곡선(histories, 전략키들, 시작, 끝, 정책, costs, 제약)
    if 곡선표.empty:
        print("곡선을 하나도 못 만들었습니다.", file=sys.stderr)
        return 1

    수익률 = 곡선표.pct_change()
    지표 = 시장지표(histories, 지수, 공포)
    거래일 = [t for t in 곡선표.index]
    print(f"■ 시장 지표 {len(지표)}일 · 거래일 {len(거래일)}일", file=sys.stderr)

    처음키 = 기준전략[0]
    낸것: dict[str, dict] = {}
    벌들: dict[str, dict] = {}
    캐시 = 준비캐시(build_strategy)

    # **설정값 한 벌에서만 좋은 것은 그 벌에 맞춘 것이다.** 여러 벌을 같은
    # 자료 위에서 계산해서, 답이 벌마다 뒤집히는지 본다. 전략별 곡선은 벌과
    # 무관하므로 위에서 한 번만 계산하고 여기서 돌려 쓴다.
    for 학습창, 앞선날, 이웃 in 설정벌읽기(인자):
        이름 = f"학습창 {학습창} · 앞선날 {앞선날} · 이웃 {이웃}"
        print(f"■ [{이름}]", file=sys.stderr)
        표, 바뀐횟수 = 날마다고르기_지표(
            지표, 앞선성적(수익률, 앞선날), 거래일, 처음키,
            학습창=학습창, 앞선날=앞선날, 이웃=이웃, 최소학습=인자.최소학습,
        )
        쓴전략 = sorted(set(표.values()))
        print(f"  {바뀐횟수}번 바꿨고 전략 {len(쓴전략)}개를 거쳤다", file=sys.stderr)

        나온것 = 굴리기(histories, 캐시쓰는갈아타기(표, 캐시, 처음키), 시작, 끝,
                    정책, costs=costs, **제약)
        if 나온것 is None:
            print("  갈아타기 계좌를 못 굴렸습니다.", file=sys.stderr)
            continue
        결과, 지표성적 = 나온것
        해별 = 해마다(결과)
        ㅈ요약 = 요약(해별, 인자.평가시작해)
        print(f"  지표로 고르기: 평균 {ㅈ요약['평균']}% 최악 {ㅈ요약['최악']}%",
              file=sys.stderr)

        무작위들 = []
        for 씨 in range(인자.무작위횟수):
            ㅁ표 = 무작위고르기(전략키들, 표, 처음키, 씨)
            ㅁ = 굴리기(histories, 캐시쓰는갈아타기(ㅁ표, 캐시, 처음키), 시작, 끝,
                     정책, costs=costs, **제약)
            if ㅁ is None:
                continue
            무작위들.append(요약(해마다(ㅁ[0]), 인자.평가시작해))
        최악들 = [ㅁ["최악"] for ㅁ in 무작위들 if ㅁ["최악"] is not None]
        평균들 = [ㅁ["평균"] for ㅁ in 무작위들 if ㅁ["평균"] is not None]

        벌들[이름] = {
            "학습창": 학습창, "앞선날": 앞선날, "이웃": 이웃,
            "지표로 고르기": {
                "해별": 해별, "요약": ㅈ요약,
                "전체수익률": round(지표성적.total_return_pct, 2),
                "전체낙폭": round(지표성적.max_drawdown_pct, 2),
                "거래": len(결과.closed_trades),
                "바꾼횟수": 바뀐횟수,
                "거친전략수": len(쓴전략),
            },
            "무작위 대조군": {
                "횟수": len(무작위들),
                "평균의 중앙값": round(float(np.median(평균들)), 2) if 평균들 else None,
                "최악의 중앙값": round(float(np.median(최악들)), 2) if 최악들 else None,
                "최악의 최고": round(float(max(최악들)), 2) if 최악들 else None,
                "지표가 이긴 비율": round(
                    float(np.mean([ㅈ요약["최악"] > m for m in 최악들])), 3
                ) if 최악들 and ㅈ요약["최악"] is not None else None,
            },
        }
        ㅁ정보 = 벌들[이름]["무작위 대조군"]
        print(f"  무작위 대조군: 최악의 중앙값 {ㅁ정보['최악의 중앙값']}% · "
              f"지표가 이긴 비율 {ㅁ정보['지표가 이긴 비율']}", file=sys.stderr)
        # **벌 하나가 끝날 때마다 남긴다.** 마지막에 한 번만 저장하면 도중에
        # 끊겼을 때 앞의 계산이 통째로 날아간다. 2026-09-02에 그렇게 40분을
        # 버렸다.
        if 인자.저장:
            Path(인자.저장).write_text(
                json.dumps({"벌": 벌들, "아직끝나지않음": True},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )

    낸것["벌"] = 벌들

    print("■ 전략 하나를 그냥 둔 것", file=sys.stderr)
    고정: dict[str, dict] = {}
    for 키, (ㄱ결과, ㄱ지표) in 성적들.items():
        ㄱ해별 = 해마다(ㄱ결과)
        고정[키] = {
            "이름": 전략이름(키), "해별": ㄱ해별, "요약": 요약(ㄱ해별, 인자.평가시작해),
            "전체수익률": round(ㄱ지표.total_return_pct, 2),
            "전체낙폭": round(ㄱ지표.max_drawdown_pct, 2),
            "거래": len(ㄱ결과.closed_trades),
        }
    낸것["고정"] = 고정

    낸것["조건"] = {
        "시작": str(시작), "끝": str(끝), "평가시작해": 인자.평가시작해,
        "예열": f"{시작}부터 {인자.평가시작해}년 전까지는 배울 과거를 쌓는 구간이라 성적에 넣지 않았습니다",
        "매매대상": f"{len(histories)}종목 (섹터 목록의 활성 종목)",
        "지표": "코스피 20일 실현변동성 · 미국 VIX(하루 미룸) · 매매 대상 거래대금 20일 평균 대비",
        "설정벌": [f"{a}:{b}:{c}" for a, b, c in 설정벌읽기(인자)],
        "설정": (f"비중 {인자.비중:.0%} · 동시보유 {인자.동시보유}종목 · "
               f"섹터당 {인자.섹터당}종목 · 손절 {인자.손절:.0%} · "
               f"슬리피지 {인자.슬리피지:.1%} · 예수금 {인자.예수금:,.0f}원"),
        "표본최소기준": 표본최소기준,
    }

    if 인자.저장:
        Path(인자.저장).write_text(
            json.dumps(낸것, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"저장했습니다: {인자.저장}", file=sys.stderr)

    print()
    print("=" * 78)
    print(f"{'설정 벌':28} {'평균':>9} {'최악':>9} {'낙폭':>9}  무작위를 이긴 비율")
    for 이름, ㅂ in 벌들.items():
        ㅈ, ㅁ = ㅂ["지표로 고르기"], ㅂ["무작위 대조군"]
        print(f"{이름:28} {ㅈ['요약']['평균']:>8}% {ㅈ['요약']['최악']:>8}% "
              f"{ㅈ['전체낙폭']:>8}%  {ㅁ['지표가 이긴 비율']} "
              f"(무작위 최악 중앙값 {ㅁ['최악의 중앙값']}%)")
    print("-" * 78)
    쓸만한 = sorted(
        (v for v in 고정.values() if v["요약"]["최악"] is not None),
        key=lambda v: -v["요약"]["최악"],
    )
    print("전략 하나를 그냥 둔 것 (가장 나빴던 해 차례, 위 다섯)")
    for ㄱ in 쓸만한[:5]:
        print(f"  {ㄱ['이름']:20} 평균 {ㄱ['요약']['평균']:>8}%  최악 {ㄱ['요약']['최악']:>8}% "
              f"({ㄱ['요약'].get('최악해')}년)  낙폭 {ㄱ['전체낙폭']:>8}%  거래 {ㄱ['거래']}건")
    print("  견줄 상대")
    for 키 in 기준전략:
        if 키 in 고정:
            ㄱ = 고정[키]
            등수 = next((i + 1 for i, v in enumerate(쓸만한) if v["이름"] == ㄱ["이름"]), None)
            print(f"  {ㄱ['이름']:20} 평균 {ㄱ['요약']['평균']:>8}%  최악 {ㄱ['요약']['최악']:>8}% "
                  f"({ㄱ['요약'].get('최악해')}년)  낙폭 {ㄱ['전체낙폭']:>8}%  "
                  f"거래 {ㄱ['거래']}건  {등수}위/{len(쓸만한)}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
