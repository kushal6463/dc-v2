"""DB tests for the schema DDL (auto-skip when no Neo4j is available).

Verifies that :func:`harness.kg.schema.init_schema` is idempotent (running it
twice raises nothing) and that ``SHOW CONSTRAINTS`` afterward includes the
constraints we declared.
"""

from __future__ import annotations

from harness.kg.driver import GraphDB
from harness.kg.schema import CONSTRAINTS, INDEXES, LEGACY_CONSTRAINTS, init_schema


def _constraint_names(db: GraphDB) -> set[str]:
    """Return the set of constraint names currently defined on the server."""
    rows = db.read("SHOW CONSTRAINTS YIELD name RETURN name")
    return {row["name"] for row in rows}


def test_init_schema_is_idempotent(graphdb: GraphDB) -> None:
    """Running ``init_schema`` twice succeeds and reports the full statement set."""
    first = init_schema(graphdb)
    assert first["constraints"] == len(CONSTRAINTS)
    assert first["indexes"] == len(INDEXES)
    assert len(first["statements"]) == (
        len(LEGACY_CONSTRAINTS) + len(CONSTRAINTS) + len(INDEXES)
    )

    # Second run must not raise (every statement is IF NOT EXISTS / IF EXISTS).
    second = init_schema(graphdb)
    assert second["constraints"] == len(CONSTRAINTS)
    assert second["indexes"] == len(INDEXES)


def test_show_constraints_includes_ours(graphdb: GraphDB) -> None:
    """After init, the declared constraint names appear in ``SHOW CONSTRAINTS``."""
    init_schema(graphdb)
    names = _constraint_names(graphdb)
    # The named constraints we declare should all be present.
    for expected in ("business_id", "domain_id", "product_id", "metric_uid"):
        assert expected in names, f"{expected!r} missing from {sorted(names)}"
    # The legacy UNIQUE constraint on canonical_id must NOT be present (it is
    # incompatible with the scope-separated / composite-decomposed skeleton, where
    # many metrics legitimately share a provenance canonical_id).
    assert "metric_canonical_id" not in names
