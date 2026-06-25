"""Arbitration writer — the SINGLE component that mutates the graph.

Every node/edge write in the harness flows through this module. It enforces the
schema's write discipline:

* **Idempotent upserts** via ``MERGE`` keyed on the node's identity field.
* **Injection guard** — labels and key fields are interpolated into Cypher (they
  cannot be parameterized) but ONLY after validation against the model
  allowlists (:data:`~harness.kg.models.NODE_LABELS`,
  :data:`~harness.kg.models.NODE_KEY_FIELDS`,
  :data:`~harness.kg.models.EDGE_TYPES`). All *values* are passed as ``$params``.
* **None-stripping** — ``SET n += $props`` deletes any property whose value is
  ``None``, so ``None`` values are removed before sending.
* **Reliable created-vs-updated** detection via a pre-``MERGE``
  ``OPTIONAL MATCH`` in the *same* query (managed-transaction safe).
* **Event logging** — every write appends to ``events.jsonl``.

Keeping the writer sequential and single-source, combined with the uniqueness
constraints (``harness.kg.schema``), makes duplicate nodes and write races
structurally impossible (implementation plan sections 2, 5c).
"""

from __future__ import annotations

from typing import Any

from harness.store.jsonl import append_event

from .driver import GraphDB
from .models import EDGE_TYPES, NODE_KEY_FIELDS, NODE_LABELS


def _validate_label(label: str) -> None:
    """Raise ``ValueError`` if ``label`` is not an allowlisted node label."""
    if label not in NODE_LABELS:
        raise ValueError(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}"
        )


def _validate_key_field(label: str, key_field: str) -> None:
    """Raise ``ValueError`` if ``key_field`` is not ``label``'s identity field."""
    expected = NODE_KEY_FIELDS[label]
    if key_field != expected:
        raise ValueError(
            f"Key field {key_field!r} is not the identity field for {label!r} "
            f"(expected {expected!r})"
        )


def _validate_relation(rel_type, props):
    """Raise ``ValueError`` if an edge's ``relation`` subtype is not allowed.

    Couples the two metric->metric edge types to their permitted relation
    vocabularies (:data:`~harness.kg.models.DECOMPOSES_RELATIONS` /
    :data:`~harness.kg.models.INFLUENCES_RELATIONS`), so a typo'd subtype is
    rejected at the single writer instead of silently persisting. A ``None``/
    empty props map or an edge with no ``relation`` is left untouched (spine /
    governance / RBAC edges carry no ``relation``).
    """
    if not props:
        return
    relation = props.get("relation")
    if relation is None:
        return
    from harness.kg.models import DECOMPOSES_RELATIONS, INFLUENCES_RELATIONS
    if rel_type == "DECOMPOSES_INTO" and relation not in DECOMPOSES_RELATIONS:
        raise ValueError(f"invalid DECOMPOSES_INTO relation: {relation!r}")
    if rel_type == "INFLUENCES" and relation not in INFLUENCES_RELATIONS:
        raise ValueError(f"invalid INFLUENCES relation: {relation!r}")


def _validate_edge_props(props):
    """Raise ``ValueError`` if an edge's structural ``role`` is not allowed.

    Structural (``DECOMPOSES_INTO``) edges carry a ``role`` that fixes a
    component's part in its parent's formula (:data:`~harness.kg.models.\
EDGE_ROLES`); a typo'd role is rejected at the single writer instead of
    silently persisting (same pattern as :func:`_validate_relation`). A
    ``None``/empty props map or an edge with no ``role`` is left untouched
    (spine / governance / RBAC / causal edges carry no ``role``).
    """
    if not props:
        return
    role = props.get("role")
    if role is None:
        return
    from harness.kg.models import EDGE_ROLES
    if role not in EDGE_ROLES:
        raise ValueError(
            f"invalid edge role {role!r}; expected one of {sorted(EDGE_ROLES)}"
        )


def upsert_node(
    db: GraphDB,
    *,
    label: str,
    key_field: str,
    key_value: str,
    props: dict[str, Any],
    source_kind: str = "manual_review",
    created_by: str = "cli",
    review_state: str = "active",
) -> dict[str, Any]:
    """Idempotently upsert a single node, returning created-vs-updated status.

    The ``label`` and ``key_field`` are validated against the model allowlists
    before being interpolated into Cypher (injection guard); every value goes
    through ``$params``. ``None`` values are stripped so ``SET n += $props``
    never deletes a property. Provenance defaults (``source_kind``,
    ``created_by``, ``review_state``) are set only when absent from ``props``.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        label: Target node label (must be in
            :data:`~harness.kg.models.NODE_LABELS`).
        key_field: The node's identity field (must equal
            :data:`~harness.kg.models.NODE_KEY_FIELDS`\\ ``[label]``).
        key_value: The identity value to merge on.
        props: Property map to set on create and on match.
        source_kind: Default provenance source kind if not already in ``props``.
        created_by: Default author if not already in ``props``.
        review_state: Default review state if not already in ``props``.

    Returns:
        ``{"status": "created" | "updated", "label", "key", "props"}``.

    Raises:
        ValueError: If ``label`` or ``key_field`` is not allowlisted.
    """
    _validate_label(label)
    _validate_key_field(label, key_field)

    # Strip None (SET n += $props would otherwise delete those properties).
    clean: dict[str, Any] = {k: v for k, v in props.items() if v is not None}
    # The identity property must always be present and consistent.
    clean[key_field] = key_value
    # Provenance defaults only when the caller did not supply them.
    clean.setdefault("source_kind", source_kind)
    clean.setdefault("created_by", created_by)
    clean.setdefault("review_state", review_state)

    # label/key_field interpolated (validated, allowlisted); value parameterized.
    cypher = (
        f"OPTIONAL MATCH (e:{label} {{{key_field}: $kv}}) "
        "WITH e IS NOT NULL AS existed "
        f"MERGE (n:{label} {{{key_field}: $kv}}) "
        "ON CREATE SET n += $props, n.created_at = datetime(), n.updated_at = datetime() "
        "ON MATCH SET n += $props, n.updated_at = datetime() "
        "RETURN existed AS existed"
    )
    rows = db.write(cypher, kv=key_value, props=clean)
    existed = bool(rows[0]["existed"]) if rows else False
    status = "updated" if existed else "created"

    append_event(
        {
            "type": "node_upsert",
            "status": status,
            "label": label,
            "key_field": key_field,
            "key": key_value,
            "source_kind": clean.get("source_kind"),
            "created_by": clean.get("created_by"),
            "review_state": clean.get("review_state"),
        }
    )
    return {"status": status, "label": label, "key": key_value, "props": clean}


def upsert_edge(
    db: GraphDB,
    *,
    rel_type: str,
    from_label: str,
    from_key: str,
    to_label: str,
    to_key: str,
    props: dict[str, Any] | None = None,
    from_key_field: str | None = None,
    to_key_field: str | None = None,
) -> dict[str, Any]:
    """Idempotently upsert a relationship between two existing nodes.

    Both endpoints are matched by their identity field; the relationship is
    ``MERGE``\\ d (so re-running is a no-op on the edge identity). ``rel_type``
    and both labels are validated against the model allowlists before being
    interpolated (injection guard); ``None`` values in ``props`` are stripped.

    If either endpoint does not exist, no edge is written and a
    ``missing_endpoint`` status is returned (the call never crashes).

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        rel_type: Relationship type (must be in
            :data:`~harness.kg.models.EDGE_TYPES`).
        from_label: Source node label (allowlisted).
        from_key: Source node identity value.
        to_label: Target node label (allowlisted).
        to_key: Target node identity value.
        props: Optional edge properties (set on create and on match).
        from_key_field: Override for the source key field; defaults to
            :data:`~harness.kg.models.NODE_KEY_FIELDS`\\ ``[from_label]``.
        to_key_field: Override for the target key field; defaults to
            :data:`~harness.kg.models.NODE_KEY_FIELDS`\\ ``[to_label]``.

    Returns:
        ``{"status": "created" | "updated" | "missing_endpoint", "rel_type",
        "from", "to"}`` (``missing_endpoint`` also lists which endpoints exist).

    Raises:
        ValueError: If ``rel_type`` or either label/key field is not allowlisted.
    """
    if rel_type not in EDGE_TYPES:
        raise ValueError(
            f"Unknown edge type {rel_type!r}; expected one of {sorted(EDGE_TYPES)}"
        )
    _validate_relation(rel_type, props)
    _validate_edge_props(props)
    _validate_label(from_label)
    _validate_label(to_label)

    src_field = from_key_field or NODE_KEY_FIELDS[from_label]
    dst_field = to_key_field or NODE_KEY_FIELDS[to_label]
    _validate_key_field(from_label, src_field)
    _validate_key_field(to_label, dst_field)

    clean: dict[str, Any] = {k: v for k, v in (props or {}).items() if v is not None}

    # Detect missing endpoints first so we never silently create dangling edges.
    # The same pre-MERGE OPTIONAL MATCH also reads any EXISTING edge's lifecycle +
    # provenance (``review_state`` / ``source_kind``) so the discovery-import guard
    # below can refuse to clobber a human-reviewed edge.
    presence = db.read(
        f"OPTIONAL MATCH (a:{from_label} {{{src_field}: $from_key}}) "
        f"OPTIONAL MATCH (b:{to_label} {{{dst_field}: $to_key}}) "
        f"OPTIONAL MATCH (a)-[er:{rel_type}]->(b) "
        "RETURN a IS NOT NULL AS from_exists, b IS NOT NULL AS to_exists, "
        "er IS NOT NULL AS edge_exists, er.review_state AS review_state, "
        "er.source_kind AS source_kind",
        from_key=from_key,
        to_key=to_key,
    )
    from_exists = bool(presence[0]["from_exists"]) if presence else False
    to_exists = bool(presence[0]["to_exists"]) if presence else False
    if not (from_exists and to_exists):
        result = {
            "status": "missing_endpoint",
            "rel_type": rel_type,
            "from": {"label": from_label, "key": from_key, "exists": from_exists},
            "to": {"label": to_label, "key": to_key, "exists": to_exists},
        }
        append_event({"type": "edge_upsert", **result})
        return result

    # Discovery-import guard: a ``kg_discovery`` write must NEVER overwrite the
    # relation / confidence / source_kind of an edge a human already approved or
    # applied. Instead it only APPENDS its source_ref to the edge's ``provenance``
    # list (audit trail) and reports ``kept_reviewed``. All other writes proceed.
    # ``.get`` (not ``[...]``) so a presence stub returning only the endpoint
    # flags — as some callers/tests do — degrades to "no existing edge".
    row0 = presence[0] if presence else {}
    edge_exists = bool(row0.get("edge_exists"))
    existing_review_state = row0.get("review_state")
    existing_source_kind = row0.get("source_kind")
    if (
        edge_exists
        and clean.get("source_kind") == "kg_discovery"
        and existing_review_state in ("approved", "applied")
    ):
        source_ref = clean.get("source_ref")
        # rel_type/labels/key fields interpolated (validated); values parameterized.
        kept_cypher = (
            f"MATCH (a:{from_label} {{{src_field}: $from_key}}) "
            f"MATCH (b:{to_label} {{{dst_field}: $to_key}}) "
            f"MATCH (a)-[r:{rel_type}]->(b) "
            "SET r.provenance = coalesce(r.provenance, []) + "
            "    CASE WHEN $source_ref IS NULL OR $source_ref IN coalesce(r.provenance, []) "
            "         THEN [] ELSE [$source_ref] END, "
            "r.updated_at = datetime() "
            "RETURN r.provenance AS provenance"
        )
        rows = db.write(
            kept_cypher, from_key=from_key, to_key=to_key, source_ref=source_ref
        )
        provenance = rows[0]["provenance"] if rows else None
        result = {
            "status": "kept_reviewed",
            "rel_type": rel_type,
            "from": {"label": from_label, "key": from_key},
            "to": {"label": to_label, "key": to_key},
            "review_state": existing_review_state,
            "existing_source_kind": existing_source_kind,
            "source_ref": source_ref,
            "provenance": provenance,
        }
        append_event({"type": "edge_upsert", **result})
        return result

    # rel_type/labels/key fields interpolated (validated); values parameterized.
    # A pre-MERGE OPTIONAL MATCH (same query) gives reliable created-vs-updated.
    cypher = (
        f"MATCH (a:{from_label} {{{src_field}: $from_key}}) "
        f"MATCH (b:{to_label} {{{dst_field}: $to_key}}) "
        f"OPTIONAL MATCH (a)-[er:{rel_type}]->(b) "
        "WITH a, b, er IS NOT NULL AS existed "
        f"MERGE (a)-[r:{rel_type}]->(b) "
        "ON CREATE SET r += $props, r.created_at = datetime(), r.updated_at = datetime() "
        "ON MATCH SET r += $props, r.updated_at = datetime() "
        "RETURN existed AS existed"
    )
    rows = db.write(cypher, from_key=from_key, to_key=to_key, props=clean)
    existed = bool(rows[0]["existed"]) if rows else False
    status = "updated" if existed else "created"

    result = {
        "status": status,
        "rel_type": rel_type,
        "from": {"label": from_label, "key": from_key},
        "to": {"label": to_label, "key": to_key},
    }
    append_event({"type": "edge_upsert", **result})
    return result


def write_node_model(db: GraphDB, model: Any) -> dict[str, Any]:
    """Upsert a node from a :class:`~harness.kg.models.GraphNode` instance.

    Reads the node's ``LABEL`` / ``KEY_FIELD`` class vars and its
    Neo4j-safe :meth:`~harness.kg.models.GraphNode.cypher_props` payload, then
    delegates to :func:`upsert_node`.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        model: A :class:`~harness.kg.models.GraphNode` subclass instance.

    Returns:
        The :func:`upsert_node` result dict.
    """
    return upsert_node(
        db,
        label=model.LABEL,
        key_field=model.KEY_FIELD,
        key_value=model.key_value,
        props=model.cypher_props(),
    )
