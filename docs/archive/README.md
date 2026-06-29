# Archive — historical & superseded design docs

These documents are kept for **historical reference only**. They describe earlier iterations of the
schema and the deterministic "skeleton" build that the project has since replaced with the LLM
agentic builder (`kg build`).

**For the current system, see:**

- [`/ARCHITECTURE.md`](../../ARCHITECTURE.md) — authoritative architecture (modules, AI layer, data flow). Browsable HTML at `/ARCHITECTURE.html`.
- [`../final-schema-claude.md`](../final-schema-claude.md) — authoritative node/edge property contract.
- [`/README.md`](../../README.md) — quickstart + full rebuild runbook.

## What's here and why it was archived

| File(s) | Superseded by |
|---|---|
| `schema-iterations/` | `docs/final-schema-claude.md` (the frozen V1 schema) |
| `final-schema-codex.md` / `.html` | `docs/final-schema-claude.md` (claude variant chosen as canonical) |
| `kg-skeleton-*.md`, `metric-skeleton-implementation-claude.md`, `deterministic-edge-formation-codex.md` | the LLM agentic builder (`harness/agentic/`, `kg build`) — the deterministic skeleton is no longer the build mechanism |
| `implementation-kg-plan.md` | implemented; see `/ARCHITECTURE.md` |
| `kg-integration-plan-claude.md` | `INTEGRATION-ANALYSIS-claude.md` |
| `kg-llm-ingestion-workflow.md` / `.html` | `/ARCHITECTURE.md` §05 (AI layer) |
| `causal-edge-coverage-plan-claude.md`, `causal-edge-gap-analysis-claude.md` | planning history; edge model now in `/ARCHITECTURE.md` §08 + `final-schema-claude.md` |
