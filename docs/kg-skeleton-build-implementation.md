# KG Skeleton Build — Implementation Reference

Date: 2026-06-23
Status: implemented & verified (142 backend tests green, `kg-canvas` builds clean).
Companion plan: `~/.claude/plans/can-u-compare-the-cozy-parasol.md` (interview-approved design).
Supersedes the open questions in `docs/kg-skeleton-implementation-claude.md` and
`docs/kg-skeleton-merged-implementation-codex.md`.

This document explains **what was built and why**, so the system can be picked up later
without re-deriving the decisions. It reflects the actual code, not the plan's aspirations.

---

## 1. What this is

A deterministic **Causal-Graph skeleton**: meaningful, scope-correct `Metric` nodes — each with
**one clean core formula** — connected by two metric→metric edge types, correct by construction,
with an LLM layer that only handles the genuinely-semantic residual and is always human-gated.

It extends the existing M1–M3 pipeline (prepass → proposer → apply → arbitration → causal) rather
than replacing it. The new metric source is the **chart-registry + OpenAPI dynamic-metric inventory,
enriched by the BC_2 dbt seeds**; the old "one coarse Metric per chart-registry entry" path is
superseded by `harness/ingest/skeleton.py`.

## 2. The problem it solves

The source `chart-registry.json` `formula` field **bundles multiple metric formulas + status/threshold
lines into one string** (e.g. `data-quality:spend_comparison` = `Google Spend = … / Meta Spend = … /
Total Spend = Google + Meta / Status = …`). Treating one chart entry as one metric with that blob as
its formula produced nonsensical nodes and edges. Three things had to change:

1. **Composite tables are not metrics** — they decompose into the scoped component metrics they show.
2. **One clean formula per metric** — sourced from the BC_2 dbt seeds (the authoritative clean SQL),
   falling back to a single split statement from the registry, then an authored override, then null.
3. **Scope-correct edges** — `metric:google-search:roas` must never decompose into YouTube/blended.

## 3. Edge model (the core decision)

Metric→metric edges are exactly **two Neo4j types**, with a `relation` property carrying the sub-kind
(`ROLLS_UP_TO` / `CORRELATES_WITH` / `CAUSES` were retired into `relation` values):

| Type | `relation` values | meaning | auto-apply? |
|---|---|---|---|
| `DECOMPOSES_INTO` | `formula` | parsed same-scope formula (`roas = revenue/spend`) | **auto-safe** |
| | `component` | exact chart/formula containment | **auto-safe** |
| | `identity` | canonical fallback (`cpc → spend, clicks`) | review |
| | `crossproduct` | additive channel → blended (currency/count) | review |
| | `rollup` | ratio re-aggregation (`aggregation_method=reaggregate`, never `sum`) | review |
| | `funnel` | bounded same-scope stage template | review |
| `INFLUENCES` | `curated_rule` | curated cross-domain mechanism | review |
| | `llm_verified` | LLM judge+refuter accepted | review |
| | `statistical` | measured/imported association | review |
| | `statistical_candidate` | future discovery feed | review |
| | `promoted` | future human/learning promotion | review |

- **Scope gates:** `formula`/`identity` are a **hard same-scope gate** (from_scope == to_scope). The
  only intentionally cross-scope relations are `crossproduct`/`rollup` (channel → blended), additive
  bases only — **a ratio is never summed**.
- Vocab + validation: `harness/kg/models.py` `EDGE_TYPES` (2 metric types) + `DECOMPOSES_RELATIONS` /
  `INFLUENCES_RELATIONS` frozensets; `harness/kg/arbitration.py:_validate_relation` rejects an
  out-of-vocab `relation` before any DB write.
- **Scoring at creation:** `harness/ingest/edge_scoring.py:score_edge("<TYPE>:<relation>", …)` →
  `EdgeScore(confidence, evidence_mass, scoring_policy, review, deterministic)`. The `review` flag is
  the single gate the `--apply-safe` / auto-approve paths consult.

## 4. Metric model & identity

`metric_uid = metric:<scope_key>:<metric_base>` (unique; one node per real measurable quantity, N
`SHOWN_ON` edges across dashboards). Scope comes from the registry `scope` field (19 values; cleanly
separates `google_search`/`google_youtube`/`google_shopping`/`google_pmax`, `meta_*`, etc.). The
`Metric` model already carried every needed field; the build added **one**:
`Metric.source_refs: list[str] | None` — a provenance trail
(`openapi:<slug>`, `registry:<canonical>`, `bc2:coded:AD_013`, `bc2:ontology:roas`, `override:<id>`).
Aliases are metadata only (`aliases[]`/`synonyms[]`) — **no alias edges**.

Composite/table entries (chart_type `table`, ranking ids, or a bundled multi-LHS formula) are NOT
nodes; `skeleton.decompose` splits them into scoped component metrics and records the surface in
`composites.<tenant>.csv`. The `spend_comparison` example correctly yields
`metric:google:spend` + `metric:meta:spend` + `metric:blended:spend`.

## 5. Formula resolution → canonical metric registry

Each metric gets ONE `core_formula` by priority (`skeleton.resolve_core_formula`):
1. **authored override** — `harness/seed/formula_overrides.<tenant>.json`
2. **BC_2 dbt seed** — clean SQL from `seed_config_metrics.csv` / `seed_config_ontology_metrics.csv`
   (catalog keyed by normalized name **and** slug id, so a base like `spend` hits the clean formula)
3. **registry split** — the single statement whose LHS matches this metric's base
4. **`null`** (residual for the LLM pass)

Output artifact `data/skeleton/canonical_metric_registry.<tenant>.json` (keyed by `metric_uid`):
`{core_formula, formula_source, scope_key, metric_base, is_derived, dashboard_ids, source_refs}`.
Regenerated deterministically each run. The experimental `metrics_catalog.json` /
`formula_reconciliation.json` in BC_2 are **hints only**, not authoritative.

Representative output (885 metrics): `google_search:roas = Revenue / Ad_Spend` (bc2_seed),
`google_search:cpc = SUM(SPEND)/SUM(CLICKS)` (registry_split), `blended:spend = SUM(cost_micros)/1000000`
(bc2_seed), `customer:aov = Total_Revenue/Total_Orders` (bc2_seed).

## 6. Two-pass build + race / missing-operand handling

The build is **two-pass**: materialize ALL metric nodes first, then resolve operands against the
complete node set (no forward-reference race; the LLM gets the full allowlist). The MERGE writer
returns `missing_endpoint` rather than crashing. A genuinely-missing operand is logged to
`unresolved_operands` and self-heals on the next recompute; a curated `hub_allowlist`
(`harness/seed/skeleton_overrides.<tenant>.json`) marks CEO/executive aggregates that may get a
review-only stub so top-level metrics always link.

## 7. LLM layer (opt-in, review-only)

- **Deterministic (no LLM):** simple same-scope `formula` + `identity` + additive `crossproduct`/
  `rollup` + same-scope `funnel`.
- **LLM extractor (residual):** metrics whose formula didn't resolve deterministically.
- **Curated cross-domain `INFLUENCES`:** `harness/seed/concept_causal_rules.json` →
  `causal.curated_influence_candidates` (resolve endpoints, require evidence keywords, constraint-role
  gate, reason codes) → `causal.run_curated_influences` (judge + refuter + `beta_confidence` fold).
- **Judge/refuter runs on all LLM-proposed edges**; determinism guards: temp 0, self-consistency,
  operand allowlist, review-freeze (an approved edge is never auto-touched by reconcile).

## 8. Source inputs & roles

| Source | Role |
|---|---|
| `docs/frd-docs/openapi.json` (== BC_2, hash-verified) | endpoint inventory, card-vs-chart, exclusions, "Available metrics" lists |
| `docs/frd-docs/chart-registry.json` (== BC_2) | scope, concept, notes, human formula narration |
| `BC_2/dbt/seeds/seed_config_metrics.csv` | **authoritative clean SQL formulas** (coded ids) |
| `BC_2/dbt/seeds/seed_config_ontology_metrics.csv` | `formula_expression`, dimensions, dashboards, thresholds (slug ids) |
| `BC_2/dbt/seeds/seed_config_chart_metric_mapping.csv` | chart↔metric mapping (`exact_id` trustworthy) |
| `BC_2/dbt/seeds/seed_config_metric_relationships.csv` | edge candidates (component/derived/correlated/impacts/leads_to/inverse) |
| `BC_2/dbt/seeds/seed_config_ontology_causal_edges.csv` | edge candidates (`computes`→DECOMPOSES, `causes`→INFLUENCES) — **validate every row** |
| `harness/seed/{identities,funnel_flow,concept_causal_rules,formula_overrides.<t>,skeleton_overrides.<t>}.json` | seeds/overrides |

BC_2 has two disjoint id namespaces (coded `AD_013` vs slug `roas`, ~0 overlap) reconciled by
name/formula. BC_2 causal rows contain SQL-token noise (`select`, `from`, `sum`…) — rejected by
`bc2_snapshot.validate_bc2_relationship_rows` with reason codes (36 sql_token + 2 self_loop of 465).

## 9. Module reference (new + changed)

**New modules**
- `harness/ingest/openapi_inventory.py` — `is_excluded_path`, `is_excluded_registry_entry`,
  `parse_available_lists` (6 description variants), `build_endpoint_inventory`.
- `harness/ingest/bc2_snapshot.py` — `load_bc2_sources`, `hash_source_files`,
  `normalize_bc2_metric_catalog`, `normalize_bc2_chart_mapping`, `validate_bc2_relationship_rows`,
  `inventory_summary`.
- `harness/ingest/edge_scoring.py` — `score_edge`, `EdgeScore`, the policy table.
- `harness/ingest/skeleton.py` — `build_skeleton`, `build_metric_drafts`, `derive_scope`,
  `split_formula_statements`, `detect_composite`, `resolve_core_formula`, `write_skeleton_artifacts`.
- `harness/kg/reconcile.py:compute_edge_diff` (pure) + `reconcile_edges` (deprecate-never-delete).

**Changed modules**
- `harness/kg/models.py` — `Metric.source_refs`; `EDGE_TYPES` → 2 metric types;
  `DECOMPOSES_RELATIONS`/`INFLUENCES_RELATIONS`.
- `harness/kg/arbitration.py` — `_validate_relation` in `upsert_edge`.
- `harness/ingest/causal.py` — `ConceptIndex.resolve(prefer_scope)` + `resolve_same_scope`;
  scope-safe `formula_edges` (`unresolved` log); `identity_edges` (fires on no-formula-edge);
  additive-safe `rollup_edges`; `correlation_edges` → `INFLUENCES{statistical}`;
  `funnel_edges`; `curated_influence_candidates` + `run_curated_influences`; `_scored_props`.
- `harness/ingest/proposer.py` — trimmed retired edge types from the LLM output schema.
- `harness/cli/kg.py` — `import-bc2-snapshot`, `build-skeleton`, `run-causal --dry-run/--reconcile`,
  `migrate-metric-edges`; auto-approve consults the `review` flag.
- `harness/mcp/graph_server.py` — 11 proposal-only tools (below).
- `harness/api/server.py` — edge relation/status payload; `/api/coverage`, `/api/edge-diff`,
  `/api/traverse/upstream|downstream`; `?include_deprecated`.
- `app/kg-canvas/src/*` — relation-subtype + deprecated edge styling, `EdgeDetail`, `EdgeDiffReview`,
  bucketed `ReviewQueue`, filter toolbar + traversal mode, upstream/downstream in `NodeDetail`.

## 10. CLI commands

```bash
# BC_2 snapshot: hashes, row counts, relationship-candidate validation (no writes)
uv run kg import-bc2-snapshot --tenant rare_seeds

# Build scoped Metric nodes (one core formula each)
uv run kg build-skeleton --tenant rare_seeds --dry-run --write-csv   # compute + artifacts, no write
uv run kg build-skeleton --tenant rare_seeds                          # write metric proposals to queue
uv run kg build-skeleton --tenant rare_seeds --apply-safe            # approve + apply nodes via arbitration

# Causal layer (edge proposals, review-only except auto-safe formula/component)
uv run kg run-causal                          # deterministic edges -> proposal queue
uv run kg run-causal --dry-run                # compute + write edge_diff artifact, no writes
uv run kg run-causal --reconcile              # + deprecate stale deterministic edges
uv run kg run-causal --llm                    # + curated cross-domain + LLM residual (judged, review-only)

# Migrate any legacy edges to the 2-type model (deprecate originals, never delete)
uv run kg migrate-metric-edges --dry-run
```

`run-causal` summary keys: `formula_edges, identity_edges, funnel_edges, rollup_edges,
correlation_edges, curated_candidates, curated_accepted, curated_rejected, unresolved_operands,
llm_link_edges, total_proposals`.

## 11. MCP tools + slash commands (proposal-only)

`harness/mcp/graph_server.py` adds 11 tools (none write the graph — proposal/read-only):
`inspect_bc2_sources`, `propose_metric_nodes`, `propose_metric_edges_from_formula`,
`propose_metric_to_spine_edges`, `propose_influence_candidates`, `validate_edge_candidate`,
`explain_edge_candidate`, and notes-lookup: `lookup_metric_notes`, `list_metrics_by_domain`,
`list_metrics_by_scope`, `get_chart_registry_entry`.

Slash commands (`.claude/commands/`): `/inspect-bc2`, `/propose-skeleton`, `/propose-influences`,
`/validate-edge`, `/lookup-notes` (+ existing `/run-causal`, `/kg-status`, `/create-*-node`).

## 12. Artifacts (`data/skeleton/`)

`canonical_metric_registry.<t>.json`, `metric_registry.<t>.csv`, `composites.<t>.csv`,
`coverage_report.<t>.json`, `source_inventory.<t>.json`, `edge_diff.<t>.<run>.json`,
`deprecated_edges.<t>.<run>.csv`. (`unresolved_operands` are surfaced in coverage + the causal run.)

## 13. How to stand up the live graph (blank-canvas)

The plan calls for a fresh build (no in-place migration). **Destructive — back up first.**

```bash
uv run python -m harness.store.backup export        # back up the current graph (verified)
uv run python -m harness.store.backup wipe --yes    # blank canvas
uv run kg schema-init
uv run kg bootstrap-spine
uv run kg build-skeleton --tenant rare_seeds --apply-safe   # metrics + SHOWN_ON
uv run kg run-causal --reconcile                            # deterministic edges (auto-safe applied; rest queued)
uv run kg run-causal --llm                                  # curated + LLM residual (review-only)
# review in the canvas, then:  uv run kg apply --run <run_id>
```

> Known follow-up: `build-skeleton` emits Metric nodes + `SHOWN_ON`→Dashboard edges; **Dashboard
> nodes themselves are still created by the existing M2 ingest path** (`kg ingest-all`). Folding a
> Dashboard upsert into `build-skeleton` is a tracked follow-up — until then `SHOWN_ON` edges to a
> not-yet-created Dashboard return `missing_endpoint` (logged, non-fatal) and self-heal once the
> dashboard exists.

## 14. Verification

- `uv run pytest harness/tests` → **142 passed**.
- `cd app/kg-canvas && npm run build` → clean (`tsc -b && vite build`).
- Scope invariant: every `DECOMPOSES_INTO{formula|identity}` has `from_scope == to_scope` (0 leaks);
  no ratio rollup uses `aggregation_method = sum`.
- Determinism: `build-skeleton` re-run is a no-op (idempotent MERGE); curated/funnel candidates are
  review-only with reason codes for rejections.

Current deterministic output (rare_seeds): **885 metric nodes**, 340 composites, 265 scope-safe
structural edges (64 formula, 58 identity, 143 rollup/crossproduct), 4 statistical influences,
31 curated INFLUENCES candidates, 21 funnel edges.

## 15. Deferred / future

ML ingestion (all `ml-*` excluded; `causal_role=ml_output` + `prediction` scope reserved); full
append-only evidence ledger; `knowledgeGraph` statistical importer; live SourceProfile ingestion;
Dashboard-upsert inside `build-skeleton`; Thoughtlets / Decision Capsules / Monitoring Contracts.
