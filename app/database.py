"""Engine / session factory. Everything is synchronous SQLAlchemy 2.0 style."""
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(url: str, **kwargs) -> Engine:
    """Build an engine. SQLite (tests only) needs two tweaks: allow use from several
    threads, and turn foreign-key enforcement on (it is OFF by default in SQLite)."""
    if url.startswith("sqlite"):
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


_s = get_settings()
_pool = {} if _s.database_url.startswith("sqlite") else {"pool_size": _s.db_pool_size, "max_overflow": _s.db_max_overflow}
engine = make_engine(_s.database_url, pool_pre_ping=True, **_pool)
# expire_on_commit=False: objects stay readable after commit without an extra SELECT.
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
