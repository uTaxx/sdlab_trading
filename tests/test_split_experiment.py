"""split 모드의 --keys 해석 시험.

## 왜 이걸 따로 시험하나

여기서 조용히 틀리면 표는 정상으로 나온다. 'A>B|C>D'에서 뒤쪽을 버려도
앞쪽 조합은 멀쩡히 돌고, 표에는 줄 하나가 덜 찍힐 뿐이다. 실험 결과를
읽는 사람은 그 줄이 원래 없었는지 사라졌는지 알 수 없다.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "run_experiment.py"
_스펙 = importlib.util.spec_from_file_location("run_experiment_for_test", _경로)
run_experiment_script = importlib.util.module_from_spec(_스펙)
sys.modules["run_experiment_for_test"] = run_experiment_script
_스펙.loader.exec_module(run_experiment_script)

조합파싱 = run_experiment_script.조합파싱


def test_한_조합을_사는쪽과_파는쪽으로_가른다():
    assert 조합파싱("volume_surge_5d>ma_rsi_v1") == [
        (("volume_surge_5d",), ("ma_rsi_v1",))
    ]


def test_여러_조합은_막대로_나눈다():
    결과 = 조합파싱("a>b|c>d")
    assert 결과 == [(("a",), ("b",)), (("c",), ("d",))]


def test_한쪽에_전략을_여럿_둘_수_있다():
    결과 = 조합파싱("a,b>c,d")
    assert 결과 == [(("a", "b"), ("c", "d"))]


def test_공백은_없는_것으로_친다():
    assert 조합파싱("  a > b  ") == [(("a",), ("b",))]


def test_빈_덩이는_건너뛴다():
    # 'a>b|' 처럼 끝에 막대가 남는 것은 손으로 적을 때 흔하다.
    assert 조합파싱("a>b|") == [(("a",), ("b",))]


def test_화살표가_없으면_멈춘다():
    # 조용히 넘어가면 조합을 안 잰 채로 표가 나온다.
    with pytest.raises(SystemExit, match="'>'가 없습니다"):
        조합파싱("a,b")


def test_한쪽이_비면_멈춘다():
    with pytest.raises(SystemExit, match="하나 이상"):
        조합파싱("a>")
    with pytest.raises(SystemExit, match="하나 이상"):
        조합파싱(">b")


def test_아무것도_안_주면_멈춘다():
    with pytest.raises(SystemExit, match="매수키>매도키"):
        조합파싱("")
    with pytest.raises(SystemExit, match="매수키>매도키"):
        조합파싱("   |  ")
