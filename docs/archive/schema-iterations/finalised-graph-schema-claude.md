# ThoughtWire Causal Knowledge Graph — Finalised V1 Schema (Claude)

Date: 2026-06-14 · DB target: **Neo4j** · One database **per tenant** · Scope: **lean V1**

> **What this file is.** The finalised, shippable V1 schema for ThoughtWire's "business body" — the causal knowledge graph an agent wakes into. It is a **merge of the two prior drafts**: the comprehensive `thoughtwire-kg-schema-claude.md` (used as the backbone) and the compact `thoughtwire-kg-v1-node-details-codex.html` (whose OpenAPI-grounded governance fields and RBAC clarity were folded in). All counts and enums below are re-verified against the live `openapi.json`, `chart-registry.json`, and the `rare_seeds` profile.
>
> Companion: `finalised-graph-schema-claude.html` — same schema, visual blueprint.

---

## 0. Provenance, verdict, and what changed

### Which prior draft was better suited?

**The Claude blueprint is the better-suited foundation.** It is the only draft that models ThoughtWire's actual differentiator described in the FRD — the **decision → monitor → learn loop** running on a **governed causal evidence ledger** (FRD Layers 2–6: Thoughtlets, Decision Capsules, Monitoring Contracts, Approval Court, Learning/Governance, Context-Injection Harness). The Codex draft is a clean **Layer-1-only** operational graph; on its own it cannot satisfy `FR-DC`, `FR-MC`, `FR-LRN`, `FR-HAR`, `FR-AGT`, or `FR-SCORE`.

**But the Codex draft was better in two specific places, and both are merged in here:**
1. **OpenAPI-grounded governance** — its `Policy` / `Threshold` fields map directly to the real API schemas (`PolicyCreate`, `ThresholdConfig`, `StatisticalThreshold`, `ConditionType`, `ConditionOperator`/`ComparisonOperator`, `Severity`). The Claude draft's governance fields were more generic.
2. **RBAC clarity** — explicit per-action edge intent and product/domain scope props on permission edges.

### What this finalised V1 deliberately leaves out (deferred, not specified)

This is a **lean V1**: the eight core nodes below are enough to ship. The FRD's memory/learning machinery and the entities **both** drafts omit are **named in §12 as a deferred pointer list only** — no property tables, on purpose. Deferred: `Thoughtlet`, `DecisionCapsule`, `MonitoringContract`/`WakeCondition`, `EvidenceEvent`/`CausalRelation`, `LearningCandidate`/`PromotedMemory`, `GraphChangeProposal`, `GraphVersion`, `SourceProfile`/`Endpoint_Family`, and the FRD Layer-1 entities `Outcome`, `Tool`/`Action`, `Investigation_Rule`, `Approval_Rule`.

### Changes applied during the merge (traceable)

| # | Change | Source | Why |
|---|---|---|---|
| 1 | `Policy` rebuilt on OpenAPI `PolicyCreate` | Codex grounding | uses the real condition model the API already exposes |
| 2 | `Threshold` carries raw **and** normalized bands + `StatisticalThreshold` numerics | Codex + OpenAPI | matches `ThresholdConfig` (string bands) and `StatisticalThreshold` (2σ numerics) |
| 3 | RBAC edges gain optional `product_scope_ids[]`/`domain_scope_ids[]` | Codex | scope without extra nodes |
| 4 | **Data fix:** `narration_text` is on **558/646** components (86.4%), not "all 646" | re-verified | the other 10 registry fields are 100% |
| 5 | **Causal layer built from scratch** — no legacy relationship catalog imported | from-scratch KG decision | every edge is earned from evidence, never inherited from the old ontology |
| 6 | All memory/learning content moved out of the body into the §12 deferred list | scope decision | keep V1 shippable; don't pollute causal truth before the causal layer is solid |

---

## 1. Verdict, provenance, and the V1 stance

The core idea is right: ThoughtWire needs a causal knowledge graph as the stable "business body," and Neo4j is justified (causal traversal, versioning, role-scoped context, provenance, governed learning). Three things hold from day one:

1. **It is not a tree, and it is not one flat graph.** A **tree-like navigation spine** (`IntelligenceProduct → Domain → Metric`) for orientation; the causal model is a **temporal DAG** with feedback loops resolved by lag; governance/RBAC ride as **overlays** — connected, never melted in.
2. **The hard part is edges, not nodes.** `rare_seeds` has **355 richly-typed nodes and only 4 edges** — and those 4 are correlation, one spurious. Node extraction is solved. **Building trustworthy causal edges is the entire project.**
3. **Confidence is derived, never typed in.** In V1 the causal edges carry a simple `confidence`; the append-only **evidence ledger** that *derives* confidence is V2 (§12). The graph is written through **governed proposals** in V2; in V1 the inventory and RBAC are seeded deterministically.

### Per-tenant database — no `Tenant` node

**Each tenant gets its own Neo4j database.** Tenant identity is therefore **database/runtime context**, not a business entity. The app selects the tenant database before querying; there is **no `Tenant` node** and **no `tenant_id` field** on any node. This matches how data is already isolated downstream — per-tenant Snowflake `USR_<SLUG>` / `RL_<SLUG>` / `WH_<SLUG>` and per-tenant databases (e.g. `DB_RARE_SEEDS`). If multi-tenant-in-one-database is ever forced, reintroduce `Tenant` with strict composite constraints.

### The five V1 merges (all validated against the data)

| Change | Why it's safe | Evidence |
|---|---|---|
| **Drop `Tenant`** | Tenant = DB/runtime context with per-tenant DBs | per-tenant Snowflake `USR_/RL_/WH_<SLUG>`, `DB_RARE_SEEDS` |
| **`MetricConcept` → `Metric`** fields | No separate concept entity exists; concept ≈ `metric_base` + canonical name | grouping recovered via `concept_key` + `ROLLS_UP_TO` |
| **`Endpoint` + `Connector` → `Metric`** fields | `rare_seeds` already stores them flat on the node | `card_endpoint`, `series_endpoint`, `source`, `source_set`, `mart_source`, `source_dashboards` |
| **`Principal` → `Role`** | No "principal" in the data — only `User {role}` | app authenticates the user; graph needs only the **Role** (carrying a `role_key` claim) |
| **Type audit** | Real `rare_seeds` values disagree with earlier declared types | `type_confidence` is categorical; booleans are `yes`/`no`; lists are `\|`-delimited (see §7) |

**Provenance checked:** live API `localhost:8005/openapi.json` (902 paths · 877 GET); checked-in `docs/frd-docs/openapi.json` (structurally identical, pretty-printed — not a byte-for-byte hash match); `docs/frd-docs/chart-registry.json` (646 entries; 10 fields at 100%, `narration_text` at 558/646); 5 connectors (GA4, Google Ads, Klaviyo, Magento, Meta Ads); products `miq·ciq·piq·dc·creative_iq`; `rare_seeds` graph (355 nodes, 4 correlation edges).

---

## 2. V1 graph shape

Each tenant database contains only that tenant's product, metric, dashboard, policy, threshold, and RBAC graph. **MetricConcept, Endpoint, and Connector are fields on `Metric`** (not nodes); **Tenant and Principal do not exist.**

```text
        NAVIGATION SPINE (tree — orientation)
        IntelligenceProduct ─▶ Domain ─▶ Metric
              (miq/ciq/piq/dc/creative_iq)   (google-shopping ROAS)
                                                 │
        ─────────────────────────────────────────┼──────────────────────
        CAUSAL FABRIC (temporal DAG — the truth agents traverse)
            bid_strategy ─CAUSES(lag)▶ cpc ─CAUSES(lag)▶ roas ◀DECOMPOSES─ revenue, spend

        GOVERNANCE OVERLAY (rides on the spine)
            Metric ─HAS_THRESHOLD▶ Threshold      Metric ─GOVERNED_BY▶ Policy ─ENFORCES_THRESHOLD▶ Threshold
            Dashboard ─RENDERS▶ UIComponent ─VISUALIZES▶ Metric

        RBAC OVERLAY (role-first, filter before context)
            Role ─CAN_ACCESS_PRODUCT/CAN_VIEW/CAN_EDIT/CAN_APPROVE▶ (product/domain/metric/policy/threshold)
```

### The five questions the graph must answer for any node
1. **Upstream** — what influences this? → `CAUSES`/`INFLUENCES` in-edges
2. **Downstream blast radius** — what does this influence? → out-edges with lag
3. **Policy & threshold** — what governs it, what's the breach line? → `GOVERNED_BY`, `HAS_THRESHOLD`
4. **Confidence** — how strong is each relationship? → edge `confidence` (V1); derived from evidence ledger (V2)
5. **Action eligibility** — what can be done, by whom? → Role RBAC (Tool/Action in V2)

---

## 3. The merged `Metric` node

`Metric` is the hub. Identity uses the three-ID strategy (`metric_uid` / `canonical_id` / `metric_id`). After the merges it also carries the concept, endpoint, and source/lineage data that used to live on separate nodes. **Flat, indexable fields** throughout; JSON only for cached summaries. `★` marks a **corrected type** (see §7).

> **No `tenant_id`** on any node — the database is the tenant boundary.

### Identity & semantics — *MetricConcept folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `metric_uid` | string | yes | app `metric:<scope>:<base>` | Neo4j identity, e.g. `metric:google-shopping:roas` |
| `canonical_id` | string | yes | catalog | cross-source business id, `google-shopping-roas` |
| `metric_id` | string | yes | rare_seeds `node_id` / path slug | API/local slug, `roas` |
| `concept_key` | string | rec | rare_seeds `metric_base` | semantic concept group (`roas`) — **replaces MetricConcept** |
| `concept_name` | string | rec | catalog/manual | "Return on Ad Spend" |
| `synonyms[]` | string[] | opt | catalog | resolution aliases |
| `unit_family` | enum | rec | manual | `currency·ratio·percent·count·duration·score` |
| `default_direction` | enum | rec | manual | `higher_is_better·lower_is_better·target_is_best` |

### Classification
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `product_id` | string | yes | rare_seeds `product` | owning IQ product |
| `domain_id` | string | yes | rare_seeds `department` | business function — **orthogonal to product** |
| `scope_key` | string | yes | rare_seeds `scope` | `blended` / `google-shopping` |
| `scope_level` ★ | enum | rec | **derived** from `scope` | `global·platform·channel·dashboard·campaign·product·customer·model` |
| `metric_base` | string | yes | rare_seeds `metric_base` | base concept: `revenue·orders·roas·cvr` |
| `category` | enum | rec | openapi `MetricCategory` (14) | see §7 |
| `aggregation` | enum | rec | rare_seeds `aggregation` | `level·sum·avg·rate·ratio·median` |
| `value_format` | enum | rec | openapi `ValueFormat` | `number·currency·percentage·decimal` |
| `granularity` | enum | rec | openapi `Granularity` | `daily·weekly·monthly·quarterly` |
| `measurement_type` | enum | rec | manual | `direct·derived·modeled·forecast·status` |

### Causal
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `causal_role` | enum | rec | rare_seeds `type` | `outcome·mediator·controllable·constraint·external·ml_output·untyped` |
| `causal_role_confidence` ★ | **enum `low·medium·high`** | opt | rare_seeds `type_confidence` | role-classification confidence — **was wrongly `number`**; numeric edge `confidence` lives on causal edges |
| `is_model_output` ★ | bool | rec | rare_seeds `is_model_output` (`yes`/`no`→bool) | ML output? |
| `is_derived` ★ | bool | yes | rare_seeds `is_derived` (`yes`/`no`→bool) | computed from other metrics? |
| `formula_status` | enum | opt | registry/dbt | `explicit·parsed·unknown` |
| `formula_text` ★ | string \| null | opt | rare_seeds `formula` | readable formula, e.g. `revenue / ad_spend`; **often null** |

### Endpoints — *Endpoint node folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `card_endpoint` | string | rec | rare_seeds `card_endpoint` | GET current-value path |
| `series_endpoint` | string | rec | rare_seeds `series_endpoint` | GET trend path |
| `endpoint_paths[]` | string[] | opt | openapi | all endpoints serving this metric |

### Source / lineage — *Connector node folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `source` ★ | **enum `single·multi`** | rec | rare_seeds `source` | scalar source cardinality — distinct from `source_set[]` |
| `source_set[]` ★ | string[] (split `\|`) | rec | rare_seeds `source_set` | connector slugs — **free list, broader than the 4-value `MetricSource` enum** |
| `connector_ids[]` | string[] | opt | resolved from `source_set` | normalized connector refs |
| `mart_sources[]` ★ | string[] (split `\|`) | opt | rare_seeds `mart_source` | warehouse lineage, `DB_RARE_SEEDS.MARTS.*` |
| `primary_grain` | enum | rec | rare_seeds `grain` | `daily·weekly·monthly·campaign·product·customer` |
| `grain_source` | string | opt | rare_seeds `grain_source` | `dbt` |
| `dimensions[]` ★ | string[] \| null (split `\|`) | opt | rare_seeds `dimensions` | slice axes; **often null** |
| `availability` | string \| null | opt | rare_seeds `availability` | nullable |
| `n_periods` ★ | int \| null | opt | rare_seeds `n_periods` | nullable integer |

### Surfacing + governance caches — *nodes/edges remain source of truth*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `source_dashboards[]` ★ | string[] (split `\|`) | opt | rare_seeds `source_dashboards` | cache of `SHOWN_ON` dashboards |
| `active_threshold_summary_json` | json | opt | Threshold nodes | fast-read cache; truth stays on `Threshold` |
| `active_policy_summary_json` | json | opt | Policy nodes | fast-read cache; truth stays on `Policy` |

### RBAC & lifecycle
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `data_classification` | enum | yes | RBAC | `public·internal·restricted·executive` |
| `owner_role_id` | string | rec | RBAC | accountable role |
| `is_kpi` | bool | rec | ingestion | headline metric? |
| `keep` ★ | bool | opt | rare_seeds `keep` (`yes`/`no`→bool) | curation flag (may collapse into `status`) |
| `status` | enum | yes | ingestion/review | `proposed·active·deprecated·blocked` |
| `data_quality_status` | enum | rec | ingestion | freshness/quality flag |
| `confidence` | number 0–1 | rec | ingestion/review | system confidence the metric was classified correctly |
| `created_at · updated_at` | datetime | yes | system | audit (no `tenant_id`) |

**Three IDs, three jobs** — `metric_uid` is the Neo4j identity; `canonical_id` is the cross-source business id; `metric_id` is the API/local slug. The API path is **never** the identity — it lives in `card_endpoint`/`series_endpoint`. The same concept across surfaces is grouped by shared `concept_key` and aggregated via `ROLLS_UP_TO`:

```text
metric:google-shopping:roas  (concept_key: roas) ─ROLLS_UP_TO▶ metric:google-ads:roas
metric:google-search:roas    (concept_key: roas) ─ROLLS_UP_TO▶ metric:google-ads:roas
metric:google-ads:roas       (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
metric:meta-overview:roas    (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
```

So the agent answers both *"what about Google Shopping ROAS?"* and *"what about ROAS overall?"* from `concept_key` + the rollup chain — no separate `MetricConcept` node required.

---

## 4. Other V1 core nodes

Every node carries audit fields `created_at`, `updated_at`, `status`, `source_profile_id` (no `tenant_id`). Listed below are the *distinguishing* properties.

### `Policy` — *OpenAPI-grounded rule that evaluates / escalates / notifies* **(merged from Codex)**
Built directly on the live `PolicyCreate` schema so it round-trips with the API, **plus** Claude's governance fields. **Separate from `Threshold`**: Policy explains *what to do and when to fire*; Threshold stores the *number*.

| Field | Type | Req | OpenAPI alignment | Purpose |
|---|---|---|---|---|
| `policy_id` | string | yes | nullable in API, required in KG | stable graph identity |
| `policy_name` | string | yes | `PolicyCreate` | human-readable |
| `description` | string \| null | rec | `PolicyCreate` | rule in language an agent can quote |
| `metric_id` | string | yes | `PolicyCreate` | governed metric (target) |
| `condition_type` | enum | yes | `ConditionType` | `threshold·anomaly·trend·missing_data` |
| `condition_operator` | enum \| null | rec | `ConditionOperator` | `lt·lte·gt·gte·eq·neq·between·outside` |
| `condition_value` | number \| null | opt | `PolicyCreate` | boundary value |
| `condition_value_high` | number \| null | opt | `PolicyCreate` | upper bound for `between`/`outside` |
| `condition_expression` | string \| null | opt | `PolicyCreate` | complex expression |
| `evaluation_window` | string | rec | default `24h` | window evaluated |
| `evaluation_frequency` | string | rec | default `1h` | evaluation cadence |
| `cooldown_hours` | number | rec | default `4` | re-alert suppression |
| `escalate_after_hours` | number | rec | default `24` | escalation timer |
| `severity` | enum | yes | `Severity` | `critical·high·medium·low·info` |
| `auto_investigate` | bool | rec | default `true` | wake an investigation agent on breach |
| `notify_channels[]` | string[] | opt | `PolicyCreate` | alert routing |
| `owner_role_id` | string | rec | KG ext (API: `owner_team`) | accountable role |
| `approval_required` | bool | yes | KG ext | whether edits need approval |
| `approval_role_ids[]` | string[] | opt | KG ext | roles that can approve |
| `priority` | int | rec | KG ext | conflict resolution across policies |
| `effective_from · effective_to` | datetime | opt | KG ext | time-bounded governance |
| `is_active` | bool | rec | `PolicyCreate` default `true` | live flag |
| `status` | enum | yes | KG ext | `draft·active·retired·superseded` |
| `source` | enum | yes | KG ext | `api·master_config_evidence·inferred·migrated·manual` |

### `Threshold` — *numeric / statistical boundary* **(merged: raw bands + normalized + 2σ)**
Stores the raw `ThresholdConfig` bands (strings, as the API returns them) **and** normalized numerics for evaluation, plus the `StatisticalThreshold` 2σ fields for percentile/seasonal types.

| Field | Type | Req | OpenAPI alignment | Purpose |
|---|---|---|---|---|
| `threshold_id` | string | yes | `ThresholdConfig.id` | identity |
| `metric_id · metric_name` | string | yes | `ThresholdConfig` | governed metric |
| `name` | string | yes | — | readable |
| `threshold_type` | enum | yes | `ThresholdType` | `static·percentile·seasonal` *(`dynamic`/`model_based` proposed, not live)* |
| `operator` | enum | yes | `ComparisonOperator` | `lt·lte·gt·gte·eq·neq·between·outside` |
| `direction` | enum | rec | `ThresholdDirection` | `higher_is_better·lower_is_better·target_is_best` |
| `green_value · yellow_value · red_value` | string | rec | `ThresholdConfig` | **raw** config bands (as strings) |
| `warning_value_num · critical_value_num · target_value_num` | number \| null | rec | parser/review | **normalized** numeric boundaries |
| `avg_val · stddev_val · lower_2sigma · upper_2sigma · min_val · max_val` | number | opt | `StatisticalThreshold` | percentile/seasonal baseline |
| `category` | string | rec | `ThresholdConfig` | grouping |
| `unit · grain` | enum | rec | — | should match the governed metric |
| `segment_filter_json` | json | opt | — | scope to platform/region/campaign/cohort/SKU |
| `evaluation_window` | string | rec | — | `last_7_days·last_4_weeks` |
| `owner_role_id` | string | rec | RBAC | who owns edits |
| `source` | enum | yes | — | `api·seed·human·statistical·learning_projection` |
| `status` | enum | yes | — | `draft·active·retired·superseded` |

### `Dashboard` — *product surface & access boundary*
| Field | Type | Req | Purpose |
|---|---|---|---|
| `dashboard_id` | string | yes | `ceo-pulse`, `website-performance` |
| `display_name · route_path` | string | yes/rec | name + where it lives |
| `product_id` | string | yes | product ownership / product-level RBAC |
| `domain_id` | string | rec | domain grouping for access & search |
| `dashboard_type` | enum | rec | `executive·operational·ml·review` |
| `default_endpoint_path · metadata_endpoint_path` | string | opt | dashboard payload + metadata endpoints |
| `audience_role_ids[]` | string[] | opt | default audience (access stays edge-driven) |
| `refresh_frequency` | string | opt | expected freshness |
| `data_classification` | enum | yes | `public·internal·restricted·executive` |
| `status` | enum | yes | `active·hidden·deprecated·proposed` |
| `source_registry` | string | opt | which file/API discovered it |

### `UIComponent` — *chart / KPI card / table (the chart registry, 646 entries)*
Holds chart content so the metric node stays small. A metric can appear in many components; a component can visualize many metrics. **Endpoint folded to a string field here too** (`query_endpoint_path`).

| Field | Type | Req | Purpose |
|---|---|---|---|
| `component_id · canonical_id` | string | yes | `google-shopping:shopping_roas_metric` |
| `dashboard_id · chart_id` | string | yes | registry identity fields |
| `component_kind · chart_type` | enum | rec | `chart·kpi_card·table·alert_panel`; 15-value `ChartType` |
| `title · subtitle` | string | yes/opt | rendered text |
| `product_id · section_id` | string | yes/rec | ownership + placement |
| `query_endpoint_path` | string | opt | GET endpoint the component uses (Endpoint-as-field) |
| `metric_keys[]` | string[] | rec | metrics visualized (edges stay source of truth) |
| `formula · formula_explanation` | string | rec | from the registry (100% present) |
| `how_to_read[] · decisions_answered[]` | string[] | rec | reader guidance + questions answered (100% present) |
| `narration_text · audio_file` | string | opt | narration **(558/646 — not all)** + audio path (100% present) |
| `visual_encoding_json` | json | opt | axes, series, colors, sort, aggregation |
| `data_classification` | enum | yes | can be stricter than the parent dashboard |
| `status` | enum | yes | `active·hidden·deprecated·proposed` |

### `Role` — *RBAC subject + approval + context filtering (Principal folded in)*
The RBAC brain: decides what an agent may retrieve, which paths it can traverse, what must be masked, who can edit. **No `Principal` node** — external auth gives a user id + role claims; the app maps the authenticated principal directly to a `Role` via `role_key`. Build the role graph now; attach `Person` later.

| Field | Type | Req | Purpose |
|---|---|---|---|
| `role_id` | string | yes | `ceo`, `cmo`, `marketing_manager` |
| `role_key` | string | yes | claim expected from JWT/session/external auth — **the principal→role mapping** |
| `display_name` | string | yes | readable |
| `role_type` | enum | yes | `executive·department_lead·operator·analyst·viewer·system_agent·approver` |
| `auth_role` | enum | opt | maps to OpenAPI `UserRole` where available |
| `department_id` | string | opt | marketing/finance/operations/product/customer/engineering |
| `seniority_rank` | int | rec | escalation & conflict resolution |
| `default_product_ids[] · default_domain_ids[]` | string[] | opt | convenience cache; permission **edges** remain source of truth |
| `max_sensitivity_level` | enum | yes | highest data classification accessible by default |
| `can_manage_rbac` | bool | yes | only a few admin roles may alter permission edges |
| `can_create_policy · can_create_threshold · can_edit_endpoint` | bool | yes | global capabilities; still constrained by scoped edges |
| `agent_context_limit` | int | opt | max graph facts exposed to an agent for this role |
| `redaction_policy_json` | json | opt | default masking for fields/values/sources/causal paths |
| `status` | enum | yes | `active·disabled·deprecated` |
| `created_at · updated_at` | datetime | yes | audit |

### Supporting nodes (kept small)

**`IntelligenceProduct`** — top visible layer. `product_id` (`miq·ciq·piq·dc·creative_iq`), `display_name`, `category` (`analytics·decisioning·creative·external`), `schema_name`+`schema_status` (`owned`/`shared`), `route_prefixes[]`, `product_gate_id`, `default_sensitivity_level`.

| product_id | display | note |
|---|---|---|
| `miq` | Marketing IQ | central analytics, 50+ dashboards |
| `ciq` | Customer IQ | real product, still on `miq` schema |
| `piq` | Product IQ | real product, still on `miq` schema |
| `dc` | Decision Canvas | writes capsules/thoughts |
| `creative_iq` | Creative IQ | external, separate repo/manifest |

**`Domain`** — business grouping for discovery + role scoping. `domain_id`, `name`, `domain_type` (`business·technical·risk·ml`), `parent_domain_id` (tree), `product_id`, `data_classification`, `owner_role_id`.

> **Optional in V1:** `Platform` (vendor system — Google Ads, GA4, Snowflake) may stay as a thin node reached by `SOURCED_FROM`; otherwise the `source_set[]` field on Metric is sufficient lineage for V1.

---

## 5. RBAC

RBAC is **graph-native** because agent context is graph-native. The app authenticates the user; the KG decides what graph facts may be retrieved for that authenticated principal's role. **Build the allowed context first, then answer from it** — never run an unrestricted traversal and filter afterward.

### Two tiers, role-first
- **Tier 1 — product gate:** `Role -CAN_ACCESS_PRODUCT-> IntelligenceProduct`. No MIQ entitlement ⇒ marketing metrics invisible.
- **Tier 2 — domain/metric:** `Role -CAN_VIEW {can_see_value, can_see_thought, redaction}-> Domain/Metric/Dashboard`. If a causal path crosses into restricted territory, the agent returns a **masked** fact (*"a restricted operations guardrail is affected; request access"*), never the name/value.

### Permission-edge properties
Most access logic lives on the Role permission edges, not the node. **Merged from Codex:** `product_scope_ids[]`/`domain_scope_ids[]` allow product/domain scoping without separate nodes.

| Property | Type | Applies to | Purpose |
|---|---|---|---|
| `effect` | `allow·deny` | all | explicit deny at higher priority blocks inherited access |
| `permission` | string | all | `view·explain·traverse·edit·approve·execute·manage` |
| `priority` | int | all | higher wins on conflict |
| `scope_depth` | int | product/domain grants | how far the grant flows down (product→all = depth 2) |
| `product_scope_ids[] · domain_scope_ids[]` | string[] | view/edit | scope without separate nodes |
| `allowed_fields[]` | string[] | view edges | fields that may be exposed (empty = safe defaults) |
| `masked_fields[]` | string[] | view edges | fields hidden even when the node is visible |
| `max_grain` | string | metric/dashboard | restrict detail (weekly-only, no campaign-level) |
| `row_filter_json` | json | metric/component | segment filters (platform/region/brand/SKU/campaign) |
| `condition_json` | json | all | time/environment/entitlement/approval-state conditions |
| `valid_from · valid_to` | datetime | all | temporary access / scheduled deactivation |
| `approval_required` | bool | edit/execute | role may request but not directly apply |
| `source` | string | all | `manual_admin·org_sync·migration·policy_import` |

> **Edge-naming note (Codex alternative):** Codex used per-action edge labels (`CAN_VIEW_METRIC`, `CAN_VIEW_DASHBOARD`, `CAN_EDIT_THRESHOLD`…). This finalised schema keeps **generic labels with a `permission` property** (`CAN_VIEW`, `CAN_EDIT`, …) because it scales better and keeps queries uniform. Either is valid; do not mix them.

### Recommended role types (seed now)
| Role | Typical access | Typical edit power |
|---|---|---|
| `ceo` | all products, executive dashboards, cross-domain causal paths | approve high-impact policy/action changes |
| `cfo` | finance, revenue, margin, spend, forecast, exec dashboards | edit finance thresholds, approve spend policies |
| `cmo` | Marketing IQ, acquisition, retention, campaign perf, blended ROAS | edit marketing thresholds/policies, approve campaign actions |
| `marketing_manager` | marketing dashboards + assigned platform/campaign metrics | edit selected thresholds with approval |
| `customer_success_lead` | Customer IQ, cohorts, churn, retention, lifecycle | edit customer monitoring thresholds |
| `product_manager` | Product IQ, SKU, inventory, catalog, conversion impact | edit product/inventory policies with approval |
| `analyst` | read-only to assigned products/domains | none (can propose, not apply) |
| `system_agent` | role-scoped read on behalf of a user/task | none unless explicitly granted; always carries an acting role + reason |

### Query patterns
```cypher
// 1 · Role closure — directly assigned + inherited
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH (r)-[:INHERITS_FROM*0..4]->(parent:Role {status: 'active'})
RETURN collect(DISTINCT r) + collect(DISTINCT parent) AS role_scope;

// 2 · Allowed metrics for a role — product/domain grants flow down; explicit deny wins in app logic
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH (r)-[pm:CAN_VIEW|CAN_ACCESS_DOMAIN|CAN_ACCESS_PRODUCT]->(target)
WHERE pm.effect = 'allow'
WITH collect({target: target, grant: pm}) AS grants
MATCH (m:Metric {status: 'active'})
WHERE any(g IN grants WHERE
  (g.target:Metric AND id(g.target) = id(m)) OR
  (g.target:Domain AND (m)-[:BELONGS_TO]->(g.target)) OR
  (g.target:IntelligenceProduct AND (m)-[:PART_OF_PRODUCT]->(g.target)))
RETURN DISTINCT m.metric_uid, m.concept_name, m.product_id, m.domain_id, m.data_classification;

// 3 · Agent answer context for one metric — fetch only allowed facts before the LLM sees context
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (m:Metric {metric_uid: $metric_uid, status: 'active'})
WHERE EXISTS { MATCH (r)-[grant:CAN_VIEW]->(m) WHERE grant.effect = 'allow' }
OPTIONAL MATCH (m)-[:HAS_THRESHOLD]->(t:Threshold {status: 'active'})
OPTIONAL MATCH (m)-[:GOVERNED_BY]->(p:Policy {status: 'active'})
OPTIONAL MATCH (c:UIComponent)-[:VISUALIZES]->(m)
OPTIONAL MATCH (d:Dashboard)-[:RENDERS]->(c)
OPTIONAL MATCH (up:Metric)-[:CAUSES]->(m)
RETURN m, collect(DISTINCT t) AS thresholds, collect(DISTINCT p) AS policies,
       collect(DISTINCT c) AS components, collect(DISTINCT d) AS dashboards,
       collect(DISTINCT up.metric_uid) AS upstream;

// 4 · Safe edit check — require explicit edit permission before any mutation
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (t:Threshold {threshold_id: $threshold_id, status: 'active'})
MATCH (r)-[grant:CAN_EDIT]->(t)
WHERE grant.effect = 'allow'
RETURN t.threshold_id, grant.approval_required AS approval_required;
```

### Example role scenarios
| Question | Caller | Expected KG behavior |
|---|---|---|
| What do you know about blended ROAS? | `cmo` | Marketing-IQ metric, formula if known, card/series endpoints, dashboards, marketing policies/thresholds, visible upstream marketing metrics |
| Why did contribution margin drop? | `marketing_manager` | marketing-visible context only; if finance margin is restricted, say a restricted finance dependency exists without exposing hidden fields |
| Edit the critical threshold for Google Ads ROAS | `analyst` | deny direct mutation; report missing edit permission (V1) |
| Show Product IQ inventory metrics affected by ad spend | `cmo` | traverse cross-product paths only where the role has Product IQ access or an approved cross-domain edge |
| Who can approve a reorder-threshold change? | `product_manager` | follow `CAN_APPROVE` / `OWNED_BY` / `REPORTS_TO` and return permitted approver roles |

---

## 6. V1 edge catalog

Keep edge names boring and explicit. Relationship-level audit props on edges: `source_profile_id`, `confidence` (inferred), `valid_from`/`valid_to`, `status`.

| Edge | From → To | Meaning | Key props |
|---|---|---|---|
| `HAS_DOMAIN` | IntelligenceProduct → Domain | product/domain tree | `primary·source` |
| `PARENT_OF` | Domain → Domain | domain tree | — |
| `PART_OF_PRODUCT` | Metric/Dashboard/UIComponent → IntelligenceProduct | top-layer ownership | `primary·confidence` |
| `BELONGS_TO` | Metric/Dashboard → Domain | business grouping | `confidence·source` |
| `ROLLS_UP_TO` | Metric → Metric | channel→platform→blended aggregation | `aggregation_method·lag·confidence` |
| `DECOMPOSES_INTO` | Metric → Metric | formula component | `operator·weight·confidence=1.0` |
| `HAS_THRESHOLD` | Metric → Threshold | metric boundary | `is_default·segment_context·priority` |
| `GOVERNED_BY` | Metric/Dashboard/UIComponent/Threshold → Policy | policy scope | `priority·status·effective_from` |
| `ENFORCES_THRESHOLD` | Policy → Threshold | policy explains/enforces the number | `explanation_type·confidence` |
| `OWNED_BY` | Metric/Domain/Policy → Role | accountable owner | — |
| `RENDERS` | Dashboard → UIComponent | dashboard composition | `section_id·order·visibility` |
| `VISUALIZES` | UIComponent → Metric | chart/card → metric | `match_type·confidence·axis_role` |
| `SHOWN_ON` | Metric → Dashboard | metric appears on surface (cached on `source_dashboards[]`) | `is_primary` |
| `SOURCED_FROM` | Metric → Platform | underlying source platform *(optional in V1)* | `source_role·freshness_sla·confidence` |
| `INFLUENCES` | Metric → Metric | weak causal/correlation candidate | `confidence·lag·mechanism·evidence_count` |
| `CORRELATES_WITH` | Metric ↔ Metric | statistical association, **not** causal | `correlation·p_value·lag` |
| `CAUSES` | Metric → Metric | approved causal relation | `edge_key·confidence·lag_hours_min/max` |
| `CAN_ACCESS_PRODUCT` | Role → IntelligenceProduct | tier-1 product gate | `effect·permission·scope_depth·masked_fields` |
| `CAN_VIEW` | Role → Domain/Metric/Dashboard/UIComponent | tier-2 read | `effect·allowed_fields·masked_fields·max_grain·row_filter_json` |
| `CAN_EDIT` | Role → Metric/Policy/Threshold | edit gate | `effect·approval_required·priority` |
| `CAN_APPROVE` | Role → Policy/Threshold/Metric | approval authority | `effect·approval_limit_json·priority` |
| `INHERITS_FROM` | Role → Role | RBAC permission inheritance (keep separate from org reporting) | `priority·source` |
| `REPORTS_TO` | Role → Role | org/social escalation | `relationship_type·source` |

---

## 7. Live enums + type-audit summary

### Authoritative live enums (re-verified from `openapi.json`)
Use these verbatim to constrain properties.
- `ValueFormat` (4): `number · currency · percentage · decimal`
- `Granularity` (4): `daily · weekly · monthly · quarterly`
- `ChartType` (15): `line · area · bar · horizontal_bar · grouped_bar · pie · donut · sankey · heatmap · table · sparkline · scatter · treemap · gauge · funnel`
- `ThresholdType` (3): `static · percentile · seasonal` *(`dynamic`/`model_based` = proposed, not live)*
- `ThresholdDirection` (3): `higher_is_better · lower_is_better · target_is_best`
- `ConditionType` (4): `threshold · anomaly · trend · missing_data`
- `ConditionOperator` = `ComparisonOperator` (8): `lt · lte · gt · gte · eq · neq · between · outside`
- `Severity` (5): `critical · high · medium · low · info`
- `MetricCategory` (14): `advertising · revenue · traffic · email · customer · sms · google_ads · meta_ads · efficiency · comparison · financial · marketing · product · operational`
- `MetricSource` (4): `ga4 · google_ads · meta_ads · klaviyo` — **but `source_set[]` is a free list** (Magento, `linkedin_ads` also appear)
- `UserRole` (7): `super_admin · agency_admin · tenant_admin · analyst · viewer · admin · user` *(auth-layer roles; map to KG `role_type` via `Role.auth_role`)*

Graph-derived vocab (from `rare_seeds`): scope `blended·store·ecom·web·ml·google·meta·klaviyo` · causal_role `outcome·mediator·controllable·constraint·external·ml_output·untyped` · aggregation `level·rate·avg·sum·ratio·median`.

### Type corrections (after merging the data)
1. **`causal_role_confidence`: `number` → enum `low·medium·high`.** Source `type_confidence` is categorical, not a 0–1 float. The numeric edge `confidence` is a **different** thing on causal edges.
2. **Booleans encoded as strings.** `is_model_output`, `is_derived`, `keep` arrive as `yes`/`no` — convert to real **bool** at ingestion.
3. **Pipe-delimited lists → `string[]`.** `source_set`, `mart_sources`, `source_dashboards`, `dimensions` arrive as a single `|`-delimited string — split at ingestion.
4. **Missing scalar `source`.** Add `source` enum `single·multi` (distinct from `source_set[]`).
5. **Nullable fields.** `availability`, `n_periods`, `formula_text`, `dimensions` are frequently empty (`—`); model as nullable. `n_periods` is **int-or-null**.
6. **`source_set` exceeds the `MetricSource` enum** (e.g. `linkedin_ads`, `magento`) — keep a free `string[]`, do **not** constrain to the 4 values.
7. **`scope` is one value.** Store it as `scope_key`; derive `scope_level`.
8. **Chart-registry coverage (data fix):** `id`, `title`, `formula`, `formula_explanation`, `how_to_read`, `decisions_answered`, `audio_file`, `dashboard_id`, `chart_id`, `canonical_id` are present on **all 646** entries; **`narration_text` is present on only 558/646 (86.4%)** — treat it as optional.
9. **`ThresholdConfig` bands are strings.** `green_value`/`yellow_value`/`red_value` come back as strings — keep raw **and** parse to `*_num`.

---

## 8. What the merges removed (traceability)

| Removed | Was | Now lives as | If you need it back |
|---|---|---|---|
| `Tenant` node + `tenant_id` field | isolation root | the Neo4j **database** is the tenant | one-DB-multi-tenant → reintroduce with composite constraints |
| `MetricConcept` node | semantic concept | `Metric.concept_key` / `concept_name` / `synonyms[]` / `unit_family` / `default_direction`; grouped via `ROLLS_UP_TO` | V2, if a concept needs its own facts/versioning |
| `Endpoint` node | API binding | `Metric.card_endpoint` / `series_endpoint` / `endpoint_paths[]`; `UIComponent.query_endpoint_path` | V2, if you need endpoint classification/harvest as graph state |
| `Connector` node | data source | `Metric.source` / `source_set[]` / `connector_ids[]` / `mart_sources[]` | V2, for shared connector status/freshness across metrics |
| `Principal` node | external user | `Role.role_key` (app maps the authed principal → role) | when real auth/HR users exist → add `Person` |

**Edges removed:** `ENTITLED_TO`, `HAS_DOMAIN`(from Tenant), `INSTANCE_OF`, `ASSIGNED_ROLE`, `USES_ENDPOINT`/`CURRENT_VALUE_ENDPOINT`/`SERIES_ENDPOINT`/`CHART_DATA_ENDPOINT`, `SOURCED_FROM`(→Connector)/`PROVIDED_BY`/`CONNECTS_TO`.

---

## 9. Ingestion — control vs business plane

Of 877 GET ops, only the **business plane** becomes graph nodes. **Edges are built by us from scratch** — no legacy relationship catalog or ontology is imported; the causal layer starts empty and every edge is earned from evidence.

| Pattern | Action | Why |
|---|---|---|
| `GET /{dash}/metrics/{id}` | **promote metric** | card / current-value surface |
| `GET /{dash}/charts/{id}` | **promote UI / series** | chart / time-series |
| `GET /{dash}/` · `/{dash}/metadata` | promote dashboard data | dashboard surface |
| `/master-config/**` · `/master/**` | **config evidence only** | control-plane; may seed Policy/Threshold, never a metric or an edge |
| `/auth/**` · `/admin/**` · `/settings/**` | **exclude** | access/admin control plane |
| `/health · /docs · /redoc · /openapi.json` | exclude / metadata | observability & review surfaces |
| `POST/PUT/PATCH/DELETE` (46 ops) | exclude from harvest | may later become a governed Tool/Action (V2) |

**Pipeline:** acquire spec (live preferred, checked-in fallback, hash it) → deterministic exclusion → endpoint families (method+path+response-sig+role) → classify once per spec hash → human review → deterministic harvest → **proposals only** → arbitration writes (dedupe by canonical identity) → completeness report → incremental (reclassify only changed families; deprecate, never delete). **Missing formulas are fine** — keep endpoints/charts, set `formula_status=unknown`. **The causal layer starts empty and is built from scratch** — no legacy relationships are imported; raw statistical associations may enter as `CORRELATES_WITH` but are never auto-promoted to `CAUSES`.

---

## 10. Neo4j constraints (V1)

```cypher
CREATE CONSTRAINT metric_uid      IF NOT EXISTS FOR (n:Metric)      REQUIRE n.metric_uid    IS UNIQUE;
CREATE CONSTRAINT product_id      IF NOT EXISTS FOR (n:IntelligenceProduct) REQUIRE n.product_id IS UNIQUE;
CREATE CONSTRAINT dashboard_id    IF NOT EXISTS FOR (n:Dashboard)   REQUIRE n.dashboard_id  IS UNIQUE;
CREATE CONSTRAINT ui_component_id IF NOT EXISTS FOR (n:UIComponent) REQUIRE n.component_id   IS UNIQUE;
CREATE CONSTRAINT policy_id       IF NOT EXISTS FOR (n:Policy)      REQUIRE n.policy_id      IS UNIQUE;
CREATE CONSTRAINT threshold_id    IF NOT EXISTS FOR (n:Threshold)   REQUIRE n.threshold_id   IS UNIQUE;
CREATE CONSTRAINT role_id         IF NOT EXISTS FOR (n:Role)        REQUIRE n.role_id        IS UNIQUE;
CREATE INDEX metric_product  IF NOT EXISTS FOR (n:Metric) ON (n.product_id);
CREATE INDEX metric_scope    IF NOT EXISTS FOR (n:Metric) ON (n.scope_key);
CREATE INDEX metric_concept  IF NOT EXISTS FOR (n:Metric) ON (n.concept_key);
CREATE INDEX metric_role     IF NOT EXISTS FOR (n:Metric) ON (n.causal_role);
CREATE INDEX role_key        IF NOT EXISTS FOR (n:Role)   ON (n.role_key);
```
*(No `tenant_id`, `metric_concept`, or `endpoint_id` constraints — the per-tenant DB means no composite tenant keys are needed.)*

---

## 11. V1 build order

1. **Inventory** — `IntelligenceProduct`, `Domain`, `Metric` (merged: concept + endpoints + source/lineage), `Dashboard`, `UIComponent`. Product-ownership + dashboard/metric mapping.
2. **RBAC** — `Role` nodes + product/domain/dashboard/metric grants (permission edges). Role-filtered context-pack query.
3. **Governance** — attach `Policy` and `Threshold` to metrics; field-level edit gating.
4. **Causal layer (the point)** — build `CAUSES`/`INFLUENCES`/`CORRELATES_WITH` from scratch; no legacy relationship catalog is imported. Raw statistical associations enter as `CORRELATES_WITH`, never auto-promoted to `CAUSES`; upstream/downstream/path queries.
5. **Defer to V2** — everything in §12.

V1's success metric is *"N evidence-backed `CAUSES`/`INFLUENCES` edges"*, not *"N nodes ingested."*

---

## 12. Deferred to V2 (named, not specified)

These are out of scope for the lean V1 above. They are the FRD's memory/learning machinery (Layers 2–6) plus the Layer-1 entities both prior drafts omitted. **Listed here so nothing is lost; deliberately without property tables.** Defer until V1's spine, RBAC, governance, and a real causal layer are live — or memory will pollute causal truth.

**Causal-graph entities the FRD names (Layer 1) but V1 omits:**
- `Outcome` — business result distinct from a Metric (revenue realization, fulfillment, retention).
- `Tool` / `Action` — what can influence a node (`google_ads_budget`, `meta_budget`); `CONTROLLED_BY` edges.
- `Investigation_Rule` — promoted "must-check" dimension, **harness-enforced** (blocks a recommendation if required data is missing).
- `Approval_Rule` — which roles must approve which classes of action; approval path **derived from node owners**.

**Memory / learning / governance (Layers 2–6):**
- `Thoughtlet` — atomic observation anchored to a metric; carries provenance + data-quality flag.
- `DecisionCapsule` — the FRD's 10-section episodic memory (Identity, Trigger, Evidence, Reasoning, Recommendation, Approval Path, Human Feedback, Execution, Monitoring Contract, Outcome Learning); `ANCHORED_TO` graph nodes for reverse temporal context injection.
- `MonitoringContract` + `WakeCondition` — decision-level monitoring with **time- and event-based** wake hooks; guardrail metrics; review date.
- `EvidenceEvent` (append-only) + `CausalRelation` (reified edge) — the source of derived `confidence` via a Beta fold (`confidence = α/(α+β)`, `evidence_mass = α+β`); tiers `prior·observational·quasi_experimental·interventional·human·outcome`.
- `LearningCandidate` → `PromotedMemory` → governed projection (new edge · confidence update · investigation rule · episodic memory); immediate vs delayed timing; narrative memory retained alongside numeric calibration.
- `GraphChangeProposal` — write-safety layer; nothing mutates the graph directly.
- `GraphVersion` — snapshot lineage for time-travel / reconstructing "what the brain believed at decision time."
- `SourceProfile` / `Endpoint_Family` / `IngestionRun` — full ingestion lineage so metrics re-ingest deterministically without re-running the classifier LLM.
- Capsule-Agent **rehydration recipe** + agent-state snapshot — agents are rehydrated on wake, not kept running.
- `Person` — real auth/HR users, once they exist.

---

## Appendix — quick reference

- **API:** TW Analytics API 1.0.0 · 902 paths · 877 GET / 32 POST / 6 PUT / 3 PATCH / 5 DELETE · 463 schemas.
- **Connectors:** GA4 (`GA4`), Google Ads (`GOOGLE_ADS`), Klaviyo (`KLAVIYO_RAW_COPY`), Magento (`magento`, needs tunnel), Meta Ads (`META_ADS`) — Snowflake + Azure dual-write. (Magento & `linkedin_ads` appear in `source_set` but not the 4-value `MetricSource` enum.)
- **Products:** `miq` Marketing IQ · `ciq` Customer IQ · `piq` Product IQ *(CIQ/PIQ on `miq` schema)* · `dc` Decision Canvas · `creative_iq` Creative IQ *(external)*.
- **Chart registry:** 646 entries; 10 fields at 100%, `narration_text` at **558/646**.
- **rare_seeds:** 355 nodes, **4 edges** — the gap this project closes.
- **Tenancy:** one Neo4j **database per tenant** — no `Tenant` node, no `tenant_id` field.

### Prior-draft comparison

| Dimension | Claude `.md` (backbone) | Codex `.html` | **Finalised (this doc)** |
|---|---|---|---|
| V1 node count | 8 (spine + 6) | 6 (no spine nodes) | **8 (spine + 6)** |
| Product/Domain | nodes (spine) | fields only | **nodes (spine)** |
| Policy fields | generic enums | **OpenAPI `PolicyCreate`** | **OpenAPI `PolicyCreate` + governance** |
| Threshold fields | bands + percentile | **`ThresholdConfig` raw bands** | **raw + normalized + `StatisticalThreshold` 2σ** |
| RBAC model | two-tier, rich edge props | per-action edges + scope ids | **two-tier + `permission` prop + scope ids** |
| FRD memory/learning | full Part II vision | none | **named & deferred (§12)** |
| Data accuracy | "all 646 have narration" | "558 narration" | **558/646 (corrected)** |
| Scope | V1 + V2 | lean V1 | **lean V1, FRD-aware** |

- **Companion:** `finalised-graph-schema-claude.html` (visual blueprint).
