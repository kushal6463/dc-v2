"""NO-DB tests for the MCP doc-reading tools (read source files, never the graph).

The agentic build replaces the deterministic proposers with three doc-reading
tools on :mod:`harness.mcp.graph_server` — :func:`~harness.mcp.graph_server.\
list_metrics`, :func:`~harness.mcp.graph_server.get_metric_source`, and
:func:`~harness.mcp.graph_server.get_bc2_sql`. Each reads the offline evidence
files (``metric_nodes.rare_seeds.json``, ``metric_registry`` CSV, chart-registry
/ openapi slices, BC_2 SQL) and returns one JSON object; NONE of them touches
Neo4j or the arbitration writers. This module asserts the JSON shapes for a known
metric (``blended.roas``) and enforces the read-only invariant by grepping each
tool's own source (mirroring ``test_mcp_skeleton_tools``). NO-DB throughout.
"""

from __future__ import annotations

import inspect
import json

import pytest

from harness.mcp import graph_server as gs

#: A known catalog metric exercised by the doc-reading tools.
_KNOWN_METRIC_ID = "blended.roas"

#: The doc-reading tools under test (all read source files only, never the graph).
_DOC_TOOLS = (gs.list_metrics, gs.get_metric_source, gs.get_bc2_sql)


def _loads(payload: str) -> dict:
    """Parse a tool's JSON-string return value, asserting it is a JSON object."""
    assert isinstance(payload, str)
    obj = json.loads(payload)
    assert isinstance(obj, dict)
    return obj


# ---------------------------------------------------------------------------
# Read-only invariant — no doc tool body calls an arbitration writer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool", _DOC_TOOLS, ids=lambda t: t.__name__)
def test_doc_tool_never_writes_graph(tool) -> None:
    """No doc-reading tool source body INVOKES an arbitration node/edge writer.

    The doc tools read the offline evidence files only; any reference to the
    arbitration writers (or even the ``GraphDB`` singleton ``get_db``) would mean
    the tool can mutate or read the live graph — a read-only-invariant failure.
    """
    source = inspect.getsource(tool)
    for forbidden in (
        "upsert_node(",
        "upsert_edge(",
        "write_node_model(",
        "arbitration.upsert",
        "arbitration.write",
        "get_db(",
    ):
        assert forbidden not in source, (
            f"{tool.__name__} must not reference {forbidden!r} (read-only invariant)"
        )


def test_doc_tool_names_avoid_pretool_guard() -> None:
    """Doc-tool names never match create_*/draw_edge (so they stay read-only)."""
    for tool in _DOC_TOOLS:
        name = tool.__name__
        assert not name.startswith("create_"), name
        assert name != "draw_edge", name


# ---------------------------------------------------------------------------
# list_metrics — compact catalog rows with the documented keys
# ---------------------------------------------------------------------------


def test_list_metrics_json_shape() -> None:
    """``list_metrics`` returns ``{count, metrics:[…]}`` with the documented keys."""
    result = _loads(gs.list_metrics())
    assert {"count", "metrics"} <= set(result)
    assert result["count"] == len(result["metrics"])
    assert result["count"] > 0
    for metric in result["metrics"]:
        assert {"metric_id", "title", "source", "domain", "node_kind", "is_ml"} <= set(
            metric
        )
        assert metric["node_kind"] in ("metric", "intermediary", "input", "constant")


def test_list_metrics_excludes_operational() -> None:
    """The ``operational`` namespace is dropped from the node set."""
    result = _loads(gs.list_metrics())
    for metric in result["metrics"]:
        assert metric["source"] != "operational"
        assert metric["metric_id"].split(".")[0] != "operational"


def test_list_metrics_namespace_filter_narrows() -> None:
    """A ``namespace`` filter returns a non-empty subset of the unfiltered set."""
    everything = _loads(gs.list_metrics())["count"]
    blended = _loads(gs.list_metrics(namespace="blended"))
    assert 0 < blended["count"] <= everything
    for metric in blended["metrics"]:
        assert metric["source"] == "blended"
    # The known metric is present in the blended slice.
    assert any(m["metric_id"] == _KNOWN_METRIC_ID for m in blended["metrics"])


def test_list_metrics_limit_caps_result() -> None:
    """The ``limit`` caps the number of returned rows."""
    capped = _loads(gs.list_metrics(limit=5))
    assert capped["count"] <= 5


# ---------------------------------------------------------------------------
# get_metric_source — joined offline evidence for a known metric
# ---------------------------------------------------------------------------


def test_get_metric_source_known_metric_keys() -> None:
    """``get_metric_source`` joins every documented source for ``blended.roas``."""
    result = _loads(gs.get_metric_source(_KNOWN_METRIC_ID))
    assert result["found"] is True
    assert result["metric_id"] == _KNOWN_METRIC_ID
    for key in (
        "source_table",
        "node_kind",
        "has_endpoint",
        "catalog",
        "registry",
        "chart_registry",
        "openapi_endpoints",
    ):
        assert key in result, f"missing key {key!r}"
    # The catalog slice is the real entry; node_kind / has_endpoint are hints.
    assert isinstance(result["catalog"], dict)
    assert result["catalog"].get("title") == "Blended ROAS"
    assert result["node_kind"] in ("metric", "intermediary", "input", "constant")
    assert isinstance(result["has_endpoint"], bool)
    assert isinstance(result["openapi_endpoints"], list)


def test_get_metric_source_missing_metric() -> None:
    """An unknown metric_id returns a clean ``{found: false}`` (no crash)."""
    result = _loads(gs.get_metric_source("blended.does-not-exist-xyz"))
    assert result["found"] is False
    assert result["metric_id"] == "blended.does-not-exist-xyz"


def test_get_metric_source_openapi_slice_is_kg_relevant() -> None:
    """Any joined OpenAPI endpoint passes the KG-relevance filter (no deny group)."""
    from harness.ingest.endpoint_filters import is_kg_endpoint

    result = _loads(gs.get_metric_source(_KNOWN_METRIC_ID))
    for endpoint in result["openapi_endpoints"]:
        assert "path" in endpoint
        assert is_kg_endpoint(endpoint["path"]), (
            f"operational endpoint leaked: {endpoint['path']!r}"
        )


# ---------------------------------------------------------------------------
# get_bc2_sql — best-effort SQL/repository slice (clean miss is valid)
# ---------------------------------------------------------------------------


def test_get_bc2_sql_known_metric_shape() -> None:
    """``get_bc2_sql`` returns the documented keys (or a clean ``{found:false}``)."""
    result = _loads(gs.get_bc2_sql(_KNOWN_METRIC_ID))
    if result.get("status") == "error":
        pytest.skip(f"BC_2 source tree unavailable: {result.get('error')}")
    assert "found" in result
    if result["found"]:
        assert result["metric_id"] == _KNOWN_METRIC_ID
        assert {"mart_sql", "repository"} & set(result)
