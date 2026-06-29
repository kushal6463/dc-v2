# Metric↔Metric Deterministic Skeleton — Implementation Spec

> **Status:** design + implementation spec (approved). **No code is changed by this document.**
> It specifies the work to build a deterministic metric-to-metric "skeleton" graph in `dc-kg`,
> consolidate the edge taxonomy, keep sub-channel edges correct, and lay the seam for a later
> tenant-specific statistical/causal layer.
>
> Companion docs: `INTEGRATION-ANALYSIS-claude.md` (knowledgeGraph ↔ dc-kg seam),
> `docs/causal-edge-gap-analysis-claude.md` + `docs/causal-edge-coverage-plan-claude.md`
> (the cross-domain `INFLUENCES` layer this spec builds toward).

---

## 1. Context — why this work

We want a **deterministic skeleton graph** connecting **metric → metric** (on top of the existing
metric → dashboard / product / domain spine) that is *correct by construction*. The motivating
requirement: *Google Search ad revenue* must decompose into *Google Search* components — never into
*Google YouTube ROAS*. The skeleton holds only relationships we can **prove** (formulas, identities,
roll-ups, funnels, aliases) at **confidence 1.0**. Context-dependent relationships (seasonality,
cross-domain business logic such as *sales ↔ inventory*) belong to a **later** statistical/causal
layer, because they are tenant-specific: an agriculture business sees low winter sales while a
clothing business sees high winter sales. The deterministic skeleton must be **identical in shape
across tenants**; only the empirical layer diverges.

**Key finding:** `dc-kg` already has most of this machinery in `harness/ingest/causal.py`
(`run_causal()` at `causal.py:1153`): it produces `DECOMPOSES_INTO` (formula, conf 1.0),
`ROLLS_UP_TO` (scope rollup, conf 1.0), `CORRELATES_WITH` (seed stats), and LLM `INFLUENCES`, all
flowing through a single arbitration writer with human review. So this is an **extend + consolidate**
effort, not a greenfield build — but it has two real correctness gaps, one taxonomy decision, and a
day-2 (incremental) gap, and `knowledgeGraph` contributes four genuinely additive ideas.

---

## 2. Findings that drive the design

### 2.1 Two correctness gaps in the existing deterministic layer
1. **Scope-blind formula resolution (the Google-Search → YouTube risk).**
   `ConceptIndex.resolve()` (`causal.py:254`) takes *no scope argument*; it always returns `_best()`,
   the **broadest-scope** match (`causal.py:216-230`). A `google-search` ROAS whose formula is
   `revenue / spend` therefore decomposes into **blended** revenue/spend, not its own channel's. This
   is exactly the cross-subdomain mis-link we must eliminate.
2. **Additivity-blind roll-ups.** `rollup_edges()` (`causal.py:411`) rolls up *every* concept with
   `aggregation_method = "sum"` (`causal.py:443`), including ratios. Summing channel ROAS → blended
   ROAS is arithmetically wrong (blended ROAS = ΣconvValue / Σspend).

### 2.2 A pre-existing constraint to respect (blocker B1)
`docs/causal-edge-gap-analysis-claude.md` shows `concept_key`/`metric_base` are mostly **raw
chart-ids** (only 6 of 329 are clean slugs), and `resolve()` matches by **exact key**. So any
clean-slug vocabulary (formula operands, identity tables, funnel stages) must be bridged through
`_ALIAS_GROUPS` (`causal.py:147-159`) or it silently resolves to nothing.

### 2.3 What `knowledgeGraph` contributes (verified, additive)
| Idea | Source in `knowledgeGraph` | What it fixes here |
|---|---|---|
| **Same-scope component resolution** | `tools/build_structural_edges.py` (resolve only within `(scope, base)`) | Gap #1 — the sub-channel correctness requirement |
| **Additive-only crossproduct** | `tools/build_crossproduct_edges.py` (`ADDITIVE` allowlist) | Gap #2 — never sum a ratio |
| **Canonical IDENTITIES table** | `tools/build_structural_edges.py` (`roas=convValue/spend`, `cpa=spend/conversions`, `aov=revenue/orders`, `cpc=spend/clicks`, `frequency=impressions/reach`, …) | dc-kg's `formula_text` is "often null" — gives a deterministic fallback |
| **Funnel / compositional edges** | `tools/build_compositional_edges.py` (`reach→impressions→clicks→conversions→conversion_value`, `sessions→orders→revenue`, `emails_sent→open→click`) | A deterministic edge type dc-kg lacks |

`knowledgeGraph` does **not** solve seasonality / tenant variation — it deseasonalizes (STL) and then
emits one static global graph (its own docs defer per-tenant/seasonal conditional edges to "v2"). So
that part we **design** (Phase C), not copy.

### 2.4 Decisions captured during the interview
- **Build approach — Hybrid:** extend `causal.py` (with the fixes) → proposals → review → Neo4j,
  **and** emit a flat inspectable `data/skeleton_export.<tenant>.csv`.
- **Capabilities:** canonical IDENTITIES fallback, additivity-safe roll-ups, SAME_AS / alias, and
  *capture as many deterministic relationships as possible* (funnel/compositional included — that is
  where future business logic attaches).
- **Sub-channel correctness:** sub-channels are distinct scopes → **same-scope resolution is the
  mechanism** (no new channel-hierarchy field needed now).
- **Tenant/seasonal variation:** **document the design only**; the skeleton stays purely
  deterministic.
- **Edge taxonomy:** consolidate to **2 metric↔metric edge types** — `CORRELATES_WITH`
  (deterministic, *formula-grounded*) and `INFLUENCES` (the richer **superset**: business logic,
  seasonal, cross-domain, causal). The user's framing: *"correlates is with respect to formulas … all
  the correlates will be in the influences since it's formula based … influences should be very good
  at capturing niche business logic (winter season, sales↔inventory)."*

---

## 3. Target edge taxonomy (the consolidation)

Today's 5 metric↔metric types (`models.py:1013` `EDGE_TYPES`: `DECOMPOSES_INTO`, `ROLLS_UP_TO`,
`CORRELATES_WITH`, `INFLUENCES`, `CAUSES`) collapse to **2**, each carrying a `relation` discriminator
property — the `knowledgeGraph` pattern (one edge type, many `relation`s):

| New type | `relation` values | Confidence | Direction | Built |
|---|---|---|---|---|
| **`CORRELATES_WITH`** (deterministic skeleton) | `formula`, `identity`, `rollup`, `crossproduct`, `funnel`, `alias` | 1.0 (funnel 0.85, crossproduct 0.9) | directed (alias symmetric) | **Phase A — now** |
| **`INFLUENCES`** (business-logic / causal superset) | `statistical`, `causal`, `cross_domain`, `seasonal` | variable (Beta-folded) | directed | Phase B (mostly exists) + Phase C (design only) |

**Mapping old → new:**
- `DECOMPOSES_INTO` → `CORRELATES_WITH {relation: formula | identity}`
- `ROLLS_UP_TO` → `CORRELATES_WITH {relation: rollup | crossproduct}`
- *(new)* funnel → `CORRELATES_WITH {relation: funnel}`
- *(new)* alias / `SAME_AS` → `CORRELATES_WITH {relation: alias, symmetric: true}`
- old statistical `CORRELATES_WITH` (the 4 seed rows) → `INFLUENCES {relation: statistical}`
- LLM `INFLUENCES` + curated cross-domain DAG → `INFLUENCES {relation: causal | cross_domain}`
- `CAUSES` → retired from auto-emission (promotion stays a manual review action only)

> **Naming caveat (confirm before coding):** this *redefines* `CORRELATES_WITH` to mean
> "deterministic, formula-grounded" — the opposite of its conventional "empirical co-movement"
> meaning — per the user's explicit framing. If preferred, the deterministic type can instead be
> named `STRUCTURAL` / `DERIVES_FROM`; the implementation is identical apart from the string. Default
> is the user's `CORRELATES_WITH`.

---

## 4. Phase A — build the deterministic CORRELATES skeleton

All changes in **`harness/ingest/causal.py`** unless noted. Every edge stays a
`review_state: "proposed"` proposal through the existing `_edge_proposal()` builder (`causal.py:270`)
→ `run_causal()` → arbitration. Builders are pure (no DB / no LLM).

### A1. Scope-aware resolution — *fixes gap #1*
- Extend `ConceptIndex.resolve(concept, prefer_scope=None)` and `_best(uids, prefer_scope=None)`
  (`causal.py:216-262`): when `prefer_scope` is set, first restrict candidates to those whose
  `scope_key` equals the subject's `scope_key` (then same platform-prefix via `_platform_of`,
  `causal.py:126`); fall back to broadest-scope `_best()` only when no same-scope match exists.
  Default `prefer_scope=None` preserves all current callers/tests.
- `formula_edges()` (`causal.py:352`) and the new `identity_edges()` pass the subject's `scope_key`
  as `prefer_scope`.
- New unit test (mirror `test_concept_index_resolves_best_scope`): a `google-search` ROAS resolves
  `spend`/`revenue` to the `google-search` metrics, **not** `blended`.

### A2. Additivity-safe roll-ups + additive crossproduct — *fixes gap #2*
- Add `_is_additive(metric)`: `True` when `not metric["is_derived"]` **and** `aggregation ∈
  {sum, level, count}` **and** `unit_family ∈ {currency, count, duration}` (i.e. not ratio / percent
  / score). Uses fields already on `Metric` (`models.py:722-765`).
- In `rollup_edges()` (`causal.py:411`): set `aggregation_method` from the real `aggregation` (never
  hard-default `"sum"` for ratios) and tag `relation`:
  - additive base → `relation: crossproduct`, `confidence: 0.9`, `aggregation_method: sum`
  - non-additive (ratio) → `relation: rollup`, `confidence: 1.0`, `aggregation_method: ratio|avg`
    (a definitional re-aggregation, not a sum).
- Emit as `CORRELATES_WITH` (not `ROLLS_UP_TO`).

### A3. Canonical IDENTITIES fallback — new `identity_edges()`
- Add `IDENTITIES: dict[str, tuple[list[str], list[str]]]` (numerator bases, denominator bases),
  ported from `knowledgeGraph/tools/build_structural_edges.py` (`roas, cpa, cpc, cpm, aov, ctr, cvr,
  frequency, …`).
- For each metric whose `formula_text` is null/unknown (`formula_status != explicit`) and whose
  `metric_base ∈ IDENTITIES`, resolve numerator + denominator bases **within the same scope** (A1) and
  emit `CORRELATES_WITH {relation: identity, confidence: 1.0, operator: divide}`. Skip (and log) when
  either side does not resolve — never invent endpoints.

### A4. Funnel / compositional edges — new `funnel_edges()`
- Add `FUNNEL_FLOW: list[tuple[str,str]]`, ported from
  `knowledgeGraph/tools/build_compositional_edges.py`.
- Index by `(scope_key, metric_base)`; for each scope and each adjacent `(upstream, downstream)` pair
  where **both** nodes exist in that scope, emit `CORRELATES_WITH {relation: funnel, confidence:
  0.85}`. Same-scope only (a Search funnel never bridges to YouTube).

### A5. Alias / SAME_AS edges — new `alias_edges()`
- Emit `CORRELATES_WITH {relation: alias, symmetric: true, confidence: 1.0}` when two metrics in the
  **same scope** are provably the same — identical `card_endpoint` (or `series_endpoint`), or one
  appears in the other's `synonyms` (`models.py` `Metric.synonyms`). Conservative: no fuzzy name
  matching (dc-kg has no live series for `knowledgeGraph`'s series-equality test — can be added
  later).

### A6. Taxonomy + allowlist changes
- `models.py:1013` `EDGE_TYPES`: keep `CORRELATES_WITH`, `INFLUENCES`; remove `DECOMPOSES_INTO`,
  `ROLLS_UP_TO`, `CAUSES` from the metric-edge set (spine/RBAC types untouched). Allow `relation` /
  `symmetric` edge props if validated.
- `arbitration.py` `upsert_edge` validates `rel_type` against `EDGE_TYPES` (`arbitration.py:171`) —
  confirm the consolidated type passes.
- `schema.py`: drop constraints/indexes that reference removed rel types.

### A7. Orchestration + hybrid CSV export (`run_causal`, `causal.py:1153`)
- Replace the formula/rollup/correlation stage wiring (`causal.py:1181-1183`, `1260-1266`) with the
  new deterministic builders — `formula_edges`, `identity_edges`, `rollup_edges` (additive-aware),
  `funnel_edges`, `alias_edges` — all emitting `CORRELATES_WITH`. Reclassify the seed
  `correlation_edges()` output as `INFLUENCES {relation: statistical}` (review-only) so the skeleton
  run stays deterministic-only.
- Add `_write_skeleton_export(run_id, proposals)` → `data/skeleton_export.<tenant>.csv` columns:
  `from_uid, from_name, from_scope, relation, to_uid, to_name, to_scope, confidence, operator,
  aggregation_method, mechanism, source_kind`. (The inspectable inventory artifact — the "hybrid".)
- Extend the `run_causal` summary (`causal.py:1272`) with per-`relation` counts.

### A8. Skill / CLI text
- Update `.claude/commands/run-causal.md` to the 2-type taxonomy, the CSV export, and the new
  `--reconcile` / `--dry-run` flags (§7).

---

## 5. Phase B — consolidate the INFLUENCES superset (mostly already designed)

The richer, non-deterministic layer is **already specified** in
`docs/causal-edge-coverage-plan-claude.md` (a curated cross-domain concept DAG → judge + refuter +
Beta, review-only). Phase B routes that output, the LLM influences, and the reclassified statistical
seeds under the single `INFLUENCES` type with `relation ∈ {statistical, causal, cross_domain}`. No new
science — reference and reuse that plan; do not duplicate it. This is what delivers the user's
*sales ↔ inventory* cross-domain capture.

---

## 6. Phase C — tenant / seasonal contextual layer (design only)

How context-dependent edges attach **on top of** the deterministic skeleton without changing it:
- **Per-tenant profile** gates which `INFLUENCES` relations are admissible/expected for a tenant. The
  agriculture-vs-clothing seasonality divergence lives here — never in the skeleton.
- **Statistical producer** = `knowledgeGraph`'s `discover_engine.py` (STL deseasonalize → Granger /
  MI / lag → Benjamini–Hochberg FDR → stability selection → PCMCI+ conditioning) feeding
  `INFLUENCES {relation: statistical | causal}` as **review-only proposals with measured Beta
  weight** — the file-handoff seam in `INTEGRATION-ANALYSIS-claude.md` §5–§6
  (`discovered_edges.<tenant>.csv`).
- **Confidence model:** replace flat `evidence_mass = 1.0` with a sample/stability/FDR-derived mass,
  so a seasonal edge can be strong for clothing and absent for seeds while the skeleton stays
  identical across tenants.

---

## 7. Day-2 operations — adding / editing / removing metrics & dashboards

The skeleton must stay correct as the inventory changes. Findings and required work:

### 7.1 How an add flows today (source of truth)
- **Source of truth = `docs/frd-docs/chart-registry.json`.** A metric/dashboard exists to the graph
  only as a registry entry (the prepass validates against `all_dashboard_ids()`, `cli/kg.py:429`). So
  "add a metric/dashboard" = add a registry entry, then ingest.
- **Add commands already exist:** `kg ingest-dashboard <id> --with-causal` (`cli/kg.py:418`) and
  `kg ingest-all` (`cli/kg.py:448`); `--with-causal` calls `_run_incremental_causal(db)` so
  deterministic edges (re)build immediately. Single-node adds also go through MCP `create_metric_node`
  → the same arbitration writer.
- **Idempotent by construction:** `upsert_node`/`upsert_edge` `MERGE` (`arbitration.py:207-216`,
  `ON CREATE/ON MATCH SET`), so re-running never duplicates. `missing_endpoint` (`arbitration.py:195`)
  means an edge is skipped (not dangling) until both nodes exist — re-run completes it
  ("nodes before edges").

### 7.2 How edges & metrics react to an add (concrete)
When metric **X** is added and the deterministic pass re-runs (it always scans **all** metrics —
`formula_edges(metrics,…)`/`rollup_edges(metrics)`; only the LLM linker is incremental,
`causal.py:1181-1203`):
1. **X's own edges** are created — formula/identity/funnel/alias resolved **within X's scope** (A1),
   so a new `google-search` metric attaches only to `google-search` siblings.
2. **X joins its concept roll-up group.** If X is a coarser scope than the current target,
   `rollup_edges` picks a **new target** (`max(group, key=_scope_rank)`, `causal.py:430`) — and the
   **old edges to the previous target become stale** (see 7.3).
3. **X becomes available as a component** for others' formulas/funnels; their edges (re)build on the
   same full recompute.

### 7.3 The real issue: stale edges are never removed *(new required work)*
`upsert_edge` is **MERGE-only — no delete/supersede** anywhere. `harness/kg/reconcile.py` only merges
duplicate **nodes** (`merge_duplicates`, APOC) and prunes empty **spine** (`prune_empty_spine`) —
neither touches metric↔metric edges. So **editing a formula, renaming a metric, or changing a roll-up
target leaves the old, now-wrong edge in the graph.** Fix:

- Add **`reconcile_edges(db, source_kinds, computed_set)`** in `harness/kg/reconcile.py`: for the
  deterministic `source_kind`s (`formula_parse`, `scope_rollup`, plus new `identity`/`funnel`/`alias`
  kinds), delete any `CORRELATES_WITH` edge whose `(from, to, relation, source_kind)` is **not** in
  the freshly-computed set for the metrics in scope. This makes the skeleton a **pure function of the
  current registry** (re-runs converge; edits self-heal). Apply only to deterministic kinds — never to
  reviewed `INFLUENCES` (human-gated).
- Wire as a final step of `run_causal` behind a `--reconcile` flag (default on for full runs, off for
  `--platform`/incremental subsets to avoid cross-scope deletes).
- Log every deleted edge (mirror the `corr_skipped` discipline — no silent drops).

### 7.4 Making adds easier (small ergonomics, reuse what exists)
- **CSV round-trip:** pair the Phase-A `skeleton_export.<tenant>.csv` with a thin
  `kg import-skeleton <csv>` that turns rows into the same edge proposals — a domain expert can add
  relationships by editing a CSV, reviewed through the normal queue (the user's "CSV ingestion" ask,
  kept governance-safe).
- **`--dry-run`** on `run-causal`: print the per-`relation` edge diff (added / removed / unchanged)
  vs. the live graph **without** writing proposals.
- **One-liner add:** `kg ingest-dashboard <id> --with-causal --reconcile` = "add a dashboard and
  rebuild its edges correctly".

### 7.5 New-tenant bootstrap (unchanged, documented)
`kg schema-init` → `kg bootstrap-spine` → `kg prepass` → `kg ingest-all --with-causal` → review →
`kg apply`. The deterministic skeleton is identical in shape across tenants; only the `INFLUENCES`
layer (Phase B/C) diverges per tenant — exactly the seeds-vs-clothing requirement.

---

## 8. Migration & consolidation blast radius (one-time)

Collapsing 5 edge types → 2 affects existing data and the canvas:
- **Existing edges** (`DECOMPOSES_INTO`, `ROLLS_UP_TO`, old statistical `CORRELATES_WITH`, `CAUSES`)
  must be migrated. Cleanest: a one-time Cypher migration (rename → `CORRELATES_WITH`/`INFLUENCES` +
  set `relation`) **or** rebuild the skeleton on a fresh graph via §7.5 (preferred for the pilot).
  Provide `kg migrate-edges` for non-rebuildable graphs.
- **Canvas (front-end) coupling — must update:** `app/kg-canvas/src/lib/graphLayout.ts:258`
  (`HUB_REL = "DECOMPOSES_INTO"` → key off `relation`) and `app/kg-canvas/src/lib/graphTheme.ts:76-116`
  (edge list + color/label map — collapse to the 2 types, color/label by `relation`).
  `harness/api/server.py` references are comments/flags only (no change). This is the entire UI blast
  radius — two files.

---

## 9. Critical files

| File | Change |
|---|---|
| `harness/ingest/causal.py` | scope-aware `resolve()`/`_best()` (216-262); `_is_additive`; new `identity_edges`/`funnel_edges`/`alias_edges`; additive-aware `rollup_edges` (411); `IDENTITIES`/`FUNNEL_FLOW` consts; emit `CORRELATES_WITH`+`relation`; reclass seed corrs → `INFLUENCES`; `run_causal` wiring + `_write_skeleton_export` (1153-1295) |
| `harness/kg/models.py` | `EDGE_TYPES` consolidation (1013); allow `relation`/`symmetric` edge props |
| `harness/kg/arbitration.py` / `schema.py` | accept consolidated rel types; drop removed-type constraints |
| `harness/ingest/causal.py` `_ALIAS_GROUPS` (147-159) | bridge clean slugs (identity/funnel/operand vocab) → real chart-ids (blocker B1) |
| `harness/kg/reconcile.py` | **new** `reconcile_edges()` — delete stale deterministic edges on re-run (§7.3) |
| `harness/cli/kg.py` | `--reconcile`/`--dry-run` on `run-causal`; new `import-skeleton` + `migrate-edges` (§7.4, §8) |
| `harness/tests/test_causal.py` | scope-aware resolve test; additivity test; funnel/identity/alias emit tests; updated edge-type assertions |
| `.claude/commands/run-causal.md` | 2-type taxonomy + CSV export + `--reconcile`/`--dry-run` wording |
| `app/kg-canvas/src/lib/graphLayout.ts` (258) + `graphTheme.ts` (76-116) | key layout/theme off the 2 types + `relation` (§8) |

**Reused (do not rebuild):** `_edge_proposal()`, `ConceptIndex`, `_platform_of`, `_ALIAS_GROUPS`,
`beta_confidence`, the proposal → review → arbitration path, and
`docs/causal-edge-coverage-plan-claude.md` for Phase B.

---

## 10. Verification (end-to-end)

1. **Unit:** `uv run pytest harness/tests/test_causal.py` green, incl. new scope/additivity/funnel/
   identity/alias tests; existing suite still passes.
2. **Scope correctness (headline):** in `data/skeleton_export.<tenant>.csv`, every
   `relation: formula | identity` edge has `from_scope == to_scope` (no cross-channel decomposition;
   prove a Google-Search metric never points at a YouTube/blended component).
3. **Additivity:** no `relation: crossproduct` edge for a ratio base (roas/cpc/cvr/ctr/aov); ratio
   roll-ups carry `aggregation_method != sum`.
4. **Coverage vs. determinism:** `uv run kg run-causal` summary shows non-zero `formula`, `identity`,
   `rollup`, `crossproduct`, `funnel`, `alias` counts and **zero** non-deterministic edges in the
   skeleton run (statistical/causal only under `--llm`).
5. **No invented endpoints:** every skeleton edge resolves both endpoints to live `metric_uid`s;
   unresolved operands logged (mirror `corr_skipped`), never dropped silently.
6. **Graph apply:** `uv run kg apply --run <id>` then `uv run kg status` shows the 2 consolidated
   metric-edge types only; spot-check the canvas for a metric's decomposition.
7. **Day-2 self-heal:** add a metric / change a formula / add a coarser-scope metric to a concept
   group, re-run `kg run-causal --reconcile`; confirm new edges appear, **old/now-wrong edges are
   gone**, counts converge, and a second re-run is a no-op. Confirm `import-skeleton <csv>`
   round-trips an edited export and `--dry-run` shows the diff without writing.
8. **Migration:** after `migrate-edges` (or fresh rebuild) only the 2 metric-edge types exist; the
   canvas renders decomposition/rollup/funnel/alias by `relation` with no broken styling.

---

## 11. Out of scope (now)

Statistical discovery code in dc-kg (file-handoff only, per the integration doc); per-tenant seasonal
edge *generation* (design-only Phase C); the RBAC `Role` layer (M4); porting `knowledgeGraph`'s heavy
deps (tigramite/statsmodels) into dc-kg's runtime; the live-series alias equality test.

---

*Source of truth for this spec: the approved plan. Implementation has not started — code changes
begin only on explicit go-ahead.*
