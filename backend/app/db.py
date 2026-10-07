import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

# PostgreSQL is the default for local development; Compose overrides the host
# with the database service name when the backend runs in Docker.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg2://triage:triage@localhost:5432/triage"
)

_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        # One shared connection, otherwise each connection gets its own empty DB.
        _kwargs["poolclass"] = StaticPool
else:
    # Connection pooling for PostgreSQL: reuse connections instead of opening
    # a new one per request. pool_pre_ping drops dead connections safely.
    _kwargs.update(pool_size=5, max_overflow=10, pool_pre_ping=True)

engine = create_engine(DATABASE_URL, **_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
