# ThoughtWire Causal Knowledge Graph (dc-kg)

AI-first ingestion engine + **Neo4j** causal knowledge graph + live canvas, built to the
authoritative spec in [`docs/final-schema-claude.md`](docs/final-schema-claude.md) and the
approved plan in [`docs/implementation-kg-plan.md`](docs/implementation-kg-plan.md).

The graph is the "business body" an agent wakes into: a single `Business` root with a
tri-axis spine (`Domain ∥ IntelligenceProduct ∥ Platform`), a `Metric` hub, and an
evidence-backed causal layer — built by parallel proposer agents and **reviewed before write**.

## Architecture (one line)

deterministic pre-pass → per-dashboard proposer agents (own context, read-only) →
proposal queue → **single arbitration writer** (idempotent `MERGE` on canonical id →
no write races, no dup nodes) → canvas review (accept/edit/reject) → reconcile → causal pass.

## Stack

| Layer | Choice |
|---|---|
| Backend | Python ≥3.12, managed with **uv** |
| Graph DB | **Neo4j 6.x** driver + **`neo4j-rust-ext`** (Rust PackStream accelerator) → local Homebrew Neo4j (Community) |
| Agent | **claude-agent-sdk** (uses your Claude Code CLI subscription login automatically) |
| MCP | **FastMCP** stdio server (`mcp__graph__*`), shared by the CLI and the SDK harness |
| Frontend (M2) | Vite + React + shadcn/ui + `@xyflow/react` + Zustand |

## Quickstart (M1 — spine bootstrap)

```bash
# 0. Neo4j must be running (you already have it):  brew services start neo4j
# 1. Add your Neo4j password:
#    edit harness/.env  →  NEO4J_PASSWORD=<your password>

# 2. Install (uv creates .venv and installs everything, including the Rust ext):
uv sync --extra dev

# 3. Apply schema constraints/indexes:
uv run kg schema-init

# 4. Bootstrap the spine (Business / Domain / IntelligenceProduct):
uv run kg bootstrap-spine        # non-interactive path (smoke test)

# 5. Status:
uv run kg status
```

## KG skeleton build (meaningful metrics + causal edges)

Builds scope-correct `Metric` nodes — **one clean core formula each** — from the chart-registry +
OpenAPI dynamic-metric inventory, **enriched by the BC_2 dbt seeds**, then the two-type causal layer
(`DECOMPOSES_INTO` + `INFLUENCES`, with `relation` subtypes). Full design + rationale:
[`docs/kg-skeleton-build-implementation.md`](docs/kg-skeleton-build-implementation.md).

```bash
# Inspect/validate the BC_2 offline snapshot (hashes, row counts, SQL-noise rejection) — no writes
uv run kg import-bc2-snapshot --tenant rare_seeds

# Build scoped Metric nodes (one core formula each):
uv run kg build-skeleton --tenant rare_seeds --dry-run --write-csv   # compute + artifacts, no write
uv run kg build-skeleton --tenant rare_seeds                          # write metric proposals to queue
uv run kg build-skeleton --tenant rare_seeds --apply-safe            # approve + apply nodes via arbitration

# Causal edges (review-only except auto-safe formula/component):
uv run kg run-causal --reconcile     # deterministic edges + deprecate stale (never delete)
uv run kg run-causal --dry-run       # compute + write data/skeleton/edge_diff.* , no writes
uv run kg run-causal --llm           # + curated cross-domain + LLM residual (judge+refuter, review-only)

# Migrate any legacy ROLLS_UP_TO / CORRELATES_WITH / CAUSES edges to the 2-type model:
uv run kg migrate-metric-edges --dry-run
```

**Edge model:** `DECOMPOSES_INTO` (`relation` ∈ formula·component·identity·rollup·crossproduct·funnel)
+ `INFLUENCES` (`relation` ∈ curated_rule·llm_verified·statistical·statistical_candidate·promoted).
`formula`/`identity` are hard same-scope (Google-Search never → YouTube); `crossproduct`/`rollup` are
additive channel→blended (a ratio is never summed). Artifacts land in `data/skeleton/`
(`canonical_metric_registry.<t>.json`, `metric_registry.<t>.csv`, `composites.<t>.csv`,
`coverage_report.<t>.json`, `edge_diff.<t>.<run>.json`).

**Blank-canvas bootstrap** (destructive — back up first): `backup export` → `backup wipe --yes` →
`schema-init` → `bootstrap-spine` → `build-skeleton --apply-safe` → `link-spine --apply-safe` →
`run-causal --reconcile` → (`run-causal --llm` / `import-discovered-edges`) → review → `apply`.
**The exact, copy-pasteable ordered sequence is the [Full rebuild runbook](#full-rebuild-runbook-backup--fresh-build--spine--causal--stats) below.**

**MCP proposal tools + slash commands** (proposal-only; never bypass arbitration):
`/inspect-bc2`, `/propose-skeleton`, `/propose-influences`, `/validate-edge`, `/lookup-notes`
(backed by `mcp__graph__{inspect_bc2_sources, propose_metric_nodes, propose_metric_edges_from_formula,
propose_metric_to_spine_edges, propose_influence_candidates, validate_edge_candidate,
explain_edge_candidate, lookup_metric_notes, list_metrics_by_domain, list_metrics_by_scope,
get_chart_registry_entry}`).

## Statistical discovery integration (knowledgeGraph feed)

Folds the teammate's *measured* causal-discovery output (PCMCI+ / Granger / FDR / deseasonalized,
nonlinear CMIknn) into the graph as **review-only `INFLUENCES {relation: statistical}`** edges through
the same proposal → review → arbitration path — replacing the 4 hand-typed correlation seeds with
measured evidence. **Self-contained**: the discovery engine is ported into `harness/discovery/` behind
an optional `[discovery]` extra, so the heavy scientific stack (`tigramite`/`statsmodels`) never enters
the core runtime and there is **no runtime dependency on the sibling repo**.

```bash
# Import the (vendored, offline) discovered-edges feed → review-only INFLUENCES proposals
uv run kg import-discovered-edges --tenant rare_seeds --dry-run   # resolution report, no writes
uv run kg import-discovered-edges --tenant rare_seeds             # → proposal queue

# Regenerate the feed yourself (opt-in; heavy stats isolated from core)
uv sync --extra discovery
uv run kg discover --mode synthetic --tenant smoketest            # offline self-test (no network)
TW_API_BASE=http://localhost:8005 \
  uv run kg discover --mode pcmci --tenant rare_seeds             # live: needs BC_2 API + Snowflake creds
```

Platform-coarse ids (`google.roas`) resolve onto the **existing** scoped nodes (`metric:google:roas`)
via a scope-map + chart-map + aliases, **strict same-scope** (never cross-scope); unresolved / `ml.*` /
chart-noise pairs are logged with reason codes in `data/skeleton/discovery_import.<t>.json` (no silent
drops). Edges carry a **measured** Beta weight (`|corr|` × `discovery_score` × `fdr_pass`), are FDR-gated,
**reconcile-protected** (`kg_discovery` never auto-deprecated), never overwrite a human-reviewed edge,
and never promote to `CAUSES`. Upstream/downstream traversal (`/api/traverse/*`) spans both edge types
with **structural ("made-of") vs causal ("driven-by") hop labels**. Proposal-only MCP tool +
`/import-discovery` slash command (`mcp__graph__propose_discovery_edges`). Vendored fixtures:
`data/discovered_edges.<t>.csv`. Full design: [`docs/kg-integration-plan-claude.md`](docs/kg-integration-plan-claude.md)
+ [`INTEGRATION-ANALYSIS-claude.md`](INTEGRATION-ANALYSIS-claude.md).

## Full rebuild runbook (backup → fresh build → spine → causal → stats)

The exact, ordered sequence to rebuild the whole graph from a clean slate. Every step flows through
the single arbitration writer; nothing is ever destroyed without an explicit backup first. Run from the
repo root with Neo4j up and `harness/.env` carrying `NEO4J_PASSWORD`. The default `--tenant` is
`rare_seeds` throughout — change it consistently if you rebuild another tenant.

```bash
# ── 0. Install ────────────────────────────────────────────────────────────────
brew services start neo4j                      # Neo4j must be running
uv sync --extra dev                            # core runtime + pytest (everything except heavy stats)
#   (only if you will REGENERATE the discovery feed yourself — step 6b:)
# uv sync --extra discovery                     # adds tigramite/statsmodels/… (heavy, isolated)

# ── 1. Back up the current graph (non-destructive — do this first) ────────────
uv run python -m harness.store.backup export   # → data/backups/neo4j-backup-<ts>.json (verified)
# uv run python -m harness.store.backup info data/backups/neo4j-backup-<ts>.json   # optional: inspect

# ── 2. Start fresh (DESTRUCTIVE — drops schema + all nodes/edges) ─────────────
uv run python -m harness.store.backup wipe --yes
#   to undo: uv run python -m harness.store.backup restore data/backups/neo4j-backup-<ts>.json

# ── 3. Schema + tri-axis spine (Business · 9 Domain · 5 Product · 5 Platform · chart-types) ──
uv run kg schema-init                          # constraints + indexes
uv run kg bootstrap-spine                      # seeds spine + Platform nodes + HAS_/USES_PLATFORM edges
uv run kg status                               # sanity: node counts + constraints

# ── 4. Build the metric skeleton (scoped Metric nodes, one clean formula each) ─
uv run kg import-bc2-snapshot --tenant rare_seeds                    # inspect/validate sources, no writes
uv run kg build-skeleton --tenant rare_seeds --dry-run --write-csv   # compute + artifacts, no writes
uv run kg build-skeleton --tenant rare_seeds --apply-safe            # apply Metric nodes via arbitration

# ── 5. Connect every metric to the spine (Domain · Product · Platform) ────────
uv run kg link-spine --tenant rare_seeds --dry-run        # coverage per axis; asserts 0 metrics → 'dc'
uv run kg link-spine --tenant rare_seeds --apply-safe     # apply deterministic BELONGS_TO_DOMAIN / PART_OF_PRODUCT / SOURCES
# uv run kg link-spine --tenant rare_seeds --llm           # OPTIONAL: LLM fills the residual axes (review-only)

# ── 6a. Causal edges — decomposition + influences ─────────────────────────────
uv run kg run-causal --auto-approve --reconcile   # land auto-safe DECOMPOSES_INTO{formula} + deprecate stale
#   (identity / rollup / crossproduct / funnel DECOMPOSES_INTO are deterministic but held REVIEW-ONLY by
#    policy — apply them in step 7. Bare `run-causal --reconcile` queues everything for review instead.)
# uv run kg run-causal --llm                        # OPTIONAL: curated + LLM-residual INFLUENCES (judge+refuter, review-only)

# ── 6b. Densify metric→metric edges (relationship layers) ─────────────────────
# Native: emit the BC_2 dbt-seed relationship layer (component_of/computes → DECOMPOSES_INTO,
# correlated_with/causes → INFLUENCES). Same-scope structural edges auto-apply; INFLUENCES review-only:
uv run kg import-bc2-edges --tenant rare_seeds --dry-run             # resolution report per relation
uv run kg import-bc2-edges --tenant rare_seeds --apply-safe          # land same-scope structural; hold influences
# Import knowledgeGraph's vendored edge layers (structural/compositional/crossproduct), all review-only:
uv run kg import-graph-edges --tenant rare_seeds --dry-run           # per-layer resolution report
uv run kg import-graph-edges --tenant rare_seeds                     # → review-only DECOMPOSES_INTO proposals

# ── 6c. Statistical discovery (the "stats" feed) ──────────────────────────────
# Uses the VENDORED, offline feed by default — no network, no sibling repo needed:
uv run kg import-discovered-edges --tenant rare_seeds --dry-run      # resolution report, no writes
uv run kg import-discovered-edges --tenant rare_seeds               # → review-only INFLUENCES{statistical} proposals
#   Regenerate the feed yourself (opt-in; needs `uv sync --extra discovery` from step 0):
# uv run kg discover --mode synthetic --tenant smoketest             # offline self-test (no network)
# TW_API_BASE=http://localhost:8005 uv run kg discover --mode pcmci --test cmiknn --tenant rare_seeds  # live: needs API + creds

# ── 7. Review + apply the review-only proposals (steps 5 --llm / 6a --llm / 6b) ─
uv run kg proposals list                       # newest run; --run <id> for a specific one
uv run kg proposals approve --all --run <id>   # or approve/reject individually (or use the canvas queue)
uv run kg apply --run <id>                     # land the approved proposals via arbitration

# ── 8. Verify ─────────────────────────────────────────────────────────────────
uv run kg status                               # spine + metric + edge counts > 0
uv run pytest harness/tests -q                 # full suite green (266 passed)
```

**What auto-applies vs what waits for review:** `bootstrap-spine`, `build-skeleton --apply-safe`,
`link-spine --apply-safe`, and the deterministic stages of `run-causal` land automatically (idempotent,
`review:false`). Everything LLM or statistical — `link-spine --llm`, `run-causal --llm`,
`import-discovered-edges` — is **review-only**: it writes proposals you triage in step 7 (CLI or the
canvas review queue). Re-running any step is idempotent: a second `build-skeleton`/`link-spine`/
`run-causal` is a no-op when nothing changed, and `run-causal --reconcile` deprecates (never deletes)
edges the recompute no longer produces.

## Dev servers (canvas)

Start the **backend** first (Neo4j must be running and `harness/.env` must have `NEO4J_PASSWORD` set):

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

### Driving it from the Claude Code CLI (the intended UX)

With `.mcp.json` registered, restart Claude Code in this dir, then:

```
/create-business-node          # confirm-before-create table → approve → written
/create-domain-node marketing
/create-product-node miq
/kg-status
```

A `PreToolUse` hook renders a field table for each proposed node and asks you to confirm;
nodes derived from excluded endpoints (`master-config`, `POST/DELETE`, `/auth`…) are denied.

## Layout

```
harness/   Python backend — kg/ (driver·schema·models·arbitration·reconcile),
           mcp/ (graph_server), cli/ (kg), hooks/, store/, api/, seed/,
           ingest/ (prepass·proposer·apply·causal + skeleton·bc2_snapshot·openapi_inventory·edge_scoring·import_discovery),
           discovery/ (ported PCMCI+/Granger/CMIknn engine — optional [discovery] extra, lazy-loaded)
harness/seed/  spine_seed, component_types, rare_seeds_*, identities, funnel_flow,
               concept_causal_rules, formula_overrides.<t>, skeleton_overrides.<t>
app/       Vite canvas (graph view · node/edge detail · review queue · edge-diff · traversal · Cmd+K search · discovery evidence)
.claude/   MCP registration, hooks, slash commands
data/      events.jsonl, proposals, backups, runs, discovered_edges.<t>.csv,
           skeleton/ (canonical_metric_registry, coverage, edge_diff, discovery_import…)
docs/      final-schema-claude.md (THE spec), kg-skeleton-build-implementation.md, frd-docs/
```

### Backup / restore (full graph, online, APOC-free)

```bash
uv run python -m harness.store.backup export        # → data/backups/neo4j-backup-<ts>.json (verified)
uv run python -m harness.store.backup info <file>   # show backup metadata
uv run python -m harness.store.backup restore <file># re-create nodes + relationships
uv run python -m harness.store.backup wipe --yes    # drop schema + all data (destructive)
```

## Status

- **M1 — spine bootstrap** ✅ _complete & verified on live Neo4j_: schema (12 constraints,
  6 indexes), single arbitration writer (idempotent MERGE), FastMCP graph server,
  confirm-before-create + exclusion hook, spine slash commands + CLI, 26 passing tests.
  DB is a pristine V1 graph (Business + 9 Domain + 5 IntelligenceProduct).
- **M2 — metric/UIComponent ingestion engine + canvas** ✅ _engine + canvas live_:
  deterministic pre-pass → per-dashboard proposer agents → proposal queue → single
  arbitration writer → reconcile; Vite canvas + SSE + review queue. 432 `Metric` +
  full spine on live Neo4j (ingest of the remaining dashboards is resumable).
- **M3 — causal layer (the point)** ✅ _implemented & verified_: `ingest/causal.py` +
  `kg run-causal` + `/run-causal`. Two metric→metric edge types — `DECOMPOSES_INTO` (relation
  formula·component·identity·rollup·crossproduct·funnel) + `INFLUENCES` (relation
  curated_rule·llm_verified·statistical·statistical_candidate·promoted) — `ROLLS_UP_TO` /
  `CORRELATES_WITH` / `CAUSES` retired into `relation` (with `migrate-metric-edges`). Deterministic
  formula/identity/rollup/crossproduct/funnel + LLM-proposed influences (`--llm`: judge + refuter +
  self-consistency + `Beta(α,β)`, **review-only**). Per-edge scoring at creation
  (`ingest/edge_scoring.py`); stale deterministic edges deprecated, never deleted
  (`kg run-causal --reconcile`).
- **KG skeleton build (BC_2 ingestion)** ✅ _implemented & verified_: meaningful scope-correct
  `Metric` nodes with **one clean core formula each** from chart-registry + OpenAPI dynamic-metric
  inventory + BC_2 dbt seeds (`ingest/{openapi_inventory,bc2_snapshot,skeleton}.py`,
  `kg import-bc2-snapshot` / `kg build-skeleton`). Composite tables decomposed (not metrics);
  hard same-scope gate (Google-Search never → YouTube); 11 proposal-only MCP tools + 5 slash
  commands; canvas redesign (relation styling, edge detail, edge-diff, traversal). `kg-canvas` builds
  clean. Full reference:
  [`docs/kg-skeleton-build-implementation.md`](docs/kg-skeleton-build-implementation.md).
- **Statistical discovery integration (knowledgeGraph feed)** ✅ _implemented & verified_: the PCMCI+ /
  Granger / FDR feed imported as review-only `INFLUENCES{statistical}` onto existing nodes
  (`ingest/import_discovery.py`, `kg import-discovered-edges`) — 29/61 of the vendored feed resolved,
  the rest logged with reason codes (no silent drops). Discovery engine ported **self-contained** behind
  the optional `[discovery]` extra (`harness/discovery/`, `kg discover --mode synthetic|scan|pcmci`);
  strict same-scope resolution (scope-map + chart-map + aliases); **measured** Beta weight; `kg_discovery`
  reconcile-protected + no-clobber of reviewed edges; unified structural/causal traversal with labeled
  hops; canvas adds Cmd+K search, a discovery-evidence panel, a discovery review bucket, an always-visible
  edge legend, and scope/domain filters. Reference:
  [`docs/kg-integration-plan-claude.md`](docs/kg-integration-plan-claude.md).
- **Tri-axis spine connection** ✅ _implemented & verified_: every skeleton Metric is wired onto the
  spine — `BELONGS_TO_DOMAIN` (Domain), `PART_OF_PRODUCT` (IntelligenceProduct), `SOURCES`
  (Platform) — by a deterministic-first cascade (`ingest/spine_links.py`, `kg link-spine`), multi-valued
  (a metric can carry several domains/products/platforms; blended metrics union their components'
  platforms), sourced **only** from chart-registry + OpenAPI + dc-kg enums (no BC_2 seeds). Platform
  nodes (ga4·google_ads·meta_ads·klaviyo·magento) are seeded at `bootstrap-spine`. **Decision Canvas
  (`dc`) is never auto-assigned** (built separately) — the LLM-residual vocab excludes it, a hard guard
  drops any `dc` the model returns, and the CLI asserts 0 `dc` assignments. The LLM (`--llm`) only fills
  the genuinely-ambiguous residual axes, review-only.
- **Metric→metric densification (relationship layers)** ✅ _implemented & verified_: the dormant BC_2
  dbt-seed relationship layer (`component_of`/`computes` → `DECOMPOSES_INTO`, `correlated_with`/`causes`
  → `INFLUENCES`) is now emitted as edges (`ingest/relationship_edges.py`, `kg import-bc2-edges`) — a
  three-tier resolver (provenance ref → coded-name reconciliation → concept) maps BC_2 ids onto live
  metrics, with a **same-scope hard gate** (cross-scope structural dropped) and direction flips for
  `component_of`/`computes`. knowledgeGraph's vendored edge layers (structural/compositional/crossproduct)
  import review-only via `ingest/import_graph_layers.py` / `kg import-graph-edges`. Together: **metric→metric
  edges 333 → 404, leaf metrics 605 → 582.** The remaining fine-grained leaves need the (deferred)
  statistical layer — `kg discover --mode pcmci --test cmiknn` against the live API. **274 passing tests**
  (incl. an end-to-end `test_smoke.py` exercising every CLI/importer/API/MCP surface); `kg-canvas` builds
  clean (bun).
- M4 — RBAC + Org-Graph engine
