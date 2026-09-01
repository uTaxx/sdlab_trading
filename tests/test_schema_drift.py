"""구글드라이브에서 받아 온 DB가 옛 스키마여도 화면이 살아 있어야 한다.

대시보드는 30초마다 muwon.db를 통째로 갈아 끼운다. 시작할 때 맞춰 둔
스키마가 매번 옛 파일로 되돌아가는데, 세션 팩토리는 캐시돼 있어서 다시
맞출 기회가 없다. 실제로 orders에 나중에 추가한 컬럼이 없는 파일이
올라와서 첫 화면이 통째로 죽었다."""

import pytest
import sqlalchemy as sa

from muwon.db.models import Base, OrderRow
from muwon.db.session import ensure_schema, make_session_factory


def _옛디비만들기(path) -> None:
    """reference_price / fill_confirmed가 없던 시절의 orders 테이블."""
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE orders ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, symbol VARCHAR(10), side VARCHAR(4), "
                "quantity INTEGER, price FLOAT, is_paper BOOLEAN, kis_order_id VARCHAR(50), "
                "reason VARCHAR(100), created_at DATETIME)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO orders (symbol, side, quantity, price, is_paper, "
                "kis_order_id, reason, created_at) "
                "VALUES ('005930', 'BUY', 1, 100.0, 1, '', '', '2026-08-19 00:00:00')"
            )
        )
    engine.dispose()


def test_an_old_file_breaks_the_query_before_the_fix(tmp_path):
    """이게 실제로 터진 방식이다. 이 테스트가 통과해야 아래 고침이 의미가 있다."""
    db = tmp_path / "옛것.db"
    _옛디비만들기(db)
    engine = sa.create_engine(f"sqlite:///{db}")
    with sa.orm.Session(engine) as session, pytest.raises(sa.exc.OperationalError):
        session.query(OrderRow).first()
    engine.dispose()


def test_ensure_schema_fixes_a_file_that_was_swapped_in_underneath(tmp_path):
    db = tmp_path / "갈아끼운것.db"
    _옛디비만들기(db)

    ensure_schema(f"sqlite:///{db}")

    engine = sa.create_engine(f"sqlite:///{db}")
    with sa.orm.Session(engine) as session:
        주문 = session.query(OrderRow).first()
    engine.dispose()
    assert 주문 is not None, "기존 행이 사라지면 안 된다. 채워 넣는 것이지 새로 만드는 게 아니다"
    assert 주문.symbol == "005930"
    assert 주문.reference_price is None


def test_ensure_schema_also_creates_tables_that_never_existed(tmp_path):
    """나중에 추가한 테이블(run_logs 등)이 통째로 없는 파일도 온다."""
    db = tmp_path / "테이블없음.db"
    _옛디비만들기(db)
    ensure_schema(f"sqlite:///{db}")
    engine = sa.create_engine(f"sqlite:///{db}")
    있는것 = set(sa.inspect(engine).get_table_names())
    engine.dispose()
    assert {t.name for t in Base.metadata.sorted_tables} <= 있는것


def test_ensure_schema_on_an_already_correct_file_changes_nothing(tmp_path):
    db = tmp_path / "멀쩡한것.db"
    make_session_factory(f"sqlite:///{db}")
    앞 = db.read_bytes()
    ensure_schema(f"sqlite:///{db}")
    assert db.read_bytes() == 앞


def test_the_sync_fixes_the_schema_right_after_downloading(monkeypatch, tmp_path):
    """받아 온 직후에 안 맞추면 맞출 기회가 없다. 팩토리는 캐시돼 있다."""
    from muwon.dashboard import app

    순서: list[str] = []
    monkeypatch.setenv("GDRIVE_SA_KEY_JSON", "{}")
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "folder")
    monkeypatch.setattr(app, "_local_db_path", lambda: str(tmp_path / "muwon.db"))
    monkeypatch.setattr(app, "gdrive_download", lambda *a, **k: 순서.append("내려받기"))
    monkeypatch.setattr(app, "ensure_schema", lambda *a, **k: 순서.append("스키마맞추기"))

    app.sync_db_from_drive()

    assert 순서 == ["내려받기", "스키마맞추기"], f"순서가 틀렸다: {순서}"


def test_a_db_failure_shows_the_real_cause_instead_of_a_blank_red_page(monkeypatch):
    """Streamlit은 안 잡은 예외를 "error message is redacted"로 가린다.
    화면은 통째로 빨간 상자가 되고 남은 탭도 못 본다. 실제로 그 상태로
    한참 헤맸다."""
    from muwon.dashboard import app

    보인것: list[str] = []
    monkeypatch.setattr(app.st, "error", lambda 글, **k: 보인것.append(글))

    with app.db_guard("대시보드 요약"):
        raise sa.exc.OperationalError("SELECT ...", {}, Exception("no such column: orders.x"))

    (글,) = 보인것
    assert "대시보드 요약" in 글, "어느 부분이 실패했는지가 없으면 찾을 수가 없다"
    assert "no such column: orders.x" in 글, "진짜 원인이 안 보이면 잡은 의미가 없다"


def test_the_guard_does_not_swallow_bugs_that_are_not_database_problems(monkeypatch):
    """DB 오류만 잡는다. 코드 버그까지 삼키면 화면은 멀쩡한데 값이 틀린,
    가장 알아채기 어려운 실패가 된다."""
    from muwon.dashboard import app

    monkeypatch.setattr(app.st, "error", lambda *a, **k: None)
    with pytest.raises(ZeroDivisionError), app.db_guard("아무거나"):
        raise ZeroDivisionError("코드 버그")
