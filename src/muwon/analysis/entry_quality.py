"""진입한 그날이 어떤 날이었나. 그리고 그게 결과와 상관이 있나.

왜 보는가. 손실 매매 660건 중 **28%가 산 날이 곧 고점**이었다(보유 구간
되짚기, 설계안 §16). 백테스트는 신호가 난 **그날 종가**에 산다. 지금 활성
전략은 "거래량 2배 급증 + 그날 2% 이상 상승"에서 신호를 내므로, 산 날이
고점이라는 건 곧 **급등 꼭지를 샀다**는 뜻이다.

그렇다면 물음은 하나로 좁혀진다. **이미 많이 오른 날일수록 더 나쁜가?**

만약 그렇다면 문턱을 위아래로 두면 된다("2% 이상 오르되 8%는 넘지 않은
날"). 아니라면 진입 시점이 아니라 다른 데 원인이 있다.

**결과를 미리 나눠 놓고 보지 않는다.** 이긴 매매만 골라 특징을 찾으면
어떤 숫자든 그럴듯한 이야기가 나온다. 전체를 진입일 특성으로 나눈 다음
각 칸의 성적을 보는 순서를 지킨다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import pandas as pd

#: 진입일 상승률 구간 (하한 이상, 상한 미만)
가격구간 = [(2, 4), (4, 6), (6, 8), (8, 12), (12, 999)]
#: 진입일 거래량 배수 구간
거래량구간 = [(2, 3), (3, 5), (5, 10), (10, 9999)]


@dataclass(frozen=True)
class EntrySample:
    symbol: str
    상승률: float  # 진입일 종가가 전날 대비 몇 % 올랐나
    거래량배수: float  # 진입일 거래량 / 최근 20일 평균
    손익: float  # 그 매매의 최종 손익률


def trace_entries(trades, histories: dict[str, pd.DataFrame], 거래량창: int = 20):
    """완결된 매매마다 진입한 날의 모습을 되짚는다."""
    samples: list[EntrySample] = []
    for trade in trades:
        df = histories.get(trade.symbol)
        if df is None or len(df) == 0:
            continue
        정렬 = df.sort_values("trade_date").reset_index(drop=True)
        위치 = 정렬.index[정렬["trade_date"] == trade.entry_date]
        # 진입일 앞 봉이 없으면 상승률을 구할 수 없다. 0으로 채우면 '안 오른
        # 날에 샀다'는 없던 사실이 표에 들어간다.
        if len(위치) == 0 or 위치[0] == 0:
            continue
        i = 위치[0]
        전날종가 = float(정렬.loc[i - 1, "close"])
        if 전날종가 <= 0:
            continue
        평균거래량 = float(정렬.loc[max(0, i - 거래량창) : i - 1, "volume"].mean())
        samples.append(
            EntrySample(
                symbol=trade.symbol,
                상승률=(float(정렬.loc[i, "close"]) / 전날종가 - 1) * 100,
                거래량배수=(
                    float(정렬.loc[i, "volume"]) / 평균거래량 if 평균거래량 > 0 else 0.0
                ),
                손익=trade.pnl_pct,
            )
        )
    return samples


def _칸(samples: list[EntrySample], 값뽑기, 구간들):
    """구간별 (이름, 건수, 승률, 평균손익, 중앙손익)."""
    결과 = []
    for 하한, 상한 in 구간들:
        묶음 = [s for s in samples if 하한 <= 값뽑기(s) < 상한]
        if not 묶음:
            continue
        손익들 = [s.손익 for s in 묶음]
        이름 = f"{하한:g}~{상한:g}" if 상한 < 900 else f"{하한:g} 이상"
        결과.append(
            (
                이름,
                len(묶음),
                sum(1 for x in 손익들 if x > 0) / len(묶음) * 100,
                statistics.fmean(손익들),
                statistics.median(손익들),
            )
        )
    return 결과


def format_entries(samples: list[EntrySample], label: str = "") -> str:
    머리 = f"■ 산 날은 어떤 날이었나{f' ({label})' if label else ''}"
    if not samples:
        return f"{머리}\n\n완결된 매매가 없습니다."

    lines = [
        머리,
        "  백테스트는 신호가 난 **그날 종가**에 삽니다. 즉 급등한 당일에 들어갑니다.",
        "  묻는 것: 이미 많이 오른 날일수록 결과가 나쁜가?",
        "",
        (f"완결 매매 {len(samples)}건 · 전체 승률 "
        f"{sum(1 for s in samples if s.손익 > 0) / len(samples) * 100:.1f}% · "
        f"평균 손익 {statistics.fmean([s.손익 for s in samples]):+.2f}%"),
    ]

    for 제목, 뽑기, 구간들, 단위 in (
        ("그날 얼마나 올랐나 (전날 종가 대비)", lambda s: s.상승률, 가격구간, "%"),
        ("그날 거래량이 평소의 몇 배였나", lambda s: s.거래량배수, 거래량구간, "배"),
    ):
        lines += ["", f"▸ {제목}", "", f"{'구간':<12}{'건수':>7}{'승률':>9}{'평균 손익':>12}{'중앙 손익':>12}"]
        for 이름, 건수, 승률, 평균, 중앙 in _칸(samples, 뽑기, 구간들):
            lines.append(
                f"{이름 + 단위:<12}{건수:>7}{승률:>8.1f}%{평균:>+11.2f}%{중앙:>+11.2f}%"
            )

    lines += [
        "",
        "읽는 법: 위 구간에서 아래 구간으로 갈수록 성적이 꾸준히 나빠지면,",
        "'너무 오른 날은 사지 않는다'는 상한이 먹힙니다. 들쭉날쭉하면 진입",
        "시점이 아니라 다른 데 원인이 있는 것입니다.",
    ]
    return "\n".join(lines)
