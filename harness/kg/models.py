"""Pydantic data models for the ThoughtWire Causal Knowledge Graph.

Encodes the 10 V1 node labels (schema sections 3-4), the section-7 enums (live
OpenAPI enums plus KG-extension vocabularies), the edge-type catalog (section 6),
and the arbitration proposal payload (section 8).

All node models subclass :class:`GraphNode`, which provides the shared
provenance/lifecycle fields and :meth:`GraphNode.cypher_props`, the serializer
that produces a Neo4j-safe property map (enum values as strings, no nested maps,
homogeneous primitive lists, no ``None`` values).

Authoritative source: ``docs/final-schema-claude.md``. Field required-ness and
nullability follow the schema's Req columns and section-7 type corrections
(``yes/no`` -> bool, pipe-delimited -> ``list[str]``, ``causal_role_confidence``
is the enum ``low/medium/high``, and ``formula_text``/``dimensions``/``n_periods``/
``availability`` are nullable).
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Section 7 enums — live OpenAPI component schemas (verbatim) + graph-derived
# and KG-extension (Codex-adopted) vocabularies.
# ---------------------------------------------------------------------------


class ValueFormat(StrEnum):
    """OpenAPI ``ValueFormat`` (4)."""

    number = "number"
    currency = "currency"
    percentage = "percentage"
    decimal = "decimal"


class Granularity(StrEnum):
    """OpenAPI ``Granularity`` (4)."""

    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"


class ChartType(StrEnum):
    """OpenAPI ``ChartType`` (15)."""

    line = "line"
    area = "area"
    bar = "bar"
    horizontal_bar = "horizontal_bar"
    grouped_bar = "grouped_bar"
    pie = "pie"
    donut = "donut"
    sankey = "sankey"
    heatmap = "heatmap"
    table = "table"
    sparkline = "sparkline"
    scatter = "scatter"
    treemap = "treemap"
    gauge = "gauge"
    funnel = "funnel"


class ThresholdType(StrEnum):
    """OpenAPI ``ThresholdType`` (3) + KG-extension (Codex) types."""

    static = "static"
    percentile = "percentile"
    seasonal = "seasonal"
    # KG-extension (Codex)
    warning = "warning"
    critical = "critical"
    target = "target"
    anomaly = "anomaly"
    sla = "sla"
    budget = "budget"


class ThresholdDirection(StrEnum):
    """OpenAPI ``ThresholdDirection`` (3)."""

    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"
    target_is_best = "target_is_best"


class ConditionType(StrEnum):
    """OpenAPI ``ConditionType`` (4)."""

    threshold = "threshold"
    anomaly = "anomaly"
    trend = "trend"
    missing_data = "missing_data"


class ComparisonOperator(StrEnum):
    """OpenAPI ``ComparisonOperator``/``ConditionOperator`` (8) + KG-ext."""

    lt = "lt"
    lte = "lte"
    gt = "gt"
    gte = "gte"
    eq = "eq"
    neq = "neq"
    between = "between"
    outside = "outside"
    # KG-extension (Codex)
    percent_change = "percent_change"
    z_score = "z_score"


class Severity(StrEnum):
    """OpenAPI ``Severity`` (5) + KG-extension ``blocking`` (Codex)."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"
    # KG-extension (Codex)
    blocking = "blocking"


class MetricCategory(StrEnum):
    """OpenAPI ``MetricCategory`` (14)."""

    advertising = "advertising"
    revenue = "revenue"
    traffic = "traffic"
    email = "email"
    customer = "customer"
    sms = "sms"
    google_ads = "google_ads"
    meta_ads = "meta_ads"
    efficiency = "efficiency"
    comparison = "comparison"
    financial = "financial"
    marketing = "marketing"
    product = "product"
    operational = "operational"


class MetricSource(StrEnum):
    """OpenAPI ``MetricSource`` (4).

    Note: ``Metric.source_set`` is a *free* ``list[str]`` (``magento``,
    ``linkedin_ads`` also appear) and is intentionally **not** constrained to
    this enum.
    """

    ga4 = "ga4"
    google_ads = "google_ads"
    meta_ads = "meta_ads"
    klaviyo = "klaviyo"


class UserRole(StrEnum):
    """OpenAPI ``UserRole`` (7) — auth-layer roles mapped via ``Role.auth_role``."""

    super_admin = "super_admin"
    agency_admin = "agency_admin"
    tenant_admin = "tenant_admin"
    analyst = "analyst"
    viewer = "viewer"
    admin = "admin"
    user = "user"


# --- Graph-derived vocab (from rare_seeds) ---------------------------------


class UnitFamily(StrEnum):
    """``Metric.unit_family``."""

    currency = "currency"
    ratio = "ratio"
    percent = "percent"
    count = "count"
    duration = "duration"
    score = "score"


class DefaultDirection(StrEnum):
    """``Metric.default_direction`` (mirrors ThresholdDirection semantics).

    Includes ``neutral`` for metrics whose catalog ``polarity`` is directionless.
    """

    higher_is_better = "higher_is_better"
    lower_is_better = "lower_is_better"
    target_is_best = "target_is_best"
    neutral = "neutral"


class ScopeLevel(StrEnum):
    """``Metric.scope_level`` — derived from ``scope``."""

    global_ = "global"
    platform = "platform"
    channel = "channel"
    dashboard = "dashboard"
    campaign = "campaign"
    product = "product"
    customer = "customer"
    model = "model"


class Aggregation(StrEnum):
    """``Metric.aggregation`` (rare_seeds)."""

    level = "level"
    sum = "sum"
    avg = "avg"
    rate = "rate"
    ratio = "ratio"
    median = "median"


class MeasurementType(StrEnum):
    """``Metric.measurement_type``."""

    direct = "direct"
    derived = "derived"
    modeled = "modeled"
    forecast = "forecast"
    status = "status"


class SourceCardinality(StrEnum):
    """``Metric.source`` — scalar source cardinality (type correction #4)."""

    single = "single"
    multi = "multi"


class PrimaryGrain(StrEnum):
    """``Metric.primary_grain`` (rare_seeds ``grain``)."""

    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    campaign = "campaign"
    product = "product"
    customer = "customer"


class CausalRole(StrEnum):
    """``Metric.causal_role`` (rare_seeds ``type``)."""

    outcome = "outcome"
    mediator = "mediator"
    controllable = "controllable"
    constraint = "constraint"
    external = "external"
    ml_output = "ml_output"
    untyped = "untyped"


class CausalRoleConfidence(StrEnum):
    """``Metric.causal_role_confidence`` — categorical (type correction #1)."""

    low = "low"
    medium = "medium"
    high = "high"


class NodeKind(StrEnum):
    """``Metric.node_kind`` — graph-node role in the causal chain.

    ``metric``/``intermediary`` are derived measures; ``input`` is a raw
    source field; ``constant`` is a fixed coefficient. Endpoint-less ``input``/
    ``constant``/``intermediary`` nodes stay in causal paths (the UI dims them).
    """

    metric = "metric"
    intermediary = "intermediary"
    input = "input"
    constant = "constant"


class MLKind(StrEnum):
    """``Metric.ml_kind`` — ML-metric flavour (set only when ``is_ml``)."""

    prediction = "prediction"
    performance = "performance"
    hybrid = "hybrid"


class FormulaStatus(StrEnum):
    """``Metric.formula_status``."""

    explicit = "explicit"
    parsed = "parsed"
    unknown = "unknown"


class DataClassification(StrEnum):
    """Shared ``data_classification`` / ``max_data_classification`` vocab."""

    public = "public"
    internal = "internal"
    restricted = "restricted"
    executive = "executive"


class MetricStatus(StrEnum):
    """``Metric.status`` lifecycle."""

    proposed = "proposed"
    active = "active"
    deprecated = "deprecated"
    blocked = "blocked"


class DataQualityStatus(StrEnum):
    """``data_quality_status`` for Metric / Platform."""

    good = "good"
    warning = "warning"
    degraded = "degraded"
    unknown = "unknown"


# --- Node-level review pipeline / lifecycle vocab --------------------------


class ReviewState(StrEnum):
    """Node ``review_state`` review pipeline (distinct from ``status``)."""

    proposed = "proposed"
    needs_review = "needs_review"
    active = "active"
    deprecated = "deprecated"


class GovernanceReviewState(StrEnum):
    """Governance ``review_state`` (Policy/Threshold) review pipeline."""

    draft = "draft"
    active = "active"
    needs_review = "needs_review"
    retired = "retired"


class PopulationStatus(StrEnum):
    """``population_status`` for governance shells (``defined`` in V1)."""

    defined = "defined"
    populated = "populated"


# --- Business / Domain / Product / Platform vocab --------------------------


class BusinessTier(StrEnum):
    """``Business.tier`` — drives org-graph shape."""

    startup = "startup"
    smb = "smb"
    mid_market = "mid_market"
    mnc = "mnc"


class BusinessType(StrEnum):
    """``Business.business_type`` / ``business_type`` KG-ext vocab."""

    ecommerce = "ecommerce"
    saas = "saas"
    marketplace = "marketplace"
    retail = "retail"
    services = "services"
    other = "other"


class DecisionRiskPosture(StrEnum):
    """``Business.decision_risk_posture``."""

    conservative = "conservative"
    balanced = "balanced"
    aggressive = "aggressive"


class BusinessStatus(StrEnum):
    """``Business.status`` lifecycle."""

    active = "active"
    paused = "paused"
    archived = "archived"


class DomainType(StrEnum):
    """``Domain.domain_type``."""

    business = "business"
    technical = "technical"
    risk = "risk"
    data_quality = "data_quality"
    ml = "ml"


class CommonStatus(StrEnum):
    """Common ``status`` lifecycle for Domain / Product (active/hidden/...)."""

    active = "active"
    hidden = "hidden"
    deprecated = "deprecated"
    proposed = "proposed"


class ProductCategory(StrEnum):
    """``IntelligenceProduct.category``."""

    analytics = "analytics"
    decisioning = "decisioning"
    creative = "creative"
    external = "external"


class SchemaStatus(StrEnum):
    """``IntelligenceProduct.schema_status``."""

    owned = "owned"
    shared = "shared"


class PlatformType(StrEnum):
    """``Platform.platform_type`` (KG-ext, Codex)."""

    analytics = "analytics"
    ads = "ads"
    crm = "crm"
    ecommerce = "ecommerce"
    warehouse = "warehouse"
    activation = "activation"
    support = "support"
    finance = "finance"
    other = "other"


class PlatformStatus(StrEnum):
    """``Platform.status`` lifecycle."""

    active = "active"
    degraded = "degraded"
    deprecated = "deprecated"
    planned = "planned"


# --- Dashboard / UIComponent vocab -----------------------------------------


class DashboardType(StrEnum):
    """``Dashboard.dashboard_type``."""

    executive = "executive"
    operational = "operational"
    ml = "ml"
    review = "review"


class SurfaceStatus(StrEnum):
    """``status`` for Dashboard / UIComponent (active/hidden/deprecated/proposed)."""

    active = "active"
    hidden = "hidden"
    deprecated = "deprecated"
    proposed = "proposed"


class ComponentKind(StrEnum):
    """``UIComponent.component_kind``."""

    chart = "chart"
    kpi_card = "kpi_card"
    table = "table"
    alert_panel = "alert_panel"


class Visibility(StrEnum):
    """``UIComponent.visibility`` (migrated from the old RENDERS edge)."""

    visible = "visible"
    hidden = "hidden"
    collapsed = "collapsed"


# --- Policy vocab -----------------------------------------------------------


class PolicyType(StrEnum):
    """``Policy.policy_type`` (KG-ext, Codex)."""

    access = "access"
    interpretation = "interpretation"
    alerting = "alerting"
    escalation = "escalation"
    approval = "approval"
    action_guardrail = "action_guardrail"
    data_quality = "data_quality"


class AppliesToKind(StrEnum):
    """``Policy.applies_to_kind`` — node kind governed."""

    Business = "Business"
    IntelligenceProduct = "IntelligenceProduct"
    Domain = "Domain"
    Platform = "Platform"
    Metric = "Metric"
    Dashboard = "Dashboard"
    UIComponent = "UIComponent"
    Threshold = "Threshold"
    Role = "Role"


# --- Role vocab -------------------------------------------------------------


class RoleType(StrEnum):
    """``Role.role_type``."""

    executive = "executive"
    department_lead = "department_lead"
    operator = "operator"
    analyst = "analyst"
    viewer = "viewer"
    system_agent = "system_agent"
    approver = "approver"


class RoleStatus(StrEnum):
    """``Role.status`` lifecycle."""

    active = "active"
    disabled = "disabled"
    deprecated = "deprecated"


# ---------------------------------------------------------------------------
# Proposal operation enum (section 8)
# ---------------------------------------------------------------------------


class ProposalOperation(StrEnum):
    """``Proposal.operation``."""

    upsert = "upsert"
    #: Edge-only proposal (M3 causal layer): no node is upserted; the proposal's
    #: ``payload`` is a single edge dict (``type``/``from_id``/``to_id``/
    #: ``properties``) applied through the arbitration ``upsert_edge`` path.
    upsert_edge = "upsert_edge"
    deprecate = "deprecate"
    delete = "delete"


# ---------------------------------------------------------------------------
# Base node model + Neo4j-safe serialization
# ---------------------------------------------------------------------------


def _neo4j_safe(value: Any) -> Any:
    """Coerce a single value into a Neo4j-storable primitive (or primitive list).

    Neo4j properties must be primitives or homogeneous lists of primitives:
    no nested maps, and no ``None`` inside lists. Any dict or list-of-dict is
    JSON-encoded to a string; lists are filtered of ``None`` and JSON-encoded
    element-wise if they contain non-primitive members.
    """
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, list):
        cleaned = [v for v in value if v is not None]
        if any(isinstance(v, (dict, list)) for v in cleaned):
            # Non-homogeneous / nested → serialize each element to a string.
            return [
                json.dumps(v, sort_keys=True, default=str)
                if isinstance(v, (dict, list))
                else v
                for v in cleaned
            ]
        return cleaned
    return value


class GraphNode(BaseModel):
    """Shared base for every graph node model.

    Subclasses set the ``LABEL`` and ``KEY_FIELD`` class variables. Common
    optional provenance/lifecycle fields (schema section 4: every node carries
    ``created_at``, ``updated_at``, ``status``, ``review_state``,
    ``source_profile_id``, ``last_verified_at``) live here.

    ``use_enum_values=True`` ensures enum-typed fields already hold their string
    values, so :meth:`cypher_props` emits Neo4j-friendly strings.
    """

    model_config = ConfigDict(use_enum_values=True)

    LABEL: ClassVar[str]
    KEY_FIELD: ClassVar[str]

    # Common optional fields shared by all nodes (section 4).
    # ``review_state`` is the *review pipeline* (distinct from ``status``, the
    # lifecycle); the node vocab is proposed/needs_review/active/deprecated.
    # Policy/Threshold override this to the governance vocab below.
    created_at: str | None = None
    updated_at: str | None = None
    last_verified_at: str | None = None
    source_profile_id: str | None = None
    review_state: ReviewState | None = None

    @property
    def key_value(self) -> str:
        """Return the value of this node's identity (``KEY_FIELD``) property."""
        return getattr(self, self.KEY_FIELD)

    def cypher_props(self) -> dict[str, Any]:
        """Return a Neo4j-safe property map for this node.

        ``model_dump(mode='json', exclude_none=True)`` drops ``None`` values
        (so ``SET n += $props`` never deletes a property) and renders enums and
        dates as JSON-native values. Remaining dict / list-of-dict values are
        JSON-encoded to strings, and list props are guaranteed homogeneous
        primitive lists with no ``None`` elements.
        """
        raw = self.model_dump(mode="json", exclude_none=True)
        return {key: _neo4j_safe(value) for key, value in raw.items()}


# ---------------------------------------------------------------------------
# Node models (schema sections 3-4)
# ---------------------------------------------------------------------------


class Business(GraphNode):
    """The single root node — one per tenant database (schema section 4)."""

    LABEL: ClassVar[str] = "Business"
    KEY_FIELD: ClassVar[str] = "business_id"

    business_id: str
    display_name: str
    tier: BusinessTier
    status: BusinessStatus
    business_type: BusinessType | None = None
    industry: str | None = None
    primary_currency: str | None = None
    timezone: str | None = None
    fiscal_year_start_month: int | None = None
    default_granularity: Granularity | None = None
    decision_risk_posture: DecisionRiskPosture | None = None
    strategic_intent_summary: str | None = None
    north_star_metrics: list[str] | None = None
    operating_constraints: list[str] | None = None
    default_data_classification: DataClassification | None = None
    root_seniority_rank: int | None = None


class Domain(GraphNode):
    """FRD functional column — spine axis (schema section 4)."""

    LABEL: ClassVar[str] = "Domain"
    KEY_FIELD: ClassVar[str] = "domain_id"

    domain_id: str
    name: str
    decision_scope_summary: str
    min_level: int
    data_classification: DataClassification
    status: CommonStatus
    domain_type: DomainType | None = None
    parent_domain_id: str | None = None
    owner_role_id: str | None = None
    approval_policy_summary: str | None = None
    default_product_ids: list[str] | None = None
    default_platform_ids: list[str] | None = None


class IntelligenceProduct(GraphNode):
    """IQ application — spine axis (schema section 4)."""

    LABEL: ClassVar[str] = "IntelligenceProduct"
    KEY_FIELD: ClassVar[str] = "product_id"

    product_id: str
    display_name: str
    status: CommonStatus
    category: ProductCategory | None = None
    description: str | None = None
    schema_name: str | None = None
    schema_status: SchemaStatus | None = None
    route_prefixes: list[str] | None = None
    owner_role_id: str | None = None
    default_domain_ids: list[str] | None = None
    default_data_classification: DataClassification | None = None
    min_level: int


class Platform(GraphNode):
    """Source/action vendor system — spine axis, thin & lazy (schema section 4)."""

    LABEL: ClassVar[str] = "Platform"
    KEY_FIELD: ClassVar[str] = "platform_id"

    platform_id: str
    platform_name: str
    platform_type: PlatformType
    supports_actions: bool
    data_classification: DataClassification
    min_level: int
    status: PlatformStatus
    #: Parent platform id for a sub-platform/sub-channel (e.g. ``google_youtube``
    #: -> ``google_ads``); ``None`` for a top-level platform. The hierarchy is also
    #: an edge (``Platform -[:PARENT_OF]-> Platform``); this is the fast-read cache.
    parent_platform_id: str | None = None
    connector_id: str | None = None
    connector_family: str | None = None
    owner_role_id: str | None = None
    freshness_sla_hours: float | None = None
    source_priority: int | None = None
    api_base_url_ref: str | None = None
    data_quality_status: DataQualityStatus | None = None
    last_successful_sync_at: str | None = None


class Metric(GraphNode):
    """The hub node (schema section 3).

    Denormalizes domain/product/platform data as flat fields; the spine nodes
    remain the source of truth. ``product_ids``/``domain_ids`` are arrays (a
    canonical metric can sit on several products/domains). Pipe-delimited source
    fields are modelled as ``list[str]``; ``formula_text``/``dimensions``/
    ``n_periods``/``availability`` are nullable per the type corrections.
    """

    LABEL: ClassVar[str] = "Metric"
    KEY_FIELD: ClassVar[str] = "metric_uid"

    # Identity & semantics
    metric_uid: str
    canonical_id: str
    metric_id: str
    display_name: str
    description: str | None = None
    concept_key: str | None = None
    concept_name: str | None = None
    synonyms: list[str] | None = None
    aliases: list[str] | None = None
    unit_family: UnitFamily | None = None
    default_direction: DefaultDirection | None = None
    #: Graph-node role in the causal chain (metric/intermediary/input/constant).
    #: Endpoint-less inputs/constants/intermediaries still sit in causal paths.
    node_kind: NodeKind = NodeKind.metric
    has_endpoint: bool = True

    # Classification (domain + product denormalized)
    product_ids: list[str]
    product_names: list[str] | None = None
    domain_ids: list[str]
    domain_names: list[str] | None = None
    domain_owner_role_keys: list[str] | None = None
    scope_key: str
    scope_level: ScopeLevel | None = None
    metric_base: str
    category: MetricCategory | None = None
    aggregation: Aggregation | None = None
    value_format: ValueFormat | None = None
    granularity: Granularity | None = None
    measurement_type: MeasurementType | None = None

    # Platform / source / lineage
    source: SourceCardinality | None = None
    source_set: list[str] | None = None
    platform_ids: list[str] | None = None
    platform_names: list[str] | None = None
    platform_types: list[str] | None = None
    primary_platform_id: str | None = None
    connector_ids: list[str] | None = None
    mart_sources: list[str] | None = None
    #: SQL expression from ``metric_registry`` (e.g. ``SUM(REVENUE)``); null ok.
    source_expr: str | None = None
    #: Mart column names used by this metric.
    source_columns: list[str] | None = None
    #: Verbatim backend SQL (from ``get_bc2_sql``).
    sql_query_real: str | None = None
    #: LLM-generated clean, runnable ``SELECT``.
    sql_query_canonical: str | None = None
    #: Per-mart grain (optional).
    mart_grains: list[str] | None = None
    platform_data_quality_json: dict[str, Any] | None = None
    data_freshness_by_platform_json: dict[str, Any] | None = None
    primary_grain: PrimaryGrain | None = None
    grain_source: str | None = None
    dimensions: list[str] | None = None
    availability: str | None = None
    n_periods: int | None = None
    #: ISO date — data-coverage start.
    history_start: str | None = None
    #: ISO date — data-coverage end.
    history_end: str | None = None
    #: Latest data older than the freshness SLA.
    data_stale: bool | None = None
    #: QA: ``formula_text`` disagrees with ``sql_query_real``.
    formula_sql_mismatch: bool | None = None
    #: QA explanation set when ``formula_sql_mismatch``.
    formula_sql_note: str | None = None

    # Causal
    causal_role: CausalRole | None = None
    causal_role_confidence: CausalRoleConfidence | None = None
    is_model_output: bool | None = None
    is_derived: bool
    formula_status: FormulaStatus | None = None
    formula_text: str | None = None
    #: Provenance trail of which sources created/enriched this metric, e.g.
    #: ``["openapi:google-search", "registry:google-search:roas",
    #: "bc2:coded:AD_013", "bc2:ontology:roas", "override:<id>", "llm:<run>"]``.
    #: Free-form audit strings (KG skeleton build); never an identity.
    source_refs: list[str] | None = None
    #: Backend repository ``file:line`` reference (catalog ``source_code_ref``).
    bc2_ref: str | None = None

    # ML classification (set when ``is_ml``; null otherwise)
    is_ml: bool | None = None
    ml_kind: MLKind | None = None
    #: ML task, e.g. ``timeseries|regression|classification|clustering|...``.
    ml_task: str | None = None
    ml_model: str | None = None
    #: ML subject, e.g. ``customer|category|product|marketing|...``.
    ml_entity: str | None = None

    # Folded-in chart-registry semantics (M2 product decision — see UIComponent
    # below). Instead of one UIComponent per registry entry (646, 1:1 with
    # Metric), the per-chart registry specifics live on the Metric, and the
    # metric links to a single generalised chart-TYPE UIComponent via VISUALIZES.
    formula_explanation: str | None = None
    how_to_read: list[str] = []
    decisions_answered: list[str] = []
    narration_text: str | None = None
    chart_type: ChartType | None = None
    chart_id: str | None = None

    # Endpoints
    card_endpoint: str | None = None
    series_endpoint: str | None = None
    endpoint_paths: list[str] | None = None

    # Surfacing + RBAC + lifecycle
    dashboard_ids: list[str] | None = None
    component_ids: list[str] | None = None
    source_dashboards: list[str] | None = None
    data_classification: DataClassification
    min_level: int
    owner_role_id: str | None = None
    is_kpi: bool | None = None
    keep: bool | None = None
    status: MetricStatus
    data_quality_status: DataQualityStatus | None = None
    confidence: float | None = None


class Dashboard(GraphNode):
    """Product surface & access boundary (schema section 4)."""

    LABEL: ClassVar[str] = "Dashboard"
    KEY_FIELD: ClassVar[str] = "dashboard_id"

    dashboard_id: str
    display_name: str
    product_id: str
    data_classification: DataClassification
    min_level: int
    status: SurfaceStatus
    route_path: str | None = None
    domain_ids: list[str] | None = None
    dashboard_type: DashboardType | None = None
    default_endpoint_path: str | None = None
    metadata_endpoint_path: str | None = None
    audience_role_ids: list[str] | None = None
    source_registry: str | None = None


class UIComponent(GraphNode):
    """Generalised chart-TYPE node — *not* one per chart-registry entry.

    **M2 product decision (approved deviation from schema §4).** The schema
    describes one ``UIComponent`` per chart-registry entry (646, 1:1 with
    ``Metric``), which is too repetitive. Instead we create a small fixed set of
    *generalised* chart-type nodes (the 15 ``ChartType`` values plus
    ``kpi_card`` / ``alert_panel`` — 17 total, ``component_id = "uic:<slug>"``),
    seeded once at bootstrap like the spine. The per-entry registry semantics
    (formula, how_to_read, decisions_answered, narration, chart_id, …) are folded
    onto the :class:`Metric`, and each metric links to its chart-type node via
    the ``VISUALIZES`` edge (one generalised node VISUALIZES many metrics).

    Because a node now represents a *type*, the previously-required per-entry
    fields (``canonical_id``, ``dashboard_id``, ``chart_id``, ``title``,
    ``data_classification``, ``min_level``, ``status``) are optional so a type
    node validates, e.g. ``UIComponent(component_id="uic:bar",
    component_kind="chart", chart_type="bar", display_name="Bar Chart",
    status="active")``. Only ``component_id`` (the identity) is required.
    """

    LABEL: ClassVar[str] = "UIComponent"
    KEY_FIELD: ClassVar[str] = "component_id"

    component_id: str
    # Generalised type node: human-readable name for the chart type / kind.
    display_name: str | None = None
    component_kind: ComponentKind | None = None
    chart_type: ChartType | None = None
    status: SurfaceStatus | None = None
    # Per-entry-only fields — now optional (the per-chart specifics live on
    # Metric; a generalised type node leaves these unset).
    canonical_id: str | None = None
    dashboard_id: str | None = None
    chart_id: str | None = None
    title: str | None = None
    data_classification: DataClassification | None = None
    min_level: int | None = None
    section_id: str | None = None
    display_order: int | None = None
    visibility: Visibility | None = None
    query_endpoint_path: str | None = None
    metric_keys: list[str] | None = None
    formula: str | None = None
    formula_explanation: str | None = None
    how_to_read: list[str] | None = None
    decisions_answered: list[str] | None = None
    narration_text: str | None = None
    audio_file: str | None = None


class Policy(GraphNode):
    """Governance node — defined now, populated later (schema section 4)."""

    LABEL: ClassVar[str] = "Policy"
    KEY_FIELD: ClassVar[str] = "policy_id"

    policy_id: str
    policy_name: str | None = None
    description: str | None = None
    metric_id: str | None = None
    policy_type: PolicyType | None = None
    applies_to_kind: AppliesToKind | None = None
    condition_type: ConditionType | None = None
    condition_operator: ComparisonOperator | None = None
    condition_value: float | None = None
    condition_value_high: float | None = None
    condition_expression: str | None = None
    evaluation_window: str | None = None
    evaluation_frequency: str | None = None
    cooldown_hours: float | None = None
    escalate_after_hours: float | None = None
    severity: Severity | None = None
    auto_investigate: bool | None = None
    notify_channels: list[str] | None = None
    effect_json: dict[str, Any] | None = None
    owner_role_id: str | None = None
    approval_required: bool | None = None
    approval_role_ids: list[str] | None = None
    priority: int | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    is_active: bool | None = None
    # ``status`` is the lifecycle; ``review_state`` is the governance review
    # pipeline (draft/active/needs_review/retired) — schema §4 lines ~277, ~422.
    status: SurfaceStatus | None = None
    review_state: GovernanceReviewState | None = None
    source: str | None = None
    population_status: PopulationStatus | None = None


class Threshold(GraphNode):
    """Metric-boundary governance node — defined now, populated later (section 4)."""

    LABEL: ClassVar[str] = "Threshold"
    KEY_FIELD: ClassVar[str] = "threshold_id"

    threshold_id: str
    metric_id: str | None = None
    metric_name: str | None = None
    threshold_type: ThresholdType | None = None
    operator: ComparisonOperator | None = None
    direction: ThresholdDirection | None = None
    green_value: str | None = None
    yellow_value: str | None = None
    red_value: str | None = None
    warning_value_num: float | None = None
    critical_value_num: float | None = None
    target_value_num: float | None = None
    avg_val: float | None = None
    stddev_val: float | None = None
    lower_2sigma: float | None = None
    upper_2sigma: float | None = None
    min_val: float | None = None
    max_val: float | None = None
    # Company percentile distribution (own data; dummy/LLM-seeded now, computed
    # from marts later — the deferred statistical layer).
    p95_val: float | None = None
    p85_val: float | None = None
    p75_val: float | None = None
    p50_val: float | None = None
    percentile_basis: str | None = None  # e.g. "company trailing-90d daily"
    # Industry benchmark (value + band + provenance) for comparison.
    industry_standard_val: float | None = None
    industry_min_val: float | None = None
    industry_max_val: float | None = None
    industry_source: str | None = None  # "llm:claude-opus-4-8" | "WordStream 2024"
    industry_as_of: str | None = None  # ISO date the benchmark reflects
    # Company's own current snapshot + the severity of a band breach.
    current_val: float | None = None
    current_as_of: str | None = None
    severity: Severity | None = None
    category: str | None = None
    unit: str | None = None
    grain: str | None = None
    evaluation_window: str | None = None
    segment_filter_json: dict[str, Any] | None = None
    explanation: str | None = None
    owner_role_id: str | None = None
    source: str | None = None
    # ``status`` is the lifecycle; ``review_state`` is the governance review
    # pipeline (draft/active/needs_review/retired) — schema §4 lines ~277, ~441.
    status: SurfaceStatus | None = None
    review_state: GovernanceReviewState | None = None
    population_status: PopulationStatus | None = None


class Role(GraphNode):
    """RBAC subject + seniority + social-graph anchor (schema section 4)."""

    LABEL: ClassVar[str] = "Role"
    KEY_FIELD: ClassVar[str] = "role_id"

    role_id: str
    role_key: str
    display_name: str
    role_type: RoleType
    seniority_rank: int
    max_data_classification: DataClassification
    can_manage_rbac: bool
    can_create_policy: bool
    can_create_threshold: bool
    can_edit_endpoint: bool
    status: RoleStatus
    auth_role: UserRole | None = None
    domain_id: str | None = None
    domain_scope_ids: list[str] | None = None
    platform_scope_ids: list[str] | None = None
    default_product_ids: list[str] | None = None
    default_platform_ids: list[str] | None = None
    agent_context_limit: int | None = None
    redaction_policy_json: dict[str, Any] | None = None
    is_engine_generated: bool | None = None


# ---------------------------------------------------------------------------
# Label / key-field / edge-type collections
# ---------------------------------------------------------------------------

#: The 10 V1 node labels.
NODE_LABELS: frozenset[str] = frozenset(
    {
        "Business",
        "Domain",
        "IntelligenceProduct",
        "Platform",
        "Metric",
        "Dashboard",
        "UIComponent",
        "Policy",
        "Threshold",
        "Role",
    }
)

#: Map of node label -> its unique identity (key) field.
NODE_KEY_FIELDS: dict[str, str] = {
    "Business": "business_id",
    "Domain": "domain_id",
    "IntelligenceProduct": "product_id",
    "Platform": "platform_id",
    "Metric": "metric_uid",
    "Dashboard": "dashboard_id",
    "UIComponent": "component_id",
    "Policy": "policy_id",
    "Threshold": "threshold_id",
    "Role": "role_id",
}

#: Every edge name in the section-6 edge catalog.
EDGE_TYPES: frozenset[str] = frozenset(
    {
        # Spine
        "HAS_DOMAIN",
        "HAS_PRODUCT",
        "PARENT_OF",
        "BELONGS_TO_DOMAIN",
        "CONTEXTUALIZES",
        "GOVERNS",
        "PART_OF_PRODUCT",
        "USES_PLATFORM",
        "SOURCES",
        "ACTIVATES",
        # Formula / aggregation
        "DECOMPOSES_INTO",
        # Governance
        "HAS_THRESHOLD",
        "GOVERNED_BY",
        "ENFORCES_THRESHOLD",
        # Surface
        "VISUALIZES",
        "SHOWN_ON",
        # Causal
        "INFLUENCES",
        # Ownership
        "OWNS",
        "OWNED_BY",
        # RBAC permission / access
        "CAN_ACCESS_PRODUCT",
        "CAN_ACCESS_PLATFORM",
        "CAN_VIEW",
        "CAN_EDIT",
        "CAN_APPROVE",
        # Org / social graph
        "REPORTS_TO",
        "ESCALATES_TO",
        "CAN_DELEGATE_TO",
        "INHERITS_FROM",
    }
)

#: Allowed ``relation`` subtypes for a ``DECOMPOSES_INTO`` (structural) edge.
#: ``formula``/``identity`` are SAME-SCOPE only; ``rollup``/``crossproduct``
#: bridge channel->blended (review-only, additive only).
DECOMPOSES_RELATIONS: frozenset[str] = frozenset(
    {"formula", "component", "identity", "rollup", "crossproduct", "funnel"}
)
#: Allowed ``relation`` subtypes for an ``INFLUENCES`` (causal) edge.
#: ``llm_causal`` is the agentic-build subtype (LLM-reasoned causal edge).
INFLUENCES_RELATIONS: frozenset[str] = frozenset(
    {
        "curated_rule",
        "llm_verified",
        "statistical",
        "statistical_candidate",
        "promoted",
        "llm_causal",
        "mart_lineage",
    }
)
#: Allowed structural-edge ``role`` values (``DECOMPOSES_INTO.role``). The role
#: fixes a component's part in its parent's formula and derives the edge sign
#: (``denominator``/``subtrahend`` ⇒ −1, else +1).
EDGE_ROLES: frozenset[str] = frozenset(
    {
        "numerator",
        "denominator",
        "addend",
        "subtrahend",
        "factor",
        "driver",
        "component",
    }
)

#: Edge ``review_state`` value that parks a causal edge in the human review
#: queue. Such an edge is PERSISTED on the graph but MUST be excluded from active
#: causal traversal / scoring until a human promotes it — mart-lineage candidates
#: (:func:`harness.agentic.enrich.promote_lineage_edges`) land here. This is an
#: *edge* review_state: a free string ('active' by default, plus the human-set
#: 'approved'/'applied' the arbitration writer protects), distinct from the node
#: :class:`ReviewState` pipeline above (which has no 'held' member).
HELD_REVIEW_STATE: str = "held"


def is_held_review_state(review_state: object) -> bool:
    """Return ``True`` when an edge ``review_state`` parks it in the review queue.

    A small predicate so callers test for the held state through one symbol
    rather than re-spelling the :data:`HELD_REVIEW_STATE` literal.
    """
    return review_state == HELD_REVIEW_STATE


def active_edge_predicate(rel_var: str = "r") -> str:
    """Return the Cypher ``WHERE`` fragment selecting edges active for traversal.

    The single source of truth every active causal traversal / scoring read
    should AND into its ``WHERE`` so held and deprecated edges are filtered
    identically: an edge counts as active only when it is neither soft-deleted
    (``status <> 'deprecated'``) nor parked in the review queue
    (``review_state <> 'held'``). A missing value defaults to active via
    ``coalesce`` (so legacy edges without the property are kept).

    Args:
        rel_var: The bound relationship variable in the surrounding pattern
            (e.g. ``"r"`` for ``-[r:INFLUENCES]->`` or a path's ``relationships``
            comprehension variable).

    Returns:
        A Cypher boolean expression string (no leading/trailing ``WHERE``).
    """
    return (
        f"coalesce({rel_var}.status, 'active') <> 'deprecated' "
        f"AND coalesce({rel_var}.review_state, 'active') <> '{HELD_REVIEW_STATE}'"
    )


# ---------------------------------------------------------------------------
# Arbitration proposal payload (schema section 8)
# ---------------------------------------------------------------------------


class Proposal(BaseModel):
    """A single arbitration proposal — the harvester emits proposals only.

    Matches the section-8 payload shape. ``payload`` holds the target node's
    properties; ``relationship_payloads`` holds edge proposals to write after
    the node.
    """

    model_config = ConfigDict(use_enum_values=True)

    proposal_id: str
    operation: ProposalOperation = ProposalOperation.upsert
    target_label: str
    target_id: str
    source_kind: str
    source_ref: str | None = None
    source_confidence: float | None = None
    review_state: str = "proposed"
    payload: dict[str, Any] = Field(default_factory=dict)
    relationship_payloads: list[dict[str, Any]] = Field(default_factory=list)
