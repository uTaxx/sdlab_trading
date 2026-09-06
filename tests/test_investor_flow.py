"""외국인·기관 순매매를 받아 오는 자리를 고정한다.

**네트워크에 나가지 않는다.** 네이버가 준 표의 모양만 흉내 낸다. 시험이
남의 서버에 매달리면 그쪽이 느린 날 저장소 전체가 빨개진다.
"""

from datetime import date

import pandas as pd
import pytest

from muwon.data import investor_flow as 흐름


def _가짜쪽(날짜들, 외국인, 기관=None, 보유율="46.71%"):
    """네이버 표와 같은 아홉 칸짜리 표를 만든다."""
    기관 = 기관 if 기관 is not None else [0] * len(날짜들)
    return pd.DataFrame(
        {
            "날짜": 날짜들,
            "종가": [1000] * len(날짜들),
            "전일비": [0] * len(날짜들),
            "등락률": ["0.00%"] * len(날짜들),
            "거래량": [10] * len(날짜들),
            "기관순매매": 기관,
            "외국인순매매": 외국인,
            "외국인보유주수": [100] * len(날짜들),
            "외국인보유율": [보유율] * len(날짜들),
        }
    )


def _붙이기(monkeypatch, 쪽별):
    """쪽 번호 → 표. 없는 쪽을 부르면 빈 표를 준다."""
    부른것 = []

    def 가짜(session, 코드, 쪽):
        부른것.append((코드, 쪽))
        표 = 쪽별.get(쪽)
        if 표 is None:
            return pd.DataFrame()
        조각 = 표.copy()
        조각["날짜"] = pd.to_datetime(조각["날짜"], format="%Y.%m.%d")
        조각 = 조각.set_index("날짜")[
            ["외국인순매매", "기관순매매", "외국인보유율", "종가", "거래량"]
        ]
        보유율 = (
            조각["외국인보유율"].astype(str).str.replace("%", "", regex=False).str.strip()
        )
        return 조각.assign(외국인보유율=pd.to_numeric(보유율)).astype(float)

    monkeypatch.setattr(흐름, "_쪽읽기", 가짜)
    return 부른것


def test_보유율의_백분율_기호를_벗긴다(monkeypatch):
    """실제로 여기서 걸렸다. 안 벗기면 그 종목이 통째로 못 받은 것이 된다."""
    _붙이기(monkeypatch, {1: _가짜쪽(["2026.09.04"], [-115377], 보유율="46.71%")})
    표 = 흐름.한종목받기("005930", date(2026, 9, 1), date(2026, 9, 30), 쉼=0)
    assert 표["외국인보유율"].iloc[0] == pytest.approx(46.71)


def test_시작일에_닿으면_더_안_넘긴다(monkeypatch):
    부른것 = _붙이기(
        monkeypatch,
        {
            1: _가짜쪽(["2026.09.04", "2026.09.03"], [1, 2]),
            2: _가짜쪽(["2026.09.02", "2026.09.01"], [3, 4]),
            3: _가짜쪽(["2026.08.31"], [5]),
        },
    )
    흐름.한종목받기("005930", date(2026, 9, 2), date(2026, 9, 30), 쉼=0)
    # 2쪽에서 시작일에 닿으므로 3쪽은 안 부른다.
    assert [ㅉ for _, ㅉ in 부른것] == [1, 2]


def test_한_종목이_실패해도_나머지는_받는다(monkeypatch, tmp_path):
    def 가짜(session, 코드, 쪽):
        if 코드 == "000660":
            raise ValueError("표 모양이 다릅니다")
        조각 = _가짜쪽(["2026.09.04"], [7])
        조각["날짜"] = pd.to_datetime(조각["날짜"], format="%Y.%m.%d")
        조각 = 조각.set_index("날짜")[
            ["외국인순매매", "기관순매매", "외국인보유율", "종가", "거래량"]
        ]
        return 조각.assign(외국인보유율=46.71).astype(float)

    monkeypatch.setattr(흐름, "_쪽읽기", 가짜)
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    결과 = 흐름.받기(
        ["005930", "000660"], date(2026, 9, 1), date(2026, 9, 30), 캐시=캐시, 쉼=0
    )
    assert "005930" in 결과.자료
    assert "000660" in 결과.못받은것
    assert not 결과.다받았나


def test_못_받은_것을_0으로_채우지_않는다(monkeypatch, tmp_path):
    """못 받은 종목이 순매매 0으로 표에 들어가면 안 된다.

    0은 '그날 사지도 팔지도 않았다'는 뜻이라 '못 받았다'와 전혀 다르다."""

    def 가짜(session, 코드, 쪽):
        raise ValueError("못 받음")

    monkeypatch.setattr(흐름, "_쪽읽기", 가짜)
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    결과 = 흐름.받기(["005930"], date(2026, 9, 1), date(2026, 9, 30), 캐시=캐시, 쉼=0)
    assert 결과.자료 == {}
    assert "005930" in 결과.못받은것


def test_못_받은_것이_한줄에_드러난다(monkeypatch, tmp_path):
    """조용히 성공한 척하지 않는다. 사람이 읽는 줄에 실패가 보여야 한다."""

    def 가짜(session, 코드, 쪽):
        raise ValueError("못 받음")

    monkeypatch.setattr(흐름, "_쪽읽기", 가짜)
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    결과 = 흐름.받기(["005930"], date(2026, 9, 1), date(2026, 9, 30), 캐시=캐시, 쉼=0)
    assert "못 받은 종목" in 결과.한줄()
    assert "005930" in 결과.한줄()


def test_캐시에_있으면_다시_안_받는다(monkeypatch, tmp_path):
    쪽별 = {1: _가짜쪽(["2026.09.04", "2026.09.03"], [1, 2])}
    부른것 = _붙이기(monkeypatch, 쪽별)
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    시작, 끝 = date(2026, 9, 3), date(2026, 9, 30)
    흐름.받기(["005930"], 시작, 끝, 캐시=캐시, 쉼=0)
    받은횟수 = len(부른것)
    결과 = 흐름.받기(["005930"], 시작, 끝, 캐시=캐시, 쉼=0)
    assert len(부른것) == 받은횟수  # 한 번도 더 안 불렀다
    assert 결과.캐시로쓴것 == ["005930"]
    assert not 결과.자료["005930"].empty


def test_캐시가_모자라면_다시_받는다(monkeypatch, tmp_path):
    """캐시에 있는 것보다 이른 날을 물으면 받으러 가야 한다."""
    쪽별 = {
        1: _가짜쪽(["2026.09.04", "2026.09.03"], [1, 2]),
        2: _가짜쪽(["2026.09.02", "2026.09.01"], [3, 4]),
    }
    부른것 = _붙이기(monkeypatch, 쪽별)
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    흐름.받기(["005930"], date(2026, 9, 3), date(2026, 9, 30), 캐시=캐시, 쉼=0)
    받은횟수 = len(부른것)
    흐름.받기(["005930"], date(2026, 9, 1), date(2026, 9, 30), 캐시=캐시, 쉼=0)
    assert len(부른것) > 받은횟수


def test_캐시에_넣고_읽으면_값이_같다(tmp_path):
    캐시 = 흐름.순매매캐시(tmp_path / "flow.sqlite")
    표 = pd.DataFrame(
        {
            "외국인순매매": [-115377.0],
            "기관순매매": [2489812.0],
            "외국인보유율": [46.71],
            "종가": [255500.0],
            "거래량": [14031862.0],
        },
        index=pd.to_datetime(["2026-09-04"]),
    )
    표.index.name = "날짜"
    assert 캐시.쓰기("005930", 표) == 1
    다시 = 캐시.읽기("005930", date(2026, 9, 1), date(2026, 9, 30))
    assert 다시["외국인순매매"].iloc[0] == pytest.approx(-115377.0)
    assert 다시["기관순매매"].iloc[0] == pytest.approx(2489812.0)
