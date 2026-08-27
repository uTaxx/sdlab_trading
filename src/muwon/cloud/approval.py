"""매수 전에 사람이 체크해야 산다 — 승인 대기열.

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
하고, 그건 상시 도는 서버다. **시트 체크박스는 그게 없어도 된다** — 사람이
시트에서 체크하고, 다음 워크플로가 읽는다. 텔레그램은 "체크하러 오세요"를
알리는 데만 쓴다.

## 세 가지 규칙 — 전부 '안 사는 쪽'으로 틀린다

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
            줄.append(f"  지난 날짜라 무시 {len(self.지난날것)}건 — 어제 승인은 오늘 못 씁니다")
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
    except Exception:  # noqa: BLE001 — 이름을 못 찾는다고 알림이 죽으면 안 된다
        return 열쇠


def 알림글(후보들, 날짜: date, 주소: str, 살펴본수: int | None = None,
         전략: str = "", 섹터요약: str = "") -> str:
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
        본것.append(f"쓰는 전략: {_전략이름(전략)}")

    if not 후보들:
        줄 = [f"{머리}", "오늘 살 만한 종목이 없습니다.", ""]
        if 본것:
            줄.append("  " + " · ".join(본것))
        if 섹터요약:
            줄.append(f"  섹터 강도: {섹터요약}")
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
    줄 = [f"{머리}", f"살 만한 종목 {len(후보들)}개를 찾았습니다.", ""]
    if 본것:
        줄 += ["  " + " · ".join(본것)]
    if 섹터요약:
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
        if c.reason:
            줄.append(f"     고른 이유: {c.reason}")

    if 총액 > 0:
        줄 += ["", f"  전부 승인하면 {총액:,.0f}원을 씁니다"]

    줄 += [
        "",
        "─────────────",
        "승인하시면 어떻게 되나",
        "  · 오늘 오전 9시 5분에 승인한 종목만 삽니다",
        "  · 그때 시장에서 팔리는 값으로 사므로, 위 가격과 다를 수 있습니다",
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


def read_today(sheet_id: str, 날짜: date, svc=None):
    """오늘 후보 (종목코드, 이름) 목록과 지금까지의 결정.

    버튼을 다시 그리려면 **지금 상태**를 알아야 한다 — 누른 뒤에 화면이
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
