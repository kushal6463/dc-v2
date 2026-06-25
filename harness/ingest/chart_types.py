"""Chart-type catalog + deterministic classifier (hybrid chart typing).

The chart-registry (``docs/frd-docs/chart-registry.json``) carries an
``entity_type`` (metric / chart / table) but **no** ``chart_type`` — the visual a
widget renders as. The authoritative source is the BC_2 backend, which sets a
required ``ChartType`` on every ``ChartResponse`` (15-value enum), mirrored by the
15 frontend chart components.

This module provides:

* :data:`CANONICAL_CHART_TYPES` — the canonical type catalog (the 15 OpenAPI
  ``ChartType`` values + ``kpi_card`` for metric cards), each with a label,
  description, and example concepts.
* :func:`classify_deterministic` — the ground-truth + high-confidence layer: it
  resolves ``entity_type=metric`` → ``kpi_card`` and ``table`` → ``table`` with
  certainty, and ``entity_type=chart`` by an explicit concept/title name match.
  Anything it cannot resolve confidently is returned as ``None`` (an *ambiguous*
  chart) so the hybrid pipeline can hand only that tail to an LLM.

Keeping the deterministic rules here (vs. inline in a script) makes them testable
and reusable, and keeps the LLM pass small — only the genuinely ambiguous charts.
"""

from __future__ import annotations

import re
from typing import Any

#: The canonical chart-type catalog. ``id`` matches the OpenAPI ``ChartType`` enum
#: where one exists; ``kpi_card`` is added for ``entity_type=metric`` single-value
#: cards (rendered as a number + sparkline, not one of the 15 plot types).
CANONICAL_CHART_TYPES: list[dict[str, Any]] = [
    {"id": "kpi_card", "label": "KPI Card", "in_openapi_enum": False,
     "description": "Single headline value + delta + sparkline trend (a metric card, not a plot).",
     "examples": ["blended.roas", "blended.cac", "magento.revenue"]},
    {"id": "line", "label": "Line Chart", "in_openapi_enum": True,
     "description": "Time series / trend / cumulative metric over time.",
     "examples": ["alert_trend", "cac_trend", "revenue_trend", "saturation_curves"]},
    {"id": "area", "label": "Area Chart", "in_openapi_enum": True,
     "description": "Cumulative or stacked time series; composition over time.",
     "examples": ["daily_spend_trend", "channel_mix_evolution"]},
    {"id": "bar", "label": "Vertical Bar Chart", "in_openapi_enum": True,
     "description": "Categorical comparison / ranked distribution / histogram.",
     "examples": ["path_length_distribution", "churn_risk_distribution", "roas_distribution"]},
    {"id": "horizontal_bar", "label": "Horizontal Bar Chart", "in_openapi_enum": True,
     "description": "Ranked categories, long labels.",
     "examples": ["top_states_by_ltv", "vtc_impact", "top_products"]},
    {"id": "grouped_bar", "label": "Grouped Bar Chart", "in_openapi_enum": True,
     "description": "Multi-series side-by-side categorical comparison.",
     "examples": ["assisted_conversions", "audience_type_comparison", "device_conversion"]},
    {"id": "pie", "label": "Pie Chart", "in_openapi_enum": True,
     "description": "Simple proportional breakdown (prefer donut).",
     "examples": ["ad_strength_distribution"]},
    {"id": "donut", "label": "Donut Chart", "in_openapi_enum": True,
     "description": "Proportional breakdown with a center total.",
     "examples": ["churn_risk_distribution", "rating_distribution"]},
    {"id": "sankey", "label": "Sankey Flow", "in_openapi_enum": True,
     "description": "Multi-step flows / path / channel attribution.",
     "examples": ["revenue_attribution_sankey", "stage_migration_flow"]},
    {"id": "heatmap", "label": "Heatmap (Matrix)", "in_openapi_enum": True,
     "description": "2D categorical performance (rows x columns).",
     "examples": ["network_device_heatmap", "cohort_retention_heatmap", "rfm_heatmap"]},
    {"id": "table", "label": "Data Table", "in_openapi_enum": True,
     "description": "Detailed tabular data, many columns/rows.",
     "examples": ["alert_history", "campaign_performance_table"]},
    {"id": "sparkline", "label": "Sparkline", "in_openapi_enum": True,
     "description": "Inline mini time series within a KPI card.",
     "examples": ["metric trend sparkline"]},
    {"id": "scatter", "label": "Scatter Plot", "in_openapi_enum": True,
     "description": "Bivariate relationship / bubble chart.",
     "examples": ["reach_frequency_scatter", "campaign_performance_scatter"]},
    {"id": "treemap", "label": "Treemap", "in_openapi_enum": True,
     "description": "Hierarchical proportional breakdown.",
     "examples": ["campaign_treemap", "share_treemap"]},
    {"id": "gauge", "label": "Gauge / Pacing", "in_openapi_enum": True,
     "description": "KPI pacing / health score (0-100%).",
     "examples": ["budget_pacing", "monthly_pacing", "todays_pacing"]},
    {"id": "funnel", "label": "Funnel Chart", "in_openapi_enum": True,
     "description": "Conversion / completion stages, dropoff.",
     "examples": ["conversion_funnel", "funnel_by_channel"]},
]

#: The valid chart-type id set (for validation of LLM answers).
CHART_TYPE_IDS: frozenset[str] = frozenset(c["id"] for c in CANONICAL_CHART_TYPES)

#: High-confidence substring → chart_type rules for ``entity_type=chart`` entries,
#: applied in order (first match wins). Derived from the BC_2 backend/frontend
#: naming conventions; only UNAMBIGUOUS tokens live here (the rest go to the LLM).
_NAME_RULES: tuple[tuple[str, str], ...] = (
    ("sankey", "sankey"),
    ("scatter", "scatter"),
    ("treemap", "treemap"),
    ("heatmap", "heatmap"),
    ("matrix", "heatmap"),
    ("funnel", "funnel"),
    ("gauge", "gauge"),
    ("pacing", "gauge"),
    ("sparkline", "sparkline"),
    ("_trend", "line"),
    ("_curve", "line"),
    ("forecast", "line"),
    ("over_time", "line"),
    ("evolution", "area"),
    ("_comparison", "grouped_bar"),
    ("vs_", "grouped_bar"),
    ("donut", "donut"),
    ("_pie", "pie"),
    ("treemap", "treemap"),
)


def _norm(value: Any) -> str:
    """Lowercase a concept/title for matching (non-alphanumerics → ``_``)."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower())


def classify_deterministic(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a chart-registry entry's ``chart_type`` deterministically.

    Ground-truth + high-confidence layer of the hybrid typer:

    * ``entity_type == "metric"`` → ``kpi_card`` (certain — a single-value card).
    * ``entity_type == "table"``  → ``table`` (certain).
    * ``entity_type == "chart"``  → the first matching :data:`_NAME_RULES` token
      on the concept/title (high confidence).

    Args:
        entry: A chart-registry entry (needs ``entity_type``; ``concept`` /
            ``chart_id`` / ``title`` drive the name match).

    Returns:
        ``{"chart_type", "confidence", "source"}`` when resolved, or ``None`` when
        the entry is an *ambiguous chart* that must go to the LLM pass.
    """
    etype = entry.get("entity_type")
    if etype == "metric":
        return {"chart_type": "kpi_card", "confidence": "high", "source": "entity_type"}
    if etype == "table":
        return {"chart_type": "table", "confidence": "high", "source": "entity_type"}

    # entity_type == "chart" (or unknown): try the high-confidence name rules.
    hay = " ".join(
        _norm(entry.get(k)) for k in ("concept", "chart_id", "id", "title")
    )
    for token, ctype in _NAME_RULES:
        if token in hay:
            return {"chart_type": ctype, "confidence": "medium", "source": f"name:{token}"}
    return None
