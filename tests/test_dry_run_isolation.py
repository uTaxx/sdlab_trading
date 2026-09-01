"""흉내만 내는 실행이 운영 DB와 알림을 오염시키지 않는지 고정한다.

2026-08-25에 실제로 난 사고를 시험으로 박아 둔다. `--dry-run`이

  1. 운영 DB에 매수 기록을 남겼고(엔진이 dry-run 여부를 안 본다)
  2. 실제 체결과 글자 하나 안 다른 텔레그램 알림을 보냈다

계좌엔 아무것도 없는데 DB만 12주·51주를 들고 있다고 말하는 상태가 됐다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from muwon.db.scratch import 사본으로
from muwon.notify.dry_run import 머리, 모의알림

# ── 사본으로 ────────────────────────────────────────────────────


def _db만들기(길: Path, 값: str) -> None:
    연결 = sqlite3.connect(길)
    연결.execute("CREATE TABLE 것 (v TEXT)")
    연결.execute("INSERT INTO 것 VALUES (?)", (값,))
    연결.commit()
    연결.close()


def _읽기(database_url: str) -> list[str]:
    연결 = sqlite3.connect(database_url.removeprefix("sqlite:///"))
    나온것 = [r[0] for r in 연결.execute("SELECT v FROM 것")]
    연결.close()
    return 나온것


def test_사본은_원본과_다른_파일이다(tmp_path):
    원본 = tmp_path / "muwon.db"
    _db만들기(원본, "원본")

    사본url = 사본으로(f"sqlite:///{원본}")

    assert 사본url != f"sqlite:///{원본}"
    assert Path(사본url.removeprefix("sqlite:///")) != 원본


def test_사본에_쓴_것이_원본에_안_남는다(tmp_path):
    """이 시험이 사고의 핵심이다. 흉내 낸 매수가 운영 DB에 남았다."""
    원본 = tmp_path / "muwon.db"
    _db만들기(원본, "원본")

    사본url = 사본으로(f"sqlite:///{원본}")
    연결 = sqlite3.connect(사본url.removeprefix("sqlite:///"))
    연결.execute("INSERT INTO 것 VALUES ('흉내낸_매수')")
    연결.commit()
    연결.close()

    assert _읽기(f"sqlite:///{원본}") == ["원본"], "원본이 오염됐다"
    assert "흉내낸_매수" in _읽기(사본url)


def test_사본은_원본_내용을_그대로_들고_온다(tmp_path):
    """읽기는 진짜 값이어야 한다. 보유 종목과 현금이 실제와 달라지면
    미리 보는 의미가 없다. 판단이 진짜 실행과 갈린다."""
    원본 = tmp_path / "muwon.db"
    _db만들기(원본, "보유중인것")

    assert _읽기(사본으로(f"sqlite:///{원본}")) == ["보유중인것"]


def test_sqlite가_아니면_거부한다():
    """사본을 못 만드는데 조용히 원본 URL을 돌려주면, 부르는 쪽은
    안전하다고 믿고 운영 DB에 쓴다. 그게 이 파일이 생긴 이유다."""
    with pytest.raises(ValueError, match="sqlite가 아니라"):
        사본으로("postgresql://localhost/muwon")


def test_원본이_아직_없어도_된다(tmp_path):
    """첫 실행이라 DB 파일이 없을 수 있다. 거기서 터지면 안 된다."""
    사본url = 사본으로(f"sqlite:///{tmp_path / '없는것.db'}")
    assert 사본url.startswith("sqlite:///")


# ── 모의알림 ────────────────────────────────────────────────────


class _받아적는알림:
    def __init__(self):
        self.보낸것: list[str] = []

    def send(self, message: str, *, 꼬리: bool = True) -> None:
        self.보낸것.append(message)

    def send_long(self, message: str) -> int:
        self.보낸것.append(message)
        return 1


def test_모의알림은_모의라고_적는다():
    진짜 = _받아적는알림()

    모의알림(진짜).send("🟢 매수 체결\n종목: ACE KRX금현물(411060)")

    assert 진짜.보낸것[0].startswith(머리)
    assert "모의" in 진짜.보낸것[0]


def test_모의알림은_본문을_안_지운다():
    진짜 = _받아적는알림()

    모의알림(진짜).send("🟢 매수 체결\n수량: 51주")

    assert "51주" in 진짜.보낸것[0]


def test_긴_글도_표시가_붙는다():
    """결과 요약은 send_long으로 나간다. 한쪽만 붙이면 그쪽으로 새 나간다."""
    진짜 = _받아적는알림()

    모의알림(진짜).send_long("🧾 승인 매매 결과")

    assert 진짜.보낸것[0].startswith(머리)


def test_꼬리_인자가_그대로_전달된다():
    """조각내 보낼 때 마지막이 아닌 조각은 꼬리=False로 간다.
    감싸면서 이걸 잃으면 조각마다 대시보드 링크가 붙는다."""
    받은것 = {}

    class _꼬리확인:
        def send(self, message, *, 꼬리=True):
            받은것["꼬리"] = 꼬리

    모의알림(_꼬리확인()).send("글", 꼬리=False)

    assert 받은것["꼬리"] is False
