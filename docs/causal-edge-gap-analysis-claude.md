# Causal Edge Coverage — Gap Analysis

> **Companion document:** the fix is specified in
> [`causal-edge-coverage-plan-claude.md`](causal-edge-coverage-plan-claude.md).
> This document is the **diagnosis only**.

## Why this exists

In the canvas, `Top Performing Campaigns` (`metric:klaviyo-email:top_campaigns`) shows only
**2 downstream edges** — `decomposes into 1` and `correlates with 1`. The concern was: *is that the
real causal picture, or are factors missing — and are cross-domain effects (e.g. "inventory affected
by marketing & sales") captured at all?*

**Verdict: the 2 edges are an artifact of candidate generation, not a complete causal picture.**
Three specific doubts, all confirmed:

1. Only 2 nodes affecting top campaign performance → **incomplete**, not the true driver set.
2. Other factors *do* exist — the engine even judged some it never attached.
3. Cross-domain influence is **not captured today** and structurally **cannot be** with the current gate.

---

## What the screenshot actually shows

The panel is filtered to **Downstream**, i.e. what `top_campaigns` *decomposes into / correlates
with* — not what *drives* it. The 3 edges that exist for this node (in
`data/proposals/run-20260616T065702Z/causal-llm-links.jsonl`) are:

| Edge | Confidence | Meaning |
|---|---|---|
| `DECOMPOSES_INTO → campaign_performance` | 0.90 | definitional ranking metric |
| `CORRELATES_WITH → worst_campaigns` | 0.60 | same campaign pool, ranked the other way |
| `worst_campaigns CORRELATES_WITH → top_campaigns` | 0.70 | reverse of the above |

There are **zero `INFLUENCES` edges pointing into** `top_campaigns`, so the "Upstream" (what drives
it) view is effectively empty.

---

## Root causes (evidence-grounded)

### 1. Only the cheap linker ran
Every current causal edge has `source_kind: "llm_link"` — the one-shot `--llm-links` pass. The
verified influence pass (`--llm` → judge + refuter + self-consistency + `Beta(α,β)`,
`source_kind: "llm_proposal"`) produced **none** for this cluster. The entire 535-node graph holds
**only ~16 causal edges**, all inside the klaviyo-email / klaviyo-flows island.

### 2. The influence candidate gate is domain-siloed
`influence_candidates()` (`harness/ingest/causal.py:554-631`) surfaces a pair only if it:
- **(a)** matches a `rare_seeds` prior, **OR**
- **(b)** **co-occurs on the same dashboard** *and* is role-compatible *and* describes a different concept.

But dashboards are single-domain — e.g. the `inventory` dashboard holds *only* inventory metrics
(`category_health_matrix`, `out_of_stock_alerts`, `low_conversion_items`, …), all domain
`supply_chain`. And the 43 priors in `harness/seed/rare_seeds_priors.json` are all funnel concepts
(`spend → impressions → … → revenue`) — **none mention inventory or campaigns**. So cross-domain
pairs never become candidates → never judged → never edges.

### 3. The real drivers are `causal_role: "constraint"`
The actual upstream drivers — `deliverability_health`, `out_of_stock_alerts`,
`deliverability_metrics`, `category_health_matrix` — all carry `causal_role: "constraint"`.
But the role gate is:

```python
CAUSE_ROLES  = {"controllable", "external", "mediator", "ml_output"}   # causal.py:101-103
EFFECT_ROLES = {"outcome", "mediator"}                                  # causal.py:104
```

`constraint` is in **neither set**. So even *within* the klaviyo-email dashboard,
`deliverability_health → top_campaigns` can never be surfaced by the structural gate — even though
the linker independently judged deliverability to influence `worst_campaigns`, `flow_performance`,
and `source_comparison`. **That inconsistency is the engine telling us the driver is real but
un-generated for `top_campaigns`.**

### 4. The linker is one-shot from a platform-biased shortlist
`link_candidates()` (`causal.py:830-881`) ranks candidates: formula(5) > same-concept(4) >
**same-platform(3)** > same-dashboard(2), capped at 25; the LLM then picks a handful in a single
call. Not exhaustive, and biased to stay within platform/domain.

### 5. `--limit` (default 24) truncates a 535-node graph
In `run_causal` the single `candidate_limit` parameter doubles as both the linker **subject** cap
(`causal.py:1201-1202`) and the influence **candidate** cap (`causal.py:1222`). Either way, most
pairs are dropped. (Drops are logged via the `causal_candidates_capped` event, so the truncation is
at least not silent.)

---

## Cross-domain verdict

Cross-domain edges are **allowed by the schema and code** (there is no domain filter on edges) but
are **never produced**, because the only two candidate sources — dashboard co-occurrence and funnel
priors — cannot bridge domains. "Inventory affected by marketing/sales" requires a candidate that
crosses the `supply_chain ↔ marketing` boundary, and nothing currently generates it.

---

## Two extra blockers any fix must clear

These were found while validating the fix and are easy to miss:

- **B1 — Vocabulary mismatch.** Ingestion left `concept_key` / `metric_base` mostly as **raw
  chart-ids**. Of 329 distinct live `concept_key`s, only **6** are clean slugs (`revenue`, `spend`,
  `roas`, `average_order_value`, `conversion_rate`, `new_customers`). `ConceptIndex.resolve()` matches
  by **exact key** with no substring fallback (`causal.py:254-262`), so a concept DAG written in clean
  slugs (`out_of_stock`, `deliverability`) **resolves to nothing**. Aliases must bridge slug → real id.

- **B2 — Role gate excludes constraints.** As in root cause #3, the highest-value drivers are
  `constraint`. New cross-domain candidates **bypass the role gate** (as `rare_seeds` priors
  already do — that source never checks roles), so the primary fix has no role dependency.
  Optionally, `constraint` can be promoted to a valid cause role for the within-dashboard gate.

> **Terminology — `causal_role` is NOT RBAC.** Every "role" in this document is **`causal_role`**, an
> M3 causal-layer property on `Metric` nodes (`outcome`, `controllable`, `constraint`, `ml_output`,
> `external`, `mediator`, `untyped`). It is **already populated on 99% of metrics** (679/682) and is
> unrelated to the **RBAC `Role`** layer (M4, deferred — 0 `Role` nodes exist yet). The fix has
> **no dependency on RBAC**.

---

## Summary

| Doubt | Finding |
|---|---|
| Only 2 nodes affect top campaigns? | Artifact — only the cheap linker ran; no upstream `INFLUENCES` exist. |
| Are there other factors? | Yes — e.g. `deliverability_health`; the engine judged it real for siblings but never attached it here. |
| Cross-domain captured (inventory ← marketing/sales)? | No, and structurally impossible today (no candidate source bridges domains). |

The shortfall is entirely in **candidate generation** (which pairs get considered) — the
**verification** layer (judge + refuter + self-consistency + Beta + review-only) is sound and should
not be loosened. The fix is in the companion plan.
