---
description: Propose the deterministic metric skeleton — Metric nodes + formula/identity DECOMPOSES_INTO edges (proposals only, no write).
argument-hint: [tenant] [dashboard_id]
allowed-tools: mcp__graph__propose_metric_nodes, mcp__graph__propose_metric_edges_from_formula
---

Build the deterministic KG skeleton for a tenant as **reviewable proposals only** (plan §7/§8). Nothing is written to the graph — the single arbitration writer applies the proposals after review.

Arguments: tenant `$1` (optional; defaults to `rare_seeds`), dashboard filter `$2` (optional; empty = all dashboards).

Do this in order:

1. **Propose metric nodes.** Call `mcp__graph__propose_metric_nodes` with `tenant` (`$1` or default `rare_seeds`) and `dashboard_id` (`$2` when given). This returns the Metric upsert proposals built by the skeleton (override -> BC_2 seed -> registry-split formula resolution). Report the `count` and a few sample `metric_uid`s with their `scope_key` / `formula_text`.

2. **Propose formula + identity edges.** Call `mcp__graph__propose_metric_edges_from_formula` with the same `tenant`. This emits the SAME-SCOPE `DECOMPOSES_INTO {relation: formula}` edges parsed from each metric's formula, plus the `{relation: identity}` fallbacks for formula-less metrics. Report `formula_edges`, `identity_edges`, and how many operands landed in `unresolved` (with their `reason` — `cross_scope` or `unresolved_or_composite`). These unresolved counts are intentional (the same-scope invariant: Google-Search never decomposes into YouTube/blended), not errors.

3. **Next step.** Remind the user these are proposals only — review them on the canvas review queue, then apply through arbitration. Both tools are read-only against the graph and never write.
