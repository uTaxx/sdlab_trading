"""승인한 종목과 거절한 종목이 그 뒤에 어떻게 됐나.

## 이 파일이 있는 이유

매수 후보 승인은 주인이 매일 하는 결정인데, 그 결정이 맞았는지를 볼 자리가
없었다. 승인한 것은 매매 기록에 남지만 **거절한 것은 어디에도 안 남는다.**
그래서 "내가 거른 것이 실제로 나빴나"를 물을 방법이 없었다.

승인대기 시트에 날짜, 종목, 승인 여부가 전부 남아 있다. 시세를 붙이면 그
뒤 며칠을 계산할 수 있다.

## 무엇을 재나

결정한 날의 **다음 거래일 시가**를 기준으로 5거래일과 20거래일 뒤의 등락률을
낸다. 실거래 엔진이 다음 날 아침에 시장가로 주문하기 때문이다. 결정한 날
종가로 재면 실제로는 낼 수 없는 성적이 된다.

## 이 숫자를 읽을 때 조심할 것

**거절한 종목에는 비용이 안 들어 있다.** 실제로 사지 않았으므로 수수료도
슬리피지도 없다. 승인한 쪽은 실제로 사고팔면서 비용을 냈다. 그래서 두 쪽의
등락률을 그대로 견주면 거절한 쪽이 유리하게 나온다.

**승인한 종목의 등락률은 실제 손익이 아니다.** 여기서는 사서 계속 들고 있던
것으로 계산하는데, 실거래는 손절과 보유 기간 상한으로 중간에 판다. 실제
손익은 매매 기록에 있다. 이 표는 "그 종목이 그 뒤 어떻게 됐나"만 본다.

**표본이 적으면 판단하지 않는다.** 며칠치로 "내 승인이 낫다"를 말하면
그다음부터 그 문장을 아무도 안 읽는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

#: 결정한 뒤 며칠을 볼 것인가. (열쇠, 화면에 적을 이름, 거래일 수)
지평들: tuple[tuple[str, str, int], ...] = (
    ("d5", "5거래일", 5),
    ("d20", "20거래일", 20),
)

지평표: dict[str, tuple[str, int]] = {ㅋ: (이름, 날) for ㅋ, 이름, 날 in 지평들}

#: 이만큼은 모여야 승인과 거절을 견준다. 기간 검증의 거래 수 최소 기준과
#: 같은 값이다. 두 곳이 어긋나면 같은 줄이 화면마다 다르게 보인다.
최소표본 = 20


@dataclass(frozen=True)
class 결정:
    """후보 하나에 대한 그날의 판단."""

    날짜: date
    종목코드: str
    종목명: str
    승인: bool
    #: 후보를 낼 때 화면에 적었던 값. 전날 종가다.
    예상가: float = 0.0


@dataclass(frozen=True)
class 되짚기:
    """결정 하나를 나중에 다시 잰 것."""

    결정: 결정
    #: 실제로 샀다면 들어갔을 가격. 다음 거래일 시가다.
    기준가: float | None = None
    등락: dict[str, float | None] = field(default_factory=dict)
    #: 못 잰 경우 왜 못 쟀는지. 빈 글자면 잰 것이다.
    못한까닭: str = ""

    @property
    def 잰것인가(self) -> bool:
        return self.기준가 is not None and not self.못한까닭

    def 값(self, 지평열쇠: str) -> float | None:
        return self.등락.get(지평열쇠)


def _봉들(df: pd.DataFrame, 시작: date) -> pd.DataFrame | None:
    """결정한 날 **다음** 거래일부터의 봉. 그날 자체는 뺀다."""
    if df is None or df.empty:
        return None
    안 = df[df["trade_date"] > 시작].sort_values("trade_date")
    return 안 if len(안) else None


def 한건(ㄱ: 결정, histories: dict[str, pd.DataFrame]) -> 되짚기:
    """결정 하나를 시세에 붙여 다시 잰다.

    아직 지평이 안 지난 것은 그 지평만 None이다. **0으로 채우지 않는다.**
    안 움직인 것과 아직 모르는 것은 다른 말이다."""
    df = histories.get(ㄱ.종목코드)
    뒤 = _봉들(df, ㄱ.날짜)
    if 뒤 is None:
        return 되짚기(결정=ㄱ, 못한까닭="결정한 날 뒤의 시세가 없습니다")

    첫봉 = 뒤.iloc[0]
    기준가 = float(첫봉.get("open") or 첫봉.get("close") or 0)
    if 기준가 <= 0:
        return 되짚기(결정=ㄱ, 못한까닭="다음 거래일 시가를 못 읽었습니다")

    종가들 = 뒤["close"].astype(float).tolist()
    등락: dict[str, float | None] = {}
    for 열쇠, _, 날 in 지평들:
        # 기준가는 첫 봉의 시가이므로, N거래일 뒤는 색인 N-1의 종가다.
        등락[열쇠] = ((종가들[날 - 1] / 기준가 - 1) * 100
                    if len(종가들) >= 날 else None)
    return 되짚기(결정=ㄱ, 기준가=기준가, 등락=등락)


def 재기(결정들: list[결정], histories: dict[str, pd.DataFrame]) -> list[되짚기]:
    return [한건(ㄱ, histories) for ㄱ in 결정들]


@dataclass(frozen=True)
class 견줌:
    """한 지평에서 승인한 쪽과 거절한 쪽."""

    지평: str
    승인수: int
    거절수: int
    승인중앙값: float | None
    거절중앙값: float | None
    승인이긴수: int = 0

    @property
    def 차이(self) -> float | None:
        if self.승인중앙값 is None or self.거절중앙값 is None:
            return None
        return self.승인중앙값 - self.거절중앙값

    @property
    def 믿을만한가(self) -> bool:
        return self.승인수 + self.거절수 >= 최소표본


def _중앙값(값들: list[float]) -> float | None:
    if not 값들:
        return None
    ㄱ = sorted(값들)
    가운데 = len(ㄱ) // 2
    return ㄱ[가운데] if len(ㄱ) % 2 else (ㄱ[가운데 - 1] + ㄱ[가운데]) / 2


def 견주기(것들: list[되짚기], 지평열쇠: str) -> 견줌:
    """**평균이 아니라 중앙값으로 견준다.** 한 종목이 크게 오르면 평균은 그
    종목 이야기가 되고, 승인 기준이 나았는지는 안 보인다."""
    승인값 = [ㄱ.값(지평열쇠) for ㄱ in 것들
            if ㄱ.결정.승인 and ㄱ.값(지평열쇠) is not None]
    거절값 = [ㄱ.값(지평열쇠) for ㄱ in 것들
            if not ㄱ.결정.승인 and ㄱ.값(지평열쇠) is not None]
    return 견줌(
        지평=지평열쇠,
        승인수=len(승인값), 거절수=len(거절값),
        승인중앙값=_중앙값(승인값), 거절중앙값=_중앙값(거절값),
        승인이긴수=sum(1 for v in 승인값 if v > 0),
    )


#: 시트 `승인되짚기` 탭의 머리. 화면과 n8n 연결이 이 순서를 읽는다.
머리 = [
    "열쇠", "잰때", "결정일", "종목코드", "종목명", "승인", "예상가", "기준가",
    *[이름 + "%" for _, 이름, _ in 지평들],
    "상태",
]


def _수(값: float | None, 자리: int = 2) -> str:
    return "" if 값 is None else f"{값:.{자리}f}"


def 줄들만들기(것들: list[되짚기], 잰때: date | str) -> list[list[str]]:
    날 = 잰때.isoformat() if isinstance(잰때, date) else str(잰때)
    나온것 = []
    for ㄱ in 것들:
        ㄷ = ㄱ.결정
        나온것.append([
            f"A{ㄷ.날짜.isoformat()}|{ㄷ.종목코드}",
            날,
            ㄷ.날짜.isoformat(),
            ㄷ.종목코드,
            ㄷ.종목명,
            "예" if ㄷ.승인 else "아니오",
            _수(ㄷ.예상가, 0),
            _수(ㄱ.기준가, 0),
            *[_수(ㄱ.값(ㅋ)) for ㅋ, _, _ in 지평들],
            ㄱ.못한까닭 or "됨",
        ])
    return 나온것


def 요약글(것들: list[되짚기], 지평열쇠: str = "d20") -> str:
    """한 줄 요약. 화면과 로그가 같은 문장을 쓴다.

    **어느 쪽이 낫다고 단정하지 않는다.** 표본이 모자라면 모자라다고 적고,
    거절한 쪽에 비용이 안 들어 있다는 것도 같이 적는다."""
    ㄱ = 견주기(것들, 지평열쇠)
    이름 = 지평표.get(지평열쇠, (지평열쇠, 0))[0]
    if ㄱ.승인수 == 0 and ㄱ.거절수 == 0:
        return f"{이름} 뒤를 계산할 수 있는 결정이 아직 없습니다."
    말 = [f"{이름} 뒤로 승인 {ㄱ.승인수}건, 거절 {ㄱ.거절수}건을 계산했습니다."]
    if ㄱ.승인중앙값 is not None:
        말.append(f"승인한 종목의 등락률 중앙값은 {ㄱ.승인중앙값:+.2f}%입니다.")
    if ㄱ.거절중앙값 is not None:
        말.append(f"거절한 종목은 {ㄱ.거절중앙값:+.2f}%입니다.")
    if not ㄱ.믿을만한가:
        말.append(
            f"두 쪽을 합쳐 {최소표본}건에 못 미치므로 이 숫자로는 아직 "
            "판단하지 않습니다."
        )
    말.append(
        "거절한 종목에는 수수료와 슬리피지가 들어 있지 않고, 승인한 종목의 "
        "등락률도 실제 손익이 아니라 계속 보유했을 때의 값입니다."
    )
    return " ".join(말)
