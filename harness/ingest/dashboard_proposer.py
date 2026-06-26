"""Per-dashboard LLM proposer for Dashboard surfaces over LIVE metrics.

This is the parallel-fan-out subagent for the dashboard ingestion (one agent per
dashboard, driven concurrently by
:func:`harness.ingest.orchestrator.ingest_dashboards` with ``propose_fn`` set to
:func:`propose_dashboard_with_cost`). It mirrors
:mod:`harness.ingest.proposer` but is scoped to a single
:class:`~harness.kg.models.Dashboard` node — metrics already exist, so it never
proposes Metric/UIComponent nodes.

Division of labour (the safety contract):

* **Deterministic** (:mod:`harness.ingest.dashboard_prepass`) owns the identity,
  the genuinely-shown *floor* edges (chart ``metric_key`` ∪ catalog membership),
  and the product/domain derivation. Every edge targets one of the live 317
  metric uids; the LLM can never invent a metric.
* **LLM** does two judgement jobs the deterministic pass cannot:
  1. *Enrich* the descriptive Dashboard fields (``display_name``,
     ``dashboard_type``, ``data_classification``, ``min_level``).
  2. *Adjudicate intermediates*: decide which — if any — of the dashboard's
     ``dependency`` candidates (metrics pulled in only as a chart's ``depends_on``
     computation input) are genuinely DISPLAYED on the surface vs. mere feeders.
     By default an intermediate is NOT mapped; the LLM only adds one back when it
     is plainly shown (e.g. it is also a KPI card on that board).

Final ``SHOWN_ON`` set = deterministic floor ∪ LLM-kept dependencies. On any LLM
failure the floor is used verbatim, so a model hiccup never drops a surface and
never re-introduces an intermediate.
"""

from __future__ import annotations

import uuid
from typing import Any

from harness.agent import engine
from harness.ingest.dashboard_prepass import prepass_for

#: Source-kind tag stamped on every proposal this module produces.
SOURCE_KIND_LLM: str = "llm_dashboard_proposal"

#: JSON Schema constraining the agent to one object: the descriptive enrichment
#: plus ``shown_dependency_uids`` — the intermediates it judges genuinely shown.
#: Identity, product, domains and the floor edges are owned by the deterministic
#: draft and are not negotiable.
DASHBOARD_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["display_name", "dashboard_type", "data_classification"],
    "properties": {
        "display_name": {
            "type": "string",
            "description": "Human-readable dashboard title (e.g. 'CEO Pulse').",
        },
        "dashboard_type": {
            "type": "string",
            "enum": ["executive", "operational", "ml", "review"],
        },
        "data_classification": {
            "type": "string",
            "enum": ["public", "internal", "restricted", "executive"],
        },
        "min_level": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Minimum seniority level required to view (1=any staff).",
        },
        "shown_dependency_uids": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Subset of the provided DEPENDENCY CANDIDATE metric_uids that are "
                "genuinely DISPLAYED on this dashboard (a KPI/chart of their own), "
                "not just computation inputs. Empty when all are mere intermediates."
            ),
        },
    },
}

DASHBOARD_PROPOSER_SYSTEM: str = """\
You are a senior analytics product ontologist. You classify ONE analytics
dashboard at a time and emit a single JSON object — you never write the database.

You are given the dashboard's genuinely-shown metrics (already decided) and a
separate list of DEPENDENCY CANDIDATES: metrics that appear only as computation
inputs behind some chart's formula. Your jobs:

1. `display_name`: a concise human title for the slug (e.g. 'ceo-pulse' ->
   'CEO Pulse', 'ml-churn-analysis' -> 'ML Churn Analysis'). Expand obvious
   acronyms (ceo, ml, roas, ga4, sms, ltv, rfm, cro) in standard casing.
2. `dashboard_type`: executive | operational | ml | review.
   - executive: company-level KPI roll-ups for leadership (ceo/exec/planning).
   - ml: surfaces built around model scores/forecasts/predictions (slug 'ml-*').
   - review: periodic review surfaces (weekly/monthly/quarterly summaries).
   - operational: everything else (channel/platform/product working dashboards).
3. `data_classification`: public | internal | restricted | executive. Most are
   `internal`; use `executive` for leadership roll-ups, `restricted` for
   finance/margin/sensitive customer surfaces.
4. `min_level`: 1-5 seniority floor. 1 for general operational dashboards; raise
   it (3-5) for executive/finance surfaces.
5. `shown_dependency_uids`: STRICT. Default to an EMPTY array — dependency
   candidates are intermediate inputs and should NOT be mapped onto the surface.
   Include a metric_uid ONLY if it is plainly displayed in its own right on THIS
   dashboard (e.g. a headline KPI card), not merely feeding another metric's
   formula. Choose ONLY from the provided candidate uids; never invent one.

Output STRICTLY the JSON object described by the schema. No prose.
"""


def _build_user_prompt(slug: str, draft: dict[str, Any], shown_on: list[str], candidates: list[dict[str, Any]]) -> str:
    """Render the compact per-dashboard classification + adjudication prompt."""
    from harness.agent.prompts import _compact_json

    return "\n".join(
        [
            f"DASHBOARD SLUG: {slug}",
            f"PRODUCT: {draft['product_id']}  DOMAINS: {_compact_json(draft['domain_ids'])}",
            "",
            f"GENUINELY-SHOWN METRICS ({len(shown_on)}, already decided — for context):",
            _compact_json(shown_on[:30]),
            "",
            f"DEPENDENCY CANDIDATES ({len(candidates)}) — intermediates; pick only "
            "those genuinely DISPLAYED here (default: none):",
            _compact_json(candidates),
            "",
            "TASK: Emit one JSON object with display_name, dashboard_type, "
            "data_classification, min_level, and shown_dependency_uids.",
        ]
    )


def _to_proposal(slug: str, draft: dict[str, Any], shown_on: list[str], enrichment: dict[str, Any]) -> dict[str, Any]:
    """Merge LLM output onto the draft and build the section-8 proposal.

    The draft supplies every required field plus the ground-truth ``product_id`` /
    ``domain_ids`` (never overridden); the agent's non-null values win only for the
    descriptive fields. ``SHOWN_ON`` edges = the deterministic floor (``shown_on``)
    plus any dependency uids the agent judged genuinely displayed.
    """
    allowed = {"display_name", "dashboard_type", "data_classification", "min_level"}
    overrides = {k: v for k, v in enrichment.items() if k in allowed and v is not None}
    payload = {**draft, **overrides}

    edge_uids = list(shown_on)
    edge_uids += [u for u in enrichment.get("kept_dependencies", []) if u not in set(shown_on)]

    relationship_payloads = [
        {
            "type": "SHOWN_ON",
            "from_label": "Metric",
            "from_id": uid,
            "to_label": "Dashboard",
            "to_id": slug,
        }
        for uid in edge_uids
    ]

    return {
        "proposal_id": f"kgd_{uuid.uuid4().hex[:12]}",
        "operation": "upsert",
        "target_label": "Dashboard",
        "target_id": slug,
        "key_field": "dashboard_id",
        "source_kind": SOURCE_KIND_LLM,
        "source_ref": slug,
        "review_state": "proposed",
        "dashboard_id": slug,
        "payload": payload,
        "relationship_payloads": relationship_payloads,
    }


async def propose_dashboard_with_cost(dashboard_id: str, *, db: Any = None) -> tuple[list[dict], float]:
    """Propose one enriched Dashboard node + its curated SHOWN_ON edges.

    Returns ``([proposal], cost_usd)``. On any LLM failure the deterministic floor
    is used verbatim (no dependencies added) so the dashboard is still proposed
    with safe defaults and clean (intermediate-free) edges.
    """
    slice_ = prepass_for(dashboard_id)
    draft = slice_["dashboard"]
    shown_on = slice_["shown_on"]
    candidates = slice_["dependency_candidates"]
    candidate_uids = {c["metric_uid"] for c in candidates}

    enrichment: dict[str, Any] = {}
    cost_usd = 0.0
    try:
        result = await engine.propose_structured(
            system_prompt=DASHBOARD_PROPOSER_SYSTEM,
            user_prompt=_build_user_prompt(dashboard_id, draft, shown_on, candidates),
            schema=DASHBOARD_PROPOSAL_SCHEMA,
            dashboard=dashboard_id,
        )
        enrichment = {k: v for k, v in result.items() if not k.startswith("_")}
        # Constrain the agent's choices to the offered candidate uids (no invention).
        kept = [u for u in (enrichment.get("shown_dependency_uids") or []) if u in candidate_uids]
        enrichment["kept_dependencies"] = kept
        cost_usd = float((result.get("_meta") or {}).get("cost_usd") or 0.0)
    except Exception:  # noqa: BLE001 — never drop a surface over a model hiccup
        enrichment = {}

    return [_to_proposal(dashboard_id, draft, shown_on, enrichment)], cost_usd


def prune_shown_on(db: Any, desired: set[tuple[str, str]]) -> int:
    """Delete ``SHOWN_ON`` edges that are not in the ``desired`` mapping.

    Reconciles the graph to the curated set: any ``(metric_uid, dashboard_id)``
    edge present in Neo4j but absent from ``desired`` (e.g. the intermediate
    ``depends_on`` mappings from an earlier, un-curated run) is removed. Returns
    the number of edges deleted. Idempotent.

    Args:
        db: A connected :class:`~harness.kg.driver.GraphDB`.
        desired: The curated set of ``(metric_uid, dashboard_id)`` edges to keep.
    """
    existing = db.read(
        "MATCH (m:Metric)-[:SHOWN_ON]->(d:Dashboard) "
        "RETURN m.metric_uid AS metric, d.dashboard_id AS dashboard"
    )
    deleted = 0
    for row in existing:
        pair = (row["metric"], row["dashboard"])
        if pair in desired:
            continue
        db.write(
            "MATCH (m:Metric {metric_uid: $m})-[r:SHOWN_ON]->(d:Dashboard {dashboard_id: $d}) "
            "DELETE r",
            m=pair[0],
            d=pair[1],
        )
        deleted += 1
    return deleted
