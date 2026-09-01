"""create_all()은 없는 테이블만 새로 만들고 기존 테이블의 새 컬럼은
반영하지 않는다. make_session_factory가 이걸 보정하는지 확인한다.
운영 DB에 실거래 기록이 쌓인 뒤 코드에 컬럼이 하나 추가되는 상황을
그대로 재현한다."""

import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from muwon.db.session import make_session_factory


def test_missing_column_on_existing_table_is_added():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.db"
        url = f"sqlite:///{db_path}"

        # "예전 버전" 스키마: reason 컬럼이 없는 orders 테이블을 먼저 만들어 둔다.
        old_engine = create_engine(url)
        with old_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE orders ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "symbol VARCHAR(10), side VARCHAR(4), quantity INTEGER, "
                    "price FLOAT, is_paper BOOLEAN, kis_order_id VARCHAR(50), "
                    "created_at DATETIME)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO orders (symbol, side, quantity, price, is_paper, kis_order_id) "
                    "VALUES ('005930', 'buy', 10, 71000.0, 1, 'OLD-1')"
                )
            )
        old_engine.dispose()

        session_factory = make_session_factory(url)

        with session_factory() as session:
            columns = {c["name"] for c in inspect(session.get_bind()).get_columns("orders")}
            assert "reason" in columns

            row = session.execute(text("SELECT symbol, reason FROM orders")).first()
            assert row.symbol == "005930"
            assert row.reason is None  # 기존 행은 새 컬럼이 NULL로 채워짐
