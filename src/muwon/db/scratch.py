"""흉내만 내는 실행이 운영 DB를 건드리지 못하게 사본으로 실행한다.

## 왜 필요한가

2026-08-25에 실제로 사고가 났다. "09:05에 뭘 살지 미리 보자"고 돌린
`execute_approved.py --dry-run`이 **운영 DB에 매수 기록을 남겼다.**
주문은 야후 시세로 흉내만 냈는데, 엔진은 그 결과를 그대로 저장했다.
`save_position`도 `record_order`도 `save_engine_state`도 dry-run 여부를
안 본다. 그리고 워크플로가 그 DB를 구글드라이브에 올려 버렸다.

결과는 **계좌는 멀쩡한데 기록만 거짓말을 하는 상태**였다:

    현금: DB 6,968,079원 vs 계좌 9,910,035원 (+2,941,956원)
    066970: DB엔 12주인데 계좌엔 없음
    411060: DB엔 51주인데 계좌엔 없음

그대로 09:05이 돌았으면 엔진이 두 종목을 이미 보유로 보고 **승인한 매수를
안 하거나**, 유령 포지션에 손절이 걸려 **없는 주식에 매도 주문**을 냈다.

## 왜 이 방법인가

엔진이 DB에 쓰는 자리는 일곱 군데다(주문·포지션·청산·매매·엔진상태·
실행기록·신호). 거기마다 `if not dry_run:`을 붙이는 방법도 있지만,
**하나만 빠뜨려도 같은 사고가 다시 난다.** 나중에 쓰는 자리가 하나 늘면
그때 또 빠뜨린다.

대신 **쓸 곳 자체를 바꾼다.** 사본을 만들어 그쪽을 보게 하면, 무엇을 쓰든
원본에는 닿지 않는다. 빠뜨릴 자리가 없다.

읽기는 그대로 된다. 사본이 원본의 완전한 복사본이라 보유 종목도 현금도
실제 값으로 읽힌다. 판단 결과는 진짜와 같고 흔적만 안 남는다.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

SQLITE = "sqlite:///"


def 사본으로(database_url: str) -> str:
    """sqlite DB를 임시 사본으로 복사하고 그 사본의 URL을 돌려준다.

    sqlite가 아니면 **거부한다.** 사본을 못 만드는데 조용히 원본 URL을
    돌려주면 부르는 쪽은 안전하다고 믿고 쓴다. 그게 이 파일이 생긴
    이유다. 막을 수 없으면 도는 것보다 서는 쪽이 낫다.
    """
    if not database_url.startswith(SQLITE):
        raise ValueError(
            f"sqlite가 아니라 사본을 못 만듭니다: {database_url.split('://')[0]}://…\n"
            "흉내만 내는 실행이 운영 DB에 쓰는 것을 막을 수 없으므로 멈춥니다."
        )

    원본 = Path(database_url[len(SQLITE):])
    사본 = Path(tempfile.mkdtemp(prefix="muwon-dryrun-")) / "muwon.db"

    if 원본.exists():
        shutil.copy2(원본, 사본)
        # sqlite는 WAL 모드일 때 아직 본체에 안 옮겨진 내용이 -wal에 남는다.
        # 그것까지 안 가져오면 사본이 옛 상태로 보이고, 그 위에서 내린
        # 판단은 진짜 실행과 달라진다. 미리 보는 의미가 없어진다.
        for 딸림 in ("-wal", "-shm"):
            곁 = 원본.with_name(원본.name + 딸림)
            if 곁.exists():
                shutil.copy2(곁, 사본.with_name(사본.name + 딸림))

    return f"{SQLITE}{사본}"
