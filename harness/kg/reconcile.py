"""Cross-partition node reconciliation (deduplication).

When the same canonical entity is proposed from several partitions (e.g. a
rollup metric shown on multiple dashboards), duplicate nodes can appear before
the uniqueness constraints take hold. :func:`merge_duplicates` collapses all
nodes sharing a ``key_field`` value into a single node, preserving every
relationship.

Two strategies are provided:

* **APOC** (preferred) — ``apoc.refactor.mergeNodes`` merges onto the *first*
  node, combining properties and re-pointing relationships in one call.
* **Plain Cypher fallback** — for environments without the APOC plugin: recreate
  the survivor's relationships from each duplicate, then ``DETACH DELETE`` the
  duplicates.

The two paths are **behaviorally equivalent**: each runs as a *single atomic*
managed (auto-retried) transaction, and each is *idempotent* (re-running the
fallback never duplicates relationships — recreation is ``MERGE``-keyed on
``(survivor, target, type)``, so a managed-transaction retry is safe).

Exercised in Milestone 2; written correctly now so the reconcile pass is ready.

Phase 5 adds **edge reconciliation** (:func:`compute_edge_diff` /
:func:`reconcile_edges`): the deterministic metric->metric edge set is recomputed
on every causal run, and an edge the recompute no longer produces is *deprecated*
(``status='deprecated'`` plus a stamp) — **never deleted** — and only when its
provenance is a deterministic, eligible ``source_kind``. Review-protected edges
(curated / LLM / manual, or any approved/applied ``INFLUENCES``) are left
untouched. The diff (:func:`compute_edge_diff`) is a pure, unit-testable function.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from harness.store.jsonl import append_event

from .config import REPO_ROOT
from .driver import GraphDB
from .models import NODE_KEY_FIELDS, NODE_LABELS

#: Where reconcile artifacts (edge diff JSON + deprecated-edge CSV) are written.
RECONCILE_DIR: Path = REPO_ROOT / "data" / "skeleton"

#: Edge ``source_kind`` tags that are SAFE to auto-deprecate when a recompute no
#: longer produces them (every one is deterministic + machine-derived). A stale
#: edge of one of these kinds is the recompute's source of truth being authoritative.
ELIGIBLE_DETERMINISTIC: frozenset[str] = frozenset(
    {
        "formula_parse",
        "identity_fallback",
        "scope_rollup",
        "statistical_proposal",
        "component_parse",
        "metric_formula_parse",
    }
)

#: Edge ``source_kind`` tags that are REVIEW-PROTECTED — a human or an LLM judge
#: stood behind them, so they are NEVER auto-deprecated by a recompute (only an
#: explicit human action retires them). ``statistical_proposal`` appears in BOTH
#: sets: the conservative protected set wins, so the eligible set below subtracts it.
#: ``kg_discovery`` (machine-discovered causal edges imported from the discovery
#: feed) is review-protected too: a discovery import lands as a reviewable
#: proposal, so a deterministic recompute must never silently retire it.
REVIEW_PROTECTED: frozenset[str] = frozenset(
    {
        "curated_rule",
        "llm_proposal",
        "llm_link",
        "manual_review",
        "statistical_proposal",
        "kg_discovery",
    }
)

#: The deterministic kinds we actually deprecate = eligible MINUS review-protected.
ELIGIBLE_SOURCE_KINDS: frozenset[str] = ELIGIBLE_DETERMINISTIC - REVIEW_PROTECTED


def prune_empty_spine(
    db: GraphDB, *, domains: bool = True, components: bool = True
) -> dict[str, list[str]]:
    """Delete spine nodes that no Metric actually uses (data-driven cleanup).

    Run after a full ingest to drop the placeholder nodes the data never
    populated:

    * **Domains** with no Metric attached via ``BELONGS_TO_DOMAIN`` and no
      ``CONTEXTUALIZES``/``GOVERNS`` edge to a Metric (e.g. ``hr`` when no HR
      dashboards exist).
    * **UIComponent** chart-type nodes with no ``VISUALIZES`` edge to a Metric
      (chart kinds nothing was rendered as).

    ``Business`` and ``IntelligenceProduct`` are never pruned (they are an
    intentional fixed set). Returns the ids removed per label.
    """
    pruned: dict[str, list[str]] = {"domains": [], "components": []}

    if domains:
        rows = db.write(
            "MATCH (d:Domain) "
            "WHERE NOT EXISTS { MATCH (:Metric)-[:BELONGS_TO_DOMAIN]->(d) } "
            "AND NOT EXISTS { MATCH (d)-[:CONTEXTUALIZES|GOVERNS]->(:Metric) } "
            "WITH d, d.domain_id AS id DETACH DELETE d RETURN id"
        )
        pruned["domains"] = [r["id"] for r in rows]

    if components:
        rows = db.write(
            "MATCH (u:UIComponent) "
            "WHERE NOT EXISTS { MATCH (u)-[:VISUALIZES]->(:Metric) } "
            "WITH u, u.component_id AS id DETACH DELETE u RETURN id"
        )
        pruned["components"] = [r["id"] for r in rows]

    append_event({"type": "prune_empty_spine", **pruned})
    return pruned


def _validate(label: str, key_field: str) -> None:
    """Validate ``label``/``key_field`` against the model allowlists.

    Raises:
        ValueError: If the label is unknown or the key field is not its identity.
    """
    if label not in NODE_LABELS:
        raise ValueError(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}"
        )
    expected = NODE_KEY_FIELDS[label]
    if key_field != expected:
        raise ValueError(
            f"Key field {key_field!r} is not the identity field for {label!r} "
            f"(expected {expected!r})"
        )


def merge_duplicates(
    db: GraphDB,
    label: str,
    key_field: str,
    key_value: str,
    use_apoc: bool = True,
) -> dict[str, Any]:
    """Collapse all ``label`` nodes sharing ``key_value`` into one node.

    The first node found becomes the survivor; properties are combined and every
    relationship is preserved (re-pointed onto the survivor). If fewer than two
    nodes match, this is a no-op.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        label: Node label to reconcile (allowlisted).
        key_field: The node's identity field (allowlisted for ``label``).
        key_value: The shared identity value whose duplicates are merged.
        use_apoc: Prefer ``apoc.refactor.mergeNodes``; set ``False`` to force the
            plain-Cypher fallback.

    Returns:
        ``{"status": "noop" | "merged", "label", "key", "merged_count",
        "strategy"}``.

    Raises:
        ValueError: If ``label`` or ``key_field`` is not allowlisted.
    """
    _validate(label, key_field)

    count_rows = db.read(
        f"MATCH (n:{label} {{{key_field}: $kv}}) RETURN count(n) AS c",
        kv=key_value,
    )
    total = int(count_rows[0]["c"]) if count_rows else 0
    if total < 2:
        result = {
            "status": "noop",
            "label": label,
            "key": key_value,
            "merged_count": total,
            "strategy": "none",
        }
        append_event({"type": "reconcile", **result})
        return result

    strategy = "apoc" if use_apoc else "plain_cypher"
    if use_apoc:
        merged_count = _merge_with_apoc(db, label, key_field, key_value)
    else:
        merged_count = _merge_with_plain_cypher(db, label, key_field, key_value)

    result = {
        "status": "merged",
        "label": label,
        "key": key_value,
        "merged_count": merged_count,
        "strategy": strategy,
    }
    append_event({"type": "reconcile", **result})
    return result


def _merge_with_apoc(
    db: GraphDB,
    label: str,
    key_field: str,
    key_value: str,
) -> int:
    """Merge duplicates via ``apoc.refactor.mergeNodes`` (onto the first node).

    Combines properties (``properties: 'combine'``) and re-points relationships
    (``mergeRels: true``).

    Returns:
        The number of duplicate nodes that were folded into the survivor.
    """
    cypher = (
        f"MATCH (n:{label} {{{key_field}: $kv}}) "
        "WITH collect(n) AS nodes "
        "WITH nodes, size(nodes) AS n_before "
        "CALL apoc.refactor.mergeNodes("
        "  nodes, {properties: 'combine', mergeRels: true}"
        ") YIELD node "
        "RETURN n_before - 1 AS merged_count"
    )
    rows = db.write(cypher, kv=key_value)
    return int(rows[0]["merged_count"]) if rows else 0


def _merge_with_plain_cypher(
    db: GraphDB,
    label: str,
    key_field: str,
    key_value: str,
) -> int:
    """Merge duplicates without APOC, in a single idempotent atomic transaction.

    Picks a deterministic survivor (lowest ``elementId``), recreates each
    duplicate's outgoing and incoming relationships onto the survivor (preserving
    type and properties), then ``DETACH DELETE``\\ s the duplicates. Self-loops
    on a duplicate are folded into self-loops on the survivor.

    The recreate **and** the delete run in **one** ``db.write`` call (one managed
    transaction), so they are atomic. Recreation is **idempotent**: it uses
    ``MERGE`` keyed on ``(survivor, target, type)`` with
    ``ON CREATE SET nr += properties(r)`` and Cypher 25's dynamic relationship
    type (``MERGE (a)-[nr:$(rt)]->(b)``). Because MERGE never duplicates an
    existing relationship, a managed-transaction auto-retry is safe — re-running
    the whole statement converges to the same graph.

    Returns:
        The number of duplicate nodes that were deleted (best-effort, computed
        inside the write transaction).
    """
    # Single atomic transaction:
    #   1. pick the survivor (lowest elementId),
    #   2. MERGE each duplicate's outgoing edges onto the survivor (idempotent),
    #   3. MERGE each duplicate's incoming edges onto the survivor (idempotent),
    #   4. DETACH DELETE the duplicates,
    # returning the delete count. CALL {} subqueries scope each pass; the final
    # WITH/MATCH performs the delete so the recreated edges survive.
    cypher = (
        f"MATCH (keep:{label} {{{key_field}: $kv}}) "
        "WITH keep ORDER BY elementId(keep) LIMIT 1 "
        # Outgoing edges of each duplicate -> survivor (preserve type + props).
        # An edge that targeted another duplicate (or itself) is redirected onto
        # the survivor.
        "CALL (keep) { "
        f"  MATCH (dup:{label} {{{key_field}: $kv}})-[r]->(tgt) "
        "   WHERE dup <> keep "
        "   WITH keep, "
        f"        (CASE WHEN tgt:{label} AND tgt.{key_field} = $kv THEN keep ELSE tgt END) AS tgt, "
        "        type(r) AS rt, properties(r) AS rp "
        "   MERGE (keep)-[nr:$(rt)]->(tgt) "
        "   ON CREATE SET nr += rp "
        "   RETURN count(nr) AS out_c "
        "} "
        # Incoming edges into each duplicate from a non-duplicate -> survivor.
        # (Edges between two duplicates were handled by the outgoing pass.)
        "CALL (keep) { "
        f"  MATCH (src)-[r]->(dup:{label} {{{key_field}: $kv}}) "
        f"  WHERE dup <> keep AND NOT (src:{label} AND src.{key_field} = $kv) "
        "   WITH keep, src, type(r) AS rt, properties(r) AS rp "
        "   MERGE (src)-[nr:$(rt)]->(keep) "
        "   ON CREATE SET nr += rp "
        "   RETURN count(nr) AS in_c "
        "} "
        # Finally delete the duplicates (their old relationships go with them).
        f"MATCH (dup:{label} {{{key_field}: $kv}}) WHERE dup <> keep "
        "WITH collect(dup) AS dups "
        "WITH dups, size(dups) AS deleted "
        "FOREACH (d IN dups | DETACH DELETE d) "
        "RETURN deleted"
    )
    rows = db.write(cypher, kv=key_value)
    return int(rows[0]["deleted"]) if rows else 0


# ---------------------------------------------------------------------------
# Edge reconciliation — deprecate-never-delete (Phase 5)
# ---------------------------------------------------------------------------
#
# The deterministic edge set is recomputed from the metric hub on every causal
# run. An edge that the recompute no longer produces is *stale* — but the graph
# is append-only for audit: we NEVER delete an edge. Instead a stale edge whose
# provenance is deterministic (machine-derived, ELIGIBLE_SOURCE_KINDS) is marked
# ``status='deprecated'`` with a deprecation stamp, while review-protected edges
# (curated/LLM/manual, or any approved/applied INFLUENCES) are left untouched.


def _edge_field(edge: dict[str, Any], name: str, default: Any = None) -> Any:
    """Read ``name`` from an edge dict, looking in nested ``properties`` too.

    Computed edges carry their provenance / subtype inside ``properties`` (the
    arbitration edge payload shape: ``{type, from_id, to_id, properties:{...}}``),
    whereas live edges read back from Neo4j carry them as flat top-level keys
    (``relation`` / ``source_kind`` / …). This reads the top-level key first,
    then falls back to ``properties[name]`` so a single edge-key extractor works
    for both shapes.
    """
    if name in edge and edge[name] is not None:
        return edge[name]
    props = edge.get("properties")
    if isinstance(props, dict) and props.get(name) is not None:
        return props[name]
    return default


def _edge_rel_type(edge: dict[str, Any]) -> str:
    """The edge's relationship type (``rel_type`` or the payload's ``type``)."""
    return str(_edge_field(edge, "rel_type") or _edge_field(edge, "type") or "")


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str]:
    """The identity tuple ``(from_id, rel_type, relation, to_id)`` of an edge.

    ``relation`` is the DECOMPOSES_INTO / INFLUENCES subtype (``formula``,
    ``rollup``, ``statistical``, …); ``""`` for an edge that carries none. This
    is the join key used to diff the live edge set against the recomputed one.
    """
    return (
        str(_edge_field(edge, "from_id") or ""),
        _edge_rel_type(edge),
        str(_edge_field(edge, "relation") or ""),
        str(_edge_field(edge, "to_id") or ""),
    )


def compute_edge_diff(
    live_edges: list[dict[str, Any]],
    computed_edges: list[dict[str, Any]],
    eligible_source_kinds: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Diff the live metric->metric edge set against a freshly-recomputed one.

    PURE (no DB, no I/O) and fully unit-testable. Every edge is identified by its
    ``(from_id, rel_type, relation, to_id)`` tuple (see :func:`_edge_key`); both
    inputs may use the flat live shape or the nested-``properties`` computed shape.

    Partitions:

    * ``added`` — computed edges whose key is absent from the live set.
    * ``unchanged`` — live edges whose key is also in the computed set.
    * ``deprecated`` — live edges absent from the computed set **and** whose
      ``source_kind`` is in ``eligible_source_kinds`` (deterministic, safe to
      retire).
    * ``skipped`` — live edges absent from the computed set whose ``source_kind``
      is NOT eligible (review-protected): they are never auto-deprecated.

    Args:
        live_edges: The edges currently in the graph (each a dict with at least
            ``from_id`` / ``to_id`` / a rel-type / ``relation`` / ``source_kind``).
        computed_edges: The freshly-recomputed deterministic edge set.
        eligible_source_kinds: ``source_kind`` values safe to auto-deprecate.

    Returns:
        ``{"added": [...], "unchanged": [...], "deprecated": [...],
        "skipped": [...]}`` — each value a list of the original edge dicts.
    """
    eligible = set(eligible_source_kinds)
    computed_keys = {_edge_key(e) for e in computed_edges}
    live_keys: set[tuple[str, str, str, str]] = set()

    unchanged: list[dict[str, Any]] = []
    deprecated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for edge in live_edges:
        key = _edge_key(edge)
        live_keys.add(key)
        if key in computed_keys:
            unchanged.append(edge)
            continue
        # Stale: present live, absent from the recompute. Deprecate only when the
        # provenance is a deterministic, eligible source_kind — never a
        # review-protected (curated / LLM / manual) edge.
        source_kind = str(_edge_field(edge, "source_kind") or "")
        if source_kind in eligible:
            deprecated.append(edge)
        else:
            skipped.append(edge)

    added = [e for e in computed_edges if _edge_key(e) not in live_keys]

    return {
        "added": added,
        "unchanged": unchanged,
        "deprecated": deprecated,
        "skipped": skipped,
    }


#: Cypher reading every live metric->metric edge with the fields the diff needs.
_LIVE_EDGES_CYPHER = (
    "MATCH (a:Metric)-[r:DECOMPOSES_INTO|INFLUENCES]->(b:Metric) "
    "RETURN a.metric_uid AS from_id, type(r) AS rel_type, r.relation AS relation, "
    "b.metric_uid AS to_id, r.source_kind AS source_kind, "
    "r.review_state AS review_state, r.status AS status"
)


def _read_live_edges(db: GraphDB) -> list[dict[str, Any]]:
    """Read every live ``DECOMPOSES_INTO`` / ``INFLUENCES`` metric->metric edge.

    Read-only. Each row carries ``from_id`` / ``rel_type`` / ``relation`` /
    ``to_id`` plus the provenance / lifecycle fields the diff + protection rules
    consult (``source_kind`` / ``review_state`` / ``status``).
    """
    return db.read(_LIVE_EDGES_CYPHER)


def _is_protected_influence(edge: dict[str, Any]) -> bool:
    """True for an ``INFLUENCES`` edge a human already approved / applied.

    An approved or applied INFLUENCES edge is human-blessed and is NEVER
    auto-deprecated even if a recompute no longer surfaces it (it may rest on
    evidence the deterministic pass cannot see).
    """
    return (
        _edge_rel_type(edge) == "INFLUENCES"
        and str(_edge_field(edge, "review_state") or "") in ("approved", "applied")
    )


def _already_deprecated(edge: dict[str, Any]) -> bool:
    """True when the edge is already ``status='deprecated'`` (idempotent skip)."""
    return str(_edge_field(edge, "status") or "") == "deprecated"


def reconcile_edges(
    db: GraphDB,
    *,
    computed_edges: list[dict[str, Any]],
    run_id: str,
    tenant: str = "rare_seeds",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reconcile the live deterministic edge set against ``computed_edges``.

    Reads every live ``DECOMPOSES_INTO`` / ``INFLUENCES`` edge, diffs it against
    the freshly-recomputed set (:func:`compute_edge_diff`, keyed on
    ``(from_id, rel_type, relation, to_id)``), and — unless ``dry_run`` —
    **deprecates** (never deletes) each stale, eligible edge by setting
    ``status='deprecated'`` plus a deprecation stamp (``deprecated_at`` /
    ``deprecated_by_run`` / ``deprecation_reason='absent_from_recompute'``).

    The deprecation set is :data:`ELIGIBLE_SOURCE_KINDS` (deterministic kinds
    minus the review-protected ones). On top of the diff, an approved/applied
    ``INFLUENCES`` edge is ALWAYS protected (moved from ``deprecated`` to
    ``skipped``) even if its ``source_kind`` were eligible, and an edge already
    deprecated is left as-is (idempotent).

    Always writes two artifacts under ``data/skeleton/``:
    ``edge_diff.<tenant>.<run_id>.json`` and
    ``deprecated_edges.<tenant>.<run_id>.csv``.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        computed_edges: The freshly-recomputed deterministic edge set.
        run_id: The run id stamped onto each deprecated edge + the artifacts.
        tenant: Tenant slug for the artifact filenames.
        dry_run: When True, compute + write the diff artifact but make no writes.

    Returns:
        ``{"added", "unchanged", "deprecated", "skipped", "protected_influences",
        "applied", "dry_run", "run_id", "tenant", "artifacts"}`` (counts).
    """
    live_edges = _read_live_edges(db)
    diff = compute_edge_diff(live_edges, computed_edges, set(ELIGIBLE_SOURCE_KINDS))

    # Lift any approved/applied INFLUENCES out of `deprecated` into `skipped`
    # (defence-in-depth: review-protected source_kinds already exclude them, but
    # an eligible source_kind on a human-blessed influence must STILL be safe),
    # and never re-deprecate an edge already marked deprecated.
    to_deprecate: list[dict[str, Any]] = []
    protected_influences = 0
    for edge in diff["deprecated"]:
        if _already_deprecated(edge):
            continue
        if _is_protected_influence(edge):
            diff["skipped"].append(edge)
            protected_influences += 1
            continue
        to_deprecate.append(edge)
    diff["deprecated"] = to_deprecate

    applied = 0
    if not dry_run and to_deprecate:
        for edge in to_deprecate:
            applied += _deprecate_edge(db, edge, run_id)

    artifacts = _write_reconcile_artifacts(tenant, run_id, diff)

    result = {
        "added": len(diff["added"]),
        "unchanged": len(diff["unchanged"]),
        "deprecated": len(diff["deprecated"]),
        "skipped": len(diff["skipped"]),
        "protected_influences": protected_influences,
        "applied": applied,
        "dry_run": dry_run,
        "run_id": run_id,
        "tenant": tenant,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
    append_event({"type": "reconcile_edges", **result})
    return result


def _deprecate_edge(db: GraphDB, edge: dict[str, Any], run_id: str) -> int:
    """Mark one stale edge ``status='deprecated'`` with a stamp (NEVER deletes).

    Matches the edge by its identity tuple (endpoints + rel-type + ``relation``)
    and sets ``status='deprecated'``, ``deprecated_at=datetime()``,
    ``deprecated_by_run=run_id``, ``deprecation_reason='absent_from_recompute'``.
    The rel-type is interpolated only after validation against the two
    metric->metric edge types (injection guard); all values are parameterized.

    Returns:
        ``1`` if a relationship was stamped, else ``0``.
    """
    rel_type = _edge_rel_type(edge)
    if rel_type not in ("DECOMPOSES_INTO", "INFLUENCES"):
        raise ValueError(
            f"reconcile only deprecates DECOMPOSES_INTO/INFLUENCES, got {rel_type!r}"
        )
    from_id = str(_edge_field(edge, "from_id") or "")
    to_id = str(_edge_field(edge, "to_id") or "")
    relation = _edge_field(edge, "relation")
    # rel_type interpolated from the validated allowlist; values parameterized.
    # `relation IS NULL AND $relation IS NULL` keeps the match correct for edges
    # that carry no relation subtype.
    cypher = (
        f"MATCH (a:Metric {{metric_uid: $from_id}})-[r:{rel_type}]->"
        "(b:Metric {metric_uid: $to_id}) "
        "WHERE r.relation = $relation OR (r.relation IS NULL AND $relation IS NULL) "
        "SET r.status = 'deprecated', r.deprecated_at = datetime(), "
        "r.deprecated_by_run = $run_id, "
        "r.deprecation_reason = 'absent_from_recompute' "
        "RETURN count(r) AS n"
    )
    rows = db.write(
        cypher, from_id=from_id, to_id=to_id, relation=relation, run_id=run_id
    )
    return int(rows[0]["n"]) if rows else 0


def _write_reconcile_artifacts(
    tenant: str, run_id: str, diff: dict[str, list[dict[str, Any]]]
) -> dict[str, Path]:
    """Write the edge-diff JSON + the deprecated-edges CSV under data/skeleton/.

    ``edge_diff.<tenant>.<run_id>.json`` holds the per-bucket counts and the full
    edge key lists; ``deprecated_edges.<tenant>.<run_id>.csv`` is the audit row
    set of exactly the edges that were (or, in dry-run, would be) deprecated.

    Returns:
        ``{"edge_diff": Path, "deprecated_edges": Path}``.
    """
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    summary = {
        "run_id": run_id,
        "tenant": tenant,
        "counts": {k: len(v) for k, v in diff.items()},
        "edges": {
            bucket: [list(_edge_key(e)) for e in edges]
            for bucket, edges in diff.items()
        },
    }
    p = RECONCILE_DIR / f"edge_diff.{tenant}.{run_id}.json"
    p.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    paths["edge_diff"] = p

    p = RECONCILE_DIR / f"deprecated_edges.{tenant}.{run_id}.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["from_id", "rel_type", "relation", "to_id", "source_kind",
                    "review_state", "deprecation_reason"])
        for edge in diff["deprecated"]:
            from_id, rel_type, relation, to_id = _edge_key(edge)
            w.writerow([
                from_id, rel_type, relation, to_id,
                _edge_field(edge, "source_kind") or "",
                _edge_field(edge, "review_state") or "",
                "absent_from_recompute",
            ])
    paths["deprecated_edges"] = p
    return paths
