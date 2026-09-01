from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from muwon.db.models import Base


def make_session_factory(database_url: str) -> sessionmaker:
    engine = create_engine(database_url, **_engine_options(database_url))
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)
    return sessionmaker(bind=engine, class_=Session)


def ensure_schema(database_url: str) -> None:
    """**지금 그 자리에 있는** DB 파일에 빠진 테이블·컬럼을 채운다.

    왜 세션 팩토리와 따로 필요한가. 대시보드는 30초마다 구글드라이브에서
    muwon.db를 다시 받아 **통째로 갈아 끼운다**. 그러면 시작할 때 한 번
    맞춰 둔 스키마가 매번 옛 파일로 되돌아간다. 팩토리는 캐시돼 있어서
    다시 만들지 않으므로 스키마를 맞출 기회가 영영 없다.

    실제로 이렇게 죽었다. orders에 나중에 추가한 컬럼(reference_price,
    fill_confirmed)이 없는 파일이 올라오자 첫 화면의 조회가 통째로
    OperationalError로 터졌고, Streamlit은 원인을 가린 채 빈 화면만 보였다.

    내려받은 **직후마다** 부르면 된다. 이미 맞는 파일이면 아무것도 안 한다."""
    engine = create_engine(database_url, **_engine_options(database_url))
    try:
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
    finally:
        engine.dispose()


def _engine_options(database_url: str) -> dict:
    """SQLite 파일 DB는 연결을 재사용하지 않는다.

    대시보드는 30초마다 구글드라이브에서 muwon.db를 다시 받아 **원자적
    교체**(os.replace)로 갈아 끼운다. 그러면 파일 이름은 같지만 실체가
    바뀌는데, 연결을 풀에 담아 두면 그 연결은 **사라진 옛 파일**을 계속
    붙들고 있다. 읽기는 옛 내용을 조용히 돌려주고, 쓰기는 커밋 순간
    OperationalError로 터진다. 실제로 대시보드에서 자동매매 스위치를 끄자
    그렇게 죽었다.

    NullPool은 세션마다 경로로 새로 연다. 그래서 항상 '지금 그 자리에 있는
    파일'을 본다. 이 앱의 쓰기 빈도(사람이 스위치를 누르는 정도)에서 연결을
    다시 여는 비용은 문제가 되지 않는다."""
    if not database_url.startswith("sqlite"):
        return {}
    if ":memory:" in database_url:
        # 메모리 DB는 연결이 끊기면 내용도 사라진다. 풀을 없애면 안 된다.
        return {}
    return {"poolclass": NullPool}


def _add_missing_columns(engine) -> None:
    """create_all()은 없는 테이블만 새로 만들 뿐, 이미 존재하는 테이블에
    나중에 추가된 컬럼은 반영하지 않는다. 이 프로젝트엔 Alembic 같은 별도
    마이그레이션 도구가 없으므로, 최소한 '컬럼 추가'만이라도 자동으로 따라가게
    해서 실거래 데이터가 쌓인 운영 DB가 스키마 변경 한 번에 깨지는 걸 막는다.
    컬럼 삭제·이름 변경·타입 변경처럼 더 복잡한 변경은 다루지 않는다. 그런
    변경이 필요해지면 Alembic 도입을 검토할 것."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                ddl_type = column.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}'))
