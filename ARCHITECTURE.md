# dc-kg — System Architecture

**ThoughtWire Causal Knowledge Graph**
_Current implementation as of 2026-06-28 · branch `kg-llm-rebuild`_

> This is the single authoritative architecture reference. It describes the **current,
> working system**: a Neo4j system-of-record populated by a **Claude Agent SDK agentic
> builder**, served over **FastAPI + SSE**, and explored through a **React Flow canvas**.
>
> For the visual, browsable version of this document open **`ARCHITECTURE.html`** at the
> repo root. For the node/edge property contract see **`docs/final-schema-claude.md`**.

---

## 1. Overview

dc-kg turns a retailer's analytics surface — dashboards, metrics, formulas, ML models, and
governance rules — into a **directed, signed, causal knowledge graph** in Neo4j. The graph
answers questions a flat metric catalog cannot:

- _"If this warehouse column changes, which metrics break?"_ (lineage / blast radius)
- _"What drives ROAS, and with what lag and sign?"_ (causal upstream/downstream traversal)
- _"What is this metric made of?"_ (formula decomposition)
- _"Who governs this metric and what thresholds alert on it?"_ (governance overlay)

### What changed (skeletal → agentic)

The project began as a **deterministic "skeleton" build** (and, even earlier, an in-memory
NetworkX prototype). Those approaches were scope-blind when resolving formulas and additivity
when rolling metrics up, and they produced graphs that were hard to reason about. The system
was **rebuilt around an LLM agentic builder** (`kg build`, `harness/agentic/`): bounded-concurrent
Claude Opus agents read the curated metric catalog and the backend SQL, then create nodes and
draw edges through a single, schema-enforcing writer.

The deterministic skeleton/causal/edge-seed CSVs still exist under `data/` as **inputs and
regenerable artifacts**, but they are no longer the build mechanism. (Historical design docs for
those phases now live in `docs/archive/`.)

### One-line architecture

```
curated inputs ──▶ bootstrap spine ──▶ agentic build (4 phases) ──▶ enrich
                                                                      │
                                                                      ▼
                                          Neo4j (single-writer arbitration)
                                                                      │
                                              ┌───────────────────────┴───────────────────────┐
                                              ▼                                                 ▼
                                   FastAPI + SSE  (harness/api)                       MCP server (harness/mcp)
                                              │                                                 │
                                              ▼                                                 ▼
                                   React Flow canvas (app/kg-canvas)              Claude agents · Claude Code CLI
```

### Stack

| Layer | Technology |
|---|---|
| Graph store | Neo4j (bolt), single-writer upserts via `MERGE` |
| Build engine | Python + Claude Agent SDK (Claude Opus 4.8) |
| Agent tools | MCP (FastMCP, stdio) — `mcp__graph__*` |
| API | FastAPI + `sse-starlette` (live event stream) |
| CLI | `kg` console script (`harness/cli/kg.py`) |
| Frontend | React 19 · Vite · TypeScript · Zustand · React Flow (`@xyflow/react`) · dagre · shadcn/Tailwind 4 |
| Models / validation | Pydantic (`harness/kg/models.py`) |

---

## 2. Repository directory structure

```
dc-kg/
├── ARCHITECTURE.md / ARCHITECTURE.html   ← this document (current source of truth)
├── README.md                             ← quickstart + runbook
├── INTEGRATION-ANALYSIS-claude.md/.html  ← knowledgeGraph ↔ dc-kg integration analysis
├── pyproject.toml · uv.lock              ← Python project + locked deps
│
├── harness/                              ← Python backend (the engine)
│   ├── kg/          ← Neo4j driver, Pydantic models, schema, SINGLE WRITER (arbitration)
│   ├── agentic/     ← LLM agentic builder: orchestrator · runner · prompts · enrich
│   ├── agent/       ← shared LLM engine (structured output + MCP tool-loop) + prompts
│   ├── ingest/      ← dashboard ingestion (prepass · proposer · apply · scoring · BC_2 snapshot)
│   ├── governance/  ← LLM Policy/Threshold extraction from documents
│   ├── seed/        ← source-of-truth seed JSON (spine, platforms, components, governance)
│   ├── mcp/         ← FastMCP graph server: mcp__graph__* write + doc-read tools
│   ├── api/         ← FastAPI + SSE server for the canvas
│   ├── cli/         ← `kg` command (all subcommands)
│   ├── store/       ← backup / restore / wipe utilities
│   ├── marts/       ← dbt-mart lineage + SQL enrichment
│   ├── stats/       ← Snowflake correlation stats (optional)
│   ├── discovery/   ← PCMCI+/Granger/CMIknn causal discovery (optional [discovery] extra)
│   ├── hooks/       ← pre-tool permission guards
│   └── tests/       ← pytest suite
│
├── app/
│   └── kg-canvas/   ← React + Vite canvas (frontend)
│       └── src/
│           ├── components/             ← Toolbar, CanvasView, NodeDetail, EdgeDetail, …
│           │   └── governance/         ← GovernancePanel (Policy/Threshold authoring wizard)
│           ├── lib/                    ← api.ts · graphTheme.ts · graphLayout.ts · egoLayout.ts
│           ├── store.ts                ← Zustand state
│           └── App.tsx                 ← layout shell
│
├── data/                               ← inputs + build artifacts
│   ├── metric_nodes.rare_seeds.json    ← METRIC SOURCE OF TRUTH (325 metrics + 161 inputs)
│   ├── metric_registry.rare_seeds.csv  ← OpenAPI metric registry (formulas, sources, dashboards)
│   ├── unique_metrics_catalog.rare_seeds.csv
│   ├── *_edges.rare_seeds.csv          ← legacy skeleton outputs (NOT read by build); discovery I/O
│   ├── proposals/run-*/                ← per-dashboard ingestion proposal queue (JSONL)
│   ├── build-report.<runId>.json       ← phase-4 audit (counts, loops, orphans, leaves)
│   ├── backups/                        ← Neo4j export snapshots
│   ├── events/                         ← append-only audit event log (JSONL)
│   └── skeleton/                       ← legacy deterministic artifacts (coverage / edge-diff)
│
├── docs/                               ← documentation
│   ├── final-schema-claude.md/.html    ← AUTHORITATIVE node/edge property contract (V1)
│   ├── graph-build-process.md          ← latest build run report
│   ├── unique-metrics-and-ml-classification.md · leaf-metrics.md
│   ├── frd-docs/                       ← functional requirements + deep research
│   └── archive/                        ← superseded design/schema/skeleton docs (historical)
│
└── BC_2  (sibling repo: /Users/kushal/Desktop/kal/BC_2)
        ← external dbt backend; read-only source of SQL formulas via MCP doc-tools
```

---

## 3. Source-of-truth / input files

The graph is **constructed from a small set of curated inputs**. Nothing in the graph is invented
from nothing — every node and edge traces to one of these.

### 3.1 Spine seeds (`harness/seed/`) — the deterministic backbone

| File | Contains |
|---|---|
| `spine_seed.json` | The tri-axis spine: `business`, `domains[]`, `products[]` (Business / Domain / IntelligenceProduct nodes). |
| `platforms.json` | Platform nodes (ga4, google_ads, meta_ads, klaviyo, magento, …) incl. sub-channel hierarchy. |
| `component_types.json` | The 17 generalized chart-type UIComponent nodes (line, bar, sankey, funnel, …). |
| `governance.rare_seeds.json` | Demo Policy + Threshold + governance edges (Google Ads KPIs). |
| `governance_seed.py` | Loader that upserts the governance demo through arbitration. |
| `concept_causal_rules.json`, `funnel_flow.json`, `identities.json`, `formula_overrides.rare_seeds.json`, `skeleton_overrides.rare_seeds.json`, `rare_seeds_priors.json`, `rare_seeds_correlations.json` | Enrichment priors / overrides consulted during build + enrich. |

### 3.2 Metric catalog (`data/`) — the metric source of truth

| File | Contains |
|---|---|
| **`metric_nodes.rare_seeds.json`** | **The metric source of truth.** `_meta` + **325 `metrics`** (metric_uid, canonical_id, formulas, dependencies, ML flags, scope) + **161 `input_nodes`** (raw source fields / constants). This is what the Phase-1 agents read. |
| `metric_registry.rare_seeds.csv` | OpenAPI-derived registry: formula, `source_expr`, `mart_model`, dashboards per metric. **Actively read** during the build — the MCP doc-tool `get_metric_source` joins this row, and `enrich.py` reads it for SQL/mart provenance. |
| `unique_metrics_catalog.rare_seeds.csv` | Deduplicated, sub-platform-aware metric manifest. |

### 3.3 Edge CSVs (`data/*_edges.rare_seeds.csv`) — NOT consumed by the build

> **Important — edges are computed by the agents, not loaded from these files.** See §5 + §8.4.

| File(s) | Status |
|---|---|
| `structural_edges`, `compositional_edges`, `crossproduct_edges`, `model_edges` | **Dead artifacts** of the retired deterministic skeleton. **No code reads them.** Kept only as regenerable reference / diffing material. |
| `candidate_edges`, `discovered_edges` | Used **only** by the optional `kg discover` statistical engine (`harness/discovery/`): `candidate_edges` is its input, `discovered_edges` its output. The main `kg build` never touches them. |

The current build draws every metric↔metric edge from **formula + SQL evidence via LLM reasoning**
(§5.1 Phase 2/3), not by importing a precomputed edge table.

### 3.4 External backend snapshot — `BC_2` (read-only)

The sibling dbt repo at `/Users/kushal/Desktop/kal/BC_2`. Agents read its seed CSVs and SQL models
**only through MCP doc-tools** (`get_metric_source`, `get_bc2_sql`, `inspect_bc2_sources`) — never
by writing into it. `harness/ingest/bc2_snapshot.py` hashes + caches the relevant slice.

### 3.5 Proposal queue (`data/proposals/run-*/`)

Dashboard-ingestion output: one JSONL file per dashboard, each line a review-state proposal
(node + relationship payloads). Reviewed (approve/reject) then `apply`-ed through arbitration.

---

## 4. Backend modules & how they interact

### `harness/kg/` — the graph core (single writer)
The **only** module that writes to Neo4j.
- `driver.py` — thin Neo4j wrapper (managed transactions, auto-retry).
- `models.py` — Pydantic models for every node/edge + all controlled vocabularies (enums).
- `schema.py` — uniqueness constraints + indexes (one `MERGE` key per label).
- **`arbitration.py`** — the **single arbitration writer**. Every node/edge upsert (from agents, the
  API, seeds, CLI) funnels through `write_node_model()` / `upsert_edge()`, which validate labels,
  enforce endpoint existence, strip `None`, and append an audit event. No other module issues writes.
- `evidence.py` — the per-edge **evidence ledger** (Beta confidence accumulation).
- `reconcile.py` — idempotent reconciliation/repair pass.

### `harness/agentic/` — the LLM builder (primary build path)
- `orchestrator.py` — **phased-parallel** driver. Slices the 325 metrics into ~8–12
  namespace/domain buckets (`slice_metrics`, offline/deterministic), defines the 4 phases and the
  inter-phase **barrier**, and builds each phase's system/user prompts.
- `runner.py` — runs one agent (Claude Opus 4.8) against the MCP graph server; budget- and
  timeout-capped; tool-calling with a structured-output fallback.
- `prompts.py` — the per-phase prompt templates.
- `enrich.py` — post-build deterministic enrichment (`critique_dedupe`, `run_deterministic_enrich`
  for mart/SQL/freshness, `migrate_edge_ledger`).

### `harness/agent/` — shared LLM engine
`engine.py` exposes `propose_structured()` (schema-validated JSON output) and the MCP tool-loop used
by ingestion and governance extraction; `prompts.py` holds their system prompts.

### `harness/ingest/` — incremental dashboard ingestion (Milestone 2)
`prepass`/`dashboard_prepass` scan the chart registry → `proposer`/`dashboard_proposer` draft +
LLM-enrich proposals → `edge_scoring` scores candidates → proposals written to `data/proposals/` →
`apply` replays approved proposals through arbitration. `openapi_inventory` / `endpoint_filters` /
`chart_types` / `bc2_snapshot` provide the supporting catalogs.

### `harness/governance/` — LLM Policy/Threshold extraction
`extract.py` (`extract_governance(text, …)`) LLM-parses a pasted/uploaded document into a draft
`{policy, threshold}` to **prefill** the canvas authoring wizard (it does not write). `extract_subprocess.py`
isolates the SDK call in a subprocess for the API.

### Supporting modules
- `harness/mcp/` — the agent tool surface (§6).
- `harness/api/` — the canvas backend (§6).
- `harness/cli/` — the `kg` command (§7).
- `harness/seed/` — source-of-truth seeds (§3.1).
- `harness/store/` — backup / restore / wipe (the only safety net before a destructive build).
- `harness/marts/` · `harness/stats/` · `harness/discovery/` — mart lineage, correlation stats, and
  optional statistical causal discovery (feed `enrich`/proposals; never write directly).
- `harness/hooks/` — pre-tool permission guards for agent runs.

---

## 5. The AI / LLM layer

Three LLM callsites, all Claude Opus 4.8, all writing only through the single arbitration writer.

### 5.1 Agentic build — `kg build` (the main one)

Four phases, parallel within a phase, with a hard barrier before edges:

```
Phase 1 · NODES        ~8–12 parallel agents. Each reads its metric slice via MCP doc-tools
                       (list_metrics, get_metric_source) and calls create_metric_node, attaching
                       each metric to the spine. The `operational.*` namespace is dropped.
        ── BARRIER ──  asyncio.gather: every node must exist before any edge is drawn.
Phase 2 · STRUCTURAL   parallel agents draw DECOMPOSES_INTO edges from formulas (draw_edge).
Phase 3 · CAUSAL       parallel agents reason over metrics + BC_2 to draw INFLUENCES edges
                       (add_causal_edge) with confidence, sign, and temporal lag.
Phase 4 · CRITIQUE     a single agent finds loops / orphans / leaves, de-dupes INFLUENCES that
                       duplicate a structural formula, and writes data/build-report.<runId>.json.
```

`kg build --dry-plan` previews the plan **fully offline** (no SDK, no Neo4j). `--smoke` builds only
the `blended.*` ROAS chain and **skips the destructive wipe** — a fast end-to-end validation.
A full build is destructive: Phase 0 exports a backup, wipes, and re-seeds the spine first.

### 5.2 Dashboard ingestion proposer
`harness/ingest/proposer.py` calls the shared engine with a section-8 output schema to turn one
dashboard's charts into **proposals** (review-before-write), not direct graph writes.

### 5.3 Governance extraction
`harness/governance/extract.py` parses a governance document into draft Policy/Threshold fields to
prefill the authoring wizard.

### 5.4 Why this is safe
- **Single writer:** agents never touch Neo4j directly — only `mcp__graph__*` tools, which call
  arbitration, which validates against the schema.
- **Sandboxed tools:** the MCP server exposes only graph writes + offline doc reads — no shell, web,
  or arbitrary file access.
- **Auditable + idempotent:** every write is a `MERGE` on the identity key and appends an event to
  `data/events/`; re-running converges instead of duplicating.

---

## 6. Interfaces — API & MCP

### 6.1 FastAPI server (`harness/api/server.py`) — the canvas backend

| Method · Path | Purpose |
|---|---|
| `GET /api/health` · `GET /api/status` | liveness · node/edge counts by label |
| `GET /api/graph` | full graph export for the canvas (`limit`, `include_deprecated`) |
| `GET /api/coverage` | metric-coverage summary (per tenant) |
| `GET /api/edge-diff` | diff a proposal run vs the live graph |
| `GET /api/traverse/upstream` · `…/downstream` | ranked **signed** lineage paths (acyclic + cyclic), per-hop sign & lag |
| `GET /api/column-impact` | metrics whose `source_columns` include a warehouse column (blast radius) |
| `GET /api/metric-chart` | a metric's chart type + its single chart-registry slice + series endpoint (hydrates the shift-click **Chart** view node) |
| `GET /api/dashboard-charts` | **read-only** chart-registry slice for one `dashboard_id` (every chart on it, `chart_type` joined from `chart_type_map.json`) — backs the shift-click-a-Dashboard reveal |
| `GET /api/dashboards` | dashboard catalog |
| `GET /api/proposals` · `POST /api/proposals/{id}/review` · `POST /api/proposals/approve-all` | proposal queue management |
| `POST /api/ingest` · `POST /api/apply` | trigger ingestion (async, streamed) · apply approved proposals |
| `POST /api/governance` · `POST /api/governance/extract` | author Policy+Threshold+edges · LLM-prefill the wizard |
| `GET /api/events` | **SSE** live event stream (named frames via `sse-starlette`) |
| `POST /api/run-causal` | **retired** — returns 501 (graph construction moved to `kg build`) |

### 6.2 MCP graph server (`harness/mcp/graph_server.py`) — the agent tool surface

stdio FastMCP server launched per agent. Three tool families:

- **Write** (→ arbitration): `create_business_node`, `create_domain_node`, `create_product_node`,
  `create_metric_node`, `create_policy_node`, `create_threshold_node`, `draw_edge`, `add_causal_edge`.
- **Graph read:** `lookup_node`, `search_nodes`, `kg_status`.
- **Offline doc read (never writes):** `list_metrics`, `list_metrics_by_domain`, `list_metrics_by_scope`,
  `get_metric_source`, `get_bc2_sql`, `inspect_bc2_sources`, `lookup_metric_notes`,
  `get_chart_registry_entry`, `validate_edge_candidate`, `explain_edge_candidate`.

These same tools back the Claude Code CLI skills (`kg-status`, `lookup-notes`, `validate-edge`, …).

---

## 7. CLI reference (`kg …`)

```
# Spine / schema
kg schema-init                 apply Neo4j constraints + indexes
kg bootstrap-spine [--dry-run] seed Business/Domain/Product/Platform/UIComponent from seed JSON
kg status                      node/edge counts + spine health
kg lookup <label> <key>        fetch one node

# Agentic build + enrich (primary path)
kg build [--dry-plan] [--smoke] [--namespaces a|b] [--resume]
kg enrich [--dry-run] [--limit N] [--no-dedupe] [--no-migrate]

# Dashboard ingestion (incremental)
kg prepass [--json]
kg ingest-dashboard <id> [--auto-approve]
kg ingest-all / kg ingest-dashboards [--limit N] [--concurrency N] [--auto-approve]
kg proposals list|approve|reject [--run <id>]
kg apply [--run <id>]
kg reconcile [--label Metric] [--dry-run]

# Governance + maintenance
kg seed-governance [--dry-run]
kg migrate-metric-edges [--dry-run]     fold legacy edge types onto the V1 two-edge model
kg discover [--mode synthetic|scan|pcmci] …
kg prune-empty
```

Backup/restore (the safety net) is separate: `python -m harness.store.backup export|restore …`.

---

## 8. Node & edge model

### 8.1 Node labels

Ten labels are defined in the schema; **nine are materialized** in V1:

| Label | Role | Materialized? |
|---|---|---|
| `Business` | tenant root (1 per DB) | ✅ |
| `Domain` | FRD functional column | ✅ |
| `IntelligenceProduct` | IQ app (MarketingIQ, CustomerIQ, …) | ✅ |
| `Platform` | source/action vendor (ga4, google_ads, …) | ✅ |
| `Metric` | the hub node (325 metrics + input/constant nodes) | ✅ |
| `Dashboard` | UI surface from the chart registry | ✅ |
| `UIComponent` | generalized chart type (17 nodes) | ✅ |
| `Policy` | governance rule | ✅ |
| `Threshold` | breach line | ✅ |
| `Role` | RBAC role | ⚠️ **schema-reserved, not materialized** |

> **Role reconciliation note.** `Role` has full schema support — uniqueness constraints
> (`role_id`, `role_key`), a `seniority_rank` index, and a Pydantic model in `harness/kg/models.py`.
> But **no code creates Role nodes**: there is no `create_role_node` MCP tool, no spine/governance
> seed emits one, and the canvas palette (`graphTheme.ts` `LABEL_STYLE`) renders the nine
> materialized labels (plus the synthetic, frontend-only `Chart` view node — see §8.1). RBAC is
> modeled in the schema but deferred in V1. Treat `Role` as reserved.

`Metric.node_kind` further classifies a metric node as `metric` / `intermediary` / `input` / `constant`.

> **Synthetic `Chart` view node (frontend-only, not in Neo4j).** The canvas can render a 10th
> label, **`Chart`**, but it is **never persisted** — it is a client-side view artifact
> (`provenance: "synthetic"`, id `chart::<canonical_id>`) created on shift-click to visualize the
> chart layer (charts are *not* a graph node type; their fields are folded onto `Metric` / the
> chart-registry per the M2 decision). It is hydrated from `/api/metric-chart` (metric reveal) or
> `/api/dashboard-charts` (dashboard reveal), wired by a `RENDERED_BY` (metric→chart) or `SHOWN_ON`
> (chart→dashboard) synthetic edge, and is excluded from search/apply/write paths (FR-CG-008).

### 8.2 Edge model

**Metric ↔ metric** uses exactly two relationship types, each with a fixed `relation` vocabulary:

- `DECOMPOSES_INTO` — structural (formula-derived): `formula · component · identity · rollup ·
  crossproduct · funnel`. A `role` of `denominator`/`subtrahend` makes the hop **inverse** (sign −1).
- `INFLUENCES` — causal: `curated_rule · promoted · llm_verified · statistical · statistical_candidate`,
  carrying `confidence`, `temporal_lag`, sign, and `mechanism`.

**Spine / surface / governance** edges: `HAS_DOMAIN`, `HAS_PRODUCT`, `USES_PLATFORM`,
`PART_OF_DOMAIN`/`BELONGS_TO_DOMAIN`, `PART_OF_PRODUCT`, `SHOWN_ON`, `VISUALIZES`, `GOVERNS`,
`ENFORCES_THRESHOLD`, `HAS_THRESHOLD`.

Edges carry an **evidence ledger** (`evidence_mass`, Beta confidence), `review_state`, `source_kind`,
and a `status` lifecycle. **Edges are never deleted** — superseded edges are marked
`status='deprecated'` (the canvas can show or hide them).

### 8.4 How edges are computed (no precomputed edge table)

The current build does **not** import any `*_edges.rare_seeds.csv` (see §3.3). Every metric↔metric edge
is **produced by an LLM agent**, grounded in formula/SQL evidence and written through arbitration:

1. **Structural `DECOMPOSES_INTO` (Phase 2).** The agent calls `get_metric_source(metric_id)` to read
   `formula_human`, `formula_explanation`, `depends_on`, and `formula_components` (each component's id +
   role), falling back to the dbt mart SQL via `get_bc2_sql` when the formula text is thin. It then emits
   one edge per component — e.g. `roas = revenue / spend` → `roas DECOMPOSES_INTO revenue` (role
   `numerator`) + `roas DECOMPOSES_INTO spend` (role `denominator`) — with `confidence = 1.0` via `draw_edge`.
   The grounding (`formula_components`) lives in `metric_nodes.rare_seeds.json` + the `metric_registry`
   `source_expr`; the **edge objects are created by the agent**, not loaded from `structural_edges.csv`.
2. **Causal `INFLUENCES` (Phase 3).** The agent reasons over the metric notes / mechanisms to draw causal
   edges with a confidence tier, a concrete `mechanism`, a `cross_domain` flag, and a temporal lag, via
   `add_causal_edge` / `draw_edge`.
3. **Statistical edges (optional, separate).** `kg discover` (`harness/discovery/`) runs PCMCI+/Granger/CMIknn
   over `candidate_edges.<tenant>.csv` and writes `discovered_edges.<tenant>.csv`; those become *proposals*
   in the review path — they are not part of `kg build`.

---

## 9. Frontend (`app/kg-canvas`)

A React 19 + Vite single-page app. Graph rendering uses **React Flow (`@xyflow/react`)** with a
**dagre** layered layout (`lib/graphLayout.ts`) and an ego/focus layout (`lib/egoLayout.ts`). State is
a single **Zustand** store (`store.ts`); styling/legends are centralized in **`lib/graphTheme.ts`**
(node palette by label/metric-category, edge styling by `(rel_type, relation)`, confidence→width,
inverse/cross-domain/deprecated treatments). API + SSE wrappers live in `lib/api.ts`.

### Layout shell (`App.tsx`)
`CommandSearch` + `Toolbar` + `ProgressBar` on top; `CanvasView` fills the center; a left
**governance drawer** (`GovernancePanel`) and a right **inspector** overlay the canvas. The inspector
is a tabbed panel: **Activity · Review · Node · Edge · Edge Diff**.

### Components (`src/components/`)
| Component | Purpose |
|---|---|
| `CanvasView` | the React Flow graph: nodes, signed edges, focus/loop rings, layout. Overview toggle **Spine · Tree · Map · Dashboards**; shift-click drill-down (see below). |
| `Toolbar` | filters (scope/domain/category facets), deprecated-edge toggle, traversal toggle, **Legend** popover (provenance + edge styles). |
| `NodeDetail` | full metric/node inspector — formula, SQL, mart sources, lineage; renders **`ChartDetail`** for a selected `Chart` view node. |
| `ChartDetail` | the chart body for a synthetic `Chart` node — canonical glyph per `chart_type`, `chart_id`/`canonical_id`, dashboard chips, formula, how-to-read, decisions, narration. |
| `EdgeDetail` | selected-edge relation, score, lag, lifecycle, mechanism (lives in `App.tsx`). |
| `ReviewQueue` | approve/reject AI-proposed nodes/edges (proposal queue). |
| `EdgeDiffReview` | diff a proposal run against the live graph. |
| `CommandSearch` | command-palette search + locate. |
| `ActivityFeed` · `ProgressBar` | live SSE build/ingest activity + progress. |
| `governance/GovernancePanel` | 3-step Policy→Threshold authoring wizard with optional LLM "prefill from a document". |
| `ThemeToggle` · `theme-provider` · `ui/` | theming + shadcn primitives. |

The frontend is **read-mostly**: it visualizes the graph and drives the **review/approve** and
**governance authoring** workflows. It never writes the graph directly — every mutation goes through
the API → arbitration. (`POST /api/run-causal` is retired; graph construction is `kg build` only.)

### Overview modes & shift-click drill-down (`graphLayout.ts` + `store.ts`)

The canvas overview has four layouts: **Spine** (metrics clustered under their Domain — default),
**Tree** (the decomposition forest), **Map** (the hub skeleton), and **Dashboards** — the curated
**main** dashboards (`dashboard_type ∈ {executive, review}` or id ending `-overview`) clustered under
their **Product** (`dashboardGroupedLayout`). Filtering NODE KINDS to a non-spine label (Dashboard /
Policy / Threshold / Chart) now packs those nodes into a grid instead of blanking (the Spine layout
falls back when it finds no spine nodes).

**Shift-click drill-down** dispatches by node kind and reveals **synthetic, client-only** view
nodes/edges (never persisted — FR-CG-008), which the focus ring then clusters around the clicked node:

- **Metric** → `revealMetricChart` — its specific `Chart` (`RENDERED_BY`), auto-selected so
  `NodeDetail`/`ChartDetail` shows it.
- **Dashboard** → `revealDashboardCharts` — every chart on it (`Chart -[SHOWN_ON]-> Dashboard`, fetched
  from `/api/dashboard-charts`) plus its real `SHOWN_ON` metrics.
- **IntelligenceProduct** → `revealProductDashboards` — that product's dashboards
  (`Dashboard -[PART_OF_PRODUCT]-> Product`).

Charts are surfaced this way because they are **not** a graph node type; the synthetic builder
(`makeChartNode`) merges the chart-registry slice with metric props, keyed by `canonical_id` so the
same chart dedupes whether reached via its metric or its dashboard.

---

## 10. End-to-end data flow

```
┌─────────────── INPUTS ───────────────┐
│ harness/seed/*.json   (spine, platforms, components, governance)
│ data/metric_nodes.rare_seeds.json     (325 metrics + 161 inputs — source of truth)
│ data/metric_registry.rare_seeds.csv   (formula / source_expr / mart — read via get_metric_source)
│ BC_2 dbt seeds + SQL                  (read-only, via MCP doc-tools)
│ (edges are DERIVED by agents from the above — no edge CSV is imported)
└───────────────┬───────────────────────┘
                ▼
   kg bootstrap-spine  ──▶  spine nodes (Business/Domain/Product/Platform/UIComponent)
                ▼
   kg build  (agentic, 4 phases)
     Phase 1 nodes ─BARRIER─▶ Phase 2 structural ─▶ Phase 3 causal ─▶ Phase 4 critique
                ▼
   kg enrich  (dedupe · mart/SQL/freshness · evidence-ledger migrate)
                ▼
   ╔═══════════════════════════════════════════════════════╗
   ║  harness/kg/arbitration.py — THE SINGLE WRITER         ║   ← also: ingest apply,
   ║  validates · MERGEs on identity key · appends event    ║     governance authoring
   ╚═══════════════════════════╤═══════════════════════════╝
                               ▼
                         Neo4j graph
                               │
              ┌────────────────┴────────────────┐
              ▼                                  ▼
   FastAPI + SSE (harness/api)          MCP server (harness/mcp)
              ▼                                  ▼
   React Flow canvas (app/kg-canvas)    Claude agents · Claude Code CLI skills
```

---

## 11. Where to look next

| You want… | Go to |
|---|---|
| Node/edge **property contract** (every field) | `docs/final-schema-claude.md` |
| **Quickstart + full rebuild runbook** | `README.md` |
| **Latest build report** (counts, loops, leaves) | `docs/graph-build-process.md` |
| Metric **identity / ML classification** | `docs/unique-metrics-and-ml-classification.md` |
| **knowledgeGraph integration** (statistical edges in) | `INTEGRATION-ANALYSIS-claude.md` |
| **Historical** schema/skeleton design docs | `docs/archive/` |
