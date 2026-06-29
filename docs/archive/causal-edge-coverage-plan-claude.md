# Causal Edge Coverage — Fix Plan

> **Companion document:** the diagnosis is in
> [`causal-edge-gap-analysis-claude.md`](causal-edge-gap-analysis-claude.md). Read it first.

## Context

The canvas shows `Top Performing Campaigns` with only 2 downstream edges and **nothing driving it
upstream**. The gap analysis traced this to **candidate generation** — cross-domain and constraint
drivers are never surfaced, so the LLM never judges them. The user's hard constraints for the fix:

1. **No hallucinated / random edges.**
2. **Maximize determinism.**

This plan adds the missing coverage *without* touching the verification layer. The LLM stays
**only a judge/refuter over a deterministically-generated, human-seeded pair** — it never invents
endpoints. A cross-domain edge can exist only if (a) a human hypothesized the concept link in a
curated DAG, **and** (b) judge + refuter + self-consistency confirmed it on the resolved instances,
**and** (c) a reviewer approved it (review-only; `CORRELATES_WITH` is never auto-promoted to `CAUSES`).

---

## Design principles (kept invariant)

- **Candidate generation stays 100% deterministic & human-seeded.** Cross-domain pairs come only
  from a curated concept DAG + deterministic `resolve()` tie-breaking (`causal.py:216-230`).
- **All existing gates preserved:** non-empty-mechanism-or-reject, refuter, self-consistency
  agreement fraction (not model-stated confidence), `Beta(α,β)` fold, `ACCEPT_THRESHOLD`,
  review-only, never auto-promote `CORRELATES_WITH → CAUSES`.
- **Only the model verdict is irreducibly non-deterministic.** Bound it with a verdict ledger
  (Step 6) so re-runs are reproducible at the I/O boundary; everything upstream is pure.

> **Terminology — `causal_role` is NOT RBAC.** The "role gate" referenced below uses `causal_role`,
> an M3 causal-layer property already populated on 99% of metrics (679/682). It is unrelated to the
> **RBAC `Role`** layer (M4, deferred — 0 `Role` nodes exist yet). This plan has **no dependency on
> RBAC**, and the only role-touching step (Step 4) is **optional**.

**Dropped from scope** (deliberately): the "shared-dimension structural bridge" idea (pair any
role-compatible metrics sharing a `category`). It is O(n²) over large buckets on 535 nodes
(e.g. 187 `outcome` metrics), floods the candidate cap, and `category` is a coarse enum, not a join
key. The curated DAG gives the same cross-domain reach deterministically without the blowup.

---

## Plan (ordered)

### Step 1 — Extend the alias table *(prerequisite — without it everything else is a no-op)*
In `harness/ingest/causal.py:147-159` (`_ALIAS_GROUPS`), add groups bridging clean DAG slugs to the
**real stored chart-ids** (blocker B1), e.g.:
- `{"out_of_stock", "out_of_stock_alerts", "stock_out_risk", "items_stopped_selling"}`
- `{"deliverability", "deliverability_health", "deliverability_metrics"}`
- `{"campaign_engagement", "engagement_heatmap", "engagement_rate_trends", "engagement_timeline"}`
- `{"demand", "product_forecast", "category_forecast", "demand_curves"}`
- (+ the rest the DAG needs, derived from live `concept_key`s in `data/proposals/`)

Add a unit test mirroring `test_concept_index_resolves_best_scope` asserting
`resolve("out_of_stock")` hits `metric:inventory:out_of_stock_alerts`.

### Step 2 — Add the curated concept-causal DAG seed
New file `harness/seed/concept_causal_dag.json` — hand-authored **cross-domain** concept→concept
hypotheses in clean slugs, each with a `mechanism_hint`, mirroring the provenance discipline of
`rare_seeds_priors.json` (a hint is *not* evidence; the pair is still judged/refuted/review-only).
Seed the user's cases explicitly, e.g.:

```
marketing_spend → demand            (demand pulled by acquisition pressure)
demand          → out_of_stock      (demand outruns replenishment)
out_of_stock    → conversion_rate   (stockouts suppress conversion)
deliverability  → campaign_engagement (inbox placement gates opens/clicks)
inventory_availability → revenue    (you can't sell what isn't in stock)
```

Keep it **separate** from `rare_seeds_priors.json` (different provenance) but feed both through the
same emit loop.

### Step 3 — Add "Source 3" to `influence_candidates()`
In `causal.py`, after L630 (before `return candidates`). Add a `CONCEPT_DAG_SEED` constant near L84.
Load the DAG via `_load_seed` (tolerant of a missing file), resolve both endpoints with
`index.resolve()`, dedup against `seen`, and emit `{a_uid, b_uid, signal, prior_mechanism}` —
**bypassing the role gate** (exactly like Source 1 priors). This is the cross-domain +
richer-driver engine.

### Step 4 — (OPTIONAL) Promote `constraint` in the within-dashboard role gate (blocker B2)
*This is the only step that touches `causal_role`, and it is NOT required for the cross-domain fix —
Source 3 (Step 3) already delivers that, role-independently. Skip it freely if you'd rather not
adjust role semantics yet; it is unrelated to RBAC/M4.*
Add `"constraint"` to `CAUSE_ROLES` (`causal.py:101-103`) so deterministic constraints
(deliverability, stock, budget caps) can be surfaced as causes by Source 2 dashboard co-occurrence.
Leave `EFFECT_ROLES` unchanged. Update `test_candidate_gate_role_compatibility` and treat this as a
deliberate, reviewed `causal_role` semantic change (no impact on the deferred RBAC `Role` layer).

### Step 5 — Harden the seed-coupled test
Source 3 reads the real DAG file, which couples `test_candidate_gate_excludes_no_signal_pair`
(`harness/tests/test_causal.py:211-221`) to seed content. Inject a test seed path (or assert the
no-signal pair's tokens are provably disjoint from DAG/alias vocabulary) so future seed edits can't
silently flip it.

### Step 6 — Split the limit + add a verdict ledger (determinism)
- Split `run_causal`'s `candidate_limit` into `subject_limit` (linker, L1201-1202) and
  `candidate_limit` (influence, L1222). In the CLI (`harness/cli/kg.py:972-978`, `cmd_run_causal`
  L697-730) add `--subject-limit` / `--candidate-limit`; keep `--limit` as a back-compat alias that
  sets both (avoids breaking scripts/tests).
- Introduce `CAUSAL_PROMPT_VERSION` in `harness/agent/prompts.py`; wrap `assess_candidate`
  (`causal.py:677`) with a disk cache keyed `(a_uid, b_uid, CAUSAL_PROMPT_VERSION, n_samples)`,
  modeled on the existing `_load_linked_subjects` / `_save_linked_subjects` markers
  (`causal.py:1042-1060`). Makes re-runs reproducible without pretending the model is deterministic.

### Step 7 — Run the verified pass graph-wide (operational, not code)
Cross-domain / richer influence uses `--llm` (structural judge over Sources 1+3), **not**
`--llm-links`. Run graph-wide with a large `--candidate-limit` and bounded `--concurrency`; review;
apply. `--llm-links` stays for within-platform decomposition only.

---

## Critical files

| File | Change |
|---|---|
| `harness/ingest/causal.py` | `_ALIAS_GROUPS` (L147-159); `CONCEPT_DAG_SEED` const (~L84); `CAUSE_ROLES` (L101); Source 3 in `influence_candidates()` (after L630); limit split in `run_causal` (L1153-1295); verdict-ledger wrap of `assess_candidate` (L677) |
| `harness/seed/concept_causal_dag.json` | **new** — curated cross-domain concept DAG |
| `harness/agent/prompts.py` | add `CAUSAL_PROMPT_VERSION` (ledger key) |
| `harness/cli/kg.py` | `--subject-limit` / `--candidate-limit` + `--limit` alias (L972-978, L697-730) |
| `harness/tests/test_causal.py` | new alias-resolve test; harden no-signal test (L211); update role-compat test (L224) |

---

## Verification (end-to-end)

1. **Unit:** `uv run pytest harness/tests/test_causal.py` — all green, including the new
   alias-resolve test and the updated gate tests (keep the 61-test suite passing).
2. **Resolution sanity:** a small script/REPL asserting every DAG endpoint resolves to a live
   `metric_uid` (no silent no-ops); log any unresolved DAG edge.
3. **Candidate count:** `uv run kg run-causal --llm --candidate-limit 500` → inspect
   `summary["influence_candidates"]` (expect it to jump from ~tens to hundreds, with cross-domain
   pairs present) and `influence_candidates_dropped == 0`.
4. **Target-node check:** in `data/proposals/<run>/causal-influence.jsonl`, confirm **upstream**
   `INFLUENCES → top_campaigns` now exist (e.g. `deliverability_health`, send-volume) and at least
   one genuine cross-domain edge (e.g. `marketing_spend` / `demand → out_of_stock_alerts`), each with
   a non-empty `mechanism` and `source_kind: "llm_proposal"`.
5. **No-hallucination audit:** every new `INFLUENCES` edge traces to a DAG/prior concept pair and a
   passed judge+refuter; spot-check 5 entries in `causal_candidate_rejected` events to confirm the
   refuter is still pruning. Confirm nothing was auto-applied (all `review_state: "proposed"`).
6. **Determinism:** re-run Step 3's command twice; with the verdict ledger, the accepted edge set is
   identical across runs (same `run-causal` summary counts).
