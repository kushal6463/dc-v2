"""FastMCP stdio server exposing graph write/read tools (``mcp__graph__*``).

A single :class:`~mcp.server.fastmcp.FastMCP` instance named ``"graph"`` reused
by both the Claude Code CLI registration and the Agent SDK harness. Tools surface
as ``mcp__graph__create_business_node`` etc.

Write discipline: *every* mutation goes through
:mod:`harness.kg.arbitration` (the single arbitration writer) — node creates build
a validated :mod:`harness.kg.models` Pydantic model and call
:func:`~harness.kg.arbitration.write_node_model`; edges call
:func:`~harness.kg.arbitration.upsert_edge`. No tool writes Cypher directly.

The ``create_*`` tools take *flat scalar* arguments whose names match the schema
field names (pipe-delimited strings for list fields) so the confirm-before-create
hook can render a meaningful field table. Every tool returns a JSON string.

Run as a stdio server::

    uv run python -m harness.mcp.graph_server
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from harness.kg.arbitration import (
    append_edge_evidence,
    upsert_edge,
    write_node_model,
)
from harness.kg.config import REPO_ROOT
from harness.kg.driver import get_db
from harness.kg.models import (
    DECOMPOSES_RELATIONS,
    EDGE_TYPES,
    INFLUENCES_RELATIONS,
    NODE_KEY_FIELDS,
    NODE_LABELS,
    Business,
    Domain,
    IntelligenceProduct,
    Metric,
    Policy,
    Threshold,
)

mcp = FastMCP("graph")

#: Repo doc / data / seed paths the read-only doc tools read (never written).
_CHART_REGISTRY_PATH: Path = REPO_ROOT / "docs" / "frd-docs" / "chart-registry.json"
_OPENAPI_PATH: Path = REPO_ROOT / "docs" / "frd-docs" / "openapi.json"
_METRIC_NODES_PATH: Path = REPO_ROOT / "data" / "metric_nodes.rare_seeds.json"
_METRIC_REGISTRY_PATH: Path = REPO_ROOT / "data" / "metric_registry.rare_seeds.csv"
#: BC_2 offline snapshot root (dbt marts + backend repository source).
_BC2_ROOT: Path = Path("/Users/kushal/Desktop/kal/BC_2")


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _split_list(value: str | None) -> list[str] | None:
    """Parse a pipe-delimited string into a clean ``list[str]`` (or ``None``).

    Empty/whitespace-only input yields ``None`` so the field is omitted from the
    Pydantic model (it stays ``None`` and is stripped before the write).

    Args:
        value: A ``"a|b|c"``-style string, or ``None``.

    Returns:
        A list of non-empty, stripped tokens, or ``None`` when there are none.
    """
    if not value:
        return None
    items = [token.strip() for token in value.split("|") if token.strip()]
    return items or None


def _none_if_blank(value: str | None) -> str | None:
    """Return ``None`` for empty/whitespace-only strings, else the value.

    Args:
        value: A string argument, or ``None``.

    Returns:
        ``None`` if blank, otherwise ``value`` unchanged.
    """
    if value is None:
        return None
    return value if value.strip() else None


def _node_result(model: Any, write_result: dict[str, Any]) -> str:
    """Build the standard ``create_*`` JSON response from a write result.

    Args:
        model: The :class:`~harness.kg.models.GraphNode` instance that was
            written (used to echo the resolved, Neo4j-safe field map).
        write_result: The dict returned by
            :func:`~harness.kg.arbitration.write_node_model`.

    Returns:
        A JSON string ``{"status", "label", "key", "fields"}``.
    """
    return json.dumps(
        {
            "status": write_result["status"],
            "label": write_result["label"],
            "key": write_result["key"],
            "fields": model.cypher_props(),
        }
    )


def _error(message: str, **extra: Any) -> str:
    """Return a JSON-encoded error payload (tools never raise to the client).

    Args:
        message: A human-readable error message.
        **extra: Additional context fields to include.

    Returns:
        A JSON string ``{"status": "error", "error": ..., ...}``.
    """
    return json.dumps({"status": "error", "error": message, **extra})


# ---------------------------------------------------------------------------
# Node create tools
# ---------------------------------------------------------------------------


@mcp.tool()
def create_business_node(
    business_id: str,
    display_name: str,
    tier: str = "smb",
    status: str = "active",
    business_type: str | None = None,
    industry: str | None = None,
    primary_currency: str | None = None,
    timezone: str | None = None,
    default_granularity: str | None = None,
    decision_risk_posture: str | None = None,
    fiscal_year_start_month: int | None = None,
    strategic_intent_summary: str | None = None,
    north_star_metrics: str | None = None,
    operating_constraints: str | None = None,
    default_data_classification: str | None = None,
    root_seniority_rank: int | None = None,
) -> str:
    """Create or update a Business root node (one per tenant) via arbitration.

    Args:
        business_id: Unique identity (``Business.business_id``).
        display_name: Human-readable name.
        tier: ``startup`` | ``smb`` | ``mid_market`` | ``mnc``.
        status: ``active`` | ``paused`` | ``archived``.
        business_type: ``ecommerce`` | ``saas`` | ``marketplace`` | ``retail``
            | ``services`` | ``other``.
        industry: Free-text industry label.
        primary_currency: ISO currency code, e.g. ``USD``.
        timezone: IANA timezone, e.g. ``America/New_York``.
        fiscal_year_start_month: Fiscal calendar anchor month (``1``–``12``).
        default_granularity: ``daily`` | ``weekly`` | ``monthly`` | ``quarterly``.
        decision_risk_posture: ``conservative`` | ``balanced`` | ``aggressive``.
        strategic_intent_summary: One-line strategic intent.
        north_star_metrics: Pipe-delimited metric ids (``a|b|c``).
        operating_constraints: Pipe-delimited constraint strings.
        default_data_classification: ``public`` | ``internal`` | ``restricted``
            | ``executive``.
        root_seniority_rank: Rank of the top role (e.g. CEO = 100).

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = Business(
            business_id=business_id,
            display_name=display_name,
            tier=tier,
            status=status,
            business_type=business_type,
            industry=industry,
            primary_currency=primary_currency,
            timezone=timezone,
            fiscal_year_start_month=fiscal_year_start_month,
            default_granularity=default_granularity,
            decision_risk_posture=decision_risk_posture,
            strategic_intent_summary=strategic_intent_summary,
            north_star_metrics=_split_list(north_star_metrics),
            operating_constraints=_split_list(operating_constraints),
            default_data_classification=default_data_classification,
            root_seniority_rank=root_seniority_rank,
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:  # surface validation/connection errors as JSON
        return _error(str(exc), label="Business", key=business_id)
    return _node_result(model, result)


@mcp.tool()
def create_domain_node(
    domain_id: str,
    name: str,
    decision_scope_summary: str,
    min_level: int = 0,
    data_classification: str = "internal",
    status: str = "active",
    domain_type: str | None = None,
    parent_domain_id: str | None = None,
    owner_role_id: str | None = None,
    approval_policy_summary: str | None = None,
    default_product_ids: str | None = None,
    default_platform_ids: str | None = None,
) -> str:
    """Create or update a Domain spine node via arbitration.

    Args:
        domain_id: Unique identity (``Domain.domain_id``).
        name: Human-readable domain name.
        decision_scope_summary: One-line summary of the domain's decision scope.
        min_level: Minimum seniority rank required to view (>= 0).
        data_classification: ``public`` | ``internal`` | ``restricted``
            | ``executive``.
        status: ``active`` | ``hidden`` | ``deprecated`` | ``proposed``.
        domain_type: ``business`` | ``technical`` | ``risk`` | ``data_quality``
            | ``ml``.
        parent_domain_id: Parent domain id (for ``PARENT_OF`` hierarchy).
        owner_role_id: Owning ``Role.role_id``.
        approval_policy_summary: One-line approval policy summary.
        default_product_ids: Pipe-delimited product ids.
        default_platform_ids: Pipe-delimited platform ids.

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = Domain(
            domain_id=domain_id,
            name=name,
            decision_scope_summary=decision_scope_summary,
            min_level=min_level,
            data_classification=data_classification,
            status=status,
            domain_type=domain_type,
            parent_domain_id=parent_domain_id,
            owner_role_id=owner_role_id,
            approval_policy_summary=approval_policy_summary,
            default_product_ids=_split_list(default_product_ids),
            default_platform_ids=_split_list(default_platform_ids),
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:
        return _error(str(exc), label="Domain", key=domain_id)
    return _node_result(model, result)


@mcp.tool()
def create_product_node(
    product_id: str,
    display_name: str,
    status: str = "active",
    category: str | None = None,
    description: str | None = None,
    schema_name: str | None = None,
    schema_status: str | None = None,
    route_prefixes: str | None = None,
    owner_role_id: str | None = None,
    default_domain_ids: str | None = None,
    default_data_classification: str | None = None,
    min_level: int | None = None,
) -> str:
    """Create or update an IntelligenceProduct spine node via arbitration.

    Args:
        product_id: Unique identity (``IntelligenceProduct.product_id``).
        display_name: Human-readable product name.
        status: ``active`` | ``hidden`` | ``deprecated`` | ``proposed``.
        category: ``analytics`` | ``decisioning`` | ``creative`` | ``external``.
        description: Free-text description.
        schema_name: Backing schema name.
        schema_status: ``owned`` | ``shared``.
        route_prefixes: Pipe-delimited route prefixes.
        owner_role_id: Owning ``Role.role_id``.
        default_domain_ids: Pipe-delimited domain ids.
        default_data_classification: ``public`` | ``internal`` | ``restricted``
            | ``executive``.
        min_level: Minimum seniority rank required to access.

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = IntelligenceProduct(
            product_id=product_id,
            display_name=display_name,
            status=status,
            category=category,
            description=description,
            schema_name=schema_name,
            schema_status=schema_status,
            route_prefixes=_split_list(route_prefixes),
            owner_role_id=owner_role_id,
            default_domain_ids=_split_list(default_domain_ids),
            default_data_classification=default_data_classification,
            min_level=min_level,
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:
        return _error(str(exc), label="IntelligenceProduct", key=product_id)
    return _node_result(model, result)


@mcp.tool()
def create_metric_node(
    metric_uid: str,
    canonical_id: str,
    metric_id: str,
    display_name: str,
    product_ids: str,
    domain_ids: str,
    scope_key: str,
    metric_base: str,
    data_classification: str = "internal",
    min_level: int = 0,
    is_derived: bool = False,
    status: str = "proposed",
    description: str | None = None,
    category: str | None = None,
    unit_family: str | None = None,
    value_format: str | None = None,
    causal_role: str | None = None,
    owner_role_id: str | None = None,
    node_kind: str = "metric",
    has_endpoint: bool = True,
    is_ml: bool | None = None,
    ml_kind: str | None = None,
    ml_task: str | None = None,
    ml_model: str | None = None,
    ml_entity: str | None = None,
    chart_id: str | None = None,
    chart_type: str | None = None,
    source_expr: str | None = None,
    bc2_ref: str | None = None,
    mart_sources: str | None = None,
    formula_text: str | None = None,
    formula_explanation: str | None = None,
    how_to_read: str | None = None,
    decisions_answered: str | None = None,
    narration_text: str | None = None,
    default_direction: str | None = None,
    platform_ids: str | None = None,
    primary_platform_id: str | None = None,
    scope_level: str | None = None,
    aggregation: str | None = None,
    measurement_type: str | None = None,
    dashboard_ids: str | None = None,
    card_endpoint: str | None = None,
    series_endpoint: str | None = None,
    is_kpi: bool | None = None,
    is_model_output: bool | None = None,
    concept_key: str | None = None,
    concept_name: str | None = None,
    source_columns: str | None = None,
    sql_query_real: str | None = None,
    sql_query_canonical: str | None = None,
    mart_grains: str | None = None,
    history_start: str | None = None,
    history_end: str | None = None,
    data_stale: bool | None = None,
    formula_sql_mismatch: bool | None = None,
    formula_sql_note: str | None = None,
) -> str:
    """Create or update a Metric hub node (schema-valid, full fields) via arbitration.

    Args:
        metric_uid: Unique identity (``Metric.metric_uid``).
        canonical_id: Cross-scope canonical metric id (uniquely constrained).
        metric_id: Source/system metric id.
        display_name: Human-readable metric name.
        product_ids: Pipe-delimited product ids this metric sits on (required).
        domain_ids: Pipe-delimited domain ids this metric sits on (required).
        scope_key: The metric's scope key (required).
        metric_base: The base metric name (required).
        data_classification: ``public`` | ``internal`` | ``restricted``
            | ``executive``.
        min_level: Minimum seniority rank required to view.
        is_derived: Whether the metric is computed from others.
        status: ``proposed`` | ``active`` | ``deprecated`` | ``blocked``.
        description: Free-text description.
        category: One of the ``MetricCategory`` values.
        unit_family: ``currency`` | ``ratio`` | ``percent`` | ``count``
            | ``duration`` | ``score``.
        value_format: ``number`` | ``currency`` | ``percentage`` | ``decimal``.
        causal_role: One of the ``CausalRole`` values.
        owner_role_id: Owning ``Role.role_id``.
        node_kind: Graph-node role — ``metric`` | ``intermediary`` | ``input``
            | ``constant`` (endpoint-less inputs/constants/intermediaries still
            sit in causal paths; the UI dims them).
        has_endpoint: Whether the metric has a live card/series endpoint.
        is_ml: Whether the metric is ML-derived.
        ml_kind: ``prediction`` | ``performance`` | ``hybrid`` (set when ML).
        ml_task: ML task, e.g. ``timeseries`` | ``regression`` |
            ``classification`` | ``clustering``.
        ml_model: ML model identifier.
        ml_entity: ML subject, e.g. ``customer`` | ``category`` | ``product``
            | ``marketing``.
        chart_id: Chart-registry chart id for this metric's canonical chart.
        chart_type: One of the ``ChartType`` values.
        source_expr: SQL expression from ``metric_registry`` (e.g.
            ``SUM(REVENUE)``).
        bc2_ref: Backend repository ``file:line`` reference (``source_code_ref``).
        mart_sources: Pipe-delimited dbt mart source identifiers.
        formula_text: Human/SQL formula text.
        formula_explanation: Prose explanation of the formula.
        how_to_read: Pipe-delimited how-to-read bullet points.
        decisions_answered: Pipe-delimited decisions this metric answers.
        narration_text: Narration / voiceover text.
        default_direction: ``higher_is_better`` | ``lower_is_better``
            | ``target_is_best`` | ``neutral``.
        platform_ids: Pipe-delimited platform ids backing this metric.
        primary_platform_id: The primary backing ``Platform.platform_id``.
        scope_level: One of the ``ScopeLevel`` values.
        aggregation: One of the ``Aggregation`` values.
        measurement_type: One of the ``MeasurementType`` values.
        dashboard_ids: Pipe-delimited dashboard ids the metric appears on.
        card_endpoint: KPI-card endpoint path.
        series_endpoint: Time-series endpoint path.
        is_kpi: Whether the metric is a KPI.
        is_model_output: Whether the metric is the output of an ML model.
        concept_key: Cross-scope concept key.
        concept_name: Human-readable concept name.
        source_columns: Pipe-delimited mart column names used by this metric.
        sql_query_real: Verbatim backend SQL (from ``get_bc2_sql``).
        sql_query_canonical: LLM-generated clean, runnable ``SELECT``.
        mart_grains: Pipe-delimited per-mart grain identifiers.
        history_start: ISO date — data-coverage start.
        history_end: ISO date — data-coverage end.
        data_stale: Whether the latest data is older than the freshness SLA.
        formula_sql_mismatch: QA flag — ``formula_text`` disagrees with
            ``sql_query_real``.
        formula_sql_note: QA explanation set when ``formula_sql_mismatch``.

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = Metric(
            metric_uid=metric_uid,
            canonical_id=canonical_id,
            metric_id=metric_id,
            display_name=display_name,
            product_ids=_split_list(product_ids) or [],
            domain_ids=_split_list(domain_ids) or [],
            scope_key=scope_key,
            metric_base=metric_base,
            data_classification=data_classification,
            min_level=min_level,
            is_derived=is_derived,
            status=status,
            description=description,
            category=category,
            unit_family=unit_family,
            value_format=value_format,
            causal_role=causal_role,
            owner_role_id=owner_role_id,
            node_kind=node_kind,
            has_endpoint=has_endpoint,
            is_ml=is_ml,
            ml_kind=ml_kind,
            ml_task=_none_if_blank(ml_task),
            ml_model=_none_if_blank(ml_model),
            ml_entity=_none_if_blank(ml_entity),
            chart_id=_none_if_blank(chart_id),
            chart_type=chart_type,
            source_expr=_none_if_blank(source_expr),
            bc2_ref=_none_if_blank(bc2_ref),
            mart_sources=_split_list(mart_sources),
            formula_text=_none_if_blank(formula_text),
            formula_explanation=_none_if_blank(formula_explanation),
            how_to_read=_split_list(how_to_read) or [],
            decisions_answered=_split_list(decisions_answered) or [],
            narration_text=_none_if_blank(narration_text),
            default_direction=default_direction,
            platform_ids=_split_list(platform_ids),
            primary_platform_id=_none_if_blank(primary_platform_id),
            scope_level=scope_level,
            aggregation=aggregation,
            measurement_type=measurement_type,
            dashboard_ids=_split_list(dashboard_ids),
            card_endpoint=_none_if_blank(card_endpoint),
            series_endpoint=_none_if_blank(series_endpoint),
            is_kpi=is_kpi,
            is_model_output=is_model_output,
            concept_key=_none_if_blank(concept_key),
            concept_name=_none_if_blank(concept_name),
            source_columns=_split_list(source_columns),
            sql_query_real=_none_if_blank(sql_query_real),
            sql_query_canonical=_none_if_blank(sql_query_canonical),
            mart_grains=_split_list(mart_grains),
            history_start=_none_if_blank(history_start),
            history_end=_none_if_blank(history_end),
            data_stale=data_stale,
            formula_sql_mismatch=formula_sql_mismatch,
            formula_sql_note=_none_if_blank(formula_sql_note),
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:
        return _error(str(exc), label="Metric", key=metric_uid)
    return _node_result(model, result)


@mcp.tool()
def create_policy_node(
    policy_id: str,
    applies_to_kind: str = "Metric",
    metric_id: str | None = None,
    policy_name: str | None = None,
    description: str | None = None,
    policy_type: str | None = None,
    condition_type: str | None = None,
    condition_operator: str | None = None,
    condition_value: float | None = None,
    condition_value_high: float | None = None,
    condition_expression: str | None = None,
    evaluation_window: str | None = None,
    severity: str | None = None,
    auto_investigate: bool | None = None,
    notify_channels: str | None = None,
    owner_role_id: str | None = None,
    approval_required: bool | None = None,
    approval_role_ids: str | None = None,
    priority: int | None = None,
    effective_from: str | None = None,
    effective_to: str | None = None,
    is_active: bool | None = None,
    status: str | None = None,
    review_state: str = "active",
    population_status: str = "populated",
    source: str | None = None,
) -> str:
    """Create or update a Policy governance node (schema-valid) via arbitration.

    A Policy is a *rule the business must obey* (FRD FR-CG-001). It governs a
    metric (``Policy -GOVERNS-> Metric``) and enforces a Threshold
    (``Policy -ENFORCES_THRESHOLD-> Threshold``); draw those edges separately.

    Args:
        policy_id: Unique identity (``Policy.policy_id``).
        applies_to_kind: Node kind the policy governs (default ``Metric``).
        metric_id: The governed ``Metric.metric_uid`` (when metric-scoped).
        policy_name: Human-readable policy name.
        description: Free-text description of the rule.
        policy_type: One of the ``PolicyType`` values (``access`` |
            ``interpretation`` | ``alerting`` | ``escalation`` | ``approval`` |
            ``action_guardrail`` | ``data_quality``).
        condition_type: One of the ``ConditionType`` values (``threshold`` |
            ``anomaly`` | ``trend`` | ``missing_data``).
        condition_operator: One of the ``ComparisonOperator`` values (``lt`` |
            ``lte`` | ``gt`` | ``gte`` | ``eq`` | ``neq`` | ``between`` |
            ``outside`` | ``percent_change`` | ``z_score``).
        condition_value: The breach value the operator compares against.
        condition_value_high: Upper bound for ``between``/``outside``.
        condition_expression: Free-text/SQL condition expression.
        evaluation_window: Evaluation window (e.g. ``P7D``).
        severity: One of the ``Severity`` values (``critical`` | ``high`` |
            ``medium`` | ``low`` | ``info`` | ``blocking``).
        auto_investigate: Whether a breach auto-wakes the investigation agent.
        notify_channels: Pipe-delimited notification channels.
        owner_role_id: Owning ``Role.role_id``.
        approval_required: Whether changes need approval.
        approval_role_ids: Pipe-delimited approver ``Role.role_id`` values.
        priority: Integer priority (higher wins on conflict).
        effective_from: ISO date the policy takes effect.
        effective_to: ISO date the policy expires.
        is_active: Whether the policy is currently active.
        status: One of the ``SurfaceStatus`` values.
        review_state: Governance review state (``draft`` | ``active`` |
            ``needs_review`` | ``retired``); default ``active``.
        population_status: ``defined`` | ``populated``; default ``populated``.
        source: Provenance string (e.g. ``demo_seed`` | ``llm_extract``).

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = Policy(
            policy_id=policy_id,
            applies_to_kind=applies_to_kind,
            metric_id=_none_if_blank(metric_id),
            policy_name=_none_if_blank(policy_name),
            description=_none_if_blank(description),
            policy_type=policy_type,
            condition_type=condition_type,
            condition_operator=condition_operator,
            condition_value=condition_value,
            condition_value_high=condition_value_high,
            condition_expression=_none_if_blank(condition_expression),
            evaluation_window=_none_if_blank(evaluation_window),
            severity=severity,
            auto_investigate=auto_investigate,
            notify_channels=_split_list(notify_channels),
            owner_role_id=_none_if_blank(owner_role_id),
            approval_required=approval_required,
            approval_role_ids=_split_list(approval_role_ids),
            priority=priority,
            effective_from=_none_if_blank(effective_from),
            effective_to=_none_if_blank(effective_to),
            is_active=is_active,
            status=status,
            review_state=review_state,
            population_status=population_status,
            source=_none_if_blank(source),
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:
        return _error(str(exc), label="Policy", key=policy_id)
    return _node_result(model, result)


@mcp.tool()
def create_threshold_node(
    threshold_id: str,
    metric_id: str | None = None,
    metric_name: str | None = None,
    threshold_type: str | None = None,
    operator: str | None = None,
    direction: str | None = None,
    unit: str | None = None,
    severity: str | None = None,
    green_value: str | None = None,
    yellow_value: str | None = None,
    red_value: str | None = None,
    warning_value_num: float | None = None,
    critical_value_num: float | None = None,
    target_value_num: float | None = None,
    p95_val: float | None = None,
    p85_val: float | None = None,
    p75_val: float | None = None,
    p50_val: float | None = None,
    percentile_basis: str | None = None,
    industry_standard_val: float | None = None,
    industry_min_val: float | None = None,
    industry_max_val: float | None = None,
    industry_source: str | None = None,
    industry_as_of: str | None = None,
    current_val: float | None = None,
    current_as_of: str | None = None,
    category: str | None = None,
    grain: str | None = None,
    evaluation_window: str | None = None,
    explanation: str | None = None,
    owner_role_id: str | None = None,
    review_state: str = "active",
    population_status: str = "populated",
    source: str | None = None,
) -> str:
    """Create or update a Threshold governance node (schema-valid) via arbitration.

    A Threshold is the *breach line* on a metric (FRD FR-CG-001). It hangs off a
    metric (``Metric -HAS_THRESHOLD-> Threshold``); draw that edge separately.
    Beyond the static green/yellow/red bands it carries the company's own
    percentile distribution (``p50/p75/p85/p95``) and an external industry
    benchmark (value + ``[min, max]`` band + source + as-of) for comparison.

    Args:
        threshold_id: Unique identity (``Threshold.threshold_id``).
        metric_id: The metric this threshold bounds (``Metric.metric_uid``).
        metric_name: Human-readable metric name (denormalised convenience).
        threshold_type: One of the ``ThresholdType`` values (``static`` |
            ``percentile`` | ``seasonal`` | ``warning`` | ``critical`` |
            ``target`` | ``anomaly`` | ``sla`` | ``budget``).
        operator: One of the ``ComparisonOperator`` values.
        direction: ``higher_is_better`` | ``lower_is_better`` |
            ``target_is_best``.
        unit: Display unit (``ratio`` | ``percent`` | ``currency`` | …).
        severity: One of the ``Severity`` values for a band breach.
        green_value: Healthy-band display value.
        yellow_value: Warning-band display value.
        red_value: Critical-band display value.
        warning_value_num: Numeric warning threshold.
        critical_value_num: Numeric critical threshold.
        target_value_num: Numeric target/aspirational value.
        p95_val: Company 95th-percentile value (own distribution).
        p85_val: Company 85th-percentile value.
        p75_val: Company 75th-percentile value.
        p50_val: Company median value.
        percentile_basis: How the percentiles were computed (e.g.
            ``company trailing-90d daily``).
        industry_standard_val: The industry benchmark point value.
        industry_min_val: Lower bound of the industry-standard band.
        industry_max_val: Upper bound of the industry-standard band.
        industry_source: Benchmark provenance (e.g. ``llm:claude-opus-4-8``).
        industry_as_of: ISO date the benchmark reflects.
        current_val: The metric's own current value (for comparison).
        current_as_of: ISO date of the current snapshot.
        category: Free-text category.
        grain: Evaluation grain (e.g. ``daily``).
        evaluation_window: Evaluation window (e.g. ``P30D``).
        explanation: Prose explanation of the bands.
        owner_role_id: Owning ``Role.role_id``.
        review_state: Governance review state; default ``active``.
        population_status: ``defined`` | ``populated``; default ``populated``.
        source: Provenance string (e.g. ``demo_seed`` | ``llm_extract``).

    Returns:
        JSON string ``{"status", "label", "key", "fields"}`` (or an error).
    """
    try:
        model = Threshold(
            threshold_id=threshold_id,
            metric_id=_none_if_blank(metric_id),
            metric_name=_none_if_blank(metric_name),
            threshold_type=threshold_type,
            operator=operator,
            direction=direction,
            unit=_none_if_blank(unit),
            severity=severity,
            green_value=_none_if_blank(green_value),
            yellow_value=_none_if_blank(yellow_value),
            red_value=_none_if_blank(red_value),
            warning_value_num=warning_value_num,
            critical_value_num=critical_value_num,
            target_value_num=target_value_num,
            p95_val=p95_val,
            p85_val=p85_val,
            p75_val=p75_val,
            p50_val=p50_val,
            percentile_basis=_none_if_blank(percentile_basis),
            industry_standard_val=industry_standard_val,
            industry_min_val=industry_min_val,
            industry_max_val=industry_max_val,
            industry_source=_none_if_blank(industry_source),
            industry_as_of=_none_if_blank(industry_as_of),
            current_val=current_val,
            current_as_of=_none_if_blank(current_as_of),
            category=_none_if_blank(category),
            grain=_none_if_blank(grain),
            evaluation_window=_none_if_blank(evaluation_window),
            explanation=_none_if_blank(explanation),
            owner_role_id=_none_if_blank(owner_role_id),
            review_state=review_state,
            population_status=population_status,
            source=_none_if_blank(source),
        )
        result = write_node_model(get_db(), model)
    except Exception as exc:
        return _error(str(exc), label="Threshold", key=threshold_id)
    return _node_result(model, result)


# ---------------------------------------------------------------------------
# Edge tools
# ---------------------------------------------------------------------------


@mcp.tool()
def draw_edge(
    rel_type: str,
    from_label: str,
    from_key: str,
    to_label: str,
    to_key: str,
    props_json: str = "{}",
) -> str:
    """Create or update a relationship between two existing nodes via arbitration.

    Both endpoints are matched by their identity field. If either endpoint does
    not exist, a ``missing_endpoint`` status is returned (no edge is written).

    Args:
        rel_type: Relationship type (must be in
            :data:`~harness.kg.models.EDGE_TYPES`).
        from_label: Source node label (must be in
            :data:`~harness.kg.models.NODE_LABELS`).
        from_key: Source node identity value.
        to_label: Target node label.
        to_key: Target node identity value.
        props_json: JSON object string of edge properties (default ``"{}"``).

    Returns:
        JSON string of the :func:`~harness.kg.arbitration.upsert_edge` result
        (or an error).
    """
    try:
        props = json.loads(props_json) if props_json else {}
        if not isinstance(props, dict):
            return _error("props_json must encode a JSON object", rel_type=rel_type)
        result = upsert_edge(
            get_db(),
            rel_type=rel_type,
            from_label=from_label,
            from_key=from_key,
            to_label=to_label,
            to_key=to_key,
            props=props,
        )
    except Exception as exc:
        return _error(str(exc), rel_type=rel_type)
    return json.dumps(result)


@mcp.tool()
def add_causal_edge(
    from_uid: str,
    to_uid: str,
    mechanism: str,
    tier: str = "prior",
    direction: str = "supports",
    weight: float | None = None,
    temporal_lag: str | None = None,
    lag_plausibility: float | None = None,
    cross_domain: bool = False,
    candidate_basis: str | None = None,
    relation: str = "mart_lineage",
) -> str:
    """Append one evidence event to a causal ``INFLUENCES`` edge via arbitration.

    Unlike :func:`draw_edge` (used for structural / spine edges written in
    place), this tool routes through
    :func:`~harness.kg.arbitration.append_edge_evidence`: an ``INFLUENCES`` edge's
    confidence is *never* set directly but is a deterministic fold over its
    append-only evidence ledger (FR-SCORE-001). One call appends a single
    evidence event and re-folds the score. Both endpoints are ``Metric`` nodes
    matched by ``metric_uid``; a missing endpoint yields a ``missing_endpoint``
    status (no edge is fabricated).

    Args:
        from_uid: Source ``Metric.metric_uid`` (the cause).
        to_uid: Target ``Metric.metric_uid`` (the effect).
        mechanism: Free-text mechanism describing how the cause drives the
            effect (recorded on both the evidence event and the edge).
        tier: Evidence tier — one of :class:`~harness.kg.evidence.EvidenceTier`
            (``prior`` | ``observational`` | ``quasi_experimental`` |
            ``interventional`` | ``human``); selects the default pseudo-count
            weight when ``weight`` is omitted.
        direction: ``supports`` (adds to alpha) or ``refutes`` (adds to beta).
        weight: Explicit per-event pseudo-count; ``None`` lets the fold fall back
            to the tier's :data:`~harness.kg.evidence.TIER_WEIGHTS` default.
        temporal_lag: Optional ISO-8601 duration (``P#DT#H#M#S``, e.g. ``"P1D"``)
            for the cause→effect lag.
        lag_plausibility: Optional lag-plausibility multiplier in ``(0, 1]``.
        cross_domain: Whether the edge crosses domain boundaries.
        candidate_basis: Optional provenance note for how the candidate arose.
        relation: ``INFLUENCES`` relation subtype (must be in
            :data:`~harness.kg.models.INFLUENCES_RELATIONS`; default
            ``mart_lineage``).

    Returns:
        JSON string of the
        :func:`~harness.kg.arbitration.append_edge_evidence` result (``created``
        | ``updated`` | ``missing_endpoint`` | ``kept_reviewed``), or an error.
    """
    if relation not in INFLUENCES_RELATIONS:
        return _error(
            f"Unknown INFLUENCES relation {relation!r}; expected one of "
            f"{sorted(INFLUENCES_RELATIONS)}",
            from_uid=from_uid,
            to_uid=to_uid,
        )
    # The fold derives confidence from this event; weight is left to the tier
    # default when None (see fold_ledger). attribution/timestamp make the score
    # traceable (FR-SCORE-002); event_id is computed by append_edge_evidence.
    event = {
        "tier": tier,
        "direction": direction,
        "weight": weight,
        "attribution": "mcp:add_causal_edge",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "mechanism": mechanism,
    }
    # Non-ledger edge scalars (None values are stripped by the arbitration write).
    edge_props = {
        "relation": relation,
        "mechanism": mechanism,
        "temporal_lag": temporal_lag,
        "lag_plausibility": lag_plausibility,
        "cross_domain": cross_domain,
        "source_kind": "mart_lineage",
        "candidate_basis": candidate_basis,
    }
    try:
        result = append_edge_evidence(
            get_db(),
            from_key=from_uid,
            to_key=to_uid,
            event=event,
            edge_props=edge_props,
        )
    except Exception as exc:
        return _error(str(exc), from_uid=from_uid, to_uid=to_uid)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


@mcp.tool()
def lookup_node(label: str, key: str) -> str:
    """Look up a single node by label and identity value.

    Args:
        label: Node label (must be in :data:`~harness.kg.models.NODE_LABELS`).
        key: The node's identity (key-field) value.

    Returns:
        JSON string ``{"status": "found", "label", "key", "node"}`` when present,
        ``{"status": "not_found", ...}`` when absent, or an error.
    """
    if label not in NODE_LABELS:
        return _error(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}",
            label=label,
        )
    key_field = NODE_KEY_FIELDS[label]
    try:
        # label/key_field interpolated only from the validated allowlist; value
        # is parameterized (Cypher-injection guard, same discipline as arbitration).
        rows = get_db().read(
            f"MATCH (n:{label} {{{key_field}: $key}}) RETURN n AS node LIMIT 1",
            key=key,
        )
    except Exception as exc:
        return _error(str(exc), label=label, key=key)
    if not rows:
        return json.dumps({"status": "not_found", "label": label, "key": key})
    return json.dumps(
        {"status": "found", "label": label, "key": key, "node": rows[0]["node"]}
    )


@mcp.tool()
def search_nodes(query: str, label: str | None = None, limit: int = 20) -> str:
    """Search nodes by a case-insensitive substring across common name fields.

    Matches against the node's key field plus ``display_name``/``name``/``title``
    (whichever exist). Restrict to one label with ``label``.

    Args:
        query: Case-insensitive substring to search for.
        label: Optional node label to restrict the search (allowlisted).
        limit: Maximum number of results to return.

    Returns:
        JSON string ``{"status": "ok", "count", "results": [...]}`` (or an error).
    """
    if label is not None and label not in NODE_LABELS:
        return _error(
            f"Unknown node label {label!r}; expected one of {sorted(NODE_LABELS)}",
            label=label,
        )
    labels = [label] if label is not None else sorted(NODE_LABELS)
    needle = query.lower()
    safe_limit = max(1, min(int(limit), 500))

    results: list[dict[str, Any]] = []
    try:
        db = get_db()
        for lbl in labels:
            if len(results) >= safe_limit:
                break
            key_field = NODE_KEY_FIELDS[lbl]
            remaining = safe_limit - len(results)
            # Label interpolated from the validated allowlist; query & limit
            # parameterized. coalesce over the candidate name fields.
            cypher = (
                f"MATCH (n:{lbl}) "
                "WITH n, toLower(coalesce("
                f"toString(n.{key_field}), '') + ' ' + "
                "coalesce(n.display_name, '') + ' ' + "
                "coalesce(n.name, '') + ' ' + "
                "coalesce(n.title, '')) AS hay "
                "WHERE hay CONTAINS $needle "
                f"RETURN n.{key_field} AS key, "
                "coalesce(n.display_name, n.name, n.title) AS name "
                "LIMIT $lim"
            )
            rows = db.read(cypher, needle=needle, lim=remaining)
            for row in rows:
                results.append(
                    {"label": lbl, "key": row["key"], "name": row.get("name")}
                )
    except Exception as exc:
        return _error(str(exc), query=query, label=label)
    return json.dumps({"status": "ok", "count": len(results), "results": results})


@mcp.tool()
def kg_status() -> str:
    """Report node counts per label across the graph.

    Returns:
        JSON string ``{"status": "ok", "counts": {label: n, ...}, "total": n}``
        (or an error).
    """
    counts: dict[str, int] = {}
    try:
        db = get_db()
        for lbl in sorted(NODE_LABELS):
            rows = db.read(f"MATCH (n:{lbl}) RETURN count(n) AS c")
            counts[lbl] = int(rows[0]["c"]) if rows else 0
    except Exception as exc:
        return _error(str(exc))
    return json.dumps(
        {"status": "ok", "counts": counts, "total": sum(counts.values())}
    )


# ---------------------------------------------------------------------------
# BC_2 inspection + edge-candidate validation (read-only)
#
# These tools never mutate the graph: ``inspect_bc2_sources`` hashes/validates
# the offline BC_2 snapshot, and ``validate_edge_candidate`` /
# ``explain_edge_candidate`` resolve the deterministic edge-scoring policy for a
# candidate (endpoints + scope rule + score). Names stay off the ``create_*`` /
# ``draw_edge`` pattern so the confirm-before-create pretool guard skips them.
# ---------------------------------------------------------------------------


def _load_json_file(path: Path) -> Any:
    """Read + parse a JSON file (raises on missing/invalid; callers wrap)."""
    return json.loads(path.read_text(encoding="utf-8"))


@mcp.tool()
def inspect_bc2_sources(bc_path: str = "/Users/kushal/Desktop/kal/BC_2") -> str:
    """Inspect the BC_2 offline snapshot (files + validated relationship rows).

    Hashes each primary seed file and validates the relationship / causal-edge
    rows (structural rejection only — no live-metric resolution here). Read-only;
    nothing is written.

    Args:
        bc_path: Path to the BC_2 snapshot root.

    Returns:
        JSON string ``{files, valid_rel_candidates, rejected_rel_rows,
        reject_reasons}`` (or an error).
    """
    from collections import Counter

    from harness.ingest import bc2_snapshot as bc2

    try:
        root = Path(bc_path)
        sources = bc2.load_bc2_sources(root)
        hashes = bc2.hash_source_files(root)
        valid, rejected = bc2.validate_bc2_relationship_rows(sources)
        summary = bc2.inventory_summary(sources, hashes)
        files = [
            {
                "name": name,
                "rows": info.get("rows"),
                "sha256": info.get("sha256"),
                "bytes": info.get("bytes"),
            }
            for name, info in sorted(hashes.items())
        ]
        reasons = Counter(r.get("reason") for r in rejected)
    except Exception as exc:  # surface as JSON; tools never raise to the client
        return _error(str(exc), bc_path=bc_path)
    return json.dumps(
        {
            "bc_path": str(root),
            "files": files,
            "row_counts": summary.get("row_counts"),
            "valid_rel_candidates": len(valid),
            "rejected_rel_rows": len(rejected),
            "reject_reasons": dict(reasons),
        }
    )


def _node_exists(uid: str) -> bool:
    """True when a ``Metric`` with this ``metric_uid`` exists (read-only)."""
    rows = get_db().read(
        "MATCH (m:Metric {metric_uid: $uid}) RETURN 1 AS ok LIMIT 1", uid=uid
    )
    return bool(rows)


def _scope_of(uid: str) -> str | None:
    """Return a metric's ``scope_key`` (or ``None`` if absent; read-only)."""
    rows = get_db().read(
        "MATCH (m:Metric {metric_uid: $uid}) RETURN m.scope_key AS s LIMIT 1",
        uid=uid,
    )
    return rows[0].get("s") if rows else None


@mcp.tool()
def validate_edge_candidate(
    from_uid: str, to_uid: str, rel_type: str, relation: str
) -> str:
    """Validate a metric->metric edge candidate (endpoints + scope + scoring).

    Checks both endpoints exist (read-only DB lookup), the
    ``rel_type``/``relation`` pair is in the allowed vocabulary, the same-scope
    rule holds for ``formula``/``identity`` relations, and resolves the
    deterministic :func:`~harness.ingest.edge_scoring.score_edge` policy.
    Read-only; emits no write.

    Args:
        from_uid: Source ``Metric.metric_uid``.
        to_uid: Target ``Metric.metric_uid``.
        rel_type: ``DECOMPOSES_INTO`` or ``INFLUENCES``.
        relation: The relation subtype (must match the rel_type vocabulary).

    Returns:
        JSON string ``{valid, endpoint_exists, scope_ok, scoring, reasons}`` (or
        an error).
    """
    from harness.ingest import edge_scoring

    reasons: list[str] = []
    try:
        from_exists = _node_exists(from_uid)
        to_exists = _node_exists(to_uid)
    except Exception as exc:
        return _error(str(exc), from_uid=from_uid, to_uid=to_uid)

    if not from_exists:
        reasons.append(f"source {from_uid} not found")
    if not to_exists:
        reasons.append(f"target {to_uid} not found")
    if from_uid == to_uid:
        reasons.append("self_loop: from_uid == to_uid")

    # Relation-vocabulary validation (same coupling the single writer enforces).
    allowed = {
        "DECOMPOSES_INTO": DECOMPOSES_RELATIONS,
        "INFLUENCES": INFLUENCES_RELATIONS,
    }
    if rel_type not in EDGE_TYPES:
        reasons.append(f"unknown rel_type {rel_type!r}")
    elif rel_type in allowed and relation not in allowed[rel_type]:
        reasons.append(
            f"relation {relation!r} not allowed for {rel_type} "
            f"(expected one of {sorted(allowed[rel_type])})"
        )

    # Same-scope rule applies to the deterministic formula / identity relations.
    scope_ok = True
    if relation in ("formula", "identity") and from_exists and to_exists:
        try:
            scope_ok = _scope_of(from_uid) == _scope_of(to_uid)
        except Exception as exc:
            return _error(str(exc), from_uid=from_uid, to_uid=to_uid)
        if not scope_ok:
            reasons.append(
                f"{relation} edges must be same-scope (source and target scope_key differ)"
            )

    score = edge_scoring.score_edge(f"{rel_type}:{relation}")
    valid = not reasons
    return json.dumps(
        {
            "valid": valid,
            "endpoint_exists": {"from": from_exists, "to": to_exists},
            "scope_ok": scope_ok,
            "scoring": {
                "confidence": score.confidence,
                "evidence_mass": score.evidence_mass,
                "scoring_policy": score.scoring_policy,
                "review": score.review,
            },
            "reasons": reasons,
        }
    )


@mcp.tool()
def explain_edge_candidate(
    from_uid: str, to_uid: str, rel_type: str, relation: str
) -> str:
    """Explain a metric->metric edge candidate's scoring policy + review gate.

    Resolves the deterministic :func:`~harness.ingest.edge_scoring.score_edge`
    policy for the ``rel_type``/``relation`` pair and renders a one-line
    rationale plus whether it is auto-safe or held for review. Read-only.

    Args:
        from_uid: Source ``Metric.metric_uid``.
        to_uid: Target ``Metric.metric_uid``.
        rel_type: ``DECOMPOSES_INTO`` or ``INFLUENCES``.
        relation: The relation subtype.

    Returns:
        JSON string ``{why, auto_safe_or_review, scoring_policy, confidence,
        evidence_mass, deterministic}``.
    """
    from harness.ingest import edge_scoring

    score = edge_scoring.score_edge(f"{rel_type}:{relation}")
    gate = "review" if score.review else "auto_safe"
    kind = "deterministic/pinned" if score.deterministic else "scored"
    why = (
        f"{from_uid} -[{rel_type} {{relation: {relation}}}]-> {to_uid} resolves to "
        f"the {score.scoring_policy!r} policy ({kind}; confidence "
        f"{score.confidence}, evidence_mass {score.evidence_mass}); "
        f"this edge class is {'held for human review' if score.review else 'auto-safe to apply'}."
    )
    return json.dumps(
        {
            "why": why,
            "auto_safe_or_review": gate,
            "scoring_policy": score.scoring_policy,
            "confidence": score.confidence,
            "evidence_mass": score.evidence_mass,
            "deterministic": score.deterministic,
        }
    )


# ---------------------------------------------------------------------------
# Phase 6 — notes-lookup tools (read-only joins over live props + docs)
# ---------------------------------------------------------------------------


def _chart_registry_slice(canonical_id: str | None, chart_id: str | None) -> dict[str, Any] | None:
    """Return ONE chart-registry entry by canonical_id (or chart_id), not the file.

    Reads the registry file and returns a single matching entry so the caller
    never embeds the whole registry in a tool response.
    """
    if not canonical_id and not chart_id:
        return None
    registry = _load_json_file(_CHART_REGISTRY_PATH)
    if canonical_id and canonical_id in registry:
        return registry[canonical_id]
    if chart_id:
        for entry in registry.values():
            if str(entry.get("chart_id")) == chart_id:
                return entry
    return None


def _openapi_endpoint_desc(card_endpoint: str | None) -> dict[str, Any] | None:
    """Return ``{path, summary, description}`` for a metric's card endpoint path.

    Reads the openapi spec and slices out just the matching GET operation's
    summary/description (never the whole spec).
    """
    if not card_endpoint:
        return None
    spec = _load_json_file(_OPENAPI_PATH)
    op = (spec.get("paths") or {}).get(card_endpoint)
    if not isinstance(op, dict):
        return None
    get = op.get("get") or {}
    return {
        "path": card_endpoint,
        "summary": get.get("summary"),
        "description": get.get("description"),
    }


@mcp.tool()
def lookup_metric_notes(metric_uid: str) -> str:
    """Join a live metric's narrative props with its chart-registry + openapi slice.

    Returns the metric's notes-relevant Neo4j props (scope_key, formula_text,
    formula_explanation, how_to_read, decisions_answered, narration_text,
    card_endpoint) plus the single matching chart-registry entry (by
    canonical_id/chart_id) and the matching openapi endpoint description. The
    registry/openapi files are sliced, NOT loaded whole into the response.
    Read-only.

    Args:
        metric_uid: The live ``Metric.metric_uid``.

    Returns:
        JSON string ``{found, metric, chart_registry, openapi_endpoint}`` (or an
        error).
    """
    try:
        rows = get_db().read(
            "MATCH (m:Metric {metric_uid: $uid}) RETURN m.metric_uid AS metric_uid, "
            "m.display_name AS display_name, m.canonical_id AS canonical_id, "
            "m.chart_id AS chart_id, m.scope_key AS scope_key, "
            "m.formula_text AS formula_text, "
            "m.formula_explanation AS formula_explanation, "
            "m.how_to_read AS how_to_read, "
            "m.decisions_answered AS decisions_answered, "
            "m.narration_text AS narration_text, "
            "m.card_endpoint AS card_endpoint LIMIT 1",
            uid=metric_uid,
        )
    except Exception as exc:
        return _error(str(exc), metric_uid=metric_uid)
    if not rows:
        return json.dumps({"metric_uid": metric_uid, "found": False})
    metric = rows[0]
    try:
        chart = _chart_registry_slice(
            metric.get("canonical_id"), metric.get("chart_id")
        )
        endpoint = _openapi_endpoint_desc(metric.get("card_endpoint"))
    except Exception as exc:
        return _error(str(exc), metric_uid=metric_uid)
    return json.dumps(
        {
            "found": True,
            "metric": metric,
            "chart_registry": chart,
            "openapi_endpoint": endpoint,
        }
    )


def _metric_list_result(rows: list[dict[str, Any]]) -> str:
    """Shape a metric-list Cypher result into the standard list response."""
    metrics = [
        {
            "metric_uid": r.get("metric_uid"),
            "display_name": r.get("display_name"),
            "concept_key": r.get("concept_key"),
            "causal_role": r.get("causal_role"),
        }
        for r in rows
    ]
    return json.dumps({"count": len(metrics), "metrics": metrics})


@mcp.tool()
def list_metrics_by_domain(domain_id: str, limit: int = 50) -> str:
    """List live metrics belonging to a domain (read-only).

    Args:
        domain_id: The ``Domain.domain_id`` to filter on (matched against each
            metric's ``domain_ids``).
        limit: Maximum number of metrics to return (1..500).

    Returns:
        JSON string ``{count, metrics: [{metric_uid, display_name, concept_key,
        causal_role}]}`` (or an error).
    """
    safe_limit = max(1, min(int(limit), 500))
    try:
        rows = get_db().read(
            "MATCH (m:Metric) WHERE $domain IN m.domain_ids "
            "RETURN m.metric_uid AS metric_uid, m.display_name AS display_name, "
            "m.concept_key AS concept_key, m.causal_role AS causal_role "
            "ORDER BY m.metric_uid LIMIT $lim",
            domain=domain_id,
            lim=safe_limit,
        )
    except Exception as exc:
        return _error(str(exc), domain_id=domain_id)
    return _metric_list_result(rows)


@mcp.tool()
def list_metrics_by_scope(scope_key: str, limit: int = 50) -> str:
    """List live metrics in a scope (read-only).

    Args:
        scope_key: The ``Metric.scope_key`` to filter on.
        limit: Maximum number of metrics to return (1..500).

    Returns:
        JSON string ``{count, metrics: [{metric_uid, display_name, concept_key,
        causal_role}]}`` (or an error).
    """
    safe_limit = max(1, min(int(limit), 500))
    try:
        rows = get_db().read(
            "MATCH (m:Metric {scope_key: $scope}) "
            "RETURN m.metric_uid AS metric_uid, m.display_name AS display_name, "
            "m.concept_key AS concept_key, m.causal_role AS causal_role "
            "ORDER BY m.metric_uid LIMIT $lim",
            scope=scope_key,
            lim=safe_limit,
        )
    except Exception as exc:
        return _error(str(exc), scope_key=scope_key)
    return _metric_list_result(rows)


@mcp.tool()
def get_chart_registry_entry(canonical_id: str) -> str:
    """Return a single chart-registry entry by canonical_id (read-only).

    Reads the registry file and returns just the one entry — never the whole
    registry.

    Args:
        canonical_id: The registry key (``dashboard_id:chart_id``).

    Returns:
        JSON string ``{found, canonical_id, entry}`` (or an error).
    """
    try:
        registry = _load_json_file(_CHART_REGISTRY_PATH)
    except Exception as exc:
        return _error(str(exc), canonical_id=canonical_id)
    entry = registry.get(canonical_id)
    if entry is None:
        return json.dumps(
            {"found": False, "canonical_id": canonical_id, "entry": None}
        )
    return json.dumps({"found": True, "canonical_id": canonical_id, "entry": entry})


# ---------------------------------------------------------------------------
# Doc-reading tools (read SOURCE files in scoped slices — NEVER the graph)
#
# These tools join the offline evidence (metric catalog + registry + chart
# registry + openapi + BC_2 SQL/repository source) so an agent can re-derive a
# metric without touching Neo4j. Every read is sliced/capped — a tool never dumps
# a whole file into its response.
# ---------------------------------------------------------------------------

#: Max characters of any single source-file slice echoed back to the caller.
_SLICE_CHAR_CAP: int = 6000


def _derive_node_kind(entry: dict[str, Any]) -> str:
    """Derive a catalog entry's ``node_kind`` (metric/intermediary/input/constant).

    Mirrors the locked derivation rule: a ``source_field`` node is an ``input``;
    a ``constant.`` metric_id prefix is a ``constant``; an ML or non-derived
    measure is a ``metric``; a derived measure with ``<= 3`` dependencies is an
    ``intermediary``, otherwise a ``metric``.

    Args:
        entry: A ``metrics`` or ``input_nodes`` catalog entry.

    Returns:
        One of ``"metric"`` | ``"intermediary"`` | ``"input"`` | ``"constant"``.
    """
    metric_id = entry.get("metric_id") or entry.get("input_id") or ""
    if entry.get("node_type") == "source_field":
        return "input"
    if metric_id.startswith("constant."):
        return "constant"
    if entry.get("is_ml"):
        return "metric"
    if not entry.get("is_derived"):
        return "metric"
    depends_on = entry.get("depends_on") or []
    return "intermediary" if len(depends_on) <= 3 else "metric"


def _load_metric_catalog() -> dict[str, Any]:
    """Read + parse the metric-node catalog JSON (``metrics`` + ``input_nodes``)."""
    return _load_json_file(_METRIC_NODES_PATH)


def _catalog_entry(metric_id: str) -> tuple[dict[str, Any] | None, str]:
    """Return ``(entry, source_table)`` for a metric_id from the catalog.

    Looks in ``metrics`` first, then ``input_nodes`` (keyed by either the dict
    key or the entry's ``metric_id``/``input_id``). ``source_table`` is
    ``"metrics"`` / ``"input_nodes"`` (or ``""`` when not found).
    """
    catalog = _load_metric_catalog()
    metrics = catalog.get("metrics") or {}
    inputs = catalog.get("input_nodes") or {}
    if metric_id in metrics:
        return metrics[metric_id], "metrics"
    if metric_id in inputs:
        return inputs[metric_id], "input_nodes"
    for entry in metrics.values():
        if entry.get("metric_id") == metric_id:
            return entry, "metrics"
    for entry in inputs.values():
        if entry.get("input_id") == metric_id:
            return entry, "input_nodes"
    return None, ""


def _registry_row(metric_id: str) -> dict[str, Any] | None:
    """Return the ``metric_registry`` CSV row whose ``node_id`` equals metric_id.

    Best-effort exact match (the registry keys on ``node_id``, which only
    partially overlaps the catalog ids). Returns ``None`` when absent.
    """
    import csv

    if not _METRIC_REGISTRY_PATH.exists():
        return None
    with _METRIC_REGISTRY_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("node_id") == metric_id:
                return row
    return None


def _has_endpoint(
    entry: dict[str, Any], registry_row: dict[str, Any] | None
) -> bool:
    """Derive ``has_endpoint`` from the registry row + catalog chart-registry flag.

    ``True`` when the registry carries a non-empty ``card_endpoint``/
    ``series_endpoint`` OR the catalog says the metric is in the chart registry
    with at least one card/series chart; ``False`` otherwise.
    """
    if registry_row:
        if (registry_row.get("card_endpoint") or "").strip():
            return True
        if (registry_row.get("series_endpoint") or "").strip():
            return True
        try:
            if int(registry_row.get("n_card") or 0) > 0:
                return True
            if int(registry_row.get("n_series") or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return bool(entry.get("in_chart_registry"))


@mcp.tool()
def list_metrics(
    namespace: str = "", domain: str = "", kind: str = "", limit: int = 500
) -> str:
    """List metrics from the offline catalog (reads source JSON, never the graph).

    Reads ``data/metric_nodes.rare_seeds.json`` and returns a compact row per
    metric. ``operational`` metrics are excluded (they are dropped from the node
    set). Optional filters narrow by source namespace, domain, or derived
    ``node_kind``. Read-only; nothing touches Neo4j.

    Args:
        namespace: Optional ``source`` namespace filter (e.g. ``google_ads``,
            ``blended``); empty = all.
        domain: Optional ``domain`` filter (e.g. ``marketing``); empty = all.
        kind: Optional ``node_kind`` filter (``metric`` | ``intermediary`` |
            ``input`` | ``constant``); empty = all.
        limit: Maximum number of metrics to return (1..2000).

    Returns:
        JSON string ``{count, metrics: [{metric_id, title, source, domain,
        node_kind, is_ml}]}`` (or an error).
    """
    safe_limit = max(1, min(int(limit), 2000))
    try:
        catalog = _load_metric_catalog()
    except Exception as exc:
        return _error(str(exc))
    metrics: list[dict[str, Any]] = []
    for metric_id, entry in (catalog.get("metrics") or {}).items():
        source = entry.get("source") or ""
        if source == "operational" or metric_id.split(".")[0] == "operational":
            continue
        if namespace and source != namespace:
            continue
        if domain and (entry.get("domain") or "") != domain:
            continue
        node_kind = _derive_node_kind(entry)
        if kind and node_kind != kind:
            continue
        metrics.append(
            {
                "metric_id": metric_id,
                "title": entry.get("title"),
                "source": source or None,
                "domain": entry.get("domain"),
                "node_kind": node_kind,
                "is_ml": entry.get("is_ml"),
            }
        )
        if len(metrics) >= safe_limit:
            break
    return json.dumps({"count": len(metrics), "metrics": metrics})


@mcp.tool()
def get_metric_source(metric_id: str) -> str:
    """Join one metric's offline evidence from every source file (never the graph).

    Returns a single JSON object joining: the catalog entry (formula_human,
    formula_explanation, depends_on, formula_components, aliases, dashboards,
    source_code_ref, ml_* …) from ``metric_nodes.rare_seeds.json``; the matching
    ``metric_registry`` CSV row (mart_model / mart_source / source_columns /
    source_expr); the chart-registry entry (matched by ``dashboard:concept``);
    and the openapi endpoint slice for the metric's card/series endpoints,
    FILTERED through :func:`harness.ingest.endpoint_filters.is_kg_endpoint`. The
    chart registry / openapi files are sliced, never embedded whole. Adds
    ``node_kind`` + ``has_endpoint`` hints. Read-only.

    Args:
        metric_id: The catalog metric id (e.g. ``blended.roas``).

    Returns:
        JSON string ``{found, metric_id, source_table, node_kind, has_endpoint,
        catalog, registry, chart_registry, openapi_endpoints}`` (or an error).
    """
    # endpoint_filters is authored by a parallel track; import lazily so this
    # module imports cleanly even if that file lands a moment later.
    try:
        entry, source_table = _catalog_entry(metric_id)
        if entry is None:
            return json.dumps({"found": False, "metric_id": metric_id})
        registry_row = _registry_row(metric_id)
        node_kind = _derive_node_kind(entry)
        has_endpoint = _has_endpoint(entry, registry_row)

        # Chart-registry entry: keyed ``dashboard:concept`` — try each dashboard
        # on the metric against its concept, then fall back to a concept scan.
        registry = _load_json_file(_CHART_REGISTRY_PATH)
        concept = entry.get("concept")
        chart_entry: dict[str, Any] | None = None
        for dash in entry.get("dashboards") or []:
            candidate = registry.get(f"{dash}:{concept}")
            if candidate is not None:
                chart_entry = candidate
                break
        if chart_entry is None and concept:
            for cand in registry.values():
                if cand.get("concept") == concept:
                    chart_entry = cand
                    break

        # OpenAPI endpoint slices for the metric's card/series endpoints, kept
        # only when KG-relevant (operational routes are filtered out).
        from harness.ingest.endpoint_filters import is_kg_endpoint

        endpoint_paths: list[str] = []
        for key in ("card_endpoint", "series_endpoint"):
            path = ((registry_row or {}).get(key) or "").strip()
            if path and path not in endpoint_paths:
                endpoint_paths.append(path)
        spec_paths = (
            _load_json_file(_OPENAPI_PATH).get("paths") or {}
        ) if endpoint_paths else {}
        openapi_endpoints: list[dict[str, Any]] = []
        for path in endpoint_paths:
            if not is_kg_endpoint(path):
                continue
            op = spec_paths.get(path)
            get = op.get("get") if isinstance(op, dict) else None
            openapi_endpoints.append(
                {
                    "path": path,
                    "summary": (get or {}).get("summary"),
                    "description": (get or {}).get("description"),
                }
            )
    except Exception as exc:
        return _error(str(exc), metric_id=metric_id)

    registry_slice = None
    if registry_row is not None:
        registry_slice = {
            "node_id": registry_row.get("node_id"),
            "mart_model": registry_row.get("mart_model"),
            "mart_source": registry_row.get("mart_source"),
            "source_columns": registry_row.get("source_columns"),
            "source_expr": registry_row.get("source_expr"),
            "formula": registry_row.get("formula"),
            "formula_components": registry_row.get("formula_components"),
            "card_endpoint": registry_row.get("card_endpoint"),
            "series_endpoint": registry_row.get("series_endpoint"),
        }
    return json.dumps(
        {
            "found": True,
            "metric_id": metric_id,
            "source_table": source_table,
            "node_kind": node_kind,
            "has_endpoint": has_endpoint,
            "catalog": entry,
            "registry": registry_slice,
            "chart_registry": chart_entry,
            "openapi_endpoints": openapi_endpoints,
        }
    )


def _mart_sql_path(mart_model: str) -> Path | None:
    """Resolve a registry ``mart_model`` token to its dbt mart ``.sql`` file.

    Searches ``BC_2/dbt/models/marts/**`` for ``<token>.sql`` or
    ``mart_<token>.sql`` (the registry token sometimes carries the ``mart_``
    prefix and sometimes not). Returns the first match, or ``None``.
    """
    marts_dir = _BC2_ROOT / "dbt" / "models" / "marts"
    if not marts_dir.is_dir():
        return None
    token = mart_model.strip()
    if not token:
        return None
    candidates = {token, f"mart_{token}"} if not token.startswith("mart_") else {token}
    for sql_path in marts_dir.rglob("*.sql"):
        if sql_path.stem in candidates:
            return sql_path
    return None


def _parse_source_code_ref(ref: str) -> tuple[str | None, list[tuple[int, int]]]:
    """Parse a catalog ``source_code_ref`` into ``(repo_path, line_ranges)``.

    The ref looks like ``backend/app/repositories/foo.py:432-489 (fn), :924-956
    (fn)`` — a single relative file path followed by one or more ``start-end``
    line ranges. Returns the path (relative to the BC_2 root) and the parsed
    ``(start, end)`` ranges (empty when none parse).
    """
    import re

    if not ref or not ref.strip():
        return None, []
    path_match = re.search(r"([\w./-]+\.py)", ref)
    repo_path = path_match.group(1) if path_match else None
    ranges = [
        (int(start), int(end))
        for start, end in re.findall(r"(\d+)\s*-\s*(\d+)", ref)
    ]
    return repo_path, ranges


def _slice_lines(text: str, start: int, end: int) -> str:
    """Return 1-based inclusive lines ``start..end`` of ``text`` (char-capped)."""
    lines = text.splitlines()
    lo = max(1, start)
    hi = min(len(lines), end)
    if lo > hi:
        return ""
    return "\n".join(lines[lo - 1 : hi])[:_SLICE_CHAR_CAP]


@mcp.tool()
def get_bc2_sql(metric_id: str) -> str:
    """Read a metric's BC_2 dbt mart SQL + backend repository slice (never the graph).

    Best-effort: resolves the registry ``mart_model`` to a
    ``BC_2/dbt/models/marts/**/*.sql`` file (head slice), and parses the catalog
    ``source_code_ref`` to read the referenced
    ``BC_2/backend/app/repositories/*.py`` function line ranges. Every read is
    sliced/char-capped — whole files are never dumped. Returns ``{found: false}``
    when neither a mart nor a repository reference resolves. Read-only.

    Args:
        metric_id: The catalog metric id (e.g. ``blended.roas``).

    Returns:
        JSON string ``{found, metric_id, mart_sql, repository}`` (or an error).
    """
    try:
        entry, _ = _catalog_entry(metric_id)
        registry_row = _registry_row(metric_id)

        # dbt mart SQL — registry mart_model may be pipe-delimited (several marts).
        mart_sql: list[dict[str, Any]] = []
        mart_model = (registry_row or {}).get("mart_model") or ""
        for token in (mart_model.split("|") if mart_model else []):
            sql_path = _mart_sql_path(token)
            if sql_path is None:
                continue
            mart_sql.append(
                {
                    "mart_model": token,
                    "path": str(sql_path),
                    "sql": sql_path.read_text(encoding="utf-8")[:_SLICE_CHAR_CAP],
                }
            )

        # Backend repository function body from source_code_ref.
        repository: dict[str, Any] | None = None
        ref = (entry or {}).get("source_code_ref") or ""
        repo_rel, ranges = _parse_source_code_ref(ref)
        if repo_rel:
            repo_path = _BC2_ROOT / repo_rel
            if repo_path.is_file():
                text = repo_path.read_text(encoding="utf-8")
                slices = [
                    {
                        "start": start,
                        "end": end,
                        "code": _slice_lines(text, start, end),
                    }
                    for start, end in ranges
                ]
                repository = {
                    "source_code_ref": ref,
                    "path": str(repo_path),
                    "slices": slices,
                }
    except Exception as exc:
        return _error(str(exc), metric_id=metric_id)

    if not mart_sql and repository is None:
        return json.dumps({"found": False, "metric_id": metric_id})
    return json.dumps(
        {
            "found": True,
            "metric_id": metric_id,
            "mart_sql": mart_sql,
            "repository": repository,
        }
    )


if __name__ == "__main__":
    mcp.run()
