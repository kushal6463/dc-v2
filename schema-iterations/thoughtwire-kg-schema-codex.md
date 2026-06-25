# ThoughtWire Compact V1 Knowledge Graph Schema - Codex

Last updated: 2026-06-13

This document is the final compact V1 schema for the ThoughtWire causal knowledge graph. It merges the earlier Codex blueprint, the Claude blueprint, the FRD, `docs/frd-docs/openapi.json`, and `docs/frd-docs/chart-registry.json` into a smaller implementation model.

The V1 graph has exactly six node labels:

1. `Metric`
2. `Dashboard`
3. `UIComponent`
4. `Policy`
5. `Threshold`
6. `Role`

Everything else is either a typed field, a controlled vocabulary, an external runtime concern, or a V2 concept.

---

## 1. Executive Decision

### The Correct V1 Shape

Use a compact six-node graph. Do not implement the broad future-state graph on day one.

The broad model is still useful conceptually, but it creates too many graph nodes before the system has enough reliable causal edges. The current source material already proves this risk: the rare-seeds graph has many nodes but very few meaningful edges. V1 should optimize for:

- reliable metric identity,
- dashboard and chart explainability,
- role-filtered agent context,
- policy and threshold governance,
- edge-generation discipline,
- simple future migration paths.

### Tenant Handling

Each tenant has a separate database. Therefore, `Tenant` is not a V1 graph node.

Tenant identity is handled by:

- Neo4j database selection,
- app runtime connection routing,
- environment/config metadata,
- audit logs outside the KG.

Inside a tenant database, every node is already tenant-scoped by construction. If the system later moves to one shared graph database for all tenants, reintroduce a `Tenant` node with composite uniqueness constraints. That is not the V1 assumption.

### Why Six Nodes

The six-node model keeps the graph easy to query and easy to explain:

```text
Role
  ├─ CAN_VIEW_* / CAN_EDIT_* / CAN_APPROVE_CHANGE
  ▼
Dashboard ──CONTAINS_COMPONENT──> UIComponent ──VISUALIZES──> Metric
                                                         │
                                                         ├─ HAS_THRESHOLD ──> Threshold
                                                         └─ GOVERNED_BY ────> Policy

Metric ──INFLUENCES|CAUSES|CORRELATES_WITH──> Metric
Policy ──EXPLAINS_THRESHOLD─────────────────> Threshold
Role   ──OWNS───────────────────────────────> Metric|Policy|Threshold|Dashboard
```

The six labels are enough to answer the first important questions:

- What is this metric?
- Where is it rendered?
- Which dashboard or chart uses it?
- Which endpoint supplies it?
- Which source systems does it depend on?
- Which policy and threshold govern it?
- Who can see, edit, approve, or own it?
- Which upstream/downstream metrics may affect it?

---

## 2. Source Truth Checked

### OpenAPI

Checked file:

- `docs/frd-docs/openapi.json`

Observed facts:

| Fact | Value |
|---|---:|
| Paths | 902 |
| Operations | 923 |
| GET operations | 877 |
| Non-GET operations | 46 |
| Component schemas | 463 |

OpenAPI role in V1:

- Provides endpoint paths and response schema names.
- Provides live enum values.
- Provides policy, threshold, metric, chart, dashboard response shapes.
- Does not directly decide causal truth.
- Does not create nodes for excluded control-plane endpoints.

### Chart Registry

Checked file:

- `docs/frd-docs/chart-registry.json`

Observed facts:

| Fact | Value |
|---|---:|
| Entries | 646 |
| Entries with `narration_text` | 558 |
| Entries with `id` | 646 |
| Entries with `title` | 646 |
| Entries with `formula` | 646 |
| Entries with `formula_explanation` | 646 |
| Entries with `how_to_read` | 646 |
| Entries with `decisions_answered` | 646 |
| Entries with `audio_file` | 646 |
| Entries with `dashboard_id` | 646 |
| Entries with `chart_id` | 646 |
| Entries with `canonical_id` | 646 |

Chart registry role in V1:

- Primary source for `UIComponent`.
- Secondary source for `Metric.formula_text`, `Metric.formula_explanation`, and `Metric.description`.
- Source for agent explanation fields such as how to read a chart and what decisions the chart answers.
- Not a direct source of causal truth.

### Claude Blueprint Material Incorporated

Useful Claude content folded into this V1 schema:

- Use exact OpenAPI enums instead of invented enum values.
- Treat RBAC as mandatory before an agent receives context.
- Split business-plane endpoint harvesting from control-plane/API infrastructure.
- Treat edge generation as the real success metric, not node count.
- Lazily materialize future causal and memory structures only after evidence exists.
- Do not construct a fake person/org chart before real auth/HR data exists.

---

## 3. V1 Nodes Only

The V1 graph must create only these labels:

| Node | Purpose | Created From |
|---|---|---|
| `Metric` | Business signal, source, endpoint, concept, formula, governance summary, causal role | OpenAPI paths/responses, chart registry, rare-seeds fields, reviewed classification |
| `Dashboard` | Product/domain surface grouping and RBAC boundary | OpenAPI dashboard paths, frontend route/dashboard ids, chart registry dashboard ids |
| `UIComponent` | Chart/card/table/narrative surface | `chart-registry.json`, OpenAPI chart endpoints |
| `Policy` | Monitoring/access/interpretation/escalation rule | OpenAPI `PolicyCreate` shape, reviewed config/manual input |
| `Threshold` | Numeric/band/anomaly boundary for metric health | OpenAPI `ThresholdConfig`, reviewed config/manual input |
| `Role` | RBAC subject and approval/context filter | Auth role enum, business role seed file/manual config |

### Not V1 Nodes

These are intentionally not graph labels in compact V1:

| Former Candidate | V1 Decision | Where It Goes |
|---|---|---|
| `Tenant` | Not a node | Database/runtime context |
| `MetricConcept` | Merge into `Metric` | `concept_key`, `concept_name`, `metric_base`, `aliases` |
| `Endpoint` | Merge into node fields | `card_endpoint_path`, `series_endpoint_path`, `query_endpoint_path`, `openapi_schema_refs` |
| `Connector` | Merge into `Metric` source fields | `connector_ids`, `source_platforms`, `source_set`, `mart_sources` |
| `Principal` / `Person` | Not a node | Auth maps session to `role_key`; KG stores only `Role` |
| `IntelligenceProduct` | Field, not node | `product_id` |
| `Domain` | Field, not node | `domain_id`, `department` |
| `Platform` | Field/vocabulary, not node | `source_platforms`, `connector_ids` |
| `DataAsset` | Field for now | `mart_sources` |
| `Formula` | Field for now | `formula_text`, `formula_status`, `formula_explanation` |
| `Dimension` | Field for now | `dimensions`, `segment_filters_json` |
| `DecisionCapsule` | Deferred V2 | Built after V1 graph context is reliable |
| `Thoughtlet` | Deferred V2 | Memory/capsule layer |
| `LearningCandidate` | Deferred V2 | Review/projection layer |
| `GraphVersion` | Deferred V2 | Needed when governed writes become active |

This is a deliberate simplification. Any merged field can be split into a first-class node later without changing the public idea of the graph.

---

## 4. Node Properties

### 4.1 `Metric`

`Metric` is the main merged node. It replaces separate `MetricConcept`, `Endpoint`, and `Connector` nodes for V1.

#### Responsibilities

A `Metric` node must answer:

- What business signal is this?
- What concept does it represent?
- Which product/domain owns it?
- Which dashboard/chart surfaces show it?
- Which endpoint paths fetch it?
- Which source platforms and connectors feed it?
- Which formula or explanation is known?
- Which policy and threshold govern it?
- Which role can see, edit, or approve it?
- Which upstream/downstream metrics are related?

#### Required Identity Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `metric_id` | string | yes | normalized path/registry/rare-seeds | Stable unique id inside tenant DB. Example: `google-shopping-roas`, `blended.revenue`. |
| `canonical_key` | string | yes | system normalization | Machine-safe lookup key. Should be unique. |
| `display_name` | string | yes | registry/manual/path | Human readable. Example: `Blended Revenue`. |
| `description` | string | recommended | chart registry/OpenAPI/manual | Agent-facing explanation. |
| `aliases` | string[] | recommended | registry/path/manual | Alternate slugs and names. |

#### Concept Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `concept_key` | string | yes | derived/manual | Former `MetricConcept.concept_id`. Example: `revenue`, `roas`, `orders`. |
| `concept_name` | string | recommended | derived/manual | Human name. Example: `Return on Ad Spend`. |
| `metric_base` | string | recommended | rare-seeds/path | Base field from rare-seeds. |
| `category` | enum | recommended | OpenAPI/manual | Use `MetricCategory` where possible. |

#### Ownership Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `product_id` | enum/string | yes | BC_2 route/product mapping | Example: `miq`, `ciq`, `piq`, `creative_iq`, `dc`. |
| `domain_id` | string | yes | path/manual | Example: `marketing`, `finance`, `customer`, `product`, `inventory`. |
| `department` | string | optional | rare-seeds/manual | Example: `Finance / Exec`, `Web / Growth`. |
| `scope` | string | recommended | rare-seeds/path | Example: `blended`, `web`, `google`, `meta`, `klaviyo`. |

#### Source Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `connector_ids` | string[] | recommended | connector overview/source_set | Example: `ga4`, `google_ads`, `meta_ads`, `klaviyo`, `magento`. |
| `source_platforms` | string[] | recommended | source_set/manual | External platforms. |
| `source_set` | string[] | recommended | rare-seeds | Raw imported source list. Preserve for traceability. |
| `mart_sources` | string[] | optional | rare-seeds/dbt | Warehouse lineage as strings in V1. |
| `source_confidence` | number | recommended | classifier/review | 0.0-1.0 confidence that lineage mapping is correct. |

#### API Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `card_endpoint_path` | string | recommended | OpenAPI/rare-seeds | Current/scalar/card value endpoint. |
| `series_endpoint_path` | string | recommended | OpenAPI/rare-seeds | Trend/time-series endpoint. |
| `metadata_endpoint_path` | string | optional | OpenAPI | Dashboard or metric metadata endpoint. |
| `openapi_schema_refs` | string[] | optional | OpenAPI | Response schemas used by endpoint family. |
| `endpoint_family_key` | string | optional | ingestion | Normalized path family key, not an `Endpoint` node. |

#### Formula Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `formula_text` | string | optional | chart registry/manual/dbt | Example: `revenue / ad_spend`. |
| `formula_explanation` | string | optional | chart registry/manual | Human explanation. |
| `formula_status` | enum | yes | classifier/review | `explicit`, `description_only`, `unknown`, `needs_review`. |
| `is_derived` | boolean | yes | formula/registry | True if computed from other metrics. |

#### Type Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `value_format` | enum | recommended | OpenAPI `ValueFormat` | `number`, `currency`, `percentage`, `decimal`. |
| `unit` | string | optional | OpenAPI/manual | Example: `USD`, `%`, `count`, `ratio`. |
| `granularity` | enum | recommended | OpenAPI `Granularity`/path | `daily`, `weekly`, `monthly`, `quarterly`. |
| `directionality` | enum | recommended | OpenAPI/manual | Use `ThresholdDirection`: `higher_is_better`, `lower_is_better`, `target_is_best`. |
| `causal_role` | enum/string | recommended | rare-seeds/manual | Example: `outcome`, `mediator`, `controllable`, `constraint`, `external`, `ml_output`. |
| `is_model_output` | boolean | yes | rare-seeds/path | True for ML output metrics. |

#### Governance Fields

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `threshold_summary` | json | optional | `Threshold` relationship | Denormalized read cache only. |
| `policy_summary` | json | optional | `Policy` relationship | Denormalized read cache only. |
| `sensitivity_level` | enum | yes | RBAC/manual | `public`, `internal`, `restricted`, `executive`. |
| `owner_role_key` | string | recommended | RBAC/manual | Role accountable for metric. |
| `status` | enum | yes | ingestion/review | `proposed`, `active`, `deprecated`, `blocked`. |
| `created_at` | datetime | yes | system | Creation timestamp. |
| `updated_at` | datetime | yes | system | Last update timestamp. |

#### Metric ID Guidance

Use a stable internal id, not the raw OpenAPI path.

Recommended:

```text
google-shopping-roas
google-search-spend
blended.revenue
weekly-exec.roas
customer-overview.total_customers
```

Keep raw paths as fields:

```text
card_endpoint_path = /api/v1/google-shopping/metrics/{metric_id}
series_endpoint_path = /api/v1/google-shopping/charts/{chart_id}
```

This lets agents answer both:

- "What is this metric?"
- "Which endpoint serves it?"

### 4.2 `Dashboard`

`Dashboard` groups components and acts as a role-visible product/domain surface.

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `dashboard_id` | string | yes | chart registry/OpenAPI route | Example: `ceo-pulse`, `weekly-exec`, `google-shopping`. |
| `display_name` | string | yes | route/registry/manual | Human name. |
| `product_id` | enum/string | yes | BC_2 route mapping/manual | Example: `miq`, `ciq`, `piq`, `creative_iq`, `dc`. |
| `domain_id` | string | recommended | path/manual | Business domain. |
| `route_path` | string | optional | frontend route map | App route if known. |
| `default_endpoint_path` | string | optional | OpenAPI | Dashboard data endpoint or metadata endpoint. |
| `metadata_endpoint_path` | string | optional | OpenAPI | Example: `/api/v1/ceo-pulse/metadata`. |
| `description` | string | optional | manual/metadata | Dashboard purpose. |
| `sensitivity_level` | enum | yes | RBAC/manual | Default surface sensitivity. |
| `status` | enum | yes | ingestion/review | `active`, `hidden`, `deprecated`, `proposed`. |
| `created_at` | datetime | yes | system | Creation timestamp. |
| `updated_at` | datetime | yes | system | Last update timestamp. |

### 4.3 `UIComponent`

`UIComponent` maps directly to chart/card/table/narrative surfaces.

The chart registry is the strongest source for this node.

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `component_id` | string | yes | registry/system | Usually same as `canonical_id`. |
| `canonical_id` | string | yes | chart registry | Format: `dashboard_id:chart_id`. |
| `dashboard_id` | string | yes | chart registry | Parent dashboard id. |
| `chart_id` | string | yes | chart registry | Chart id within dashboard. |
| `id` | string | yes | chart registry | Registry local id. |
| `title` | string | yes | chart registry | Rendered title. |
| `component_type` | enum | recommended | OpenAPI/manual | Use `ChartType` where possible. |
| `formula` | string | yes | chart registry | Registry formula text. |
| `formula_explanation` | string | yes | chart registry | Explanation for agents/users. |
| `how_to_read` | string[] | yes | chart registry | Guidance bullets. |
| `decisions_answered` | string[] | yes | chart registry | Questions this chart answers. |
| `audio_file` | string | yes | chart registry | Narration/audio asset path. |
| `narration_text` | string | optional | chart registry | Present on 558 entries. |
| `metric_keys` | string[] | recommended | classifier/manual | Metrics visualized. Source of truth is `VISUALIZES` edge. |
| `query_endpoint_path` | string | optional | OpenAPI/classifier | GET endpoint for chart/component data. |
| `openapi_schema_refs` | string[] | optional | OpenAPI | Response schemas. |
| `visual_encoding_json` | json | optional | OpenAPI/frontend | Axes, series, sort, aggregation. |
| `sensitivity_level` | enum | yes | RBAC/manual | Can be stricter than parent dashboard. |
| `status` | enum | yes | registry/review | `active`, `hidden`, `deprecated`, `proposed`. |

### 4.4 `Policy`

`Policy` aligns with the OpenAPI `PolicyCreate` shape and adds `owner_role_key`.

Policy explains what should happen when a metric condition is met. It is distinct from a threshold. Threshold stores boundaries; policy stores evaluation and response behavior.

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `policy_id` | string | recommended | OpenAPI/manual/system | Optional in `PolicyCreate`, but required for KG stability. |
| `policy_name` | string | yes | OpenAPI/manual | Required by `PolicyCreate`. |
| `description` | string | optional | OpenAPI/manual | Human explanation. |
| `metric_id` | string | yes | OpenAPI/manual | Governed metric id. Source of truth also edge. |
| `condition_type` | enum | yes | OpenAPI `ConditionType` | `threshold`, `anomaly`, `trend`, `missing_data`. |
| `condition_operator` | enum/null | optional | OpenAPI `ConditionOperator` | `lt`, `lte`, `gt`, `gte`, `eq`, `neq`, `between`, `outside`. |
| `condition_value` | number/null | optional | OpenAPI | Low/single condition value. |
| `condition_value_high` | number/null | optional | OpenAPI | High value for ranges. |
| `condition_expression` | string/null | optional | OpenAPI | Complex condition expression. |
| `evaluation_window` | string | yes | OpenAPI | Default from API: `24h`. |
| `evaluation_frequency` | string | yes | OpenAPI | Default from API: `1h`. |
| `cooldown_hours` | number | yes | OpenAPI | Default from API: `4`. |
| `severity` | enum | yes | OpenAPI `Severity` | `critical`, `high`, `medium`, `low`, `info`. |
| `auto_investigate` | boolean | yes | OpenAPI | Default true. |
| `notify_channels` | string[] | recommended | OpenAPI | Notification channels. |
| `escalate_after_hours` | number | yes | OpenAPI | Default from API: `24`. |
| `owner_role_key` | string | recommended | RBAC/manual | Accountable role. |
| `is_active` | boolean | yes | OpenAPI | Whether policy is active. |
| `status` | enum | recommended | system | `draft`, `active`, `retired`, `superseded`. |
| `created_at` | datetime | yes | system | Creation timestamp. |
| `updated_at` | datetime | yes | system | Last update timestamp. |

### 4.5 `Threshold`

`Threshold` aligns with OpenAPI `ThresholdConfig` while also storing normalized numeric fields for agent/tool evaluation.

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `threshold_id` | string | yes | OpenAPI/manual/system | Stable id. |
| `metric_id` | string | recommended | OpenAPI/manual | Governed metric id. Source of truth also edge. |
| `metric_name` | string | yes | OpenAPI `ThresholdConfig` | Raw API field. |
| `green_value` | string | yes | OpenAPI `ThresholdConfig` | Raw string from API/config. |
| `yellow_value` | string | yes | OpenAPI `ThresholdConfig` | Raw string from API/config. |
| `red_value` | string | yes | OpenAPI `ThresholdConfig` | Raw string from API/config. |
| `warning_value_num` | number/null | recommended | parser/review | Normalized yellow/warning boundary. |
| `critical_value_num` | number/null | recommended | parser/review | Normalized red/critical boundary. |
| `target_value_num` | number/null | optional | parser/review | Target boundary when applicable. |
| `operator` | enum | recommended | OpenAPI `ComparisonOperator` | `lt`, `lte`, `gt`, `gte`, `eq`, `neq`, `between`, `outside`. |
| `threshold_type` | enum | recommended | OpenAPI `ThresholdType` | `static`, `percentile`, `seasonal`. |
| `direction` | enum/string | yes | OpenAPI/raw config | Prefer `ThresholdDirection`. |
| `category` | string | yes | OpenAPI `ThresholdConfig` | Threshold category/group. |
| `evaluation_window` | string | optional | policy/manual | Example: `24h`, `7d`, `last_4_weeks`. |
| `owner_role_key` | string | recommended | RBAC/manual | Role accountable for threshold. |
| `status` | enum | yes | system/manual | `draft`, `active`, `retired`, `superseded`. |
| `created_at` | datetime | yes | system | Creation timestamp. |
| `updated_at` | datetime | yes | system | Last update timestamp. |

### 4.6 `Role`

`Role` is the only RBAC subject stored in V1 Neo4j. Do not create `Principal` or `Person` nodes for V1.

The application authenticates users. The app passes one or more `role_key` values into graph queries. The graph stores permissions for those roles.

| Property | Type | Required | Source | Notes |
|---|---|---:|---|---|
| `role_key` | string | yes | auth/business config | Stable unique role id. |
| `display_name` | string | yes | business config | Human readable. |
| `role_source` | enum/string | yes | system | `openapi_user_role`, `business_seed`, `manual`, `external_auth`. |
| `auth_role` | enum/string | optional | OpenAPI `UserRole` | `super_admin`, `agency_admin`, `tenant_admin`, `analyst`, `viewer`, `admin`, `user`. |
| `business_function` | string | recommended | manual | Example: `marketing`, `finance`, `customer`, `product`, `executive`. |
| `seniority_rank` | integer | recommended | manual | Higher rank means broader approval/escalation authority. |
| `default_product_ids` | string[] | optional | manual | Convenience cache only; permission edges are source of truth. |
| `default_domain_ids` | string[] | optional | manual | Convenience cache only. |
| `max_sensitivity_level` | enum | yes | security policy | `public`, `internal`, `restricted`, `executive`. |
| `can_manage_rbac` | boolean | yes | security policy | Can manage permission edges. |
| `can_create_policy` | boolean | yes | security policy | Global capability before scoped edge check. |
| `can_create_threshold` | boolean | yes | security policy | Global capability before scoped edge check. |
| `redaction_policy_json` | json | optional | security policy | Field masking defaults. |
| `status` | enum | yes | auth/manual | `active`, `disabled`, `deprecated`. |
| `created_at` | datetime | yes | system | Creation timestamp. |
| `updated_at` | datetime | yes | system | Last update timestamp. |

---

## 5. Controlled Vocabularies And Types

Use exact live OpenAPI enum values where the API provides them.

### `ChartType`

```text
line
area
bar
horizontal_bar
grouped_bar
pie
donut
sankey
heatmap
table
sparkline
scatter
treemap
gauge
funnel
```

### `ValueFormat`

```text
number
currency
percentage
decimal
```

### `Granularity`

```text
daily
weekly
monthly
quarterly
```

### `ThresholdType`

```text
static
percentile
seasonal
```

Do not document `dynamic` or `model_based` as live V1 values until the API exposes them.

### `ThresholdDirection`

```text
higher_is_better
lower_is_better
target_is_best
```

### `ConditionType`

```text
threshold
anomaly
trend
missing_data
```

### `ConditionOperator`

```text
lt
lte
gt
gte
eq
neq
between
outside
```

### `MetricCategory`

```text
advertising
revenue
traffic
email
customer
sms
google_ads
meta_ads
efficiency
comparison
financial
marketing
product
operational
```

### `MetricSource`

```text
ga4
google_ads
meta_ads
klaviyo
```

Magento and LinkedIn can still appear in `source_set` or `connector_ids`, but they are not in the current OpenAPI `MetricSource` enum.

### `UserRole`

```text
super_admin
agency_admin
tenant_admin
analyst
viewer
admin
user
```

Business roles such as `ceo`, `cfo`, `cmo`, `marketing_manager`, and `product_manager` can be stored as `role_key` values. Map them to `auth_role` only when the auth layer exposes one of the OpenAPI enum values.

---

## 6. V1 Edges

Only implement these V1 edge types.

### Surface Edges

| Edge | From -> To | Purpose | Key Properties |
|---|---|---|---|
| `CONTAINS_COMPONENT` | `Dashboard -> UIComponent` | Dashboard composition | `section_id`, `order`, `visibility`, `source`, `confidence` |
| `VISUALIZES` | `UIComponent -> Metric` | Component shows metric | `match_type`, `axis_role`, `confidence`, `source` |

### Governance Edges

| Edge | From -> To | Purpose | Key Properties |
|---|---|---|---|
| `HAS_THRESHOLD` | `Metric -> Threshold` | Metric boundary | `is_default`, `segment_context`, `priority`, `confidence` |
| `GOVERNED_BY` | `Metric -> Policy` | Policy applies to metric | `priority`, `effective_from`, `effective_to`, `status` |
| `EXPLAINS_THRESHOLD` | `Policy -> Threshold` | Policy explains threshold intent | `explanation_type`, `confidence` |
| `OWNS` | `Role -> Metric|Policy|Threshold|Dashboard` | Accountable owner | `ownership_type`, `priority`, `source` |

### Causal / Associative Edges

| Edge | From -> To | Purpose | Key Properties |
|---|---|---|---|
| `INFLUENCES` | `Metric -> Metric` | Weak driver/candidate relationship | `confidence`, `evidence_mass`, `lag_min_hours`, `lag_max_hours`, `mechanism`, `review_state` |
| `CAUSES` | `Metric -> Metric` | Approved evidence-backed causal relationship | `confidence`, `evidence_mass`, `lag_min_hours`, `lag_max_hours`, `mechanism`, `review_state` |
| `CORRELATES_WITH` | `Metric -> Metric` | Statistical association only | `correlation`, `p_value`, `lag_hours`, `sample_size`, `source` |

Do not auto-promote a correlation into causality. Imported rare-seeds correlation edges should enter as `CORRELATES_WITH` or low-confidence `INFLUENCES`, not `CAUSES`.

### RBAC Edges

| Edge | From -> To | Purpose |
|---|---|---|
| `INHERITS_FROM` | `Role -> Role` | Permission inheritance |
| `CAN_VIEW_METRIC` | `Role -> Metric` | Metric visibility and traversal |
| `CAN_VIEW_DASHBOARD` | `Role -> Dashboard` | Dashboard visibility |
| `CAN_VIEW_COMPONENT` | `Role -> UIComponent` | Component visibility |
| `CAN_EDIT_POLICY` | `Role -> Policy` | Policy edit permission |
| `CAN_EDIT_THRESHOLD` | `Role -> Threshold` | Threshold edit permission |
| `CAN_APPROVE_CHANGE` | `Role -> Policy|Threshold` | Approval authority |

#### Permission Edge Properties

All RBAC edges should support this common property shape:

| Property | Type | Purpose |
|---|---|---|
| `effect` | enum | `allow` or `deny`. Explicit deny wins. |
| `permission` | string | `view`, `explain`, `traverse`, `edit`, `approve`, `manage`. |
| `priority` | integer | Higher priority wins when grants conflict. |
| `allowed_fields` | string[] | Fields the role may see. |
| `masked_fields` | string[] | Fields always redacted. |
| `product_scope_ids` | string[] | Product scoping without product nodes. |
| `domain_scope_ids` | string[] | Domain scoping without domain nodes. |
| `max_grain` | enum/string | Restrict detail level, such as weekly instead of daily. |
| `row_filter_json` | json | Segment filters such as platform, region, SKU, campaign group. |
| `condition_json` | json | Conditional grants, time windows, environment checks. |
| `valid_from` | datetime | Permission start. |
| `valid_to` | datetime/null | Permission end. |
| `approval_required` | boolean | Whether requested edit needs approval. |
| `source` | string | `manual_admin`, `org_sync`, `migration`, `policy_import`. |

---

## 7. RBAC Before Context

The application must never let an agent run unrestricted graph traversal and filter afterward.

Correct sequence:

1. Authenticate user/service outside Neo4j.
2. Resolve one or more `role_key` values.
3. Query Role permissions and inherited roles.
4. Build the allowed graph neighborhood.
5. Apply field masking and row/domain/product filters.
6. Send only the allowed context to the agent.

If a causal path crosses a restricted metric, the agent may say a restricted dependency exists, but it must not reveal the hidden node's name, value, endpoint, chart, or policy.

### Role Closure Query

```cypher
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH path = (r)-[:INHERITS_FROM*0..4]->(parent:Role {status: 'active'})
RETURN collect(DISTINCT r) + collect(DISTINCT parent) AS role_scope;
```

### Allowed Metric Query

```cypher
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH (r)-[grant:CAN_VIEW_METRIC]->(m:Metric {status: 'active'})
WHERE grant.effect = 'allow'
RETURN DISTINCT
  m.metric_id,
  m.display_name,
  m.product_id,
  m.domain_id,
  m.sensitivity_level,
  grant.allowed_fields,
  grant.masked_fields;
```

### Product/Domain Scoped Metric Query

Because product and domain are fields in compact V1, scoped permissions use edge properties.

```cypher
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (r)-[grant:CAN_VIEW_METRIC]->(scope:Metric)
WHERE grant.effect = 'allow'
WITH r, collect(DISTINCT grant.product_scope_ids) AS product_scopes,
        collect(DISTINCT grant.domain_scope_ids) AS domain_scopes
MATCH (m:Metric {status: 'active'})
WHERE m.product_id IN apoc.coll.flatten(product_scopes)
   OR m.domain_id IN apoc.coll.flatten(domain_scopes)
RETURN DISTINCT m;
```

If APOC is not available, flatten scope ids in the application before passing query parameters.

### Agent Context Query For One Metric

```cypher
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (m:Metric {metric_id: $metric_id, status: 'active'})
WHERE EXISTS {
  MATCH (r)-[grant:CAN_VIEW_METRIC]->(m)
  WHERE grant.effect = 'allow'
}
OPTIONAL MATCH (c:UIComponent)-[:VISUALIZES]->(m)
OPTIONAL MATCH (d:Dashboard)-[:CONTAINS_COMPONENT]->(c)
OPTIONAL MATCH (m)-[:HAS_THRESHOLD]->(t:Threshold {status: 'active'})
OPTIONAL MATCH (m)-[:GOVERNED_BY]->(p:Policy)
OPTIONAL MATCH (up:Metric)-[:INFLUENCES|CAUSES|CORRELATES_WITH]->(m)
OPTIONAL MATCH (m)-[:INFLUENCES|CAUSES|CORRELATES_WITH]->(down:Metric)
RETURN
  m,
  collect(DISTINCT c) AS components,
  collect(DISTINCT d) AS dashboards,
  collect(DISTINCT t) AS thresholds,
  collect(DISTINCT p) AS policies,
  collect(DISTINCT up.metric_id) AS upstream_candidates,
  collect(DISTINCT down.metric_id) AS downstream_candidates;
```

### Safe Threshold Edit Check

```cypher
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (t:Threshold {threshold_id: $threshold_id, status: 'active'})
MATCH (r)-[grant:CAN_EDIT_THRESHOLD]->(t)
WHERE grant.effect = 'allow'
RETURN t.threshold_id, grant.approval_required AS approval_required;
```

---

## 8. Endpoint Harvesting Policy

Endpoints are not V1 graph nodes. They become fields on `Metric`, `Dashboard`, and `UIComponent`.

### Promote Into Fields

| OpenAPI Pattern | V1 Field Target | Notes |
|---|---|---|
| `GET /api/v1/{dashboard}/metrics/{metric_id}` | `Metric.card_endpoint_path` | Current/card value |
| `GET /api/v1/{dashboard}/metrics/` | dashboard metric inventory metadata | Use for discovery/review |
| `GET /api/v1/{dashboard}/charts/{chart_id}` | `UIComponent.query_endpoint_path` and possibly `Metric.series_endpoint_path` | Chart/time-series data |
| `GET /api/v1/{dashboard}/charts/` | component inventory metadata | Use for discovery/review |
| `GET /api/v1/{dashboard}/metadata` | `Dashboard.metadata_endpoint_path` | Surface metadata |
| `GET /api/v1/{dashboard}/` | `Dashboard.default_endpoint_path` | Dashboard-level payload |

### Exclude From Metric Discovery

| Pattern | Decision |
|---|---|
| `auth`, `login`, `logout`, `token` | Exclude |
| `admin`, `settings`, `billing`, user management | Exclude |
| `health`, `ready`, `status` | Exclude |
| `docs`, `redoc`, `openapi` | Exclude as business facts; can be API-review metadata |
| `master-config`, `master` | Exclude from metric discovery; may inform reviewed policy/threshold config |
| `POST`, `PUT`, `PATCH`, `DELETE` | Exclude from V1 harvest; future governed tools/actions |

### Ingestion Output

The ingestion job should emit proposals, not write arbitrary truth:

```json
{
  "node_label": "Metric",
  "metric_id": "weekly-exec.roas",
  "product_id": "miq",
  "domain_id": "marketing",
  "card_endpoint_path": "/api/v1/weekly-exec/metrics/roas",
  "series_endpoint_path": "/api/v1/weekly-exec/charts/weekly-trend",
  "source_confidence": 0.82,
  "status": "proposed"
}
```

Human or deterministic arbitration promotes proposals to active nodes.

---

## 9. Chart Registry Mapping

Every chart registry entry becomes or updates one `UIComponent`.

Example source:

```json
{
  "id": "active_rules",
  "title": "Active Rules",
  "formula": "Active Rules = COUNT(AlertRules WHERE status = \"enabled\")",
  "formula_explanation": "...",
  "how_to_read": ["..."],
  "decisions_answered": ["..."],
  "audio_file": "/audio/alerts-config/active-rules.mp3",
  "narration_text": "...",
  "dashboard_id": "alerts-config",
  "chart_id": "active_rules",
  "canonical_id": "alerts-config:active_rules"
}
```

V1 target:

```text
(:UIComponent {
  component_id: "alerts-config:active_rules",
  canonical_id: "alerts-config:active_rules",
  dashboard_id: "alerts-config",
  chart_id: "active_rules",
  title: "Active Rules",
  formula: "...",
  formula_explanation: "...",
  how_to_read: [...],
  decisions_answered: [...],
  audio_file: "...",
  narration_text: "...",
  status: "active"
})
```

The component links to metrics only after classifier/manual resolution:

```cypher
(:Dashboard {dashboard_id:"alerts-config"})
  -[:CONTAINS_COMPONENT]->
(:UIComponent {component_id:"alerts-config:active_rules"})
  -[:VISUALIZES {match_type:"formula_parse", confidence:0.78}]->
(:Metric {metric_id:"alerts-config.active_rules"})
```

---

## 10. Causal Edge Discipline

V1 success is not "number of nodes imported." V1 success is "number of useful, reviewed metric relationships."

Edge tiers:

| Tier | Edge | Meaning |
|---|---|---|
| Low | `CORRELATES_WITH` | Statistical association only |
| Medium | `INFLUENCES` | Plausible driver relationship with mechanism/evidence |
| High | `CAUSES` | Approved causal relationship with evidence and lag |

Required causal/association edge properties:

| Property | Type | Purpose |
|---|---|---|
| `confidence` | number | 0.0-1.0 confidence in relationship. |
| `evidence_mass` | number | Amount/weight of evidence. |
| `lag_min_hours` | number/null | Minimum plausible lag. |
| `lag_max_hours` | number/null | Maximum plausible lag. |
| `mechanism` | string | Plain-language explanation. |
| `source` | string | `manual`, `registry`, `rare_seeds`, `analysis`, `experiment`. |
| `review_state` | enum | `proposed`, `reviewed`, `approved`, `rejected`. |

Use temporal interpretation:

```text
source_metric at time T affects target_metric at time T + lag
```

Static graph cycles are allowed if the time-expanded view is acyclic. Example:

```text
ad_spend -> traffic -> revenue -> budget -> ad_spend
```

This is a real business loop, not an error, as long as each edge has a forward lag.

---

## 11. Neo4j Constraints And Indexes

V1 constraints:

```cypher
CREATE CONSTRAINT metric_id IF NOT EXISTS
FOR (n:Metric) REQUIRE n.metric_id IS UNIQUE;

CREATE CONSTRAINT dashboard_id IF NOT EXISTS
FOR (n:Dashboard) REQUIRE n.dashboard_id IS UNIQUE;

CREATE CONSTRAINT component_id IF NOT EXISTS
FOR (n:UIComponent) REQUIRE n.component_id IS UNIQUE;

CREATE CONSTRAINT policy_id IF NOT EXISTS
FOR (n:Policy) REQUIRE n.policy_id IS UNIQUE;

CREATE CONSTRAINT threshold_id IF NOT EXISTS
FOR (n:Threshold) REQUIRE n.threshold_id IS UNIQUE;

CREATE CONSTRAINT role_key IF NOT EXISTS
FOR (n:Role) REQUIRE n.role_key IS UNIQUE;
```

Recommended indexes:

```cypher
CREATE INDEX metric_product_domain IF NOT EXISTS
FOR (n:Metric) ON (n.product_id, n.domain_id);

CREATE INDEX metric_concept_scope IF NOT EXISTS
FOR (n:Metric) ON (n.concept_key, n.scope);

CREATE INDEX metric_source IF NOT EXISTS
FOR (n:Metric) ON (n.source_set);

CREATE INDEX dashboard_product IF NOT EXISTS
FOR (n:Dashboard) ON (n.product_id, n.domain_id);

CREATE INDEX component_dashboard IF NOT EXISTS
FOR (n:UIComponent) ON (n.dashboard_id);

CREATE INDEX policy_metric IF NOT EXISTS
FOR (n:Policy) ON (n.metric_id, n.is_active);

CREATE INDEX role_auth IF NOT EXISTS
FOR (n:Role) ON (n.auth_role, n.status);
```

Do not add constraints for `Tenant`, `MetricConcept`, `Endpoint`, `Connector`, `Principal`, `Person`, `IntelligenceProduct`, or `Domain` in V1 because they are not labels.

---

## 12. Example Agent Answer

Question:

```text
What do you know about blended revenue?
```

Role:

```text
cmo
```

Allowed answer shape:

```text
blended.revenue is a Marketing IQ metric in the finance/executive domain.

Concept:
- concept_key: revenue
- metric_base: revenue
- scope: blended
- value_format: currency
- granularity: daily or weekly depending on surface

Source:
- source_set: ga4, google_ads, linkedin_ads, meta_ads
- mart_sources: mart_annual_planning_summary, mart_ceo_daily_pulse

Endpoints:
- card_endpoint_path: /api/v1/ceo-pulse/metrics/revenue
- series_endpoint_path: /api/v1/annual-planning/charts/channel_revenue

Surfaces:
- dashboard: ceo-pulse
- dashboard: annual-planning
- UI components are available through VISUALIZES edges.

Governance:
- thresholds and policies should be read from HAS_THRESHOLD and GOVERNED_BY edges.

Causal context:
- upstream candidates may include sessions, conversion rate, AOV, marketing spend.
- downstream candidates may include margin, CAC, forecast variance.

Access:
- this answer is filtered by role_key=cmo. Restricted finance-only fields are masked.
```

---

## 13. V2 Split Points

The compact model is intentionally reversible.

Split merged fields into nodes only when the operational need is real:

| Split Candidate | When To Promote To Node |
|---|---|
| Metric concept fields -> `MetricConcept` | Same concept spans many scopes and needs semantic governance. |
| Endpoint fields -> `Endpoint` | Endpoint lifecycle, schema drift, owners, and versioning become important. |
| Connector fields -> `Connector` | Connector status/freshness/SLA needs graph traversal and alerting. |
| Product fields -> `IntelligenceProduct` | Product-level entitlements become too complex for edge properties. |
| Domain fields -> `Domain` | Domain hierarchy and ownership become central to RBAC. |
| Auth role fields -> `Person`/`Principal` | Real user-to-role assignment and audit must live in graph. |
| Causal edge props -> `CausalRelation`/`EvidenceEvent` | Evidence ledger becomes large enough to query independently. |
| Agent outputs -> `DecisionCapsule` | Decisions need durable monitoring, approval, and retrospectives. |
| Observations -> `Thoughtlet` | Reusable atomic memory becomes necessary. |
| Change proposals -> `GraphVersion` | Approved graph edits require version snapshots. |

Until those conditions occur, keep V1 small.

---

## 14. Implementation Checklist

Build order:

1. Create uniqueness constraints for six labels.
2. Load `Dashboard` ids from chart registry and OpenAPI dashboard paths.
3. Load `UIComponent` nodes from all 646 chart registry entries.
4. Create proposed `Metric` nodes from OpenAPI metric paths and rare-seeds metric inventory.
5. Resolve `UIComponent -VISUALIZES-> Metric` links by registry formula/title/path matching, then review uncertain matches.
6. Load `Policy` and `Threshold` nodes from reviewed config/manual seeds.
7. Seed `Role` nodes and permission edges.
8. Enforce RBAC-before-context in every agent read path.
9. Add `CORRELATES_WITH` / `INFLUENCES` / `CAUSES` edges only through reviewed evidence.
10. Defer memory/capsule/versioning nodes until the V1 context graph is trusted.

Acceptance criteria:

- No V1 graph label outside the six-node set.
- Every agent answer is role-filtered before context leaves Neo4j/application.
- Every `UIComponent` has a source registry id.
- Every `Metric` has product/domain/source/API fields where discoverable.
- Every policy and threshold points to a metric through an edge.
- Causal edges expose confidence, evidence mass, and lag.
- Control-plane endpoints do not become business graph nodes.

