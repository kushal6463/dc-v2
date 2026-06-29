"""FastAPI application for the ThoughtWire live causal-KG canvas.

Exposes the pinned REST + SSE contract the React canvas
(``app/kg-canvas``) consumes:

REST (all under ``/api``)::

    GET  /api/health
    GET  /api/status
    GET  /api/graph?limit=2000&include_deprecated=false
    GET  /api/coverage?tenant=rare_seeds
    GET  /api/edge-diff?tenant=rare_seeds&run_id=
    GET  /api/traverse/upstream?metric_uid=&max_depth=4&min_confidence=0
    GET  /api/traverse/downstream?metric_uid=&max_depth=4&min_confidence=0
    GET  /api/column-impact?column=
    GET  /api/metric-chart?metric_uid=
    GET  /api/dashboards
    GET  /api/proposals?run_id=
    POST /api/proposals/{proposal_id}/review
    POST /api/ingest
    POST /api/apply

SSE::

    GET  /api/events   (text/event-stream, sse-starlette)

The graph + status endpoints read Neo4j through the shared
:class:`~harness.kg.driver.GraphDB`; the ingest endpoint schedules the (async)
:func:`harness.ingest.orchestrator.ingest_dashboards` as a background task that
streams events on the bus (and, when ``auto_approve`` is set, approves + applies
each dashboard's proposals as they land, emitting ``node_written``). Arbitration
remains the single graph writer — this server never writes nodes/edges directly.

Run with::

    uv run uvicorn harness.api.server:app --port 8000

or ``uv run python -m harness.api.server``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request

from harness.api.events import bus
from harness.api.sse import event_stream
from harness.ingest import apply as apply_mod
from harness.ingest.prepass import all_dashboard_ids, run_prepass
from harness.ingest.run_subprocess import EVENT_PREFIX
from harness.kg.arbitration import upsert_edge, write_node_model
from harness.kg.config import REPO_ROOT
from harness.kg.driver import GraphDB, get_db
from harness.kg.models import (
    EDGE_ROLES,
    NODE_KEY_FIELDS,
    NODE_LABELS,
    Policy,
    Threshold,
    active_edge_predicate,
)
from harness.marts.snowflake_reader import fetch_active_campaign_breakdown
from harness.store.proposals import (
    approve_all_pending,
    latest_run_id,
    load_proposals,
    new_run_id,
    set_review_state,
    set_review_state_anywhere,
)


def _jsonable(value: Any) -> Any:
    """Coerce a Neo4j-returned value into a JSON-serializable Python value.

    Neo4j temporal/spatial types (``neo4j.time.DateTime`` etc.) are not natively
    JSON-serializable by pydantic; this recursively converts them via their
    ``isoformat()`` / ``str()`` and walks lists/dicts. Primitives pass through.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        return iso()
    return str(value)


def _clean_props(props: Any) -> dict[str, Any]:
    """Return a JSON-safe copy of a node/edge property map."""
    return {str(k): _jsonable(v) for k, v in dict(props or {}).items()}


#: The edge fields every wire edge exposes at top level (the canvas legend /
#: traversal payloads read these without spelunking ``props``). ``status``
#: defaults to ``"active"`` when absent; the rest default to ``None`` so the
#: shape is stable across structural (``DECOMPOSES_INTO``) and causal
#: (``INFLUENCES``) edges, which carry different subsets of props.
_EDGE_WIRE_FIELDS: tuple[str, ...] = (
    "relation",
    "confidence",
    "evidence_mass",
    "scoring_policy",
    "review_state",
    "temporal_lag",
    "mechanism",
    "source_kind",
    "deprecated_at",
)


def _edge_wire_fields(props: dict[str, Any]) -> dict[str, Any]:
    """Lift the pinned edge fields out of ``props`` onto the edge top level.

    ``status`` defaults to ``"active"`` (only reconciliation sets it to
    ``"deprecated"``); every other field defaults to ``None`` so the wire shape
    is uniform regardless of which edge subtype produced it.
    """
    out: dict[str, Any] = {"status": props.get("status") or "active"}
    for field in _EDGE_WIRE_FIELDS:
        out[field] = props.get(field)
    return out


def _lag_to_days(value: Any) -> float:
    """Parse an ISO-8601 duration (``P0D``/``P3D``/``PT6H``) into days.

    Mirrors the inverse of :func:`harness.ingest.bc2_snapshot._hours_to_iso`:
    days (``P#D``) pass through, hours/minutes/seconds (``PT#H``/``M``/``S``) are
    converted to fractional days, and a combined ``P#DT#H`` form sums both. A
    missing / unparseable value contributes ``0.0`` (treated as no lag).
    """
    if value is None:
        return 0.0
    text = str(value).strip().upper()
    if not text or text == "P0D":
        return 0.0
    match = re.fullmatch(
        r"P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?"
        r"(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        text,
    )
    if not match:
        return 0.0
    days, hours, minutes, seconds = match.groups()
    total = float(days or 0.0)
    total += float(hours or 0.0) / 24.0
    total += float(minutes or 0.0) / (24.0 * 60.0)
    total += float(seconds or 0.0) / (24.0 * 60.0 * 60.0)
    return total


#: The structural rel type — a metric decomposed into its components. Every
#: other traversed rel type (``INFLUENCES``) is treated as a causal hop.
_STRUCTURAL_REL_TYPE = "DECOMPOSES_INTO"

#: Structural ``role`` values that flip a hop's sign negative (a component that
#: enters its parent's formula as a divisor / subtracted term). Every other role
#: in :data:`~harness.kg.models.EDGE_ROLES` (``numerator``/``addend``/``factor``/
#: ``component``/``driver``) is additive (+1); see :func:`_hop_sign`.
_NEGATIVE_ROLES: frozenset[str] = frozenset({"denominator", "subtrahend"})


def _hop_sign(role: Any) -> int:
    """Derive a per-hop sign from a structural edge's ``role`` prop.

    The sign captures whether a component pushes its parent up or down through
    the formula it decomposes into: a ``denominator`` / ``subtrahend`` enters
    inversely (``-1``); every other known structural role (``numerator``,
    ``addend``, ``factor``, ``component``, ``driver``) is additive (``+1``). A
    causal ``INFLUENCES`` hop carries no role, so its direction is unknown in V1
    and contributes ``0`` (an unsigned hop), which zeroes the whole path's sign.

    Args:
        role: The edge's ``role`` prop (``None`` / absent on ``INFLUENCES``).

    Returns:
        ``-1`` for a divisor/subtracted role, ``+1`` for any other role in
        :data:`~harness.kg.models.EDGE_ROLES`, and ``0`` when there is no role
        (a causal hop or an unrecognised value).
    """
    if role is None:
        return 0
    role_str = str(role)
    if role_str in _NEGATIVE_ROLES:
        return -1
    if role_str in EDGE_ROLES:
        return 1
    return 0


def _hop_kind(rel_type: Any) -> str:
    """Classify a traversal hop by its relationship type.

    A ``DECOMPOSES_INTO`` hop is ``"structural"`` (a formula/identity edge); any
    other type (``INFLUENCES``) is ``"causal"``. Mirrors the structural/causal
    split the canvas legend draws, so a unified path can colour each hop.

    Args:
        rel_type: The Neo4j relationship type (``"DECOMPOSES_INTO"`` /
            ``"INFLUENCES"``); anything not structural is treated as causal.

    Returns:
        ``"structural"`` when ``rel_type == "DECOMPOSES_INTO"`` else ``"causal"``.
    """
    return "structural" if rel_type == _STRUCTURAL_REL_TYPE else "causal"


def _shape_hop(rel: dict[str, Any]) -> dict[str, Any]:
    """Shape one raw Cypher rel-map into a wire edge for a traversal path.

    Lifts the pinned per-hop fields (``from``, ``to``, ``rel_type``,
    ``relation``, ``kind``, ``role``, ``sign``, ``confidence``, ``temporal_lag``,
    ``lag_plausibility``) into the stable wire shape, labelling the hop ``kind``
    via :func:`_hop_kind` and deriving its ``sign`` from the structural ``role``
    via :func:`_hop_sign`.
    Endpoints are coerced to ``str`` (or ``None`` when absent) so the shape is
    uniform.

    Args:
        rel: A rel-map row as returned by the traversal Cypher (keys ``from``,
            ``to``, ``rel_type``, ``relation``, ``role``, ``confidence``,
            ``temporal_lag``, ``lag_plausibility``).

    Returns:
        The wire edge dict.
    """
    rel_type = rel.get("rel_type")
    role = rel.get("role")
    return {
        "from": str(rel.get("from")) if rel.get("from") is not None else None,
        "to": str(rel.get("to")) if rel.get("to") is not None else None,
        "rel_type": rel_type,
        "relation": rel.get("relation"),
        "kind": _hop_kind(rel_type),
        "role": role,
        "sign": _hop_sign(role),
        "confidence": _jsonable(rel.get("confidence")),
        "temporal_lag": rel.get("temporal_lag"),
        "lag_plausibility": _jsonable(rel.get("lag_plausibility")),
    }


def _as_factor(value: Any) -> float:
    """Coerce a confidence / lag-plausibility prop into a multiplicative factor.

    A present numeric value is used as-is; a missing (``None``) or unparseable
    value contributes the neutral factor ``1.0``, so it neither helps nor
    penalises a path's product score.

    Args:
        value: A ``confidence`` / ``lag_plausibility`` edge prop (any type).

    Returns:
        The value as a ``float``, or ``1.0`` when it is missing / unparseable.
    """
    if value is None:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _score_path(rels: list[dict[str, Any]]) -> tuple[float, float, int]:
    """Score one traversal path from its raw rel-maps — pure, no DB.

    Realises FR-SCORE-003: a path's ``score`` is the product over its hops of
    ``(confidence or 1.0) * (lag_plausibility or 1.0)`` (a missing / unparseable
    factor counts as ``1.0`` via :func:`_as_factor`, so it neither helps nor
    penalises). Alongside the score it accumulates the cumulative temporal lag in
    days (summing :func:`_lag_to_days` over each hop's ``temporal_lag``) and the
    ``path_sign`` — the product of each hop's :func:`_hop_sign`, so a structural
    ``denominator`` / ``subtrahend`` hop flips the sign and an unsigned causal hop
    zeroes it.

    Takes the raw rel-map dicts the traversal Cypher projects (``confidence``,
    ``lag_plausibility``, ``temporal_lag`` and ``role`` keys), so it is fully
    unit-testable with no Neo4j.

    Args:
        rels: The path's rel-maps, each carrying ``confidence``,
            ``lag_plausibility``, ``temporal_lag`` and ``role`` keys (any missing
            key defaults safely).

    Returns:
        ``(score, cumulative_lag_days, path_sign)``.
    """
    score = 1.0
    cumulative_lag = 0.0
    path_sign = 1
    for rel in rels:
        conf = _as_factor(rel.get("confidence"))
        plausibility = _as_factor(rel.get("lag_plausibility"))
        score *= conf * plausibility
        cumulative_lag += _lag_to_days(rel.get("temporal_lag"))
        path_sign *= _hop_sign(rel.get("role"))
    return score, cumulative_lag, path_sign


def _artifact_path(name: str) -> Path:
    """Resolve a skeleton artifact file under ``data/skeleton``."""
    return REPO_ROOT / "data" / "skeleton" / name


#: The chart-registry doc the metric-chart endpoint slices (read-only; never the
#: whole file is returned). Same source the MCP ``get_chart_registry_entry`` /
#: ``lookup_metric_notes`` tools read.
_CHART_REGISTRY_PATH: Path = REPO_ROOT / "docs" / "frd-docs" / "chart-registry.json"

#: canonical_id -> {chart_type, …}. The registry itself carries no chart_type;
#: this map (built by the ingestion prepass) supplies the visualization type so
#: dashboard-revealed charts render the right glyph instead of all defaulting KPI.
_CHART_TYPE_MAP_PATH: Path = REPO_ROOT / "data" / "chart_type_map.json"


#: Source kinds that mark a node as agent-authored (for provenance derivation).
_AGENT_SOURCE_KINDS = frozenset({"llm_proposal", "statistical_proposal"})
#: Source kinds / created_by markers that mean a human authored the node.
_HUMAN_MARKERS = frozenset({"manual_review", "human"})

app = FastAPI(title="ThoughtWire Causal KG — Live Canvas API")

# Permissive CORS for the Vite dev server (the proxy is primary; this is backup).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Provenance derivation
# ---------------------------------------------------------------------------


def _provenance(props: dict[str, Any]) -> str:
    """Derive a node's provenance bucket from its props.

    Rules (per the pinned contract): ``"agent"`` when ``source_kind`` is an
    agent/statistical proposal or ``created_by`` looks agent-authored; ``"human"``
    when ``source_kind``/``created_by`` is a manual-review/human marker; otherwise
    ``"deterministic"``.

    Args:
        props: The node's property map.

    Returns:
        ``"agent" | "human" | "deterministic"``.
    """
    source_kind = str(props.get("source_kind") or "").lower()
    created_by = str(props.get("created_by") or "").lower()

    if source_kind in _AGENT_SOURCE_KINDS or "agent" in created_by:
        return "agent"
    if source_kind in _HUMAN_MARKERS or created_by in _HUMAN_MARKERS:
        return "human"
    return "deterministic"


def _title(label: str, props: dict[str, Any]) -> str:
    """Pick a human display title for a node (display_name|name|title|metric_id|id)."""
    for field in ("display_name", "name", "title", "metric_id"):
        value = props.get(field)
        if value:
            return str(value)
    key_field = NODE_KEY_FIELDS.get(label)
    if key_field and props.get(key_field):
        return str(props[key_field])
    return str(props.get("id") or label)


def _node_id(label: str, props: dict[str, Any]) -> str:
    """Return a node's stable key string (its identity-field value)."""
    key_field = NODE_KEY_FIELDS.get(label)
    if key_field and props.get(key_field) is not None:
        return str(props[key_field])
    return str(props.get("id") or "")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ReviewBody(BaseModel):
    """Body for ``POST /api/proposals/{proposal_id}/review``."""

    action: str
    run_id: str
    reason: str | None = None
    payload: dict[str, Any] | None = None


class IngestBody(BaseModel):
    """Body for ``POST /api/ingest``."""

    dashboard_id: str | None = None
    all: bool = False
    concurrency: int = 6
    auto_approve: bool = False


class ApplyBody(BaseModel):
    """Body for ``POST /api/apply``."""

    run_id: str = Field(...)


class CausalBody(BaseModel):
    """Body for ``POST /api/run-causal`` (M3 causal pass)."""

    #: Include the LLM-proposed INFLUENCES stage. CLI-only (needs the agent SDK's
    #: clean-process handshake); the API runs the deterministic stages only.
    use_llm: bool = False
    auto_approve: bool = False


class GovernanceBody(BaseModel):
    """Body for ``POST /api/governance`` — author a Policy + Threshold on a metric.

    Writes one or more ``Policy`` nodes, a single shared ``Threshold`` node, and
    the governance edges (``Policy -GOVERNS-> Metric``,
    ``Metric -HAS_THRESHOLD-> Threshold``, and ``Policy -ENFORCES_THRESHOLD->
    Threshold`` per policy) through the single arbitration writer.
    ``policy``/``threshold`` are field maps validated against the Pydantic models
    (unknown keys are dropped). A metric may carry several policies (alerting /
    budget / SLA …) via ``policies``; all enforce the one shared Threshold.
    The singular ``policy``/``policy_id`` remain accepted for back-compat. Ids are
    derived from ``metric_uid`` (and the policy name) when omitted.
    """

    metric_uid: str
    policy_id: str | None = None
    threshold_id: str | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    policies: list[dict[str, Any]] | None = None
    threshold: dict[str, Any] = Field(default_factory=dict)

    def resolved_policies(self) -> list[dict[str, Any]]:
        """The policies to write: the ``policies`` list, else the singular ``policy``."""
        if self.policies:
            return self.policies
        return [self.policy]


class ExtractBody(BaseModel):
    """Body for ``POST /api/governance/extract`` — LLM-parse a doc into draft fields."""

    text: str
    metric_uid: str | None = None
    metric_name: str | None = None


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, bool]:
    """Liveness probe."""
    return {"ok": True}


@app.get("/api/status")
def status() -> dict[str, dict[str, int]]:
    """Return per-label node counts and per-type edge counts from Neo4j."""
    db = get_db()
    node_rows = db.read(
        "MATCH (n) UNWIND labels(n) AS label "
        "RETURN label AS label, count(*) AS count"
    )
    nodes = {
        str(row["label"]): int(row["count"])
        for row in node_rows
        if str(row["label"]) in NODE_LABELS
    }
    edge_rows = db.read(
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count"
    )
    edges = {str(row["type"]): int(row["count"]) for row in edge_rows}
    return {"nodes": nodes, "edges": edges}


def _graph_payload(
    db: GraphDB, limit: int, *, include_deprecated: bool = False
) -> dict[str, Any]:
    """Read up to ``limit`` nodes + their connecting edges into the wire shape.

    Each edge dict carries the pinned top-level fields (``relation``, ``status``,
    ``confidence``, ``evidence_mass``, ``scoring_policy``, ``review_state``,
    ``temporal_lag``, ``mechanism``, ``source_kind``, ``deprecated_at``) lifted
    out of its props. Edges with ``status == "deprecated"`` are excluded unless
    ``include_deprecated`` is true.
    """
    node_rows = db.read(
        "MATCH (n) "
        "RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props "
        "LIMIT $limit",
        limit=limit,
    )

    nodes: list[dict[str, Any]] = []
    id_set: set[str] = set()
    # Map each returned node's Neo4j elementId -> its wire id, so edges can be
    # queried by the exact selected node set (no LIMIT-multiplier heuristic) and
    # mapped back to wire ids regardless of per-label key differences.
    eid_to_wire: dict[str, str] = {}
    for row in node_rows:
        labels = [lbl for lbl in (row["labels"] or []) if lbl in NODE_LABELS]
        if not labels:
            continue
        label = labels[0]
        props = _clean_props(row["props"])
        node_id = _node_id(label, props)
        if not node_id or node_id in id_set:
            continue
        id_set.add(node_id)
        eid_to_wire[str(row["eid"])] = node_id
        nodes.append(
            {
                "id": node_id,
                "label": label,
                "title": _title(label, props),
                "provenance": _provenance(props),
                "props": props,
            }
        )

    # Edges whose BOTH endpoints are in the returned node set (so the graph is
    # self-consistent and the canvas never references a missing node). Querying
    # by the selected nodes' elementIds means edge completeness does not depend
    # on a LIMIT multiplier heuristic.
    edges: list[dict[str, Any]] = []
    seen_edges: set[str] = set()
    if eid_to_wire:
        edge_rows = db.read(
            "MATCH (a)-[r]->(b) "
            "WHERE elementId(a) IN $eids AND elementId(b) IN $eids "
            "RETURN elementId(a) AS a_eid, type(r) AS type, "
            "properties(r) AS r_props, elementId(b) AS b_eid",
            eids=list(eid_to_wire.keys()),
        )
        for row in edge_rows:
            source = eid_to_wire.get(str(row["a_eid"]))
            target = eid_to_wire.get(str(row["b_eid"]))
            if source is None or target is None:
                continue
            rel_type = str(row["type"])
            edge_id = f"{source}-{rel_type}-{target}"
            if edge_id in seen_edges:
                continue
            seen_edges.add(edge_id)
            props = _clean_props(row["r_props"])
            wire = _edge_wire_fields(props)
            # Exclude deprecated edges by default (reconciliation soft-deletes
            # edges by flipping status -> "deprecated" rather than removing them).
            if not include_deprecated and wire["status"] == "deprecated":
                continue
            edges.append(
                {
                    "id": edge_id,
                    "source": source,
                    "target": target,
                    "type": rel_type,
                    **wire,
                    "props": props,
                }
            )

    return {"nodes": nodes, "edges": edges}


@app.get("/api/graph")
def graph(limit: int = 2000, include_deprecated: bool = False) -> dict[str, Any]:
    """Return the graph (nodes + connecting edges) in the canvas wire shape.

    Deprecated edges (``status == "deprecated"``) are filtered out unless
    ``include_deprecated=true``.
    """
    return _graph_payload(
        get_db(), max(1, limit), include_deprecated=include_deprecated
    )


# ---------------------------------------------------------------------------
# Coverage (computed LIVE from the graph) + edge diff (still reads the
# data/skeleton/ artifact for proposal-run comparison)
# ---------------------------------------------------------------------------


@app.get("/api/coverage")
def coverage(tenant: str = "rare_seeds") -> dict[str, Any]:
    """Return a LIVE coverage summary computed from the graph (Neo4j).

    The canvas CoverageBadge reads ``metric_nodes`` / ``metrics_with_formula``.
    This previously returned the static ``data/skeleton/coverage_report.<tenant>``
    artifact written by the (now-removed) deterministic skeleton build — which
    went permanently stale after the LLM build replaced the graph (e.g. it still
    reported the old 885-metric count). It now reflects the LIVE graph instead, so
    the badge always matches what the canvas renders. Returns ``{"error": ...}``
    (not a raised 5xx) when the graph is unreachable, so the canvas renders an
    empty-state rather than a fetch failure.
    """
    try:
        db = get_db()
        total = db.read("MATCH (m:Metric) RETURN count(m) AS n")[0]["n"]
        with_formula = db.read(
            "MATCH (m:Metric) WHERE m.formula_text IS NOT NULL "
            "AND m.formula_text <> '' RETURN count(m) AS n"
        )[0]["n"]
        by_scope = {
            r["s"]: r["n"]
            for r in db.read(
                "MATCH (m:Metric) RETURN coalesce(m.scope_key, '(none)') AS s, "
                "count(m) AS n ORDER BY n DESC"
            )
        }
        edges = {
            r["t"]: r["n"]
            for r in db.read(
                "MATCH ()-[r]->() WHERE type(r) IN ['DECOMPOSES_INTO', 'INFLUENCES'] "
                f"AND {active_edge_predicate('r')} "
                "RETURN type(r) AS t, count(r) AS n"
            )
        }
    except Exception as exc:  # noqa: BLE001 — surface as empty-state, not a 5xx
        return {"error": f"live coverage unavailable: {exc}"}
    return {
        "tenant": tenant,
        "metric_nodes": int(total),
        "metrics_with_formula": int(with_formula),
        "metrics_without_formula": int(total) - int(with_formula),
        "metrics_by_scope": by_scope,
        "structural_edges": int(edges.get("DECOMPOSES_INTO", 0)),
        "causal_edges": int(edges.get("INFLUENCES", 0)),
        "source": "live_graph",
    }


@app.get("/api/edge-diff")
def edge_diff(tenant: str = "rare_seeds", run_id: str | None = None) -> dict[str, Any]:
    """Return the parsed ``edge_diff.<tenant>.<run_id>.json``.

    When ``run_id`` is omitted, the most recent ``edge_diff.<tenant>.*.json`` for
    the tenant is selected (newest by mtime). Returns ``{"error": ...}`` when no
    matching file exists.
    """
    if run_id:
        path = _artifact_path(f"edge_diff.{tenant}.{run_id}.json")
        if not path.exists():
            return {
                "error": (
                    f"edge diff not found for tenant {tenant!r} run {run_id!r}"
                )
            }
    else:
        candidates = sorted(
            (REPO_ROOT / "data" / "skeleton").glob(f"edge_diff.{tenant}.*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        if not candidates:
            return {"error": f"no edge diff found for tenant {tenant!r}"}
        path = candidates[-1]
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Lineage traversal (upstream causes / downstream blast radius)
# ---------------------------------------------------------------------------

#: Relationship types a lineage traversal walks (structural + causal).
_TRAVERSE_REL_TYPES = "DECOMPOSES_INTO|INFLUENCES"


#: Max acyclic lineage paths returned per traversal (kept stable from the
#: original contract); cyclic paths are surfaced separately under a tighter cap.
_ACYCLIC_PATH_CAP = 50
#: Max cyclic lineage paths surfaced under ``cyclic_paths`` (loops are reported,
#: never broken, so the count is bounded to keep the payload small).
_CYCLIC_PATH_CAP = 25


def _traverse(
    metric_uid: str,
    *,
    max_depth: int,
    upstream: bool,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Walk variable-length lineage paths from a metric over both edge types.

    ``upstream`` follows edges INTO the metric (its dependencies / causes);
    downstream follows edges OUT (its blast radius). Deprecated edges are
    excluded. Each path is scored by the product of its per-hop ``confidence ×
    lag_plausibility`` (a missing factor counts as ``1.0`` so it neither helps
    nor penalises; see :func:`_score_path`), annotated with the cumulative
    temporal lag in days, and given a ``path_sign``
    — the product of its per-hop signs (a structural ``denominator``/
    ``subtrahend`` hop is ``-1``, every other structural role ``+1``, a causal
    ``INFLUENCES`` hop ``0``; see :func:`_hop_sign`), so the sign is non-zero only
    on a purely structural path and tells you whether the anchor moves the
    endpoint up (``+1``) or down (``-1``). Each hop is labelled with its ``kind``
    (``"structural"`` for ``DECOMPOSES_INTO`` / ``"causal"`` for ``INFLUENCES``).

    Cycles are detected (a path on which a ``metric_uid`` repeats) and surfaced
    *separately* under ``cyclic_paths`` rather than broken or silently skipped:
    the acyclic ``paths`` array stays the stable, ranked (score-descending),
    ~50-capped lineage; ``cyclic_paths`` carries the loop-bearing paths (ranked
    the same way, ~25-capped). Neo4j never reuses a relationship within a single
    variable-length path, so traversal stays finite (bounded by ``max_depth``)
    even though node repeats are now allowed through.

    Args:
        metric_uid: The anchor metric's ``metric_uid``.
        max_depth: Maximum traversal depth (number of hops).
        upstream: ``True`` for upstream (dependencies), ``False`` for downstream.
        min_confidence: Exclude any path containing a hop whose ``confidence``
            (a missing value counts as ``1.0``) is below this threshold; the
            default ``0.0`` filters nothing.

    Returns:
        ``{"paths": [...], "cyclic_paths": [...], "summary": {"acyclic_count":
        int, "cyclic_count": int}}``. Every path is
        ``{nodes, edges, score, cumulative_lag, path_sign}`` where each edge
        carries ``from``, ``to``, ``rel_type``, ``relation``, ``kind``, ``role``,
        ``sign``, ``confidence``, ``temporal_lag`` and ``lag_plausibility``.
    """
    depth = max(1, max_depth)
    db = get_db()
    # Directed variable-length pattern; the anchor is the metric in both cases,
    # only the arrow direction flips. ``status` excludes deprecated edges via the
    # per-rel predicate in the WHERE clause (ALL over relationships(p)).
    if upstream:
        pattern = (
            f"(m:Metric {{metric_uid: $uid}})"
            f"<-[rels:{_TRAVERSE_REL_TYPES}*1..{depth}]-(other:Metric)"
        )
    else:
        pattern = (
            f"(m:Metric {{metric_uid: $uid}})"
            f"-[rels:{_TRAVERSE_REL_TYPES}*1..{depth}]->(other:Metric)"
        )
    # No acyclicity guard: cyclic paths must be RETURNED (reported, not broken),
    # so they are split out in Python below. Neo4j won't reuse a relationship
    # within one path, so the traversal stays bounded by ``max_depth``.
    rows = db.read(
        f"MATCH p = {pattern} "
        f"WHERE ALL(r IN relationships(p) WHERE {active_edge_predicate('r')}) "
        "AND ALL(r IN relationships(p) "
        "WHERE coalesce(r.confidence, 1.0) >= $min_conf) "
        "RETURN [n IN nodes(p) | n.metric_uid] AS node_uids, "
        "[r IN relationships(p) | {"
        "from: startNode(r).metric_uid, to: endNode(r).metric_uid, "
        "rel_type: type(r), relation: r.relation, role: r.role, "
        "confidence: r.confidence, temporal_lag: r.temporal_lag, "
        "lag_plausibility: r.lag_plausibility}] AS rels",
        uid=metric_uid,
        min_conf=min_confidence,
    )

    paths: list[dict[str, Any]] = []
    cyclic_paths: list[dict[str, Any]] = []
    for row in rows:
        node_uids = [str(u) for u in (row["node_uids"] or []) if u is not None]
        rels = row["rels"] or []
        edges = [_shape_hop(rel) for rel in rels]
        score, cumulative_lag, path_sign = _score_path(rels)
        path = {
            "nodes": node_uids,
            "edges": edges,
            "score": score,
            "cumulative_lag": cumulative_lag,
            "path_sign": path_sign,
        }
        # A path is cyclic when a node repeats on it (an INFLUENCES loop that the
        # old acyclicity guard used to drop); surface it separately, don't break.
        if len(set(node_uids)) != len(node_uids):
            cyclic_paths.append(path)
        else:
            paths.append(path)

    paths.sort(key=lambda p: p["score"], reverse=True)
    cyclic_paths.sort(key=lambda p: p["score"], reverse=True)
    paths = paths[:_ACYCLIC_PATH_CAP]
    cyclic_paths = cyclic_paths[:_CYCLIC_PATH_CAP]
    return {
        "paths": paths,
        "cyclic_paths": cyclic_paths,
        "summary": {
            "acyclic_count": len(paths),
            "cyclic_count": len(cyclic_paths),
        },
    }


@app.get("/api/traverse/upstream")
def traverse_upstream(
    metric_uid: str, max_depth: int = 4, min_confidence: float = 0.0
) -> dict[str, Any]:
    """Return upstream lineage paths (what the metric depends on / its causes).

    Walks ``DECOMPOSES_INTO`` + ``INFLUENCES`` edges INTO the metric, excluding
    deprecated edges. Paths are scored (product of per-hop ``confidence ×
    lag_plausibility``), signed (product of per-hop signs as ``path_sign``) and
    ranked descending; acyclic paths land in ``paths`` (~50-capped), loop-bearing
    paths in ``cyclic_paths`` (~25-capped), with an ``acyclic_count``/
    ``cyclic_count`` summary. ``min_confidence`` drops any path with a hop below
    that confidence (default ``0.0`` = no filtering). See :func:`_traverse`.
    """
    return _traverse(
        metric_uid, max_depth=max_depth, upstream=True, min_confidence=min_confidence
    )


@app.get("/api/traverse/downstream")
def traverse_downstream(
    metric_uid: str, max_depth: int = 4, min_confidence: float = 0.0
) -> dict[str, Any]:
    """Return downstream lineage paths (the metric's blast radius).

    Walks ``DECOMPOSES_INTO`` + ``INFLUENCES`` edges OUT of the metric, excluding
    deprecated edges. Paths are scored (product of per-hop ``confidence ×
    lag_plausibility``), signed (product of per-hop signs as ``path_sign``) and
    ranked descending; acyclic paths land in ``paths`` (~50-capped), loop-bearing
    paths in ``cyclic_paths`` (~25-capped), with an ``acyclic_count``/
    ``cyclic_count`` summary. ``min_confidence`` drops any path with a hop below
    that confidence (default ``0.0`` = no filtering). See :func:`_traverse`.
    """
    return _traverse(
        metric_uid, max_depth=max_depth, upstream=False, min_confidence=min_confidence
    )


# ---------------------------------------------------------------------------
# Column impact (warehouse-column blast radius)
# ---------------------------------------------------------------------------


@app.get("/api/column-impact")
def column_impact(column: str) -> dict[str, Any]:
    """Return every ``Metric`` whose ``source_columns`` list contains ``column``.

    A parameterised property-scan over ``Metric.source_columns`` — it answers
    "which metrics break if this warehouse column changes?" without walking any
    graph edges. For each matching metric the pinned fields (``metric_uid``,
    ``display_name``, ``mart_sources``, ``domain_ids``) are returned.

    Args:
        column: The warehouse source-column name to scan ``source_columns`` for.

    Returns:
        ``{column, count, metrics: [{metric_uid, display_name, mart_sources,
        domain_ids}]}``.
    """
    rows = get_db().read(
        "MATCH (m:Metric) WHERE $col IN m.source_columns "
        "RETURN m.metric_uid AS metric_uid, m.display_name AS display_name, "
        "m.mart_sources AS mart_sources, m.domain_ids AS domain_ids",
        col=column,
    )
    metrics = [
        {
            "metric_uid": row.get("metric_uid"),
            "display_name": row.get("display_name"),
            "mart_sources": _jsonable(row.get("mart_sources")),
            "domain_ids": _jsonable(row.get("domain_ids")),
        }
        for row in rows
    ]
    return {"column": column, "count": len(metrics), "metrics": metrics}


# ---------------------------------------------------------------------------
# Metric chart (shift-click chart panel)
# ---------------------------------------------------------------------------


def _chart_registry_entry(
    canonical_id: str | None, chart_id: str | None
) -> dict[str, Any] | None:
    """Return ONE chart-registry entry by ``canonical_id`` (or ``chart_id``).

    Reads ``docs/frd-docs/chart-registry.json`` and slices out a single matching
    entry — never the whole registry — mirroring the slice approach the MCP
    ``lookup_metric_notes`` tool uses (``canonical_id`` is the registry key, so it
    is tried first; ``chart_id`` is a value-scan fallback). A missing / unreadable
    registry file yields ``None`` (the endpoint reports ``found: false``).

    Args:
        canonical_id: The metric's ``canonical_id`` (the registry key
            ``dashboard_id:chart_id``).
        chart_id: The metric's ``chart_id`` (fallback when no canonical match).

    Returns:
        The matching registry entry dict, or ``None`` when nothing matches.
    """
    if not canonical_id and not chart_id:
        return None
    try:
        registry = json.loads(_CHART_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(registry, dict):
        return None
    if canonical_id and canonical_id in registry:
        return registry[canonical_id]
    if chart_id:
        for entry in registry.values():
            if isinstance(entry, dict) and str(entry.get("chart_id")) == chart_id:
                return entry
    return None


@app.get("/api/metric-chart")
def metric_chart(metric_uid: str) -> dict[str, Any]:
    """Return a metric's chart-registry entry + its series endpoint (passthrough).

    Backs the canvas shift-click chart panel: looks up the live ``Metric`` for its
    ``canonical_id`` / ``chart_id`` / ``chart_type`` / ``series_endpoint``, then
    resolves the single matching chart-registry entry via
    :func:`_chart_registry_entry` (registry-slice, not the whole file). The
    ``series_endpoint`` is a verbatim passthrough of the metric's stored value —
    this endpoint never calls the external BC_2 series API; the canvas fetches it
    when it renders the chart.

    Args:
        metric_uid: The live ``Metric.metric_uid``.

    Returns:
        ``{found, metric_uid, chart_type, chart_id, registry_entry,
        series_endpoint}``. ``found`` is ``False`` (with the other fields
        ``None``) when no such metric exists.
    """
    rows = get_db().read(
        "MATCH (m:Metric {metric_uid: $uid}) RETURN "
        "m.canonical_id AS canonical_id, m.chart_id AS chart_id, "
        "m.chart_type AS chart_type, m.series_endpoint AS series_endpoint LIMIT 1",
        uid=metric_uid,
    )
    if not rows:
        return {
            "found": False,
            "metric_uid": metric_uid,
            "chart_type": None,
            "chart_id": None,
            "registry_entry": None,
            "series_endpoint": None,
        }
    metric = rows[0]
    canonical_id = metric.get("canonical_id")
    chart_id = metric.get("chart_id")
    registry_entry = _chart_registry_entry(
        str(canonical_id) if canonical_id is not None else None,
        str(chart_id) if chart_id is not None else None,
    )
    return {
        "found": True,
        "metric_uid": metric_uid,
        "chart_type": metric.get("chart_type"),
        "chart_id": chart_id,
        "registry_entry": registry_entry,
        "series_endpoint": metric.get("series_endpoint"),
    }


@app.get("/api/dashboard-charts")
def dashboard_charts(dashboard_id: str) -> dict[str, Any]:
    """Return every chart-registry entry for one dashboard (read-only slice).

    Backs the canvas "shift-click a Dashboard → cluster its charts" reveal: a slice
    of ``docs/frd-docs/chart-registry.json`` filtered by ``dashboard_id`` — never a
    graph write. Each entry already carries ``chart_id`` / ``canonical_id`` /
    ``chart_type`` / ``formula`` / ``how_to_read`` / ``decisions_answered`` /
    ``narration_text`` / ``metric_key`` (so metric-less composite charts surface
    here too). A missing / unreadable registry file yields an empty list.

    Args:
        dashboard_id: The ``Dashboard.dashboard_id`` (registry ``dashboard_id``).

    Returns:
        ``{dashboard_id, count, charts}`` — ``charts`` sorted by chart id.
    """
    try:
        registry = json.loads(_CHART_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        registry = {}
    try:
        type_map = json.loads(_CHART_TYPE_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        type_map = {}
    charts: list[dict[str, Any]] = []
    if isinstance(registry, dict):
        for entry in registry.values():
            if not (isinstance(entry, dict) and str(entry.get("dashboard_id")) == dashboard_id):
                continue
            # Attach the visualization type (registry entries carry none).
            if not entry.get("chart_type"):
                mapped = type_map.get(str(entry.get("canonical_id"))) if isinstance(type_map, dict) else None
                if isinstance(mapped, dict) and mapped.get("chart_type"):
                    entry = {**entry, "chart_type": mapped["chart_type"]}
            charts.append(entry)
    charts.sort(key=lambda e: str(e.get("chart_id") or e.get("canonical_id") or ""))
    return {"dashboard_id": dashboard_id, "count": len(charts), "charts": charts}


# ---------------------------------------------------------------------------
# Active-campaign breakdown (runtime overlay — never persisted)
# ---------------------------------------------------------------------------


@app.get("/api/active-campaign-breakdown")
def active_campaign_breakdown(
    metric_uid: str, date_from: str, date_to: str
) -> dict[str, Any]:
    """Return the runtime ``active_campaigns`` COUNT breakdown for a platform metric.

    A read-only RUNTIME overlay for the canvas: it delegates to
    :func:`harness.marts.snowflake_reader.fetch_active_campaign_breakdown`, which
    reads the BC_2 Snowflake marts DIRECTLY (read-only) for the per-child
    active-campaign counts that make up ``metric_uid`` over ``[date_from,
    date_to]``, plus non-additive dimension cuts (ad-network-type / objective).
    This NEVER touches Neo4j and NEVER persists anything — the counts decorate the
    existing ``DECOMPOSES_INTO`` fan-out at request time, and changing the date
    range only re-fetches counts (it never creates, mutates or deprecates an
    edge). ``counts_by_metric_uid`` / ``zero_count_metric_uids`` are keyed by
    dot-form ``metric_uid`` (== the canvas graph node id), so they map straight
    onto nodes.

    Args:
        metric_uid: The anchor ``Metric.metric_uid`` (dot-form, e.g.
            ``blended.active_campaigns``). Forwarded to the reader as
            ``anchor_metric_uid`` (the HTTP param keeps the ``metric_uid`` name
            shared by every other endpoint).
        date_from: Inclusive ISO start date (``YYYY-MM-DD``).
        date_to: Inclusive ISO end date (``YYYY-MM-DD``).

    Returns:
        ``{anchor_metric_uid, date_from, date_to, counts_by_metric_uid,
        overlay_dims: {ad_network_type, objective}, zero_count_metric_uids,
        stale, freshness_notes, source_marts}``. The reader is contractually
        graceful: when Snowflake is unconfigured / unreachable it returns
        ``stale=True`` with empty counts rather than raising, so this endpoint
        never 5xx's on a warehouse outage (no try/except needed here).
    """
    return fetch_active_campaign_breakdown(
        anchor_metric_uid=metric_uid, date_from=date_from, date_to=date_to
    )


@app.get("/api/dashboards")
def dashboards() -> dict[str, list[dict[str, Any]]]:
    """List every dashboard with its prepass counts and whether it is ingested."""
    db = get_db()
    ingested_rows = db.read("MATCH (d:Dashboard) RETURN d.dashboard_id AS id")
    ingested_ids = {str(row["id"]) for row in ingested_rows if row["id"]}

    prepass = run_prepass()["dashboards"]
    out: list[dict[str, Any]] = []
    for dashboard_id in sorted(prepass):
        bucket = prepass[dashboard_id]
        out.append(
            {
                "dashboard_id": dashboard_id,
                "components": len(bucket["components"]),
                "metrics": len(bucket["metrics"]),
                "ingested": dashboard_id in ingested_ids,
            }
        )
    return {"dashboards": out}


@app.get("/api/proposals")
def proposals(run_id: str | None = None) -> dict[str, Any]:
    """Return a run's proposals (the latest run when ``run_id`` is omitted)."""
    resolved = run_id or latest_run_id()
    items = load_proposals(run_id=resolved) if resolved else []
    return {"run_id": resolved, "proposals": items}


@app.post("/api/proposals/approve-all")
def approve_all(body: ApplyBody) -> dict[str, Any]:
    """Approve every still-pending proposal in a run (the canvas "Approve all").

    A single bulk flip of ``proposed``/``pending`` -> ``approved`` for the whole
    run; the operator then applies them with ``POST /api/apply``. Returns the
    number approved. (This is a human bulk action on the visible queue, so it
    approves ALL pending proposals — including LLM ``INFLUENCES`` — unlike the
    CLI ``--auto-approve`` flag, which deliberately holds the LLM stage.)
    """
    approved = approve_all_pending(body.run_id)
    return {"run_id": body.run_id, "approved": approved}


@app.post("/api/proposals/{proposal_id}/review")
def review_proposal(proposal_id: str, body: ReviewBody) -> dict[str, Any]:
    """Approve / reject / edit a single proposal, mutating its review state.

    Semantics: ``edit`` persists the supplied ``payload`` AND moves the proposal
    to ``approved`` (an edit is an approval of the human-corrected payload, so it
    is immediately appliable via ``POST /api/apply`` and the applied node uses
    the edited payload). ``approve`` also persists an optional ``payload`` if one
    is included. ``reject`` records an optional reason.
    """
    action = body.action.lower()
    if action not in {"approve", "reject", "edit"}:
        raise HTTPException(status_code=400, detail=f"unknown action {body.action!r}")

    # `edit` is an approval of the corrected payload; `approve` may also carry an
    # edited payload, which we persist so the applied node uses it.
    state = {"approve": "approved", "reject": "rejected", "edit": "approved"}[action]
    payload = body.payload if action in {"edit", "approve"} else None
    updated = set_review_state(
        body.run_id, proposal_id, state, reason=body.reason, payload=payload
    )
    if not updated:
        # The proposal may live under a different run (e.g. an auto-approve batch
        # wrote it per-dashboard) — find it across all runs before giving up.
        updated = set_review_state_anywhere(
            proposal_id, state, reason=body.reason, payload=payload
        )
    if not updated:
        # Truly unknown / already-consumed proposal: a no-op, not an error, so
        # the canvas never shows a scary banner for a benign re-click.
        return {"ok": True, "state": state, "note": "proposal not pending (no-op)"}
    return {"ok": True, "state": state}


# ---------------------------------------------------------------------------
# Background ingest
# ---------------------------------------------------------------------------


def _spawn_and_stream(
    dashboards_arg: str,
    *,
    run_id: str,
    concurrency: int,
    auto_approve: bool,
) -> None:
    """Run ingestion as a SEPARATE OS PROCESS and republish its events.

    The agent SDK spawns ``claude --input-format stream-json`` and feeds the
    request over the child's stdin; that handshake only completes from a clean
    main-thread ``asyncio.run`` — driven from the server's loop (or a worker
    thread) the spawned ``claude`` blocks forever. So we run ingestion exactly
    how the working CLI does — its own process — and read the JSONL events it
    prints (prefixed with ``KGEVENT:``), forwarding each onto the SSE bus (which
    is thread-safe). Runs inside :func:`asyncio.to_thread`, so the blocking
    ``Popen``/pipe reads never touch the event loop.
    """
    # Spawn the EXACT invocation the working CLI uses ("uv run python -m ..."),
    # not sys.executable directly: uv sets up the environment the bundled
    # ``claude`` agent process needs; a bare venv-python spawn makes it hang.
    uv = shutil.which("uv") or "uv"
    cmd = [
        uv,
        "run",
        "python",
        "-m",
        "harness.ingest.run_subprocess",
        "--run-id",
        run_id,
        "--dashboards",
        dashboards_arg,
        "--concurrency",
        str(concurrency),
    ]
    if auto_approve:
        cmd.append("--auto-approve")

    # CRITICAL: strip the Claude-Code nesting-context env vars for the WHOLE
    # child chain (uv -> python -> bundled claude). Inherited CLAUDECODE /
    # CLAUDE_CODE_* make the agent SDK's bundled ``claude`` try to attach to this
    # parent Claude Code session and hang forever for a server-spawned process.
    # Cleaning at the Popen boundary (not just in-process) is what actually works.
    # Any real auth token (CLAUDE_CODE_OAUTH_TOKEN) is preserved.
    _keep = ("TOKEN", "OAUTH", "API", "KEY")
    child_env = {
        k: v
        for k, v in os.environ.items()
        if not (
            k in ("CLAUDECODE", "CLAUDE_EFFORT")
            or (k.startswith("CLAUDE_CODE_") and not any(s in k for s in _keep))
        )
    }

    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        cwd=str(REPO_ROOT),
        env=child_env,
        stdin=subprocess.DEVNULL,  # clean stdin; don't inherit the server's
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    saw_terminal = False
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.startswith(EVENT_PREFIX):
            continue  # incidental stdout (e.g. driver notifications) — ignore
        try:
            event = json.loads(line[len(EVENT_PREFIX) :])
        except json.JSONDecodeError:
            continue
        bus.publish(event)
        if event.get("type") in ("run_done", "error"):
            saw_terminal = True
    proc.wait()
    if not saw_terminal:
        err = (proc.stderr.read() if proc.stderr else "")[-800:]
        bus.emit(
            "error",
            run_id=run_id,
            message=f"ingest process exited rc={proc.returncode}: {err}",
        )


async def _run_ingest(
    dashboard_ids: list[str],
    *,
    run_id: str,
    concurrency: int,
    auto_approve: bool,
    is_all: bool,
) -> None:
    """Schedule the subprocess-backed ingest off the event loop."""
    dashboards_arg = "ALL" if is_all else ",".join(dashboard_ids)
    await asyncio.to_thread(
        _spawn_and_stream,
        dashboards_arg,
        run_id=run_id,
        concurrency=concurrency,
        auto_approve=auto_approve,
    )


@app.post("/api/ingest")
async def ingest(body: IngestBody) -> dict[str, str]:
    """Schedule a background ingestion run; events stream on ``/api/events``."""
    if body.all:
        dashboard_ids = all_dashboard_ids()
    elif body.dashboard_id:
        dashboard_ids = [body.dashboard_id]
    else:
        raise HTTPException(
            status_code=400, detail="provide either dashboard_id or all=true"
        )

    run_id = new_run_id()
    asyncio.create_task(
        _run_ingest(
            dashboard_ids,
            run_id=run_id,
            concurrency=body.concurrency,
            auto_approve=body.auto_approve,
            is_all=bool(body.all),
        )
    )
    return {"run_id": run_id}


@app.post("/api/apply")
async def apply_run(body: ApplyBody) -> dict[str, Any]:
    """Apply a run's approved proposals through the arbitration writer."""
    summary = await asyncio.to_thread(
        apply_mod.apply_approved, get_db(), body.run_id, emit=bus.emit
    )
    return summary


# ---------------------------------------------------------------------------
# Governance — Policy & Threshold authoring
# ---------------------------------------------------------------------------

#: Stdout marker the extract subprocess prints its one JSON result line with.
_EXTRACT_PREFIX = "KGEXTRACT:"


def _slug(text: str | None, *, fallback: str = "policy") -> str:
    """Lower-snake a label into an id-safe slug (``fallback`` when empty)."""
    out = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return out or fallback


def _governance_fields(
    model_cls: type[BaseModel], data: dict[str, Any] | None
) -> dict[str, Any]:
    """Keep only known, non-empty model fields from a loose request map."""
    allowed = set(model_cls.model_fields)
    return {
        k: v
        for k, v in (data or {}).items()
        if k in allowed and v is not None and v != ""
    }


def _write_governance(body: GovernanceBody) -> dict[str, Any]:
    """Write the Policy node(s) + shared Threshold and the governance edges (idempotent).

    A metric may carry several policies, all enforcing one shared Threshold:
    one ``HAS_THRESHOLD`` edge per metric, plus ``GOVERNS`` + ``ENFORCES_THRESHOLD``
    per policy. Runs off the event loop (blocking Neo4j writes). The metric need
    not exist: nodes are still written and any edge to an absent metric returns
    ``missing_endpoint`` (surfaced as a ``warning``), never a crash.
    """
    db = get_db()
    metric_uid = body.metric_uid
    tid = body.threshold_id or f"threshold:{metric_uid}:bands"

    tfields = _governance_fields(Threshold, body.threshold)
    tfields.update(
        threshold_id=tid,
        metric_id=metric_uid,
        population_status=tfields.get("population_status", "populated"),
    )
    t_res = write_node_model(db, Threshold(**tfields))

    # One HAS_THRESHOLD per metric; GOVERNS + ENFORCES_THRESHOLD per policy.
    edges: list[dict[str, Any]] = []
    triples: list[tuple[str, str, str, str, str]] = [
        ("HAS_THRESHOLD", "Metric", metric_uid, "Threshold", tid),
    ]

    resolved = body.resolved_policies()
    single = len(resolved) == 1  # only then may the caller pin policy_id explicitly
    policy_results: list[dict[str, Any]] = []
    for policy in resolved:
        pid = (
            body.policy_id
            if single and body.policy_id
            else f"policy:{metric_uid}:{_slug(policy.get('policy_name'))}"
        )
        pfields = _governance_fields(Policy, policy)
        pfields.update(
            policy_id=pid,
            applies_to_kind=pfields.get("applies_to_kind", "Metric"),
            metric_id=metric_uid,
            population_status=pfields.get("population_status", "populated"),
        )
        p_res = write_node_model(db, Policy(**pfields))
        bus.emit("node_written", label="Policy", key=pid)
        policy_results.append({"status": p_res["status"], "key": pid})
        triples.append(("GOVERNS", "Policy", pid, "Metric", metric_uid))
        triples.append(("ENFORCES_THRESHOLD", "Policy", pid, "Threshold", tid))

    for rel_type, fl, fk, tl, tk in triples:
        res = upsert_edge(
            db,
            rel_type=rel_type,
            from_label=fl,
            from_key=fk,
            to_label=tl,
            to_key=tk,
            props={"source_kind": "governance_ui"},
        )
        edges.append({"rel_type": rel_type, "status": res.get("status")})

    bus.emit("node_written", label="Threshold", key=tid)

    missing = [e["rel_type"] for e in edges if e["status"] == "missing_endpoint"]
    warning = (
        f"metric {metric_uid!r} not found; edges not drawn: {', '.join(missing)}"
        if missing
        else None
    )
    return {
        "status": "ok",
        "metric_uid": metric_uid,
        # ``policy`` (first) kept for back-compat; ``policies`` is the full list.
        "policy": policy_results[0],
        "policies": policy_results,
        "threshold": {"status": t_res["status"], "key": tid},
        "edges": edges,
        "warning": warning,
    }


@app.post("/api/governance")
async def create_governance(body: GovernanceBody) -> dict[str, Any]:
    """Author a Policy + Threshold against a metric (nodes + 3 edges, idempotent)."""
    try:
        return await asyncio.to_thread(_write_governance, body)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _run_governance_extract(request_json: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Spawn the LLM extract subprocess (clean env) and return its draft fields.

    The agent SDK's bundled ``claude`` only completes its stdin handshake from a
    clean main-thread ``asyncio.run`` in its own process — driven in-process under
    uvicorn it hangs forever (same constraint that retired ``/api/run-causal``).
    So we spawn ``uv run python -m harness.governance.extract_subprocess``, feed
    the request JSON on stdin, and read its single ``KGEXTRACT:`` result line.
    Runs inside :func:`asyncio.to_thread` so the blocking pipe I/O never touches
    the event loop.
    """
    uv = shutil.which("uv") or "uv"
    cmd = [uv, "run", "python", "-m", "harness.governance.extract_subprocess"]
    # Strip the Claude-Code nesting env for the whole child chain (preserving any
    # real auth token) — otherwise the bundled agent tries to attach to this
    # parent session and hangs. Mirrors `_spawn_and_stream`.
    _keep = ("TOKEN", "OAUTH", "API", "KEY")
    child_env = {
        k: v
        for k, v in os.environ.items()
        if not (
            k in ("CLAUDECODE", "CLAUDE_EFFORT")
            or (k.startswith("CLAUDE_CODE_") and not any(s in k for s in _keep))
        )
    }
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        cwd=str(REPO_ROOT),
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=request_json, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise HTTPException(status_code=504, detail="extraction timed out") from None
    for line in out.splitlines():
        if line.startswith(_EXTRACT_PREFIX):
            return json.loads(line[len(_EXTRACT_PREFIX) :])
    raise HTTPException(
        status_code=502,
        detail=f"extraction failed (rc={proc.returncode}): {err[-500:]}",
    )


@app.post("/api/governance/extract")
async def extract_governance(body: ExtractBody) -> dict[str, Any]:
    """LLM-parse pasted/uploaded text into draft ``{policy, threshold}`` fields.

    The draft is returned for the wizard to prefill — it is **not** written. The
    user reviews/edits, then ``POST /api/governance`` performs the write.
    """
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    request_json = body.model_dump_json()
    return await asyncio.to_thread(_run_governance_extract, request_json)


@app.post("/api/run-causal")
async def run_causal_endpoint(body: CausalBody) -> dict[str, Any]:
    """Removed — superseded by the agentic builder.

    The deterministic causal pass (formula / rollup / rare_seeds correlations)
    was removed when graph construction moved to LLM-driven, auto-approved
    building. Metric→metric edges (``DECOMPOSES_INTO`` + ``INFLUENCES``) are now
    built by the agentic builder (``harness.agentic`` / ``kg build``), not by an
    API trigger, so this endpoint returns ``501 Not Implemented``.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Deterministic causal pass removed; build the graph via the agentic "
            "builder (`kg build` / harness.agentic)."
        ),
    )


@app.post("/api/_event")
async def ingest_event(event: dict[str, Any]) -> dict[str, bool]:
    """Loopback hook: republish an event from an external ingest process.

    The reliable way to drive a live ingest is the CLI
    (``uv run kg ingest-all --emit-url http://127.0.0.1:8000/api/_event``),
    which runs as a normal terminal process (where the agent SDK works) and
    POSTs each event here so the canvas streams it live over ``/api/events``.
    """
    bus.publish(event)
    return {"ok": True}


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


@app.get("/api/events")
async def events(request: Request) -> Any:
    """Stream live canvas events to one client as Server-Sent Events."""
    return await event_stream(request)


def main() -> None:
    """Run the API under uvicorn (``python -m harness.api.server``)."""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
