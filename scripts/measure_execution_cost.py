"""실제 주문 기록에서 '결정가 대비 체결가'를 재서 보고한다.

백테스트의 슬리피지 값을 추측으로 정하지 않기 위한 도구다. 지금까지는
0.05%인지 0.2%인지 근거가 없었다. 실제 주문이 쌓이면 여기서 답이 나온다.

사용 예:
    python scripts/measure_execution_cost.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from muwon.analysis.execution_cost import collect, format_report
from muwon.config import bootstrap_settings
from muwon.db.session import make_session_factory


def main() -> None:
    session_factory = make_session_factory(bootstrap_settings.database_url)
    print(format_report(collect(session_factory)))


if __name__ == "__main__":
    main()
