"""매수 전에 사람이 체크해야 산다. 승인 대기열.

`docs/설계_스트림릿을_걷어낼까.md`의 **5단계**이고, LX MI 시스템에서
가장 값나갔던 부분(사람 승인 스텝)을 이쪽에 옮긴 것이다.

## 왜 필요한가

모의투자를 꺼 둔 이유가 "완전 자동이 무섭다"였다. 그런데 켜지 않으면
**슬리피지(사겠다고 판단한 값과 실제로 사진 값의 차이) 실측 표본이 영영
안 생긴다.** 지금 이 저장소의 모든 백테스트 숫자가 "종가에 딱 체결됐다"는
가정 위에 있고, 그 가정을 검증할 방법이 그것뿐이다.

승인 스텝이 그 사이를 잇는다. 자동으로 고르되 **사람이 체크한 것만 산다.**

## 왜 텔레그램 버튼이 아니라 시트인가

텔레그램 버튼을 받으려면 봇이 응답을 받는 자리(웹훅이나 폴링)가 있어야
하고, 그건 상시 도는 서버다. **시트 체크박스는 그게 없어도 된다**. 사람이
시트에서 체크하고, 다음 워크플로가 읽는다. 텔레그램은 "체크하러 오세요"를
알리는 데만 쓴다.

## 세 가지 규칙: 전부 '안 사는 쪽'으로 틀린다

**① 빈 칸은 승인이 아니다.** 체크 안 한 것, 지운 것, 오타 전부 거부다.
`종목` 탭에서는 빈 칸이 '켜짐'이지만 여기서는 반대다. 사는 쪽으로 기우는
기본값을 두면 안 된다.

**② 어제 승인은 오늘 못 쓴다.** 후보를 낸 날짜와 주문 내는 날짜가 다르면
무시한다. 어제 좋아 보이던 종목이 오늘 20% 올라 있을 수 있다.

**③ 목록에 없는 줄은 무시한다.** 사람이 시트에 손으로 종목을 적어 넣어도
사지 않는다. 승인은 "제안된 것 중에 고르는" 행위지 "새로 주문하는" 행위가
아니다. 새로 사고 싶으면 증권사 앱에서 사는 것이 맞다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from muwon.text import 을를, 이가

승인머리 = ["열쇠", "날짜", "종목코드", "종목명", "섹터", "전략", "수량", "예상가",
            "승인", "이유"]

#: 명시적으로 이 중 하나여야 승인이다. 빈 칸·오타는 전부 거부다.
승인표시 = ("Y", "YES", "TRUE", "1", "O", "OK", "승인", "예", "V", "✓", "☑")


@dataclass(frozen=True)
class 후보:
    symbol: str
    name: str
    strategy: str
    quantity: int
    price: float
    reason: str = ""
    #: 어느 섹터에서 나왔나. 한 섹터에 몰리는 것을 막는 데 쓴다.
    sector: str = ""
    sector_name: str = ""
    #: 최근 사흘 하루하루의 등락률(%). 오래된 날부터. 자료가 모자라면 빈 값.
    #:
    #: 승인 단추를 누를 때 "이미 많이 오른 것을 사는 건 아닌가"를 보는 자리다.
    #: 거래량 급증 전략은 오른 날 사는 전략이라 그 판단이 특히 필요하다.
    사흘등락: tuple[float, ...] = ()


@dataclass(frozen=True)
class 상한넘긴것:
    """섹터 보유 상한에 걸려 오늘은 살 수 없는 종목 (2026-09-02에 더함).

    ## 왜 지우지 않고 보여 주나

    전에는 상한에 걸린 것을 후보 목록에서 조용히 빼기만 했다. 그러면 오늘
    전략이 무엇을 찾았는지가 안 보인다. 후보가 둘뿐인 날에, 신호가 둘밖에
    안 난 것인지 다섯이 났는데 셋이 상한에 걸린 것인지가 갈린다. 뒤쪽이면
    자리가 비는 대로 살 것이 있다는 뜻이라 전혀 다른 이야기다.

    ## 승인 버튼은 안 붙인다

    09:05 실행이 섹터 상한을 진짜 보유 기준으로 다시 본다. 버튼을 붙이면
    눌러도 안 사고, 그것이 곧 "승인했는데 왜 안 샀지"다. 시트의 승인대기
    탭에도 안 올린다."""

    symbol: str
    name: str
    섹터이름: str
    #: 지금 그 섹터를 몇 종목 들고 있나.
    보유수: int
    #: 오늘 승인 목록에 든 같은 섹터 종목 수. 자리를 채우는 이유가 둘이라
    #: 나눠 적는다. 이미 들고 있어서 막힌 것과, 오늘 후보끼리 밀린 것은
    #: 사람이 할 일이 다르다. 앞쪽은 팔려야 자리가 나고 뒤쪽은 오늘 안에
    #: 다른 것을 거절하면 된다.
    오늘후보수: int
    상한: int


@dataclass(frozen=True)
class 승인결과:
    """읽어서 판단이 끝난 상태. **왜 안 샀는지가 왜 샀는지만큼 중요하다.**"""

    승인된것: tuple[str, ...]
    거부된것: tuple[str, ...]
    지난날것: tuple[str, ...]
    목록밖: tuple[str, ...]
    #: 거부된 것 중 **버튼으로 명시하게 거절한 것.** 매매 결과는 빈 칸과
    #: 같지만, "안 봤다"와 "보고 거절했다"는 전혀 다른 이야기다. 앞의 것은
    #: 알림이 안 갔거나 놓친 것이고, 뒤의 것은 판단이다. 구별해 두지 않으면
    #: 승인 스텝이 실제로 쓰이고 있는지 알 수가 없다.
    명시거절: tuple[str, ...] = ()

    @property
    def 무응답(self) -> tuple[str, ...]:
        본것 = set(self.명시거절)
        return tuple(s for s in self.거부된것 if s not in 본것)

    def 요약(self) -> str:
        줄 = [f"승인 {len(self.승인된것)}종목 · 미승인 {len(self.거부된것)}종목"]
        if self.거부된것:
            줄.append(
                f"  그중 눌러서 거절 {len(self.명시거절)}종목 · "
                f"아무 답 없음 {len(self.무응답)}종목"
            )
        if self.지난날것:
            줄.append(f"  지난 날짜라 무시 {len(self.지난날것)}건: 어제 승인은 오늘 못 씁니다")
        if self.목록밖:
            줄.append(
                f"  ⚠️ 제안한 적 없는 종목 {len(self.목록밖)}건을 무시했습니다: "
                f"{', '.join(self.목록밖)}"
            )
        return "\n".join(줄)


def 열쇠(날짜: date, symbol: str) -> str:
    return f"A{날짜.isoformat()}|{symbol}"


def pending_rows(후보들: Iterable[후보], 날짜: date) -> list[list[str]]:
    """오늘의 후보 → 시트에 올릴 줄. **승인 칸은 비워서 올린다.**

    미리 체크해 두면 '기본값이 산다'가 되고, 그건 승인 스텝이 없는 것과
    같다."""
    return [
        [
            열쇠(날짜, c.symbol),
            날짜.isoformat(),
            c.symbol,
            c.name,
            c.sector_name or c.sector,
            c.strategy,
            str(c.quantity),
            f"{c.price:.0f}",
            "",  # ← 사람이 여기에 체크한다
            c.reason,
        ]
        for c in 후보들
    ]


def parse_approvals(
    줄들: Sequence[Sequence[str]], 날짜: date, 제안한것: Iterable[str]
) -> 승인결과:
    """시트에서 읽은 줄 → 오늘 살 종목.

    **네트워크 없이 시험할 수 있게 따로 뺐다.** 규칙이 이 함수의 전부다."""
    제안 = set(제안한것)
    승인, 거부, 지난날, 목록밖, 명시거절 = [], [], [], [], []

    for 줄 in 줄들[1:]:  # 머리줄 건너뜀
        칸 = (list(줄) + [""] * len(승인머리))[: len(승인머리)]
        if not str(칸[0]).strip():
            continue
        적힌날 = str(칸[1]).strip()
        symbol = str(칸[2]).strip()
        적힌것 = str(칸[8]).strip().upper()
        체크됨 = 적힌것 in 승인표시

        if not 체크됨:
            거부.append(symbol)
            if 적힌것:
                명시거절.append(symbol)
            continue
        if 적힌날 != 날짜.isoformat():
            지난날.append(symbol)
            continue
        if symbol not in 제안:
            목록밖.append(symbol)
            continue
        승인.append(symbol)

    return 승인결과(
        승인된것=tuple(dict.fromkeys(승인)),
        거부된것=tuple(dict.fromkeys(거부)),
        지난날것=tuple(dict.fromkeys(지난날)),
        목록밖=tuple(dict.fromkeys(목록밖)),
        명시거절=tuple(dict.fromkeys(명시거절)),
    )


_요일 = ("월", "화", "수", "목", "금", "토", "일")


def _날짜글(날짜) -> str:
    """'2026-08-25'보다 '8월 25일(화)'가 폰에서 빨리 읽힌다."""
    try:
        return f"{날짜.month}월 {날짜.day}일({_요일[날짜.weekday()]})"
    except AttributeError:
        return str(날짜)


def _전략이름(열쇠: str) -> str:
    """`volume_surge_5d`를 사람이 읽는 이름으로.

    전략 카탈로그에 이미 한글 이름이 있는데(`거래량 급증 단타 (2배, 5일 보유)`)
    알림에는 코드 이름이 그대로 나가고 있었다. 처음 보는 사람에게
    `volume_surge_5d`는 아무 뜻도 없다."""
    try:
        from muwon.strategy.registry import get_definition

        return get_definition(열쇠).화면이름
    except Exception:  # noqa: BLE001 (이름을 못 찾는다고 알림이 죽으면 안 된다)
        return 열쇠


def 키를이름으로(글: str) -> str:
    """글 안에 섞인 전략 키를 사람이 읽는 이름으로 바꾼다.

    막힌 까닭 같은 문장은 `f"예약한 전략이 목록에 없습니다: {줄.새전략}"`처럼
    키를 그대로 끼워 만든다. 그 문장이 사람에게 갈 때는 `volume_surge_3d`가
    아니라 `거래량 급증 3일`이어야 한다.

    긴 키부터 바꾼다. `volume_surge_5d`를 먼저 바꾸면 `volume_surge_5d_ma20`이
    반쪽만 바뀌어 `거래량 급증 5일_ma20`이 된다."""
    if not 글:
        return 글
    try:
        from muwon.strategy.registry import list_definitions

        표 = {ㅈ.key: ㅈ.화면이름 for ㅈ in list_definitions()}
    except Exception:  # noqa: BLE001 (이름을 못 찾는다고 알림이 죽으면 안 된다)
        return 글
    for 키 in sorted(표, key=len, reverse=True):
        글 = 글.replace(키, 표[키])
    return 글


#: 08:20 반영이 오늘 무엇을 했는지 한 줄로 적을 때 쓰는 머리말.
변경없음글 = "전략 변경: 없음. 어제와 같은 전략으로 계산했습니다"


def 전략변경글(줄) -> str:
    """오늘 아침 전략이 바뀌었는지를 한 줄로.

    ## 왜 매수 후보 알림에 붙이나

    08:20 반영은 바꿨을 때와 막혔을 때만 알린다. 아무 일도 없는 날이 대부분인데
    매일 "오늘도 안 바꿨습니다"를 따로 보내면 알림이 흔해지고, 흔해진 알림은
    진짜일 때도 안 읽힌다.

    그렇다고 안 적으면 아침에 받은 후보가 어느 전략으로 나온 것인지 알 수 없다.
    그래서 **이미 매일 나가는 08:30 후보 알림 안에** 한 줄로 넣는다. 알림 수는
    안 늘고, 후보를 승인할 그 자리에서 같이 읽힌다.

    ## 막힌 날이 제일 중요하다

    막히면 전략이 안 바뀐 채로 후보가 나온다. 사람은 바뀐 줄 알고 승인한다.
    그래서 막힌 날은 경고 표시를 붙이고 까닭까지 적는다."""
    if 줄 is None:
        return 변경없음글
    if getattr(줄, "상태", "") == "막힘":
        까닭 = 키를이름으로(getattr(줄, "막힌까닭", "").strip() or "까닭이 기록되지 않았습니다")
        # 까닭 안에 이미 쌍점이 들어 있는 경우가 많다("목록에 없습니다: 거래량
        # 급증 3일"). 앞에 쌍점을 하나 더 붙이면 한 줄에 둘이 되어 어디까지가
        # 까닭인지 안 보인다. 줄을 나누고 이름표는 두 칸 띄어 붙인다.
        return (
            "⚠️ 전략 변경이 막혔습니다. 아래 후보는 이전 전략으로 계산한 것입니다.\n"
            f"사유  {까닭}"
        )
    이전 = _전략이름(getattr(줄, "이전전략", "") or "")
    새것 = _전략이름(getattr(줄, "새전략", "") or "")
    if not 새것:
        return 변경없음글
    # 조사를 글자로 박으면 안 된다. 전략 이름이 설정에서 오므로 무엇이 올지
    # 모른다. 2026-09-01에 같은 실수를 두 번 겪었다.
    from muwon.text import 으로로

    끝 = f"{새것}{으로로(새것)}"
    바뀜 = f"{이전}에서 {끝}" if 이전 else 끝
    return f"전략 변경: 오늘 아침에 {바뀜} 바꿨습니다"


def _사흘글(등락: Sequence[float]) -> list[str]:
    """최근 사흘 등락을 두 줄로. 자료가 모자라면 빈 목록.

    **합계와 하루하루를 같이 적는다.** 합계만 보면 사흘 내리 오른 것과
    이틀 빠지고 하루 크게 오른 것이 같아 보인다. 승인 단추 앞에서는 그
    둘이 다른 이야기다."""
    값 = [ㄱ for ㄱ in 등락 if ㄱ is not None]
    if not 값:
        return []
    합 = 1.0
    for ㄱ in 값:
        합 *= 1 + ㄱ / 100
    하루하루 = " · ".join(f"{ㄱ:+.1f}%" for ㄱ in 값)
    return [f"     최근 {len(값)}일 {(합 - 1) * 100:+.1f}%  ({하루하루})"]


def _섹터글(섹터강도: Sequence) -> list[str]:
    """섹터 강도를 전부 적는다. 오른 순이다.

    **뽑힌 것만 적으면 안 뽑힌 섹터가 왜 빠졌는지 알 수 없다.** 강도가
    나쁜 섹터에서 신호가 안 나온 것인지, 애초에 안 본 것인지가 갈린다."""
    있는것 = [ㄱ for ㄱ in 섹터강도 if getattr(ㄱ, "상대강도", None) is not None]
    if not 있는것:
        return []
    줄 = ["섹터 강도 (최근 20일, 코스피 대비)"]
    for ㄱ in sorted(있는것, key=lambda ㅅ: -ㅅ.상대강도):
        표 = "○" if getattr(ㄱ, "뽑힘", False) else " "
        줄.append(f"  {표} {ㄱ.이름:<10} {ㄱ.상대강도:>+6.1f}%p")
    못잰것 = [ㄱ.이름 for ㄱ in 섹터강도 if getattr(ㄱ, "상대강도", None) is None]
    if 못잰것:
        줄.append(f"    못 잰 섹터: {', '.join(못잰것)}")
    줄.append("  ○ 표시가 오늘 매수 대상 섹터입니다")
    return 줄


@dataclass(frozen=True)
class 보유종목:
    """알림에 적을 보유 현황 한 줄.

    남은거래일이 None이면 셀 수 없었다는 뜻이다. 0으로 채우면 "오늘 판다"로
    읽히는데, 그것과 "며칠 남았는지 모른다"는 전혀 다른 말이다."""

    symbol: str
    name: str
    entry_date: date
    상한: int | None = None
    남은거래일: int | None = None


def _보유글(보유들: Sequence[보유종목]) -> list[str]:
    """들고 있는 종목이 며칠 뒤에 팔리는지.

    **보유 기간은 팔리는 여러 이유 중 하나일 뿐이다.** 손절이나 매도 신호가
    먼저 걸리면 그 전에 판다. 남은 날짜만 적으면 "그날까지는 안 판다"로
    읽히므로 그 문장을 같이 넣는다.

    날짜는 거래일로 센다. 달력으로 세면 연휴가 낀 주에 하루씩 어긋난다."""
    if not 보유들:
        return []

    줄 = [f"들고 있는 종목 {len(보유들)}개"]
    for ㄱ in 보유들:
        줄.append(f"  {ㄱ.name}({ㄱ.symbol})")
        산날 = f"{ㄱ.entry_date:%m월 %d일} 매수"
        if ㄱ.남은거래일 is None or not ㄱ.상한:
            줄.append(f"     {산날} · 매도까지 며칠인지 못 셌습니다")
            continue
        남은 = ㄱ.남은거래일
        if 남은 < 0:
            # 팔았어야 하는데 아직 들고 있다. 매도가 꺼져 있거나 주문이
            # 거부된 것이다. "오늘 판다"로 적으면 그 사실이 묻힌다.
            언제 = f"매도일이 {-남은}거래일 지났는데 아직 안 팔렸습니다"
        elif 남은 == 0:
            언제 = "오늘 팝니다"
        elif 남은 == 1:
            언제 = "내일 팝니다"
        else:
            언제 = f"{남은}거래일 뒤에 팝니다"
        줄.append(f"     {산날} · 보유 {ㄱ.상한}거래일 중 {ㄱ.상한 - 남은}일 지남 · {언제}")

    줄.append("  손절이나 매도 신호가 먼저 걸리면 그 전에 팝니다")
    return 줄


def _상한초과글(넘긴것들) -> list[str]:
    """섹터 보유 상한에 걸린 것을 빨간 램프로 적는다.

    조용히 빼면 오늘 전략이 무엇을 찾았는지가 안 보인다. 후보가 둘뿐인 날에
    신호가 둘밖에 안 난 것인지, 다섯이 났는데 셋이 상한에 걸린 것인지가
    갈린다."""
    if not 넘긴것들:
        return []
    줄 = ["🔴 섹터 보유 상한에 걸려 오늘은 못 사는 종목"]
    for ㄱ in 넘긴것들:
        줄.append(f"  🔴 [{ㄱ.섹터이름}] {ㄱ.name}({ㄱ.symbol})")
        # 세 경우의 문장을 따로 만든다. 한 틀에 끼워 넣으면 "화학을 오늘
        # 후보에 3종목이 있어"처럼 목적어가 붙을 데가 없는 문장이 나온다.
        #
        # 섹터 이름의 받침에 따라 을/를과 이/가가 갈린다. 손으로 적으면
        # "2차전지을"이 나온다(설계안 §38에서 같은 실수를 한 번 했다).
        이름 = ㄱ.섹터이름
        if ㄱ.보유수 and ㄱ.오늘후보수:
            앞 = (f"{이름}{을를(이름)} 이미 {ㄱ.보유수}종목 들고 있고 "
                 f"오늘 후보에 {ㄱ.오늘후보수}종목이 더 있어")
        elif ㄱ.보유수:
            앞 = f"{이름}{을를(이름)} 이미 {ㄱ.보유수}종목 들고 있어"
        elif ㄱ.오늘후보수:
            앞 = f"오늘 후보에 {이름}{이가(이름)} {ㄱ.오늘후보수}종목 있어"
        else:
            앞 = "자리가 차서"
        줄.append(f"     {앞} 자리가 없습니다. "
                 f"섹터당 보유 상한은 {ㄱ.상한}종목입니다")
    줄.append("  승인 버튼을 만들지 않았습니다. 눌러도 살 수 없는 종목입니다")
    줄.append("  들고 있는 종목이 팔려 자리가 비면 다음 날 후보로 다시 나옵니다")
    return 줄


def 알림글(후보들, 날짜: date, 주소: str, 살펴본수: int | None = None,
         전략: str = "", 섹터요약: str = "", 섹터강도=(), 보유=(),
         전략변경: str = "", 상한초과=()) -> str:
    """텔레그램으로 보낼 글. **버튼이 아니라 '보러 오세요'다.**

    ## 후보가 없는 날에도 할 말이 있다

    "오늘은 없습니다" 한 줄만 보내면, **제대로 돌아서 0인지 고장 나서 0인지
    구별이 안 된다.** 며칠 조용하면 "요즘 신호가 없나 보다"로 넘기게 되는데,
    실은 시세를 못 받고 있었을 수도 있다.

    그래서 없는 날에도 **몇 종목을 무슨 기준으로 봤는지**를 같이 보낸다.
    45종목을 봤는데 0개인 것과, 0종목을 봐서 0개인 것은 전혀 다른 얘기다."""
    머리 = f"📋 {_날짜글(날짜)}"
    본것 = []
    if 살펴본수 is not None:
        본것.append(f"살펴본 종목 {살펴본수}개")
    if 전략:
        본것.append(f"현재 전략: {_전략이름(전략)}")

    if not 후보들:
        줄 = [f"{머리}", "오늘은 매수 조건에 맞는 종목이 없습니다.", ""]
        if 본것:
            줄.append("  " + " · ".join(본것))
        if 전략변경:
            줄 += [f"  {ㄹ}" for ㄹ in 전략변경.splitlines()]
        섹터줄 = _섹터글(섹터강도)
        if 섹터줄:
            줄 += ["", *섹터줄]
        elif 섹터요약:
            줄.append(f"  섹터 강도: {섹터요약}")
        넘긴줄 = _상한초과글(상한초과)
        if 넘긴줄:
            줄 += ["", *넘긴줄]
        보유줄 = _보유글(보유)
        if 보유줄:
            줄 += ["", *보유줄]
        if 살펴본수 == 0:
            # 0종목을 봤다는 것은 신호가 없는 게 아니라 **시세를 못 받은
            # 것**이다. 같은 "후보 없음"이라도 이건 고쳐야 하는 상태다.
            줄 += ["", ("⚠️ 살펴본 종목이 0개입니다. 신호가 없어서가 아니라 "
                        "시세나 목록을 못 읽은 것입니다. 고쳐야 하는 상태입니다.")]
        줄 += [
            "",
            "아무것도 안 하셔도 됩니다. 조건에 맞는 종목이 없는 날이 훨씬 많습니다.",
        ]
        return "\n".join(줄)

    총액 = sum(c.quantity * c.price for c in 후보들)
    줄 = [f"{머리}", f"매수 조건에 맞는 종목이 {len(후보들)}개 있습니다.", ""]
    if 본것:
        줄 += ["  " + " · ".join(본것)]
    if 전략변경:
        줄 += [f"  {ㄹ}" for ㄹ in 전략변경.splitlines()]
    섹터줄 = _섹터글(섹터강도)
    if 섹터줄:
        줄 += ["", *섹터줄]
    elif 섹터요약:
        줄.append(f"  섹터 강도: {섹터요약}")
    줄.append("")

    for c in 후보들:
        섹터 = f"[{c.sector_name or c.sector}] " if (c.sector_name or c.sector) else ""
        줄.append(f"  {섹터}{c.name}({c.symbol})")
        if c.quantity:
            줄.append(
                f"     {c.quantity}주 · 1주 {c.price:,.0f}원 → "
                f"{c.quantity * c.price:,.0f}원어치"
            )
        else:
            줄.append(f"     1주 {c.price:,.0f}원")
        줄 += _사흘글(getattr(c, "사흘등락", ()))
        if c.reason:
            줄.append(f"     고른 이유: {c.reason}")

    if 총액 > 0:
        줄 += ["", f"  전부 승인하면 {총액:,.0f}원을 씁니다"]

    넘긴줄 = _상한초과글(상한초과)
    if 넘긴줄:
        줄 += ["", "─────────────", *넘긴줄]

    보유줄 = _보유글(보유)
    if 보유줄:
        줄 += ["", "─────────────", *보유줄]

    줄 += [
        "",
        "─────────────",
        "승인하시면 어떻게 되나",
        "  · 오늘 오전 9시 5분에 승인한 종목만 삽니다",
        "  · 그때의 시장 가격으로 매수하므로 위 가격과 다를 수 있습니다",
        "    (위 가격은 어제 종가입니다)",
        "  · 아무것도 안 하시면 아무것도 안 삽니다. 그게 기본값입니다",
        "",
        "아래 버튼을 누르시거나, 시트에서 직접 체크하셔도 됩니다:",
        주소,
    ]
    return "\n".join(줄)


def set_decisions(sheet_id: str, 날짜: date, 결정: dict[str, str], svc=None
                  ) -> tuple[list[str], list[str]]:
    """`승인대기` 탭의 승인 칸에 Y(승인)나 N(거절)을 적는다. (적은 것, 못 찾은 것).

    **오늘 줄만 고친다.** 어제 줄은 어차피 사지 않지만(위의 규칙 ②), 거기
    흔적을 남기면 나중에 기록을 읽을 때 헷갈린다.

    **거절도 적는다.** 빈 칸으로 두면 매매 결과는 같지만, 나중에 "안 봤다"와
    "보고 거절했다"를 구별할 수 없다.

    못 찾은 것을 돌려주는 이유는, 승인했다고 믿는 종목이 실제로는 후보에
    없었을 때 **그 사실을 말해 줘야** 하기 때문이다."""
    from muwon.cloud.sheet_log import _service

    svc = svc or _service().spreadsheets()
    칸 = svc.values().get(spreadsheetId=sheet_id, range="승인대기!A1:J5000").execute(num_retries=3)
    줄들 = 칸.get("values", [])
    적은것 = []
    남은것 = dict(결정)

    for i, 줄 in enumerate(줄들):
        칸값 = (list(줄) + [""] * len(승인머리))[: len(승인머리)]
        if str(칸값[1]).strip() != 날짜.isoformat():
            continue
        symbol = str(칸값[2]).strip()
        if symbol not in 남은것:
            continue
        svc.values().update(
            spreadsheetId=sheet_id, range=f"승인대기!I{i + 1}",
            valueInputOption="RAW", body={"values": [[남은것[symbol]]]},
        ).execute(num_retries=3)
        적은것.append(symbol)
        남은것.pop(symbol)

    return 적은것, sorted(남은것)


def approve_in_sheet(sheet_id: str, 날짜: date, 종목들, svc=None
                     ) -> tuple[list[str], list[str]]:
    """`/승인 005930`처럼 손으로 친 명령이 쓰는 길. 전부 Y로 적는다."""
    return set_decisions(sheet_id, 날짜, dict.fromkeys(종목들, "Y"), svc=svc)


def 지난결정(sheet_id: str, 최소날짜: date | None = None, svc=None) -> list[dict]:
    """시트에 쌓인 승인·거절 결정을 전부 읽는다.

    `read_today`는 하루치만 본다. 되짚기는 지난 것을 다 봐야 하므로 따로 둔다.

    **승인 칸이 빈 줄은 거절로 읽지 않고 뺀다.** 안 누른 것과 거절한 것은
    다른 판단이다. 빈 것을 거절로 세면 "거절한 종목이 이만큼 올랐다"가
    실제로는 "쳐다보지도 않은 종목"의 이야기가 된다."""
    from muwon.cloud.sheet_log import _service

    svc = svc or _service().spreadsheets()
    칸 = svc.values().get(
        spreadsheetId=sheet_id, range="승인대기!A1:J5000"
    ).execute(num_retries=3)
    나온것 = []
    for 줄 in 칸.get("values", [])[1:]:
        칸값 = (list(줄) + [""] * len(승인머리))[: len(승인머리)]
        적힌것 = str(칸값[8]).strip().upper()
        if not 적힌것:
            continue
        try:
            날 = date.fromisoformat(str(칸값[1]).strip())
        except ValueError:
            continue
        if 최소날짜 is not None and 날 < 최소날짜:
            continue
        코드 = str(칸값[2]).strip()
        if not 코드:
            continue
        try:
            예상가 = float(str(칸값[7]).replace(",", "").strip() or 0)
        except ValueError:
            예상가 = 0.0
        나온것.append({
            "날짜": 날,
            "종목코드": 코드,
            "종목명": str(칸값[3]).strip(),
            "승인": 적힌것 in 승인표시,
            "예상가": 예상가,
        })
    return 나온것


def read_today(sheet_id: str, 날짜: date, svc=None):
    """오늘 후보 (종목코드, 이름) 목록과 지금까지의 결정.

    버튼을 다시 그리려면 **지금 상태**를 알아야 한다. 누른 뒤에 화면이
    안 바뀌면 먹었는지 몰라서 또 누르게 된다."""
    from muwon.cloud.sheet_log import _service
    from muwon.notify.telegram_buttons import 버튼항목

    svc = svc or _service().spreadsheets()
    칸 = svc.values().get(spreadsheetId=sheet_id, range="승인대기!A1:J5000").execute(num_retries=3)
    후보, 결정 = [], {}
    for 줄 in 칸.get("values", [])[1:]:
        칸값 = (list(줄) + [""] * len(승인머리))[: len(승인머리)]
        if str(칸값[1]).strip() != 날짜.isoformat():
            continue
        symbol = str(칸값[2]).strip()
        후보.append(버튼항목(symbol, str(칸값[3]).strip()))
        적힌것 = str(칸값[8]).strip().upper()
        if 적힌것:
            결정[symbol] = "Y" if 적힌것 in 승인표시 else "N"
    return 후보, 결정
