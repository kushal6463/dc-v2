---
description: Propose INFLUENCES edges from the machine-discovery feed (data/discovered_edges.<tenant>.csv) as reviewable proposals — no write.
argument-hint: "[tenant] [csv_path] (both optional; default tenant rare_seeds, default vendored CSV)"
allowed-tools: mcp__graph__propose_discovery_edges, Bash(uv run kg import-discovered-edges:*)
---

Propose metric→metric `INFLUENCES` edges from the **machine-discovery feed** — the vendored `data/discovered_edges.<tenant>.csv` produced by the causal-discovery engine (PCMCI+ / parcorr / cmiknn). This feed **supersedes** the hand-typed seed correlations.

**Read-only / proposal-only.** This never mutates the graph. It resolves each discovered `src`/`dst` node id (`scope.metric[.agg]`, e.g. `google.roas`, `meta.impressions`, `blended.budget.sum`) to a live metric and returns reviewable proposals tagged `source_kind='kg_discovery'`. Discovery proposals are **review-protected**: a later deterministic recompute never auto-deprecates them, and a `kg_discovery` re-import never clobbers an edge a human already approved/applied (it only appends its `source_ref` to the edge's `provenance`).

## Steps

1. Call `mcp__graph__propose_discovery_edges` with the optional `tenant` (default `rare_seeds`) and optional explicit `csv` path (empty = the vendored `data/discovered_edges.<tenant>.csv`).
2. Report the returned JSON:
   - `resolved` — count / list of discovered edges that resolved to live metrics and became proposals.
   - `unresolved` + `reasons` — rows whose endpoints did not resolve (no silent drops); surface the per-row reason.
   - `proposals` — the proposal payloads (each an `INFLUENCES` edge proposal, `source_kind='kg_discovery'`).
3. To run the same import from the CLI instead, use `uv run kg import-discovered-edges` (still proposal-only).

Do **not** apply these to the graph from this command — applying happens only after review.
