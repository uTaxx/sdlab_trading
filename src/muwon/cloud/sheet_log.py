"""매매·전망·하루 요약을 구글 시트에 쌓는다. **덧붙이기만 한다.**

`docs/설계_스트림릿을_걷어낼까.md`의 **3단계**다. 2단계에서 시트가 설정의
원본이 됐고, 여기서 **기록도 시트에서 볼 수 있게** 한다. 그러면 대시보드를
켜지 않고도 "어제 뭘 샀고 지금 뭘 들고 있나"를 폰에서 본다.

## 방향이 반대다. 설정과 헷갈리지 말 것

| | 원본 | 코드는 |
|---|---|---|
| `섹터`·`종목`·`설정` 탭 | **사람** | 읽기만 |
| `매매기록`·`전망기록`·`일일요약` 탭 | **코드** | 쓰기만 |

한 탭을 양쪽이 고치면 충돌 처리를 만들어야 한다. 그래서 탭마다 주인을
하나로 못 박았다.

## 덧붙이기만 하고 지우지 않는다

지난 줄을 고치지 않는다. **기록을 고칠 수 있으면 기록이 아니다**. 나중에
"그때 왜 샀지"를 볼 때 믿을 수 있어야 한다. 잘못 들어간 줄이 있으면 지우지
말고 다음 줄에 정정을 남긴다.

## 두 번 돌아도 두 줄이 되지 않는다

워크플로는 재실행되고, 재실행은 실패를 고치는 정상적인 수단이다. 그런데
그때마다 같은 매매가 한 줄씩 늘면 **시트를 세어 만든 숫자가 전부 틀린다.**

그래서 줄마다 맨 앞에 **열쇠**를 붙이고, 올리기 전에 시트에 이미 있는
열쇠를 읽어 **없는 것만** 올린다. 열쇠는 DB의 표와 id를 붙인 값이라
(`T31`, `F2026-08-19|반도체|20`) 같은 것이 두 번 만들어지지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

매매머리 = ["열쇠", "청산일", "종목", "전략", "수량", "산값", "판값",
            "손익금액", "손익%", "산이유", "판이유", "모의"]
주문머리 = ["열쇠", "때", "종목", "사고팜", "수량", "값", "상태", "주문번호", "모의"]
회차머리 = ["열쇠", "때", "전략", "매매대상", "살펴본종목", "매수신호", "매도신호",
            "주문수", "막힌이유", "현금", "평가액"]
알림머리 = ["열쇠", "때", "종류", "글"]
이력머리 = ["열쇠", "때", "무엇", "이전", "이후"]
전망머리 = ["열쇠", "낸날", "대상", "지평", "중앙값%", "상승확률%",
            "아주나빴을때%", "구간수", "우연넘음", "실제%"]
요약머리 = ["열쇠", "날짜", "매수", "매도", "거부", "평가액", "현금", "메모"]

#: 시트 한 셀은 5만 자까지지만, 그만큼 긴 이유는 읽지도 못한다.
MAX_CELL = 200


def _자르기(글: object) -> str:
    """긴 글은 잘라 넣는다. 시트에서 한 줄이 화면을 다 먹으면 표가 아니다."""
    s = "" if 글 is None else str(글)
    return s if len(s) <= MAX_CELL else s[: MAX_CELL - 1] + "…"


def _날짜(값: object) -> str:
    if isinstance(값, datetime):
        return 값.strftime("%Y-%m-%d %H:%M")
    if isinstance(값, date):
        return 값.isoformat()
    return _자르기(값)


def trade_rows(trades: Iterable) -> list[list[str]]:
    """완결된 매매(TradeRow) → 시트 줄. **네트워크 없이 시험한다.**"""
    줄들 = []
    for t in trades:
        줄들.append([
            f"T{t.id}",
            _날짜(t.exited_at),
            _자르기(t.symbol),
            _자르기(t.strategy_key),
            str(t.quantity),
            f"{t.entry_price:.0f}",
            f"{t.exit_price:.0f}",
            f"{t.pnl_amount:.0f}",
            f"{t.pnl_pct:.2f}",
            _자르기(t.entry_reason),
            _자르기(t.exit_reason),
            "모의" if t.is_paper else "실거래",
        ])
    return 줄들


def _사고팜(side: object) -> str:
    """`buy`/`sell`을 화면에 쓸 말로. 모르는 값은 지어내지 않고 그대로 둔다."""
    글 = str(side or "").strip().lower()
    return {"buy": "매수", "sell": "매도"}.get(글, 글 or "?")


def _체결상태(o) -> str:
    """`price`가 진짜 체결가인지, 조회에 실패해 기준가를 쓴 것인지.

    이 둘을 같은 말로 적으면 슬리피지 통계에 '차이 0'인 가짜 표본이 섞인다.
    화면에서도 마찬가지다. '체결'이라고 적힌 줄의 값은 실제로 그 값에
    사고판 것이어야 한다."""
    확인 = getattr(o, "fill_confirmed", None)
    if 확인 is True:
        return "체결"
    if 확인 is False:
        return "값 미확인"
    return "기록 전"


def order_rows(orders: Iterable) -> list[list[str]]:
    """낸 주문(OrderRow) → 시트 줄.

    완결된 매매(`trade_rows`)와 다르다. 저건 사고판 것이 짝지어진 뒤에야
    생기므로, **산 날에는 아무것도 안 남아** 화면이 "오늘 아무 일도 없었다"로
    보인다. 주문은 낸 즉시 남는다."""
    줄들 = []
    for o in orders:
        줄들.append([
            f"O{o.id}",
            _날짜(o.created_at),
            _자르기(o.symbol),
            _사고팜(o.side),
            str(o.quantity),
            f"{o.price:.0f}",
            _체결상태(o),
            _자르기(getattr(o, "kis_order_id", "")),
            "모의" if o.is_paper else "실거래",
        ])
    return 줄들


def runlog_rows(runs: Iterable) -> list[list[str]]:
    """엔진 회차(RunLogRow) → 시트 줄.

    **체결이 없어도 한 줄이 남는다.** 빈 화면이 "오늘은 살 게 없었다"인지
    "오늘은 아예 안 돌았다"인지 여기서만 갈린다. 둘은 고치는 방법이
    정반대다."""
    줄들 = []
    for r in runs:
        막힌것 = str(getattr(r, "rejections", "") or "").strip()
        줄들.append([
            f"R{r.id}",
            _날짜(r.created_at),
            _자르기(r.strategy_key),
            str(getattr(r, "universe_size", 0)),
            str(getattr(r, "checked_symbols", 0)),
            str(getattr(r, "buy_signals", 0)),
            str(getattr(r, "sell_signals", 0)),
            str(getattr(r, "orders", 0)),
            _자르기(막힌것.replace("\n", " · ")),
            f"{getattr(r, 'cash', 0.0):.0f}",
            f"{getattr(r, 'equity', 0.0):.0f}",
        ])
    return 줄들


#: 사람이 바꾼 것이 아니라 코드가 매번 다시 적는 값들. 표에 넣으면 진짜
#: 변경이 그 사이에 묻힌다. 실제로 106줄 중 100줄이 이것이었다.
_기계가쓰는키 = (
    "kis.access_token", "kis.token_expires_at", "telegram.update_offset",
)


def history_rows(변경들: Iterable) -> list[list[str]]:
    """설정 변경 기록(AppSettingHistoryRow) → 시트 줄.

    **비밀값은 값을 안 적는다.** 토큰과 API 키가 같은 표에 들어 있고,
    시트는 사람이 열어 보는 곳이다. 무엇이 언제 바뀌었다는 사실만 남긴다.

    **안 바뀐 줄은 빼고, 기계가 매번 다시 적는 값도 뺀다.** 이 표는 "성적이
    달라졌을 때 무엇을 바꿨는지"를 보는 곳이다. 워크플로가 돌 때마다 같은
    자격증명을 다시 써 넣으므로, 거르지 않으면 그 줄이 표를 다 채우고 진짜
    변경 한 줄이 그 사이에 묻힌다."""
    줄들 = []
    for h in 변경들:
        키 = str(h.key or "")
        옛것 = h.old_value or ""
        새것 = h.new_value or ""
        if 키 in _기계가쓰는키:
            continue
        if 옛것 == 새것:
            continue
        비밀 = bool(getattr(h, "is_secret", False))
        줄들.append([
            f"H{h.id}",
            _날짜(h.changed_at),
            _자르기(키),
            "(비밀값)" if 비밀 else _자르기(옛것),
            "(비밀값)" if 비밀 else _자르기(새것),
        ])
    return 줄들


def notice_rows(orders: Iterable = (), trades: Iterable = (),
                runs: Iterable = ()) -> list[list[str]]:
    """"오늘 무슨 일이 있었나"를 시간 순으로.

    별도의 알림 표를 DB에 두지 않는다. 알림은 이미 일어난 일을 말로 옮긴
    것이라, 원본이 둘이 되면 **어느 쪽이 맞는지 알 수 없는 날이 온다.**
    그래서 주문·매매·회차 기록에서 그때그때 만든다.

    회차는 주문이 하나도 없었을 때만 적는다. 주문이 있으면 주문 줄이
    같은 말을 더 자세히 하고 있다."""
    줄들 = []
    for o in orders:
        줄들.append([
            f"NO{o.id}",
            _날짜(o.created_at),
            "체결" if getattr(o, "fill_confirmed", None) else "주문",
            _자르기(
                f"{o.symbol} {o.quantity:,}주를 {o.price:,.0f}원에 "
                f"{'샀습니다' if _사고팜(o.side) == '매수' else '팔았습니다'}."
            ),
        ])
    for t in trades:
        줄들.append([
            f"NT{t.id}",
            _날짜(t.exited_at),
            "청산",
            _자르기(
                f"{t.symbol}을 정리했습니다. {t.pnl_amount:+,.0f}원 ({t.pnl_pct:+.1f}%)."
            ),
        ])
    for r in runs:
        if getattr(r, "orders", 0):
            continue
        막힌것 = str(getattr(r, "rejections", "") or "").strip()
        신호 = getattr(r, "buy_signals", 0) + getattr(r, "sell_signals", 0)
        if 막힌것:
            글 = f"신호 {신호}건이 났지만 주문은 없었습니다. {막힌것.splitlines()[0]}"
        elif 신호:
            글 = f"신호 {신호}건이 났지만 주문은 없었습니다. 막힌 이유는 안 남았습니다."
        else:
            글 = f"살 만한 신호가 없었습니다. 종목 {getattr(r, 'checked_symbols', 0)}개를 봤습니다."
        줄들.append([f"NR{r.id}", _날짜(r.created_at), "회차", _자르기(글)])

    # 시간 순으로 세운다. 화면은 이걸 뒤집어 최근 것부터 보여 준다.
    줄들.sort(key=lambda 줄: 줄[1])
    return 줄들


def forecast_rows(전망들: Iterable) -> list[list[str]]:
    """낸 전망 → 시트 줄.

    열쇠에 id를 못 쓴다. 전망 기록은 나중에 실제 결과가 채워지면서 같은
    줄이 바뀌기 때문이다. 그래서 (낸날·대상·지평)을 열쇠로 쓴다. 하루에
    같은 대상·지평 전망을 두 번 내지 않으므로 이걸로 충분하다."""
    줄들 = []
    for f in 전망들:
        낼수있나 = getattr(f, "낼수있나", True)
        줄들.append([
            f"F{_날짜(f.기준일)}|{f.대상}|{f.지평}",
            _날짜(f.기준일),
            _자르기(f.대상),
            str(f.지평),
            f"{f.중앙값:.1f}" if 낼수있나 else "",
            f"{f.상승확률:.0f}" if 낼수있나 else "",
            f"{f.하위10:.1f}" if 낼수있나 else "",
            str(getattr(f, "구간수", "")) if 낼수있나 else "",
            ("Y" if getattr(f, "우연을_넘었나", False) else "N") if 낼수있나 else "",
            "",  # 실제 결과는 지평이 지난 뒤에 채운다
        ])
    return 줄들


def daily_rows(
    날짜: date, 매수: int, 매도: int, 거부: int,
    평가액: float | None = None, 현금: float | None = None, 메모: str = "",
) -> list[list[str]]:
    """하루 한 줄. 열쇠가 날짜라서 **같은 날 두 번 실행해도 한 줄이다.**"""
    return [[
        f"D{날짜.isoformat()}",
        날짜.isoformat(),
        str(매수), str(매도), str(거부),
        f"{평가액:.0f}" if 평가액 is not None else "",
        f"{현금:.0f}" if 현금 is not None else "",
        _자르기(메모),
    ]]


def only_new(있는열쇠: Iterable[str], 후보: Sequence[Sequence[str]]) -> list[list[str]]:
    """시트에 없는 줄만. **재실행이 줄을 늘리지 않게 하는 자리.**

    후보 안에서도 열쇠가 겹치면 첫 줄만 남긴다. 한 번에 올리는 묶음
    안에서도 중복이 생길 수 있다."""
    본것 = set(있는열쇠)
    결과 = []
    for 줄 in 후보:
        열쇠 = str(줄[0])
        if 열쇠 in 본것:
            continue
        본것.add(열쇠)
        결과.append(list(줄))
    return 결과


# ── 여기부터는 구글에 붙는다 ────────────────────────────────────────


def _service():  # pragma: no cover: 실제 호출은 시험하지 않는다
    from googleapiclient.discovery import build

    from muwon.cloud.sector_sheet import _credentials

    return build("sheets", "v4", credentials=_credentials())


def 머리늘려야하나(있는머리: Sequence[str], 머리: Sequence[str]) -> bool:
    """칸을 새로 붙였을 때 제목 줄만 옛것으로 남는 것을 막는다.

    탭은 처음 만들 때만 제목을 적는다. 나중에 칸이 늘면 값은 오른쪽에
    붙는데 제목은 그대로라, 시트를 열어 본 사람이 그 칸이 뭔지 알 수가 없다.

    **줄어들 때는 손대지 않는다.** 앞쪽이 그대로 겹칠 때만 늘린다. 사람이
    제목을 고쳐 뒀을 수도 있는데 그것을 코드가 덮으면 안 된다."""
    있는것 = [str(ㄱ).strip() for ㄱ in (있는머리 or [])]
    새것 = [str(ㄱ).strip() for ㄱ in 머리]
    if len(있는것) >= len(새것):
        return False
    return 있는것 == 새것[: len(있는것)]


def _머리늘리기(svc, sheet_id: str, 탭: str, 머리: Sequence[str]) -> None:  # pragma: no cover
    첫줄 = (
        svc.values()
        .get(spreadsheetId=sheet_id, range=f"{탭}!1:1")
        .execute()
        .get("values", [[]])
    )
    if not 머리늘려야하나(첫줄[0] if 첫줄 else [], 머리):
        return
    svc.values().update(
        spreadsheetId=sheet_id, range=f"{탭}!A1",
        valueInputOption="RAW", body={"values": [list(머리)]},
    ).execute()


def append(sheet_id: str, 탭: str, 머리: Sequence[str], 줄들: Sequence[Sequence[str]],
           svc=None) -> int:
    """탭이 없으면 만들고, 이미 있는 열쇠는 빼고 덧붙인다. 올린 줄 수를 돌려준다."""
    if not 줄들:
        return 0
    svc = svc or _service().spreadsheets()

    있는탭 = {s["properties"]["title"] for s in svc.get(spreadsheetId=sheet_id).execute()["sheets"]}
    if 탭 not in 있는탭:
        svc.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": 탭}}}]},
        ).execute()
        svc.values().update(
            spreadsheetId=sheet_id, range=f"{탭}!A1",
            valueInputOption="RAW", body={"values": [list(머리)]},
        ).execute()
        있는열쇠: list[str] = []
    else:
        _머리늘리기(svc, sheet_id, 탭, 머리)
        칸 = svc.values().get(spreadsheetId=sheet_id, range=f"{탭}!A1:A100000").execute()
        있는열쇠 = [줄[0] for 줄 in 칸.get("values", []) if 줄]

    올릴것 = only_new(있는열쇠, 줄들)
    if not 올릴것:
        return 0
    svc.values().append(
        spreadsheetId=sheet_id, range=f"{탭}!A1",
        valueInputOption="RAW", insertDataOption="INSERT_ROWS",
        body={"values": 올릴것},
    ).execute()
    return len(올릴것)
