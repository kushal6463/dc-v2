"""Tests for the read-only / notes-lookup / scoring MCP tools.

These tools live in :mod:`harness.mcp.graph_server`. They are READ-ONLY against
the graph and must emit JSON only — never calling the arbitration writers. (The
deterministic ``propose_*`` skeleton/spine/causal proposer tools were removed
when graph construction moved to the LLM-driven agentic builder; the doc-reading
tools ``list_metrics`` / ``get_metric_source`` are covered in
``test_mcp_doc_reading.py``.) This module asserts:

* the non-DB tools (``inspect_bc2_sources``, ``get_chart_registry_entry``, and
  the pure ``explain_edge_candidate`` scoring tool) return valid JSON with the
  documented keys, and
* NONE of the read-only tool source bodies call ``arbitration.upsert_node`` /
  ``upsert_edge`` (the read-only invariant), enforced by grepping each tool's
  own source.

DB-requiring tools are exercised only when Neo4j is reachable; otherwise they are
skipped (mirroring ``conftest.graphdb``).
"""

from __future__ import annotations

import inspect
import json

import pytest

from harness.kg.config import get_settings
from harness.kg.driver import GraphDB
from harness.mcp import graph_server as gs

# ---------------------------------------------------------------------------
# The kept read-only / notes / scoring tools.
# ---------------------------------------------------------------------------
#: Tools that do NOT touch Neo4j (file / scoring only) — safe to run bare.
_NON_DB_TOOLS = (
    gs.inspect_bc2_sources,
    gs.get_chart_registry_entry,
    gs.explain_edge_candidate,
)
#: Tools that read the live graph (skipped when Neo4j is unavailable).
_DB_TOOLS = (
    gs.validate_edge_candidate,
    gs.lookup_metric_notes,
    gs.list_metrics_by_domain,
    gs.list_metrics_by_scope,
)
#: Every read-only tool whose body must stay write-free (no arbitration writes).
_ALL_READ_TOOLS = _NON_DB_TOOLS + _DB_TOOLS


def _loads(payload: str) -> dict:
    """Parse a tool's JSON-string return value, asserting it is a JSON object."""
    assert isinstance(payload, str)
    obj = json.loads(payload)
    assert isinstance(obj, dict)
    return obj


# ---------------------------------------------------------------------------
# (b) read-only invariant — no tool body calls the arbitration writers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", _ALL_READ_TOOLS, ids=lambda t: t.__name__)
def test_read_tool_is_write_free(tool) -> None:
    """No read-only tool source body INVOKES an arbitration node/edge writer."""
    source = inspect.getsource(tool)
    for forbidden in (
        "upsert_node(",
        "upsert_edge(",
        "write_node_model(",
        "arbitration.upsert",
        "arbitration.write",
    ):
        assert forbidden not in source, (
            f"{tool.__name__} must not reference {forbidden!r} (read-only invariant)"
        )


def test_read_tool_names_avoid_pretool_guard() -> None:
    """Read tool names never match create_*/draw_edge (so they stay read-only)."""
    for tool in _ALL_READ_TOOLS:
        name = tool.__name__
        assert not name.startswith("create_"), name
        assert name != "draw_edge", name


# ---------------------------------------------------------------------------
# (a) non-DB tools return valid JSON with the expected keys
# ---------------------------------------------------------------------------


def test_inspect_bc2_sources_json_shape() -> None:
    """``inspect_bc2_sources`` returns the documented inventory keys."""
    result = _loads(gs.inspect_bc2_sources())
    if result.get("status") == "error":
        pytest.skip(f"BC_2 snapshot unavailable: {result.get('error')}")
    for key in ("files", "valid_rel_candidates", "rejected_rel_rows", "reject_reasons"):
        assert key in result, f"missing key {key!r}"
    assert isinstance(result["files"], list)
    assert isinstance(result["valid_rel_candidates"], int)
    assert isinstance(result["rejected_rel_rows"], int)
    assert isinstance(result["reject_reasons"], dict)
    if result["files"]:
        first = result["files"][0]
        assert "name" in first and "sha256" in first and "rows" in first


def test_get_chart_registry_entry_found_and_missing() -> None:
    """``get_chart_registry_entry`` returns one entry (found) and a clean miss."""
    found = _loads(gs.get_chart_registry_entry("alerts-config:active_rules"))
    assert found["found"] is True
    assert found["canonical_id"] == "alerts-config:active_rules"
    assert isinstance(found["entry"], dict)
    assert found["entry"].get("canonical_id") == "alerts-config:active_rules"

    missing = _loads(gs.get_chart_registry_entry("nope:not-a-real-chart"))
    assert missing["found"] is False
    assert missing["entry"] is None


def test_explain_edge_candidate_scoring() -> None:
    """``explain_edge_candidate`` resolves the deterministic scoring policy."""
    # A pinned, auto-safe deterministic class.
    formula = _loads(
        gs.explain_edge_candidate("a", "b", "DECOMPOSES_INTO", "formula")
    )
    assert formula["auto_safe_or_review"] == "auto_safe"
    assert formula["scoring_policy"] == "formula_exact_v1"
    assert formula["deterministic"] is True
    assert "why" in formula

    # A review-only curated influence class.
    curated = _loads(
        gs.explain_edge_candidate("a", "b", "INFLUENCES", "curated_rule")
    )
    assert curated["auto_safe_or_review"] == "review"
    assert curated["scoring_policy"] == "curated_prior_v1"


# ---------------------------------------------------------------------------
# DB-requiring tools — exercised only when Neo4j is reachable
# ---------------------------------------------------------------------------


def _db_or_skip() -> GraphDB:
    """Return a verified :class:`GraphDB`, or skip if no DB is available."""
    settings = get_settings()
    if not settings.neo4j_password:
        pytest.skip("NEO4J_PASSWORD is empty; skipping DB-dependent test.")
    db = GraphDB.from_settings(settings)
    try:
        db.verify()
    except Exception as exc:  # noqa: BLE001 — any connectivity failure -> skip
        db.close()
        pytest.skip(f"Neo4j not reachable; skipping DB-dependent test ({exc}).")
    db.close()
    return db


def test_list_metrics_by_scope_json_shape() -> None:
    """``list_metrics_by_scope`` returns the {count, metrics} list shape."""
    _db_or_skip()
    result = _loads(gs.list_metrics_by_scope("blended", limit=5))
    if result.get("status") == "error":
        pytest.fail(f"tool errored: {result.get('error')}")
    assert {"count", "metrics"} <= set(result)
    assert result["count"] == len(result["metrics"])
    assert result["count"] <= 5
    for metric in result["metrics"]:
        assert {"metric_uid", "display_name", "concept_key", "causal_role"} <= set(metric)


def test_list_metrics_by_domain_json_shape() -> None:
    """``list_metrics_by_domain`` returns the {count, metrics} list shape."""
    _db_or_skip()
    result = _loads(gs.list_metrics_by_domain("marketing", limit=5))
    if result.get("status") == "error":
        pytest.fail(f"tool errored: {result.get('error')}")
    assert {"count", "metrics"} <= set(result)
    assert result["count"] == len(result["metrics"])


def test_validate_edge_candidate_unknown_endpoints() -> None:
    """``validate_edge_candidate`` flags missing endpoints + bad relation."""
    _db_or_skip()
    result = _loads(
        gs.validate_edge_candidate(
            "metric:does-not-exist:x", "metric:does-not-exist:y",
            "DECOMPOSES_INTO", "formula",
        )
    )
    if result.get("status") == "error":
        pytest.fail(f"tool errored: {result.get('error')}")
    assert {"valid", "endpoint_exists", "scope_ok", "scoring", "reasons"} <= set(result)
    assert result["valid"] is False
    assert result["endpoint_exists"] == {"from": False, "to": False}
    assert result["scoring"]["scoring_policy"] == "formula_exact_v1"

    # An invalid relation subtype is rejected even with the right rel_type.
    bad = _loads(
        gs.validate_edge_candidate("a", "b", "DECOMPOSES_INTO", "not_a_relation")
    )
    assert bad["valid"] is False
    assert any("not allowed" in r for r in bad["reasons"])


def test_lookup_metric_notes_missing() -> None:
    """``lookup_metric_notes`` cleanly reports a not-found metric_uid."""
    _db_or_skip()
    result = _loads(gs.lookup_metric_notes("metric:does-not-exist:zzz"))
    if result.get("status") == "error":
        pytest.fail(f"tool errored: {result.get('error')}")
    assert result["found"] is False
