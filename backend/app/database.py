"""SQLAlchemy engine, session factory and declarative base."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

#: Seconds to wait for PostgreSQL to accept a connection, per address tried.
#: Without it a database that is down holds a connection attempt for the
#: operating system's own limit -- over four minutes on Windows -- so the
#: API's start-up check (`app.media_check`) and every request would wait that
#: long before saying anything.
CONNECT_TIMEOUT_SECONDS = 10

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """Declarative base. Importing app.models registers every table on it."""

    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
