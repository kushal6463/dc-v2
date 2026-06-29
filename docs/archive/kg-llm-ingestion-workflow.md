# How the LLM Builds the Knowledge Graph — Ingestion Workflow

> A step-by-step walkthrough of the ThoughtWire Causal Knowledge Graph (`dc-kg`)
> ingestion pipeline: what each stage does, **where the LLM is involved**, and the
> exact commands to run it.

---

## The one thing to understand first

**The LLM never writes to Neo4j.** It only *proposes* nodes and edges as JSON.

Every write to the graph goes through a single deterministic component (the
**arbitration writer**) *after* the proposal has been reviewed. This is the core
safety design:

- Proposer agents run **read-only** and **in parallel** (one per dashboard).
- Exactly **one sequential writer** mutates Neo4j.
- Combined with `canonical_id` uniqueness constraints, **duplicate nodes and write
  races are structurally impossible** — no locking required.

The LLM's job is purely **classification, formula reading, link resolution, and
causal judgment** — never the write itself.

---

## Architecture at a glance

```
 PURE CODE              LLM  (read-only, proposes JSON)         PURE CODE (writes DB)
 ─────────              ───────────────────────────────         ─────────────────────
 pre-pass    ──drafts──▶  proposer agents   ──proposals──▶  review ──▶ arbitration ──▶ Neo4j
 (OpenAPI ⨝               (per dashboard,                    (accept/    writer        │
  registry)                own context)                       edit/      (single,      │
                                                               reject)    idempotent    │
                                                                          MERGE)        │
                          causal judge + refuter  ◀────── reconcile ◀───────────────────┘
                           (per metric-pair)
```

**Why the context window stays flat:** structural fields are harvested in pure code
(zero tokens); work is partitioned per dashboard (~7 metrics each); each proposer is
a sub-agent in its own context returning ~1–2k tokens; output is schema-constrained;
and the agent only sees a tiny "RAG shortlist" of spine IDs, never the whole graph.

---

## The three milestones

| Milestone | What it builds | LLM involved? |
|---|---|---|
| **M0 — Spine bootstrap** | `Business` root + `Domain` + `IntelligenceProduct` nodes | No (seeded from JSON) |
| **M2 — Metric ingestion** | `Metric` nodes + surface/spine edges, per dashboard | **Yes — proposer agents** |
| **M3 — Causal layer** | `DECOMPOSES_INTO` / `ROLLS_UP_TO` / `CORRELATES_WITH` / `INFLUENCES` edges | **Yes — judge + refuter (for `INFLUENCES` only)** |

---

## Step 0 — One-time setup (no LLM)

```bash
uv sync --extra dev            # create .venv, install deps (incl. the Neo4j Rust ext)
uv run kg schema-init          # apply Neo4j constraints + indexes (canonical_id UNIQUE, ...)
uv run kg bootstrap-spine      # write Business + Domain + IntelligenceProduct spine
uv run kg status               # node/edge counts per label
```

The spine is seeded deterministically from `harness/seed/spine_seed.json` — **not**
LLM-generated. It matters because it defines the set of IDs the LLM is later **only
allowed to link to** (the agent can never invent a domain or product).

**Claude Code slash-command equivalents** (confirm-before-create):

```
/create-business-node
/create-domain-node marketing
/create-product-node miq
/kg-status
```

---

## Step 1 — Deterministic pre-pass (no LLM)

**File:** `harness/ingest/prepass.py`

The biggest design decision: structural fields are extracted in plain code so the
LLM context stays tiny. It loads two ground-truth files:

- `docs/frd-docs/openapi.json` — ~902 API paths (gives endpoints)
- `docs/frd-docs/chart-registry.json` — 646 chart entries (gives `formula`,
  `formula_explanation`, `how_to_read`, `decisions_answered`)

It **joins them deterministically** (`GET /{dashboard}/metrics/{id}` ⟷ registry key
`{dashboard}:{id}`), **drops excluded endpoints** (`master-config`, `/auth`,
`POST/DELETE`, health/docs, …), and emits one **draft dict** per metric/dashboard.
Zero tokens spent.

```bash
uv run kg prepass            # prints draft counts (~600 metric + dashboard drafts)
uv run kg prepass --json     # dumps the full draft payload
```

---

## Step 2 — Proposer agents: the LLM generates nodes + edges

This is the **first** place the LLM runs. **One sub-agent per dashboard**, fanned out
concurrently.

**Orchestration** — `harness/ingest/orchestrator.py`
- `ingest_dashboards()` fans out across ~90 dashboards with an `asyncio.Semaphore`
  (default concurrency **6**).
- Before spawning, `get_spine_context()` reads the **linkable spine** from Neo4j —
  *only IDs + names*, never full nodes. This is the "RAG shortlist" the agent links
  against, and the only Neo4j read in the proposer pipeline (strictly read-only).

**Per dashboard** — `harness/ingest/proposer.py`
1. Get the deterministic drafts for that dashboard (`prepass_for`).
2. Chunk metrics into batches of **6** (`CHUNK_SIZE`) — big dashboards in one call
   deadlock the SDK.
3. For each chunk, call the LLM via `engine.propose_structured(...)`.

**The actual LLM call** — `harness/agent/engine.py`
- Uses `claude-agent-sdk` with your **Claude Code subscription OAuth** (no API key).
- Runs with `setting_sources=[]` so the proposer does **not** load this project's
  hooks / MCP / skills.
- Forces **schema-constrained output** via
  `output_format={"type": "json_schema", "schema": ...}` so the model returns exactly
  `{"proposals": [...]}`. Falls back to parsing a fenced ` ```json ` block if needed.
- Retries transient failures on a `[5, 15, 45]`-second backoff; turn-limit / auth
  errors fail fast (no retry).

**What the LLM is told to do** — the `PROPOSER_SYSTEM` prompt in
`harness/agent/prompts.py`. The genuinely non-deterministic work:

1. **Registry is truth** — never invent IDs, endpoints, or values.
2. **Reconcile** — group metrics describing the same concept under a shared
   `concept_key` / `metric_base`.
3. **Classify** each metric using **only allowed enum values** (`causal_role`,
   `unit_family`, `category`, `chart_type`, …).
4. **Read each `formula_text`** and propose `DECOMPOSES_INTO` edges to referenced
   metrics (confidence 1.0).
5. **Resolve `domain_ids` / `product_ids`** only from the provided spine IDs.
6. **Pick a `chart_type`** and emit a `VISUALIZES` edge from the pre-seeded
   `uic:<chart_type>` node.
7. **Emit edges:** `VISUALIZES`, `SHOWN_ON`, `BELONGS_TO_DOMAIN`,
   `PART_OF_PRODUCT`, `DECOMPOSES_INTO`.

The output schema restricts the agent to only `Dashboard` or `Metric` target labels.
Then `_normalize_proposal` **merges the LLM's enrichment onto the deterministic
draft** (`{**draft, **agent_non_null}`) — so the LLM enriches/classifies, but
required base fields always come from code. This is the **"deterministic draft →
agent reconciles"** contract.

**Commands:**

```bash
uv run kg ingest-dashboard ceo-pulse                  # one dashboard
uv run kg ingest-dashboard ceo-pulse --auto-approve   # propose + apply immediately
uv run kg ingest-all                                  # fan out all ~90 dashboards
uv run kg ingest-all --limit 5 --concurrency 6        # first 5, 6 at a time
```

Slash-command equivalents: `/ingest-dashboard <id>`, `/ingest-all` (these stream
live onto the canvas via SSE).

**Result:** proposals written to `data/proposals/<dashboard>/` with
`review_state:"proposed"` and a checkpoint event logged. **Nothing in Neo4j yet.**

---

## Step 3 — Review the proposals (human / canvas)

```bash
uv run kg proposals list --run <run_id>
uv run kg proposals approve <proposal_id>           # or --all
uv run kg proposals reject <proposal_id> --reason "..."
```

A reject re-spawns that dashboard's proposer with the reason injected as steering
(Reflexion-style verbal feedback), bounded retry.

---

## Step 4 — Arbitration writer: the only thing that writes Neo4j

```bash
uv run kg apply --run <run_id>
```

**File:** `harness/kg/arbitration.py`. Sequentially consumes approved proposals and
does idempotent `MERGE (m:Metric {canonical_id:$id}) ON CREATE SET … ON MATCH SET …`.
Running it twice gives identical counts (true upsert). Every write is appended to
`data/events/events.jsonl` (the replay log).

---

## Step 5 — Cross-partition reconcile (no LLM)

```bash
uv run kg reconcile               # collapse a metric that appeared on N dashboards into one node
uv run kg reconcile --dry-run
uv run kg prune-empty             # drop domains / chart-types no metric uses
```

**File:** `harness/kg/reconcile.py`. Groups by canonical key and merges, preserving
every dashboard's relationships.

---

## Step 6 — Causal pass: the LLM proposes causal edges

**Files:** `harness/ingest/causal.py` + `harness/agent/prompts.py`. Mostly
deterministic, with a carefully guarded LLM layer.

```bash
uv run kg run-causal                        # deterministic only
uv run kg run-causal --llm                  # + LLM-proposed INFLUENCES (review-only)
uv run kg run-causal --llm-links --platform meta_ads --limit 50
uv run kg run-causal --auto-approve         # applies ONLY the deterministic edges
```

**Stages:**

1. **`DECOMPOSES_INTO`** — deterministic, parsed from `formula_text`, confidence 1.0.
2. **`ROLLS_UP_TO`** — structural scope rollup, confidence 1.0.
3. **`CORRELATES_WITH`** — imported from `harness/seed/rare_seeds_correlations.json`
   (**never** auto-promoted to `CAUSES`).
4. **`INFLUENCES`** (LLM, `--llm`) — the careful part:
   - A **statistical / structural gate runs first** — no signal, no LLM call.
   - A **pointwise judge** sees one isolated metric pair and must return
     `{relationship, mechanism_text, abstain}`; an **empty mechanism rejects the
     candidate**.
   - A **refuter** agent then tries to disprove it, defaulting to "refuted" when
     uncertain.
   - **Self-consistency** across N samples (`--samples`); confidence = the
     *agreement fraction*, **not** the model's stated number (verbalized confidence
     is mis-calibrated, ~39% ECE).
   - Folded into a `Beta(α, β)` posterior; everything lands as
     `review_state:"proposed"`. **`INFLUENCES` are never auto-committed** — even
     `--auto-approve` only applies the deterministic edges.

Slash-command equivalent: `/run-causal [--llm] [--llm-links] [--platform meta_ads]`.

---

## The whole thing in one paragraph

Code joins OpenAPI + the chart registry into per-dashboard drafts (no LLM). For each
dashboard, a Claude sub-agent (subscription OAuth, schema-constrained JSON,
read-only) **classifies each metric, picks its chart type, reads its formula, and
resolves links to existing spine IDs** — returning proposals, not writes. Those are
reviewed, then a single deterministic writer `MERGE`s them into Neo4j. Finally a
causal pass adds deterministic formula / rollup / correlation edges plus
**LLM-judged-and-refuted `INFLUENCES`** that are gated, mechanism-required,
self-consistency-scored, and always held for human review.

---

## End-to-end run

```bash
uv run kg schema-init && uv run kg bootstrap-spine
uv run kg ingest-all --auto-approve
uv run kg reconcile && uv run kg prune-empty
uv run kg run-causal --llm
uv run kg status
```

---

## Command reference

| Command | Stage | LLM? | What it does |
|---|---|:--:|---|
| `uv run kg schema-init` | Setup | — | Apply Neo4j constraints + indexes |
| `uv run kg bootstrap-spine` | Setup | — | Seed Business / Domain / Product spine |
| `uv run kg prepass` | 1 | — | Deterministic OpenAPI ⨝ registry → drafts |
| `uv run kg ingest-dashboard <id>` | 2 | ✅ | Proposer agent for one dashboard |
| `uv run kg ingest-all` | 2 | ✅ | Fan out proposer agents over all dashboards |
| `uv run kg proposals list/approve/reject` | 3 | — | Review the proposal queue |
| `uv run kg apply` | 4 | — | Arbitration writer MERGEs approved proposals |
| `uv run kg reconcile` | 5 | — | Collapse duplicate cross-dashboard metrics |
| `uv run kg prune-empty` | 5 | — | Drop unused domains / chart-types |
| `uv run kg run-causal [--llm]` | 6 | ✅* | Build causal edges (LLM only for `INFLUENCES`) |
| `uv run kg status` | any | — | Node/edge counts per label |

\* `run-causal` is deterministic for `DECOMPOSES_INTO` / `ROLLS_UP_TO` /
`CORRELATES_WITH`; the LLM is used **only** for review-only `INFLUENCES` candidates.

---

## Key design guarantees

- **No hallucinated nodes** — the agent can only link to spine IDs it was given; the
  output schema and `_normalize_proposal` reject anything off-vocabulary.
- **No write races / duplicates** — a single sequential writer + `canonical_id`
  uniqueness constraint makes them structurally impossible.
- **No auto-committed causality** — correlations never auto-promote to `CAUSES`;
  every `INFLUENCES` edge carries `confidence · evidence_mass · mechanism ·
  review_state` and is held for human review.
- **Calibrated confidence** — causal confidence comes from self-consistency agreement,
  not the model's stated number.
- **Flat context window** — pre-pass + per-dashboard partitioning + schema-constrained
  output + RAG shortlist keep each agent call small and cheap.
