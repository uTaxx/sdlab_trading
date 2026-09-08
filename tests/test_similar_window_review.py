"""검토 2단계: 비슷한 구간을 시트 줄로 바꾸는 함수.

## 왜 따로 시험하나

`비슷한구간남기기`는 시세를 받고 시트에 쓰는 I/O 함수라 빠르게 반복
시험하기 어렵다. 줄을 만드는 부분만 순수 함수(`비슷한구간시트줄`)로
떼어 뒀고, 여기서는 그 함수만 본다.

**칸이 밀리면 조용히 어긋난다.** 순위 표의 중앙값 칸에 최악 값이 들어가도
숫자라서 화면은 멀쩡히 그려진다. n8n 창구 사본과 칸 번호가 맞는지는
`test_n8n_gateway_fields.py`가 본다.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "run_strategy_review.py"
_스펙 = importlib.util.spec_from_file_location("run_strategy_review_for_similar_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["run_strategy_review_for_similar_test"] = _모듈
_스펙.loader.exec_module(_모듈)

비슷한구간시트줄 = _모듈.비슷한구간시트줄
비슷한구간머리 = _모듈.비슷한구간머리

from muwon.analysis.similar_window import 상태, 전략성적, 찾은것


def _상태(끝일=date(2026, 9, 5)):
    return 상태(끝일=끝일, 등락률=3.2, 변동성=1.8, 거래량비=1.4, 외국인비=0.5)


def test_전략이_없으면_한_줄만_내고_사유를_담는다():
    찾은것것 = 찾은것(기준일=date(2026, 9, 5), 지금=_상태(), 사유="비교할 과거가 짧습니다.")
    줄들 = 비슷한구간시트줄(찾은것것, date(2026, 9, 5), datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001

    assert len(줄들) == 1
    줄 = dict(zip(비슷한구간머리, 줄들[0], strict=True))
    assert 줄["사유"] == "비교할 과거가 짧습니다."
    assert 줄["전략키"] == ""
    assert 줄["순위"] == ""
    assert "등락률" in 줄["상태글"]


def test_전략마다_한_줄씩_순위대로_낸다():
    성적들 = [
        전략성적(키="volume_surge_3d", 구간수=10, 중앙값=5.0, 최악=-3.0, 최고=12.0,
               이긴구간=7, 거래합=40),
        전략성적(키="ma_rsi_v1", 구간수=10, 중앙값=2.0, 최악=-8.0, 최고=9.0,
               이긴구간=5, 거래합=30),
    ]
    찾은것것 = 찾은것(
        기준일=date(2026, 9, 5), 지금=_상태(), 순위=성적들,
    )
    줄들 = 비슷한구간시트줄(찾은것것, date(2026, 9, 5), datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001

    assert len(줄들) == 2
    첫줄 = dict(zip(비슷한구간머리, 줄들[0], strict=True))
    둘줄 = dict(zip(비슷한구간머리, 줄들[1], strict=True))

    assert 첫줄["전략키"] == "volume_surge_3d"
    assert 첫줄["순위"] == "1"
    assert 첫줄["전략중앙값"] == "5.00"
    assert 첫줄["전략최악"] == "-3.00"

    assert 둘줄["전략키"] == "ma_rsi_v1"
    assert 둘줄["순위"] == "2"

    # 구간 정보(기준일·상태글·표본글)는 두 줄이 같다. 한 번 계산한 것이다.
    assert 첫줄["기준일"] == 둘줄["기준일"] == "2026-09-05"


def test_열쇠가_전략마다_다르다():
    """다르지 않으면 sheet_log.append가 두 번째 줄을 '이미 있는 열쇠'로
    보고 빼 버린다. 순위표가 한 줄만 남는다."""
    성적들 = [
        전략성적(키="a", 구간수=10, 중앙값=1.0, 최악=-1.0, 최고=1.0, 이긴구간=5, 거래합=10),
        전략성적(키="b", 구간수=10, 중앙값=1.0, 최악=-1.0, 최고=1.0, 이긴구간=5, 거래합=10),
    ]
    찾은것것 = 찾은것(기준일=date(2026, 9, 5), 지금=_상태(), 순위=성적들)
    줄들 = 비슷한구간시트줄(찾은것것, date(2026, 9, 5), datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001
    열쇠들 = [줄[0] for 줄 in 줄들]
    assert len(열쇠들) == len(set(열쇠들))


def test_지금_상태를_못_구했으면_상태글이_빈다():
    """구간을 못 찾은 것과 시세 자체를 못 받은 것은 다르다. 상태글이
    비어 있으면 후자다."""
    찾은것것 = 찾은것(기준일=date(2026, 9, 5), 지금=None, 사유="시세를 못 받아 상태를 재지 못했습니다.")
    줄들 = 비슷한구간시트줄(찾은것것, date(2026, 9, 5), datetime(2026, 9, 5, 17, 50))  # noqa: DTZ001
    줄 = dict(zip(비슷한구간머리, 줄들[0], strict=True))
    assert 줄["상태글"] == ""
    assert "시세를 못 받아" in 줄["사유"]
