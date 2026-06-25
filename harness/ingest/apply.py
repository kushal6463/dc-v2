"""Apply approved proposals through the M1 arbitration writer.

This is the bridge between the file-based proposal queue
(:mod:`harness.store.proposals`) and the single graph writer
(:mod:`harness.kg.arbitration`) — implementation plan section 5c. It never
touches the Neo4j driver directly: every node goes through
:func:`harness.kg.arbitration.upsert_node` and every edge through
:func:`harness.kg.arbitration.upsert_edge`, preserving the "arbitration is the
only writer" discipline.

Edges whose endpoints do not yet exist are handled gracefully: ``upsert_edge``
returns a ``missing_endpoint`` status rather than raising, and
:func:`apply_approved` counts those separately so a re-run (after the missing
node is applied) can complete the edge idempotently.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from harness.kg import arbitration
from harness.kg.driver import GraphDB
from harness.kg.models import (
    NODE_KEY_FIELDS,
    Business,
    Dashboard,
    Domain,
    IntelligenceProduct,
    Metric,
    UIComponent,
)
from harness.store.proposals import (
    latest_run_id,
    load_proposals,
    mark_review_state,
)

#: Maps a proposal ``target_label`` to the Pydantic model used to re-validate the
#: agent-produced payload before it reaches the (single) arbitration writer. This
#: drops unknown / invented field names (pydantic ignores extras) and validates /
#: coerces enum-typed fields, so out-of-vocabulary values can never be written.
_PAYLOAD_MODELS = {
    "Metric": Metric,
    "Dashboard": Dashboard,
    "UIComponent": UIComponent,
    "Business": Business,
    "Domain": Domain,
    "IntelligenceProduct": IntelligenceProduct,
}

#: Type of the optional live-canvas emit hook: ``emit(type, run_id=..., **data)``.
EmitHook = Callable[..., None]


def _edge_args(rel: dict[str, Any]) -> dict[str, Any]:
    """Translate a relationship payload into :func:`upsert_edge` kwargs.

    The proposal relationship shape is ``{type, from_label, from_id, to_label,
    to_id, properties?}``; the arbitration writer expects ``rel_type``,
    ``from_key``, ``to_key`` and ``props``.
    """
    return {
        "rel_type": str(rel.get("type", "")),
        "from_label": str(rel.get("from_label", "")),
        "from_key": str(rel.get("from_id", "")),
        "to_label": str(rel.get("to_label", "")),
        "to_key": str(rel.get("to_id", "")),
        "props": dict(rel.get("properties") or {}),
    }


def apply_edge_proposal(
    db: GraphDB,
    proposal: dict,
    *,
    emit: EmitHook | None = None,
    run_id: str | None = None,
) -> dict:
    """Apply one edge-only proposal (``operation == "upsert_edge"``).

    M3 causal layer. The proposal's ``payload`` is a single edge dict
    (``type``/``from_label``/``from_id``/``to_label``/``to_id``/``properties``),
    applied through :func:`harness.kg.arbitration.upsert_edge` — the SAME single
    writer the node path uses. No node is upserted (both endpoints are expected
    to already exist; a missing one yields a ``missing_endpoint`` status rather
    than raising). A human edit replaces ``payload`` in place (see the ``edit``
    action in :mod:`harness.api.server`), so the edited edge — including its
    confidence / mechanism — is exactly what is written here.

    Returns:
        ``{"edge": <upsert_edge result>}``, or ``{"status": "invalid_payload",
        "target_id", "error"}`` when the edge type / labels are not allowlisted
        (so one bad causal proposal never aborts the whole apply run).
    """
    edge = dict(proposal.get("payload") or {})
    try:
        edge_result = arbitration.upsert_edge(db, **_edge_args(edge))
    except (ValueError, KeyError) as exc:
        # Unknown rel_type / label (allowlist guard) — surface, do not raise, so
        # a single malformed causal proposal does not abort the apply run.
        return {
            "status": "invalid_payload",
            "target_id": str(proposal.get("target_id", "")),
            "error": str(exc),
        }
    if emit is not None:
        emit(
            "edge_written",
            run_id=run_id,
            rel_type=edge_result.get("rel_type"),
            status=edge_result.get("status"),
            **{k: edge_result[k] for k in ("from", "to") if k in edge_result},
        )
    return {"edge": edge_result}


def apply_proposal(
    db: GraphDB,
    proposal: dict,
    *,
    emit: EmitHook | None = None,
    run_id: str | None = None,
) -> dict:
    """Apply one proposal: upsert its node, then each of its edges.

    Routes the node through :func:`harness.kg.arbitration.upsert_node` (keyed on
    the proposal's ``key_field`` — falling back to the model allowlist when
    absent) and each relationship payload through
    :func:`harness.kg.arbitration.upsert_edge`. Edge endpoints that do not exist
    yield a ``missing_endpoint`` edge result instead of raising.

    An **edge-only** proposal (``operation == "upsert_edge"``, the M3 causal
    layer) is routed to :func:`apply_edge_proposal` instead — no node is
    upserted; only the single edge in ``payload`` is written.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        proposal: A section-8 proposal dict (``target_label``, ``target_id``,
            ``payload``, ``relationship_payloads``, ...).
        emit: Optional live-canvas emit hook ``emit(type, run_id=..., **data)``.
            When supplied, a ``node_written`` event is published after the node
            upsert. ``None`` (the default) leaves behaviour unchanged.
        run_id: The run this apply belongs to (stamped onto emitted events).

    Returns:
        ``{"node": <upsert_node result>, "edges": [<upsert_edge result>...]}``,
        ``{"edge": <upsert_edge result>}`` for an edge-only proposal, or
        ``{"status": "invalid_payload", "target_id", "error"}`` when a payload
        fails re-validation (bad enum value / bad edge type).

    Raises:
        ValueError: If ``target_label`` / ``key_field`` or an edge's type /
            labels are not allowlisted (re-raised from the arbitration writer).
    """
    if str(proposal.get("operation") or "") == "upsert_edge":
        return apply_edge_proposal(db, proposal, emit=emit, run_id=run_id)

    target_label = str(proposal["target_label"])
    target_id = str(proposal["target_id"])
    key_field = proposal.get("key_field") or NODE_KEY_FIELDS[target_label]
    source_kind = str(proposal.get("source_kind") or "llm_proposal")
    payload = dict(proposal.get("payload") or {})

    # Re-validate the agent-produced payload through the matching Pydantic model
    # BEFORE it reaches the writer: this drops invented field names (pydantic
    # ignores extras) and validates / coerces enum-typed fields, so an
    # out-of-vocabulary enum is rejected per-proposal instead of being written.
    # A bad value raises ValidationError, which we surface (not raise) so one bad
    # proposal does not abort the whole apply run.
    model_cls = _PAYLOAD_MODELS.get(target_label)
    if model_cls is not None:
        try:
            props = model_cls(**{**payload, key_field: target_id}).cypher_props()
        except ValidationError as exc:
            return {
                "status": "invalid_payload",
                "target_id": target_id,
                "error": str(exc),
            }
    else:
        props = payload

    node_result = arbitration.upsert_node(
        db,
        label=target_label,
        key_field=key_field,
        key_value=target_id,
        props=props,
        source_kind=source_kind,
    )
    if emit is not None:
        emit(
            "node_written",
            run_id=run_id,
            label=target_label,
            key=target_id,
            status=node_result["status"],
        )

    edge_results: list[dict[str, Any]] = []
    for rel in proposal.get("relationship_payloads") or []:
        if not isinstance(rel, dict):
            continue
        edge_results.append(arbitration.upsert_edge(db, **_edge_args(rel)))

    return {"node": node_result, "edges": edge_results}


def apply_approved(
    db: GraphDB,
    run_id: str | None = None,
    *,
    emit: EmitHook | None = None,
) -> dict:
    """Apply every ``approved`` proposal in a run, returning aggregate counts.

    Loads the run's approved proposals (the latest run when ``run_id`` is
    ``None``) and applies each via :func:`apply_proposal`, tallying node
    creates/updates, written edges, and edges skipped because an endpoint did not
    yet exist. The pass is idempotent: re-running upserts are no-ops and
    previously ``missing_endpoint`` edges complete once their nodes exist.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        run_id: The run to apply; the latest run is used when ``None``.
        emit: Optional live-canvas emit hook ``emit(type, run_id=..., **data)``;
            forwarded to :func:`apply_proposal` so each applied node publishes a
            ``node_written`` event. ``None`` (the default) leaves behaviour
            unchanged.

    Returns:
        ``{"nodes_created", "nodes_updated", "edges",
        "skipped_missing_endpoint", "skipped_invalid"}`` integer counts.
        ``skipped_invalid`` tallies proposals whose payload failed Pydantic
        re-validation (a bad proposal never aborts the run).
    """
    approved = load_proposals(run_id=run_id, state="approved")

    nodes_created = 0
    nodes_updated = 0
    edges_written = 0
    skipped_missing_endpoint = 0
    skipped_invalid = 0
    applied_ids: set[str] = set()

    for proposal in approved:
        result = apply_proposal(db, proposal, emit=emit, run_id=run_id)
        pid = str(proposal.get("proposal_id") or "")
        # A payload that failed Pydantic re-validation yields no node/edge write;
        # tally it and move on so one bad proposal does not abort the run.
        if result.get("status") == "invalid_payload":
            skipped_invalid += 1
            continue
        # Edge-only proposal (M3 causal layer): a single edge, no node.
        if "edge" in result:
            if result["edge"]["status"] == "missing_endpoint":
                skipped_missing_endpoint += 1
            else:
                edges_written += 1
                applied_ids.add(pid)
            continue
        if result["node"]["status"] == "created":
            nodes_created += 1
        elif result["node"]["status"] == "updated":
            nodes_updated += 1
        missing = False
        for edge in result["edges"]:
            if edge["status"] == "missing_endpoint":
                skipped_missing_endpoint += 1
                missing = True
            else:
                edges_written += 1
        # Mark the proposal "applied" only when fully written (no dangling edge),
        # so a re-run still completes any missing_endpoint edge later.
        if not missing:
            applied_ids.add(pid)

    # Flip written proposals to "applied" so the canvas stops counting them as
    # still-to-apply ("Apply approved (N)" / review panel read review_state).
    applied = 0
    if applied_ids:
        resolved = run_id or latest_run_id()
        if resolved:
            applied = mark_review_state(resolved, applied_ids, "applied")

    return {
        "nodes_created": nodes_created,
        "nodes_updated": nodes_updated,
        "edges": edges_written,
        "skipped_missing_endpoint": skipped_missing_endpoint,
        "skipped_invalid": skipped_invalid,
        "marked_applied": applied,
    }
