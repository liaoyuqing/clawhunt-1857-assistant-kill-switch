"""Database access: engine factory, session factory and an instrumentation counter.

The counter is what makes acceptance criterion 1 checkable: while the emergency
brake is on, the assertion is that the process opened zero database sessions and
ran zero statements, not merely that the answer looked right.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import Settings


class Base(DeclarativeBase):
    """Declarative root for every table of this service."""


class DatabaseStats:
    """Mutable counters shared by the engine listener and the test assertions."""

    def __init__(self) -> None:
        self.connects = 0
        self.statements = 0
        self.sessions = 0

    def reset(self) -> None:
        self.connects = 0
        self.statements = 0
        self.sessions = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "connects": self.connects,
            "statements": self.statements,
            "sessions": self.sessions,
        }

    def total(self) -> int:
        return self.connects + self.statements + self.sessions


def build_engine(settings: Settings, stats: DatabaseStats) -> Engine:
    """Create the engine and attach the counters.

    `connect` fires on every physical checkout, `before_cursor_execute` on every
    statement. Both are pure observers: they never touch the database themselves.
    """

    kwargs: dict[str, Any] = {"pool_pre_ping": False, "future": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # NullPool keeps the connect counter honest: every checkout is a real one.
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"connect_timeout": 5}
    engine = create_engine(settings.database_url, **kwargs)

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ANN401, ARG001
        stats.connects += 1

    @event.listens_for(engine, "before_cursor_execute")
    def _on_statement(
        conn: Any,  # noqa: ANN401
        cursor: Any,  # noqa: ANN401
        statement: str,
        parameters: Any,  # noqa: ANN401
        context: Any,  # noqa: ANN401
        executemany: bool,
    ) -> None:
        stats.statements += 1

    return engine


class Database:
    """Owns the engine and hands out short-lived sessions."""

    def __init__(self, settings: Settings, stats: DatabaseStats | None = None) -> None:
        self.settings = settings
        self.stats = stats or DatabaseStats()
        self.engine = build_engine(settings, self.stats)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def session(self) -> Session:
        self.stats.sessions += 1
        return self._session_factory()

    @contextmanager
    def read(self) -> Iterator[Session]:
        session = self.session()
        try:
            yield session
        finally:
            session.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()


def make_sqlite_url(path: str) -> str:
    """Absolute sqlite URL helper used by the operator scripts and tests."""

    return "sqlite+pysqlite:///" + os.path.abspath(path).replace(os.sep, "/")
