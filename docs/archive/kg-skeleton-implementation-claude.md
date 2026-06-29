# KG Skeleton Implementation Plan (Claude — Merged v2)

Date: 2026-06-21
Scope: graph-only V1 skeleton for `dc-kg` (the FRD's Causal Graph / Layer 1)
Status: interview-approved design + implementation spec. **No code is written yet.**

> **What this is.** The reconciled merge of `docs/kg-skeleton-implementation-claude.md` (prior),
> `docs/kg-skeleton-merged-implementation-codex.md`, and the **ThoughtWire FRD Part V**, updated with
> the latest interview decisions. **Supersedes** all earlier skeleton drafts (incl. the root
> `metric-skeleton-implementation-claude.md`). Companions: `INTEGRATION-ANALYSIS-claude.md`,
> `docs/causal-edge-coverage-plan-claude.md` (cross-domain INFLUENCES).

---

## 1. Goal

Build a deterministic **Causal Graph skeleton** in `dc-kg` connecting **metric → metric** (and
metric → dashboard / domain / product / UI component), correct by construction, at confidence 1.0 —
without inventing tenant-specific causal behaviour. The skeleton is the FRD's "subconscious / X-Y
plane": the body an agent wakes into. Tenant-specific statistical/causal relationships (seasonality,
sales↔inventory) attach later as a separate, evidence-backed layer.

Motivating correctness requirement: **Google Search revenue must decompose into Google Search
components — never into Google YouTube ROAS.**

---

## 2. Locked decisions (current interview)

| Topic | Decision |
|---|---|
| **Metric model** | **One tier, one meaningful id per metric**: `metric:<scope>:<base>` (e.g. `metric:google-search:roas`). The word "atomic" is dropped — it just meant "one node per real measurable quantity." Google-Search ROAS and YouTube ROAS are **separate nodes** (different scope). |
| **Charts** | **Metrics-only** — *no* per-chart node. Each metric is `SHOWN_ON` its Dashboard and `VISUALIZES` a chart-type UIComponent; per-chart prose (how-to-read / narration) attaches to the Dashboard. A derived metric carries its own `formula_text`. |
| **Edge model** | **2 types.** `CORRELATES_WITH` (deterministic structural) + `INFLUENCES` (evidence/causal). A `relation` property carries sub-kinds. Retire `DECOMPOSES_INTO`, `ROLLS_UP_TO`, `CAUSES`. |
| **Auto-apply** | Auto-apply **formula-derived `CORRELATES_WITH {relation: formula}`** through arbitration. Everything else (identity, rollup, crossproduct, funnel, all `INFLUENCES`) is **review-only**. |
| **Aliases** | **No alias edges.** The single meaningful id is the identity; `synonyms[]` is an optional resolution hint only. |
| **Start state** | **Blank canvas.** Back up the current Neo4j DB, stand up a fresh instance, build the final taxonomy directly — **no in-place migration.** |
| **Stale edges** | **Deprecate, never delete** (FR-ING-010 / FR-CG-010). |
| **UI** | **Full KG canvas redesign** (Codex Phase 5). |
| **Evidence ledger** | **Defer full storage**; keep edge props now (`confidence`/`evidence_mass`/`mechanism`/`lag`/`source`) + document the ledger contract. Build the append-only ledger when Decision-Capsule outcomes exist. |
| **Source for V1** | Authored static **input JSON/CSV snapshot files**; no BC adapter, no live API, **master-config excluded**. |
| **LLM** | Review-only candidate generation; never deterministic truth in the skeleton. |

---

## 3. FRD grounding (KG-only)

We build **only** Layer 1 (the Causal Graph / X-Y plane). The Z-axis layers (Thoughtlets,
Investigation Agent, Decision Capsules, Approval Graph, Execution, Monitoring Contracts, Learning
Memory, Context Harness) are **out of scope** — but the skeleton is the substrate they depend on.

**Followed (aligned):** single-writer arbitration (FR-CG-008), provenance/attribution (FR-CG-009,
FR-ING-006), proposals → review (FR-ING-005), `SHOWN_ON` + canonical-first identity (FR-ING-013 — the
`metric:<scope>:<base>` id satisfies this: one node per concept-scope, `SHOWN_ON` per dashboard,
namespaced so distinct same-named metrics never merge), coverage report (FR-ING-015), auto-promote the
deterministic graph (FR-ING-016 ≈ our auto-apply of formula edges).

**Added because the FRD makes them `MUST`:**

| FRD req | Addition |
|---|---|
| FR-CG-003/004 | **Temporal Causal DAG** — every `INFLUENCES` edge carries `temporal_lag` (ISO-8601, e.g. `P1D`); acyclic once time is included. |
| FR-CG-002 / FR-SCORE-001/002 | **Evidence-fold confidence** — `INFLUENCES` confidence = `α/(α+β)`, `evidence_mass = α+β` over tiered evidence (PRIOR/OBSERVATIONAL/QUASI-EXP/INTERVENTIONAL/HUMAN). Full append-only **ledger storage deferred** (contract documented). Structural `CORRELATES_WITH` is deterministic math, pinned 1.0, outside the ledger. |
| FR-SCORE-003 / FR-CG-005/006 | **Path-score traversal** — upstream/downstream rank by `Π(confidence) × lag plausibility`, returning score + cumulative lag. |
| FR-ING-010 / FR-CG-010 | **Deprecate, never delete.** |
| FR-CG-001 | Wire deterministic governance/context edges where derivable: `BELONGS_TO_DOMAIN`, `PART_OF_PRODUCT`, threshold/policy shells. |

**Deviations (deliberate, documented):**
- **FR-ING-001 (live-API-only).** We bootstrap from authored static snapshots (API not running
  locally). The input-file layout (§6) is harvester-replaceable so a live path drops in later.
- **FR-ING-014 (master-config relationships = primary edge source).** Excluded as stale; deterministic
  edges come from formulas + identities + authored model-SQL lineage instead.

---

## 4. Metric model & identity (single tier)

**One kind of `:Metric` node.** Identity = **`metric_uid = metric:<scope>:<base>`** — unique,
human-readable, stable across dashboard add/remove. Examples:

```
metric:google-search:roas      metric:google-search:spend     metric:google-search:revenue
metric:google-youtube:roas     metric:meta:cpc                metric:blended:revenue
metric:inventory:out_of_stock  metric:blended:aov
```

Identity rules:
- A metric exposed by N dashboards = **one** node + N `SHOWN_ON` edges (FR-ING-013); never duplicated
  per dashboard.
- **Never** a single global `roas`/`revenue` node; same concept across scopes stays **separate**.
  Cross-scope links are explicit edges, not silent merges.
- **No aliases as edges.** `synonyms[]` is an optional resolution hint; the meaningful id is the truth.
- Dimension-scoped metrics (device, network, campaign type, category) only when the source names the
  dimension explicitly.

`Metric` fields to add (optional props first, promote to formal fields if stable):
`source_scope`, `platform_family`, `dimension_scope`, `is_derived`, `formula_text`,
`formula_components`, plus existing `metric_base`, `aggregation`, `unit_family`, `default_direction`,
`synonyms[]`, `dashboard_ids[]` (cache from `SHOWN_ON`), `component_ids[]` (cache from `VISUALIZES`).
*(Dropped vs the prior draft: `is_atomic`, `parent_chart_metric_uid` — no second tier.)*

**Metric source change.** The skeleton builder becomes the metric producer: it extracts
`metric:<scope>:<base>` metrics from chart formulas + authored model-SQL lineage. `prepass.py`
continues to provide Dashboards + UIComponents and the `SHOWN_ON`/`VISUALIZES` links; it no longer
creates one coarse metric per chart-registry entry.

---

## 5. Edge model (2 types)

### 5.1 `CORRELATES_WITH` — deterministic structural
Directed `derived_metric → input_metric`. One Neo4j type; sub-kinds via `relation`:

| `relation` | Source | Confidence | Apply | Notes |
|---|---|---|---|---|
| `formula` | parsed `formula_text` RHS, **same-scope** resolution | 1.0 | **auto** | the core skeleton |
| `identity` | canonical IDENTITIES when `formula_text` is null | 1.0 | review | `roas=convValue/spend`, … |
| `rollup` | finer→coarser scope, same concept, **ratio-safe** | 1.0 | review | `aggregation_method` ≠ "sum" for ratios |
| `crossproduct` | channel→blended, **additive bases only** | 0.9 | review | never sums a ratio |
| `funnel` | bounded stage→stage templates, same scope | 0.85 | review | `reach→impressions→clicks→…` |

### 5.2 `INFLUENCES` — evidence/causal
Directed cause→effect; the upstream/downstream layer. Sub-kinds via `relation`: `causal` (LLM judge +
refuter + Beta), `cross_domain` (curated concept rules), `statistical` (Phase 6 discovery feed),
`seasonal` (tenant-profile, later). Each edge: `{confidence=α/(α+β), evidence_mass=α+β, temporal_lag,
relation, mechanism, source_kind, provenance[], review_state}`. **Always review-only**; confidence is
only updatable via the governed evidence fold.

### 5.3 Naming caveat (acknowledged)
This names the **deterministic structural** edge `CORRELATES_WITH` (your choice). Conventionally
"correlation" means statistical co-movement — so in this model, statistical co-movement is **not**
`CORRELATES_WITH`; it is `INFLUENCES {relation: statistical}` (a review-only candidate). Implementers
must use the `relation` value, not the type name, to judge determinism.

### 5.4 Edge vocabulary change (`models.py:1013 EDGE_TYPES`)
Keep `CORRELATES_WITH`, `INFLUENCES`. Retire `DECOMPOSES_INTO`, `ROLLS_UP_TO`, `CAUSES` from the
metric-edge set (`CAUSES` becomes a manual promotion only, if ever). Allow `relation`, `temporal_lag`,
`evidence_mass` edge props. Spine/surface edges unchanged (`SHOWN_ON`, `VISUALIZES`,
`BELONGS_TO_DOMAIN`, `PART_OF_PRODUCT`, …).

---

## 6. Input files (authored snapshots — the foundation)

```
data/skeleton/inputs/
  metrics.<tenant>.csv          # one row per metric (metric:<scope>:<base>) — the node seed
  model_sql.<tenant>.json       # dbt/model SQL as JSON: output metric, expr, components (lineage)
  endpoints.<tenant>.json       # OpenAPI business endpoints (card/series) per metric (provenance)
harness/seed/
  identities.json               # canonical IDENTITIES: metric_base -> (numerators, denominators)
  funnel_flow.json              # bounded funnel stage pairs
  concept_causal_rules.json     # curated cross-domain INFLUENCES rules (§12)
  tenants/<tenant>/skeleton_overrides.json   # tenant overrides (§10)
```

`metrics.<tenant>.csv` columns: `metric_uid, source_scope, metric_base, aggregation, is_derived,
unit_family, default_direction, formula, formula_components, platform_family, dimension_scope, domain,
product, source, dashboard_ids`.

`model_sql.<tenant>.json` (replaces a BC adapter — authored, not scraped):
```jsonc
{ "models": [
  { "model": "mart_google_search_daily", "scope": "google-search",
    "metrics": [
      { "metric_base": "roas", "expr": "case when spend>0 then conversion_value/spend end",
        "components": ["conversion_value","spend"], "is_derived": true },
      { "metric_base": "spend", "expr": "sum(cost_micros)/1e6", "components": [], "is_derived": false }
    ] } ] }
```

For the **basic skeleton** author a representative set (google-search, google-youtube, meta, blended)
that proves scope-correctness and decomposition, then expand. `identities.json` / `funnel_flow.json`
are ported from `knowledgeGraph/tools/build_structural_edges.py` / `build_compositional_edges.py`.

---

## 7. Implementation phases

### Phase 0 — Baseline & backup (blank canvas)
- `uv run pytest harness/tests` green; export current edge/metric counts to
  `data/skeleton/baseline_*.{csv,json}` for reference.
- **Back up the current Neo4j** (`neo4j-admin database dump` or APOC export) and provision the **fresh
  instance** the skeleton will build into. No behaviour change yet.

### Phase 1 — Metric builder (`harness/ingest/skeleton.py`, pure)
Extract `metric:<scope>:<base>` metrics from chart formulas + `model_sql`; emit metric node drafts +
**formula `CORRELATES_WITH`** edges; **auto-apply formula edges** via arbitration; the rest as
proposals. Functions: `load_metric_sources`, `split_formula_statements`, `normalize_metric_concept`,
`is_metric_lhs` (filters status/quadrant/recommendation labels), `extract_rhs_operands`,
`derive_scope`, `build_metric_drafts`, `build_formula_edges`, `write_skeleton_artifacts`.
CLI: `kg build-skeleton --tenant <t> [--dry-run|--write-csv|--apply-safe]`.

### Phase 2 — Scope-safe deterministic edge builders (`causal.py`)
- `ConceptIndex.resolve(concept, prefer_scope=None, prefer_platform=None)` + `_best(...)`: exact
  `scope_key` → same platform → broadest fallback. Tests for Google-Search vs YouTube vs blended.
- `identity_edges()` (canonical IDENTITIES, same-scope, review-only).
- `_is_additive()`: additive → `CORRELATES_WITH {relation: crossproduct}`; ratios →
  `{relation: rollup}` with `aggregation_method != sum`; both review-only.
- `funnel_edges()` (bounded templates, same scope, review-only).
- Expand `_ALIAS_GROUPS` for resolution; **no alias edges**; store `synonyms[]` on the metric.

### Phase 3 — Curated cross-domain `INFLUENCES` (review-only)
`harness/seed/concept_causal_rules.json` → resolve endpoints via `ConceptIndex` → evidence-text check
→ LLM judge/refuter → fold into edge props → review-only proposal. `constraint`-role metrics are a
cause **only** through a rule that allows it. Reason codes logged (§12).

### Phase 4 — Stale deterministic edge deprecation (`reconcile_edges()`)
Compute the deterministic edge set per run; mark live deterministic edges absent from it as
`status: deprecated` (+ `deprecated_at`/`deprecated_by_run`/`reason`). **Never delete; never touch
reviewed `INFLUENCES`.** Emit `edge_diff.<t>.<run>.json`. CLI `kg run-causal --reconcile|--dry-run`.

### Phase 5 — KG canvas full redesign (§15)

### Phase 6 — Statistical evidence feed (deferred)
`kg import-discovered-edges` → `INFLUENCES {relation: statistical}` from `discovered_edges.<t>.csv`,
preserving lag/p-value/correlation/method/FDR/sample size. Never auto-promoted.

### Phase 7 — Live SourceProfile ingestion (deferred)
OpenAPI acquisition → content hash → family classification → SourceProfile → human review →
deterministic harvester → drift diff → multi-source registry. Replaces the input-file shim.

---

## 8. Write & governance policy

**Auto-applied (conf 1.0, via arbitration):** `CORRELATES_WITH {relation: formula}` + the existing
exact surface links (`SHOWN_ON`, `VISUALIZES` for known chart types), inherited `BELONGS_TO_DOMAIN` /
`PART_OF_PRODUCT`.

**Review-only:** `CORRELATES_WITH {relation: identity|rollup|crossproduct|funnel}`, all `INFLUENCES`,
prose/LLM candidates, statistical edges.

**Causal claim discipline:** formula/rollup edges are mathematics/aggregation, **not** causality;
`INFLUENCES` is the only causal candidate type; `CAUSES` is never auto-created.

**Evidence ledger (deferred storage, contract now):** all review-only `INFLUENCES`/statistical edges
must carry `confidence`, `evidence_mass`, `mechanism`, `lag`, `source_kind`. Confidence folds via
`beta_confidence` (`causal.py:539`). The full append-only ledger (`{edge, tier, direction, weight,
attribution, ts}`) is specified but **implemented later** when Decision-Capsule outcomes exist.

---

## 9. Day-2 — add / edit / remove (deprecate, never delete)

- **Add** → author input row(s), re-run `build-skeleton`. Idempotent MERGE (`arbitration.py:207`); a
  metric is one node regardless of how many dashboards show it.
- **Edit/remove** → `reconcile_edges()` marks superseded deterministic edges `DEPRECATED`
  (Phase 4) — not deleted. Reviewed `INFLUENCES` are never auto-deprecated.
- **How edges react to an add:** the new metric resolves its own same-scope formula edges; joins its
  concept rollup group (a coarser new node re-points the rollup target — the old edge is *deprecated*);
  becomes an operand for others on the next full recompute.

---

## 10. Tenant overrides

`harness/seed/tenants/<tenant>/skeleton_overrides.json` — global rules + per-tenant deltas: alias
groups (resolution only), source-scope mapping, platform-family mapping, domain/product overrides,
formula-parser exclusions, concept normalisation, safe-rule toggles, (future) approved causal rules.
A new tenant with different domains/relationships is supported without forking code; seasonality
(agriculture vs clothing) becomes tenant evidence/rules **later**, never global V1 edges.

---

## 11. Traversal (FR-CG-005/006, FR-SCORE-003)

Upstream and downstream queries traverse **both** `CORRELATES_WITH` and `INFLUENCES` (both directed),
ranking paths by `Π(confidence) × lag_plausibility`, returning score + cumulative lag. Exposed via the
MCP graph server / API: `GET /api/traverse/upstream` and `/downstream`.

---

## 12. Curated cross-domain rules + reason codes

`concept_causal_rules.json` rule shape:
```jsonc
{ "rule_id": "deliverability_to_campaign_engagement",
  "source_concepts": ["deliverability","bounce_rate"], "target_concepts": ["open_rate","click_rate"],
  "relation": "cross_domain", "direction": "source_to_target",
  "required_source_keywords": ["deliverability","bounce","inbox"],
  "required_target_keywords": ["open","click","engagement"],
  "mechanism_template": "Deliverability constrains whether recipients can open/click campaigns.",
  "allow_constraint_source": true, "review_required": true }
```
Flow: resolve source+target (ConceptIndex + aliases) → require evidence text (formula/title/
explanation/how-to-read/decisions/narration/category) → emit only if both resolve and evidence passes
→ LLM judge/refuter → review-only `INFLUENCES`. **LLM never invents endpoints.** Rejected candidates
log a reason code: `missing_source_endpoint`, `missing_target_endpoint`,
`missing_required_source_evidence`, `missing_required_target_evidence`, `constraint_source_rejected`,
`judge_rejected`, `refuter_rejected`.

---

## 13. Artifacts & coverage report (FR-ING-015)

```
data/skeleton/metric_registry.<tenant>.csv
data/skeleton/deterministic_edges.<tenant>.csv
data/skeleton/unresolved_operands.<tenant>.csv
data/skeleton/filtered_terms.<tenant>.csv
data/skeleton/rejected_candidates.<tenant>.csv     # with reason codes
data/skeleton/coverage_report.<tenant>.json
data/skeleton/skeleton_export.<tenant>.csv          # flat inspectable edge inventory
```
Coverage report: source files + hashes, dashboards/charts scanned, formulas parsed, metrics created,
auto-applied formula edges, review-only edges, unresolved operands, filtered LHS terms, rejected
candidates by reason, **deprecated** links, invalid payloads, missing endpoints.

---

## 14. Backup + fresh-instance bootstrap (blank canvas)

No in-place migration. Sequence:
1. **Back up** current Neo4j (`neo4j-admin database dump` / APOC export) — preserve the old graph.
2. Provision the **fresh instance**; point `dc-kg` config (`KG_NEO4J_*`) at it.
3. `kg schema-init` → `kg bootstrap-spine` → `kg prepass` (dashboards + components) →
   `kg build-skeleton --tenant <t>` (metrics + formula edges, auto-safe) →
   `kg run-causal` (review-only structural + cross-domain candidates) → review → `kg apply`.
The deterministic skeleton is identical in shape across tenants; only the `INFLUENCES` layer diverges
per tenant.

---

## 15. UI — full KG canvas redesign (`app/kg-canvas`)

No Thoughtlet/Decision-Capsule UI. Views/components:
- **Graph overview** — metrics + dashboards/components; toggle metric visibility; distinguish
  `CORRELATES_WITH` vs `INFLUENCES` (and `relation` sub-kind) by colour.
- **Metric detail** — id, formula, operands, synonyms, source endpoints, domain/product/platform,
  upstream/downstream summaries.
- **Edge detail** — type, relation, confidence, evidence_mass, lag, mechanism, source_kind,
  review_state, status (active/deprecated), deprecation metadata.
- **Review queue** — separated by class (auto-safe formula / review structural / influence
  candidates); approve/reject/edit; shows *why* a candidate exists.
- **Edge diff** — added / unchanged / deprecated / skipped (with reason codes).
- **Filter toolbar** — edge type, relation, review_state, active/deprecated, confidence range,
  source_kind, show/hide derived metrics.
- **Traversal mode** — upstream causes / downstream blast radius / structural decomposition / rollup.

Files: `src/lib/{api,graphTheme,graphLayout}.ts`, `src/store.ts`,
`src/components/{CanvasView,NodeDetail,ReviewQueue,Toolbar}.tsx`, new
`src/components/{EdgeDetail,EdgeDiffReview}.tsx`. Backend: relation/status/deprecation in the graph
payload; coverage + edge-diff endpoints; traversal endpoints (§11). Playwright UI checks.

---

## 16. Critical files

| File | Change |
|---|---|
| `harness/ingest/skeleton.py` | **new** — Phase-1 metric builder + formula edges |
| `harness/ingest/causal.py` | scope-aware `resolve(prefer_scope)`; `identity_edges`; `_is_additive`; `funnel_edges`; curated-rule candidates; `relation` props; emit 2-type edges |
| `harness/kg/models.py` | `Metric` scope fields (§4); `EDGE_TYPES` → 2-type (§5.4); `relation`/`temporal_lag`/`evidence_mass` props |
| `harness/kg/reconcile.py` | **new** `reconcile_edges()` — deprecate-not-delete (§9) |
| `harness/seed/{identities,funnel_flow,concept_causal_rules}.json` + `tenants/<t>/skeleton_overrides.json` | **new** seeds/overrides |
| `data/skeleton/inputs/*` | **new** authored snapshots (§6) |
| `harness/cli/kg.py` | `build-skeleton`; `run-causal --dry-run/--reconcile`; traversal cmds |
| `harness/api/server.py` / `harness/mcp/graph_server.py` | relation/status payload; coverage + edge-diff + traversal endpoints |
| `app/kg-canvas/*` | full redesign (§15) |
| `harness/tests/{test_skeleton,test_causal}.py` | new/updated tests (§17) |

**Reused:** `ConceptIndex`, `_edge_proposal`, `_platform_of`, `beta_confidence`, arbitration +
proposal + review path, `docs/causal-edge-coverage-plan-claude.md`.

---

## 17. Test plan

Unit: formula splitting & multi-line; LHS metric filtering; same-scope RHS operand resolution; scoped
identity (`metric:<scope>:<base>`); **Google-Search not linking to YouTube/blended operands**; formula
edges; identity fallback; additivity (no ratio crossproduct, no ratio sum); unresolved-operand
logging; alias resolution metadata (no alias edge); curated-rule resolution; `constraint` only via
rules; LLM candidate endpoint validation; rejection reason codes; stale-edge deprecation diff;
evidence-fold confidence. Integration: `build-skeleton --dry-run/--write-csv/--apply-safe` idempotency;
deprecate-on-edit; arbitration-only mutation. UI: Playwright (§15). Run: `uv run pytest harness/tests`.

---

## 18. Acceptance criteria

- metrics exist as `metric:<scope>:<base>` with one unique id each; no per-chart metric nodes;
- formula edges are scope-correct (Google-Search never decomposes into YouTube/blended);
- ratios are never summed in crossproduct rollups;
- formula `CORRELATES_WITH` auto-applies via arbitration; all other classes require review;
- unresolved operands and rejected candidates are reported, not silently dropped;
- cross-domain `INFLUENCES` exist only from curated rules that passed judge/refuter, review-only;
- `constraint` sources used only where a rule permits;
- stale deterministic edges are deprecated/versioned, never deleted;
- `confidence`/`evidence_mass`/`lag`/`mechanism`/`source` present where required;
- the redesigned canvas inspects metrics, edge relations, review reasons, and edge diffs;
- no Thoughtlet/Decision-Capsule implementation.

---

## 19. Verification (end-to-end)

1. Unit + integration + UI checks green.
2. **Scope correctness (headline):** every `CORRELATES_WITH {relation: formula|identity}` in
   `skeleton_export.<t>.csv` has `from_scope == to_scope`.
3. **Additivity:** no `crossproduct` for a ratio base; ratio rollups carry `aggregation_method != sum`.
4. **2 types only:** `kg status` shows just `CORRELATES_WITH` + `INFLUENCES` between metrics.
5. **Auto-apply scope:** only `relation: formula` edges land without review; the rest sit in the queue.
6. **Day-2 self-heal:** edit a formula / add a coarser metric, re-run; superseded edges become
   `DEPRECATED` (not gone); a second run is a no-op.
7. **Traversal:** upstream/downstream return path-score + cumulative lag.
8. **Coverage report** present with deprecated/skip/missing/reason-code tallies.

---

## 20. Out of scope / deferred

Full append-only evidence ledger storage; `knowledgeGraph` statistical importer (Phase 6); live
SourceProfile ingestion (Phase 7); master-config relationship endpoint; Thoughtlets, Investigation
Agent, Decision Capsules, Approval Graph, Execution, Monitoring Contracts, Learning Memory, Context
Harness; RBAC `Role` layer; heavy statistical deps in `dc-kg` runtime; what-if simulation (FR-CG-007).
