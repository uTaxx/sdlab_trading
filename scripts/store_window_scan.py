"""측정 결과 파일을 읽어 상태 DB에 쌓는다. 설계안 §6, §14다.

## 왜 재는 것과 따로 두나

측정은 30분에서 한 시간 걸린다. 상태 DB는 구글드라이브의 파일 하나라,
고치는 워크플로는 전부 `state-write` 자물쇠로 묶어 겹치지 않게 한다. 측정을
그 자물쇠 안에서 돌리면 한 시간 동안 장중 손절 감시와 매수 후보 산출이
전부 기다리게 된다.

그래서 나눴다. 재는 워크플로는 자물쇠 없이 오래 돌면서 결과를 파일로만
남기고, 이 스크립트가 그 파일을 몇 초 만에 DB에 넣는다. 자물쇠는 이쪽만
잡는다.

## 순위도 같이 남긴다

나중에 판단 기준을 바꾸면 순위가 통째로 달라지는데, 그때 "예전에는 무엇이
1위였나"를 계산으로 되살릴 수 없다. 그 사이 매매 대상 종목이 바뀌기
때문이다.

**매매가 0건인 전략은 순위에서 뺀다.** 한 건도 안 산 것을 수익률 0%로
맨 위에 두지 않기 위해서다. 요약 표와 같은 규칙이다.

## 실행

    python scripts/store_window_scan.py --파일 window-scan.json
    python scripts/store_window_scan.py --파일 window-scan.json --미리보기
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis import window_judgment as ㅈ
from muwon.analysis import window_perf as ㅇ
from muwon.analysis import window_store as ㅅ
from muwon.analysis.window_report import 요약찍기
from muwon.config import bootstrap_settings
from muwon.db.session import make_session_factory
from muwon.settings.service import build_settings_service

한국 = ZoneInfo("Asia/Seoul")


def 인자읽기() -> argparse.Namespace:
    ㄱ = argparse.ArgumentParser(description="측정 결과를 상태 DB에 쌓기")
    ㄱ.add_argument("--파일", default="window-scan.json")
    ㄱ.add_argument("--미리보기", action="store_true",
                  help="DB를 고치지 않고 무엇이 들어갈지만 출력한다")
    ㄱ.add_argument("--표찍기", action="store_true",
                  help="쌓기 전에 요약 표를 로그에 찍는다")
    ㄱ.add_argument("--sheet-id", default=os.environ.get("MUWON_SHEET_ID", ""))
    ㄱ.add_argument("--folder-id", default=os.environ.get("GDRIVE_FOLDER_ID", ""))
    return ㄱ.parse_args()


def 읽어들이기(내용: dict) -> tuple[list[ㅇ.잰것], str, date]:
    """파일 한 벌을 `잰것` 목록과 조건으로 되돌린다.

    **매매대상 열쇠가 없는 옛 파일도 받는다.** 첫 두 측정이 그 칸 없이
    나왔다. 그때는 사람이 읽는 이름에서 되짚는다. 그것도 못 찾으면 빈 글자로
    두고 부르는 쪽이 막는다. 모르는 것을 아무 이름으로나 채우면 나중에
    서로 다른 측정이 한 이름 밑에 섞인다."""
    열쇠 = str(내용.get("매매대상열쇠") or "").strip()
    if not 열쇠:
        보인글 = str(내용.get("매매대상") or "")
        if "시가총액" in 보인글:
            열쇠 = "market_cap"
        elif "시트" in 보인글:
            열쇠 = "sheet"

    잰날글 = str(내용.get("잰날") or "")
    잰날 = date.fromisoformat(잰날글) if 잰날글 else datetime.now(한국).date()

    종목수 = int(내용.get("종목수") or 0)
    if not 종목수:
        # 종목 수를 따로 안 적던 판이다. 사람이 읽는 글에 "63종목"처럼
        # 들어 있으므로 거기서 되짚는다. 못 찾으면 0으로 둔다. 0은
        # "모른다"는 뜻이고, 아무 수나 채워 넣는 것보다 낫다.
        찾은것 = re.search(r"(\d+)\s*종목", str(내용.get("매매대상") or ""))
        종목수 = int(찾은것.group(1)) if 찾은것 else 0
    잰것들 = []
    for 줄 in 내용.get("줄") or []:
        ㄱ = ㅇ.잰것으로(줄)
        if not ㄱ.종목수 and 종목수:
            # 줄마다 종목수를 안 적던 판이다. 머리에 적힌 값으로 채운다.
            ㄱ = replace(ㄱ, 종목수=종목수)
        잰것들.append(ㄱ)
    return 잰것들, 열쇠, 잰날


def 기준고르기(인자):
    """시트에 적힌 판단 기준. 못 읽으면 기본값이다.

    **되돌아갔다는 사실을 반드시 찍는다.** 조용히 기본값으로 돌면 화면에
    적힌 지침과 실제로 매긴 순위가 다른 날이 생긴다.

    시트를 못 읽었다고 쌓기를 멈추지는 않는다. 순위 한 벌이 기본 기준으로
    남는 것이, 측정 결과가 통째로 안 남는 것보다 낫다.

    **연 단위 지침과 다른 칸을 읽는다**(2026-09-04에 나눔). 전에는 둘이
    `rank_1st` 한 자리를 나눠 썼는데, 거기 적힌 `worst_slice`가 구간 성적
    목록에 없는 이름이라 이 순위는 언제나 기본값으로 돌아갔다. 화면에서
    1순위를 바꿔도 여기에는 아무 영향이 없었다."""
    try:
        시트 = 인자.sheet_id
        if not 시트 and 인자.folder_id:
            from muwon.cloud.sector_sheet import DEFAULT_TITLE, find_or_create

            시트, _ = find_or_create(인자.folder_id, DEFAULT_TITLE)
        if not 시트:
            print("  시트를 못 찾아 기본 판단 기준으로 갑니다.")
            return ㅈ.기본기준

        from muwon.settings.from_sheet import build_policy_provider

        _, _, 시트설정 = build_policy_provider(build_settings_service(), 시트)
        적힌것 = str(시트설정.가져오기(ㅈ.시트칸[0]) or "").strip()
        고른것 = ㅈ.시트에서(시트설정)
        if 적힌것 and 적힌것 != 고른것.일순위:
            print(f"  시트의 1순위 '{적힌것}'은 이 자료로 못 읽어 "
                  f"기본 판단 기준으로 갑니다.")
        elif 적힌것:
            print(f"  시트에 적힌 판단 기준으로 순위를 냅니다: "
                  f"{고른것.일순위} → {고른것.이순위} → {고른것.삼순위}")
        return 고른것
    except Exception as 탈:  # noqa: BLE001
        print(f"  판단 기준을 시트에서 못 읽어 기본값으로 갑니다: {탈}")
        return ㅈ.기본기준


def main() -> int:
    인자 = 인자읽기()
    경로 = Path(인자.파일)
    if not 경로.exists():
        print(f"::error::측정 결과 파일이 없습니다: {경로}")
        return 1

    내용 = json.loads(경로.read_text(encoding="utf-8"))
    잰것들, 대상열쇠, 잰날 = 읽어들이기(내용)

    if not 잰것들:
        print("::error::파일에 줄이 하나도 없습니다.")
        return 1
    if not 대상열쇠:
        # 여기서 멈추는 것이 맞다. 이름을 지어내 넣으면 앞서 쌓은 줄과
        # 다른 측정으로 갈리고, 화면은 한쪽을 통째로 못 본다.
        print("::error::매매 대상이 무엇인지 파일에 없습니다. 쌓지 않습니다.")
        return 1

    상한들 = sorted({ㄱ.상한 for ㄱ in 잰것들})
    슬리피지들 = sorted({ㄱ.슬리피지 for ㄱ in 잰것들})
    기준 = 기준고르기(인자)

    print(f"■ {경로}: {len(잰것들)}줄")
    print(f"■ 잰날 {잰날} · 매매대상 {대상열쇠} · 상한 {상한들} · 슬리피지 {슬리피지들}")
    print(f"■ 판단 기준 {' → '.join(기준.열쇠들)}")

    # 재는 워크플로가 요약을 찍기 전 판이면 결과를 볼 길이 여기뿐이다.
    # 아티팩트를 내려받는 주소가 막힌 자리에서도 이 표는 로그로 남는다.
    if 인자.표찍기:
        요약찍기(내용.get("줄") or [], 상한들, 슬리피지들)

    if 인자.미리보기:
        print("■ 미리보기입니다. DB를 고치지 않았습니다.")
        return 0

    session_factory = make_session_factory(bootstrap_settings.database_url)
    with session_factory() as 세션:
        넣은수 = ㅅ.쌓기(세션, 잰것들, 잰날=잰날, 매매대상=대상열쇠)
        print(f"■ window_perf {넣은수}줄")

        순위수 = 0
        for 상한 in 상한들:
            for 슬립 in 슬리피지들:
                고른것 = [
                    ㄱ for ㄱ in 잰것들
                    if ㄱ.상한 == 상한 and ㄱ.슬리피지 == 슬립 and ㄱ.매매.매매수 > 0
                ]
                if not 고른것:
                    continue
                순위수 += ㅅ.순위쌓기(
                    세션, 고른것, 기준, 잰날=잰날, 상한=상한,
                    슬리피지=슬립, 매매대상=대상열쇠,
                )
        print(f"■ strategy_rank {순위수}줄")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
