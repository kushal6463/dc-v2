"""System / user prompts for the metric-ingestion proposer agent.

The proposer is a per-dashboard sub-agent (implementation plan section 5b): it
receives the deterministic *drafts* from the pre-pass plus a small RAG shortlist
of spine nodes to link against, and returns schema-constrained proposals (schema
section 8) — it never writes Neo4j.

:data:`PROPOSER_SYSTEM` fixes the agent's role and the hard rules (registry is
truth; classify only with allowed enum values; never invent ids/endpoints).
:func:`build_proposer_user_prompt` renders one dashboard's drafts + spine context
into a compact, deterministic task prompt.
"""

from __future__ import annotations

import json
import os
from typing import Any

# Allowed enum values surfaced to the agent so it classifies with the exact
# vocabulary from schema section 7 (kept in sync with harness.kg.models).
_ENUM_HINTS = {
    "unit_family": ["currency", "ratio", "percent", "count", "duration", "score"],
    "default_direction": ["higher_is_better", "lower_is_better", "target_is_best"],
    "causal_role": [
        "outcome",
        "mediator",
        "controllable",
        "constraint",
        "external",
        "ml_output",
        "untyped",
    ],
    "value_format": ["number", "currency", "percentage", "decimal"],
    "granularity": ["daily", "weekly", "monthly", "quarterly"],
    "scope_level": [
        "global",
        "platform",
        "channel",
        "dashboard",
        "campaign",
        "product",
        "customer",
        "model",
    ],
    "category": [
        "advertising",
        "revenue",
        "traffic",
        "email",
        "customer",
        "sms",
        "google_ads",
        "meta_ads",
        "efficiency",
        "comparison",
        "financial",
        "marketing",
        "product",
        "operational",
    ],
    "aggregation": ["level", "sum", "avg", "rate", "ratio", "median"],
    "measurement_type": ["direct", "derived", "modeled", "forecast", "status"],
    # The 15 OpenAPI ChartType values — used to classify each metric's
    # chart_type and to pick the uic:<chart_type> node for the VISUALIZES edge.
    "chart_type": [
        "line",
        "area",
        "bar",
        "horizontal_bar",
        "grouped_bar",
        "pie",
        "donut",
        "sankey",
        "heatmap",
        "table",
        "sparkline",
        "scatter",
        "treemap",
        "gauge",
        "funnel",
    ],
}


def _render_enum_hints() -> str:
    """Render the allowed-enum vocabulary as a compact, readable block."""
    lines = [f"- {field}: {', '.join(values)}" for field, values in _ENUM_HINTS.items()]
    return "\n".join(lines)


PROPOSER_SYSTEM: str = f"""\
You are a senior metrics ontologist for a causal knowledge graph. You harvest
one analytics dashboard at a time and emit *proposals only* — you never write the
database; a separate arbitration writer applies approved proposals.

GROUND RULES (follow exactly):
1. The chart registry is the source of truth for ids and semantics. The OpenAPI
   spec only enriches endpoint paths; where the sparse OpenAPI data conflicts
   with the registry, the registry wins. Never invent ids, endpoints, or values.
2. Reconcile the per-entry Metric drafts: group drafts that describe the same
   underlying concept by setting a shared normalized `concept_key` (the base
   concept, e.g. "revenue", "roas", "conversion_rate"), and set `metric_base`
   to that same normalized base. Keep each draft's `metric_uid`/`canonical_id`
   exactly as given.
3. Classify each metric using ONLY these allowed enum values (omit a field if
   you are unsure rather than guessing an out-of-vocabulary value):
{_render_enum_hints()}
4. Read each metric's `formula_text`. When the formula references other metrics
   that exist in THIS dashboard's metric set, propose a `DECOMPOSES_INTO` edge
   from the metric to each referenced component metric, with confidence 1.0 and
   `review_state` "proposed". Set `is_derived` true and `formula_status`
   "parsed" for metrics that have a formula.
5. Resolve `domain_ids` and `product_ids` by choosing ONLY from the spine ids
   provided in the user message. Never invent a domain or product id. Use an
   empty array when you are unsure.
6. Classify each metric's `chart_type` using ONLY the 15 allowed ChartType
   values (see the enum list above). Choose the best fit from the metric's
   `chart_id`, `display_name`, `formula_text`, and `how_to_read`/
   `decisions_answered` guidance. Defaults when ambiguous: a single headline KPI
   value -> "gauge" (or "line" if it is plainly a trend), tabular/list data ->
   "table". Set the chosen value on the Metric payload as `chart_type`. Do NOT
   propose any UIComponent node — the chart-type nodes already exist (they are
   the pre-seeded generalised `uic:<chart_type>` nodes).
7. Propose these edges with the section-8 relationship-payload shape:
   - VISUALIZES: UIComponent -> Metric. The `from` endpoint is the pre-seeded
     generalised chart-type node — `from_label` "UIComponent", `from_id`
     "uic:<chart_type>" (the same value you set on the metric in rule 6) — and
     the `to` endpoint is the metric (`to_label` "Metric", `to_id` the
     `metric_uid`). One generalised node VISUALIZES many metrics.
   - SHOWN_ON: Metric -> Dashboard (each metric is shown on this dashboard).
   - BELONGS_TO_DOMAIN: Metric -> Domain (only for chosen `domain_ids`).
   - PART_OF_PRODUCT: Metric -> IntelligenceProduct (only for chosen
     `product_ids`).
   - DECOMPOSES_INTO: Metric -> Metric (from formulas, rule 4).
8. Output STRICTLY the JSON object described by the provided schema: a single
   top-level object with a "proposals" array. Each proposal has `target_label`
   (only "Dashboard" or "Metric"), `target_id`, a `payload` object (the node's
   properties), and a `relationship_payloads` array. Do not emit prose outside
   the JSON.
"""


def _compact_json(value: Any) -> str:
    """Serialize a value to compact, deterministic JSON (sorted keys)."""
    return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)


def _spine_summary(spine: dict[str, Any]) -> str:
    """Render the spine context (business + domain/product ids) compactly.

    Only the linkable identifiers and names are included — never the whole graph
    — to keep the proposer's context window flat (plan section 6, tactic 5).
    """
    business = spine.get("business") or {}
    domains = spine.get("domains") or []
    products = spine.get("products") or []
    platforms = spine.get("platforms") or []

    lines = [
        "BUSINESS: " + _compact_json(business),
        "AVAILABLE DOMAIN IDS (choose only from these):",
        _compact_json([{"domain_id": d.get("domain_id"), "name": d.get("name")} for d in domains]),
        "AVAILABLE PRODUCT IDS (choose only from these):",
        _compact_json(
            [{"product_id": p.get("product_id"), "display_name": p.get("display_name")} for p in products]
        ),
    ]
    if platforms:
        lines += [
            "AVAILABLE PLATFORM IDS (choose only from these):",
            _compact_json(
                [{"platform_id": p.get("platform_id"), "platform_name": p.get("platform_name")} for p in platforms]
            ),
        ]
    return "\n".join(lines)


def _trunc(value: Any, limit: int = 160) -> Any:
    """Truncate a string (or each of up to 3 strings in a list) to bound size."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        return [str(v)[:limit] for v in value[:3]]
    return value


def _slim_metric(metric: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the agent needs to CLASSIFY a metric.

    The full draft is merged back onto the agent's output at apply-time
    (proposer ``_draft_index``), so the prompt only needs identity +
    classification inputs. Trimming long ``how_to_read``/``decisions_answered``/
    ``narration`` text keeps the stdin payload small — large prompts make the
    agent SDK's stdin handshake deadlock (observed on the 13-15 metric boards).
    """
    base = {
        "metric_uid": metric.get("metric_uid"),
        "metric_id": metric.get("metric_id"),
        "display_name": metric.get("display_name"),
        "chart_id": metric.get("chart_id"),
        "formula_text": _trunc(metric.get("formula_text"), 200),
    }
    # Minimal mode (KG_MINIMAL_PROMPT=1): drop the verbose reader-guidance text.
    # A few dashboards' full text reliably stalls the agent; with id/title/formula
    # only it completes. The full registry text is still stored on the node (it is
    # merged from the deterministic draft at apply-time) — only the agent's
    # classification (chart_type/domain/causal_role) uses the slimmer context.
    if os.environ.get("KG_MINIMAL_PROMPT"):
        return base
    return {
        **base,
        "formula_explanation": _trunc(metric.get("formula_explanation"), 240),
        "how_to_read": _trunc(metric.get("how_to_read"), 120),
        "decisions_answered": _trunc(metric.get("decisions_answered"), 120),
    }


def build_proposer_user_prompt(
    dashboard_id: str,
    drafts: dict[str, Any],
    spine: dict[str, Any],
) -> str:
    """Build the per-dashboard task prompt for the proposer agent.

    Args:
        dashboard_id: The dashboard being harvested.
        drafts: The pre-pass slice for this dashboard
            (``{"dashboard", "components" (always empty), "metrics"}``). Each
            metric draft carries the folded-in chart-registry semantics
            (``chart_id``/``formula_explanation``/``how_to_read``/
            ``decisions_answered``/``narration_text``) that the agent uses to
            classify ``chart_type``.
        spine: The RAG spine context (``{"business", "domains", "products",
            "platforms"?}``) the proposer may link against.

    Returns:
        A compact, deterministic prompt string instructing the agent to emit
        section-8 proposals for this dashboard's nodes and edges.
    """
    dashboard_draft = drafts.get("dashboard", {})
    metrics = drafts.get("metrics", [])

    sections = [
        f"DASHBOARD: {dashboard_id}",
        "",
        "SPINE CONTEXT (link only to these ids):",
        _spine_summary(spine),
        "",
        "DASHBOARD DRAFT (propose one Dashboard node from this):",
        _compact_json(dashboard_draft),
        "",
        f"METRIC DRAFTS ({len(metrics)}) — reconcile concept_key/metric_base, "
        "classify with the allowed enums, classify each metric's chart_type from "
        "its folded-in chart_id/how_to_read/formula_text, and read each "
        "formula_text for DECOMPOSES_INTO edges:",
        _compact_json([_slim_metric(m) for m in metrics]),
        "",
        "TASK: Emit proposals for the Dashboard and every Metric above. Do NOT "
        "propose UIComponent nodes — the generalised chart-type nodes "
        "(uic:<chart_type>) already exist. For each metric, set chart_type on its "
        "payload and emit a VISUALIZES edge from UIComponent uic:<chart_type> to "
        "the metric, plus the SHOWN_ON / BELONGS_TO_DOMAIN / PART_OF_PRODUCT / "
        "DECOMPOSES_INTO edges per your rules. Keep each node's identity fields "
        "exactly as drafted. Return ONLY the JSON object matching the schema.",
    ]
    # ASCII-sanitize: some registry text is mojibake/non-ASCII (double-encoded
    # UTF-8, e.g. 'Ã—'), which can deadlock the agent SDK's stdin stream. The
    # original text is preserved on the stored node (merged from the draft at
    # apply-time); the agent only needs a clean, readable prompt.
    return "\n".join(sections).encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# M3 causal layer — pointwise judge + refuter (implementation plan section 5e)
# ---------------------------------------------------------------------------
#
# The causal pass asks the model about ONE isolated candidate metric pair at a
# time (pointwise), never the whole graph, so each judgement is independent and
# the context window stays flat. We deliberately DO NOT trust the model's stated
# confidence number (verbalized confidence is badly mis-calibrated, ECE ~39%);
# the edge confidence comes from the agreement fraction across N self-consistency
# samples plus a refuter's verdict, folded into a Beta(alpha, beta) posterior in
# :mod:`harness.ingest.causal`. The judge's job is only to classify the single
# pair and supply a concrete mechanism.

#: Output schema for one pointwise causal judgement.
CAUSAL_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relationship", "direction", "mechanism_text", "abstain"],
    "properties": {
        "relationship": {
            # "causal" -> a plausible cause/effect link (-> INFLUENCES candidate);
            # "correlational" -> they co-move but no mechanism (-> CORRELATES_WITH);
            # "none" -> unrelated.
            "type": "string",
            "enum": ["causal", "correlational", "none"],
        },
        "direction": {
            "type": "string",
            "enum": ["a_to_b", "b_to_a", "bidirectional", "none"],
        },
        "mechanism_text": {
            "type": "string",
            "description": (
                "One concrete sentence naming the mechanism by which A affects B "
                "(or B affects A). MUST be empty when relationship is not causal "
                "or when you cannot name a real mechanism — an empty mechanism "
                "rejects the candidate."
            ),
        },
        "lag_days": {
            "type": ["number", "null"],
            "description": "Typical lag in days from cause to effect, or null.",
        },
        "abstain": {
            "type": "boolean",
            "description": "True if you cannot judge this pair (treated as no support).",
        },
    },
}

#: Output schema for the refuter's verdict on a proposed causal link.
REFUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["refuted", "reason"],
    "properties": {
        "refuted": {
            "type": "boolean",
            "description": (
                "True if the proposed causal link is implausible, reversed, "
                "confounded, or a mere definitional/correlational artifact. "
                "Default to True when uncertain."
            ),
        },
        "reason": {"type": "string"},
    },
}


CAUSAL_JUDGE_SYSTEM: str = """\
You are a careful causal-inference analyst for a marketing/e-commerce metrics
knowledge graph. You judge ONE ordered pair of metrics (A, B) in isolation and
decide whether A plausibly CAUSES / influences B.

RULES (follow exactly):
1. Judge ONLY the single pair given. Do not reason about other metrics.
2. Classify `relationship`:
   - "causal": there is a concrete real-world mechanism by which a change in A
     produces a change in B (or B in A — set `direction`). Example: ad spend ->
     impressions ("budget wins ad auctions").
   - "correlational": they reliably co-move but you cannot name a direct
     mechanism, or the link is purely definitional/shared-input.
   - "none": no real relationship.
3. `mechanism_text`: when (and only when) `relationship` is "causal", give ONE
   concrete sentence naming the mechanism. If you cannot name a real mechanism,
   leave it EMPTY and do NOT claim "causal" — an empty mechanism rejects the
   candidate. Never restate the metric names as a pseudo-mechanism
   ("A affects B because A influences B" is NOT a mechanism).
4. Do not confuse a formula/definitional relationship (B is computed FROM A)
   with causation — that is "correlational" at most, because it is already
   captured by the deterministic formula edges.
5. Be conservative. A spurious causal claim is worse than abstaining. Set
   `abstain` true if you genuinely cannot judge.
6. Output STRICTLY the JSON object described by the schema; no prose outside it.
"""


REFUTER_SYSTEM: str = """\
You are a skeptical reviewer whose job is to REFUTE a proposed causal link
between two metrics. Assume the proposal is wrong until it survives scrutiny.

Try hard to refute it on any of these grounds:
- the direction is reversed (B actually drives A);
- the association is confounded by a third driver (e.g. seasonality, budget);
- it is merely definitional / a shared input, not causation;
- the named mechanism is vacuous, circular, or restates the metric names;
- the two metrics are unrelated.

Set `refuted` true if ANY of these clearly applies, AND default to true when you
are uncertain — the bar for a causal edge is high. Set `refuted` false only when
the mechanism is concrete and the causal direction is clearly the more plausible
reading. Output STRICTLY the JSON object described by the schema.
"""


def _metric_brief(metric: dict[str, Any]) -> dict[str, Any]:
    """A compact, judge-ready brief of one metric (identity + causal context)."""
    return {
        "metric_uid": metric.get("metric_uid"),
        "name": metric.get("display_name") or metric.get("metric_id"),
        "concept": metric.get("concept_key") or metric.get("metric_base"),
        "causal_role": metric.get("causal_role"),
        "domains": metric.get("domain_ids"),
        "formula": _trunc(metric.get("formula_text"), 160),
        "description": _trunc(metric.get("description"), 160),
    }


def build_causal_judge_prompt(
    a: dict[str, Any], b: dict[str, Any], *, signal: str | None = None
) -> str:
    """Build the pointwise judge prompt for the ordered pair (A -> B?).

    Args:
        a: The candidate cause metric (dict with identity + causal fields).
        b: The candidate effect metric.
        signal: Optional one-line description of the structural signal that made
            this a candidate (e.g. "co-occur on dashboard ceo-pulse"), given so
            the model knows why the pair was surfaced (it must still judge on the
            mechanism, not on the signal alone).

    Returns:
        A compact, ASCII-safe judge prompt.
    """
    lines = [
        "Judge whether metric A causally influences metric B.",
        "",
        "A (candidate cause): " + _compact_json(_metric_brief(a)),
        "B (candidate effect): " + _compact_json(_metric_brief(b)),
    ]
    if signal:
        lines += ["", f"Why surfaced (structural signal only): {signal}"]
    lines += [
        "",
        "Decide `relationship` (causal / correlational / none) and, only if "
        "causal, give a concrete one-sentence `mechanism_text` and `direction`. "
        "Return ONLY the JSON object matching the schema.",
    ]
    return "\n".join(lines).encode("ascii", "ignore").decode("ascii")


def build_refuter_prompt(
    a: dict[str, Any], b: dict[str, Any], mechanism: str
) -> str:
    """Build the refuter prompt for a proposed ``A -> B`` link with a mechanism."""
    lines = [
        "A reviewer proposed that metric A causally influences metric B.",
        "",
        "A (proposed cause): " + _compact_json(_metric_brief(a)),
        "B (proposed effect): " + _compact_json(_metric_brief(b)),
        f"Proposed mechanism: {mechanism}",
        "",
        "Try to refute this causal claim. Return ONLY the JSON object matching "
        "the schema.",
    ]
    return "\n".join(lines).encode("ascii", "ignore").decode("ascii")


# ---------------------------------------------------------------------------
# M3 causal LINKING — per-metric LLM edge builder (implementation plan §5e+)
# ---------------------------------------------------------------------------
#
# Unlike the pairwise judge (which asks "is A->B causal?"), the linker asks the
# model to look at ONE subject metric plus a SHORTLIST of real candidate metrics
# and pick the true DECOMPOSES_INTO / CORRELATES_WITH / INFLUENCES edges among
# them. The model may ONLY return target_uids copied from the shortlist (the
# caller drops anything else), so it cannot hallucinate a node — the same
# "resolve to existing ids" discipline the metric proposer uses.

#: Output schema for one subject metric's links.
CAUSAL_LINK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["edges"],
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target_uid", "type", "direction", "mechanism"],
                "properties": {
                    "target_uid": {
                        "type": "string",
                        "description": "MUST be copied verbatim from a candidate metric_uid.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["DECOMPOSES_INTO", "CORRELATES_WITH", "INFLUENCES"],
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["to_target", "from_target"],
                        "description": (
                            "'to_target' = subject -> target (subject decomposes "
                            "into / drives the target); 'from_target' = target -> "
                            "subject."
                        ),
                    },
                    "mechanism": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
            },
        }
    },
}


CAUSAL_LINK_SYSTEM: str = """\
You are a senior marketing-analytics ontologist building the causal layer of a
metric knowledge graph. You are given ONE subject metric (its formula, platform,
concept, role) and a SHORTLIST of real candidate metrics. Identify the true
relationships between the subject and those candidates.

EDGE TYPES:
- DECOMPOSES_INTO: the subject's formula is COMPUTED FROM the target (the target
  is a component/input of the subject's formula). Read the subject's `formula`.
  e.g. ROAS = Revenue / Spend -> ROAS DECOMPOSES_INTO Revenue and Spend.
- CORRELATES_WITH: the two metrics reliably co-move but neither is a formula
  component nor a clear cause (statistical association). Bidirectional.
- INFLUENCES: the subject causally drives the target (or the target drives the
  subject) via a real-world mechanism that is NOT a formula identity. Use
  `direction` to say which way the causation runs.

RULES (follow exactly):
1. `target_uid` MUST be copied verbatim from the provided candidate list. NEVER
   invent a uid or a metric. If no candidate is a true relationship, return an
   empty `edges` list.
2. Prefer SAME-PLATFORM components and drivers: a Meta ROAS decomposes into Meta
   spend / Meta revenue (not blended), and is driven by Meta CPC / Meta CTR (not
   Google's). Use the `platform` field on the subject and candidates.
3. DECOMPOSES_INTO is for genuine formula components only — do not invent
   decompositions the `formula` does not support.
4. Every edge needs a concrete one-sentence `mechanism`. No mechanism -> omit it.
5. Be precise, not exhaustive: only emit edges you are confident are real. A
   sparse, correct set beats a dense, wrong one.
6. Output STRICTLY the JSON object described by the schema; no prose.
"""


def build_causal_link_prompt(
    subject_brief: dict[str, Any], candidates: list[dict[str, Any]]
) -> str:
    """Build the per-subject linking prompt (subject + a shortlist of candidates).

    Both ``subject_brief`` and each ``candidates`` entry are already-enriched
    metric briefs (identity + derived ``platform`` + ``concept`` + ``formula``),
    built by the caller so platform derivation stays in the causal module.
    """
    lines = [
        "SUBJECT METRIC (find its components, drivers, effects, and correlates):",
        _compact_json(subject_brief),
        "",
        f"CANDIDATE METRICS — choose target_uid ONLY from these {len(candidates)} "
        "(copy the uid verbatim):",
        _compact_json(candidates),
        "",
        "Return the true DECOMPOSES_INTO / CORRELATES_WITH / INFLUENCES edges "
        "between the SUBJECT and the candidates as the JSON object matching the "
        "schema. Empty list if none are real.",
    ]
    return "\n".join(lines).encode("ascii", "ignore").decode("ascii")
