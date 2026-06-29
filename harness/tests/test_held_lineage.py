"""Tests for held-edge machinery + mart-lineage promotion + mart-drift audit.

Covers the new held / mart-lineage feature surface:

* NO-DB: the ``held`` review-state vocab + the single-source-of-truth
  :func:`harness.kg.models.active_edge_predicate`; the
  ``INFLUENCES:mart_lineage`` scoring policy
  (:func:`harness.ingest.edge_scoring.score_edge`); and the pure mart-drift
  computation :func:`harness.agentic.enrich._mart_drift_rows`.
* DB (auto-skip without Neo4j via the ``graphdb`` fixture): held edges are
  EXCLUDED from the active causal read
  (:func:`harness.kg.arbitration.read_active_causal_edges`), and
  :func:`harness.agentic.enrich.promote_lineage_edges` is idempotent — running it
  twice appends NO duplicate evidence to a candidate's ledger.

The DB tests operate graph-wide (like the existing ``critique_dedupe`` /
``migrate_edge_ledger`` DB tests) but assert only on the controlled metrics they
create and clean up with ``DETACH DELETE``.
"""

from __future__ import annotations

import json
import uuid

from harness.agentic import enrich
from harness.ingest.edge_scoring import known_edge_classes, score_edge
from harness.kg.arbitration import read_active_causal_edges, upsert_edge, upsert_node
from harness.kg.driver import GraphDB
from harness.kg.models import (
    HELD_REVIEW_STATE,
    active_edge_predicate,
    is_held_review_state,
)

# ---------------------------------------------------------------------------
# NO-DB: held review-state vocab + active-edge predicate
# ---------------------------------------------------------------------------


def test_held_review_state_recognized() -> None:
    """``held`` is a recognized edge review_state with a predicate helper."""
    assert HELD_REVIEW_STATE == "held"
    assert is_held_review_state("held") is True
    assert is_held_review_state("active") is False
    assert is_held_review_state(None) is False


def test_active_edge_predicate_excludes_held_and_deprecated() -> None:
    """The predicate filters BOTH deprecated status and held review_state."""
    pred = active_edge_predicate("r")
    assert "coalesce(r.status, 'active') <> 'deprecated'" in pred
    assert "coalesce(r.review_state, 'active') <> 'held'" in pred
    # A missing value defaults to active (kept) via coalesce.
    assert "coalesce(" in pred
    # The relationship variable is configurable (used by different patterns).
    other = active_edge_predicate("rel")
    assert "rel.status" in other and "rel.review_state" in other
    assert "r.status" not in other


# ---------------------------------------------------------------------------
# NO-DB: INFLUENCES:mart_lineage scoring policy (held, low-mass, not auto-safe)
# ---------------------------------------------------------------------------


def test_mart_lineage_policy_applied() -> None:
    """``INFLUENCES:mart_lineage`` resolves to a held, low-mass, non-deterministic score."""
    assert "INFLUENCES:mart_lineage" in known_edge_classes()
    score = score_edge("INFLUENCES:mart_lineage")
    assert score.review is True  # parked for human review (held)
    assert score.deterministic is False  # never auto-applied
    assert score.scoring_policy == "mart_lineage_v1"
    assert score.confidence == 0.3  # pinned LOW
    assert score.evidence_mass == 2.0  # pinned LOW
    # It is an EXPLICIT class now, not the unknown_edge_class_v1 fallback.
    assert score.scoring_policy != "unknown_edge_class_v1"


# ---------------------------------------------------------------------------
# NO-DB: pure mart-drift computation
# ---------------------------------------------------------------------------


def test_mart_drift_rows_detects_unresolved() -> None:
    """A declared mart that the resolver maps to ``None`` is reported as drift."""

    def fake_resolver(token: str) -> object | None:
        # Only these table stems "exist"; everything else has drifted away.
        return object() if token in {"mart_ok"} else None

    metric_marts = {
        "m1": ["MARTS.mart_ok"],
        "m2": ["MARTS.mart_gone", "MARTS.mart_ok"],
        "m3": [],  # no marts -> not counted
        "m4": None,  # tolerated -> not counted
    }
    out = enrich._mart_drift_rows(metric_marts, fake_resolver)
    assert out["metrics_checked"] == 2  # m1 + m2 only
    assert out["marts_checked"] == 3  # 1 + 2
    assert out["resolved"] == 2  # both mart_ok bindings
    assert out["drifted"] == 1
    assert out["drift"] == [{"metric_uid": "m2", "mart": "MARTS.mart_gone"}]


def test_mart_drift_rows_clean_graph_is_empty() -> None:
    """When every binding resolves, there is no drift."""
    out = enrich._mart_drift_rows(
        {"m1": ["MARTS.mart_a"], "m2": ["MARTS.mart_b"]},
        lambda token: object(),  # everything resolves
    )
    assert out["drifted"] == 0
    assert out["drift"] == []
    assert out["resolved"] == 2


# ---------------------------------------------------------------------------
# DB: held edges excluded from the active causal read
# ---------------------------------------------------------------------------


def _mk_metric(db: GraphDB, key: str) -> None:
    """Create a minimal ``:Metric`` node to anchor an edge."""
    upsert_node(
        db,
        label="Metric",
        key_field="metric_uid",
        key_value=key,
        props={"display_name": "T", "status": "active"},
    )


def test_held_edges_excluded_from_active_causal(graphdb: GraphDB) -> None:
    """A held (and a deprecated) INFLUENCES edge is excluded; an active one is kept."""
    a = f"test-metric-{uuid.uuid4().hex[:8]}"
    b = f"test-metric-{uuid.uuid4().hex[:8]}"  # active neighbour
    c = f"test-metric-{uuid.uuid4().hex[:8]}"  # held neighbour
    d = f"test-metric-{uuid.uuid4().hex[:8]}"  # deprecated neighbour
    for key in (a, b, c, d):
        _mk_metric(graphdb, key)
    try:
        # Active causal edge a -> b (review_state unset -> coalesce defaults active).
        upsert_edge(
            graphdb, rel_type="INFLUENCES", from_label="Metric", from_key=a,
            to_label="Metric", to_key=b,
            props={"relation": "llm_causal", "confidence": 0.6},
        )
        # Held causal edge a -> c (parked for review).
        upsert_edge(
            graphdb, rel_type="INFLUENCES", from_label="Metric", from_key=a,
            to_label="Metric", to_key=c,
            props={"relation": "mart_lineage", "review_state": "held", "confidence": 0.3},
        )
        # Deprecated causal edge a -> d.
        upsert_edge(
            graphdb, rel_type="INFLUENCES", from_label="Metric", from_key=a,
            to_label="Metric", to_key=d,
            props={"relation": "llm_causal", "confidence": 0.5, "status": "deprecated"},
        )

        downstream = read_active_causal_edges(graphdb, a, upstream=False)
        to_ids = {row["to_id"] for row in downstream}
        assert b in to_ids  # active edge surfaces
        assert c not in to_ids  # held edge EXCLUDED
        assert d not in to_ids  # deprecated edge EXCLUDED

        # The active edge is reachable upstream from b (and reports its endpoints).
        upstream = read_active_causal_edges(graphdb, b, upstream=True)
        assert {row["from_id"] for row in upstream} == {a}
        assert upstream[0]["to_id"] == b
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b, $c, $d] DETACH DELETE n",
            a=a, b=b, c=c, d=d,
        )


# ---------------------------------------------------------------------------
# DB: promote_lineage_edges is idempotent (no duplicate ledger evidence)
# ---------------------------------------------------------------------------


def test_promote_lineage_edges_idempotent(graphdb: GraphDB) -> None:
    """Two metrics sharing a mart get ONE held edge; re-running adds no evidence."""
    tag = uuid.uuid4().hex[:8]
    a = f"test-metric-{tag}-a"
    b = f"test-metric-{tag}-b"
    shared_mart = f"MARTS.mart_heldtest_{tag}"  # unique -> only a,b pair via it
    for key in (a, b):
        upsert_node(
            graphdb, label="Metric", key_field="metric_uid", key_value=key,
            props={
                "display_name": "T", "status": "active",
                "mart_sources": [shared_mart], "domain_ids": [f"d_{tag}"],
            },
        )
    try:
        first = enrich.promote_lineage_edges(dry_run=False)
        assert first["written"] >= 1  # our shared-mart pair was written

        # The held candidate edge exists exactly once between a and b.
        edge = graphdb.read(
            "MATCH (x:Metric {metric_uid: $a})-[r:INFLUENCES]-(y:Metric {metric_uid: $b}) "
            "RETURN r.review_state AS rs, r.relation AS rel, "
            "r.evidence_ledger AS l, r.source_kind AS sk",
            a=a, b=b,
        )
        assert len(edge) == 1
        assert edge[0]["rs"] == HELD_REVIEW_STATE
        assert edge[0]["rel"] == "mart_lineage"
        assert edge[0]["sk"] == "mart_lineage"
        ledger_after_first = json.loads(edge[0]["l"])
        assert len(ledger_after_first) == 2  # supports + refutes prior, low mass

        # Re-run: idempotent -> the ledger does NOT grow (no duplicate evidence).
        enrich.promote_lineage_edges(dry_run=False)
        edge2 = graphdb.read(
            "MATCH (x:Metric {metric_uid: $a})-[r:INFLUENCES]-(y:Metric {metric_uid: $b}) "
            "RETURN r.evidence_ledger AS l, r.review_state AS rs",
            a=a, b=b,
        )
        assert len(edge2) == 1
        assert edge2[0]["rs"] == HELD_REVIEW_STATE  # still held
        assert len(json.loads(edge2[0]["l"])) == 2  # SAME length -> no dup evidence
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n", a=a, b=b
        )


def test_promote_lineage_edges_dry_run_writes_nothing(graphdb: GraphDB) -> None:
    """``dry_run`` reports candidates but persists no edge."""
    tag = uuid.uuid4().hex[:8]
    a = f"test-metric-{tag}-a"
    b = f"test-metric-{tag}-b"
    shared_mart = f"MARTS.mart_heldtest_{tag}"
    for key in (a, b):
        upsert_node(
            graphdb, label="Metric", key_field="metric_uid", key_value=key,
            props={
                "display_name": "T", "status": "active",
                "mart_sources": [shared_mart], "domain_ids": [f"d_{tag}"],
            },
        )
    try:
        result = enrich.promote_lineage_edges(dry_run=True)
        assert result["dry_run"] is True
        assert result["written"] == 0
        n = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]-(:Metric {metric_uid: $b}) "
            "RETURN count(r) AS n", a=a, b=b,
        )[0]["n"]
        assert n == 0  # nothing persisted in dry-run
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n", a=a, b=b
        )
