# ThoughtWire Causal Knowledge Graph — V1 Blueprint (Claude)

Date: 2026-06-13 · DB target: **Neo4j** · One database **per tenant**

> The design for ThoughtWire's "business body" — the causal knowledge graph an agent wakes into. Built from independent verification of the live API, the chart registry, the connectors, the product registry, and the `rare_seeds` model. This revision **combines** the full-target Claude blueprint with the lean Codex V1 node-details (graph shape, RBAC depth, query patterns) and applies five data-grounded simplifications. **Part I** is the shippable V1; **Part II** is the full vision (V2+).
>
> Companion: `thoughtwire-kg-schema-claude.html` — same schema, visual blueprint with the V1 graph-shape diagram and property tables.

---

# Part I — V1 (what ships first)

## 1. Verdict, provenance, and the V1 stance

The core idea is right: ThoughtWire needs a causal knowledge graph as the stable "business body," and Neo4j is justified (causal traversal, versioning, role-scoped context, provenance, governed learning). Three things must hold from day one:

1. **It is not a tree, and it is not one flat graph.** A **tree-like navigation spine** (`IntelligenceProduct → Domain → Metric`) for orientation; the causal model is a **temporal DAG** with feedback loops resolved by lag; governance/memory ride as **overlays** — connected, never melted in.
2. **The hard part is edges, not nodes.** `rare_seeds` has **355 richly-typed nodes and only 4 edges** — and those 4 are correlation, one spurious. Node extraction is solved. **Building trustworthy causal edges is the entire project.**
3. **Confidence is derived, never typed in.** Edge strength folds from an append-only **evidence ledger**, and the graph is only written through **governed proposals** with **versioning** — so we can always answer "why does the brain believe this, and what did it believe at decision time?"

### Per-tenant database — no `Tenant` node

**Each tenant gets its own Neo4j database.** Tenant identity is therefore **database/runtime context**, not a business entity in the graph. The app selects the tenant database before querying; there is **no `Tenant` node** and **no `tenant_id` field** on any node. This matches how the data is already isolated downstream — per-tenant Snowflake `USR_<SLUG>` / `RL_<SLUG>` / `WH_<SLUG>` users/roles/warehouses and per-tenant databases (e.g. `DB_RARE_SEEDS`). If multi-tenant-in-one-database is ever forced, reintroduce `Tenant` with strict composite constraints.

### The five V1 merges (all validated against the data)

| Change | Why it's safe | Evidence |
|---|---|---|
| **Drop `Tenant`** | Tenant = DB/runtime context with per-tenant DBs | per-tenant Snowflake `USR_/RL_/WH_<SLUG>`, `DB_RARE_SEEDS` naming |
| **`MetricConcept` → `Metric`** fields | No separate concept entity exists; concept ≈ `metric_base` + canonical name | `MetricDefinition` and `MetricResponse` are both keyed by metric; cross-scope grouping recovered via `concept_key` + `ROLLS_UP_TO` |
| **`Endpoint` + `Connector` → `Metric`** fields | `rare_seeds` already stores them flat on the node | `card_endpoint`, `series_endpoint`, `source`, `source_set`, `mart_source`, `source_dashboards` |
| **`Principal` → `Role`** | No "principal" in the data — only `User {role}` | app authenticates the user; graph needs only the **Role** (carrying a `role_key` claim) |
| **Type audit** | Real `rare_seeds` values disagree with earlier declared types | `type_confidence: high` is categorical, not a number; booleans are `yes`/`no`; lists are `\|`-delimited (see §7) |

**Provenance checked:** live API `localhost:8005/openapi.json` (902 paths · 877 GET); checked-in `docs/frd-docs/openapi.json` (structurally identical, pretty-printed — not a byte-for-byte hash match); `docs/frd-docs/chart-registry.json` (646 entries, all with formula/explanation/how_to_read/decisions_answered/audio); 5 connectors (GA4, Google Ads, Klaviyo, Magento, Meta Ads); products `miq·ciq·piq·dc·creative_iq`; `rare_seeds` graph (355 nodes, 4 correlation edges).

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
            Metric ─HAS_THRESHOLD▶ Threshold      Metric ─GOVERNED_BY▶ Policy ─ENFORCES▶ Threshold
            Dashboard ─RENDERS▶ UIComponent ─VISUALIZES▶ Metric
            Role ─CAN_ACCESS_PRODUCT/CAN_VIEW/CAN_EDIT/CAN_APPROVE▶ (product/domain/metric/policy/threshold)
```

Compared with the earlier shape: the `Tenant` and `Principal` boxes are gone; `MetricConcept`, `Endpoint`, and `Connector` are no longer boxes — their data lives on `Metric`. The spine starts at `IntelligenceProduct`.

### The five questions the graph must answer for any node
1. **Upstream** — what influences this? → `CAUSES`/`INFLUENCES` in-edges
2. **Downstream blast radius** — what does this influence? → out-edges with lag
3. **Policy & threshold** — what governs it, what's the breach line? → `GOVERNED_BY`, `HAS_THRESHOLD`
4. **Confidence** — how strong is each relationship? → evidence ledger on the edge (V2)
5. **Action eligibility** — what can be done, by whom? → Role RBAC (+ Action/Tool in V2)

---

## 3. The merged `Metric` node

`Metric` is the hub. Identity uses the three-ID strategy (`metric_uid` / `canonical_id` / `metric_id`). After the merges it also carries the concept, endpoint, and source/lineage data that used to live on separate nodes. **Flat, indexable fields** throughout; JSON only for the cached threshold/policy summaries. The **Source field** column maps each graph field to its `rare_seeds`/OpenAPI origin so ingestion is unambiguous. `★` marks a **corrected type** (see §7).

> **No `tenant_id`** on any node — the database is the tenant boundary.

### Identity & semantics — *MetricConcept folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `metric_uid` | string | yes | app `metric:<scope>:<base>` | Neo4j identity, e.g. `metric:google-shopping:roas` |
| `canonical_id` | string | yes | catalog | cross-source business id, `google-shopping-roas` |
| `metric_id` | string | yes | rare_seeds `node_id` / path slug | API/local slug, `roas` |
| `concept_key` | string | rec | rare_seeds `metric_base` | semantic concept group (`roas`) — **replaces MetricConcept**; group cross-scope via this + `ROLLS_UP_TO` |
| `concept_name` | string | rec | catalog/manual | "Return on Ad Spend" |
| `synonyms[]` | string[] | opt | catalog | resolution aliases, `return_on_ad_spend` |
| `unit_family` | enum | rec | manual | `currency·ratio·percent·count·duration·score` |
| `default_direction` | enum | rec | manual | `higher_is_better·lower_is_better·target_is_best` |

### Classification
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `product_id` | string | yes | rare_seeds `product` (→ `miq`…) | owning IQ product |
| `domain_id` | string | yes | rare_seeds `department` (e.g. Finance/Exec) | business function — **orthogonal to product** |
| `scope_key` | string | yes | rare_seeds `scope` | `blended` / `google-shopping` |
| `scope_level` ★ | enum | rec | **derived** from `scope` | `global·platform·channel·dashboard·campaign·product·customer·model` |
| `metric_base` | string | yes | rare_seeds `metric_base` | base concept: `revenue·orders·roas·cvr` |
| `category` | enum | rec | openapi `MetricCategory` (14) | see §7 enums |
| `aggregation` | enum | rec | rare_seeds `aggregation` | `level·sum·avg·rate·ratio·median` |
| `measurement_type` | enum | rec | manual | `direct·derived·modeled·forecast·status` |

### Causal
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `causal_role` | enum | rec | rare_seeds `type` | `outcome·mediator·controllable·constraint·external·ml_output·untyped` |
| `causal_role_confidence` ★ | **enum `low·medium·high`** | opt | rare_seeds `type_confidence` | role-classification confidence — **was wrongly `number`**; numeric edge `confidence` lives on `CausalRelation` (Part II) |
| `is_model_output` ★ | bool | rec | rare_seeds `is_model_output` (`yes`/`no`→bool) | ML output? |
| `is_derived` ★ | bool | yes | rare_seeds `is_derived` (`yes`/`no`→bool) | computed from other metrics? |
| `formula_status` | enum | opt | registry/dbt | `explicit·parsed·unknown` |
| `formula_text` ★ | string \| null | opt | rare_seeds `formula` | readable formula, e.g. `revenue / ad_spend`; **often null** |

### Endpoints — *Endpoint node folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `card_endpoint` | string | rec | rare_seeds `card_endpoint` | GET current-value path, `/api/v1/ceo-pulse/metrics/revenue` |
| `series_endpoint` | string | rec | rare_seeds `series_endpoint` | GET trend path, `/api/v1/annual-planning/charts/channel_revenue` |
| `endpoint_paths[]` | string[] | opt | openapi | all endpoints serving this metric |

### Source / lineage — *Connector node folded in*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `source` ★ | **enum `single·multi`** | rec | rare_seeds `source` | scalar source cardinality — **was missing**, distinct from `source_set[]` |
| `source_set[]` ★ | string[] (split `\|`) | rec | rare_seeds `source_set` | connector slugs `ga4·google_ads·linkedin_ads·meta_ads·klaviyo` — **free list, broader than the 4-value `MetricSource` enum** |
| `connector_ids[]` | string[] | opt | resolved from `source_set` | normalized connector refs |
| `mart_sources[]` ★ | string[] (split `\|`) | opt | rare_seeds `mart_source` | warehouse lineage, `DB_RARE_SEEDS.MARTS.mart_ceo_daily_pulse` |
| `primary_grain` | enum | rec | rare_seeds `grain` | `daily·weekly·monthly·campaign·product·customer` |
| `grain_source` | string | opt | rare_seeds `grain_source` | `dbt` |
| `dimensions[]` ★ | string[] \| null (split `\|`) | opt | rare_seeds `dimensions` | slice axes; **often null** |
| `availability` | string \| null | opt | rare_seeds `availability` | nullable |
| `n_periods` ★ | int \| null | opt | rare_seeds `n_periods` | nullable integer |

### Surfacing + governance caches — *nodes/edges remain source of truth*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `source_dashboards[]` ★ | string[] (split `\|`) | opt | rare_seeds `source_dashboards` | cache of `SHOWN_ON` dashboards, `annual_planning\|ceo_pulse\|…` |
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

**Three IDs, three jobs** — the clean answer to "`google-shopping-roas` vs `/google-shopping/metrics/roas`": `metric_uid` is the Neo4j identity; `canonical_id` is the cross-source business id; `metric_id` is the API/local slug. The API path is **never** the identity — it lives in `card_endpoint`/`series_endpoint`. The same concept across surfaces is grouped by shared `concept_key` and aggregated via `ROLLS_UP_TO`:

```text
metric:google-shopping:roas  (concept_key: roas) ─ROLLS_UP_TO▶ metric:google-ads:roas
metric:google-search:roas    (concept_key: roas) ─ROLLS_UP_TO▶ metric:google-ads:roas
metric:google-ads:roas       (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
metric:meta-overview:roas    (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
```

So the agent answers both *"what about Google Shopping ROAS?"* and *"what about ROAS overall?"* from `concept_key` + the rollup chain — no separate `MetricConcept` node required.

---

## 4. Other V1 core nodes

Every node carries audit fields `created_at`, `updated_at`, `status`, `source_profile_id`, `graph_version_id` (no `tenant_id`). Listed below are the *distinguishing* properties.

### `Policy` — *the rule that explains / evaluates / escalates / constrains*
Keep structured fields, not just prose, so agents can reason over it. **Separate from `Threshold`**: Policy explains what to do; Threshold stores the number.

| Field | Type | Req | Purpose |
|---|---|---|---|
| `policy_id` | string | yes | `miq-roas-investigation-policy` |
| `policy_name` | string | yes | human-readable |
| `policy_type` | enum | yes | `monitoring·access·editing·alerting·action·escalation·interpretation·threshold·trend·anomaly` |
| `applies_to_kind` | enum | yes | `Metric·Dashboard·UIComponent·Threshold·Domain·Product·Action` |
| `description` | string | yes | rule in language an agent can quote |
| `rule_json` | json | rec | structured condition/action that can be evaluated |
| `severity` | enum | rec | `info·warning·critical·blocking` |
| `priority` | int | rec | conflict resolution when multiple policies apply |
| `owner_role_id` | string | rec | accountable role |
| `approval_required` | bool | yes | whether edits need approval |
| `approval_role_ids[]` | string[] | opt | roles that can approve |
| `effective_from · effective_to` | datetime | opt | time-bounded governance |
| `status` | enum | yes | `draft·active·retired·superseded` |
| `source` | enum | yes | `manual·master_config_evidence·inferred·migrated` |

### `Threshold` — *numeric / statistical boundary*
| Field | Type | Req | Purpose |
|---|---|---|---|
| `threshold_id` | string | yes | `google-shopping-roas-default-band` |
| `name` | string | yes | readable |
| `threshold_type` | enum | yes | live: `static·percentile·seasonal` *(`dynamic`/`model_based` are proposed, not live)* |
| `operator` | enum | yes | `<·<=·>·>=·between·outside_band` |
| `warning_value · critical_value · target_value` | number | opt | band boundaries |
| `green_value · yellow_value · red_value` | number | opt | manual bands |
| `percentile_baseline_json · seasonality_adjustment_json` | json | opt | P10–P90 / seasonal factors |
| `unit · grain · directionality` | enum | rec | should match the governed metric |
| `segment_filter_json` | json | opt | scope to platform/region/campaign/cohort/SKU |
| `evaluation_window` | string | rec | `last_7_days·last_4_weeks` |
| `owner_role_id` | string | rec | who owns edits |
| `source` | enum | yes | `api·seed·human·statistical·learning_projection` |
| `status` | enum | yes | `draft·active·retired·superseded` |

### `Dashboard` — *product surface & access boundary*
| Field | Type | Req | Purpose |
|---|---|---|---|
| `dashboard_id` | string | yes | `ceo-pulse`, `website-performance` |
| `display_name · route_path` | string | yes/rec | name + where it lives |
| `product_id` | string | yes | product ownership / product-level RBAC |
| `domain_id` | string | rec | domain grouping for access & search |
| `dashboard_type` | enum | rec | `executive·operational·ml·review` |
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
| `component_kind · chart_type` | enum | rec | `chart·kpi_card·table·alert_panel`; 15-value `ChartType` |
| `title · subtitle` | string | yes/opt | rendered text |
| `product_id · dashboard_id · section_id` | string | yes/rec | ownership + placement |
| `chart_registry_key` | string | rec | original registry id for round-trip |
| `query_endpoint_path` | string | opt | GET endpoint the component uses (Endpoint-as-field) |
| `metric_keys[]` | string[] | rec | metrics visualized (edges stay source of truth) |
| `formula_text · formula_explanation` | string | rec | from the registry |
| `how_to_read[] · decisions_answered[]` | string[] | rec | reader guidance + questions answered |
| `narration_text · audio_file` | string | opt | narration (558) + audio path |
| `visual_encoding_json` | json | opt | axes, series, colors, sort, aggregation |
| `data_classification` | enum | yes | can be stricter than the parent dashboard |
| `status` | enum | yes | `active·hidden·deprecated·proposed` |

### `Role` — *RBAC subject + approval + context filtering (Principal folded in)*
The RBAC brain: decides what an agent may retrieve, which paths it can traverse, what must be masked, who can edit. **No `Principal` node** — external auth gives a user id + role claims; the app maps the authenticated principal directly to a `Role` via `role_key`. Build the role graph now; attach `Person` later (a guessed org chart creates false authority).

| Field | Type | Req | Purpose |
|---|---|---|---|
| `role_id` | string | yes | `ceo`, `cmo`, `marketing_manager` |
| `role_key` | string | yes | claim expected from JWT/session/external auth — **the principal→role mapping** |
| `display_name` | string | yes | readable |
| `role_type` | enum | yes | `executive·department_lead·operator·analyst·viewer·system_agent·approver` |
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

**`IntelligenceProduct`** — top visible layer in the tenant DB. `product_id` (`miq·ciq·piq·dc·creative_iq`), `display_name`, `category` (`analytics·decisioning·creative·external`), `schema_name`+`schema_status` (`owned`/`shared` — CIQ/PIQ still on the `miq` schema), `route_prefixes[]`, `product_gate_id`, `default_sensitivity_level`.

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
Most access logic lives on the Role permission edges, not the node.

| Property | Type | Applies to | Purpose |
|---|---|---|---|
| `effect` | `allow·deny` | all | explicit deny at higher priority blocks inherited access |
| `permission` | string | all | `view·explain·traverse·edit·approve·execute·manage` |
| `priority` | int | all | higher wins on conflict |
| `scope_depth` | int | product/domain grants | how far the grant flows down (product→all domains/metrics = depth 2) |
| `allowed_fields[]` | string[] | view edges | fields that may be exposed (empty = safe defaults) |
| `masked_fields[]` | string[] | view edges | fields hidden even when the node is visible |
| `max_grain` | string | metric/dashboard | restrict detail (weekly-only, no campaign-level) |
| `row_filter_json` | json | metric/component | segment filters (platform/region/brand/SKU/campaign) |
| `condition_json` | json | all | time/environment/entitlement/approval-state conditions |
| `valid_from · valid_to` | datetime | all | temporary access / scheduled deactivation |
| `approval_required` | bool | edit/execute | role may request but not directly apply |
| `source` | string | all | `manual_admin·org_sync·migration·policy_import` |

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

Keep edge names boring and explicit. Relationship-level audit props on edges: `source_profile_id`, `confidence` (inferred), `valid_from`/`valid_to`, `status`. **Edges removed by the merges** are noted in §8.

| Edge | From → To | Meaning | Key props |
|---|---|---|---|
| `HAS_DOMAIN` | IntelligenceProduct → Domain | product/domain tree | `primary·source` |
| `PARENT_OF` | Domain → Domain | domain tree | — |
| `PART_OF_PRODUCT` | Metric/Dashboard/UIComponent → IntelligenceProduct | top-layer ownership | `primary·confidence` |
| `BELONGS_TO` | Metric/Dashboard → Domain | business grouping | `confidence·source` |
| `ROLLS_UP_TO` | Metric → Metric | channel→platform→blended aggregation (recovers cross-scope grouping) | `aggregation_method·lag·confidence` |
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
| `CAUSES` | Metric → Metric | approved causal relation (evidence-backed, V2 ledger) | `edge_key·confidence·lag_hours_min/max` |
| `CAN_ACCESS_PRODUCT` | Role → IntelligenceProduct | tier-1 product gate | `effect·permission·scope_depth·masked_fields` |
| `CAN_VIEW` | Role → Domain/Metric/Dashboard/UIComponent | tier-2 read | `effect·allowed_fields·masked_fields·max_grain·row_filter_json` |
| `CAN_EDIT` | Role → Metric/Policy/Threshold | edit gate | `effect·approval_required·priority` |
| `CAN_APPROVE` | Role → Policy/Threshold/Metric/Action | approval authority | `effect·approval_limit_json·priority` |
| `INHERITS_FROM` | Role → Role | RBAC permission inheritance (keep separate from org reporting) | `priority·source` |
| `REPORTS_TO` | Role → Role | org/social escalation | `relationship_type·source` |

---

## 7. Live enums + type-audit summary

### Authoritative live enums (from `openapi.json`)
Use these verbatim to constrain properties.
- `ValueFormat`: `number · currency · percentage · decimal`
- `ChartType` (15): `line · area · bar · horizontal_bar · grouped_bar · pie · donut · sankey · heatmap · table · sparkline · scatter · treemap · gauge · funnel`
- `ThresholdType`: `static · percentile · seasonal` *(`dynamic`/`model_based` = proposed, not live)*
- `ThresholdDirection`: `higher_is_better · lower_is_better · target_is_best`
- `MetricCategory` (14): `advertising · revenue · traffic · email · customer · sms · google_ads · meta_ads · efficiency · comparison · financial · marketing · product · operational`
- `MetricSource` (4): `ga4 · google_ads · meta_ads · klaviyo` — **but `source_set[]` is a free list** (Magento, `linkedin_ads` also appear)

Graph-derived vocab (from `rare_seeds`): scope `blended·store·ecom·web·ml·google·meta·klaviyo` · causal_role `outcome·mediator·controllable·constraint·external·ml_output·untyped` · aggregation `level·rate·avg·sum·ratio·median`.

### Type corrections (after merging the data)
Checking the merged fields against real `rare_seeds` values surfaced genuine type fixes:

1. **`causal_role_confidence`: `number` → enum `low·medium·high`.** Source `type_confidence` is categorical (`high`), not a 0–1 float. The numeric edge `confidence` (0–1, from the evidence fold) is a **different** thing and lives on `CausalRelation` (Part II).
2. **Booleans encoded as strings.** `is_model_output`, `is_derived`, `keep` arrive as `yes`/`no` — convert to real **bool** at ingestion.
3. **Pipe-delimited lists → `string[]`.** `source_set`, `mart_sources`, `source_dashboards`, `dimensions` arrive as a single `|`-delimited string — split at ingestion.
4. **Missing scalar `source`.** Add `source` enum `single·multi` (distinct from `source_set[]`).
5. **Nullable fields.** `availability`, `n_periods`, `formula_text`, `dimensions` are frequently empty (`—`); model as nullable. `n_periods` is **int-or-null**.
6. **`source_set` exceeds the `MetricSource` enum** (e.g. `linkedin_ads`) — keep a free `string[]`, do **not** constrain to the 4 values.
7. **`scope` is one value.** Store it as `scope_key`; derive `scope_level`.

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

Of 877 GET ops, only the **business plane** becomes graph nodes. Edges are built by us, not imported from the legacy ontology.

| Pattern | Action | Why |
|---|---|---|
| `GET /{dash}/metrics/{id}` | **promote metric** | card / current-value surface |
| `GET /{dash}/charts/{id}` | **promote UI / series** | chart / time-series |
| `GET /{dash}/` | promote dashboard data | dashboard surface |
| `/master-config/**` · `/master/**` | **config evidence only** | control-plane; may seed Policy/Threshold, never a metric |
| `/auth/**` · `/admin/**` · `/settings/**` | **exclude** | access/admin control plane |
| `/health · /docs · /redoc · /openapi.json` | exclude / APISource metadata | observability & review surfaces |
| `POST/PUT/PATCH/DELETE` | exclude from harvest | may later become a governed Tool/Action |

**Pipeline:** acquire spec (live preferred, checked-in fallback, hash it) → deterministic exclusion → endpoint families (method+path+response-sig+role) → classify once per spec hash → human review → deterministic harvest → **proposals only** → arbitration writes (dedupe by canonical identity) → completeness report → incremental (reclassify only changed families; deprecate, never delete). **Missing formulas are fine** — keep endpoints/charts, set `formula_status=unknown`.

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
*(Dropped vs the old target: `tenant_id`, `metric_concept`, `endpoint_id` constraints. The per-tenant DB means no composite tenant keys are needed.)*

---

## 11. V1 build order

1. **Inventory** — `IntelligenceProduct`, `Domain`, `Metric` (merged: concept + endpoints + source/lineage), `Dashboard`, `UIComponent`. Product-ownership + dashboard/metric mapping.
2. **RBAC** — `Role` nodes + product/domain/dashboard/metric grants (permission edges). Role-filtered context-pack query.
3. **Governance** — attach `Policy` and `Threshold` to metrics; field-level edit gating.
4. **Causal layer (the point)** — `CAUSES`/`INFLUENCES`/`CORRELATES_WITH`; import `rare_seeds`/legacy edges as priors *(the 4 correlation edges enter as `CORRELATES_WITH`, never auto-promoted)*; upstream/downstream/path queries.
5. **Defer to V2** — memory/learning (Part II), the reified evidence ledger, governed-proposal writes at scale.

V1's success metric is *"N evidence-backed `CAUSES`/`INFLUENCES` edges with `evidence_mass > k`"*, not *"N nodes ingested."*

---

# Part II — Full vision (V2+)

The V1 above is deliberately smaller than the full target. The memory/learning/governed-write machinery below is what makes the graph *improve* — defer it until V1's spine, RBAC, governance, and a real causal layer are live, or memory will pollute causal truth.

## 12. Memory & learning families

| Node | Role | Key properties |
|---|---|---|
| `Thoughtlet` | atomic observation anchored to a metric | `observed_at · value · baseline_value · delta_pct · severity · status(raw→questioned→concern→composed→dismissed)` |
| `DecisionCapsule` | a decision's full life (episodic memory) | `capsule_id · decision_class · status(proposed→in_approval→executed→monitoring→reconciled) · expected_upside_json · graph_version_id · packet_uri` |
| `MonitoringContract` | decision-level monitoring promise | `expected_outcome_json · primary_metrics[] · guardrail_metrics[] · review_at · status` |
| `WakeCondition` | time/event hook that rehydrates a capsule | `condition_type(time·event·threshold·guardrail·data_quality) · expression · severity` |
| `EvidenceEvent` | **append-only — the source of confidence** | `edge_key · tier · direction(supports·refutes) · weight · effect_size · p_value · lag_hours · source_id` |
| `CausalRelation` | reified causal edge (Neo4j can't hang rels off rels) | `edge_key · from/to_metric_uid · effect_direction · lag_hours_min/max · confidence · evidence_mass · alpha · beta · is_deterministic · review_status` |
| `LearningCandidate` | proposed learning (feedback/outcome) | `origin · classification · projection_type · status` |
| `PromotedMemory` | governed learning allowed to project | `memory_type · projection_target_type · valid_from/to` |
| `GraphChangeProposal` | **write-safety layer — nothing mutates the graph directly** | `proposal_type · target_label · target_id · proposed_payload_json · before_snapshot_json · diff_json · review_status(proposed→approved→applied)` |
| `GraphVersion` | snapshot lineage for time-travel | `created_at · node_count · edge_count · change_summary` |
| `IngestionRun` | audit envelope for one pipeline run | `source_type · discovered/proposal/promoted/excluded counts` |

Also deferred: `Action` / `Tool` (proposed-then-executed business actions + their sanctioned callables), `ApprovalRule` (the approval "court"), `Formula` and `Dimension` as standalone nodes, `APISource` / `SourceProfile` / `DataAsset` (full ingestion lineage), `Person` (real auth/HR users).

## 13. Confidence — derived from an evidence ledger, never typed in

```text
α = 0.5, β = 0.5                      # Jeffreys prior
for each supporting EvidenceEvent: α += weight
for each refuting   EvidenceEvent: β += weight
confidence    = α / (α + β)
evidence_mass = α + β                 # 0.8 from one LLM guess ≠ 0.8 from forty outcomes
```

| tier | weight | note |
|---|---:|---|
| `formula` | pinned 1.0 | deterministic edges sit outside the fold |
| `prior` / `llm` | 1 | imported / hypothesis — must be reviewed |
| `observational` | 2–5 | scaled by effect size, sample, FDR, lag |
| `quasi_experimental` | 5 | natural experiment / diff-in-diff |
| `interventional` | 8 | an approved action moved a lever, result followed |
| `human` | 10 | domain-expert confirmation |
| `outcome` | 8 | monitoring-contract predicted-vs-actual |

**Traversal path score** `= Π(edge.confidence) × Π(lag_plausibility) × min(data_quality) × role_visibility_factor`. Always return `confidence` **and** `evidence_mass`. This numeric `confidence` is the edge-level value — **distinct** from the Metric's categorical `causal_role_confidence` (the classifier's `low/medium/high` self-assessment).

## 14. Where this converges with / diverges from Codex

**Converged (this revision adopts Codex's leaner stance):**
- **No `Tenant` node** with per-tenant databases — we now agree.
- **Six-core V1** (`Metric`, `Policy`, `Threshold`, `Dashboard`, `UIComponent`, `Role`) + small supporting nodes — adopted as Part I.
- **`Principal` collapses into `Role`** via `role_key`; rich permission-edge model adopted.

**Diverged (kept from the Claude analysis):**
1. **Spec-hash corrected** — live and checked-in OpenAPI are structurally identical but **not** byte-for-byte (Codex said they matched). Mark `APISource.status=cached`, compare a normalized hash.
2. **Enum precision** — use live enums verbatim; `dynamic`/`model_based` thresholds are proposed, not real.
3. **Frame the build around edges** — `rare_seeds` proves nodes are easy and edges are the gap; V1 success is measured in evidence-backed edges, not node count.
4. **Lazy materialization survives into V2** — when `MetricConcept`/`CausalRelation` return as nodes, create them only when they earn their keep (a concept spanning ≥2 scopes; an edge bearing evidence).

## 15. Re-promotion notes

The V1 merges are reversible. If V2 needs cross-metric operational queries, re-promote:
- **`Connector`** → when you want one connector's `sync_frequency`/`snowflake_schema`/`azure_dual_write`/`status`/`freshness_sla_hours` shared and queryable across all metrics it feeds. Restore `SOURCED_FROM`/`PROVIDED_BY`/`CONNECTS_TO`.
- **`Endpoint`** → when ingestion needs `endpoint_role`/`harvest_decision`/`control_plane`/`response_schema_hash` as graph state. Restore `CURRENT_VALUE_ENDPOINT`/`SERIES_ENDPOINT`/`CHART_DATA_ENDPOINT`.
- **`MetricConcept`** → when a concept needs its own facts, versioning, or governance independent of any one scoped metric. Restore `INSTANCE_OF`.

---

## Appendix — quick reference

- **API:** TW Analytics API 1.0.0 · 902 paths · 877 GET / 32 POST / 6 PUT / 3 PATCH / 5 DELETE · 463 schemas.
- **Connectors:** GA4 (`GA4`), Google Ads (`GOOGLE_ADS`), Klaviyo (`KLAVIYO_RAW_COPY`), Magento (`magento`, needs tunnel), Meta Ads (`META_ADS`) — Snowflake + Azure dual-write.
- **Products:** `miq` Marketing IQ · `ciq` Customer IQ · `piq` Product IQ *(CIQ/PIQ on `miq` schema)* · `dc` Decision Canvas · `creative_iq` Creative IQ *(external)*.
- **Chart registry:** 646 entries, all with formula/explanation/how_to_read/decisions_answered/audio.
- **rare_seeds:** 355 nodes, **4 edges** — the gap this project closes.
- **Tenancy:** one Neo4j **database per tenant** — no `Tenant` node, no `tenant_id` field.
- **Companion:** `thoughtwire-kg-schema-claude.html` (visual blueprint).
