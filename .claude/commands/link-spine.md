---
description: Attribute every metric onto the tri-axis spine (Domain · Product · Platform) and emit its deterministic spine edges as reviewable proposals (--llm proposes residual axes, review-only).
argument-hint: [--tenant rare_seeds] [--dry-run] [--apply-safe] [--llm]
allowed-tools: mcp__graph__propose_spine_links, Bash(uv run kg link-spine:*)
---

Wire every metric onto the three spine axes — **Domain** (FRD functional column), **IntelligenceProduct** (IQ app), and **Platform** (source/action vendor) — as reviewable proposals (Phase 2). The deterministic cascade runs from LOCAL signals only; the single arbitration writer applies the proposals after review. **Governance: Decision Canvas (`dc`) is built separately and is NEVER auto-assigned** — neither the deterministic cascade nor the `--llm` residual pass may assign it.

Requested arguments: `$ARGUMENTS` (forward `--tenant`, `--dry-run`, `--apply-safe`, `--llm` as given; default to none = write the deterministic proposals).

Do this in order:

1. **Inspect deterministically (read-only).** Call `mcp__graph__propose_spine_links` (optionally with `metric_uid` / `scope` / `tenant`) to preview the attribution. It reports each metric's resolved `domain_ids` / `product_ids` / `platform_ids`, the `residual` axes, and the flat deterministic edge proposals. This tool is proposal-only — it never writes the graph.

2. **Run the pass.** Execute `uv run kg link-spine $ARGUMENTS`. This computes the deterministic attribution + spine edges over `build_skeleton(tenant)`, prints the per-axis coverage (with / residual) and **asserts 0 metrics are auto-assigned to `dc`**, then:
   - `--dry-run` — prints only, no proposals written.
   - default — writes the deterministic spine edge proposals (`review: false`, auto-safe) to a fresh run.
   - `--apply-safe` — writes + approves + applies them (auto-safe).
   - `--llm` — ALSO runs the LLM residual pass over the residual metrics and writes its **REVIEW-ONLY** `BELONGS_TO_DOMAIN` / `PART_OF_PRODUCT` / `SOURCES` proposals (`review: true`, `source_kind=spine_link_llm`), held for human review. The product vocabulary excludes `dc`; any `dc` the LLM returns is dropped.

3. **Report + next step.** Surface the coverage table, the residual counts, the `run_id`, and confirm 0 `dc` assignments. Unless `--apply-safe` was passed, tell the user to review with `uv run kg proposals list --run <run_id>` then apply with `uv run kg apply --run <run_id>`. The `--llm` residual proposals are always held for review (never auto-applied).
