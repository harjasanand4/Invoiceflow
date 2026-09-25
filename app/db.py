"""Database engine and session setup (SQLAlchemy 2.0)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


Session = scoped_session(sessionmaker(expire_on_commit=False))
_engine: Engine | None = None


def init_engine(url: str) -> Engine:
    """Create the engine and bind the session factory to it."""
    global _engine
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        db_path = url.replace("sqlite:///", "", 1)
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, **kwargs)
    Session.remove()
    Session.configure(bind=_engine)
    return _engine


def create_tables() -> None:
    from app import models  # noqa: F401  (registers the models on Base)

    if _engine is None:
        raise RuntimeError("init_engine() must be called first")
    Base.metadata.create_all(_engine)


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("init_engine() must be called first")
    return _engine
