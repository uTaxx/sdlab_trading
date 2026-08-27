"""scripts/ 의 함수를 시험에서 부르기 위한 다리.

scripts는 패키지가 아니라 import 경로에 없다. 경로를 시험마다 만지는 대신
여기 한 곳에서만 만진다."""

import importlib.util
import sys
from pathlib import Path

_경로 = Path(__file__).resolve().parent.parent / "scripts" / "export_dashboard_data.py"
_스펙 = importlib.util.spec_from_file_location("export_dashboard_data_for_test", _경로)
_모듈 = importlib.util.module_from_spec(_스펙)
sys.modules["export_dashboard_data_for_test"] = _모듈
_스펙.loader.exec_module(_모듈)

전략설명 = _모듈.전략설명
용어사전 = _모듈.용어사전
