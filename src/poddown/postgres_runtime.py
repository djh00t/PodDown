"""Explicit PostgreSQL connection and migration bootstrap boundaries."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from poddown.postgres_persistence import PostgresPersistenceConnection
from poddown.postgres_schema import MigrationConnection, MigrationRunner


class PostgresConnector(Protocol):
    """Callable boundary for the optional psycopg driver."""

    def __call__(self, dsn: str) -> PostgresPersistenceConnection:
        """Open one non-autocommit PostgreSQL connection."""


ConnectionFactory = Callable[[], PostgresPersistenceConnection]


def _default_connector(dsn: str) -> PostgresPersistenceConnection:
    """Load psycopg lazily so offline/local contract paths stay dependency-safe."""
    try:
        module = import_module("psycopg")
    except ImportError as error:  # pragma: no cover - environment-dependent
        raise RuntimeError("psycopg is required for the PostgreSQL runtime") from error
    connect = getattr(module, "connect", None)
    if not callable(connect):
        raise RuntimeError("psycopg.connect is unavailable")
    return cast(
        PostgresPersistenceConnection,
        connect(dsn, autocommit=False),
    )


def postgres_connection_factory(
    dsn: str,
    *,
    connector: PostgresConnector | None = None,
) -> ConnectionFactory:
    """Return a secret-free factory for short-lived tenant-scoped connections."""
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError("PostgreSQL DSN must be non-empty")
    connect = connector or _default_connector
    normalized = dsn.strip()

    def factory() -> PostgresPersistenceConnection:
        return connect(normalized)

    return factory


def initialize_postgres(connection_factory: ConnectionFactory) -> tuple[int, ...]:
    """Apply forward-only migrations before the API serves PostgreSQL traffic."""
    connection = connection_factory()
    try:
        return MigrationRunner().apply(cast(MigrationConnection, connection))
    finally:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


__all__ = [
    "ConnectionFactory",
    "PostgresConnector",
    "initialize_postgres",
    "postgres_connection_factory",
]
