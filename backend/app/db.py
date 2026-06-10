from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        db_path = database_url.removeprefix("sqlite:///")
        if db_path and not db_path.startswith(":memory:"):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def init_engine(database_url: str) -> Engine:
    global _engine, _session_factory
    _engine = make_engine(database_url)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()
