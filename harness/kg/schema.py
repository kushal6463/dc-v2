"""Neo4j schema DDL for the ThoughtWire Causal Knowledge Graph.

:data:`CONSTRAINTS` and :data:`INDEXES` mirror section 9 of
``docs/final-schema-claude.md`` **verbatim**. The node identity — and therefore
the ``MERGE`` key the single arbitration writer upserts on — is ``metric_uid``
(``metric:<scope>:<base>``); ``canonical_id`` is a coarser *provenance* grouping
(``<dashboard>:<chart>``) that the scope-separated / composite-decomposed
skeleton legitimately shares across many metrics (e.g. every component of one
composite table, or the Google-Search vs Meta variant of one base). It therefore
carries a **non-unique lookup index**, not a uniqueness constraint — an earlier
``metric_canonical_id`` UNIQUE constraint (M2 dedup holdover) is incompatible
with that model and is dropped on init.

The DDL targets Neo4j 2026.05 (default ``CYPHER_25``): each statement is an
idempotent ``CREATE CONSTRAINT … IF NOT EXISTS`` / ``CREATE INDEX … IF NOT
EXISTS`` and is run inside a managed write transaction.
"""

from __future__ import annotations

from .driver import GraphDB

#: Uniqueness constraints — section 9 of the schema doc, verbatim. The Metric
#: identity (and arbitration ``MERGE`` key) is ``metric_uid``; ``canonical_id`` is
#: NOT unique (see the module docstring) and lives in :data:`INDEXES` instead.
CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT business_id IF NOT EXISTS FOR (n:Business) REQUIRE n.business_id IS UNIQUE",
    "CREATE CONSTRAINT domain_id IF NOT EXISTS FOR (n:Domain) REQUIRE n.domain_id IS UNIQUE",
    "CREATE CONSTRAINT product_id IF NOT EXISTS FOR (n:IntelligenceProduct) REQUIRE n.product_id IS UNIQUE",
    "CREATE CONSTRAINT platform_id IF NOT EXISTS FOR (n:Platform) REQUIRE n.platform_id IS UNIQUE",
    "CREATE CONSTRAINT metric_uid IF NOT EXISTS FOR (n:Metric) REQUIRE n.metric_uid IS UNIQUE",
    "CREATE CONSTRAINT dashboard_id IF NOT EXISTS FOR (n:Dashboard) REQUIRE n.dashboard_id IS UNIQUE",
    "CREATE CONSTRAINT ui_component_id IF NOT EXISTS FOR (n:UIComponent) REQUIRE n.component_id IS UNIQUE",
    "CREATE CONSTRAINT policy_id IF NOT EXISTS FOR (n:Policy) REQUIRE n.policy_id IS UNIQUE",
    "CREATE CONSTRAINT threshold_id IF NOT EXISTS FOR (n:Threshold) REQUIRE n.threshold_id IS UNIQUE",
    "CREATE CONSTRAINT role_id IF NOT EXISTS FOR (n:Role) REQUIRE n.role_id IS UNIQUE",
    "CREATE CONSTRAINT role_key IF NOT EXISTS FOR (n:Role) REQUIRE n.role_key IS UNIQUE",
]

#: Legacy constraints to DROP on init (idempotent self-heal for DBs created by an
#: older schema). ``metric_canonical_id`` was a UNIQUE constraint on
#: ``canonical_id`` — incompatible with the scope-separated / composite-decomposed
#: skeleton, where many metrics share a provenance ``canonical_id``. Replaced by
#: the non-unique ``metric_canonical`` index below.
LEGACY_CONSTRAINTS: list[str] = [
    "DROP CONSTRAINT metric_canonical_id IF EXISTS",
]

#: Lookup indexes — section 9 of the schema doc, verbatim, PLUS a non-unique index
#: on ``canonical_id`` (provenance lookups; replaces the dropped UNIQUE constraint).
INDEXES: list[str] = [
    "CREATE INDEX metric_canonical IF NOT EXISTS FOR (n:Metric) ON (n.canonical_id)",
    "CREATE INDEX metric_product IF NOT EXISTS FOR (n:Metric) ON (n.product_ids)",
    "CREATE INDEX metric_domain IF NOT EXISTS FOR (n:Metric) ON (n.domain_ids)",
    "CREATE INDEX metric_platform IF NOT EXISTS FOR (n:Metric) ON (n.platform_ids)",
    "CREATE INDEX metric_concept IF NOT EXISTS FOR (n:Metric) ON (n.concept_key)",
    "CREATE INDEX metric_minlevel IF NOT EXISTS FOR (n:Metric) ON (n.min_level)",
    "CREATE INDEX role_seniority IF NOT EXISTS FOR (n:Role) ON (n.seniority_rank)",
]


def init_schema(db: GraphDB) -> dict[str, object]:
    """Apply every constraint and index to the database (idempotently).

    Each statement is an ``IF NOT EXISTS`` DDL run inside its own managed write
    transaction, so re-running this is safe.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.

    Returns:
        A summary dict::

            {
                "constraints": <count of constraint statements run>,
                "indexes": <count of index statements run>,
                "dropped": <count of legacy DROP statements run>,
                "statements": [<every statement run, in order>],
            }
    """
    statements: list[str] = []
    # Self-heal: drop superseded legacy constraints before creating the current
    # set (idempotent — DROP … IF EXISTS is a no-op on a fresh DB).
    for statement in LEGACY_CONSTRAINTS:
        db.write(statement)
        statements.append(statement)
    for statement in CONSTRAINTS:
        db.write(statement)
        statements.append(statement)
    for statement in INDEXES:
        db.write(statement)
        statements.append(statement)
    return {
        "constraints": len(CONSTRAINTS),
        "indexes": len(INDEXES),
        "dropped": len(LEGACY_CONSTRAINTS),
        "statements": statements,
    }
