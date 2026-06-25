"""Neo4j driver wrapper for the ThoughtWire Causal Knowledge Graph.

Wraps the ``neo4j`` 6.x driver behind a small, friendly facade. Uses the 6.x
managed-transaction API (:meth:`Session.execute_read` /
:meth:`Session.execute_write`) and context managers exclusively — the implicit
``__del__`` close removed in 6.0 is never relied upon.

Managed-transaction callbacks are auto-retried on transient failures, so the
work functions here are idempotent and consume their results *inside* the
transaction (``[r.data() for r in tx.run(...)]``) before the result cursor is
invalidated.

``get_db()`` returns a lazily-built, process-wide :class:`GraphDB` singleton
sourced from :func:`harness.kg.config.get_settings`.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
from neo4j.exceptions import AuthError, ConfigurationError, ServiceUnavailable

from .config import Settings, get_settings


class GraphDB:
    """A thin, friendly wrapper around a ``neo4j`` 6.x :class:`~neo4j.Driver`.

    Construct from explicit connection params, or from application
    :class:`~harness.kg.config.Settings` via :meth:`from_settings`. The driver
    is created eagerly in :meth:`__init__`; call :meth:`verify` to check
    connectivity (this is *not* done automatically, so the object can be built
    before Neo4j is reachable).
    """

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        """Create the underlying driver.

        Args:
            uri: Bolt URI, e.g. ``bolt://127.0.0.1:7687``.
            user: Neo4j username.
            password: Neo4j password.
            database: Default database for sessions (the per-tenant boundary).

        Raises:
            RuntimeError: If the auth argument is malformed
                (``ConfigurationError`` from the driver).
        """
        self.uri = uri
        self.user = user
        self.database = database
        try:
            self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        except ConfigurationError as exc:  # malformed auth / config
            raise RuntimeError(
                f"Invalid Neo4j driver configuration for {uri!r}: {exc}"
            ) from exc

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> GraphDB:
        """Build a :class:`GraphDB` from application settings.

        Args:
            settings: Optional :class:`~harness.kg.config.Settings`; falls back
                to :func:`~harness.kg.config.get_settings` when omitted.

        Returns:
            A configured (but not yet verified) :class:`GraphDB`.
        """
        cfg = settings or get_settings()
        return cls(
            uri=cfg.neo4j_uri,
            user=cfg.neo4j_user,
            password=cfg.neo4j_password,
            database=cfg.neo4j_database,
        )

    def verify(self) -> None:
        """Verify connectivity to the server.

        Raises:
            RuntimeError: With a friendly, actionable message if authentication
                fails (``AuthError``) or the server is unreachable
                (``ServiceUnavailable``).
        """
        try:
            self._driver.verify_connectivity()
        except AuthError as exc:
            raise RuntimeError(
                "Neo4j authentication failed. Check NEO4J_USER / NEO4J_PASSWORD "
                "in harness/.env (the password is unset by default)."
            ) from exc
        except ServiceUnavailable as exc:
            raise RuntimeError(
                f"Cannot reach Neo4j at {self.uri!r}. Is the server running "
                "(e.g. `brew services start neo4j`)?"
            ) from exc

    def get_session(self, database: str | None = None):
        """Open a new session bound to ``database`` (or the configured default).

        Returns:
            A :class:`neo4j.Session` — use it as a context manager.
        """
        return self._driver.session(database=database or self.database)

    def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a read query inside a managed (auto-retried) read transaction.

        Args:
            cypher: A Cypher statement.
            **params: Query parameters bound as ``$name``.

        Returns:
            A list of row dicts (``record.data()`` for each record).
        """

        def _work(tx: ManagedTransaction) -> list[dict[str, Any]]:
            # Consume the cursor inside the tx — the result is invalid afterward.
            return [record.data() for record in tx.run(cypher, **params)]

        with self.get_session() as session:
            return session.execute_read(_work)

    def write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        """Run a write query inside a managed (auto-retried) write transaction.

        The callback is idempotent and consumes its cursor inside the
        transaction, satisfying the managed-transaction retry contract.

        Args:
            cypher: A Cypher statement.
            **params: Query parameters bound as ``$name``.

        Returns:
            A list of row dicts (``record.data()`` for each returned record).
        """

        def _work(tx: ManagedTransaction) -> list[dict[str, Any]]:
            return [record.data() for record in tx.run(cypher, **params)]

        with self.get_session() as session:
            return session.execute_write(_work)

    def close(self) -> None:
        """Close the underlying driver and release its connection pool."""
        self._driver.close()

    def __enter__(self) -> GraphDB:
        """Enter the runtime context, returning this instance."""
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the runtime context, closing the driver."""
        self.close()


_db: GraphDB | None = None


def get_db() -> GraphDB:
    """Return a lazily-built, process-wide :class:`GraphDB` singleton.

    Built once from :func:`~harness.kg.config.get_settings`; connectivity is
    *not* verified here so the singleton can exist before Neo4j is reachable.
    """
    global _db
    if _db is None:
        _db = GraphDB.from_settings()
    return _db
