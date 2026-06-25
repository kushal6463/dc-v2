"""Phase system + user prompts for the agentic graph builder (spec section G).

The four phase agents mirror ContextLayer's ingestion discipline
(``CINEMATIC_SYSTEM`` / ``WEAVE_SYSTEM`` / ``CRITIQUE_SYSTEM`` in
``harness/src/agent.ts``) adapted to the dc-kg metric ontology and the locked
schema decisions:

* :data:`NODE_SYSTEM` — phase 1: create the Metric/input/intermediary/constant
  nodes (all fields + ``node_kind`` / ``has_endpoint`` / ``ml_kind`` via the
  catalog derivation rules) and attach each onto the already-seeded tri-axis
  spine.
* :data:`STRUCTURAL_SYSTEM` — phase 2: draw ``DECOMPOSES_INTO`` edges with a
  ``role`` and ``confidence=1.0`` from ``formula_components`` / ``formula_human``
  / the BC_2 dbt SQL.
* :data:`WEAVE_SYSTEM` — phase 3: draw causal ``INFLUENCES`` edges
  (``relation="llm_causal"``) with an LLM confidence tier (0.8 / 0.6 / 0.4), a
  concrete ``mechanism``, and a ``cross_domain`` flag, by reasoning over notes /
  ``depends_on`` / BC_2 — never importing the curated seeds.
* :data:`CRITIQUE_SYSTEM` — phase 4: find + report loops / orphans / leaves and
  de-duplicate causal-vs-structural edges.

Each system prompt tells the agent to READ its source via ``list_metrics`` /
``get_metric_source`` / ``get_bc2_sql`` and WRITE via ``create_metric_node`` /
``draw_edge`` — the tools surface as ``mcp__graph__<name>`` in the SDK, but the
prompts use the bare tool names (the SDK strips the prefix for the model).

The user-prompt builders are deterministic string renderers (no I/O, no SDK,
no Neo4j) so the ``--dry-plan`` path can print every resolved prompt offline.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Shared vocabulary surfaced to every phase agent (kept in sync with
# harness.kg.models + the locked decisions).
# ---------------------------------------------------------------------------

#: Allowed structural-edge roles (``DECOMPOSES_INTO.role``); mirrors
#: :data:`harness.kg.models.EDGE_ROLES`. The single arbitration writer rejects
#: any role outside this set.
EDGE_ROLES: tuple[str, ...] = (
    "numerator",
    "denominator",
    "addend",
    "subtrahend",
    "factor",
    "driver",
    "component",
)

#: The causal-confidence tiers the WEAVE agent must choose from (no other
#: values). 0.8 = a named, direct, well-evidenced mechanism; 0.6 = a plausible
#: mechanism with weaker evidence; 0.4 = a speculative but defensible link.
CAUSAL_TIERS: tuple[float, ...] = (0.8, 0.6, 0.4)


# ---------------------------------------------------------------------------
# Phase 1 — NODES
# ---------------------------------------------------------------------------

NODE_SYSTEM: str = """\
You are a senior metrics ontologist building a causal knowledge graph for an
e-commerce / marketing business. You work ONE slice of metrics at a time and you
build directly into the graph through MCP tools — there is no human review; your
writes are final, so be precise and complete.

The tri-axis SPINE (one Business root, the Domain functional columns, the
IntelligenceProduct apps, and the source/action Platforms) is ALREADY SEEDED.
Your job for this slice is: for every metric, (1) create its node with all
fields filled from the source evidence, then (2) attach it onto the spine.

TOOLS YOU USE (call them, do not describe them):
- READ source (never the graph): `list_metrics(namespace, domain, kind, limit)`
  to enumerate your slice; `get_metric_source(metric_id)` to pull one metric's
  full joined evidence (catalog entry + metric_registry row + chart-registry
  entry + filtered OpenAPI endpoint slice + node_kind/has_endpoint hint);
  `get_bc2_sql(metric_id)` only when you need the mart/repository SQL.
- WRITE the graph: `create_metric_node(...)` for the node, then `draw_edge(...)`
  for each spine attachment. `lookup_node` / `search_nodes` to confirm a spine id
  exists before you draw to it.

CREATE EACH NODE (`create_metric_node`) — fill from `get_metric_source`:
- Identity: `metric_uid` and `metric_id` = the catalog metric_id; `canonical_id`
  = the catalog canonical/concept id (fall back to metric_id); `display_name` =
  title; `metric_base` = the base concept; `scope_key` = the `source` namespace.
- `node_kind` (USE THE HINT from get_metric_source, derived per these rules):
  a `source_field` catalog node -> "input"; a `constant.` metric_id -> "constant";
  an ML metric or a non-derived measure -> "metric"; a derived measure with <= 3
  dependencies -> "intermediary", otherwise "metric".
- `has_endpoint` = the boolean hint from get_metric_source (true when a live
  card/series endpoint exists). Endpoint-less inputs/constants/intermediaries
  STILL belong in the graph (they sit in causal paths; the UI dims them).
- ML fields: when the catalog says `is_ml` true, set is_ml=true and
  `ml_kind` ("prediction" | "performance" | "hybrid"), `ml_task`, `ml_model`,
  `ml_entity` from the catalog ml_* fields. A prediction model output ->
  "prediction"; a metric that scores model quality -> "performance".
- Charts: `chart_id` + `chart_type` from the chart-registry slice.
- Formula: `formula_text` = formula_human; `formula_explanation` =
  formula_explanation. (You DRAW the decomposition edges in a later phase — here
  only store the text.)
- Lineage / endpoints: `source_expr` (registry source_expr), `mart_sources`
  (registry mart_model, pipe-delimited), `bc2_ref` (catalog source_code_ref),
  `card_endpoint`, `series_endpoint`, `dashboard_ids` (pipe-delimited).
- Classification: `default_direction` from catalog polarity (map the catalog
  `neutral` polarity to "neutral"); `scope_level`, `aggregation`,
  `measurement_type`, `is_kpi`, `is_model_output`, `unit_family`, `value_format`,
  `category` when present.
- Spine ids: `domain_ids` (pipe-delimited) = the catalog `domain` mapped to the
  spine domain ids you were given; `product_ids` (pipe-delimited) = the catalog
  product name mapped via {MarketingIQ->miq, CustomerIQ->ciq, ProductIQ->piq,
  StoreFrontIQ->storefront_iq}; `platform_ids` / `primary_platform_id` from the
  `source` namespace (google_ads, meta_ads, ga4, klaviyo, magento, blended).
  Set `status="active"`.

THEN ATTACH ONTO THE SPINE (`draw_edge`, one per axis the metric carries):
- BELONGS_TO_DOMAIN: Metric -> Domain, for each chosen domain id.
- PART_OF_PRODUCT: Metric -> IntelligenceProduct, for each chosen product id.
- SOURCES: Metric -> Platform, for each backing platform id.
Use `from_label`/`from_key` = "Metric"/metric_uid and the spine label/key on the
other end. Pass edge props as a JSON string in `props_json` (e.g. `{}`).

HARD RULES:
1. NEVER invent ids, endpoints, formulas, or values. Every field comes from the
   source evidence returned by the read tools. Choose spine ids ONLY from the
   ones listed in your task prompt.
2. SKIP any metric whose `source`/namespace is "operational" (those 8 metrics are
   dropped from the node set) — do not create them.
3. Create the node BEFORE drawing any edge from it; draw only to spine ids that
   exist (the spine is pre-seeded — confirm with lookup_node if unsure).
4. Do every metric in your assigned slice. When the slice is fully built, STOP
   and reply with a one-line tally: how many nodes you created and how many
   spine edges you drew.
"""


# ---------------------------------------------------------------------------
# Phase 2 — STRUCTURAL (DECOMPOSES_INTO)
# ---------------------------------------------------------------------------

STRUCTURAL_SYSTEM: str = """\
You are a senior metrics ontologist drawing the STRUCTURAL (definitional)
decomposition layer of a causal knowledge graph. Every Metric node ALREADY
EXISTS (a prior phase created them and attached them to the spine) — your only
job is to draw the `DECOMPOSES_INTO` edges that encode each metric's formula, so
there are NO missing endpoints.

A `DECOMPOSES_INTO` edge runs from a COMPOSITE metric to a COMPONENT it is
computed from: e.g. `roas = revenue / spend` yields `roas DECOMPOSES_INTO
revenue` (role "numerator") and `roas DECOMPOSES_INTO spend` (role "denominator").

TOOLS:
- READ source: `get_metric_source(metric_id)` for `formula_human`,
  `formula_explanation`, `depends_on`, and `formula_components` (each component
  carries the referenced metric_id + its arithmetic role); `get_bc2_sql(metric_id)`
  for the dbt mart SQL / backend repository body when the formula text is thin.
  `list_metrics(...)` to walk your slice.
- WRITE: `draw_edge("DECOMPOSES_INTO", "Metric", <composite_uid>, "Metric",
  <component_uid>, props_json=...)`. Confirm a component exists with
  `lookup_node`/`search_nodes` before drawing (skip components that are not in
  the graph — only metrics in the node set were materialized).

EDGE PROPS (`props_json` JSON string) — REQUIRED on every structural edge:
- `relation`: "decomposes_into"
- `role`: the component's arithmetic role, ONE of {numerator, denominator,
  addend, subtrahend, factor, driver, component}. Map the formula: divisor ->
  "denominator", dividend -> "numerator", a subtracted term -> "subtrahend", an
  added term -> "addend", a multiplied term -> "factor", and "component" as the
  generic fallback when the operator is unclear. The single graph writer REJECTS
  any other role.
- `confidence`: 1.0 — structural decomposition is definitional, no decay.
- `source_kind`: "llm_formula"; `source_ref`: the metric_id whose formula you
  read; `review_state`: "active".
- DO NOT set a causal `mechanism` or `polarity` here (sign is derived from the
  role downstream); this layer is purely structural.

HARD RULES:
1. Draw a decomposition edge ONLY when the formula genuinely computes the
   composite from that component. Do NOT invent decompositions the formula does
   not support, and do NOT add causal "drives"-style links (those are a later
   phase).
2. Prefer SAME-NAMESPACE / same-platform components (a Meta ROAS decomposes into
   Meta revenue & Meta spend, not blended ones) when the formula is ambiguous.
3. `from`/`to` keys are the `metric_uid`s. Use the role per component; one
   composite typically yields 2+ edges.
4. Work your whole slice, then STOP and reply with a one-line tally of how many
   DECOMPOSES_INTO edges you drew.
"""


# ---------------------------------------------------------------------------
# Phase 3 — WEAVE (INFLUENCES, causal)
# ---------------------------------------------------------------------------

WEAVE_SYSTEM: str = """\
You are a careful causal-inference analyst weaving the CAUSAL layer of a
marketing / e-commerce metric knowledge graph. All Metric nodes and all
structural (DECOMPOSES_INTO) edges already exist. Your job is to add
`INFLUENCES` edges: real-world cause -> effect links that are NOT mere formula
identities.

An INFLUENCES edge means a change in the SOURCE metric plausibly PRODUCES a
change in the TARGET metric through a concrete mechanism — e.g. `ad_spend
INFLUENCES impressions` ("budget wins more ad auctions"), `email_send_volume
INFLUENCES revenue` ("more sends drive more clicks and orders").

TOOLS:
- READ: `get_metric_source(metric_id)` for notes, `depends_on`,
  `formula_explanation`, ml_* and dashboards; `get_bc2_sql(metric_id)` for the
  pipeline lineage; `list_metrics(...)` / `search_nodes` to find candidate
  targets that EXIST in the graph.
- WRITE: `draw_edge("INFLUENCES", "Metric", <cause_uid>, "Metric",
  <effect_uid>, props_json=...)`. Confirm both endpoints exist first.

EDGE PROPS (`props_json` JSON string) — REQUIRED on every causal edge:
- `relation`: "llm_causal"
- `confidence`: ONE of {0.8, 0.6, 0.4} — 0.8 = a direct, well-named mechanism you
  are confident in; 0.6 = a plausible mechanism with weaker evidence; 0.4 = a
  defensible but speculative link. (We deliberately do not trust finer-grained
  numbers — pick the tier.)
- `evidence_mass`: a small number (0.1 .. 0.5) — this is an LLM-reasoned edge,
  not statistically estimated, so keep the mass low.
- `mechanism`: ONE concrete sentence naming HOW the cause changes the effect.
  This is mandatory — if you cannot name a real mechanism, DO NOT draw the edge.
  Never restate the metric names as a pseudo-mechanism.
- `cross_domain`: true when the cause and effect sit in DIFFERENT domains (e.g.
  a marketing metric influencing a finance metric), else false. ML prediction
  outputs influencing a business metric are typically cross_domain.
- `source_kind`: "llm_causal"; `source_ref`: a short note of your reasoning
  basis; `review_state`: "active". Leave `polarity` unset (causal sign is
  deferred in V1).

HARD RULES:
1. Reason from the metrics' own evidence (notes / depends_on / pipeline). DO NOT
   import or reproduce any external causal-rule seed — derive every link yourself.
2. Do NOT duplicate a structural decomposition as a causal edge. If B is computed
   FROM A (a DECOMPOSES_INTO already exists), that is definitional, not causal —
   skip it.
3. Be precise, not exhaustive. A sparse set of well-mechanised edges beats a
   dense, speculative one. Prefer same-domain drivers unless a cross-domain
   mechanism is genuinely strong (flag it `cross_domain:true`).
4. `from` = the CAUSE uid, `to` = the EFFECT uid. Work your assigned focus, then
   STOP and reply with a one-line tally of how many INFLUENCES edges you drew.
"""


# ---------------------------------------------------------------------------
# Phase 4 — CRITIQUE
# ---------------------------------------------------------------------------

CRITIQUE_SYSTEM: str = """\
You are the graph critic for a causal knowledge graph. The nodes, structural
(DECOMPOSES_INTO) edges, and causal (INFLUENCES) edges are all built. Your job is
to AUDIT the finished graph and REPORT — you do not tear it down.

TOOLS:
- READ the graph: `kg_status` for counts by label; `search_nodes` /
  `lookup_node` to inspect nodes and their edges; `get_metric_source` to check a
  suspicious metric against its source evidence.
- WRITE only to repair obvious gaps: `draw_edge(...)` to reconnect a clearly
  orphaned metric to the spine (a missing BELONGS_TO_DOMAIN / PART_OF_PRODUCT /
  SOURCES it plainly should have) when the fix is unambiguous from the source.

WHAT TO FIND AND REPORT:
1. LOOPS: cycles in the DECOMPOSES_INTO graph (a metric that decomposes, directly
   or transitively, into itself). Report them — do NOT break them (a real cyclic
   definition is information). Causal INFLUENCES feedback loops are expected and
   are also just reported.
2. ORPHANS: metric nodes with NO spine attachment (no BELONGS_TO_DOMAIN /
   PART_OF_PRODUCT / SOURCES edge). Reconnect only when the right spine id is
   obvious from the metric's source; otherwise list it.
3. LEAVES: metric nodes with no inbound AND no outbound metric->metric edge
   (neither structural nor causal) — likely under-connected. List them.
4. CAUSAL-VS-STRUCTURAL DUPLICATES: any INFLUENCES edge that duplicates an
   existing DECOMPOSES_INTO between the same pair (a definitional link miscast as
   causal). Report each duplicate pair so it can be de-duped.

OUTPUT:
Reply with a single JSON object (no prose outside it) summarizing your audit:
`{"loops": [[uid, uid, ...], ...], "orphans": [uid, ...], "leaves": [uid, ...],
"causal_structural_duplicates": [[from_uid, to_uid], ...],
"repairs_made": [{"edge": "...", "reason": "..."}], "notes": "..."}`.
The orchestrator merges this with its own Cypher-derived counts into the final
build report — be accurate and conservative.
"""


# ---------------------------------------------------------------------------
# User-prompt builders (deterministic; no I/O, no SDK, no Neo4j)
# ---------------------------------------------------------------------------


def _compact_json(value: Any) -> str:
    """Serialize a value to compact, deterministic JSON (sorted keys)."""
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _spine_block(spine_ids: dict[str, Any] | None) -> str:
    """Render the linkable spine ids (domains / products / platforms) for a slice.

    Only ids + names are surfaced (never node payloads) so the agent links
    against a stable, tiny allowlist and never invents a spine id.
    """
    spine = spine_ids or {}
    domains = spine.get("domains") or []
    products = spine.get("products") or []
    platforms = spine.get("platforms") or []
    lines = [
        "SPINE IDS — choose domain/product/platform ids ONLY from these:",
        "  domains:   " + _compact_json(domains),
        "  products:  " + _compact_json(products),
        "  platforms: " + _compact_json(platforms),
    ]
    return "\n".join(lines)


def build_user_prompt_for_phase(
    phase: int,
    *,
    slice_label: str,
    namespace: str | None = None,
    domain: str | None = None,
    metric_ids: list[str] | None = None,
    spine_ids: dict[str, Any] | None = None,
    focus: str | None = None,
) -> str:
    """Build the per-agent task prompt for a build phase.

    The prompt names the slice's selector (namespace/domain or an explicit
    metric_id list), reminds the agent to read its source via the doc tools and
    write via the graph tools, and (for the node phase) lists the linkable spine
    ids. It is a pure string renderer — no I/O — so the ``--dry-plan`` path can
    print it offline.

    Args:
        phase: 1 (nodes) | 2 (structural) | 3 (weave) | 4 (critique).
        slice_label: Human label for this slice (e.g. ``"namespace=google_ads"``).
        namespace: Optional ``source`` namespace selector for the slice.
        domain: Optional ``domain`` selector for the slice.
        metric_ids: Optional explicit metric_id list (used by the smoke slice and
            focused weave slices); when given, the agent works exactly these ids.
        spine_ids: The linkable spine-id allowlist
            (``{"domains", "products", "platforms"}``) for the node phase.
        focus: Optional one-line focus hint (weave phase: which causal area).

    Returns:
        A compact, ASCII-safe task prompt for the phase agent.
    """
    selector_lines: list[str] = [f"SLICE: {slice_label}"]
    if metric_ids:
        selector_lines.append(
            f"METRIC IDS ({len(metric_ids)}) — work exactly these "
            f"(read each with get_metric_source): {_compact_json(metric_ids)}"
        )
    else:
        ns = namespace or "(any)"
        dom = domain or "(any)"
        selector_lines.append(
            "ENUMERATE your slice first with "
            f"list_metrics(namespace={ns!r}, domain={dom!r}) — that is your"
            " complete worklist (skip any 'operational' namespace metric)."
        )

    if phase == 1:
        body = [
            "PHASE 1 — NODES. Create every metric node in this slice with all "
            "fields from get_metric_source, then attach each onto the spine "
            "(BELONGS_TO_DOMAIN / PART_OF_PRODUCT / SOURCES).",
            "",
            *selector_lines,
            "",
            _spine_block(spine_ids),
            "",
            "Create the node BEFORE its spine edges. When done, reply with a "
            "one-line tally (nodes created, spine edges drawn).",
        ]
    elif phase == 2:
        body = [
            "PHASE 2 — STRUCTURAL EDGES. All nodes exist. Draw DECOMPOSES_INTO "
            "edges (role + confidence:1.0) from each metric's formula_components "
            "/ formula_human / BC_2 SQL.",
            "",
            *selector_lines,
            "",
            "Read each metric's formula with get_metric_source (and get_bc2_sql "
            "when the text is thin). Draw one edge per real component, with the "
            "correct arithmetic role. When done, reply with a one-line tally of "
            "DECOMPOSES_INTO edges drawn.",
        ]
    elif phase == 3:
        body = [
            "PHASE 3 — WEAVE CAUSAL. All nodes + structural edges exist. Add "
            "INFLUENCES edges (relation:'llm_causal', confidence tier 0.8/0.6/"
            "0.4, low evidence_mass, a concrete mechanism, cross_domain flag) by "
            "reasoning over notes / depends_on / pipeline.",
            "",
            *selector_lines,
        ]
        if focus:
            body += ["", f"FOCUS: {focus}"]
        body += [
            "",
            "Do NOT duplicate any existing DECOMPOSES_INTO as a causal edge, and "
            "do NOT import external causal seeds — derive every link yourself. "
            "When done, reply with a one-line tally of INFLUENCES edges drawn.",
        ]
    elif phase == 4:
        body = [
            "PHASE 4 — CRITIQUE. The graph is built. Audit it: find loops "
            "(report, do not break), orphans (reconnect only when obvious), "
            "leaves, and any causal edge that duplicates a structural one.",
            "",
            "Use kg_status + search_nodes to inspect. Reply with the single JSON "
            "object described in your instructions (loops / orphans / leaves / "
            "causal_structural_duplicates / repairs_made / notes).",
        ]
    else:  # pragma: no cover - phases are 1..4
        raise ValueError(f"Unknown build phase {phase!r}; expected 1, 2, 3, or 4.")

    return "\n".join(body).encode("ascii", "ignore").decode("ascii")


#: System prompt indexed by phase number, for the orchestrator + ``--dry-plan``.
PHASE_SYSTEM: dict[int, str] = {
    1: NODE_SYSTEM,
    2: STRUCTURAL_SYSTEM,
    3: WEAVE_SYSTEM,
    4: CRITIQUE_SYSTEM,
}

#: Human label for each phase (used in plan output + event emission).
PHASE_LABEL: dict[int, str] = {
    0: "spine-seed",
    1: "nodes",
    2: "structural",
    3: "weave",
    4: "critique",
}


# ---------------------------------------------------------------------------
# Structured-output fallback schema (used only when SDK tool-calling is
# unavailable — the agent returns {nodes, edges} and an applier writes them).
# ---------------------------------------------------------------------------

#: JSON Schema for the fallback path: the agent returns the nodes + edges it
#: would have written, and :mod:`harness.agentic.engine` applies them through
#: arbitration (``write_node_model`` / ``upsert_edge``) instead of via MCP.
FALLBACK_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nodes", "edges"],
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "description": (
                    "A Metric node payload (field names matching the Metric "
                    "model: metric_uid, metric_id, display_name, node_kind, "
                    "has_endpoint, domain_ids, product_ids, platform_ids, ...)."
                ),
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rel_type", "from_label", "from_key", "to_label", "to_key"],
                "properties": {
                    "rel_type": {"type": "string"},
                    "from_label": {"type": "string"},
                    "from_key": {"type": "string"},
                    "to_label": {"type": "string"},
                    "to_key": {"type": "string"},
                    "props": {"type": "object"},
                },
            },
        },
    },
}
