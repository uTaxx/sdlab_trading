"""2단계(비슷했던 과거 찾기)가 지켜야 하는 것을 고정한다.

**네트워크에 나가지 않는다.** 시세를 손으로 만들어 넣는다.

여기서 지키는 것은 정확도가 아니라 규율이다. "얼마나 잘 맞히나"는 시험으로
고정할 수 없지만, 미래를 보지 않는 것과 표본 수를 감추지 않는 것은 고정할
수 있고 그것이 틀리면 결과 전체가 뜻을 잃는다.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from muwon.analysis import similar_window as 이단계


def _시세(날수: int = 900, 종목수: int = 3, 씨앗: int = 7) -> dict[str, pd.DataFrame]:
    """거래일이 이어지는 가짜 일봉. 주말은 건너뛴다."""
    무작위 = np.random.default_rng(씨앗)
    날들 = []
    ㅇ = date(2021, 1, 4)
    while len(날들) < 날수:
        if ㅇ.weekday() < 5:
            날들.append(ㅇ)
        ㅇ += timedelta(days=1)
    표들 = {}
    for i in range(종목수):
        걸음 = 무작위.normal(0.0005, 0.02, 날수)
        종가 = 10000 * np.exp(np.cumsum(걸음))
        표들[f"{i:06d}"] = pd.DataFrame({
            "trade_date": 날들,
            "open": 종가 * 0.998,
            "high": 종가 * 1.01,
            "low": 종가 * 0.99,
            "close": 종가,
            "volume": 무작위.integers(10_000, 200_000, 날수).astype(float),
        })
    return 표들


def _수급(시세: dict[str, pd.DataFrame], 씨앗: int = 3) -> dict[str, pd.DataFrame]:
    무작위 = np.random.default_rng(씨앗)
    나온것 = {}
    for 코드, df in 시세.items():
        자리 = pd.to_datetime(df["trade_date"])
        나온것[코드] = pd.DataFrame({
            "외국인순매매": 무작위.normal(0, 30_000, len(df)),
            "기관순매매": 무작위.normal(0, 30_000, len(df)),
            "외국인보유율": 40.0,
            "종가": df["close"].to_numpy(),
            "거래량": df["volume"].to_numpy(),
        }, index=자리)
    return 나온것


# ── 미래를 보지 않는다 ────────────────────────────────────────


def test_이후_구간이_안_끝난_날은_후보에서_뺀다():
    """대표일 다음 20거래일이 오늘까지 다 지나가 있어야 한다.

    안 빼면 아직 끝나지 않은 구간의 성적을 아는 척하게 된다."""
    시세 = _시세()
    구간들, 지금, 사유, _ = 이단계.비슷한구간찾기(시세, _수급(시세))
    assert 지금 is not None, 사유
    마지막날 = max(d for df in 시세.values() for d in df["trade_date"])
    거래일 = sorted({d for df in 시세.values() for d in df["trade_date"]})
    자를자리 = 거래일[-(이단계.지평 + 이단계.구간길이)]
    for ㄱ in 구간들:
        assert ㄱ.대표일 <= 자를자리, (
            f"{ㄱ.대표일}은 이후 {이단계.지평}거래일이 아직 안 끝났습니다 "
            f"(마지막 {마지막날})")


def test_z점수가_그_시점까지의_자료로만_난다():
    """전체 기간 평균으로 z를 내면 오늘의 평균이 2021년 날짜의 z에 섞인다.

    뒤에 자료를 더 붙여도 앞쪽 z가 안 바뀌어야 한다."""
    시세 = _시세(날수=800)
    표 = 이단계.특징표(시세)
    앞 = 이단계.z로바꾸기(표.iloc[:600])
    전체 = 이단계.z로바꾸기(표)
    같이볼자리 = 앞.dropna().index[-5:]
    pd.testing.assert_frame_equal(
        앞.loc[같이볼자리], 전체.loc[같이볼자리], check_exact=False, atol=1e-9)


# ── 겹치는 날은 하나로 센다 ───────────────────────────────────


def test_한_구간에서_하루만_대표로_쓴다():
    """연속된 날은 같은 사건이다. 안 묶으면 표본이 스무 배로 보인다."""
    시세 = _시세()
    구간들, _, _, _ = 이단계.비슷한구간찾기(시세, _수급(시세))
    대표일들 = [ㄱ.대표일 for ㄱ in 구간들]
    assert len(대표일들) == len(set(대표일들))
    for ㄱ in 구간들:
        assert ㄱ.시작 <= ㄱ.대표일 <= ㄱ.끝
    # 묶인 날이 여럿인 구간이 하나라도 있어야 묶기가 실제로 동작한 것이다.
    assert any(ㄱ.묶인일수 > 1 for ㄱ in 구간들)


# ── 표본 수를 감추지 않는다 ───────────────────────────────────


def test_구간이_적어도_숫자를_내되_몇_개인지_적는다():
    """2026-09-06에 주인이 정했다. 숨기지 말고 개수를 같이 보여 준다."""
    결과 = 이단계.찾은것(
        기준일=date(2026, 9, 4), 지금=None,
        구간들=[이단계.비슷한구간(date(2022, 1, 3), date(2022, 1, 3),
                          date(2022, 1, 4), 0.3, 2)],
        순위=[이단계.전략성적("x", 1, 1.0, 1.0, 1.0, 1, 5)],
    )
    assert 결과.낼수있나, "구간이 적다고 숫자를 감추면 안 됩니다"
    assert not 결과.표본충분
    말 = 결과.표본글()
    assert "1개" in 말
    assert "우연일 수 있습니다" in 말


def test_구간이_충분하면_모자란다고_안_적는다():
    구간들 = [이단계.비슷한구간(date(2022, 1, 3 + i), date(2022, 1, 3 + i),
                        date(2022, 1, 3 + i), 0.3, 1)
           for i in range(이단계.최소구간)]
    결과 = 이단계.찾은것(기준일=date(2026, 9, 4), 지금=None, 구간들=구간들)
    assert 결과.표본충분
    assert "못 미칩니다" not in 결과.표본글()


def test_구간을_못_찾으면_그렇다고_적는다():
    결과 = 이단계.찾은것(기준일=date(2026, 9, 4), 지금=None)
    assert not 결과.낼수있나
    assert "찾지 못했습니다" in 결과.표본글()


# ── 못 잰 것을 0으로 채우지 않는다 ────────────────────────────


def test_한_구간도_못_잰_전략은_순위에_안_넣는다():
    """0%로 채우면 '안 움직였다'로 읽힌다. 계산을 못 한 것과 다르다."""
    성적 = 이단계.구간에서재기(
        구간들=[이단계.비슷한구간(date(2030, 1, 1), date(2030, 1, 1),
                         date(2030, 1, 1), 0.1, 1)],
        전략들={"없는것": (lambda: (_ for _ in ()).throw(RuntimeError("못 만듦")))},
        histories=_시세(날수=300),
        정책=이단계.RiskPolicy(),
    )
    assert 성적 == []


def test_거래가_한_건도_없던_전략은_순위에서_뺀다():
    """수익률 0%로 위에 오지만 지킨 것이 아니라 아무것도 안 한 것이다."""
    from muwon.strategy.registry import build_strategy

    시세 = _시세(날수=400)
    구간 = 이단계.비슷한구간(
        시세["000000"]["trade_date"].iloc[300],
        시세["000000"]["trade_date"].iloc[300],
        시세["000000"]["trade_date"].iloc[300], 0.1, 1)
    성적 = 이단계.구간에서재기(
        [구간],
        {"rsi_reversion": (lambda: build_strategy("rsi_reversion"))},
        시세, 이단계.RiskPolicy(max_holding_days=5))
    for ㄱ in 성적:
        assert ㄱ.거래합 > 0


# ── 순위 기준 ────────────────────────────────────────────────


def test_가장_나빴던_구간을_먼저_본다():
    """CLAUDE.md §4의 1순위 판단 기준과 같아야 한다. 평균이 아무리 높아도
    한 번 크게 잃으면 그 뒤가 없다."""
    from muwon.strategy.registry import build_strategy

    시세 = _시세(날수=500)
    날들 = sorted(시세["000000"]["trade_date"])
    구간들 = [이단계.비슷한구간(날들[i], 날들[i], 날들[i], 0.1, 1)
           for i in (300, 340, 380)]
    키들 = ["volume_surge_3d", "macd_cross", "ma_rsi_v1"]
    성적 = 이단계.구간에서재기(
        구간들, {ㅋ: (lambda k=ㅋ: build_strategy(k)) for ㅋ in 키들},
        시세, 이단계.RiskPolicy(max_holding_days=5))
    if len(성적) < 2:
        pytest.skip("가짜 시세에서 매수가 안 나 순서를 못 봅니다")
    최악들 = [ㄱ.최악 for ㄱ in 성적]
    assert 최악들 == sorted(최악들, reverse=True)


# ── 외국인 자료가 없어도 돈다 ─────────────────────────────────


def test_외국인_자료가_없으면_셋으로_잰다():
    """수급을 못 받은 날에 전체가 멈추면 안 된다. 대신 무엇으로 쟀는지를
    돌려줘서, 셋으로 잰 결과를 넷으로 잰 것으로 읽지 않게 한다."""
    시세 = _시세()
    _, 지금, 사유, 쓴것 = 이단계.비슷한구간찾기(시세, 수급=None)
    assert 지금 is not None, 사유
    assert 지금.외국인비 is None
    assert "외국인비" not in 쓴것
    assert set(쓴것) == {"등락률", "변동성", "거래량비"}
    assert "없음" in 지금.한줄()


def test_외국인_자료가_있으면_넷으로_잰다():
    시세 = _시세()
    _, 지금, 사유, 쓴것 = 이단계.비슷한구간찾기(시세, _수급(시세))
    assert 지금 is not None, 사유
    assert 지금.외국인비 is not None
    assert set(쓴것) == set(이단계.특징이름)


# ── 이것으로 매매를 바꾸지 않는다 ─────────────────────────────


def test_전략_코드가_이_모듈을_안_부른다():
    """이 모듈은 후보를 보여 줄 뿐이다. 전략이 이걸 부르면 순위를 내는
    자리와 매매하는 자리가 섞이고, 그때부터 '이 순위로 매매가 바뀌지
    않습니다'가 거짓말이 된다. `market/regime`과 같은 규칙이다."""
    import pathlib

    뿌리 = pathlib.Path(__file__).resolve().parent.parent / "src" / "muwon"
    for 파일 in (뿌리 / "strategy").rglob("*.py"):
        글 = 파일.read_text(encoding="utf-8")
        assert "similar_window" not in 글, f"{파일.name}이 2단계를 부릅니다"
