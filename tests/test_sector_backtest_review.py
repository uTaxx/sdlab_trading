"""검토 탭에 곁들이는 섹터별 성적: 청산 거래를 섹터로 묶는 함수.

## 왜 따로 시험하나

`섹터별성적남기기`는 백테스트를 돌리고 시트에 쓰는 I/O 함수라 빠르게 반복
시험하기 어렵다. 거래를 묶는 부분(`섹터별성적계산`)과 줄을 만드는 부분
(`섹터별성적시트줄`)을 순수 함수로 떼어 뒀고, 여기서는 그 둘만 본다.

**칸이 밀리면 조용히 어긋난다.** 평균수익률 칸에 최악 값이 들어가도
숫자라서 화면은 멀쩡히 그려진다. n8n 창구 사본과 칸 번호가 맞는지는
`test_n8n_gateway_fields.py`가 본다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "run_strategy_review.py"
_스펙 = importlib.util.spec_from_file_location("run_strategy_review_for_sector_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["run_strategy_review_for_sector_test"] = _모듈
_스펙.loader.exec_module(_모듈)

섹터별성적계산 = _모듈.섹터별성적계산
섹터별성적시트줄 = _모듈.섹터별성적시트줄
섹터별성적머리 = _모듈.섹터별성적머리
섹터별성적_최소표본 = _모듈.섹터별성적_최소표본

from muwon.backtest.engine import ClosedTrade


def _거래(symbol, pnl_pct):
    return ClosedTrade(
        symbol=symbol, entry_date=date(2026, 1, 1), exit_date=date(2026, 1, 5),
        entry_price=10_000.0, exit_price=10_000.0 * (1 + pnl_pct / 100),
        quantity=10, pnl_pct=pnl_pct, pnl_amount=pnl_pct * 1_000,
        exit_reason="손절",
    )


섹터표 = {"000010": "SEMI", "000020": "SEMI", "000030": "BATT"}
섹터이름표 = {"SEMI": "반도체", "BATT": "2차전지"}


def test_섹터마다_평균과_승률을_낸다():
    거래들 = [_거래("000010", 5.0), _거래("000020", -3.0), _거래("000030", 10.0)]
    성적들 = 섹터별성적계산(거래들, 섹터표, 섹터이름표)

    이름들 = {ㄱ["섹터코드"]: ㄱ for ㄱ in 성적들}
    반도체 = 이름들["SEMI"]
    assert 반도체["거래수"] == 2
    assert 반도체["평균수익률"] == 1.0  # (5 + -3) / 2
    assert 반도체["승률"] == 50.0
    assert 반도체["최악"] == -3.0
    assert 반도체["최고"] == 5.0
    assert 반도체["섹터이름"] == "반도체"


def test_평균수익률_내림차순으로_정렬한다():
    거래들 = [_거래("000010", 1.0), _거래("000030", 9.0)]
    성적들 = 섹터별성적계산(거래들, 섹터표, 섹터이름표)
    assert [ㄱ["섹터코드"] for ㄱ in 성적들] == ["BATT", "SEMI"]


def test_섹터_매핑에_없는_종목은_뺀다():
    """섹터에서 빠진 뒤 남은 보유분이 청산되면 매핑에 없을 수 있다.
    조용히 버리지 않으면 '기타' 같은 가짜 섹터가 생긴다."""
    거래들 = [_거래("999999", 5.0), _거래("000010", 3.0)]
    성적들 = 섹터별성적계산(거래들, 섹터표, 섹터이름표)
    assert len(성적들) == 1
    assert 성적들[0]["섹터코드"] == "SEMI"


def test_거래가_적으면_표본부족으로_적는다():
    거래들 = [_거래("000010", 1.0)] * (섹터별성적_최소표본 - 1)
    성적들 = 섹터별성적계산(거래들, 섹터표, 섹터이름표)
    assert 성적들[0]["표본충분"] is False

    거래들 = [_거래("000010", 1.0)] * 섹터별성적_최소표본
    성적들 = 섹터별성적계산(거래들, 섹터표, 섹터이름표)
    assert 성적들[0]["표본충분"] is True


def test_거래가_없으면_한_줄만_내고_섹터칸이_빈다():
    줄들 = 섹터별성적시트줄([], "volume_surge_3d", date(2026, 9, 5),
                     datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001
    assert len(줄들) == 1
    줄 = dict(zip(섹터별성적머리, 줄들[0], strict=True))
    assert 줄["전략키"] == "volume_surge_3d"
    assert 줄["섹터코드"] == ""


def test_섹터마다_한_줄씩_내고_열쇠가_다르다():
    성적들 = 섹터별성적계산(
        [_거래("000010", 5.0), _거래("000030", 10.0)], 섹터표, 섹터이름표)
    줄들 = 섹터별성적시트줄(성적들, "volume_surge_3d", date(2026, 9, 5),
                     datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001

    assert len(줄들) == 2
    열쇠들 = [줄[0] for 줄 in 줄들]
    assert len(열쇠들) == len(set(열쇠들))

    첫줄 = dict(zip(섹터별성적머리, 줄들[0], strict=True))
    assert 첫줄["섹터코드"] == "BATT"
    assert 첫줄["평균수익률"] == "10.00"
