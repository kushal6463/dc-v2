---
description: Build the M3 causal layer (DECOMPOSES_INTO / ROLLS_UP_TO / CORRELATES_WITH + LLM-proposed INFLUENCES) as reviewable edge proposals.
argument-hint: [--llm-links] [--platform meta_ads] [--llm] [--auto-approve] [--limit N] [--samples N]
allowed-tools: Bash(uv run kg run-causal:*), Bash(uv run kg proposals:*), Bash(uv run kg apply:*), Bash(uv run kg status:*)
---

Build the causal layer for the ThoughtWire knowledge graph (implementation plan §5e / Milestone 3). The graph builds as **proposals only** — the single arbitration writer applies them after review; causal edges are never written directly and a correlation is **never** auto-promoted to `CAUSES`.

The causal pass has these stages:
- **`DECOMPOSES_INTO`** (deterministic, confidence 1.0) — parsed from each metric's `formula_text` (e.g. `roas = revenue / spend`).
- **`ROLLS_UP_TO`** (structural, confidence 1.0) — finer-scope → coarsest-scope metric of the same `concept_key`.
- **`CORRELATES_WITH`** — the rare_seeds pilot correlations, resolved onto live metrics.
- **`INFLUENCES`** (LLM-proposed, review-only) — with `--llm`: a structural + rare_seeds-prior candidate gate → pointwise judge + refuter + self-consistency → `Beta(α,β)` confidence. Always held for review.
- **LLM linking** (`--llm-links`) — for each metric, the LLM reads its formula + derived platform (from `scope_key`) + a shortlist of real candidate metrics and builds its `DECOMPOSES_INTO` / `CORRELATES_WITH` edges directly, plus `INFLUENCES` that are then judge+refuter+Beta verified. This **replaces** the deterministic formula parse for that run and is the way to densify the tree. Scope/cost-control with `--platform <slug>` (`meta_ads`/`google_ads`/`klaviyo`/`magento`/`ml`/`blended`) and `--limit <subjects>`.

Requested arguments: `$ARGUMENTS` (forward `--llm-links`, `--platform`, `--llm`, `--auto-approve`, `--limit N`, `--samples N` as given; default to none).

Do this in order:

1. **Run the pass.** Execute `uv run kg run-causal $ARGUMENTS`. This scans every `Metric`, writes the edge proposals to a fresh run, and prints a per-stage summary table.

2. **Report the summary.** Surface the per-stage counts (formula / rollup / correlation / influence proposed + rejected) and the `run_id`. Note how many correlations were skipped (unresolved concepts) and how many LLM candidates the cap dropped — these are intentional, not errors.

3. **Next step.** Unless `--auto-approve` was passed, tell the user to review the proposals on the canvas review queue (or with `uv run kg proposals list --run <run_id>`), then apply with `uv run kg apply --run <run_id>`. Remind them that `--auto-approve` only applies the deterministic edges; LLM `INFLUENCES` are always held for human review.
