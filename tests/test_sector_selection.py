"""1차 섹터 고르기.

**개수를 채우려고 약한 섹터를 넣지 않는가**가 여기서 제일 중요한 시험이다.
전부 시장보다 못한 날에 셋을 억지로 고르면, 그게 곧 '아무 때나 산다'다."""

from datetime import date, timedelta

import pandas as pd

from muwon.sector.selection import format_ranking, pick, rank


def 선(값들, 시작=date(2026, 1, 1)):
    날 = [시작 + timedelta(days=i) for i in range(len(값들))]
    return pd.Series(값들, index=날, dtype=float)


이름표 = {"SEMI": "반도체", "BIO": "바이오", "AUTO": "자동차"}


def _평평한(길이=30, 값=100.0):
    return 선([값] * 길이)


def test_시장보다_많이_간_섹터가_위로():
    시장 = 선([100.0] * 21 + [110.0])
    지수 = {
        "SEMI": 선([100.0] * 21 + [130.0]),   # 시장보다 더 감
        "BIO": 선([100.0] * 21 + [105.0]),    # 시장보다 덜 감
    }
    순위 = rank(지수, 이름표, 시장, lookback=20)
    assert [p.코드 for p in 순위] == ["SEMI", "BIO"]
    assert 순위[0].상대강도 > 순위[1].상대강도


def test_전부_시장보다_못하면_아무것도_안_고른다():
    """개수를 채우려고 약한 섹터를 넣으면 '아무 때나 산다'와 같아진다."""
    시장 = 선([100.0] * 21 + [130.0])
    지수 = {코드: 선([100.0] * 21 + [110.0]) for 코드 in 이름표}
    결과 = pick(rank(지수, 이름표, 시장, lookback=20))
    assert [p for p in 결과 if p.뽑힘] == []
    assert "안 삽니다" in format_ranking(결과)


def test_상위_몇개까지만_고른다():
    시장 = _평평한(22)
    지수 = {
        "SEMI": 선([100.0] * 21 + [130.0]),
        "BIO": 선([100.0] * 21 + [120.0]),
        "AUTO": 선([100.0] * 21 + [110.0]),
    }
    결과 = pick(rank(지수, 이름표, 시장, lookback=20), top_n=2)
    뽑힌것 = [p.코드 for p in 결과 if p.뽑힘]
    assert 뽑힌것 == ["SEMI", "BIO"]
    assert next(p for p in 결과 if p.코드 == "AUTO").사유 == "자리가 찼습니다"


def test_자료가_짧으면_못_잰다고_말한다():
    """0으로 채우면 '보통'인 척하게 된다. 모르는 것은 모른다고 둔다."""
    시장 = _평평한(22)
    지수 = {"SEMI": 선([100.0] * 5)}
    순위 = rank(지수, 이름표, 시장, lookback=20)
    assert 순위[0].상대강도 is None
    assert "짧습니다" in 순위[0].사유
    assert [p for p in pick(순위) if p.뽑힘] == []


def test_못_잰_섹터는_언제나_뒤로():
    시장 = _평평한(22)
    지수 = {"SEMI": 선([100.0] * 5), "BIO": 선([100.0] * 21 + [110.0])}
    순위 = rank(지수, 이름표, 시장, lookback=20)
    assert [p.코드 for p in 순위] == ["BIO", "SEMI"]


def test_기준일_이후는_안_본다():
    """이걸 어기면 되돌려 검증이 통째로 거짓말이 된다."""
    시장 = _평평한(40)
    # 30일째까지는 평평하다가 그 뒤에 급등: 기준일을 30일째로 두면 안 보여야 한다
    지수 = {"SEMI": 선([100.0] * 30 + [200.0] * 10)}
    기준 = date(2026, 1, 1) + timedelta(days=29)
    순위 = rank(지수, 이름표, 시장, 기준일=기준, lookback=20)
    assert 순위[0].상대강도 == 0.0


def test_최소강도를_올리면_더_까다로워진다():
    시장 = _평평한(22)
    지수 = {"SEMI": 선([100.0] * 21 + [103.0])}  # +3%p
    assert [p for p in pick(rank(지수, 이름표, 시장, lookback=20), 최소강도=5.0) if p.뽑힘] == []
    assert len([p for p in pick(rank(지수, 이름표, 시장, lookback=20), 최소강도=2.0) if p.뽑힘]) == 1


class 가짜후보:
    def __init__(self, symbol, sector):
        self.symbol, self.sector = symbol, sector

    def __repr__(self):
        return f"{self.symbol}/{self.sector}"


def test_한_섹터에서_상한만큼만_남긴다():
    """반도체 다섯 종목을 사면 분산이 아니라 반도체 하나에 다섯 배로 건 것이다."""
    from muwon.sector.selection import cap_per_sector

    후보 = [가짜후보(f"S{i}", "SEMI") for i in range(5)] + [가짜후보("B1", "BIO")]
    남김, 밀림 = cap_per_sector(후보, 상한=2)
    assert [c.symbol for c in 남김] == ["S0", "S1", "B1"]
    assert [c.symbol for c in 밀림] == ["S2", "S3", "S4"]


def test_상한은_섹터마다_따로_센다():
    from muwon.sector.selection import cap_per_sector

    후보 = [가짜후보("S1", "SEMI"), 가짜후보("B1", "BIO"),
            가짜후보("S2", "SEMI"), 가짜후보("B2", "BIO")]
    남김, 밀림 = cap_per_sector(후보, 상한=2)
    assert len(남김) == 4 and 밀림 == []


def test_밀려난것도_돌려준다():
    """왜 안 샀는지가 왜 샀는지만큼 중요하다."""
    from muwon.sector.selection import cap_per_sector

    남김, 밀림 = cap_per_sector([가짜후보("A", "X"), 가짜후보("B", "X")], 상한=1)
    assert len(남김) == 1 and len(밀림) == 1


def test_기본값이_기준표와_어긋나지_않는다():
    """두 군데에 기본값을 적어 두면 하나만 고치고 다른 하나를 잊는다."""
    from muwon.sector.selection import LOOKBACK, MAX_PER_SECTOR, TOP_N
    from muwon.settings.from_sheet import 기준표

    assert str(LOOKBACK) == 기준표["sector_lookback"].기본
    assert str(TOP_N) == 기준표["sector_top_n"].기본
    assert str(MAX_PER_SECTOR) == 기준표["max_per_sector"].기본
