# ThoughtWire Causal Knowledge Graph — Implementation Plan (V1)

> **Scope of this plan:** build the **AI-first ingestion engine + Neo4j causal knowledge graph + live canvas** described in `docs/final-schema-claude.md`. The `docs/frd-docs/thoughtwire-frd.md` is the *end-goal product*; V1 here is **only the causal graph** (the "business body" an agent wakes into). Everything is grounded in the four reference sources analyzed below (`../ContextLayer`, `../decision-canvas-os`, `../BC_2`, and the local `docs/`).

---

> **How to use this doc:** this is the concrete, approved implementation plan. Drive the build phase-by-phase with **`ultracode`** (see §15). No Docker; dev uses your **local Homebrew Neo4j**. All packages pinned to **latest stable** (verify-before-pin).

## 1. Context — why we're building this

The previous KG (`../decision-canvas-os`) stored a small causal graph in **NetworkX + a 50 MB `graph_cache.json`**, sourced from Snowflake `CONFIG_ONTOLOGY_*` tables, with no self-evolution, no provenance, no RBAC, and no live ingestion UX. We are **redesigning from the ground up** onto the authoritative V1 schema (`docs/final-schema-claude.md`): **Neo4j**, a single `Business` root with a **tri-axis spine** (`Domain ∥ IntelligenceProduct ∥ Platform`), a `Metric` hub, and an **evidence-backed causal layer** — built **AI-first** by an agentic ingestion engine, with a **real-time canvas** for watching and steering ingestion, and **everything reviewed before it is written**.

Two sources of truth feed the metric layer and must be **combined**:
- `docs/frd-docs/openapi.json` — **902 paths / 877 GET / 463 schemas**. Gives endpoints, params, response schemas. Fields are often sparse.
- `docs/frd-docs/chart-registry.json` — **646 entries** keyed `{dashboard_id}:{chart_id}`. Gives **`formula`, `formula_explanation`, `how_to_read`, `decisions_answered`** (10 fields at 100%, `narration_text` at 558/646).
- **The join is deterministic:** `GET /{dashboard}/metrics/{id}` ⟷ registry key `{dashboard}:{id}`. (RAG is *not* needed for this join — only for fuzzy `concept_key` grouping and causal candidates.)

**Excluded entirely** (never a node/edge/identity/governance source): `master-config/**`, `/auth/**`, non-dashboard `/admin/**`, `/settings/**`, `/health·/docs·/redoc`, and all `POST/PUT/PATCH/DELETE` (46 ops).

### Locked decisions (from the interview)
| Area | Decision |
|---|---|
| Backend | **Python** (FastAPI + asyncio) |
| Frontend | **Vite + React + shadcn/ui + React Flow (`@xyflow/react`) + Zustand**, scaffolded with **`bunx --bun shadcn@latest init --preset b395TAHL8 --template vite`** (bun runtime), **latest** React/Vite |
| Agent auth | **Local Claude subscription OAuth** (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; unset `ANTHROPIC_API_KEY`) |
| Graph DB | **Neo4j — local Homebrew install** (`brew services start neo4j`), **NOT Docker yet**; one DB *is* the tenant boundary (Community single-DB; see §Challenges) |
| Ingest trigger | **Claude Code slash commands** (`/ingest-dashboard`, `/ingest-all`) drive the engine; graph builds **live on the canvas** via SSE. The `kg` Python CLI is the same entrypoint underneath. |
| Milestone 1 | **Spine bootstrap via slash commands + MCP + Neo4j** (Business/Domain/Product), confirm-before-create |
| Ingestion model | **Full-agentic with guardrails** — orchestrator → parallel proposer agents → proposal queue → single arbitration writer → human review |
| Partition key | **Per-dashboard (~90 partitions)** |
| Agent input | **Deterministic draft → agent reconciles & enriches** |
| Causal V1 | **Formula edges (deterministic) + rare_seeds correlations + LLM-proposed influences (review-only, never auto-committed)** |
| Policy/Threshold | **Defined shells only, `population_status:'defined'`** — no instances (separate service later) |
| Packages | **Latest stable** for everything (verify-before-pin) |
| KG framework | **Borrow Graphiti patterns; do not adopt it wholesale** |

---

## 2. Architecture at a glance

**The whole engine is the schema's `proposals → arbitration → review` path, executed by parallel agents:**

```
                         ┌─────────────── ORCHESTRATOR (own context) ───────────────┐
 deterministic pre-pass  │  partitions = 1 dashboard each (~90)                       │
 (OpenAPI⨝registry →     │  fan out, concurrency cap (Semaphore ~5–8)                 │
  draft proposals)  ────▶│   ├─▶ PROPOSER agent (dash A)  ─┐  each: OWN context,      │
                         │   ├─▶ PROPOSER agent (dash B)   │  READ-ONLY on DB + RAG    │
                         │   └─▶ PROPOSER agent (dash N)  ─┘  shortlist; returns        │
                         └────────────────────────────────────~1–2k-token proposals ──┘
                                          ▼
                          PROPOSAL QUEUE  (events.jsonl  +  :Proposal{review_state:'proposed'} staging)
                                          ▼
                          CANVAS REVIEW   accept │ edit │ reject(+reason)        ◀── SSE live stream
                                          ▼            └─ reject → re-spawn proposer w/ reason (Reflexion), bounded retry
                          SINGLE ARBITRATION WRITER (sequential, idempotent MERGE on canonical_id)
                                          ▼
                                       Neo4j  (uniqueness constraints = no dup nodes, no write races)
                                          ▼
                          CROSS-PARTITION RECONCILE  (apoc.refactor.mergeNodes by canonical key → rollups)
                                          ▼
                          CAUSAL PASS  (formula edges det. + correlations + LLM-judged influences → review)
```

**Why this is conflict-free:** proposer agents **never write Neo4j**. Exactly one sequential writer mutates the DB after approval (Single-Writer Principle / LMAX). Combined with `canonical_id` uniqueness constraints, write races and duplicate nodes are *structurally impossible* — no locking required.

**Why the context window stays flat** (the worry): (1) structural fields harvested in **pure code** (OpenAPI/registry are typed trees → zero tokens); (2) **per-dashboard partitions** (~7 metrics each); (3) each proposer is a **sub-agent in its own window** returning a tiny summary; (4) **schema-constrained structured output** (one item, minimal prompt, grammar cached 24h); (5) **RAG shortlist** of candidate link targets (never the whole graph in context); (6) **checkpoint + native `resume`**; (7) **`clear_tool_uses` context-editing** as the safety net. ContextLayer used 6 of these 8 to keep a 95-min run stable; we add the deterministic pre-pass + structured output.

---

## 3. Repository structure (AI-first, modeled on `../ContextLayer`, Python backend)

A monorepo. Mirrors ContextLayer's `app/ + harness/ + data/ + skills/ + .claude/` shape (which the user liked), ported to Python.

```
dc-kg/
├── docs/                         # already present: final-schema-claude.md (THE spec), frd, openapi.json, chart-registry.json
├── harness/                      # Python backend — FastAPI + the ingestion engine
│   ├── kg/
│   │   ├── schema.py             # Neo4j constraints/indexes (§9 of schema doc) + label/edge enums (§7)
│   │   ├── driver.py             # neo4j driver, get_session(database=...) → per-tenant boundary (Enterprise-ready)
│   │   ├── arbitration.py        # SINGLE writer: consume queue → idempotent MERGE (canonical_id) → events.jsonl
│   │   ├── reconcile.py          # cross-partition apoc.refactor.mergeNodes for rollup metrics
│   │   └── models.py             # Pydantic node/edge models = the 10 labels + edge catalog (schema §3–6)
│   ├── ingest/
│   │   ├── prepass.py            # deterministic OpenAPI⨝registry → draft proposals (zero LLM); exclusion rules
│   │   ├── orchestrator.py       # fan out proposer sub-agents per dashboard, Semaphore cap, checkpoint/resume
│   │   ├── proposer.py           # one partition: Agent SDK loop, reconcile draft, formula→edges, classify, RAG-link → proposals
│   │   ├── causal.py             # formula edges (det) + correlations + pointwise LLM-judge + refuter + Beta(α,β)
│   │   ├── retrieval.py          # hybrid shortlist (BM25 + Neo4j vector index + RRF); slim briefs, fan-out cap
│   │   └── budget.py             # per-run read/work budget (re-reads free) — ContextLayer pattern
│   ├── mcp/
│   │   └── graph_server.py       # FastMCP stdio server: create_*_node, draw_edge, lookup_node, search_nodes
│   ├── agent/
│   │   ├── engine.py             # Claude Agent SDK wrapper (OAuth subscription auth, resume, retry/backoff)
│   │   └── prompts.py            # proposer / classifier / causal-judge / refuter system prompts
│   ├── api/
│   │   ├── server.py             # FastAPI: REST (graph reads, proposals, review actions) + SSE stream
│   │   ├── sse.py                # sse-starlette EventSourceResponse + per-run asyncio.Queue bus
│   │   └── events.py             # event types: ingest_progress, agent_action, proposal_new, node_written...
│   ├── cli/
│   │   └── kg.py                 # `kg` CLI: bootstrap-spine, ingest-dashboard, run-causal, status
│   └── store/
│       └── jsonl.py              # append-only events.jsonl + run manifests (atomic .tmp→rename)
├── app/                          # Vite + React frontend (the canvas) — scaffold via:
│   │                             #   bunx --bun shadcn@latest init --preset b395TAHL8 --template vite
│   └── src/
│       ├── components/           # CanvasView (@xyflow/react), ActivityFeed, ProgressBar, NodeDetailForm, ReviewQueue
│       │                         #   + shadcn/ui primitives from the preset
│       ├── api.ts                # REST + EventSource (SSE) client
│       ├── store.ts              # Zustand graph/state
│       └── graphLayout.ts        # dagre layout, provenance colors (deterministic=blue, agent=purple, human=green)
├── data/
│   ├── events/events.jsonl       # append-only audit/replay log
│   ├── proposals/                # queued proposal payloads (pre-write)
│   └── runs/                     # per-run manifests (resume)
├── skills/                       # reusable agent prompt-skills (proposer, causal-judge, org-graph)
├── .claude/
│   ├── settings.json             # HOOKS (PreToolUse on mcp__graph__* → confirm/deny), MCP registration
│   ├── commands/                 # spine: /create-business-node, /create-domain-node, /create-product-node
│   │                             # ingest: /ingest-dashboard <id>, /ingest-all, /run-causal, /kg-status
│   └── mcp.json (or settings)    # graph MCP server registration (stdio)
├── infra/
│   └── docker-compose.yml        # STUB ONLY (deferred) — dev uses local Homebrew Neo4j, not Docker
├── pyproject.toml                # uv-managed; latest stable pins
└── README.md
```

---

## 4. Data model — Neo4j (authoritative: `docs/final-schema-claude.md`)

Implement **exactly** the schema doc. Do **not** re-derive it. Key points the engine depends on:
- **10 labels:** `Business · Domain · IntelligenceProduct · Platform · Metric · Dashboard · UIComponent · Policy · Threshold · Role`. No `Tenant` node, **no `tenant_id`** (DB = tenant boundary).
- **Constraints/indexes:** copy `§9` verbatim (`metric_uid`, `canonical_id`-based uniqueness, `dashboard_id`, `component_id`, etc.). **Add `REQUIRE m.canonical_id IS UNIQUE`** so MERGE is a true upsert and dedup is automatic.
- **`Metric` hub** (`§3`): three IDs (`metric_uid`/`canonical_id`/`metric_id`); **arrays** `product_ids[]`/`domain_ids[]`/`platform_ids[]` (a canonical metric is never duplicated per axis); denormalized caches are rebuildable from edges (**edge wins on conflict**).
- **Edge catalog** (`§6`): spine (`HAS_DOMAIN/HAS_PRODUCT/USES_PLATFORM`, `BELONGS_TO_DOMAIN/PART_OF_PRODUCT/SOURCES`), formula (`DECOMPOSES_INTO` conf 1.0, `ROLLS_UP_TO`), causal (`CAUSES/INFLUENCES/CORRELATES_WITH` with `confidence·evidence_mass·lag·mechanism·review_state`), surface (`VISUALIZES`, `SHOWN_ON`), RBAC overlay. Every important edge carries provenance: `source_kind·source_ref·source_confidence·created_by·review_state`.
- **`UIComponent`** = the 646 registry entries; dashboard composition is the **`dashboard_id` FK**, not an edge.
- **`Platform`** is **lazily materialized** — seed `Metric.platform_ids[]` + `SOURCES` edge metadata first; create the node only when platform-level traversal is needed.
- **`Policy`/`Threshold`** = **defined shells, `population_status:'defined'`**, no instances in V1.
- **Type fixes** (`§7`): `yes/no`→bool; pipe-delimited→`string[]`; `causal_role_confidence` enum not number; nullable `formula_text/dimensions/n_periods`.

---

## 5. The ingestion engine (the heart)

### 5a. Deterministic pre-pass (`ingest/prepass.py`) — zero LLM tokens
Parse `openapi.json` (a typed tree) + `chart-registry.json`. Apply the **exclusion filter first** (drops `master-config/**`, POST/DELETE/health/auth, etc.). For each surviving `{dashboard}:{id}`, join the two sources into a **draft proposal**: copy the 100%-present registry fields (`formula`, `formula_explanation`, `how_to_read`, `decisions_answered`, `title`) + endpoint paths + response-schema-derived fields. Emit one draft per metric/component, grouped by dashboard.

### 5b. Orchestrator + parallel proposer agents (`ingest/orchestrator.py`, `proposer.py`)
- Partition by **dashboard** (~90). Fan out with `asyncio.Semaphore(~5–8)` (mind multi-agent ≈15× token cost).
- Each **proposer is a sub-agent in its own context window**, **read-only on Neo4j**, given: its dashboard's draft proposals + a **RAG shortlist** of existing spine nodes (domains/products/platforms/dashboards) to link against.
- The agent's job (the genuinely non-deterministic part): **reconcile** the sparse OpenAPI fields against the registry, **read each formula to draw `DECOMPOSES_INTO`/`ROLLS_UP_TO` edges**, **classify** (`causal_role`, `domain_ids[]`, `product_ids[]`, `concept_key`), and **resolve links** to existing node ids via the shortlist (`node_id` / `NEW` / `UNKNOWN`).
- Output is **schema-constrained structured output** (Agent SDK strict tool / JSON-schema) → validated proposal objects (`docs/final-schema-claude.md §8` payload shape). Returns ~1–2k tokens to the orchestrator.
- **Checkpoint per dashboard** + native session `resume`; a crashed run resumes on remaining partitions only.

### 5c. Single arbitration writer (`kg/arbitration.py`)
One sequential consumer of the approved queue. Idempotent `MERGE (m:Metric {canonical_id:$id}) ON CREATE SET … ON MATCH SET …` + edge MERGEs, inside a **managed transaction** (auto-retry transient). Appends every write to `events.jsonl`. **This is the only component that writes the graph.**

### 5d. Cross-partition reconcile (`kg/reconcile.py`)
After the parallel phase, collapse rollup metrics that appeared on several dashboards: group by canonical key, `apoc.refactor.mergeNodes(nodes,{properties:{'.*':'combine'},mergeRels:true})`. Preserves every dashboard's relationships. Provide a plain-Cypher fallback if APOC is unavailable.

### 5e. Causal pass (`ingest/causal.py`) — V1 scope
1. **Deterministic formula edges**: parse `formula_text` → `DECOMPOSES_INTO` (e.g. `roas = revenue/ad_spend`), confidence 1.0.
2. **Correlations**: import the 4 `rare_seeds` correlations as `CORRELATES_WITH` (never auto-promoted to `CAUSES`).
3. **LLM-proposed influences** (review-only): for candidate pairs **with a statistical/structural signal only** (gate first — no signal → don't ask the LLM), run a **pointwise judge** (one isolated edge), requiring `{relationship∈{causal,correlational,none}, mechanism_text, abstain}` (empty mechanism ⇒ reject); a **refuter** agent tries to disprove it; **self-consistency** (N≈5–10, confidence = agreement fraction, *not* the model's stated number). Accumulate into **`Beta(α,β)`** on the edge (`confidence=α/(α+β)`, expose `evidence_mass=α+β`). All land in the **review queue** as `review_state:'proposed'` — **never auto-committed**.

---

## 6. Context-window strategy (ranked tactics actually wired in)
1. **Deterministic pre-pass** — structural fields built in code (biggest win).
2. **Per-dashboard partitioning** — bounded slice per agent.
3. **Sub-agent context isolation** — each returns ~1–2k tokens.
4. **Schema-constrained structured output** — tiny per-item prompt, no parse/retry loop (grammar cached 24h).
5. **RAG shortlist** (hybrid BM25 + Neo4j vector index + RRF) — candidate ids only, never the full graph.
6. **Hard read/work budget** (re-reads free) + slim briefs + fan-out cap (ContextLayer `tools.ts`/`retrieval.ts` patterns).
7. **Checkpoint + native `resume`** — re-feed only remaining partitions.
8. **`clear_tool_uses_20250919` context-editing** + retry/backoff `[5s,15s,45s]` as the safety net.

---

## 7. MCP server, hooks, slash commands (`.claude/` + `harness/mcp/`)

**One FastMCP stdio server** (`mcp/graph_server.py`) reused by *both* the Claude Code CLI (`.claude` registration → tools surface as `mcp__graph__*`) and the Agent SDK harness. Tools: `create_business_node`, `create_domain_node`, `create_product_node`, `create_metric_node`, `draw_edge`, `lookup_node`, `search_nodes`. Writes go through the arbitration path, not direct.

**Hooks** (`.claude/settings.json`, `PreToolUse`, matcher `mcp__graph__.*`):
- **Confirm-before-create**: intercept `create_*` → render a **field table** of the proposed node + return `permissionDecision:'ask'` (the table-then-confirm UX the user described). Already-exists → return the existing node's table and `deny` the duplicate create.
- **Exclusion guard**: block any node derived from an excluded endpoint (`POST/DELETE/health/auth/master-config`) → `permissionDecision:'deny'` + reason.

**Slash commands** (`.claude/commands/*.md`, `allowed-tools: mcp__graph__*`) — the primary way you drive the system from the Claude Code CLI:
- **Spine (M1):** `/create-business-node`, `/create-domain-node`, `/create-product-node` — create if absent (after table confirm), report "already exists" + table if present.
- **Ingestion (M2/M3):** `/ingest-dashboard <id>` (one partition), `/ingest-all` (fan out all ~90), `/run-causal`, `/kg-status`. These kick off the orchestrator/engine; **the build streams live onto the canvas via SSE** (progress bar + endpoint activity feed + nodes appearing) so you watch and steer in real time. Each slash command is a thin wrapper over the same `kg` Python entrypoint, so CLI and programmatic runs share one code path.

---

## 8. Frontend canvas (`app/`, Vite + React)
- **`CanvasView`** (`@xyflow/react` + dagre): live graph; provenance-colored nodes (deterministic / agent-proposed / human-approved); proposed nodes shown as "pending" until approved.
- **`ProgressBar` + `ActivityFeed`**: SSE-driven — current dashboard, metrics done/total, live `agent_action` / `proposal_new` / `node_written` stream. (Layers 1–2 don't need real-time; the **metric layer does**.)
- **`ReviewQueue` + `NodeDetailForm`**: each proposal as a table/form → **accept / edit / reject(+reason)**. This is the **steering** surface.
- **Persistence**: the canvas reads from **Neo4j via REST**, so nodes survive a backend restart (Neo4j is durable; `events.jsonl` is the replay log). The frontend holds no source-of-truth state.

**Real-time transport: SSE** (`sse-starlette`) — one-way progress/activity is simpler and more robust than WebSockets here; browser `EventSource` auto-reconnects.

---

## 9. Steering & reject semantics (the user's explicit ask)
- **accept** → `review_state:'approved'` → arbitration writer MERGEs.
- **edit** → human-corrected payload → same MERGE path with edited values.
- **reject** → `review_state:'rejected'` + `reject_reason` (+`rejected_by/at`); **never written**. Then **re-spawn that partition's proposer with the reason injected as steering** ("proposal for metric X rejected because <reason>; revise") — Reflexion-style verbal feedback. Bounded retry (~2), then escalate to a human-authored entry. Never fake a success result back to the agent.
- **Mid-run steering**: pause at partition boundary (`interrupt()`), edit the shortlist/prompt, resume — ContextLayer's pause/resume pattern.

---

## 10. Milestones & build order

**M1 — Spine bootstrap (slash commands + MCP + Neo4j).** Use the **local Homebrew Neo4j** (`brew services start neo4j`) + install the **APOC plugin** (needed for reconcile, §5d); apply constraints/indexes (`§9`); FastMCP graph server; `.claude` hooks (confirm-table + exclusion) + slash commands; `/create-business-node` etc. populate `Business` (rich context) + `Domain` (FRD columns) + `IntelligenceProduct` (`miq/ciq/piq/dc/creative_iq`), each with confirm-before-create. **Proves the confirm→create loop end-to-end on the small layers.**

**M2 — Metric/UIComponent ingestion engine + canvas.** Deterministic pre-pass → orchestrator → per-dashboard proposer agents → proposal queue → single arbitration writer → reconcile. Vite canvas + SSE progress/activity + review queue (accept/edit/reject). The heavy, long-running, steerable layer.

**M3 — Causal layer (the point).** Formula `DECOMPOSES_INTO`/`ROLLS_UP_TO` (det.) + `rare_seeds` correlations + pointwise LLM-judge + refuter + Beta(α,β) → review queue. Success metric = **N evidence-backed `CAUSES`/`INFLUENCES` edges**, not "N nodes."

**M4 — Thin RBAC + Org-Graph engine.** Set `min_level`/`data_classification` defaults; seed illustrative `Role`s + `REPORTS_TO`; clearance+branch VIEW gate enforced before any context leaves the DB. Full adaptive Org-Graph Ingestion Engine (`§5`) as a fast-follow.

**Deferred to V2** (`schema §11`): governance instances, memory/learning layers, evidence ledger, `Person` nodes, Tool/Action runtime.

---

## 11. Challenges & mitigations
| Challenge | Mitigation |
|---|---|
| **Context window on 600 metrics** | Deterministic pre-pass + per-dashboard partition + sub-agent isolation + structured output + RAG shortlist + checkpoint/resume + context-editing (§6). |
| **Parallel write conflicts / dup nodes** | Proposers never write; single arbitration writer; `canonical_id` uniqueness constraint; managed-tx retry. Races structurally impossible. |
| **Neo4j one-DB-per-tenant** | True multi-database is **Enterprise-only**. Use the **local Homebrew Neo4j (Community, single-DB)** for the `rare_seeds` pilot — the DB *is* the boundary, no `tenant_id` (matches schema). Keep `get_session(database=…)` abstraction so swapping to Enterprise (or Dockerized multi-DB) later is trivial. |
| **APOC on Homebrew Neo4j** | `apoc.refactor.mergeNodes` (reconcile, §5d) needs the **APOC plugin** — drop the APOC core jar into `$(brew --prefix)/share/neo4j/labs` (or `/plugins`) and allow it in `neo4j.conf` (`dbms.security.procedures.unrestricted=apoc.*`). Provide a **plain-Cypher fallback** merge for environments without APOC. |
| **No Docker yet** | Run Neo4j (Homebrew) + FastAPI + Vite dev servers directly on the host. Dockerization is deferred to a later packaging step (the `infra/` compose file is a stub for then). |
| **Sparse OpenAPI fields** | Registry is the formula/semantic source (10 fields @100%); agent reconciles draft against both; missing → `null` (allowed). |
| **Hallucinated causality** | Statistical gate before LLM; require `mechanism_text`; refuter + self-consistency; Beta confidence; abstention; review-only (never auto-commit). |
| **Don't trust LLM confidence number** | Use self-consistency agreement fraction (measured ECE ~39% on verbalized confidence). |
| **Long-run crashes / Overloaded** | Checkpoint per partition + native `resume` + retry/backoff (ContextLayer-proven over 95 min). |
| **Persistence across restart** | Neo4j durable + append-only `events.jsonl`; frontend reads from DB. |
| **Graphiti temptation** | Borrow patterns (episodes, validity-window invalidation, hybrid index); don't adopt — its auto-write + emergent ontology fight our fixed schema + arbitration gate (and custom-edge bugs). |

---

## 12. Tech stack (all **latest stable** — verify-before-pin at implementation time)
- **Backend:** Python ≥3.11, **FastAPI**, **uvicorn**, **pydantic** v2, **`claude-agent-sdk`** (bundles CLI; subscription OAuth), **`mcp`/FastMCP**, **`neo4j`** driver (6.x), **`sse-starlette`**, **`sentence-transformers`** (all-MiniLM-L6-v2) for the shortlist (Neo4j native vector index), **uv** for env/deps, **ruff/pytest**.
- **Frontend (all latest):** scaffold with **`bunx --bun shadcn@latest init --preset b395TAHL8 --template vite`** → **Vite + React + TypeScript + shadcn/ui + Tailwind** (bun as runtime/pkg manager), then add **`@xyflow/react`** + **dagre** + **Zustand**.
- **Infra:** **local Homebrew Neo4j + APOC** (`brew services start neo4j`) — **no Docker yet**; `infra/docker-compose.yml` is a stub kept for a later packaging step.
- **Auth:** `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`; ensure `ANTHROPIC_API_KEY` is unset locally. (Note: from 2026-06-15 SDK subscription usage draws a separate credit pool.)
> **Action:** before pinning, run `pip index versions` / `npm view <pkg> version` (or `uv`/`npm`/`bun` latest) for each package and lock the newest stable.

---

## 13. Smoke tests (per the user's "smoke test each thing")
- **M1:** `kg bootstrap-spine` → assert Neo4j has 1 `Business`, N `Domain`, 5 `IntelligenceProduct`; constraints present; a duplicate `/create-business-node` returns "already exists" + table; an excluded endpoint is denied by the hook.
- **Pre-pass:** run on `openapi.json`+`chart-registry.json` → assert excluded paths dropped, ~600 metric drafts + 646 component drafts produced, every draft has a valid `{dashboard}:{id}` join.
- **Proposer (1 dashboard):** run on `ceo-pulse` → assert proposals validate against the schema payload, formula edges present, links resolve to existing spine ids or `NEW`.
- **Arbitration idempotency:** run the same approved batch twice → node/edge counts identical (MERGE upsert).
- **Reconcile:** a metric on 2 dashboards collapses to one node, both `SHOWN_ON` edges preserved.
- **Causal:** a known formula (`roas`) yields `DECOMPOSES_INTO` conf 1.0; a no-signal pair is *not* sent to the LLM; a rejected influence stays out of the graph.
- **Canvas/SSE:** start an ingest, watch progress+activity stream; restart backend → canvas re-renders from Neo4j (persistence).
- **Reject loop:** reject a proposal with a reason → proposer re-proposes a corrected version.

---

## 14. End-to-end verification
1. `brew services start neo4j` (local Homebrew, APOC installed); `kg schema-init`.
2. `/create-business-node` … (spine) via the Claude Code CLI; confirm spine on the canvas.
3. `/ingest-dashboard ceo-pulse` → watch the canvas (progress bar + live endpoints, graph building in real time), review/approve proposals → nodes appear, persist across a backend restart.
4. `/ingest-all` (parallel, ~90 dashboards) → reconcile → assert ~600 `Metric` + 646 `UIComponent` nodes, tri-axis edges present, Platform lazily materialized.
5. `/run-causal` → review `DECOMPOSES_INTO`/`CORRELATES_WITH`/proposed `INFLUENCES`; assert no auto-committed `CAUSES`, every causal edge has `confidence·evidence_mass·mechanism·review_state`.
6. RBAC spot-check: a low-clearance role's read is filtered before context leaves the DB; VIEW ≠ EDIT.

---

## 15. Implementing with ultracode
Build **Milestone by milestone**, each as its own `ultracode` workflow (understand → implement → adversarially review → smoke-test), staying in the loop between phases. M1 first (smallest, proves the loop), then M2 (the engine + canvas), then M3 (causal), then M4 (RBAC).

### Decision rationale (validated against `../ContextLayer`)
The choices here either **match the design ContextLayer proved over a ~95-min run** or are **deliberate upgrades** for this project's specifics:
- **Match:** Vite + React + `@xyflow/react` + Zustand frontend; subscription OAuth auth; SSE transport; full-agentic multi-pass ingestion with a human review gate; parallel-per-partition proposers.
- **Justified divergence:** deterministic **draft pre-pass** (our sources are *structured* — ContextLayer's `Start.md` was free text with nothing to pre-extract); **Neo4j** persistence (the schema + RBAC demand Cypher/constraints — ContextLayer used plain JSON for ~145 nodes); **stdio FastMCP + `.claude` hooks + slash commands** (you explicitly want the CLI confirm-before-create workflow, which ContextLayer's app-driven design doesn't have).

The single most important refinement over a naïve ContextLayer copy is **deterministic draft → agent-reconciles** input — it keeps the context window flat where a pure-agentic copy would balloon on 600 structured items.
