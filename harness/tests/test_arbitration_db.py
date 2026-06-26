"""DB tests for the arbitration writer (auto-skip when no Neo4j is available).

Verifies the core write discipline:

* ``upsert_node`` twice -> first ``created`` then ``updated``, with the node
  count for that label unchanged after the second call (idempotent MERGE).
* ``upsert_edge`` twice -> idempotent (``created`` then ``updated``), and a
  missing endpoint returns ``missing_endpoint`` without crashing.
* every write appends to the JSONL event log (it grows).
"""

from __future__ import annotations

import json
import uuid

import pytest

from harness.kg.arbitration import (
    append_edge_evidence,
    upsert_edge,
    upsert_node,
    write_node_model,
)
from harness.kg.driver import GraphDB
from harness.kg.models import Domain
from harness.store.jsonl import EVENTS_PATH


def _label_count(db: GraphDB, label: str) -> int:
    """Return the number of nodes carrying ``label``."""
    rows = db.read(f"MATCH (n:{label}) RETURN count(n) AS c")
    return int(rows[0]["c"]) if rows else 0


def _event_lines() -> int:
    """Return the current number of lines in the event log (0 if absent)."""
    if not EVENTS_PATH.exists():
        return 0
    with EVENTS_PATH.open("r", encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def test_upsert_node_created_then_updated(graphdb: GraphDB) -> None:
    """First upsert reports ``created``; the second reports ``updated``."""
    key = f"test-domain-{uuid.uuid4().hex[:8]}"
    props = {
        "name": "Test Domain",
        "decision_scope_summary": "scope",
        "min_level": 10,
        "data_classification": "internal",
        "status": "active",
    }

    before = _label_count(graphdb, "Domain")
    events_before = _event_lines()

    first = upsert_node(
        graphdb,
        label="Domain",
        key_field="domain_id",
        key_value=key,
        props=dict(props),
    )
    assert first["status"] == "created"
    assert _label_count(graphdb, "Domain") == before + 1

    second = upsert_node(
        graphdb,
        label="Domain",
        key_field="domain_id",
        key_value=key,
        props={**props, "name": "Renamed Domain"},
    )
    assert second["status"] == "updated"
    # Count is unchanged after the second (idempotent) upsert.
    assert _label_count(graphdb, "Domain") == before + 1

    # Event log grew (at least the two node_upsert events).
    assert _event_lines() >= events_before + 2

    # Cleanup so reruns stay clean.
    graphdb.write("MATCH (n:Domain {domain_id: $k}) DETACH DELETE n", k=key)


def test_write_node_model_roundtrip(graphdb: GraphDB) -> None:
    """``write_node_model`` upserts a model and the node is then findable."""
    key = f"test-domain-{uuid.uuid4().hex[:8]}"
    model = Domain(
        domain_id=key,
        name="Model Domain",
        decision_scope_summary="scope",
        min_level=20,
        data_classification="internal",
        status="active",
    )
    result = write_node_model(graphdb, model)
    assert result["status"] == "created"

    rows = graphdb.read(
        "MATCH (n:Domain {domain_id: $k}) RETURN n.name AS name", k=key
    )
    assert rows and rows[0]["name"] == "Model Domain"

    graphdb.write("MATCH (n:Domain {domain_id: $k}) DETACH DELETE n", k=key)


def test_upsert_edge_idempotent(graphdb: GraphDB) -> None:
    """Endpoints present: first edge upsert ``created``, second ``updated``."""
    biz_key = f"test-biz-{uuid.uuid4().hex[:8]}"
    dom_key = f"test-domain-{uuid.uuid4().hex[:8]}"

    upsert_node(
        graphdb,
        label="Business",
        key_field="business_id",
        key_value=biz_key,
        props={"display_name": "TB", "tier": "smb", "status": "active"},
    )
    upsert_node(
        graphdb,
        label="Domain",
        key_field="domain_id",
        key_value=dom_key,
        props={
            "name": "TD",
            "decision_scope_summary": "s",
            "min_level": 10,
            "data_classification": "internal",
            "status": "active",
        },
    )

    events_before = _event_lines()
    first = upsert_edge(
        graphdb,
        rel_type="HAS_DOMAIN",
        from_label="Business",
        from_key=biz_key,
        to_label="Domain",
        to_key=dom_key,
    )
    assert first["status"] == "created"

    second = upsert_edge(
        graphdb,
        rel_type="HAS_DOMAIN",
        from_label="Business",
        from_key=biz_key,
        to_label="Domain",
        to_key=dom_key,
    )
    assert second["status"] == "updated"

    # Exactly one edge exists between the two endpoints.
    rows = graphdb.read(
        "MATCH (:Business {business_id: $b})-[r:HAS_DOMAIN]->(:Domain {domain_id: $d}) "
        "RETURN count(r) AS c",
        b=biz_key,
        d=dom_key,
    )
    assert int(rows[0]["c"]) == 1
    assert _event_lines() >= events_before + 2

    graphdb.write("MATCH (n:Business {business_id: $k}) DETACH DELETE n", k=biz_key)
    graphdb.write("MATCH (n:Domain {domain_id: $k}) DETACH DELETE n", k=dom_key)


def test_upsert_edge_missing_endpoint(graphdb: GraphDB) -> None:
    """A missing endpoint yields ``missing_endpoint`` and does not crash."""
    result = upsert_edge(
        graphdb,
        rel_type="HAS_DOMAIN",
        from_label="Business",
        from_key=f"nope-{uuid.uuid4().hex[:8]}",
        to_label="Domain",
        to_key=f"nope-{uuid.uuid4().hex[:8]}",
    )
    assert result["status"] == "missing_endpoint"
    assert result["from"]["exists"] is False
    assert result["to"]["exists"] is False


def _mk_metric(db: GraphDB, key: str) -> None:
    """Create a minimal ``:Metric`` node (just enough to anchor an edge)."""
    upsert_node(
        db,
        label="Metric",
        key_field="metric_uid",
        key_value=key,
        props={"display_name": "T", "status": "active"},
    )


def test_append_edge_evidence_folds_and_is_idempotent(graphdb: GraphDB) -> None:
    """First append creates a folded INFLUENCES edge; re-appending the SAME
    event is idempotent (ledger length and confidence unchanged)."""
    a = f"test-metric-{uuid.uuid4().hex[:8]}"
    b = f"test-metric-{uuid.uuid4().hex[:8]}"
    _mk_metric(graphdb, a)
    _mk_metric(graphdb, b)
    ev = {
        "tier": "prior",
        "direction": "supports",
        "attribution": "test",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    try:
        first = append_edge_evidence(
            graphdb,
            from_key=a,
            to_key=b,
            event=dict(ev),
            edge_props={"relation": "llm_causal"},
        )
        assert first["status"] == "created"
        rows = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]->(:Metric {metric_uid: $b}) "
            "RETURN r.confidence AS c, r.evidence_mass AS m, r.evidence_ledger AS l",
            a=a,
            b=b,
        )
        # One PRIOR supports (weight 1.0) over Jeffreys -> 1.5 / 2.0 = 0.75, mass 2.0.
        assert rows and abs(float(rows[0]["c"]) - 0.75) < 1e-6
        assert abs(float(rows[0]["m"]) - 2.0) < 1e-6
        assert len(json.loads(rows[0]["l"])) == 1

        # Re-append the identical event -> event_id dedupe keeps it a no-op.
        append_edge_evidence(
            graphdb,
            from_key=a,
            to_key=b,
            event=dict(ev),
            edge_props={"relation": "llm_causal"},
        )
        rows2 = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]->(:Metric {metric_uid: $b}) "
            "RETURN r.confidence AS c, r.evidence_ledger AS l",
            a=a,
            b=b,
        )
        assert len(json.loads(rows2[0]["l"])) == 1
        assert abs(float(rows2[0]["c"]) - 0.75) < 1e-6
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n",
            a=a,
            b=b,
        )


def test_append_edge_evidence_skips_structural_dup(graphdb: GraphDB) -> None:
    """A causal append is REFUSED when the pair already has a DECOMPOSES_INTO
    edge (structural subsumes causal) — no parallel INFLUENCES is written."""
    a = f"test-metric-{uuid.uuid4().hex[:8]}"
    b = f"test-metric-{uuid.uuid4().hex[:8]}"
    _mk_metric(graphdb, a)
    _mk_metric(graphdb, b)
    try:
        # Formula edge: A decomposes into B (B is A's numerator).
        upsert_edge(
            graphdb,
            rel_type="DECOMPOSES_INTO",
            from_label="Metric",
            from_key=a,
            to_label="Metric",
            to_key=b,
            props={"relation": "formula", "role": "numerator", "confidence": 1.0},
        )
        # A causal attempt in the REVERSE direction must be skipped, not stacked.
        result = append_edge_evidence(
            graphdb,
            from_key=b,
            to_key=a,
            event={
                "tier": "prior",
                "direction": "supports",
                "attribution": "test",
                "timestamp": "2026-01-01T00:00:00Z",
            },
            edge_props={"relation": "llm_causal"},
        )
        assert result["status"] == "skipped_structural_dup"
        rows = graphdb.read(
            "MATCH (:Metric {metric_uid: $a})-[r:INFLUENCES]-(:Metric {metric_uid: $b}) "
            "RETURN count(r) AS c",
            a=a,
            b=b,
        )
        assert int(rows[0]["c"]) == 0
    finally:
        graphdb.write(
            "MATCH (n:Metric) WHERE n.metric_uid IN [$a, $b] DETACH DELETE n",
            a=a,
            b=b,
        )


def test_append_edge_evidence_rejects_non_influences(graphdb: GraphDB) -> None:
    """The ledger is INFLUENCES-only; any other ``rel_type`` is rejected up front."""
    with pytest.raises(ValueError):
        append_edge_evidence(
            graphdb,
            from_key="x",
            to_key="y",
            event={"tier": "prior", "direction": "supports"},
            rel_type="DECOMPOSES_INTO",
        )
