# ThoughtWire Causal Knowledge Graph (dc-kg)

LLM-built **Neo4j** causal knowledge graph + live canvas for the **rare_seeds** tenant,
built to the authoritative spec in [`docs/final-schema-claude.md`](docs/final-schema-claude.md).

The graph is the "business body" an agent wakes into: a single `Business` root with a
tri-axis spine (`Domain ∥ IntelligenceProduct ∥ Platform`), a `Metric` hub, and a
two-type metric→metric edge layer (structural `DECOMPOSES_INTO` + causal `INFLUENCES`).

The spine is **seeded deterministically**; the **metric/edge layer is built by an LLM
agentic harness** (`harness/agentic/`) — an LLM reads every metric and constructs the
nodes + edges itself, auto-approving its writes through the graph MCP tools.

## Architecture (one line)

deterministic spine seed (Phase 0) → LLM **node** agents (parallel, by namespace) →
**BARRIER** → LLM **structural-edge** agents → LLM **weave-causal** agents → LLM
**critique** (loops / orphans / leaves report) — every write lands in Neo4j via the
single `MERGE` arbitration writer.

> **dc-kg is NOT a git repo.** There is no git rollback. The **Neo4j backup**
> (`harness/store/backup.py export`) is the only safety net for graph data — a live
> build always exports first, then wipes, then rebuilds.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python ≥3.12, managed with **uv** |
| Graph DB | **Neo4j 6.x** driver + **`neo4j-rust-ext`** (Rust PackStream accelerator) → local Homebrew Neo4j (Community) |
| Builder | LLM agentic harness over **claude-agent-sdk** (`claude-opus-4-8`, `bypassPermissions`); uses your Claude Code CLI subscription login automatically |
| MCP | **FastMCP** stdio server (`mcp__graph__*`) — write + doc-reading tools shared by the CLI, the SDK harness, and Claude Code |
| Frontend | Vite + React + shadcn/ui + `@xyflow/react` + Zustand |

## Quickstart

```bash
# 0. Neo4j must be running:
brew services start neo4j

# 1. Add your Neo4j password:
#    edit harness/.env  →  NEO4J_PASSWORD=<your password>

# 2. Install (uv creates .venv and installs everything, including the Rust ext):
uv sync --extra dev

# 3. Apply schema constraints/indexes, then seed the tri-axis spine:
uv run kg schema-init
uv run python -m harness.ingest.spine_seed       # 1 Business · 9 Domain · 6 Product · 5 Platform

# 4. Preview the LLM build plan (offline — no SDK, no Neo4j writes):
uv run kg build --dry-plan

# 5. Status:
uv run kg status
```

## The spine (seeded deterministically)

The reusable, client-portable backbone is seeded by
[`harness/ingest/spine_seed.py`](harness/ingest/spine_seed.py) from two local seed files
(`harness/seed/spine_seed.json` + `harness/seed/platforms.json`), each upserted through the
single arbitration writer (idempotent `MERGE` — re-running never duplicates):

```bash
uv run python -m harness.ingest.spine_seed             # seed into Neo4j
uv run python -m harness.ingest.spine_seed --dry-run   # build + validate + print only (no DB)
```

It seeds **1 `Business`** root, **9 `Domain`** functional columns, **6 `IntelligenceProduct`**
apps (`miq`, `ciq`, `piq`, **`storefront_iq`**, `dc`, `creative_iq`), and **5 `Platform`**
source/action vendors (`ga4`, `google_ads`, `meta_ads`, `klaviyo`, `magento`).

> The **Magento platform now displays as "StoreFront IQ"** (`platform_name`), but its
> `platform_id` stays `magento` so Snowflake / dbt-mart lineage joins survive.

## Building the graph (LLM agentic builder)

The metric/edge layer is built by [`harness/agentic/`](harness/agentic/) — driven by
`kg build`. **A live build WIPES + REBUILDS the graph** (it exports a backup first). It runs
**phased-parallel**:

| Phase | What it does |
|---|---|
| **0 — spine-seed** *(deterministic)* | `backup export` → `wipe --yes` → run `spine_seed.py`. The **wipe is skipped on `--smoke`** (subset layered onto the existing graph). |
| **1 — nodes** *(parallel)* | One agent per namespace/domain bucket (~13 buckets, **max 35 metrics each**); each reads its slice via the doc tools and creates `Metric` nodes + spine edges. **BARRIER** — every node exists before any edge is drawn. |
| **2 — structural edges** *(parallel)* | `DECOMPOSES_INTO` edges from each metric's formula. |
| **3 — weave causal** *(parallel)* | `INFLUENCES` edges from reasoning over notes / `depends_on` / BC_2 SQL. |
| **4 — critique** *(single agent)* | Audits the finished graph; writes `data/build-report.<runId>.json` with loops, orphans, and leaves. |

Agents work **only** through the `mcp__graph__*` tools (filesystem/shell/web builtins are
denied) and **auto-approve** their writes (`bypassPermissions`) — there is no human review
queue in this build path.

```bash
# Offline preview: phase plan, node slices, system prompts, resolved SDK options.
# Never imports the SDK and never touches Neo4j.
uv run kg build --dry-plan
uv run kg build --dry-plan --smoke                 # preview just the smoke subset

# Smoke build: build ONLY the blended.* ROAS chain (one small namespace) and
# SKIP the destructive wipe — a fast, non-destructive end-to-end validation.
uv run kg build --smoke

# Full build (DESTRUCTIVE — exports a backup, wipes, then rebuilds the whole graph):
uv run kg build

# Restrict the node phase to specific source namespaces (pipe-delimited):
uv run kg build --namespaces 'google_ads|meta_ads'
```

`kg build` has exactly three flags: `--smoke`, `--namespaces`, and `--dry-plan`.

### Edge model

Two metric→metric edge types, both written via `draw_edge`:

* **`DECOMPOSES_INTO`** (structural / definitional) — carries a `role`
  (`numerator` · `denominator` · `addend` · `subtrahend` · `factor` · `driver` · `component`)
  and `confidence = 1.0` (no decay). The arbitration writer rejects any other role. The
  **sign is derived** from the role (`denominator` / `subtrahend` ⇒ −1, else +1).
* **`INFLUENCES`** (causal) — confidence is a **deterministic fold over an append-only
  evidence ledger** (Beta posterior: `confidence = α/(α+β)`, `evidence_mass = α+β`, Jeffreys
  prior), never set directly — [`arbitration.append_edge_evidence`](harness/kg/arbitration.py)
  is the only writer. Each edge also carries a `temporal_lag` (ISO-8601), `lag_plausibility`,
  a one-sentence `mechanism`, and a `cross_domain` flag. A causal edge is **never** written
  parallel to a `DECOMPOSES_INTO` pair — the formula edge (pinned 1.0) subsumes it.

> The ledger is live with LLM **prior** evidence (the legacy `0.8/0.6/0.4` tiers were
> migrated into seeded prior events). The **observational** layer — lagged cross-correlation
> + FDR over the mart time-series (Snowflake; [`harness/stats/correlation.py`](harness/stats/correlation.py)
> + the `kg discover` engine) — is **deferred**: when wired it appends OBSERVATIONAL evidence
> and a data-estimated `temporal_lag` into the same ledger.

### Enriching the graph (marts · SQL · evidence ledger)

`kg enrich` runs a **deterministic, additive, idempotent** pass (no LLM, no wipe) over the
live graph (the same functions also run inside `build()`, so a fresh build is natively
enriched):

```bash
uv run kg enrich                 # dedupe + mart/SQL/freshness + ledger migration
uv run kg enrich --dry-run       # report only; write nothing
```

It (1) removes any `INFLUENCES` that parallels a formula edge (`critique_dedupe`),
(2) populates `mart_sources` / `source_columns` / `sql_query_real` / freshness on each metric
— binding marts from the BC_2 repo class `MART_NAME` + the registry `mart_source`
(platform-namespace aliased, e.g. `google_ads`→`google`), filtered to the real dbt mart
inventory — and (3) folds each legacy `INFLUENCES` flat confidence onto the Beta ledger
(`migrate_edge_ledger`). `sql_query_canonical` is generated by a separate LLM pass.

### Metric node fields

Each `Metric` node carries, beyond identity/classification: `node_kind`
(`metric` · `intermediary` · `input` · `constant`), `has_endpoint`, the ML fields
`is_ml` / `ml_kind` (`prediction` · `performance` · `hybrid`) / `ml_task` / `ml_model` /
`ml_entity`, `chart_id` + `chart_type`, `source_expr` (SQL expr from the metric registry),
and `bc2_ref` (backend repo `file:line`).

**Mart / SQL / freshness** (populated by `kg enrich`): `mart_sources` (the dbt mart table(s)
the metric reads, as `MARTS.<table>`), `source_columns` (the mart columns it uses),
`sql_query_real` (the verbatim backend SQL), `sql_query_canonical` (a clean, runnable
`SELECT … FROM DB_{tenant}.<mart> …`), and the freshness window `history_start` /
`history_end` / `n_periods` / `data_stale`. ML-prediction metrics carry no mart (they are
query-time model outputs, not mart-backed). `/api/column-impact?column=<COL>` answers "which
metrics break if this column changes" from `source_columns`.

### OpenAPI ingestion filter

[`harness/ingest/endpoint_filters.py`](harness/ingest/endpoint_filters.py) keeps only
KG-relevant routes. `DENY_GROUPS` excludes the operational route groups —
`admin`, `auth`, `master-config`, `feature-flags`, `tenants`, `support`, `audit-log`,
`health`, `discovery`, `alerts-config`, `data-quality` — so they never seed a metric or
become a metric's endpoint. The 8 `operational.*` metrics are dropped from the node set.

## Full rebuild runbook

The exact, ordered sequence to rebuild the whole graph from a clean slate. Run from the
repo root with Neo4j up and `harness/.env` carrying `NEO4J_PASSWORD`.

```bash
# ── 0. Neo4j up + password ────────────────────────────────────────────────────
brew services start neo4j                          # Neo4j must be running
#    edit harness/.env  →  NEO4J_PASSWORD=<your password>
uv sync --extra dev                                # core runtime + pytest

# ── 1. Back up the current graph (the ONLY safety net — do this first) ─────────
uv run python -m harness.store.backup export       # → data/backups/neo4j-backup-<ts>.json (verified)
#    to undo a later wipe:
#    uv run python -m harness.store.backup restore data/backups/neo4j-backup-<ts>.json

# ── 2. Preview the build plan (offline — no SDK, no writes) ────────────────────
uv run kg build --dry-plan                         # phases, slices, prompts, resolved SDK options

# ── 3. Smoke-build to validate the pipeline (non-destructive — wipe skipped) ───
uv run kg build --smoke                            # builds only the blended.* ROAS chain

# ── 4. Full build (DESTRUCTIVE — Phase 0 exports a backup, then wipes + rebuilds)
uv run kg build                                    # phased-parallel LLM build of the whole graph

# ── 5. Review the build report ────────────────────────────────────────────────
#    data/build-report.<runId>.json — node/edge counts, loops, orphans, leaves
uv run kg status                                   # spine + metric + edge counts > 0

# ── 6. Start the canvas (backend, then frontend) ──────────────────────────────
uv run uvicorn harness.api.server:app --port 8000  # backend (FastAPI + SSE)
cd app/kg-canvas && bun run dev                     # frontend → http://localhost:5173
```

`kg build` does **not** re-apply the schema — its Phase 0 only backs up, wipes, and re-seeds
the spine. The constraints/indexes are assumed to exist, so run `kg schema-init` once on a
fresh DB (Quickstart step 3). Every step above flows through the single arbitration writer;
nothing is destroyed without the explicit Phase-0 backup first.

## Dev servers (canvas)

Start the **backend** first (Neo4j must be running and `harness/.env` must have
`NEO4J_PASSWORD` set):

```bash
# FastAPI + SSE on http://127.0.0.1:8000
uv run uvicorn harness.api.server:app --port 8000

# equivalent:
uv run python -m harness.api.server
```

Then start the **frontend** (Vite proxies `/api` → the backend):

```bash
cd app/kg-canvas
bun install          # first time only
bun run dev          # → http://localhost:5173
```

Health check: `http://127.0.0.1:8000/api/health`

**Canvas UI:** a persistent, decoupled sidebar + filter panel (state survives reloads;
toggling sidebar tabs never hides the filter); a **signed / cyclic TraversalPanel**
(upstream / downstream paths with per-hop sign and `path_sign`); **shift-click** a metric to
render its chart in the `MetricChartPanel`; and edge styling that reflects sign,
`cross_domain`, and leaf / loop membership. There is **no Run-Causal button** (the graph is
built by `kg build`, not from the canvas).

**API surface:** traversal (`/api/traverse/{upstream,downstream}`) returns
`{paths, cyclic_paths, summary}` — acyclic paths in `paths`, loop-bearing paths surfaced
separately in `cyclic_paths` — each path carrying per-hop `sign` and a `path_sign`.
`/api/metric-chart?metric_uid=` backs shift-click. `POST /api/run-causal` is removed and
returns **501 Not Implemented** (superseded by the agentic builder).

### Driving it from the Claude Code CLI

With `.mcp.json` registered, restart Claude Code in this dir. The graph MCP server exposes:

* **Write tools:** `create_business_node`, `create_domain_node`, `create_product_node`,
  `create_metric_node`, `draw_edge`.
* **Doc-reading tools** (read source files, never the graph): `list_metrics`,
  `get_metric_source`, `get_bc2_sql`, `lookup_metric_notes`, `get_chart_registry_entry`,
  `inspect_bc2_sources`.
* **Graph reads:** `lookup_node`, `search_nodes`, `kg_status`, `list_metrics_by_domain`,
  `list_metrics_by_scope`, `validate_edge_candidate`, `explain_edge_candidate`.

A `PreToolUse` hook renders a field table for each proposed spine node and asks you to
confirm before it is written.

## CLI reference

```
kg schema-init           Apply Neo4j constraints and indexes.
kg bootstrap-spine       Upsert the spine from the seed (via the kg CLI; see also spine_seed).
kg status                Node counts per label + existing constraints.
kg lookup <label> <key>  Fetch one node by label + key.
kg build                 Build the metric/edge layer with the LLM agentic builder
                           (--smoke | --namespaces 'a|b' | --dry-plan).
kg enrich                Deterministic mart/SQL/columns/freshness enrichment + causal
                           dedupe + Beta-ledger migration over the live graph (additive,
                           idempotent; --dry-run | --limit N | --no-dedupe | --no-migrate).
kg migrate-metric-edges  Rewrite legacy ROLLS_UP_TO / CORRELATES_WITH / CAUSES edges onto
                           the V1 DECOMPOSES_INTO + INFLUENCES model (originals deprecated).
kg prune-empty           Delete Domains / chart-types that no Metric uses.
kg reconcile             Collapse duplicate nodes (e.g. concept metrics).
kg discover              Optional [discovery] extra: synthetic / scan / PCMCI+ feed
                           (writes data/discovered_edges.<tenant>.csv; not used by `kg build`).
```

The `prepass` / `ingest-dashboard` / `ingest-all` / `proposals` / `apply` subcommands belong
to the older deterministic ingestion engine (review-queue path) and remain available, but the
metric/edge **graph is built by `kg build`**, not by them.

## Backup / restore (full graph, online, APOC-free)

```bash
uv run python -m harness.store.backup export        # → data/backups/neo4j-backup-<ts>.json (verified)
uv run python -m harness.store.backup info <file>   # show backup metadata
uv run python -m harness.store.backup restore <file># re-create nodes + relationships
uv run python -m harness.store.backup wipe --yes    # drop schema + all data (destructive)
```

## Layout

```
harness/
  agentic/   LLM build harness — orchestrator (phased-parallel slicing + run),
             runner (ClaudeAgentOptions + Phase 0 backup/wipe/seed), prompts (per-phase)
  ingest/    spine_seed, endpoint_filters, openapi_inventory, bc2_snapshot, edge_scoring,
             + the older review-queue pipeline (prepass·proposer·apply·orchestrator)
  kg/        driver · schema · models · arbitration (single MERGE writer) · reconcile · config
  mcp/       graph_server (FastMCP mcp__graph__* — write + doc-reading tools)
  cli/       kg (the `kg` console script)
  api/       server (FastAPI + SSE; traverse · metric-chart)
  store/     backup (export/restore/info/wipe) · proposals · jsonl
  seed/      spine_seed.json · platforms.json · component_types.json · …
  discovery/ optional PCMCI+/Granger/CMIknn engine ([discovery] extra, lazy-loaded)
app/kg-canvas/   Vite canvas — graph view · node/edge detail · TraversalPanel ·
                 MetricChartPanel · Cmd+K search · persistent sidebar/filter
data/      build-report.<runId>.json · backups/ · events.jsonl · metric_nodes.rare_seeds.json
docs/      final-schema-claude.md (THE spec) · frd-docs/
.claude/   MCP registration, hooks, slash commands
```

## Status

- **Spine** ✅ seeded deterministically (`spine_seed.py`): 1 Business · 9 Domain ·
  6 IntelligenceProduct (incl. `storefront_iq`) · 5 Platform (Magento displayed as
  "StoreFront IQ"). Idempotent `MERGE` — re-runs never duplicate.
- **LLM agentic builder** ✅ `harness/agentic/` + `kg build`: phased-parallel
  (seed → nodes → BARRIER → structural → weave → critique), auto-approved writes via the
  `mcp__graph__*` tools, build report to `data/build-report.<runId>.json`. Offline
  `--dry-plan` preview; non-destructive `--smoke` validation on the `blended.*` chain.
- **Edge model** ✅ two metric→metric types: structural `DECOMPOSES_INTO` (role + derived
  sign + confidence 1.0) and causal `INFLUENCES` (LLM confidence tier + mechanism +
  `cross_domain`). Statistical (Snowflake / PCMCI) scoring deferred.
- **Canvas** ✅ persistent decoupled sidebar/filter, signed/cyclic TraversalPanel,
  shift-click MetricChartPanel, edge sign / cross-domain / leaf-loop styling.
- **Deferred:** temporal DAG, statistical scoring, governance population (Policy / Threshold
  / RBAC), multi-client import tooling (schema fields stay defined).
</content>
</invoke>
