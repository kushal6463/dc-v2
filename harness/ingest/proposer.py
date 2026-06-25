"""Per-dashboard proposer for the metric / UIComponent ingestion engine.

This is the LLM-driven middle stage of Milestone 2 (implementation plan section
5b). For one dashboard it:

1. Builds the deterministic drafts from :func:`harness.ingest.prepass.prepass_for`.
2. Builds a compact spine RAG context (linkable Domain / IntelligenceProduct ids)
   via :func:`harness.ingest.orchestrator.get_spine_context` — the *only* Neo4j
   access here, and it is strictly **read-only**.
3. Calls :func:`harness.agent.engine.propose_structured` with
   :data:`PROPOSAL_OUTPUT_SCHEMA`, which constrains the agent to emit a single
   top-level object with a ``"proposals"`` array in the schema-section-8 shape.
4. Normalizes every returned raw proposal into a complete section-8 proposal dict
   (filling ``proposal_id``, ``operation``, ``source_kind``, ``review_state`` and
   the ``key_field`` from :data:`~harness.kg.models.NODE_KEY_FIELDS`).

The proposer never writes Neo4j and never applies anything — it returns proposal
dicts that the orchestrator persists and the apply stage replays through the M1
arbitration writer.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from harness.agent import engine
from harness.agent.prompts import PROPOSER_SYSTEM, build_proposer_user_prompt
from harness.ingest.prepass import prepass_for
from harness.kg.models import NODE_KEY_FIELDS

#: Source-kind tag stamped on every proposal this module produces.
SOURCE_KIND_LLM: str = "llm_proposal"

# ---------------------------------------------------------------------------
# Output schema (JSON Schema) — constrains the agent to the section-8 shape.
# ---------------------------------------------------------------------------

#: JSON Schema handed to the agent via ``output_format``. It forces a single
#: top-level object ``{"proposals": [ ... ]}`` where each item is a section-8
#: proposal: a target node (``target_label`` + ``target_id`` + ``payload``) plus
#: a list of relationship payloads (each ``type`` + typed ``from``/``to``
#: endpoints). ``additionalProperties`` is left permissive on the free-form
#: ``payload`` / relationship ``properties`` objects (node/edge props vary by
#: label) while the proposal envelope itself is tightly constrained.
PROPOSAL_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposals"],
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_label", "target_id", "payload"],
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["upsert", "deprecate", "delete"],
                    },
                    "target_label": {
                        # M2 product decision: the agent never proposes per-entry
                        # UIComponent nodes — chart types are generalised type
                        # nodes seeded at bootstrap. The agent classifies each
                        # metric's chart_type (on the Metric payload) and emits a
                        # VISUALIZES edge from the matching uic:<chart_type> node.
                        "type": "string",
                        "enum": ["Dashboard", "Metric"],
                    },
                    "target_id": {"type": "string"},
                    "key_field": {"type": "string"},
                    "source_confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "payload": {
                        "type": "object",
                        "description": (
                            "The target node's properties (only field names that "
                            "exist on the corresponding Pydantic model)."
                        ),
                    },
                    "relationship_payloads": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "type",
                                "from_label",
                                "from_id",
                                "to_label",
                                "to_id",
                            ],
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "VISUALIZES",
                                        "SHOWN_ON",
                                        "BELONGS_TO_DOMAIN",
                                        "PART_OF_PRODUCT",
                                        "DECOMPOSES_INTO",
                                    ],
                                },
                                "from_label": {"type": "string"},
                                "from_id": {"type": "string"},
                                "to_label": {"type": "string"},
                                "to_id": {"type": "string"},
                                "properties": {"type": "object"},
                            },
                        },
                    },
                },
            },
        }
    },
}


def _key_field_for(target_label: str) -> str | None:
    """Return the identity (key) field for ``target_label``, or ``None``.

    Looks the label up in :data:`~harness.kg.models.NODE_KEY_FIELDS`; an unknown
    label yields ``None`` so the caller can decide how to handle it (the apply
    stage / arbitration writer re-validates the label anyway).
    """
    return NODE_KEY_FIELDS.get(target_label)


def _draft_index(drafts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a dashboard's deterministic drafts by their target id.

    Maps each Metric draft (by ``metric_uid``) and the Dashboard draft (by
    ``dashboard_id``) so the agent's enrichment can be merged ONTO the draft,
    guaranteeing required fields the agent may omit (``scope_key``,
    ``product_ids``, ``metric_base`` …) are always present.
    """
    index: dict[str, dict[str, Any]] = {}
    for metric in drafts.get("metrics") or []:
        uid = metric.get("metric_uid")
        if uid:
            index[str(uid)] = metric
    dash = drafts.get("dashboard") or {}
    if dash.get("dashboard_id"):
        index[str(dash["dashboard_id"])] = dash
    return index


def _normalize_proposal(
    raw: dict[str, Any],
    dashboard_id: str,
    draft_index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize one raw agent proposal into a complete section-8 proposal dict.

    Fills the proposal envelope fields the agent is not required to supply
    (``proposal_id``, ``operation``, ``source_kind``, ``review_state``,
    ``key_field``) and — crucially — MERGES the agent's enrichment payload onto
    the deterministic draft for this target (``{**draft, **agent_non_null}``).
    The draft supplies the required base fields; the agent overrides/enriches
    (chart_type, causal_role, concept_key, domain_ids, …). This is the plan's
    "deterministic draft → agent reconciles & enriches" contract, and prevents a
    metric from being dropped at apply-time because the agent omitted a required
    field.

    Args:
        raw: A single proposal object from the agent's ``proposals`` array.
        dashboard_id: The dashboard this proposal belongs to.
        draft_index: Map of target_id -> deterministic draft payload.

    Returns:
        A normalized section-8 proposal dict with a complete merged payload.
    """
    target_label = str(raw.get("target_label", ""))
    target_id = str(raw.get("target_id", ""))
    key_field = raw.get("key_field") or _key_field_for(target_label)

    agent_payload = dict(raw.get("payload") or {})
    draft = (draft_index or {}).get(target_id, {})
    # Draft provides required base fields; the agent's non-null values win.
    merged = {**draft, **{k: v for k, v in agent_payload.items() if v is not None}}

    return {
        "proposal_id": str(raw.get("proposal_id") or f"kgp_{uuid.uuid4().hex[:12]}"),
        "operation": str(raw.get("operation") or "upsert"),
        "target_label": target_label,
        "target_id": target_id,
        "key_field": key_field,
        "source_kind": SOURCE_KIND_LLM,
        "source_ref": dashboard_id,
        "source_confidence": raw.get("source_confidence"),
        "review_state": "proposed",
        "dashboard_id": dashboard_id,
        "payload": merged,
        "relationship_payloads": list(raw.get("relationship_payloads") or []),
    }


#: Max metrics per agent call. Large structured outputs (one proposal per
#: metric, each with relationship payloads) reliably deadlock the SDK on the
#: biggest dashboards (15-18 metrics), so we chunk the metrics and make several
#: small calls per dashboard instead of one big one. Tunable via env for the
#: hardest dashboards (smaller = smaller per-call output = more reliable).
CHUNK_SIZE = int(os.environ.get("KG_CHUNK_SIZE", "6"))


async def _propose_chunked(
    dashboard_id: str, *, db: Any = None
) -> tuple[list[dict], float]:
    """Propose a dashboard in small metric-chunks; return (proposals, cost_usd).

    The Dashboard node is proposed once (with the first chunk); each chunk emits
    proposals for ~``CHUNK_SIZE`` metrics. The agent's enrichment is merged onto
    the full deterministic draft index so required fields are never lost.
    """
    from harness.ingest.orchestrator import get_spine_context

    drafts = prepass_for(dashboard_id)
    spine = get_spine_context(db)
    metrics = list(drafts.get("metrics") or [])
    index = _draft_index(drafts)

    chunks = [metrics[i : i + CHUNK_SIZE] for i in range(0, len(metrics), CHUNK_SIZE)]
    if not chunks:
        chunks = [[]]

    proposals: list[dict] = []
    cost_usd = 0.0
    for ci, chunk in enumerate(chunks):
        # The Dashboard draft goes only with the first chunk (proposed once).
        sub = {
            "dashboard": drafts.get("dashboard", {}) if ci == 0 else {},
            "components": [],
            "metrics": chunk,
        }
        user_prompt = build_proposer_user_prompt(dashboard_id, sub, spine)
        result = await engine.propose_structured(
            system_prompt=PROPOSER_SYSTEM,
            user_prompt=user_prompt,
            schema=PROPOSAL_OUTPUT_SCHEMA,
            dashboard=dashboard_id,
        )
        for raw in result.get("proposals") or []:
            if isinstance(raw, dict):
                proposals.append(_normalize_proposal(raw, dashboard_id, index))
        cost_usd += float((result.get("_meta") or {}).get("cost_usd") or 0.0)

    return proposals, cost_usd


async def propose_for_dashboard(dashboard_id: str, *, db: Any = None) -> list[dict]:
    """Propose all nodes + edges for one dashboard (read-only on Neo4j).

    Chunks the dashboard's metrics into small per-call batches (see
    :data:`CHUNK_SIZE`) so a big dashboard never produces one oversized agent
    output. Returns normalized section-8 proposal dicts; performs no writes.
    """
    proposals, _ = await _propose_chunked(dashboard_id, db=db)
    return proposals


async def propose_for_dashboard_with_cost(
    dashboard_id: str, *, db: Any = None
) -> tuple[list[dict], float]:
    """Like :func:`propose_for_dashboard` but also return the summed call cost."""
    return await _propose_chunked(dashboard_id, db=db)
