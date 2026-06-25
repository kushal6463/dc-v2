# knowledgeGraph — Architecture & File-by-File Reference

> **What this repo is (today):** a file-based **causal-discovery pipeline** over Thoughtwire's
> ~500+ enterprise metrics. It reads BC_ANALYTICS through its APIs (read-only) + dbt source for
> metadata, builds a per-tenant **metric registry**, discovers typed directed **edges** across six
> independent providers, and exports a single canonical artifact `data/graph.<tenant>.json` plus
> self-contained HTML explorers.
>
> **What it was *intended* to be:** the proactive *breach → Thought* knowledge graph described in
> `docs/07-KG-ARCHITECTURE.md`. That doc openly notes the repo "drifted into a time-series-discovery
> tool." See the **Deviation note** at the end — it matters for how this repo relates to `dc-kg`.

This document is generated as a companion to the integration analysis
(`INTEGRATION-ANALYSIS-claude.md`). It catalogs **every module** under `tools/` (31 scripts), the
data artifacts they produce, and the design rationale (including *why CMIknn*).

---

## 1. The big picture

```
BC_ANALYTICS APIs ─┐
dbt marts / SQL  ──┼─► [Phase 0: REGISTRY]  catalog → seed → enrich → classify → finalize → merge → validate
handler code     ──┘            │
                                ▼
                    metric_registry.<tenant>.csv   (the central node table, ~60 columns)
                                │
                                ▼
                    [Phase 1: DISCOVERY]  admissibility → 6 edge providers
                                │   structural · temporal · model · compositional · crossproduct · alias
                                ▼
                    *_edges.<tenant>.csv  (one file per provider)
                                │
                                ▼
                    [Phase 2: ASSEMBLY]  enforce_dag → export_graph
                                │
                                ▼
                    data/graph.<tenant>.json   ◄── the ONE canonical artifact
                                │
                ┌───────────────┼────────────────┐
                ▼               ▼                 ▼
        graph.<t>.html   pipeline.<t>.html   trace_breach (breach → Thought)
```

**Orchestrated by** `tools/run_pipeline.py`, which runs ~22 of these steps in order, idempotently,
with no human gates. One command rebuilds the whole graph; `--interval N` turns it into a daemon.

**Tech:** Python. The registry/edge/graph/UI layers are **pure standard library** (csv, json, os) —
they run with zero third-party deps. The **discovery engine** is the only heavy part (numpy, scipy,
statsmodels, optionally tigramite + torch for GPU CMIknn).

**Identity & tenancy:** node id = `scope.metric[.agg]` (e.g. `blended.revenue`, `meta.impressions`).
IDs are **tenant-free**; the per-tenant *file* is the boundary (`metric_registry.rare_seeds.csv`).
Every node also carries a `tenant` column, but there is no tenant prefix on IDs and no cross-tenant
graph.

---

## 2. The orchestrator

### `tools/run_pipeline.py`
The autonomous Phase-0/Phase-1 driver. Defines `STEPS` — an ordered list of `(name, argv)` for ~22
sub-scripts — and runs each as a subprocess, threading `--repo`, `--tenant`, and `--api-base`
through. **Self-onboarding** (re-discovers endpoints each run), **self-upgrading** (`try_self_upgrade`
attempts dbt manifest + Snowflake `INFORMATION_SCHEMA`, degrades gracefully if absent),
**self-deciding** (rules + review queue, never blocks on a human), **self-reporting** (writes
`data/run_report.<tenant>.json` with registry/discovery/graph stats and new/removed nodes),
**self-scheduling** (`--interval N`). API base resolves CLI → `TW_API_BASE` → `config.json` →
`localhost:8005`.
- **Run:** `python tools/run_pipeline.py --repo E:/BC_ANALYTICS --tenant rare_seeds [--interval 3600]`
- **Reads:** `config.json`, the prior `.nodes_snapshot.<tenant>.json`. **Writes:** `run_report.<tenant>.json`, the node snapshot, and (indirectly) every artifact below.

---

## 3. Phase 0 — Registry & Catalog

### `tools/build_metric_catalog.py`
Discovers **every served series** by unioning two sources: live-API enumeration (pattern-agnostic;
any id) and the BC_ANALYTICS in-code handler maps. De-dupes on `(dashboard, level, id)`.
- **Reads:** BC_ANALYTICS repo (`backend/app/repositories`, `backend/app/features`), an OpenAPI spec or live API. **Writes:** `data/metric_catalog.{jsonl,csv,json,md}` — one row per series, each carrying the `http_path` you fetch values from. *Not* time-series data; it is the structural inventory.

### `tools/build_registry_seed.py`
Folds metric-cards + charts into proper **nodes** keyed by `(scope, metric_base, aggregation)`.
Charts attach as roles (card endpoint, series endpoint, breakdown dimension). Seeds fields derivable
without client schema (grain, product, department, occurrence counts).
- **Reads:** `data/metric_catalog.jsonl`. **Writes:** `data/metric_registry.<tenant>.csv` (the central node table).

### `tools/validate_registry.py`
Eight hard sanity checks on the registry: catalog→nodes conservation, `node_id` uniqueness,
single-scope per node, required-field completeness, occurrence integrity, derived flags, scope
validity, tenant binding. Prints pass/fail; no output files. The pipeline's correctness gate.

### `tools/build_admissibility.py`
Bridges Phase 0 → Phase 1: turns the typed registry into **type-legal candidate edges**, enforcing
admissibility rules (e.g. `external → controllable/mediator/outcome/constraint`) and excluding
self-loops + algebraic identities (a metric's own formula components). Prunes the N·(N−1) pair space
by ~69% to a tractable set.
- **Reads:** typed registry. **Writes:** `data/candidate_edges.<tenant>.csv` (`src, dst, src_type, dst_type`).

### `tools/merge_registry.py`
**Upsert** across rebuilds: carries forward stateful columns (human edits like steward/aliases/
version; quality stats like null_rate/completeness/sla) so a rebuild never wipes curated data.
Logs deletions to an append-only audit trail.
- **Reads:** fresh registry + `.registry_prev.<tenant>.csv`. **Writes:** merged registry + `data/removed_nodes.<tenant>.jsonl`.

---

## 4. Phase 0 — Enrichment (layer metadata onto each node)

All enrichment steps **degrade gracefully**: if the warehouse / dbt manifest / live API is
unreachable, they fall back to schema-free facts and log the downgrade rather than failing.

### `tools/enrich_attributes.py`
Adds governance/semantic/quality/causal/lifecycle columns deterministically: `roles, data_owner,
sensitivity, compliance_tags, unit, polarity, description, aliases, is_kpi, priority_tier, status`,
etc. Idempotent (no per-run timestamps). **Reads/Writes:** the registry CSV (augmented).

### `tools/enrich_lineage.py`
Traces dbt lineage: `node.dashboard → repositories/<slug>.py MART_NAME → dbt mart → source()`. Fills
`source, source_set, source_confidence, mart_model, mart_source, source_method` (dbt-traced vs
heuristic fallback). **Reads:** BC_ANALYTICS `dbt/models` + `backend/app/repositories` + registry.

### `tools/enrich_schema.py`
The **verification plane**: reads dbt mart SQL to set exact `grain` from the mart name/date columns,
validates that `metric_base` actually appears as a mart column, and downgrades `keep` for
chart-view artifacts with no backing column. Sets `schema_verified`, upgrades `grain_source` to `dbt`.

### `tools/enrich_handler_lineage.py`
Handler-SQL → **column-level** lineage. Parses repository `SELECT` lists (`<expr> as <ALIAS>`) to
extract `source_columns` and `source_expr` per kept node — enabling identity proofs (two metrics
built from the same columns+expr). **Reads:** BC_ANALYTICS repositories + registry.

### `tools/enrich_formula.py`
Recovers formulas from dbt mart SQL — finds "ratio/product of columns" definitions and marks
`is_derived`, `formula`, `formula_components`. These feed the **structural** edge provider (and are
*excluded* from causal candidate edges, since algebra ≠ causation).

### `tools/enrich_taxonomy.py`
Tags `domain` (MarketingIQ / CustomerIQ / ProductIQ) and `department` (Marketing / E-commerce /
Customer / Merchandising / Executive / Finance) by majority-voting across source dashboards via the
authoritative `sectionNav` config. Flags cross-product / cross-functional metrics.

### `tools/enrich_registry.py`
*Optional* live-API probe enrichment (all-or-nothing; if login fails nothing breaks). Hits endpoints
to upgrade `chart_type`, data columns, `grain` (inferred from date spacing), and an empirical
`is_derived` check. Sets `probe_status`.

### `tools/classify_types.py`
Context-aware causal-role classification (not name-only): observable nodes →
`outcome / mediator / controllable / external / constraint` via scope+source+agg+is_derived+lineage
heuristics; ML nodes → `outcome / mediator` by what the model outputs. Sets `type`, `type_confidence`.

### `tools/finalize.py`
The **autonomous decision** step. Quarantines chart-view artifacts (no card, no mart column,
structural base) as `keep=no`. Routes uncertain nodes (missing/low-confidence type, unverified
column, heuristic source, derived without formula) to a **non-blocking** review queue — the pipeline
never stops to ask a human.
- **Writes:** finalized registry + `data/review_queue.<tenant>.csv`.

---

## 5. Phase 1 — The discovery core (the genuinely hard part)

### `tools/discover_engine.py`
The **temporal causal-discovery engine**. Two modes: `synthetic` (self-test on planted structure)
and `api`/`pcmci`/`scan` (real series). Per-edge pipeline:
1. **Deseasonalize** each series with STL (remove weekly/seasonal trend so co-movement ≠ shared
   seasonality).
2. **Stationarity** check (ADF).
3. **Pairwise tests** — cross-correlation (best lag), Granger causality, mutual information.
4. **Multiple-testing control** — Benjamini–Hochberg **FDR** across the candidate batch.
5. **Stability selection** — keep only edges surviving a majority of sub-windows (kills flukes).
6. **Conditioning** — PCMCI+ partial-correlation conditioning to remove spurious common-cause links.
- **Reads:** `candidate_edges.<tenant>.csv` + series via the API. **Writes:** `data/discovered_edges.<tenant>.csv` (columns: `src, dst, lag, corr, granger_p, mi, discovery_score, stability, cond_corr, method, fdr_pass`). Variants for method comparison: `discovered_edges_parcorr*.csv`, `discovered_edges_cmiknn-gpu_tau3.csv`.

### `tools/cmi_gpu.py` — *why CMIknn?*
A **GPU-accelerated k-NN estimator of Conditional Mutual Information** (CMIknn), packaged as a drop-in
`tigramite` `CondIndTest` subclass, validated against the CPU implementation.

**Why it exists:** PCMCI+ needs a *conditional-independence test*. The default `ParCorr` (partial
correlation) only detects **linear** dependence. Many real metric relationships are **nonlinear** —
e.g. ad-spend → ROAS has diminishing returns. CMIknn measures *conditional mutual information*, which
captures **arbitrary nonlinear** dependence, and via conditioning it distinguishes a **direct** link
from one explained away by a common driver. The trade-off is cost: CMIknn runs a permutation null
test, which is expensive — hence the **GPU batched** variant here, which makes the null tractable at
scale (`--max-nodes`, batched permutations). The repo's `docs/diff.html` visualizes exactly what this
buys: edges found by **both** ParCorr and CMIknn, **linear-only**, and **nonlinear-only**.

### `tools/compare_discovery.py`
Diffs two discovery runs (e.g. linear ParCorr vs nonlinear CMIknn-GPU): agreement, unique links,
lag/MCI deltas. Drives `docs/diff.html`. Read-only analysis (stdout + HTML input).

---

## 6. Phase 1 — The six edge providers

Edges are a **union over typed providers**, each with a `kind`, `relation`, `confidence`, and
`provenance`. A node connects through whichever providers fit its shape — no single provider is
required. Each writes its own `data/<kind>_edges.<tenant>.csv`.

| Provider | Builder | Edge meaning | Certainty | Output |
|---|---|---|---|---|
| **Structural** | `build_structural_edges.py` | `component → metric` (definitional / identity) | 1.0 (exact) | `structural_edges.<t>.csv` |
| **Temporal** | `discover_engine.py` | `X → Y (lag)` (statistical) | statistical | `discovered_edges.<t>.csv` |
| **Model** | `build_model_edges.py` | `feature → model → target` | model-derived | `model_edges.<t>.csv` |
| **Compositional** | `build_compositional_edges.py` | funnel `stage → stage` | ~0.85 | `compositional_edges.<t>.csv` |
| **Cross-product** | `build_crossproduct_edges.py` | channel → blended aggregate | ~0.9 | `crossproduct_edges.<t>.csv` |
| **Alias** | `build_aliases.py` | `same_as` (proven) | 1.0 | `alias_edges.<t>.csv` |

### `tools/build_structural_edges.py`
Definitional edges, resolved in priority order: `formula_components` from dbt SQL (authoritative),
then handler source-column mapping, then a canonical-identity table (e.g. `cpc = spend / clicks`).
Resolves only to nodes in the **same scope**; unresolvable tokens are logged (raw columns, not
metrics). `relation ∈ {definitional, identity}`, `confidence = 1.0`, `evidence` = the SQL expression.

### `tools/build_model_edges.py`
Wires ML nodes, degrading across three tiers: **Tier 0** `model → target` (always available, from a
`MODEL_TARGETS` map — even a black-box DL model is connected); **Tier 1** `feature → model` weighted
by a feature-importance endpoint when present; **Tier 2** reserved for a feature manifest. Key rule:
a node's membership never depends on its explainability output. `--base` enables the API probe.

### `tools/build_compositional_edges.py`
Canonical funnel/compositional steps from domain knowledge (ads:
reach→impressions→clicks→conversions→revenue; email: sent→open→click; etc.), emitted **only where
both stages exist as nodes** in the same scope. `relation = funnel_step`, `confidence ≈ 0.85`.

### `tools/build_crossproduct_edges.py`
Cross-channel **additive** aggregation only (sum, never ratio): per-channel/per-product metrics →
blended aggregate (`google.revenue + meta.revenue → blended.revenue`). `relation = contributes_to`,
`confidence ≈ 0.9`.

### `tools/build_aliases.py`
Provable node aliasing (`same_as`). Deterministic proofs first (shared card/series endpoint, raw
ids, or matching `source_expr + mart`); for unproven candidates, full-series equality (≥30 aligned
points within 0.5% tolerance). Series that **differ** are dropped (proven *not* aliases).
- **Writes:** `alias_edges.<t>.csv` (proven, conf 1.0) + `alias_candidates.<t>.csv` (unprovable, for review).

### `tools/build_node_state.py`
Current node **values** (the breach signal — the highest-leverage data for the product loop). Per
node: a metric-card scalar (`value, previous, change_pct`) or a series tail. Needs the live API.
- **Writes:** `data/node_state.<tenant>.csv` (`node_id, value, previous, change_pct, unit, as_of, source`).

---

## 7. Phase 2 — Assembly & export

### `tools/enforce_dag.py`
Builds the **maximum-trust acyclic** view: edges ranked by trust
(`structural > crossproduct/compositional > model > temporal`) and added in order; any edge that would
close a cycle is dropped (the weaker back-edge first).
- **Reads:** all `*_edges` files. **Writes:** `data/dag_dropped.<tenant>.csv` (the feedback edges removed for traversal — recorded, not deleted).

### `tools/export_graph.py`
The **canonical export** — the single authoritative artifact every downstream consumer reads. Unions
all six edge layers (applying the FDR filter to temporal and the DAG drop-set), attaches node `type`,
lineage, current `state`, and optional `policy`.
- **Reads:** registry, all edge files, `node_state`, `dag_dropped`, `policy_store`. **Writes:** `data/graph.<tenant>.json` (keys: `tenant, schema_version, id_scheme, view_acyclic, feedback_edges_dropped, nodes, edges, stats`).
- **Note:** for temporal edges it currently *flattens* the rich stats (`granger_p, stability, cond_corr, mi`) into one human-readable `evidence` string. The structured columns live in `discovered_edges.<t>.csv` — relevant for anything that wants the raw statistics (see the integration doc).

---

## 8. UI & observability

### `tools/build_graph_ui.py`
The **node-centric** causal-graph explorer. Never renders the whole graph; you search/click a node and
see its neighbourhood — **drivers on the left, effects on the right** — with edges colored by provider
and weighted by confidence. The side panel shows state, lineage (source SQL), and policy.
- **Reads:** `graph.<t>.json`. **Writes:** `docs/graph.<tenant>.html` (self-contained, works over `file://`).

### `tools/build_pipeline_ui.py`
**Step-by-step observability**: one self-contained HTML where each pipeline step is a card with a
status badge, headline numbers, and a *real sample* of that step's output — so you can confirm each
stage actually worked.
- **Writes:** `docs/pipeline.<tenant>.html`.

---

## 9. Policies, breach tracing & audit

### `tools/policy_store.py`
A separate persistent policy store (`data/policies.<tenant>.csv`), *not* node columns. Policies match
nodes by `match_node_id / match_metric_base / match_scope` (blank = wildcard); most-specific wins,
client > derived. CLI prints a coverage report; library exposes `load_policies(tenant)` and
`resolve(node_row, policies)`.

### `tools/trace_breach.py`
The **breach tracer** — the core Decision-Canvas traversal and the product's reason for existing.
Given a node (real state or `--value` simulated), it walks the union of all edge layers to assemble a
**Thought skeleton**: **WHAT** (breach, value vs policy, gap) → **WHY** (upstream drivers, multi-hop)
→ **IMPACT** (downstream effects, financial gap). Read-only; output to stdout.
- **Run:** `python tools/trace_breach.py --tenant rare_seeds --node blended.revenue --value 1000 --depth 2`

### `tools/audit_merge_risks.py`
Merge-risk audit sourced from the OpenAPI spec (authoritative paths, not the collapsed catalog).
Reports bare metric names served by multiple dashboards/scopes (dangerous to merge into one node).
Read-only.

---

## 10. Data artifacts (`data/`)

| Artifact | Produced by | What it is |
|---|---|---|
| `metric_catalog.{jsonl,csv,json,md}` | build_metric_catalog | Inventory of every served series (with `http_path`) |
| `metric_registry.<t>.csv` | build_registry_seed → merge | **The node table** (~60 columns) |
| `candidate_edges.<t>.csv` | build_admissibility | Type-legal pairs to test |
| `discovered_edges.<t>.csv` (+ parcorr/cmiknn variants) | discover_engine | Temporal edges + raw statistics |
| `structural_/model_/compositional_/crossproduct_/alias_edges.<t>.csv` | the 5 deterministic providers | Per-provider edge layers |
| `node_state.<t>.csv` | build_node_state | Current value per node |
| `dag_dropped.<t>.csv` | enforce_dag | Feedback edges removed for the acyclic view |
| `policies.<t>.csv` | policy_store | Threshold/breach rules |
| `review_queue.<t>.csv` | finalize | Non-blocking uncertain nodes |
| `removed_nodes.<t>.jsonl` | merge_registry | Append-only deletion audit |
| `graph.<t>.json` | **export_graph** | **The canonical graph** |
| `run_report.<t>.json` | run_pipeline | Per-run registry/discovery/graph stats |

### `graph.<tenant>.json` shape
```jsonc
{
  "tenant": "rare_seeds", "schema_version": 3,
  "id_scheme": "scope.metric[.agg] (tenant-free; the per-tenant file is the boundary)",
  "view_acyclic": true, "feedback_edges_dropped": 8,
  "nodes": [ { "id": "blended.revenue", "scope": "blended", "product": "Marketing IQ",
               "type": "...", "metric_base": "revenue", "aggregation": "...",
               "unit": "...", "grain": "daily", "is_derived": false,
               "mart_source": "...", "formula": "...",
               "state": { "value": "43.8", "change_pct": "0.0", "as_of": "2026-06-13" } } ],
  "edges": [ { "src": "blended.revenue", "dst": "blended.aov",
               "kind": "structural", "relation": "definitional", "confidence": 1.0,
               "lag": null, "evidence": "CASE WHEN SUM(...) ... END",
               "provenance": "structural_edges.csv" } ],
  "stats": { "nodes": 395, "edges": 219,
             "edge_kinds": {"structural":88,"temporal":50,"model":31,
                            "compositional":25,"crossproduct":23,"alias":2},
             "with_state": 153 }
}
```

**Current snapshot (tenant `rare_seeds`):** 464 nodes / 395 kept; 54 with a live endpoint (warehouse
reads sandboxed off); graph = **395 nodes, 219 edges**, acyclic, 8 feedback edges dropped; PCMCI+
discovery tested 61 / kept 61.

---

## 11. Identity & tenancy model

- **Node id:** `scope.metric[.agg]` — e.g. `blended.revenue`, `meta.impressions`, `blended.reach.sum`.
- **Tenant-free ids:** the per-tenant *file* (`metric_registry.rare_seeds.csv`,
  `graph.rare_seeds.json`) is the isolation boundary. A graph can never be served for the wrong
  tenant because each tenant has its own files.
- **Architecture invariant:** time-series values are ingested **only** from API endpoints; dbt marts
  are used **only** to verify/enrich node metadata at build time — never to serve values.

---

## 12. How to run it

```bash
# Full pipeline (needs a BC_ANALYTICS checkout + a reachable API for discovery/state)
python tools/run_pipeline.py --repo ../BC_2 --tenant rare_seeds

# Rebuild the graph from EXISTING data/ (zero deps, no API): the deterministic providers + assembly
python tools/build_structural_edges.py --tenant rare_seeds
python tools/build_compositional_edges.py --tenant rare_seeds
python tools/build_crossproduct_edges.py --tenant rare_seeds
python tools/enforce_dag.py --tenant rare_seeds
python tools/export_graph.py --tenant rare_seeds
python tools/build_graph_ui.py --tenant rare_seeds      # docs/graph.rare_seeds.html
python tools/build_pipeline_ui.py --tenant rare_seeds   # docs/pipeline.rare_seeds.html

# Trace a (simulated) breach end-to-end
python tools/trace_breach.py --tenant rare_seeds --node blended.revenue --value 1000 --depth 2
```

The discovery core additionally needs: `numpy`, `scipy`, `statsmodels` (and `tigramite` + `torch`
for GPU CMIknn), plus a live BC_ANALYTICS series API.

---

## 13. Deviation note (read this before integrating with dc-kg)

`docs/07-KG-ARCHITECTURE.md` states plainly: *"This is the KG we intended, not the
time-series-discovery tool we drifted into."* The intended product (breach → node lookup →
drivers/effects → Thought) needs three things per node — **state, typed edges, metadata** — and
"barely uses time series at all." This repo **over-invested in temporal edges** and a flat-file model,
and under-built state ingestion and any governance/review layer.

That deviation is **not waste**: it produced a rigorous statistical discovery engine
(`discover_engine.py` + `cmi_gpu.py`) — FDR-controlled, deseasonalized, stability-selected, PCMCI+-
conditioned, nonlinear-aware — which is precisely the capability the production graph (`dc-kg`) is
missing. **How that capability should feed `dc-kg` is the subject of
`INTEGRATION-ANALYSIS-claude.md`.**
