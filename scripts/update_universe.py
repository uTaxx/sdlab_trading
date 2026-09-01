"""매매 대상 종목 목록(유니버스)을 시가총액 상위로 다시 뽑아 저장한다.

data/universe.py의 기본 목록은 사람이 골라 고정해 둔 18종목이라 시간이
지나면 낡는다(상장폐지·순위 역전·신규 대형주 누락). 주기적으로 이 스크립트를
돌려 갱신하면, 매매 스크립트가 자동으로 최신 스냅샷을 쓴다.

덮어쓰지 않고 스냅샷으로 쌓기 때문에, 종목이 언제 어떻게 바뀌었는지
남는다. 성과 변화가 전략 탓인지 종목 교체 탓인지 구분하려면 필요하다.

주의: KIS의 순위 조회 API는 모의투자를 지원하지 않을 수 있다. 거부되면
사유를 그대로 출력하고 종료하며, 기존 유니버스는 그대로 유지된다.

사용 예:
    python scripts/update_universe.py                 # 미리보기만(저장 안 함)
    python scripts/update_universe.py --apply         # 실제로 저장
    python scripts/update_universe.py --apply --size 40
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.config import bootstrap_settings
from muwon.data.kis_client import KISClient
from muwon.data.universe import UNIVERSE
from muwon.data.universe_builder import (
    KIND_MARKET_CAP,
    KIND_VOLUME,
    active_universe,
    build_universe,
    build_volume_universe,
    diff_universe,
    save_snapshot,
)
from muwon.db.session import make_session_factory
from muwon.notify.telegram import TelegramNotifier
from muwon.settings.service import build_settings_service


def main() -> None:
    parser = argparse.ArgumentParser(description="순위 기준으로 매매 대상 종목 갱신")
    parser.add_argument(
        "--kind",
        choices=[KIND_MARKET_CAP, KIND_VOLUME],
        default=KIND_MARKET_CAP,
        help="market_cap: 시가총액 상위(실거래가 쓰는 목록) / volume: 거래대금 상위(실험용)",
    )
    parser.add_argument("--size", type=int, default=30, help="유니버스 종목 수 (기본 30)")
    parser.add_argument(
        "--kosdaq-ratio",
        type=float,
        default=0.3,
        help="코스닥에 할당할 비율 (기본 0.3: 시총만으로 뽑으면 코스닥이 0종목이 된다)",
    )
    parser.add_argument("--apply", action="store_true", help="실제로 저장한다(없으면 미리보기만)")
    parser.add_argument("--notify", action="store_true", help="변경 내역을 텔레그램으로 발송")
    parser.add_argument(
        "--basis",
        default="amount",
        choices=["amount", "volume", "surge"],
        help="volume 종류일 때 줄 세우는 기준 (기본 amount=거래대금)",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        default=1000,
        help="volume 종류일 때 제외할 최저 가격 (기본 1000원: 저가주는 호가 단위가 커서 "
        "종가 체결 가정이 실제와 벌어진다)",
    )
    args = parser.parse_args()

    settings_service = build_settings_service()
    creds = settings_service.get_kis_credentials()
    if not creds.app_key or not creds.app_secret:
        raise SystemExit("KIS 인증정보가 없습니다. configure.py kis ...로 먼저 설정하세요.")

    client = KISClient.from_settings(settings_service)
    session_factory = make_session_factory(bootstrap_settings.database_url)

    try:
        if args.kind == KIND_VOLUME:
            new_universe, metrics = build_volume_universe(
                client,
                size=args.size,
                kosdaq_ratio=args.kosdaq_ratio,
                basis=args.basis,
                min_price=args.min_price,
            )
        else:
            new_universe, metrics = build_universe(
                client, size=args.size, kosdaq_ratio=args.kosdaq_ratio
            )
    except RuntimeError as e:
        raise SystemExit(
            f"❌ 유니버스 갱신 실패: {e}\n"
            "모의투자 키로는 순위 조회가 막혀 있을 수 있습니다. 그 경우 기존 "
            "종목 목록이 그대로 유지되므로 매매에는 영향이 없습니다."
        ) from e

    if not new_universe:
        raise SystemExit("❌ 조건에 맞는 종목이 하나도 없습니다. 갱신을 중단합니다.")

    current = active_universe(session_factory, list(UNIVERSE), kind=args.kind)
    added, removed = diff_universe(current, new_universe)

    print(f"\n=== 유니버스 갱신 [{args.kind}] {'(미리보기)' if not args.apply else ''} ===")
    print(f"현재 {len(current)}종목 → 신규 {len(new_universe)}종목")
    for rank, ticker in enumerate(new_universe, start=1):
        print(f"{rank:>3}. {ticker.name}({ticker.symbol}) {ticker.market}")

    print(f"\n편입 {len(added)}종목: {', '.join(added) if added else '없음'}")
    print(f"제외 {len(removed)}종목: {', '.join(removed) if removed else '없음'}")

    if not args.apply:
        print("\n미리보기 모드입니다. 저장하려면 --apply 를 붙이세요.")
        return

    save_snapshot(session_factory, new_universe, metrics, kind=args.kind)
    if args.kind == KIND_VOLUME:
        print(
            f"\n✅ 저장 완료: 실험용 목록 {len(new_universe)}종목입니다. "
            "실거래 대상은 바뀌지 않습니다(실거래는 market_cap 목록만 읽습니다)."
        )
    else:
        print(
            f"\n✅ 저장 완료: 다음 매매 실행부터 이 {len(new_universe)}종목을 대상으로 합니다."
        )

    if args.notify and (added or removed):
        message = (
            f"📋 매매 대상 종목 갱신 ({len(new_universe)}종목)\n"
            f"편입: {', '.join(added) if added else '없음'}\n"
            f"제외: {', '.join(removed) if removed else '없음'}"
        )
        TelegramNotifier(settings_service).send(message)


if __name__ == "__main__":
    main()
