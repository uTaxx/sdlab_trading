"""화면이 읽을 상한별 측정 자료를 뽑는 자리를 본다.

## 여기서 꼭 잡아야 하는 것

**줄이 값만 담은 배열이다.** 칸 이름을 줄마다 되풀이하면 파일이 세 배가
된다. 대신 머리의 칸 목록과 줄의 값이 어긋나면 화면이 엉뚱한 값을 읽는데,
그 어긋남은 아무것도 빨갛게 만들지 않는다.

**못 잰 값이 0이 되면 안 된다.** 계산하지 못한 것을 0으로 두면 잃지도
벌지도 않은 전략과 같은 자리에 선다.

**측정이 하나도 없으면 파일을 안 고친다.** 빈 파일로 덮으면 화면에 있던
자료가 사라지고, 아직 안 쌓인 것과 지워 버린 것을 구별할 수 없다.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from muwon.analysis import window_perf as ㅇ
from muwon.analysis import window_store as ㅅ
from muwon.db.session import make_session_factory

_뿌리 = Path(__file__).resolve().parent.parent


def _스크립트():
    자리 = importlib.util.spec_from_file_location(
        "export_window_data", _뿌리 / "scripts" / "export_window_data.py")
    쪽 = importlib.util.module_from_spec(자리)
    자리.loader.exec_module(쪽)
    return 쪽


ㅁ = _스크립트()


def _잰것(이름="volume_surge_3d", 상한=20, 슬립=0.001, 대상="sheet",
        연환산=12.0, 종목수=63) -> ㅇ.잰것:
    return ㅇ.잰것(
        전략=이름, 상한=상한, 슬리피지=슬립, 매매대상=대상,
        시작일=date(2021, 1, 4), 끝일=date(2026, 9, 4), 잰날=date(2026, 9, 4),
        구간=ㅇ.구간성적(
            길이=상한, 겹침=False, 구간수=69, 기하평균=0.912345,
            연환산=연환산, 산술평균=1.1, 중앙값=0.8, 플러스비율=55.0,
            하위10=-4.2, 하위25=-1.5, 최악=-11.0, 최고=14.0, 표준편차=5.5,
            하락대비수익=0.31, 구간낙폭중앙값=-3.3),
        겹친구간=None,
        매매=ㅇ.매매성적(
            매매수=282, 승률=48.0, 손익비=1.4, 기대수익=0.7, 중앙값=0.2,
            평균보유일수=6.4, 미청산수=2,
            갈래비율={"손절": 30.0, "익절": 0.0, "트레일링": 5.0,
                    "매도신호": 26.0, "기간만료": 39.0}),
        누적수익률=154.0, 최대낙폭=-22.5, 종목수=종목수,
    )


def _채운DB(tmp_path, 잰것들, 대상="sheet"):
    만들기 = make_session_factory(f"sqlite:///{tmp_path / 't.db'}")
    with 만들기() as 세션:
        ㅅ.쌓기(세션, 잰것들, 잰날=date(2026, 9, 4), 매매대상=대상)
    return 만들기


# ── 줄의 모양 ──────────────────────────────────────────────────


def test_줄의_길이가_칸_수와_같다(tmp_path):
    """어긋나면 화면이 엉뚱한 자리의 값을 읽는다."""
    자료 = ㅁ.모으기(_채운DB(tmp_path, [_잰것()]))
    [줄] = 자료["측정"]["sheet"]["줄"]
    assert len(줄) == len(자료["칸들"])


def test_칸_이름으로_값을_찾을_수_있다(tmp_path):
    자료 = ㅁ.모으기(_채운DB(tmp_path, [_잰것(연환산=36.8)]))
    [줄] = 자료["측정"]["sheet"]["줄"]
    자리 = {이름: i for i, 이름 in enumerate(자료["칸들"])}

    assert 줄[자리["전략"]] == "volume_surge_3d"
    assert 줄[자리["상한"]] == 20
    assert 줄[자리["연환산"]] == pytest.approx(36.8)
    assert 줄[자리["매매수"]] == 282
    assert 줄[자리["기간만료비율"]] == pytest.approx(39.0)
    assert 줄[자리["최대낙폭"]] == pytest.approx(-22.5)


def test_못_잰_값은_비워_둔다(tmp_path):
    """0으로 채우면 잃지도 벌지도 않은 전략과 같은 자리에 선다."""
    빈것 = ㅇ.잰것(
        전략="빔", 상한=20, 슬리피지=0.0, 매매대상="sheet",
        시작일=date(2021, 1, 4), 끝일=date(2026, 9, 4), 잰날=date(2026, 9, 4),
        구간=ㅇ.구간재기([], 20), 겹친구간=None, 매매=ㅇ.매매재기([]),
    )
    자료 = ㅁ.모으기(_채운DB(tmp_path, [빈것]))
    [줄] = 자료["측정"]["sheet"]["줄"]
    자리 = {이름: i for i, 이름 in enumerate(자료["칸들"])}
    assert 줄[자리["연환산"]] is None
    assert 줄[자리["승률"]] is None
    assert 줄[자리["기간만료비율"]] is None


def test_전략_이름을_한글로_같이_넣는다(tmp_path):
    """화면 어디에도 volume_surge_3d가 뜨면 안 된다."""
    자료 = ㅁ.모으기(_채운DB(tmp_path, [_잰것()]))
    assert 자료["이름표"]["volume_surge_3d"] == "거래량 급증 3일"


def test_조건을_값과_같이_남긴다(tmp_path):
    """조건 없는 숫자는 나중에 확인할 수 없다."""
    칸 = ㅁ.모으기(_채운DB(tmp_path, [_잰것()]))["측정"]["sheet"]
    assert 칸["종목수"] == 63
    assert 칸["잰날"] == "2026-09-04"
    assert 칸["시작일"] == "2021-01-04"
    assert 칸["상한들"] == [20]
    assert 칸["슬리피지들"] == [0.001]


# ── 매매 대상 ───────────────────────────────────────────────────


def test_매매_대상마다_따로_담는다(tmp_path):
    만들기 = _채운DB(tmp_path, [_잰것(대상="sheet")], 대상="sheet")
    with 만들기() as 세션:
        ㅅ.쌓기(세션, [_잰것(대상="market_cap", 종목수=30)],
              잰날=date(2026, 9, 4), 매매대상="market_cap")

    자료 = ㅁ.모으기(만들기)
    assert set(자료["측정"]) == {"sheet", "market_cap"}
    assert 자료["측정"]["market_cap"]["종목수"] == 30
    assert 자료["매매대상이름"]["sheet"] == "실거래 시트"


def test_한쪽만_있어도_그쪽은_담는다(tmp_path):
    자료 = ㅁ.모으기(_채운DB(tmp_path, [_잰것()]))
    assert set(자료["측정"]) == {"sheet"}


# ── 파일로 쓰기 ─────────────────────────────────────────────────


def test_측정이_없으면_파일을_안_고친다(tmp_path, monkeypatch):
    """빈 파일로 덮으면 화면에 있던 자료가 사라진다. 아직 안 쌓인 것과
    지워 버린 것은 화면에서 구별되지 않는다."""
    나온곳 = tmp_path / "상한측정.json"
    나온곳.write_text('{"측정":{"sheet":{}}}', encoding="utf-8")

    monkeypatch.setattr(ㅁ.bootstrap_settings, "database_url",
                        f"sqlite:///{tmp_path / '빈.db'}", raising=False)
    monkeypatch.setattr("sys.argv",
                        ["export_window_data.py", "--나온곳", str(나온곳)])
    assert ㅁ.main() == 1
    assert "sheet" in 나온곳.read_text(encoding="utf-8")


def test_파일로_쓰면_다시_읽힌다(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    만들기 = make_session_factory(f"sqlite:///{db}")
    with 만들기() as 세션:
        ㅅ.쌓기(세션, [_잰것()], 잰날=date(2026, 9, 4), 매매대상="sheet")

    나온곳 = tmp_path / "상한측정.json"
    monkeypatch.setattr(ㅁ.bootstrap_settings, "database_url",
                        f"sqlite:///{db}", raising=False)
    monkeypatch.setattr("sys.argv",
                        ["export_window_data.py", "--나온곳", str(나온곳)])
    assert ㅁ.main() == 0

    자료 = json.loads(나온곳.read_text(encoding="utf-8"))
    assert len(자료["측정"]["sheet"]["줄"]) == 1
    assert 자료["주의"]


# ── 워크플로 ────────────────────────────────────────────────────


def test_쌓는_워크플로가_화면_자료도_뽑는다():
    """사람이 손으로 옮겨 적을 수 없는 양이다. 줄이 천 개가 넘는다."""
    글 = (_뿌리 / ".github" / "workflows" / "store-window-scan.yml").read_text(
        encoding="utf-8")
    assert "scripts/export_window_data.py" in 글
    assert "dashboard/자료/상한측정.json" in 글


def test_미리보기로_돌면_화면_자료를_안_고친다():
    글 = (_뿌리 / ".github" / "workflows" / "store-window-scan.yml").read_text(
        encoding="utf-8")
    뽑는곳 = 글.index("scripts/export_window_data.py")
    앞부분 = 글[:뽑는곳]
    assert 앞부분.rindex("dry_run != 'true'") > 앞부분.rindex("- name:")


def test_안_바뀌었으면_빈_커밋을_안_남긴다():
    """빈 커밋이 쌓이면 기록에서 실제로 자료가 바뀐 날을 찾을 수 없다."""
    글 = (_뿌리 / ".github" / "workflows" / "store-window-scan.yml").read_text(
        encoding="utf-8")
    assert "git diff --cached --quiet" in 글
