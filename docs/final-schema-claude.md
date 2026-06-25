# ThoughtWire Causal Knowledge Graph — Final V1 Schema (Claude · master-config-free)

Date: 2026-06-14 · DB target: **Neo4j** · One database **per tenant** · Scope: **lean V1** · Companion: `final-schema-claude.html`

> **What this file is.** The authoritative V1 schema for ThoughtWire's "business body" — the causal knowledge graph an agent wakes into. It **supersedes** both prior finalised drafts (`finalised-graph-schema-claude.md` and `finalised-graph-schema-codex.md`). It corrects the mistakes in the Codex draft (§1), re-roots the graph on a single **`Business`** node with a **tri-axis `Domain` ∥ `IntelligenceProduct` ∥ `Platform` spine** (§2), denormalizes domain/product/platform data onto `Metric` (§3), **eliminates every dependency on `master-config` endpoints** (§8), and adds a **seniority + social-graph RBAC model fed by an adaptive Org Graph Ingestion Engine** (§5). This revision also **cross-pollinates the parallel `final-schema-codex.md`** (§1) — adding the `Platform` axis, richer `Business` decision-context, governance-shaped `Policy`, and full edge/ingestion provenance.
>
> Grounded against the live `thoughtwire-frd.md`, `openapi.json` (902 paths · 877 GET · 463 schemas), `chart-registry.json` (646 entries), and the `rare_seeds` pilot profile (355 nodes · 4 correlation edges).

---

## 0. Provenance, verdict, and the four locked decisions

### Verdict

Both finalised drafts were close, but each carried a structural flaw. The **Codex draft** is cleanly written but **sources its identity, edges, and governance from `master-config` endpoints** and **drops `IntelligenceProduct`/`Domain` as nodes** — both disqualifying here. The **Claude draft** keeps the spine nodes and the rich field/type modelling, but **roots the spine on `IntelligenceProduct` (product-first)**, which **contradicts the FRD**, and it lacks a `Business` root and a real seniority/social RBAC model. This document keeps the Claude backbone, fixes its topology to match the FRD, removes all master-config coupling, and adds the org-graph RBAC the FRD implies but never specifies.

### FRD grounding that drove the decisions

- The FRD organizes the enterprise as **"Columns as Silos — separate functions, one enterprise structure: Sales, Marketing, Finance, Operations, Supply Chain, HR, Product, Customer, Data/IT."** Decisions and approval paths are **derived by domain owner** (e.g. *Marketing Director → Creative Lead → Finance → Execution Owner*). **The FRD never mentions the IQ products.** → Domain is a first-class organizing axis.
- **Dashboards / metrics / charts are fully discoverable without `master-config`**: 487 per-dashboard business endpoints (`/{dashboard}/metrics/{id}`, `/{dashboard}/charts/{id}`, `/{dashboard}/metadata`), and all **646** chart-registry entries already carry `dashboard_id`.
- **All Threshold/Policy CRUD lives *only* under `master-config`**; outside it there is only alert *visualization*. → Governance nodes are **defined now, populated later** (never from master-config).
- The API exposes **no org-hierarchy / reporting data**. → The social graph must be **built by us**, by an adaptive engine.

### The four locked decisions

| # | Decision | Choice (locked with stakeholder) |
|---|---|---|
| 1 | **Graph root / topology** | **Dual equal spine.** A single `Business` root; `Domain` and `IntelligenceProduct` are **both** first-class children; every `Metric` links to **both** (`BELONGS_TO_DOMAIN` + `PART_OF_PRODUCT`). No single primary spine; decision/approval logic stays domain-owner-keyed per FRD. |
| 2 | **Policy & Threshold** | **Define now, populate later.** Full node definitions + OpenAPI-shaped enums are in the schema, but **left unpopulated in V1**. Instances are wired only when a trusted **non-master-config** source exists. |
| 3 | **RBAC access model** | **Clearance + domain branch.** VIEW iff `role.seniority_rank ≥ resource.min_level` **AND** the resource sits in the role's domain/reporting branch (or has an explicit cross-grant). EDIT/APPROVE is a separate gate governed by FRD owner/approval authority. The social/org graph is built and maintained by a separate **Org Graph Ingestion Engine** (LLM-based, adaptive to company size, editable). |
| 4 | **Deliverables** | This `.md` + a polished visual-blueprint `.html` (`final-schema-claude.html`), content-identical. |

### Per-tenant database — no `Tenant` node, and `rare_seeds` is not a tenant

**Each tenant gets its own Neo4j database.** Tenant identity is **database/runtime context**, not a business entity: there is **no `Tenant` node** and **no `tenant_id` field** on any graph node. `rare_seeds` was the **pilot client** whose Snowflake database happened to be named `DB_RARE_SEEDS`; it is used here only as a **field-source profile**, never as a tenancy concept.

### V1 node labels (10)

`Business` · `Domain` · `IntelligenceProduct` · `Platform`☆ · `Metric` · `Dashboard` · `UIComponent` · `Policy`★ · `Threshold`★ · `Role`
*(★ defined now, populated later — §4. ☆ thin, lazily materialized — §4: the schema defines it and platform data is denormalized on `Metric`; the node is built when platform-level traversal is actually needed.)*

---

## 1. Mistakes & leftovers in the Codex draft (and Claude self-corrections)

The user asked specifically for an analysis of what was wrong/left over in the Codex draft. Every item below is verified against the live data.

### Codex drawbacks corrected here

| # | Codex position | Why it's a mistake here | Correction in this doc |
|---|---|---|---|
| 1 | **`master-config` is the primary source** for edges (`knowledge-graph/relationships`), identity (`/config/metrics` central library), and governance (`/config/thresholds`, `ontology/policies`). Codex §7 even calls the relationship endpoint *"not optional… primary deterministic edge source."* | All of these paths are under `master-config`, which is treated as **stale**. Building identity/edges/governance on them yields wrong results. | **All `master-config` sources removed.** Identity, edges, dashboards, and governance are sourced without it (§3, §8). |
| 2 | **Drops `IntelligenceProduct` and `Domain` as nodes** (compact 6-label graph; product/domain become fields + scope arrays). | Contradicts the stakeholder requirement *and* the FRD's domain-centric decision/approval flow. | **Both kept as nodes** under a `Business` root (§2, §4), *and* denormalized as fields on `Metric` (§3). |
| 3 | **Canonical-first metric identity depends on the master-config central metric library.** | The library is a master-config source → unavailable. | **Master-config-free identity** (§3): `metric_uid`/`canonical_id`/`metric_id`, grouped via `concept_key` + `ROLLS_UP_TO`; dashboard-local namespacing as fallback. |
| 4 | **Flat RBAC** — `effect`/`permission`/`priority` only; no seniority, no social graph, no clearance comparison. | The stakeholder wants seniority-based access where subordinates can't see a manager's features and the manager can't see above their level. | **Seniority + social-graph RBAC** with numeric clearance and an adaptive org-graph engine (§5). |
| 5 | **Per-action edge labels** (`CAN_VIEW_METRIC`, `CAN_VIEW_DASHBOARD`, `CAN_EDIT_THRESHOLD`…). | Label explosion; non-uniform queries. | **Generic labels + `permission` prop** (`CAN_VIEW`, `CAN_EDIT`, `CAN_APPROVE`) (§5, §6). |
| 6 | **Acceptance criteria (§13) explicitly ban product/domain labels.** | Directly conflicts with the chosen topology. | Acceptance criteria rewritten around the tri-axis spine (§10). |
| 7 | **Treats `master-config/config/knowledge-graph/relationships` as mandatory.** | Same as #1 — it is master-config. | Causal layer is **built from evidence, from scratch** (§8); no relationship-endpoint import. |

### One Codex idea kept

**Evidence scoring (the Beta fold).** Codex §8 derives edge `confidence` from an evidence ledger (`α/(α+β)`, `evidence_mass = α+β`) rather than letting it be typed in. That is the right mechanism and is carried forward (§8), with the ledger itself deferred to V2.

### Cross-pollinated from the parallel `final-schema-codex.md` (this revision)

The Codex track later produced a strong parallel doc (`final-schema-codex.md`). After a full comparison, these Codex ideas were **adopted here** (the rest of Claude's stance — clearance RBAC, generic permission edges, no Dashboard→UIComponent edge — is kept):

| Adopted from Codex | Where |
|---|---|
| **`Platform` as a thin, lazily-materialized 3rd axis node** (`Business ─USES_PLATFORM▶ Platform ─SOURCES/ACTIVATES▶ Metric`), plus platform cache fields on `Metric` | §2, §3, §4, §6 |
| **Governance-shaped `Policy`** — `policy_type`, `applies_to_kind`, `effect_json`, `review_state` blended onto the OpenAPI condition fields | §4 |
| **Richer `Business` decision-context** — strategic intent, north-star metrics, operating constraints, risk posture, currency/timezone/fiscal | §4 |
| **Data-detail discipline** — `review_state` (distinct from lifecycle `status`), `last_verified_at`, and a source-of-truth-vs-cache reconciliation rule | §4 |
| **Edge & ingestion provenance** — `source_kind`/`source_ref`/`source_confidence`/`created_by` on edges; `ESCALATES_TO`/`CAN_DELEGATE_TO`; proposal-payload shape + source-priority table | §6, §8 |
| **Richer `Threshold` types** — `sla·budget` types, `percent_change·z_score` operators | §4, §7 |
| **⊕ Multi-axis cardinality (this revision)** — `Metric.product_ids[]`/`domain_ids[]` and `Dashboard.domain_ids[]` are **arrays, not scalars** (one canonical metric sits on several products/domains); plus `display_name`/`description`/`aliases[]`, surface caches `dashboard_ids[]`/`component_ids[]`, and `domain_owner_role_keys[]` | §3, §4 |
| **⊕ Richer domain edges (this revision)** — `CONTEXTUALIZES` / `GOVERNS` (Domain → Metric) distinguished from the `BELONGS_TO_DOMAIN` spine; plus `Domain.decision_scope_summary`/`approval_policy_summary`, `IntelligenceProduct.owner_role_id`, `Policy.severity:blocking`, `Threshold.explanation` | §4, §6 |

**Not adopted (deliberate divergence):** Codex's per-action permission labels (`CAN_VIEW_METRIC`…) — Claude keeps generic `CAN_VIEW` + `permission` (§1 above); Codex's `CONTAINS_COMPONENT` Dashboard→UIComponent edge — Claude keeps composition as the `UIComponent.dashboard_id` FK (§4); Codex's "rank is never sufficient" RBAC — Claude keeps clearance + branch auto-visibility (§5).

### Claude-draft self-corrections (the other finalised draft)

| Claude draft had | Fixed to |
|---|---|
| **Product-first spine** `IntelligenceProduct → Domain → Metric` | **FRD-aligned tri-axis spine** under a `Business` root (§2) |
| **No `Business` root** | `Business` is the single root node (§4) |
| **No seniority / social RBAC** (two-tier role gates only) | Clearance + domain-branch model + org-graph engine (§5) |
| Insisted *"causal layer from scratch, no relationship catalog"* for ontological reasons | Same outcome (from-scratch), now also **required** because the relationship source is master-config (§8) |

---

## 2. V1 graph shape

A single **`Business`** root anchors a **tri-axis, equal spine**: `Domain` (the FRD's functional columns), `IntelligenceProduct` (the IQ apps), and `Platform` (the source/action vendor systems — thin & lazily materialized) are all direct children, and every `Metric` links across all three. Governance and RBAC ride as overlays. The causal model is a temporal DAG layered on the metric hub.

```text
                                        ┌─────────────┐
                                        │  Business   │   single root (one per tenant DB)
                                        └─┬────┬────┬─┘
                       HAS_DOMAIN ────────┘    │    └──────── USES_PLATFORM
                                   ┌───────────┘ HAS_PRODUCT
                                   ▼            ▼              ▼
                          ┌────────────┐ ┌──────────────┐ ┌──────────────┐
                          │   Domain   │ │ Intelligence │ │ Platform ☆   │  tri-axis (equal)
                          │ finance·   │ │ Product      │ │ ga4·google_  │  ☆ thin / lazily
                          │ marketing· │ │ miq·ciq·piq· │ │ ads·meta·    │     materialized
                          │ operations │ │ dc·creative_ │ │ klaviyo·     │
                          │            │ │ iq           │ │ snowflake    │
                          └─────┬──────┘ └──────┬───────┘ └──────┬───────┘
         BELONGS_TO_DOMAIN      │   PART_OF_PRODUCT│  SOURCES / ACTIVATES│
                                ▼                 ▼                 ▼
                          ┌──────────────────────────────────────────────┐
                          │                   Metric                     │  hub (denormalizes
                          │  domain_ids[]+product_ids[]+platform_ids[]   │  domain + product +
                          │  + source/platform fields)                   │  platform onto itself)
                          └──────────────────────────────────────────────┘
   CAUSAL FABRIC (temporal DAG)        SURFACE OVERLAY                GOVERNANCE OVERLAY (defined, empty in V1)
   m ─CAUSES(lag)▶ m                   UIComponent ─VISUALIZES▶ Metric  Metric ─HAS_THRESHOLD▶ Threshold★
   m ─INFLUENCES▶ m                    Metric ─SHOWN_ON▶ Dashboard       Metric ─GOVERNED_BY▶ Policy★ ─ENFORCES_THRESHOLD▶ Threshold★
   m ◀CORRELATES_WITH▶ m               UIComponent ▸dashboard_id▸ Dashboard  (composition = FK field, not an edge)

   PLATFORM / LINEAGE (thin, lazy)     RBAC OVERLAY (seniority + social graph)
   Platform ─SOURCES▶ Metric            Role ─REPORTS_TO / ESCALATES_TO / CAN_DELEGATE_TO▶ Role   Role ─INHERITS_FROM▶ Role
   Platform ─ACTIVATES▶ Metric (future) Role ─CAN_ACCESS_PRODUCT / CAN_ACCESS_DOMAIN / CAN_ACCESS_PLATFORM / CAN_VIEW / CAN_EDIT / CAN_APPROVE▶ (…)
   (edge is truth; Metric caches it)    VIEW gate = (role.seniority_rank ≥ resource.min_level)  AND  resource ∈ role's domain/product/platform branch
```

### The five questions the graph must answer for any node
1. **Upstream** — what influences this? → `CAUSES`/`INFLUENCES` in-edges (with lag).
2. **Downstream blast radius** — what does this influence? → out-edges with lag.
3. **Policy & threshold** — what governs it, what's the breach line? → `GOVERNED_BY`, `HAS_THRESHOLD` (node shapes defined V1; instances V1.x+).
4. **Confidence** — how strong is each relationship? → edge `confidence`, derived from the evidence Beta fold (§8).
5. **Who may see/act** — what context is allowed, by whom? → seniority + domain-branch RBAC (§5).

---

## 3. The `Metric` node (hub)

`Metric` is the hub. Identity uses the three-ID strategy **without any master-config library**. Per the stakeholder request, it **denormalizes domain, product, and platform/source data as flat fields** (the `Domain` and `IntelligenceProduct` nodes remain the source of truth; these fields are fast-read denormalizations). `★` marks a **corrected type** (§7). `⊕` marks a field/edge **merged in from the parallel `final-schema-codex.md`** in this revision because it materially matters — chiefly **multi-axis cardinality** (`product_ids[]`/`domain_ids[]` are arrays, not scalars: one canonical metric can sit on several products and be contextualized/owned by several domains), plus identity (`display_name`/`aliases[]`) and surface caches. Flat, indexable fields throughout; JSON only for cached summaries.

> **No `tenant_id`** on any node — the database is the tenant boundary.

### Identity & semantics — *MetricConcept folded in, no master-config library*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `metric_uid` | string | yes | app `metric:<scope>:<base>` | Neo4j identity, e.g. `metric:google-shopping:roas` |
| `canonical_id` | string | yes | derived (no master-config) | cross-source business id, `google-shopping-roas` |
| `metric_id` | string | yes | rare_seeds `node_id` / dashboard slug | API/local slug, `roas`; dashboard-local fallback `<dashboard>-<id>` |
| `display_name` ⊕ | string | yes | chart registry / endpoint metadata | human-readable metric name (`Blended ROAS`) — **adopted from Codex** |
| `description` ⊕ | string \| null | rec | manual / endpoint docs | agent-facing explanation — **adopted from Codex** |
| `concept_key` | string | rec | rare_seeds `metric_base` | semantic concept group (`roas`) — replaces MetricConcept |
| `concept_name` | string | rec | manual | "Return on Ad Spend" |
| `synonyms[]` | string[] | opt | manual | resolution aliases |
| `aliases[]` ⊕ | string[] | rec | routes, charts, endpoints, review | raw slugs / old names / UI labels for resolution — **adopted from Codex** |
| `unit_family` | enum | rec | manual | `currency·ratio·percent·count·duration·score` |
| `default_direction` | enum | rec | manual | `higher_is_better·lower_is_better·target_is_best` |

### Classification — *domain + product denormalized here (per request)*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `product_ids[]` ★⊕ | string[] | yes | rare_seeds `product` + `PART_OF_PRODUCT` edges | owning IQ products — **denormalized; `IntelligenceProduct` nodes are source of truth. Array, not scalar: one canonical metric can be surfaced by several products (corrected via Codex).** |
| `product_names[]` ⊕ | string[] | opt | IntelligenceProduct | denormalized display names (`Marketing IQ`) |
| `domain_ids[]` ★⊕ | string[] | yes | rare_seeds `department` + domain edges | business functions — **denormalized; `Domain` nodes are source of truth; orthogonal to product. Array, not scalar: a metric can be contextualized by marketing yet owned by finance (corrected via Codex).** |
| `domain_names[]` ⊕ | string[] | opt | Domain | denormalized display names (`Marketing`) |
| `domain_owner_role_keys[]` ⊕ | string[] | opt | domain ownership edges | cached domain owner roles — **adopted from Codex** |
| `scope_key` | string | yes | rare_seeds `scope` | `blended` / `google-shopping` |
| `scope_level` ★ | enum | rec | **derived** from `scope` | `global·platform·channel·dashboard·campaign·product·customer·model` |
| `metric_base` | string | yes | rare_seeds `metric_base` | base concept: `revenue·orders·roas·cvr` |
| `category` | enum | rec | openapi `MetricCategory` (14) | see §7 |
| `aggregation` | enum | rec | rare_seeds `aggregation` | `level·sum·avg·rate·ratio·median` |
| `value_format` | enum | rec | openapi `ValueFormat` | `number·currency·percentage·decimal` |
| `granularity` | enum | rec | openapi `Granularity` | `daily·weekly·monthly·quarterly` |
| `measurement_type` | enum | rec | manual | `direct·derived·modeled·forecast·status` |

### Platform / source / lineage — *vendor `Platform` folded in as fields **and** cache of the `Platform` node*
The `Platform` node (§4) is the source of truth **when materialized**; these fields are the fast-read cache (and the sole lineage store before the node exists). **If a `SOURCES` edge and a cache field disagree, the edge wins and the cache is rebuilt** (§4 data-detail standard). `☆` marks a platform-cache field.

| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `source` ★ | enum `single·multi` | rec | rare_seeds `source` | scalar source cardinality |
| `source_set[]` ★ | string[] (split `\|`) | rec | rare_seeds `source_set` | **raw vendor-platform slugs** — `ga4·google_ads·meta_ads·klaviyo·magento·linkedin_ads`; **free list, broader than the 4-value `MetricSource` enum** |
| `platform_ids[]` ☆ | string[] | rec | cache from `SOURCES` edges | normalized `Platform` ids (rebuildable from edges) |
| `platform_names[]` ☆ | string[] | opt | cache from `Platform` | platform display names |
| `platform_types[]` ☆ | string[] | opt | cache from `Platform` | platform classes (`analytics·ads·crm·warehouse·…`) |
| `primary_platform_id` ☆ | string \| null | opt | derived from `source_set`/`SOURCES` | dominant source platform (renamed from `platform_primary`) |
| `connector_ids[]` | string[] | opt | resolved from `source_set` | normalized connector refs |
| `mart_sources[]` ★ | string[] (split `\|`) | opt | rare_seeds `mart_source` | warehouse lineage, `DB_RARE_SEEDS.MARTS.*` |
| `platform_data_quality_json` ☆ | json | opt | ingestion quality jobs | per-platform quality details |
| `data_freshness_by_platform_json` ☆ | json | opt | ingestion freshness jobs | per-platform last-seen / freshness |
| `primary_grain` | enum | rec | rare_seeds `grain` | `daily·weekly·monthly·campaign·product·customer` |
| `grain_source` | string | opt | rare_seeds `grain_source` | `dbt` |
| `dimensions[]` ★ | string[] \| null (split `\|`) | opt | rare_seeds `dimensions` | slice axes; **often null** |
| `availability` | string \| null | opt | rare_seeds `availability` | nullable |
| `n_periods` ★ | int \| null | opt | rare_seeds `n_periods` | nullable integer |

### Causal
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `causal_role` | enum | rec | rare_seeds `type` | `outcome·mediator·controllable·constraint·external·ml_output·untyped` |
| `causal_role_confidence` ★ | enum `low·medium·high` | opt | rare_seeds `type_confidence` | role-classification confidence — **categorical, not a number** |
| `is_model_output` ★ | bool | rec | rare_seeds `is_model_output` (`yes`/`no`→bool) | ML output? |
| `is_derived` ★ | bool | yes | rare_seeds `is_derived` (`yes`/`no`→bool) | computed from other metrics? |
| `formula_status` | enum | opt | registry/dbt | `explicit·parsed·unknown` |
| `formula_text` ★ | string \| null | opt | rare_seeds `formula` | readable formula, e.g. `revenue / ad_spend`; **often null** |

### Endpoints — *Endpoint node folded in (non-master-config paths only)*
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `card_endpoint` | string | rec | rare_seeds `card_endpoint` | GET current-value path (`/{dashboard}/metrics/{id}`) |
| `series_endpoint` | string | rec | rare_seeds `series_endpoint` | GET trend path (`/{dashboard}/charts/{id}`) |
| `endpoint_paths[]` | string[] | opt | openapi (business plane) | all non-master-config endpoints serving this metric |

### Surfacing + RBAC + lifecycle
| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `dashboard_ids[]` ⊕ | string[] | rec | `SHOWN_ON` / surface edges | cached dashboards showing this metric (rebuildable from edges) — **adopted from Codex** |
| `component_ids[]` ⊕ | string[] | rec | `VISUALIZES` edges | cached UI components visualizing this metric (rebuildable from edges) — **adopted from Codex** |
| `source_dashboards[]` ★ | string[] (split `\|`) | opt | rare_seeds `source_dashboards` | raw cache of `SHOWN_ON` dashboards |
| `data_classification` | enum | yes | RBAC | `public·internal·restricted·executive` |
| `min_level` ★ | int | yes | RBAC | **minimum `seniority_rank` to view** (clearance, §5) |
| `owner_role_id` | string | rec | RBAC | accountable role (drives approval path) |
| `is_kpi` | bool | rec | ingestion | headline metric? |
| `keep` ★ | bool | opt | rare_seeds `keep` (`yes`/`no`→bool) | curation flag (may collapse into `status`) |
| `status` | enum | yes | ingestion/review | `proposed·active·deprecated·blocked` |
| `data_quality_status` | enum | rec | ingestion | freshness/quality flag |
| `confidence` | number 0–1 | rec | ingestion/review | system confidence the metric was classified correctly |
| `created_at · updated_at` | datetime | yes | system | audit (no `tenant_id`) |

**Three IDs, three jobs** — `metric_uid` is the Neo4j identity; `canonical_id` is the cross-source business id; `metric_id` is the API/local slug (dashboard-local `<dashboard>-<id>` only as fallback). The same concept across surfaces is grouped by shared `concept_key` and aggregated via `ROLLS_UP_TO` — **no central master-config library is consulted**:

```text
metric:google-shopping:roas  (concept_key: roas) ─ROLLS_UP_TO▶ metric:google-ads:roas
metric:meta-overview:roas    (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
metric:google-ads:roas       (concept_key: roas) ─ROLLS_UP_TO▶ metric:blended:roas
```

### One canonical `Metric` on all three axes (worked example)

A single `Metric` is **never duplicated** per product/domain/platform — it links to each axis once and caches the ids for fast reads:

```text
(:Business {business_id:"rare-seeds"})
  ─HAS_PRODUCT▶   (:IntelligenceProduct {product_id:"miq"})  ──PART_OF_PRODUCT──    m
  ─HAS_DOMAIN▶    (:Domain {domain_id:"marketing"})          ──BELONGS_TO_DOMAIN──  m
  ─HAS_DOMAIN▶    (:Domain {domain_id:"finance"})            ──OWNS (domain owner)─ m
  ─USES_PLATFORM▶ (:Platform {platform_id:"shopify"})        ──SOURCES─▶            m
  ─USES_PLATFORM▶ (:Platform {platform_id:"google_ads"})     ──ACTIVATES─▶ (future) m
                                       m = (:Metric {metric_uid:"metric:blended:revenue"})
```

Cached fast-read fields on `m` (rebuildable from the edges above; the edge is truth):

```json
{ "metric_uid": "metric:blended:revenue", "concept_key": "revenue",
  "product_ids": ["miq", "piq"], "domain_ids": ["marketing", "finance"],
  "domain_owner_role_keys": ["cfo"],
  "platform_ids": ["shopify", "google_ads", "ga4"], "primary_platform_id": "shopify",
  "value_format": "currency", "default_direction": "higher_is_better",
  "data_classification": "internal", "min_level": 40, "status": "active" }
```

---

## 4. Other V1 core nodes

Every node carries `created_at`, `updated_at`, `status`, `review_state`, `source_profile_id`, and `last_verified_at` (no `tenant_id`). Listed below are the *distinguishing* properties.

### Data-detail standard (carried from Codex)

Every record is explicit about identity, ownership, source, and refresh state so agents never have to reverse-engineer whether a field is authoritative, cached, inferred, or pending review.

| Data area | Standard |
|---|---|
| Stable identity | a deterministic id that never changes when display names/routes change |
| Display identity | human-facing names stored separately from ids |
| Source ownership | each node/edge carries source metadata, or is created by the arbitration writer that records it |
| **`review_state`** (distinct from `status`) | harvested/inferred entities are `proposed·needs_review`; only reviewed entities become `active`. `status` is the *lifecycle* (active/hidden/deprecated/blocked); `review_state` is the *review pipeline* |
| Status over deletion | missing/retired entities become `deprecated·hidden·blocked·archived` — never deleted by ingestion |
| Cached context | denormalized arrays (on `Metric`) are read caches from edges, not independent truth |
| Audit | `created_at`, `updated_at`, `last_verified_at` on nodes and important edges |
| Sensitivity | sensitive nodes/edges declare `data_classification`/`min_level` or inherit from a policy |

**Source-of-truth vs cached fields.** Graph edges are the source of truth for traversal; `Metric` arrays are caches for fast reads. **If an edge and a cached field disagree, the edge wins and the cache is rebuilt.**

| Question | Source of truth (edge) | Cached on `Metric` |
|---|---|---|
| Which products surface this metric? | `(:Metric)-[:PART_OF_PRODUCT]->(:IntelligenceProduct)` (+ dashboard/component path) | `product_ids[]`, `product_names[]` |
| Which domains own/govern it? | `(:Metric)-[:BELONGS_TO_DOMAIN]->(:Domain)`, `(:Domain)-[:CONTEXTUALIZES\|GOVERNS]->(:Metric)`, `(:Role)-[:OWNS]->` | `domain_ids[]`, `domain_names[]`, `domain_owner_role_keys[]` |
| Which platforms source it? | `(:Platform)-[:SOURCES]->(:Metric)` | `platform_ids[]`, `platform_names[]`, `primary_platform_id` |
| Which dashboards/components show it? | `(:UIComponent)-[:VISUALIZES]->`, `(:Metric)-[:SHOWN_ON]->(:Dashboard)` | `dashboard_ids[]`, `component_ids[]`, `source_dashboards[]` |

### `Business` — *the single root (new)*
One node per tenant database; the anchor every spine hangs from and the input that parameterizes the Org Graph Ingestion Engine (§5).

| Field | Type | Req | Purpose |
|---|---|---|---|
| `business_id` | string | yes | stable identity (e.g. `rare-seeds`) |
| `display_name` | string | yes | "Rare Seeds" |
| `tier` | enum | yes | `startup·smb·mid_market·mnc` — **drives org-graph shape** (startup ≈ flat; mnc ≈ full C-suite) |
| `business_type` | enum | rec | `ecommerce·saas·marketplace·retail·services·other` — defaults for domains/metrics/policy |
| `industry` | string | opt | vertical context for the org engine |
| `primary_currency` | string | rec | default currency for financial metrics |
| `timezone` | string | rec | default reporting / freshness timezone |
| `fiscal_year_start_month` | int \| null | opt | fiscal calendar anchor (`1`–`12`) |
| `default_granularity` | enum | rec | `daily·weekly·monthly·quarterly` — default analysis grain |
| `decision_risk_posture` | enum | rec | `conservative·balanced·aggressive` — informs later decision guardrails |
| `strategic_intent_summary` | string | rec | short agent-readable statement of what the company optimizes for |
| `north_star_metrics[]` | string[] | rec | company-priority `metric_uid`s / `concept_key`s (resolved after the metric catalog exists) |
| `operating_constraints[]` | string[] | rec | business-wide constraints: cash, brand, inventory, compliance, service quality |
| `default_data_classification` | enum | rec | baseline `data_classification` for new nodes |
| `root_seniority_rank` | int | rec | rank of the top role (e.g. CEO = 100) |
| `status` | enum | yes | `active·paused·archived` |

### `Domain` — *FRD functional column (spine, equal)*
| Field | Type | Req | Purpose |
|---|---|---|---|
| `domain_id` | string | yes | `finance`, `marketing`, `operations`, `service`, `customer`, `product`, `supply_chain`, `hr`, `data_it` |
| `name` | string | yes | "Marketing" |
| `domain_type` | enum | rec | `business·technical·risk·data_quality·ml` (`data_quality` ⊕ adopted from Codex) |
| `parent_domain_id` | string | opt | domain tree (sub-domains) |
| `owner_role_id` | string | rec | accountable role (anchors the approval path) |
| `decision_scope_summary` ⊕ | string | yes | decisions this domain owns or contextualizes — **adopted from Codex (core to the decision graph)** |
| `approval_policy_summary` ⊕ | string | rec | human summary of approval expectations; machine rules live in `Policy` — **adopted from Codex** |
| `default_product_ids[]` ⊕ | string[] | rec | products commonly associated with this domain (scope cache) — **adopted from Codex** |
| `default_platform_ids[]` ⊕ | string[] | rec | platforms commonly used by this domain (scope cache) — **adopted from Codex** |
| `min_level` | int | yes | clearance floor for the domain branch (§5) |
| `data_classification` | enum | yes | `public·internal·restricted·executive` |

> The FRD's "columns as silos" (Sales, Marketing, Finance, Operations, Supply Chain, HR, Product, Customer, Data/IT) seed the initial `Domain` set; `rare_seeds.department` supplies the live values.

### `IntelligenceProduct` — *IQ app (spine, equal)*
| product_id | display | note |
|---|---|---|
| `miq` | Marketing IQ | central analytics, 50+ dashboards |
| `ciq` | Customer IQ | real product, still on `miq` schema |
| `piq` | Product IQ | real product, still on `miq` schema |
| `dc` | Decision Canvas | writes capsules/thoughts |
| `creative_iq` | Creative IQ | external, separate repo/manifest |

Fields: `product_id`, `display_name`, `category` (`analytics·decisioning·creative·external`), `description` ⊕, `schema_name`+`schema_status` (`owned`/`shared`), `route_prefixes[]`, `owner_role_id` ⊕ (accountable product owner / escalation), `default_domain_ids[]` ⊕ (domains commonly surfaced), `default_data_classification`, `min_level`.
*(⊕ `description`, `owner_role_id`, `default_domain_ids[]` adopted from Codex — product accountability + product↔domain linkage.)*

### `Platform` ☆ — *source/action vendor system (spine, equal — thin & lazily materialized)*
The third axis (adopted from Codex). A `Platform` is a vendor source/action system — GA4, Google Ads, Meta Ads, Klaviyo, Magento, Snowflake, Shopify. It is **thin and lazily materialized**: the schema defines it and platform data is denormalized on `Metric` (§3); the node is **built only when platform-level traversal is actually needed** — e.g. *"which metrics depend on GA4?"*, *"who owns Google Ads freshness?"*, *"which dashboards are hit by a degraded connector?"*, *"which roles can access a platform?"*. Until then, `Metric.source_set[]`/`platform_ids[]` + `SOURCES` edge metadata carry the lineage.

| Field | Type | Req | Source field | Purpose |
|---|---|---|---|---|
| `platform_id` | string | yes | connector/platform manifest | stable slug — `ga4·google_ads·meta_ads·klaviyo·magento·snowflake·shopify` |
| `platform_name` | string | yes | manifest | display name (`Google Ads`) |
| `platform_type` | enum | yes | manifest | `analytics·ads·crm·ecommerce·warehouse·activation·support·finance·other` |
| `connector_id` | string \| null | opt | connector manifest | runtime connector id if known |
| `connector_family` | string \| null | opt | connector manifest | `google·meta·shopify·warehouse·…` |
| `owner_role_id` | string | rec | RBAC | role accountable for platform quality/ops |
| `freshness_sla_hours` | number \| null | opt | manifest / ops policy | expected data freshness SLA |
| `supports_actions` | bool | yes | manifest | whether the platform can later execute sanctioned actions (KG flag only; no runtime) |
| `source_priority` | int \| null | opt | source policy | tie-breaker when multiple platforms claim the same concept |
| `api_base_url_ref` | string \| null | opt | manifest | **reference/key only, never a secret** |
| `data_quality_status` | enum | rec | ingestion | `good·warning·degraded·unknown` |
| `last_successful_sync_at` | datetime \| null | opt | ingestion | last known successful sync |
| `data_classification` | enum | yes | RBAC | `public·internal·restricted·executive` |
| `min_level` | int | yes | RBAC | clearance floor for platform-scoped access |
| `status` | enum | yes | arbitration | `active·degraded·deprecated·planned` |

> **`ACTIVATES` is KG-schema only.** `(:Platform)-[:ACTIVATES]->(:Metric)` records *future* action eligibility; it does **not** imply an execution runtime exists in V1 (Tool/Action runtime is V2, §11).

### `Dashboard` — *product surface & access boundary (no master-config)*
**Identity is derived entirely from `chart-registry.json` (`dashboard_id` on all 646 entries) + per-dashboard business endpoints (`/{dashboard}/`, `/{dashboard}/metadata`).** Master-config is never consulted.

| Field | Type | Req | Purpose |
|---|---|---|---|
| `dashboard_id` | string | yes | `ceo-pulse`, `website-performance` — from chart-registry / `/admin/dashboards` |
| `display_name · route_path` | string | yes/rec | name + where it lives |
| `product_id` | string | yes | owning IQ product (spine) for access & search |
| `domain_ids[]` ⊕ | string[] | rec | domains represented by the dashboard (spine) — **array, corrected via Codex** |
| `dashboard_type` | enum | rec | `executive·operational·ml·review` |
| `default_endpoint_path · metadata_endpoint_path` | string | opt | non-master-config payload + metadata endpoints |
| `audience_role_ids[]` | string[] | opt | default audience (access stays edge-driven) |
| `data_classification` | enum | yes | `public·internal·restricted·executive` |
| `min_level` | int | yes | clearance floor (§5) |
| `status` | enum | yes | `active·hidden·deprecated·proposed` |
| `source_registry` | string | opt | which file/endpoint discovered it (`chart-registry` / business endpoint) |

### `UIComponent` — *chart / KPI card / table (the chart registry, 646 entries)*
> **No `RENDERS` edge.** Dashboard composition is a **field on the component** — `dashboard_id` (the parent FK) plus `section_id` / `display_order` / `visibility`. "Components of dashboard X" is a field lookup (`MATCH (c:UIComponent {dashboard_id:X})`), not a traversal.

| Field | Type | Req | Purpose |
|---|---|---|---|
| `component_id · canonical_id` | string | yes | `alerts-config:active_rules` (the registry `canonical_id`) |
| `dashboard_id · chart_id` | string | yes | registry identity fields (100% present); `dashboard_id` is the **dashboard-composition link (FK)** |
| `section_id` | string | opt | placement section on the dashboard (migrated from the old `RENDERS` edge) |
| `display_order` | int | opt | order within the dashboard (migrated from `RENDERS`) |
| `visibility` | enum | opt | `visible·hidden·collapsed` (migrated from `RENDERS`) |
| `component_kind · chart_type` | enum | rec | `chart·kpi_card·table·alert_panel`; 15-value `ChartType` |
| `title` | string | yes | rendered text (100% present) |
| `query_endpoint_path` | string | opt | non-master-config GET endpoint the component uses |
| `metric_keys[]` | string[] | rec | metrics visualized (edges stay source of truth) |
| `formula · formula_explanation` | string | rec | from the registry (100% present) |
| `how_to_read[] · decisions_answered[]` | string[] | rec | reader guidance + questions answered (100% present) |
| `narration_text · audio_file` | string | opt | narration **(558/646 — not all)** + audio path (100% present) |
| `data_classification · min_level` | enum/int | yes | can be stricter than the parent dashboard |
| `status` | enum | yes | `active·hidden·deprecated·proposed` |

### `Policy` ★ — *defined now, populated later (blended governance + OpenAPI shape)*
Node shape **blends** Claude's OpenAPI condition fields with Codex's general governance fields, so a Policy can govern **access / interpretation / alerting / escalation / approval / action-guardrail / data-quality** — not only metric breaches. The OpenAPI enums (`PolicyCreate`, `ConditionType`, `ConditionOperator`/`ComparisonOperator`, `Severity`) are the API's general type system, **not** master-config data. **No instances are ingested in V1** (the only policy data is under master-config). Populate from human/statistical/`alerts-config` sources when available.

| Field | Type | OpenAPI alignment | Purpose |
|---|---|---|---|
| `policy_id` | string | required in KG | stable identity |
| `policy_name · description · metric_id` | string | `PolicyCreate` | human-readable + governed metric |
| `policy_type` | enum | KG ext (Codex) | `access·interpretation·alerting·escalation·approval·action_guardrail·data_quality` |
| `applies_to_kind` | enum | KG ext (Codex) | node kind governed: `Business·IntelligenceProduct·Domain·Platform·Metric·Dashboard·UIComponent·Threshold·Role` |
| `condition_type` | enum | `ConditionType` | `threshold·anomaly·trend·missing_data` |
| `condition_operator` | enum\|null | `ConditionOperator` | `lt·lte·gt·gte·eq·neq·between·outside` |
| `condition_value · condition_value_high · condition_expression` | number/string\|null | `PolicyCreate` | boundaries / complex expression |
| `evaluation_window · evaluation_frequency` | string | defaults `24h` / `1h` | cadence |
| `cooldown_hours · escalate_after_hours` | number | defaults `4` / `24` | alert + escalation timers |
| `severity` | enum | `Severity` (+KG-ext ⊕) | `critical·high·medium·low·info·blocking` (`blocking` ⊕ adopted from Codex) |
| `auto_investigate · notify_channels[]` | bool/string[] | `PolicyCreate` | wake investigation; routing |
| `effect_json` | json | KG ext (Codex) | machine-readable effect: `mask·deny·escalate·require_approval` |
| `owner_role_id · approval_required · approval_role_ids[] · priority` | mixed | KG ext | ownership, edit gating, conflict resolution |
| `effective_from/to · is_active · status · source` | mixed | KG ext | lifecycle + provenance (`source` excludes `master_config`) |
| `review_state` | enum | KG ext (Codex) | `draft·active·needs_review·retired` — review pipeline (distinct from `status`) |
| `population_status` | enum | KG ext | `defined·populated` — **`defined` in V1** |

### `Threshold` ★ — *defined now, populated later*
Shape reuses `ThresholdConfig` (raw string bands) + `StatisticalThreshold` (2σ numerics) + enums `ThresholdType`/`ThresholdDirection`/`ComparisonOperator`. **Unpopulated in V1.**

| Field | Type | OpenAPI alignment | Purpose |
|---|---|---|---|
| `threshold_id` | string | `ThresholdConfig.id` | identity |
| `metric_id · metric_name` | string | `ThresholdConfig` | governed metric |
| `threshold_type` | enum | `ThresholdType` + KG ext | OpenAPI-native `static·percentile·seasonal`; KG-ext (Codex) `warning·critical·target·anomaly·sla·budget` |
| `operator` | enum | `ComparisonOperator` + KG ext | `lt·lte·gt·gte·eq·neq·between·outside`; KG-ext (Codex) `percent_change·z_score` |
| `direction` | enum | `ThresholdDirection` | `higher_is_better·lower_is_better·target_is_best` |
| `green_value · yellow_value · red_value` | string | `ThresholdConfig` | **raw** bands (as strings) |
| `warning_value_num · critical_value_num · target_value_num` | number\|null | parser/review | **normalized** numerics |
| `avg_val · stddev_val · lower_2sigma · upper_2sigma · min_val · max_val` | number | `StatisticalThreshold` | percentile/seasonal baseline |
| `category · unit · grain · evaluation_window · segment_filter_json` | mixed | — | grouping + scope |
| `explanation` ⊕ | string \| null | KG ext (Codex) | human reason for the boundary — **adopted from Codex** |
| `owner_role_id · source · status` | mixed | — | RBAC + lifecycle (`source` excludes `master_config`) |
| `review_state` | enum | KG ext (Codex) | `draft·active·needs_review·retired` — review pipeline (distinct from `status`) |
| `population_status` | enum | KG ext | `defined·populated` — **`defined` in V1** |

### `Role` — *RBAC subject + seniority + social-graph anchor*
The RBAC brain. External auth maps an authenticated principal to a `Role` via `role_key`; the graph decides what may be retrieved. Role nodes, their `seniority_rank`, and the `REPORTS_TO` social graph are **created and maintained by the Org Graph Ingestion Engine** (§5) and are editable.

| Field | Type | Req | Purpose |
|---|---|---|---|
| `role_id` | string | yes | `ceo`, `cmo`, `marketing_manager`, `general_manager` |
| `role_key` | string | yes | claim expected from JWT/session — the principal→role mapping |
| `display_name` | string | yes | readable |
| `role_type` | enum | yes | `executive·department_lead·operator·analyst·viewer·system_agent·approver` |
| `seniority_rank` ★ | int | yes | **clearance level** — higher = more senior (CEO 100 … viewer 10); the number compared in the VIEW gate (§5) |
| `auth_role` | enum | opt | maps to OpenAPI `UserRole` where available |
| `domain_id` | string | rec | the role's **home domain** (anchors its domain branch) |
| `domain_scope_ids[] · platform_scope_ids[]` ★ | string[] | opt | additional domains / platforms the role governs (cross-grants) |
| `default_product_ids[] · default_platform_ids[]` | string[] | opt | convenience scope cache; permission edges remain source of truth |
| `max_data_classification` | enum | yes | highest `data_classification` accessible by default |
| `can_manage_rbac` | bool | yes | only a few admin roles may alter permission edges or the org graph |
| `can_create_policy · can_create_threshold · can_edit_endpoint` | bool | yes | global capabilities; still constrained by scoped edges |
| `agent_context_limit` | int | opt | max graph facts exposed to an agent for this role |
| `redaction_policy_json` | json | opt | default masking for fields/values/sources/causal paths |
| `is_engine_generated` ★ | bool | rec | `true` if created by the Org Graph Engine; `false`/edited if human-overridden |
| `status` | enum | yes | `active·disabled·deprecated` |

---

## 5. RBAC — seniority clearance + domain branch + adaptive org graph

RBAC is **graph-native** because agent context is graph-native: **build the allowed context first, then answer from it** — never run an unrestricted traversal and filter afterward.

### The VIEW gate (clearance + domain branch)

A role may **view** a resource (Metric / Dashboard / UIComponent / Domain / Product / Platform) iff **both** hold:

```text
(1) CLEARANCE     role.seniority_rank ≥ resource.min_level
(2) BRANCH        resource's domain / product / platform ∈ role's branch
                  ( role.domain_id ∪ domain_scope_ids[] ∪ default_product_ids[] ∪ platform_scope_ids[]
                    ∪ domains reachable from the role down the REPORTS_TO / Domain tree )
                  OR an explicit CAN_VIEW / CAN_ACCESS_PLATFORM {effect:'allow'} cross-grant exists
```

> **Kept, not Codex's.** This stays Claude's clearance-+-branch model (rank auto-grants within branch) — the "automatic from the social graph" behaviour. Codex's stricter "rank is necessary but never sufficient" was considered and **not adopted** (§1).

…and no explicit `CAN_VIEW {effect:'deny'}` at higher priority blocks it.

- **Subordinates can't see a manager's features.** A feature exposed at GM level has `min_level = 70`. A Manager (`seniority_rank = 50`) reporting under the GM fails clearance (1) → blocked.
- **A manager can't see above their level.** A board-only metric has `min_level = 95`. The GM (`seniority_rank = 70`) fails clearance (1) → blocked, even within their own branch.
- **Higher rank sees its level and below, within its branch.** A VP of Marketing (`rank = 80`, branch `marketing`) sees all marketing resources with `min_level ≤ 80`, but not Finance internals unless cross-granted.

### EDIT / APPROVE is a separate gate (FRD owner/approval authority)

VIEW is governed by clearance+branch. **EDIT and APPROVE are *not*** — they follow the FRD's owner/approval-rule model: explicit `CAN_EDIT` / `CAN_APPROVE` edges, `approval_required`, and the approval path derived from node `owner_role_id`s (e.g. *Marketing Director → Creative Lead → Finance → Execution Owner*). Seeing a metric never implies being able to change its threshold.

### The Org Graph Ingestion Engine (adaptive, editable)

The API has **no org-hierarchy data**, and org shape varies wildly (a startup has no CFO/CMO; an MNC has a full C-suite). So the social/org graph is produced by a **separate LLM-based service**, run as ingestion-style proposals (never a direct write):

```text
INPUT   Business.tier  +  org description (free text / HR export / headcount / titles)
            │
            ▼  (LLM digestion, scaled to tier)
PROPOSE   Role nodes  +  REPORTS_TO edges  +  seniority_rank assignment  +  domain ownership
            │            startup → flat (founder/GM wears many hats)
            │            mid-market → directors + managers
            │            mnc → CEO → C-suite → VP → director → manager → lead → analyst
            ▼
ARBITRATE one writer dedupes/validates → writes the Role subgraph (is_engine_generated = true)
            │
            ▼
EDIT      humans add/remove/re-rank roles as the org grows; edits flip is_engine_generated = false
          and are preserved on re-run (engine proposals never clobber human overrides)
```

This keeps the access model honest as the company evolves: re-running the engine after a reorg proposes deltas; human edits are sticky; `seniority_rank` and domain branches stay consistent with the live org.

> **`REPORTS_TO` (org/social tree)** and **`INHERITS_FROM` (permission inheritance)** are kept **distinct**. Reporting lines drive the *branch* test and approval escalation; inheritance drives permission roll-up. A role may inherit permissions it does not socially report through, and vice versa. Two additional social edges (from Codex) refine the graph: **`ESCALATES_TO`** (explicit incident/approval/data-quality escalation path with `sla_hours`, when it differs from the plain reporting line) and **`CAN_DELEGATE_TO`** (scoped, time-boxed delegation — `delegation_scope`, `max_duration_hours`, `requires_approval`).

### Recommended seed roles (illustrative ranks)
| Role | seniority_rank | Home domain | Typical edit power |
|---|---|---|---|
| `ceo` | 100 | (all) | approve high-impact policy/action changes |
| `cfo` | 90 | finance | edit finance thresholds, approve spend policies |
| `cmo` | 90 | marketing | edit marketing thresholds/policies, approve campaign actions |
| `general_manager` | 70 | (branch) | approve within branch; subordinates can't see GM-level features |
| `marketing_manager` | 50 | marketing | edit selected thresholds with approval |
| `analyst` | 30 | assigned | none (can propose, not apply) |
| `viewer` | 10 | assigned | none |
| `system_agent` | (acting) | (acting) | inherits the acting user's rank + reason; never above it |

### Query patterns (clearance + branch baked in)
```cypher
// 1 · Role closure — directly assigned + inherited permissions
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH (r)-[:INHERITS_FROM*0..4]->(parent:Role {status: 'active'})
RETURN collect(DISTINCT r) + collect(DISTINCT parent) AS role_scope;

// 2 · Allowed metrics for a role — clearance AND domain branch (or cross-grant), explicit deny wins in app logic
MATCH (r:Role {role_key: $role_key, status: 'active'})
OPTIONAL MATCH (r)-[:REPORTS_TO*0..6]->(:Role)-[:OWNS|OWNED_BY]->(:Domain)  // branch via org tree
WITH r, collect(DISTINCT r.domain_id) + r.domain_scope_ids AS branch_domains
MATCH (m:Metric {status: 'active'})
WHERE r.seniority_rank >= m.min_level                                       // (1) clearance
  AND ( ANY(d IN m.domain_ids WHERE d IN branch_domains)                   // (2a) branch — m.domain_ids is an array
        OR EXISTS { MATCH (r)-[g:CAN_VIEW {effect:'allow'}]->(m) } )       // (2b) cross-grant
  AND NOT EXISTS { MATCH (r)-[d:CAN_VIEW {effect:'deny'}]->(m) }
RETURN DISTINCT m.metric_uid, m.concept_name, m.product_ids, m.domain_ids, m.min_level;

// 3 · Agent answer context for one metric — fetch only allowed facts before the LLM sees context
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (m:Metric {metric_uid: $metric_uid, status: 'active'})
WHERE r.seniority_rank >= m.min_level
OPTIONAL MATCH (m)-[:HAS_THRESHOLD]->(t:Threshold {population_status:'populated'})
OPTIONAL MATCH (m)-[:GOVERNED_BY]->(p:Policy {population_status:'populated'})
OPTIONAL MATCH (c:UIComponent)-[:VISUALIZES]->(m)
OPTIONAL MATCH (up:Metric)-[:CAUSES]->(m) WHERE r.seniority_rank >= up.min_level
RETURN m, collect(DISTINCT t) AS thresholds, collect(DISTINCT p) AS policies,
       collect(DISTINCT c) AS components, collect(DISTINCT up.metric_uid) AS upstream;

// 4 · Safe edit check — VIEW never implies EDIT; require explicit edit grant
MATCH (r:Role {role_key: $role_key, status: 'active'})
MATCH (t:Threshold {threshold_id: $threshold_id})
MATCH (r)-[grant:CAN_EDIT {effect:'allow'}]->(t)
RETURN t.threshold_id, grant.approval_required AS approval_required;
```

### Example role scenarios
| Question | Caller | Expected KG behavior |
|---|---|---|
| What about blended ROAS? | `cmo` (rank 90, marketing) | full marketing context — formula, endpoints, dashboards, visible upstream marketing metrics |
| Why did contribution margin drop? | `marketing_manager` (rank 50) | marketing-visible context only; if finance margin has `min_level 90`, report a restricted finance dependency without exposing its name/value |
| Open the GM-level pacing board | `analyst` (rank 30) | clearance fail (board `min_level 70`) → not shown |
| See the board-only revenue forecast | `general_manager` (rank 70) | clearance fail (`min_level 95`) → masked even though it's in-branch |
| Edit the critical threshold for Google Ads ROAS | `analyst` | deny mutation; report missing edit grant (VIEW ≠ EDIT) |

---

## 6. V1 edge catalog

Edge names are boring and explicit. Relationship-level audit/provenance props on every important edge (provenance set from Codex): `source_kind` (`live_openapi·chart_registry·route_metadata·connector_manifest·manual_review·statistical_proposal·llm_proposal`), `source_ref`, `source_confidence`, `created_by`, `review_state`, `valid_from`/`valid_to`, plus `source_profile_id`, `confidence`, `status`.

| Edge | From → To | Meaning | Key props |
|---|---|---|---|
| `HAS_DOMAIN` | Business → Domain | root → functional column | `primary` |
| `HAS_PRODUCT` | Business → IntelligenceProduct | root → IQ app | `primary` |
| `PARENT_OF` | Domain → Domain | domain tree | — |
| `BELONGS_TO_DOMAIN` | Metric/Dashboard → Domain | functional grouping (spine) | `confidence·source` |
| `CONTEXTUALIZES` ⊕ | Domain → Metric | domain provides business context (no ownership) — **adopted from Codex** | `context_type·confidence·source` |
| `GOVERNS` ⊕ | Domain → Metric | domain governs interpretation / action — **adopted from Codex** | `governance_type·confidence·source` |
| `PART_OF_PRODUCT` | Metric/Dashboard/UIComponent → IntelligenceProduct | IQ ownership (spine) | `primary·confidence` |
| `USES_PLATFORM` | Business → Platform | root → source/action vendor | `primary·review_state` |
| `SOURCES` | Platform → Metric | platform provides the metric's source data | `source_metric_name·lineage_ref·freshness_sla_hours·review_state` |
| `ACTIVATES` | Platform → Metric | **future** action eligibility (KG flag; no runtime) | `activation_mode·requires_approval·review_state` |
| `ROLLS_UP_TO` | Metric → Metric | channel→platform→blended aggregation | `aggregation_method·lag·confidence` |
| `DECOMPOSES_INTO` | Metric → Metric | formula component | `operator·weight·confidence=1.0` |
| `HAS_THRESHOLD` | Metric → Threshold | metric boundary (instances V1.x+) | `is_default·segment_context·priority` |
| `GOVERNED_BY` | Metric/Dashboard/Threshold → Policy | policy scope (instances V1.x+) | `priority·status·effective_from` |
| `ENFORCES_THRESHOLD` | Policy → Threshold | policy explains/enforces the number | `explanation_type·confidence` |
| `VISUALIZES` | UIComponent → Metric | chart/card → metric | `visual_role(primary·secondary·comparison·filter)·formula_ref·match_type·confidence` |
| `SHOWN_ON` | Metric → Dashboard | metric appears on surface (cached on `source_dashboards[]`) | `is_primary` |
| `INFLUENCES` | Metric → Metric | weak causal/correlation candidate | `confidence·lag·mechanism·evidence_count` |
| `CORRELATES_WITH` | Metric ↔ Metric | statistical association, **not** causal | `correlation·p_value·lag·sample_size` |
| `CAUSES` | Metric → Metric | approved causal relation (evidence-backed) | `edge_key·confidence·evidence_mass·lag_min/max·mechanism·review_state` |
| `OWNS` / `OWNED_BY` | Role ↔ Metric/Domain/Platform/Policy/Threshold/Dashboard | accountable owner (anchors approval path) | `ownership_type·accountability_level·priority` |
| `CAN_ACCESS_PRODUCT` | Role → IntelligenceProduct | product gate | `effect·permission·scope_depth·min_authority_rank·classification_ceiling·masked_fields` |
| `CAN_ACCESS_PLATFORM` | Role → Platform | platform gate | `effect·permission·min_authority_rank·classification_ceiling` |
| `CAN_VIEW` | Role → Domain/Metric/Dashboard/UIComponent | read grant / cross-grant | `effect·allowed_fields·masked_fields·max_grain·row_filter_json·priority` |
| `CAN_EDIT` | Role → Metric/Policy/Threshold | edit gate | `effect·approval_required·priority` |
| `CAN_APPROVE` | Role → Policy/Threshold/Metric | approval authority | `effect·approval_limit_json·priority` |
| `REPORTS_TO` | Role → Role | **org/social tree** (branch test + escalation) | `relationship_type·source` |
| `ESCALATES_TO` | Role → Role | explicit escalation path (incident/approval/data-quality) | `escalation_reason·sla_hours·review_state` |
| `CAN_DELEGATE_TO` | Role → Role | scoped, time-boxed delegation | `delegation_scope·max_duration_hours·requires_approval` |
| `INHERITS_FROM` | Role → Role | **permission inheritance** (kept separate from `REPORTS_TO`) | `priority·source` |

> **Domain-edge write convention.** Every metric/dashboard gets exactly one **spine** membership edge, `BELONGS_TO_DOMAIN`, to its home functional column (mirrored in the `domain_ids[]` cache as the first/primary id). `CONTEXTUALIZES` and `GOVERNS` are **additive overlays**, written only when a *second* domain reasons about the metric without owning it: `CONTEXTUALIZES` = provides business context (no authority), `GOVERNS` = governs interpretation/action. Accountability is a separate edge, `OWNS`/`OWNED_BY` (Role or Domain → Metric). So a metric owned by finance but shown under marketing is: `BELONGS_TO_DOMAIN→marketing`, `GOVERNS` (or `OWNS`) `←finance`, and `domain_ids:["marketing","finance"]`. Cache is rebuilt from these edges; the edge is truth.

### Permission-edge properties
| Property | Type | Applies to | Purpose |
|---|---|---|---|
| `effect` | `allow·deny` | all | explicit deny at higher priority blocks inherited/clearance access |
| `permission` | string | all | `view·explain·traverse·edit·approve·execute·manage` |
| `priority` | int | all | higher wins on conflict |
| `scope_depth` | int | product/domain grants | how far a grant flows down |
| `product_scope_ids[] · domain_scope_ids[] · platform_scope_ids[]` | string[] | view/edit/access | scope without extra nodes |
| `min_authority_rank · classification_ceiling` | int / enum | access grants | extra constraints on `CAN_ACCESS_*` (Codex; additive to clearance) |
| `allowed_fields[] · masked_fields[]` | string[] | view edges | field-level exposure/redaction |
| `max_grain` | string | metric/dashboard | restrict detail (weekly-only) |
| `row_filter_json · condition_json` | json | metric/component | segment filters; time/entitlement conditions |
| `valid_from · valid_to` | datetime | all | temporary / scheduled access |
| `approval_required` | bool | edit/execute | may request but not directly apply |

---

## 7. Live enums + type-audit summary

### Authoritative live enums (re-verified from `openapi.json`)
These are the API's **global component schemas** — the general type system, **not** master-config data. Use verbatim to constrain properties.
- `ValueFormat` (4): `number · currency · percentage · decimal`
- `Granularity` (4): `daily · weekly · monthly · quarterly`
- `ChartType` (15): `line · area · bar · horizontal_bar · grouped_bar · pie · donut · sankey · heatmap · table · sparkline · scatter · treemap · gauge · funnel`
- `ThresholdType` (3): `static · percentile · seasonal`
- `ThresholdDirection` (3): `higher_is_better · lower_is_better · target_is_best`
- `ConditionType` (4): `threshold · anomaly · trend · missing_data`
- `ConditionOperator` = `ComparisonOperator` (8): `lt · lte · gt · gte · eq · neq · between · outside`
- `Severity` (5): `critical · high · medium · low · info`
- `MetricCategory` (14): `advertising · revenue · traffic · email · customer · sms · google_ads · meta_ads · efficiency · comparison · financial · marketing · product · operational`
- `MetricSource` (4): `ga4 · google_ads · meta_ads · klaviyo` — **but `source_set[]` is a free list** (`magento`, `linkedin_ads` also appear)
- `UserRole` (7): `super_admin · agency_admin · tenant_admin · analyst · viewer · admin · user` *(auth-layer roles; map to KG `role_type`/`seniority_rank` via `Role.auth_role`)*

Graph-derived vocab (from `rare_seeds`): scope `blended·store·ecom·web·ml·google·meta·klaviyo` · causal_role `outcome·mediator·controllable·constraint·external·ml_output·untyped` · aggregation `level·rate·avg·sum·ratio·median`.

KG-extension vocab (adopted from Codex — not OpenAPI enums): `platform_type` `analytics·ads·crm·ecommerce·warehouse·activation·support·finance·other` · `policy_type` `access·interpretation·alerting·escalation·approval·action_guardrail·data_quality` · `Threshold.threshold_type` KG-ext `warning·critical·target·anomaly·sla·budget` + operator `percent_change·z_score` · `review_state` `proposed·needs_review·active·deprecated` (nodes) / `draft·active·needs_review·retired` (governance) · `business_type` `ecommerce·saas·marketplace·retail·services·other`.

### Type corrections (carried from the data audit)
1. **`causal_role_confidence`: number → enum `low·medium·high`** (`type_confidence` is categorical; the numeric edge `confidence` is a different thing).
2. **Booleans encoded as strings** — `is_model_output`, `is_derived`, `keep` arrive as `yes`/`no` → real bool at ingestion.
3. **Pipe-delimited lists → `string[]`** — `source_set`, `mart_sources`, `source_dashboards`, `dimensions` are single `|`-delimited strings; split at ingestion.
4. **Missing scalar `source`** — add enum `single·multi` (distinct from `source_set[]`).
5. **Nullable fields** — `availability`, `n_periods`, `formula_text`, `dimensions` are frequently empty; model nullable. `n_periods` is int-or-null.
6. **`source_set` exceeds the `MetricSource` enum** (`linkedin_ads`, `magento`) — keep a free `string[]`.
7. **`scope` is one value** — store `scope_key`; derive `scope_level`.
8. **Chart-registry coverage** — 10 fields at 100%; **`narration_text` only 558/646 (86.4%)** → optional.
9. **`ThresholdConfig` bands are strings** — keep raw `green/yellow/red_value` and parse to `*_num`.

---

## 8. Ingestion — business plane only, zero master-config

Of 877 GET ops, only the **business plane** becomes graph nodes. **`master-config/**` is excluded entirely** (not "config evidence") — it is the stale source we are eliminating. **Edges are built by us from evidence**; no relationship catalog or ontology is imported.

| Pattern | Action | Why |
|---|---|---|
| `GET /{dash}/metrics/{id}` | **promote metric** | card / current-value surface |
| `GET /{dash}/charts/{id}` | **promote UI / series** | chart / time-series |
| `GET /{dash}/` · `/{dash}/metadata` · `GET /admin/dashboards` | promote dashboard | dashboard surface (no master-config) |
| chart-registry.json (646) | promote `UIComponent` | resolve `VISUALIZES` after review |
| `master-config/**` · `master/**` | **EXCLUDE ENTIRELY** | stale; never a node, edge, identity, or governance source |
| `/auth/**` · `/admin/**`(non-dashboards) · `/settings/**` | exclude | access/admin control plane |
| `/health · /docs · /redoc · /openapi.json` | exclude / metadata | observability surfaces |
| `POST/PUT/PATCH/DELETE` (46 ops) | exclude from harvest | later governed Tool/Action (V2) |

**Pipeline:** acquire spec (live preferred, checked-in fallback, hash it) → deterministic exclusion (drops `master-config/**` first) → endpoint families (method+path+response-sig+role) → classify once per spec hash → human review → deterministic harvest → **proposals only** → arbitration writes (dedupe by canonical identity) → completeness report → incremental (reclassify only changed families; deprecate, never delete).

**Governance instances** (`Policy`/`Threshold`) are **not ingested in V1** — their only API source is master-config. The node shapes exist; instances arrive later from human/statistical/`alerts-config` sources.

**Causal layer** starts empty and is built from evidence. The `rare_seeds` correlations enter as `CORRELATES_WITH`; **no correlation is auto-promoted to `CAUSES`.** Confidence is derived via the **Beta fold** (kept from Codex):

```text
α = β = 0.5
for each supporting evidence record:  α += weight
for each refuting evidence record:    β += weight
confidence    = α / (α + β)
evidence_mass = α + β        // expose BOTH — 0.80 from one weak prior ≠ 0.80 from many outcomes
```
The append-only evidence ledger that stores those records is **V2** (§11); V1 records may live outside Neo4j.

### Source priority (per entity) — *adopted from Codex*

`master-config` is in the **never-use-automatically** column for every entity.

| Entity | Preferred sources | Fallback | Never (automatic) |
|---|---|---|---|
| `Business` | reviewed business profile | manual bootstrap file | master-config |
| `IntelligenceProduct` | product catalog, live route metadata | reviewed manual config | master-config |
| `Domain` | reviewed business taxonomy | product/domain owner input | master-config |
| `Platform` | connector/platform manifests | reviewed platform catalog | master-config |
| `Dashboard` | live route metadata, non-master dashboard endpoints, chart-registry | reviewed manual config | master-config dashboards/endpoints |
| `UIComponent` | chart-registry, live dashboard metadata | reviewed manual config | master-config |
| `Metric` | canonical metric review, endpoint schemas, chart formulas, connector manifests | statistical/LLM proposals **after review** | master-config metrics/relationships |
| `Policy` / `Threshold` | governance review, domain-owner input | observed suggestions after review | master-config thresholds/policies |
| `Role` | auth/session map, reviewed org map (+ Org Graph Engine §5) | reviewed manual config | inferred hierarchy without review |

### Proposal payload shape — *the harvester emits proposals only; arbitration is the only writer*

```json
{
  "proposal_id": "kgp_2026_001",
  "operation": "upsert",
  "target_label": "Metric",
  "target_id": "metric:blended:revenue",
  "source_kind": "chart_registry",
  "source_ref": "marketing_iq/revenue_overview/blended_revenue_card",
  "source_confidence": 0.82,
  "review_state": "proposed",
  "payload": {
    "canonical_id": "blended-revenue", "concept_key": "revenue",
    "value_format": "currency", "default_direction": "higher_is_better",
    "product_ids": ["miq", "piq"], "domain_ids": ["marketing", "finance"],
    "platform_ids": ["shopify", "google_ads", "meta_ads"]
  },
  "relationship_payloads": [
    { "type": "PART_OF_PRODUCT", "from_id": "metric:blended:revenue", "to_label": "IntelligenceProduct", "to_id": "miq" },
    { "type": "SOURCES", "from_label": "Platform", "from_id": "shopify", "to_id": "metric:blended:revenue",
      "properties": { "source_metric_name": "gross_sales", "review_state": "proposed" } }
  ]
}
```

---

## 9. Neo4j constraints (V1)

```cypher
CREATE CONSTRAINT business_id     IF NOT EXISTS FOR (n:Business)            REQUIRE n.business_id   IS UNIQUE;
CREATE CONSTRAINT domain_id       IF NOT EXISTS FOR (n:Domain)              REQUIRE n.domain_id     IS UNIQUE;
CREATE CONSTRAINT product_id      IF NOT EXISTS FOR (n:IntelligenceProduct) REQUIRE n.product_id    IS UNIQUE;
CREATE CONSTRAINT platform_id     IF NOT EXISTS FOR (n:Platform)            REQUIRE n.platform_id   IS UNIQUE;
CREATE CONSTRAINT metric_uid      IF NOT EXISTS FOR (n:Metric)              REQUIRE n.metric_uid    IS UNIQUE;
CREATE CONSTRAINT dashboard_id    IF NOT EXISTS FOR (n:Dashboard)           REQUIRE n.dashboard_id  IS UNIQUE;
CREATE CONSTRAINT ui_component_id IF NOT EXISTS FOR (n:UIComponent)         REQUIRE n.component_id  IS UNIQUE;
CREATE CONSTRAINT policy_id       IF NOT EXISTS FOR (n:Policy)              REQUIRE n.policy_id     IS UNIQUE;
CREATE CONSTRAINT threshold_id    IF NOT EXISTS FOR (n:Threshold)           REQUIRE n.threshold_id  IS UNIQUE;
CREATE CONSTRAINT role_id         IF NOT EXISTS FOR (n:Role)                REQUIRE n.role_id       IS UNIQUE;
CREATE CONSTRAINT role_key        IF NOT EXISTS FOR (n:Role)                REQUIRE n.role_key      IS UNIQUE;

CREATE INDEX metric_product   IF NOT EXISTS FOR (n:Metric) ON (n.product_ids);
CREATE INDEX metric_domain    IF NOT EXISTS FOR (n:Metric) ON (n.domain_ids);
CREATE INDEX metric_platform  IF NOT EXISTS FOR (n:Metric) ON (n.platform_ids);
CREATE INDEX metric_concept   IF NOT EXISTS FOR (n:Metric) ON (n.concept_key);
CREATE INDEX metric_minlevel  IF NOT EXISTS FOR (n:Metric) ON (n.min_level);
CREATE INDEX role_seniority   IF NOT EXISTS FOR (n:Role)   ON (n.seniority_rank);
```
*(No `tenant_id`, `metric_concept`, `endpoint_id`, or master-config-derived keys.)*

---

## 10. V1 build order & acceptance criteria

1. **Spine inventory** — `Business` (rich context) → `Domain` ∥ `IntelligenceProduct` → `Metric` (denormalized domain+product+platform) → `Dashboard` → `UIComponent`. Tri-axis ownership edges; surface edges. **`Platform` is lazily materialized** — seed platform fields + `SOURCES` edge metadata first; create `Platform` nodes when platform-level traversal is needed. **All from business endpoints + chart-registry + connector manifests; zero master-config.**
2. **Org graph + RBAC** — run the Org Graph Ingestion Engine (seeded from `Business.tier` + org description) → `Role` nodes, `REPORTS_TO`, `seniority_rank`; set `min_level` on resources; enforce the clearance+branch VIEW gate before any context leaves the DB.
3. **Governance shells** — create `Policy`/`Threshold` definitions with `population_status = 'defined'`; no instances.
4. **Causal layer (the point)** — build `CAUSES`/`INFLUENCES`/`CORRELATES_WITH` from evidence; correlations never auto-promote; confidence via the Beta fold.
5. **Defer to V2** — §11.

**Acceptance criteria**
- V1 labels are exactly: `Business`, `Domain`, `IntelligenceProduct`, `Platform`, `Metric`, `Dashboard`, `UIComponent`, `Policy`, `Threshold`, `Role`.
- **No data source is `master-config`** — identity, edges, dashboards, governance all sourced without it (it is in the never-use column of the source-priority table, §8).
- Every `Metric` carries denormalized `domain_ids[]`+`product_ids[]`+`platform_ids[]` fields (arrays — a canonical metric can sit on several products/domains) and links to all three axes; one canonical metric is never duplicated per product/domain/platform.
- `Platform` is always queryable via `Metric` fields; the node is materialized only when platform-level traversal is needed.
- Every agent read is role-filtered (clearance + branch) before context leaves the DB; VIEW never implies EDIT.
- `Policy`/`Threshold` exist as defined shells with `population_status = 'defined'`.
- Every causal edge has `confidence`, `evidence_mass`, temporal lag, mechanism, `review_state`, source; correlation never auto-promotes.
- Disappeared entities are deprecated, not deleted.
- Success metric is **"N evidence-backed `CAUSES`/`INFLUENCES` edges"**, not "N nodes ingested."

---

## 11. Deferred to V2 (named, not specified)

The FRD's memory/learning machinery (Layers 2–6) plus the Layer-1 entities both prior drafts omitted, plus the **populate-later governance instances**. Listed so nothing is lost; deliberately without property tables.

- **Governance instances** — `Policy`/`Threshold` rows, once a trusted non-master-config source exists.
- **Layer-1 causal entities** — `Outcome`, `Tool`/`Action` (`CONTROLLED_BY`), `Investigation_Rule`, `Approval_Rule`.
- **Memory / learning (Layers 2–6)** — `Thoughtlet`; `DecisionCapsule` (10-section episodic memory, `ANCHORED_TO` graph nodes); `MonitoringContract` + `WakeCondition`; `EvidenceEvent` (append-only) + `CausalRelation` (reified edge, source of the derived Beta-fold confidence); `LearningCandidate` → `PromotedMemory` → governed projection; `GraphChangeProposal`; `GraphVersion`; `SourceProfile`/`Endpoint_Family`/`IngestionRun`; Capsule-Agent rehydration recipe.
- **`Person`** — real auth/HR users, once they exist (the Org Graph Engine attaches them to `Role`s).

---

## 12. Appendix — quick reference & prior-draft comparison

- **API:** TW Analytics API · 902 paths · 877 GET / 46 non-GET · 463 schemas. 65 master-config paths — **all excluded**.
- **Node labels (10):** `Business` · `Domain` · `IntelligenceProduct` · `Platform`☆ · `Metric` · `Dashboard` · `UIComponent` · `Policy`★ · `Threshold`★ · `Role`.
- **Connectors / vendor platforms:** GA4, Google Ads, Klaviyo, Magento, Meta Ads, Snowflake, Shopify (+ `linkedin_ads` in `source_set`) — these are the `Platform` node values (§4). Snowflake + Azure dual-write.
- **IntelligenceProducts:** `miq` · `ciq` · `piq` *(CIQ/PIQ on `miq` schema)* · `dc` · `creative_iq` *(external)*.
- **Chart registry:** 646 entries; 10 fields at 100%, `narration_text` 558/646.
- **rare_seeds:** pilot client; 355 nodes, 4 correlation edges — the gap this project closes. Not a tenant.
- **Tenancy:** one Neo4j database per tenant — no `Tenant` node, no `tenant_id`.

### `final-schema-claude` (this doc) vs the parallel `final-schema-codex`

After cross-pollination the two designs **converge** on most structure; the remaining differences are deliberate.

| Dimension | `final-schema-codex` | **`final-schema-claude` (this doc)** | Status |
|---|---|---|---|
| Root node | `Business` | `Business` | ✅ agree |
| Axes | tri-axis Product ∥ Domain ∥ Platform | **tri-axis Product ∥ Domain ∥ Platform** | ✅ agree (Platform adopted) |
| `Platform` node | thin, lazily materialized | **thin, lazily materialized** | ✅ agree (adopted) |
| Rich `Business` context | strategic intent, north-star, constraints | **adopted** + Claude's `tier`/`root_seniority_rank` | ✅ agree (+org-engine driver) |
| Master-config | excluded/stale | **eliminated entirely** | ✅ agree |
| Policy shape | general governance (`policy_type`/`applies_to_kind`/`effect_json`) | **blended**: governance fields + OpenAPI condition fields | ✅ agree (blend) |
| Edge/ingestion provenance | `source_kind`/`source_ref`/proposal payload | **adopted** | ✅ agree |
| Causal confidence | evidence-backed Beta fold | **Beta fold** | ✅ agree |
| **RBAC visibility** | rank necessary, never sufficient (explicit scope) | **clearance + branch auto-visibility** | ⟂ differ (Claude's, by choice) |
| **Permission edges** | per-action labels (`CAN_VIEW_METRIC`…) | **generic `CAN_VIEW` + `permission`** | ⟂ differ (Claude's) |
| **Dashboard→UIComponent** | `CONTAINS_COMPONENT` edge | **`UIComponent.dashboard_id` FK** (no edge) | ⟂ differ (Claude's) |
| Org/social graph | reviewed role/org map | **adaptive LLM Org Graph Engine** (`tier`-scaled, editable) | ＋ Claude adds |
| Naming | `owner_role_key`; `role_key`-only | `owner_role_id`; `role_id`+`role_key`. Classification **standardized to `data_classification`/`default_data_classification`/`max_data_classification`+`min_level`** in this revision (was `sensitivity_level`) | cosmetic (classification converged) |

**Companion:** `final-schema-claude.html` (visual blueprint).
