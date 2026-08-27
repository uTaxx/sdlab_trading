"""지금 걸린 전략을 다른 것으로 바꾼다.

## 왜 따로 스크립트인가

`configure.py strategy --active-key`가 이미 바꿀 수는 있다. 그런데 그것만으로는
**바꾸기 전에 무엇이 달라지는지 안 보인다.** 매매 규칙을 바꾸는 일인데 화면에
"저장 완료" 한 줄만 나오면, 무엇이 바뀌었는지도 지금 들고 있는 종목이 어떻게
되는지도 모르는 채로 넘어간다.

여기서는 바꾸기 전에 셋을 보여 준다.

1. 지금 걸린 것과 바꿀 것의 사는 조건·파는 조건
2. 지금 들고 있는 종목이 새 규칙으로 어떻게 되는지
3. 성적표에 있는 두 전략의 숫자

## 들고 있는 종목에도 바로 적용된다

엔진의 청산 판단은 보유 종목이 어떤 전략으로 들어왔는지가 아니라 **지금 걸린
전략**을 본다(`execution/engine.py`의 청산 블록). 그래서 바꾸는 순간 들고
있는 것에도 적용된다. 그 사실을 화면에 적는다 — 모르고 바꾸면 "왜 갑자기
팔렸지"가 된다.

## 기본은 미리보기다

`--apply` 없이는 아무것도 안 쓴다.

사용 예:
    python scripts/switch_strategy.py                       # 지금 뭐가 걸렸나
    python scripts/switch_strategy.py --key volume_surge_5d_ma20
    python scripts/switch_strategy.py --key volume_surge_5d_ma20 --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

from muwon.config import bootstrap_settings
from muwon.db.models import PositionRow
from muwon.db.session import ensure_schema, make_session_factory
from muwon.settings.schema import StrategySelection
from muwon.settings.service import build_settings_service
from muwon.strategy.registry import get_definition, list_definitions


def _한줄(키: str) -> str:
    try:
        d = get_definition(키)
    except Exception:  # noqa: BLE001 — 없는 키도 화면에는 그대로 보여 준다
        return f"{키} (등록되지 않은 키입니다)"
    return f"{키} — {d.display_name}\n      {d.description}"


def _성적(키: str) -> str:
    """성적표에 있으면 숫자를 같이 보여 준다.

    **바꾸기 전에 근거를 옆에 둔다.** 없으면 없다고 적는다 — 안 재 본 전략을
    숫자 없이 내놓으면 재 본 것처럼 읽힌다."""
    try:
        from muwon.analysis.report_card import load

        표 = load()
    except Exception as e:  # noqa: BLE001
        return f"      (성적표를 못 읽었습니다: {type(e).__name__})"

    for 줄 in 표.전략:
        if 줄.키 == 키:
            return (
                f"      평균 {줄.평균:+.1f}% · 가장 나빴던 해 {줄.최악:+.1f}% · "
                f"샤프 {줄.샤프:.2f} · 최대낙폭 {줄.낙폭:.1f}% · "
                f"손익비 {줄.손익비:.2f} · 5년 {줄.거래}거래 · 판정 {줄.판정}"
            )
    return "      (성적표에 없습니다 — 아직 같은 조건에서 안 재 봤다는 뜻입니다)"


def _섞은성적(사는키: str, 파는키: str) -> str:
    """섞은 조합을 이미 재 봤으면 그 숫자를, 아니면 안 쟀다고 적는다.

    섞은 조합은 등록된 전략이 아니라 이름이 없다. 그래서 성적표의 전략
    목록에는 없고 `매수매도분리` 쪽에 "사는키>파는키"로 들어간다."""
    키 = f"{사는키}>{파는키}"
    try:
        from muwon.analysis.report_card import load

        표 = load()
    except Exception as e:  # noqa: BLE001
        return f"  (성적표를 못 읽었습니다: {type(e).__name__})"

    for 줄 in 표.매수매도분리:
        if 줄.키 == 키:
            return (
                f"  이 조합을 재 본 숫자가 있습니다 ({표.기준.get('측정일', '측정일 모름')} 기준).\n"
                f"      평균 {줄.평균:+.1f}% · 가장 나빴던 해 {줄.최악:+.1f}% · "
                f"샤프 {줄.샤프:.2f} · 최대낙폭 {줄.낙폭:.1f}% · "
                f"손익비 {줄.손익비:.2f} · 5년 {줄.거래}거래 · 판정 {줄.판정}\n"
                f"      {줄.한줄평}"
            )
    return (
        "  ⚠ 매수와 매도를 섞은 이 조합 자체는 성적표에 없습니다.\n"
        "    위 두 줄은 각 전략을 통째로 썼을 때의 숫자입니다.\n"
        "    섞은 조합의 성적은 아직 재지 않았습니다.\n"
        "    재 보려면 '전략 실험' 워크플로를 mode=split, "
        f"keys={사는키}>{파는키} 로 돌리세요."
    )


def _나가는길경고(사는키: str, 파는키: str) -> str:
    """이 조합에서 나가는 길이 좁으면 그 이유를 한 줄로.

    매도 신호도 없고 보유 기간 상한도 없으면 손절 말고는 파는 길이 없다.
    막지는 않는다. 조용히 두지도 않는다."""
    try:
        from muwon.strategy.registry import build_strategies

        묶음 = build_strategies((사는키,), "OR", (파는키,))
    except Exception:  # noqa: BLE001
        return ""
    return getattr(묶음, "왜조심해야하나", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="", help="사는 쪽 전략 키 (비우면 목록만 보여 준다)")
    parser.add_argument(
        "--sell-key", default="",
        help="파는 쪽 전략 키. 안 주면 사는 쪽이 양쪽을 다 맡습니다(지금까지의 동작). "
             "'같게'라고 주면 따로 걸어 둔 것을 지웁니다",
    )
    parser.add_argument("--apply", action="store_true", help="실제로 바꾼다")
    args = parser.parse_args()

    print("■ 모드: " + ("실제로 바꿈(--apply)" if args.apply else "미리보기만"))
    print()

    ensure_schema(bootstrap_settings.database_url)
    service = build_settings_service()
    고름 = service.get_strategy_selection()
    지금 = 고름.active_key
    지금매도 = 고름.sell_keys[0] if 고름.sell_keys else ""

    print("지금 걸린 전략")
    print("  사는 쪽: " + _한줄(지금))
    print(_성적(지금))
    if 지금매도:
        print("  파는 쪽: " + _한줄(지금매도))
    else:
        print("  파는 쪽: 따로 안 걸림 (사는 쪽 전략의 파는 규칙을 씁니다)")
    print()

    키 = (args.key or "").strip()
    if not 키:
        print("고를 수 있는 것")
        for d in list_definitions():
            표시 = " [지금 이것]" if d.key == 지금 else ""
            print(f"  {d.key}{표시}")
        print()
        print("바꾸려면 --key 로 하나 고르세요. --apply 까지 줘야 실제로 바뀝니다.")
        return 0

    get_definition(키)  # 등록 안 된 키면 여기서 바로 에러

    # '같게'는 따로 걸어 둔 파는 쪽을 지우라는 뜻이다. 빈 값과 구별해야 한다 —
    # 빈 값은 "이번에 안 건드린다"이고, 지우려면 그 뜻을 말해야 한다.
    매도인자 = (args.sell_key or "").strip()
    지울까 = 매도인자 in ("같게", "없음")
    새매도 = "" if 지울까 else (매도인자 or 지금매도)
    if 새매도:
        get_definition(새매도)

    if 키 == 지금 and 새매도 == 지금매도:
        print(f"이미 그렇게 걸려 있습니다({고름.describe()}). 바꿀 것이 없습니다.")
        return 0

    print("바꿀 전략")
    print("  사는 쪽: " + _한줄(키))
    print(_성적(키))
    if 새매도:
        print("  파는 쪽: " + _한줄(새매도))
        print(_성적(새매도))
        print()
        # 섞은 조합은 이름이 없으므로 전략 목록에는 없다. 따로 재 둔 것이
        # 있으면 그 숫자를, 없으면 안 쟀다고 적는다 — 안 재 본 조합을 숫자
        # 없이 내놓으면 재 본 것처럼 읽힌다.
        print(_섞은성적(키, 새매도))
        나가는길 = _나가는길경고(키, 새매도)
        if 나가는길:
            print(f"  ⚠ {나가는길}")
    else:
        print("  파는 쪽: 따로 안 걺 (사는 쪽 전략의 파는 규칙을 씁니다)")
    print()

    # 들고 있는 종목은 새 규칙으로 팔린다. 그 사실을 반드시 적는다.
    session_factory = make_session_factory(bootstrap_settings.database_url)
    with session_factory() as session:
        보유 = list(session.scalars(select(PositionRow).order_by(PositionRow.symbol)))

    print("지금 들고 있는 종목")
    if not 보유:
        print("  없습니다.")
    else:
        for p in 보유:
            print(f"  {p.symbol} {p.quantity:,}주 · 산 값 {p.entry_price:,.0f}원 "
                  f"· 들어온 날 {p.entry_date} · 들어올 때 전략 {p.strategy_key or '(안 적힘)'}")
        print()
        print("  이 종목들도 바꾼 뒤부터는 새 전략의 파는 규칙으로 팔립니다.")
        print("  엔진은 '어떤 전략으로 샀나'가 아니라 '지금 무엇이 걸려 있나'를 봅니다.")

    print()
    if not args.apply:
        print("미리보기라 아무것도 안 바꿨습니다. --apply 를 주면 바꿉니다.")
        return 0

    # active_key는 읽기 전용 속성이고 생성자는 active_keys(튜플)를 받는다.
    # 미리보기에서는 이 줄까지 안 가서, 처음 돌렸을 때 실제 적용에서만 터졌다.
    service.set_strategy_selection(
        StrategySelection(active_keys=(키,), sell_keys=((새매도,) if 새매도 else ()))
    )
    뒤 = service.get_strategy_selection()
    뒤매도 = 뒤.sell_keys[0] if 뒤.sell_keys else ""
    if 뒤.active_key != 키 or 뒤매도 != 새매도:
        # 조용히 실패하면 화면은 바뀐 줄 알고 매매는 옛 규칙으로 돈다.
        print(f"❌ 바꾸지 못했습니다. 다시 읽으니 {뒤.describe()}입니다.", file=sys.stderr)
        return 1

    print(f"바꿨습니다: {지금} → {뒤.describe()}")
    print("다음 실행부터 적용됩니다. 이 변경은 변경 이력에 남습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
