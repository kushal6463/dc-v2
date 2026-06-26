"""DB tests for the graph-mutating enrichment passes (auto-skip without Neo4j).

Exercises :func:`harness.agentic.enrich.critique_dedupe` and
:func:`~harness.agentic.enrich.migrate_edge_ledger` against a real Neo4j (the
``graphdb`` fixture auto-skips when ``NEO4J_PASSWORD`` is empty). Both functions
operate graph-wide but are idempotent on an already-clean graph, so each test
sets up a controlled metric pair, runs the pass, asserts on that pair, and
cleans up with ``DETACH DELETE``.
"""

from __future__ import annotations

import json
import uuid

from harness.agentic import enrich
from harness.kg.arbitration import upsert_edge, upsert_node
from harness.kg.driver import GraphDB


def _mk_metric(db: GraphDB, key: str) -> None:
    """Create a minimal ``:Metric`` node to anchor an edge."""
    upsert_node(
        db,
        label="Metric",
        key_field="metric_uid",
        key_value=key,
        props={"display_name": "T", "status": "active"},
    )


def test_critique_dedupe_removes_parallel_influences(graphdb: GraphDB) -> None:
    """A causal edge parallel to a formula edge is removed; the formula survives."""
    a = f"test-metric-{uuid.uuid4().hex[:8]}"
    b = f"test-metric-{uuid.uuid4().hex[:8]}"
    _mk_metric(graphdb, a)
    _mk_metric(graphdb, b)
    try:
        # Structural (formula) edge A -> B.
        upsert_edge(
            graphdb, rel_type="DECOMPOSES_INTO", from_label="Metric", from_key=a,
            to_label="Metric", to_key=b,
            props={"relation": "formula", "role": "numerator", "confidence": 1.0},
        )
        # A legacy parallel INFLUENCES, written directly (simulating pre-guard state).
        graphdb.write(
            "MATCH (x:Metric {metric_uid: $a}), (y:Metric {metric_uid: $b}) "
            "MERGE (x)-[r:INFLUENCES]->(y) SET r.confidence = 0.6",
            a=a, b=b,
        )

        result = enrich.critique_dedupe(dry_run=False)
        assert [a, b] in result["pairs"] or [b, a] in result["pairs"]

        n_infl = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]-(:Metric {metric_uid: $b}) "
            "RETURN count(r) AS n", a=a, b=b,
        )[0]["n"]
        assert n_infl == 0  # the parallel causal edge is gone
        n_struct = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:DECOMPOSES_INTO]-(:Metric {metric_uid: $b}) "
            "RETURN count(r) AS n", a=a, b=b,
        )[0]["n"]
        assert n_struct == 1  # the formula edge survives
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n", a=a, b=b
        )


def test_migrate_edge_ledger_folds_flat_confidence(graphdb: GraphDB) -> None:
    """A legacy flat-confidence INFLUENCES gets a seeded Beta ledger on migration."""
    a = f"test-metric-{uuid.uuid4().hex[:8]}"
    b = f"test-metric-{uuid.uuid4().hex[:8]}"
    _mk_metric(graphdb, a)
    _mk_metric(graphdb, b)
    try:
        # Legacy INFLUENCES: flat confidence, NO evidence_ledger.
        graphdb.write(
            "MATCH (x:Metric {metric_uid: $a}), (y:Metric {metric_uid: $b}) "
            "MERGE (x)-[r:INFLUENCES]->(y) "
            "SET r.confidence = 0.6, r.relation = 'llm_causal'",
            a=a, b=b,
        )

        enrich.migrate_edge_ledger(dry_run=False)

        row = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]->(:Metric {metric_uid: $b}) "
            "RETURN r.evidence_ledger AS l, r.confidence AS c, r.evidence_mass AS m",
            a=a, b=b,
        )[0]
        assert row["l"] is not None  # ledger seeded
        assert len(json.loads(row["l"])) == 2  # seed_prior_event -> supports + refutes
        # 0.6 tier folded with prior_mass 4 -> alpha 2.9 / beta 2.1 -> conf 0.58, mass 5.0.
        assert abs(float(row["c"]) - 0.58) < 0.01
        assert abs(float(row["m"]) - 5.0) < 0.01
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n", a=a, b=b
        )
