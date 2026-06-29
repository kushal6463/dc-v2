"""LLM extraction of Policy + Threshold draft fields from document text.

:func:`extract_governance` takes free text (a pasted policy doc, a JSON/markdown
export, etc.) plus optional metric context and returns a draft
``{"policy": {...}, "threshold": {...}}`` whose keys match the
:class:`~harness.kg.models.Policy` / :class:`~harness.kg.models.Threshold` fields.
The draft is for the wizard to *prefill* — a human reviews/edits before the write
(``POST /api/governance``); nothing here touches the graph.

Industry-standard benchmarks are filled from the model's own training knowledge
(tagged ``industry_source="llm:claude-opus-4-8"`` + ``industry_as_of``) — a live
web search can be swapped in later without changing this schema.
"""

from __future__ import annotations

from typing import Any

from harness.agent.engine import propose_structured

#: Default model for governance extraction (kept explicit for reproducibility).
EXTRACT_MODEL = "claude-opus-4-8"

#: The benchmark provenance + knowledge-as-of stamped on LLM-derived industry
#: standards (so the UI can show "where from / how old").
_LLM_SOURCE = "llm:claude-opus-4-8"
_LLM_AS_OF = "2025-01-01"

#: JSON Schema constraining the model's output to the Policy + Threshold fields.
GOVERNANCE_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["policy", "threshold"],
    "additionalProperties": False,
    "properties": {
        "policy": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "policy_name": {"type": "string"},
                "description": {"type": "string"},
                "policy_type": {
                    "type": "string",
                    "enum": [
                        "access",
                        "interpretation",
                        "alerting",
                        "escalation",
                        "approval",
                        "action_guardrail",
                        "data_quality",
                    ],
                },
                "condition_operator": {
                    "type": "string",
                    "enum": [
                        "lt",
                        "lte",
                        "gt",
                        "gte",
                        "eq",
                        "neq",
                        "between",
                        "outside",
                    ],
                },
                "condition_value": {"type": "number"},
                "condition_value_high": {"type": "number"},
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info", "blocking"],
                },
                "approval_required": {"type": "boolean"},
            },
        },
        "threshold": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "threshold_type": {
                    "type": "string",
                    "enum": [
                        "static",
                        "percentile",
                        "seasonal",
                        "warning",
                        "critical",
                        "target",
                        "anomaly",
                        "sla",
                        "budget",
                    ],
                },
                "operator": {
                    "type": "string",
                    "enum": [
                        "lt",
                        "lte",
                        "gt",
                        "gte",
                        "eq",
                        "neq",
                        "between",
                        "outside",
                    ],
                },
                "direction": {
                    "type": "string",
                    "enum": [
                        "higher_is_better",
                        "lower_is_better",
                        "target_is_best",
                    ],
                },
                "unit": {"type": "string"},
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info", "blocking"],
                },
                "p95_val": {"type": "number"},
                "p85_val": {"type": "number"},
                "p75_val": {"type": "number"},
                "p50_val": {"type": "number"},
                "industry_standard_val": {"type": "number"},
                "industry_min_val": {"type": "number"},
                "industry_max_val": {"type": "number"},
                "industry_source": {"type": "string"},
                "industry_as_of": {"type": "string"},
                "current_val": {"type": "number"},
                "target_value_num": {"type": "number"},
                "explanation": {"type": "string"},
            },
        },
    },
}

_SYSTEM_PROMPT = (
    "You are a marketing-analytics governance analyst. Given a document and a "
    "target metric, extract TWO things that match the provided JSON schema:\n"
    "1) a POLICY — the rule the business must obey (an alert/guardrail), with its "
    "breach operator + value and a severity.\n"
    "2) a THRESHOLD — the metric's breach lines, including the company's own "
    "percentile distribution (p50<p75<p85<p95) and an INDUSTRY BENCHMARK "
    "(industry_standard_val plus an industry_min_val..industry_max_val band).\n\n"
    "Rules:\n"
    "- Use the metric context to set `direction` (higher_is_better vs "
    "lower_is_better). For lower-is-better metrics (CPC, CPA, cost), the percentile "
    "ladder DESCENDS — the lower tail is the 'good' end, so p95 < p50.\n"
    "- If the document does not state an industry standard, fill it from your own "
    "training knowledge of typical Google Ads / digital-marketing benchmarks, and "
    f'set industry_source to "{_LLM_SOURCE}" and industry_as_of to "{_LLM_AS_OF}".\n'
    "- Only include fields you are confident about; OMIT unknown fields entirely "
    "(do not invent precise numbers you cannot justify). Numbers must be plain "
    "numerics in the metric's natural unit (e.g. ROAS 2.5, CTR as a percent 3.5, "
    "CPC in dollars 2.69).\n"
    "- Return ONLY the JSON object."
)


def _build_user_prompt(
    text: str, *, metric_uid: str | None, metric_name: str | None
) -> str:
    """Assemble the task prompt from the metric context + the document text."""
    ctx_lines = []
    if metric_uid:
        ctx_lines.append(f"metric_uid: {metric_uid}")
    if metric_name:
        ctx_lines.append(f"metric display name: {metric_name}")
    ctx = "\n".join(ctx_lines) or "(no metric context provided)"
    return (
        f"TARGET METRIC\n{ctx}\n\n"
        f"DOCUMENT\n-----\n{text.strip()}\n-----\n\n"
        "Extract the policy and threshold per the schema."
    )


async def extract_governance(
    *,
    text: str,
    metric_uid: str | None = None,
    metric_name: str | None = None,
) -> dict[str, Any]:
    """Parse ``text`` into a draft ``{"policy": {...}, "threshold": {...}}``.

    Args:
        text: The raw document text (pasted or read from an uploaded file).
        metric_uid: The metric the governance is being authored against, if known.
        metric_name: The metric's human-readable name, if known.

    Returns:
        ``{"policy": {...}, "threshold": {...}}`` with only the fields the model
        was confident about (empty dicts when nothing was extracted).
    """
    result = await propose_structured(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(
            text, metric_uid=metric_uid, metric_name=metric_name
        ),
        schema=GOVERNANCE_EXTRACTION_SCHEMA,
        max_turns=1,
        model=EXTRACT_MODEL,
    )
    return {
        "policy": result.get("policy") or {},
        "threshold": result.get("threshold") or {},
    }
