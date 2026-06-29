# Plan: Platform/Network breakdown of Active Campaigns + denser KG edges

## Context

In the ThoughtWire Causal KG, `blended.active_campaigns` (campaign-matrix dashboard) currently
"decomposes into" **spend** metrics (Meta Total Spend, Google Spend). That's wrong/misleading and
it's why you "can't see any Google campaigns": the per-platform and per-network active-campaign
metrics **were never authored**, so the graph had nothing to decompose into except the metric's
`depends_on` list (which is spend, the activity predicate).

Root causes (verified across both repos):
1. **Missing metrics, not stale Snowflake.** Of all per-network active-campaign metrics, only
   `google_shopping.active_campaigns` exists. No repo method / router entry / catalog row exists for
   search, youtube, pmax, display, demand_gen, platform-level `google_ads`, `meta_ads`, `klaviyo`,
   or the meta prospecting/retargeting split. The `kg build`/`enrich` pipeline is **fully offline**
   (reads BC_2 CSV/JSON/SQL text, never queries Snowflake), and dc-kg's snapshot is **identical** to
   BC_2's catalog — so leftover Snowflake rows cannot block edges. Snowflake cleanup is real hygiene
   debt, not the cause.
2. **The lineage edge engine is dormant.** `harness/marts/lineage.py` already computes
   shared-mart / shared-column / dbt-`ref()` candidates (with hub suppression) but **nothing promotes
   them to edges** — edges come only from formulas (structural) and LLM reasoning (causal).

Outcome: author the real per-platform/per-network `active_campaigns` metrics in BC_2, fix the
decomposition to be **additive and exhaustive**, re-ingest + rebuild the KG, turn on the lineage
edge engine, and add a snapshot drift-guard + Snowflake schema cleanup.

The data is all available: `MART_GOOGLE_CAMPAIGN_PERFORMANCE` carries `CAMPAIGN_TYPE`
(SEARCH/VIDEO/PERFORMANCE_MAX/SHOPPING/DISPLAY/DEMAND_GEN); `campaign_matrix.py` already computes
`GOOGLE_COUNT`/`META_COUNT`/`KLAVIYO_COUNT`; Meta audience metadata
(`CUSTOM_AUDIENCE_HISTORY.SUBTYPE`, `AD_SET_CUSTOM_AUDIENCE`) is already staged.

## Decisions (locked with user)

| Decision | Choice |
|---|---|
| Breakdown depth | Platform **+** network (~bounded nodes, stable) |
| Where metrics live | **Real BC_2 endpoints first**, then rebuild the KG |
| Extra edge sources | Shared mart/column + dbt `ref()` lineage + backend SQL grain (NOT catalog `depends_on`) |
| Snowflake | dc-kg drift-guard **and** clean BC_2 stale schemas + verify marts |
| Meta split | **New dbt model** (audience-metadata classifier) |
| Google buckets | **Exhaustive** (add display + demand_gen + "other" remainder) |
| Meta buckets | **Exhaustive** (prospecting + retargeting + "other") |
| Lineage edge placement | **Keep as `INFLUENCES:mart_lineage`** (causal, low-confidence) |
| "Active" definition | **Align all** to blended: `spend>0 OR conversion_value>0 OR impressions>0 OR clicks>0` |

Resulting target decomposition (all `DECOMPOSES_INTO`, role=addend, confidence 1.0):
```
blended.active_campaigns = google_ads + meta_ads + klaviyo
google_ads.active_campaigns = search + youtube + pmax + shopping + display + demand_gen + other
meta_ads.active_campaigns  = prospecting + retargeting + other
```

---

## Phase 0 — BC_2 Snowflake hygiene (prereq for dbt/endpoint validation)

- **Verify marts populated** in the active account (`HPAXBLZ-AV43441`). No script exists today; add
  `BC_2/backend/scripts/verify_marts.py` (mirror `verify_endpoints.py`) querying
  `INFORMATION_SCHEMA.TABLES` for the ~91 marts in `mart_tables_inventory.md`; flag missing/0-row.
- **Run stale-schema cleanup**: `BC_2/backend/scripts/cleanup_stale_snowflake_schemas.py`
  (drops dup `KLAVIYO` / lowercase `magento`; row-count safety gate; `--execute` to apply). Dry-run
  first, then `--execute`.
- Confirm `MART_GOOGLE_CAMPAIGN_PERFORMANCE`, `MART_META_CAMPAIGN_PERFORMANCE`,
  `MART_CAMPAIGN_MATRIX`, and `CUSTOM_AUDIENCE_HISTORY` are populated (needed below).

## Phase 1 — BC_2: Google network + platform + klaviyo active_campaigns (no new dbt)

Template to copy: `google_shopping.py::_fetch_active_campaigns_count()` (lines ~335-398).

- **Single source-of-truth query for Google** to guarantee reconciliation: one
  `GROUP BY CAMPAIGN_TYPE` over `MART_GOOGLE_CAMPAIGN_PERFORMANCE` with the **aligned predicate**
  `(SPEND>0 OR CONVERSIONS_VALUE>0 OR IMPRESSIONS>0 OR CLICKS>0)`. Map types →
  search(SEARCH), youtube(VIDEO), pmax(PERFORMANCE_MAX), shopping(SHOPPING), display(DISPLAY),
  demand_gen(DEMAND_GEN), other(else). Each becomes a `*.active_campaigns` metric; their sum =
  `google_ads.active_campaigns`.
  - Add `active_campaigns` to the `VALID_METRICS` list + `get_all_metrics()` in
    `google_search.py`, `google_youtube.py`, `google_pmax.py`; create thin
    `google_display.py`/`google_demand_gen.py` repos **or** expose display/demand_gen/other via a
    platform-level `google_ads` breakdown endpoint (preferred — fewer files, sums by construction).
  - **Update existing** `google_shopping.active_campaigns` to the aligned OR-predicate (behavior
    change — currently `SPEND>0` only).
- **Platform metrics from existing breakdown:** `campaign_matrix.py` already computes
  `GOOGLE_COUNT`/`META_COUNT`/`KLAVIYO_COUNT` (lines ~327-330, OR-predicate). Surface them as real
  metrics `google_ads.active_campaigns`, `meta_ads.active_campaigns`, `klaviyo.active_campaigns`
  (new endpoint or fields), instead of only inside the description string.
- **Reconciliation check (critical):** `GOOGLE_COUNT` from `MART_CAMPAIGN_MATRIX` must equal the sum
  of per-`CAMPAIGN_TYPE` counts from `MART_GOOGLE_CAMPAIGN_PERFORMANCE`. If the two marts cover
  different campaign universes, reconcile (align source) or document the delta in the "other" bucket.

## Phase 2 — BC_2: Meta funnel-stage dbt model + meta network metrics

- **New** `BC_2/dbt/models/intermediate/marketing/int_meta__campaign_funnel_stage.sql` (view, grain
  = campaign). 3-layer classifier → `FUNNEL_STAGE ∈ {PROSPECTING, RETARGETING, OTHER}`:
  1. **Audience metadata (primary):** join `stg_meta_ads__ad_set_custom_audience` →
     `stg_meta_ads__custom_audience_history`; `SUBTYPE='LOOKALIKE'` ⇒ prospecting;
     `SUBTYPE ∈ (CRM_CUSTOM,PIXEL,RETENTION,ENGAGEMENT)` ⇒ retargeting.
  2. **Name patterns (fallback):** reuse existing regex from `mart_meta_retargeting_daily.sql`
     (`'%Retarget%'`, `'%Prospect%'`, `'%Lookalike%'`, …).
  3. **Objective (weak last resort).** MIXED/none ⇒ `OTHER`.
- Expose `FUNNEL_STAGE` on `MART_META_CAMPAIGN_PERFORMANCE` (add `left join` + column).
- Add `meta_prospecting.active_campaigns`, `meta_retargeting.active_campaigns`,
  `meta_other.active_campaigns` (GROUP BY `FUNNEL_STAGE`, aligned predicate). Sum = `meta_ads`.
- Replace the fragile name-only filter in `meta_prospecting.py`/`meta_retargeting.py` with
  `WHERE FUNNEL_STAGE = …`.
- Build/validate: `cd BC_2/dbt && dbt build --target dev --select int_meta__campaign_funnel_stage
  mart_meta_campaign_performance` (live Snowflake — **not** offline; needs Phase 0).

## Phase 3 — BC_2: regenerate catalog

- `python -m backend.scripts.metric_catalog.build_catalog` → `docs/metric-catalog/catalog.csv`
  (auto-discovers new endpoints from `openapi.json`).
- **Open item:** the root `unique_metrics_catalog.csv` is a downstream artifact of `catalog.csv`;
  find its post-processor (`grep -r "unique_metrics_catalog" BC_2/backend/scripts BC_2/scripts`) and
  run it, or update the new rows by hand. New rows use the 28-column layout (see `google_shopping`
  row ~line 79 as template): set `platform`, `sub_platform`, `unit=count`, `aggregation=count`,
  `depends_on`, `source_code_ref`, `formula`.

## Phase 4 — dc-kg: re-ingest + fix decomposition + rebuild

dc-kg's snapshot files are **hand-authored** (no auto-sync). Edit:
- `data/metric_nodes.rare_seeds.json` — add entries for every new metric
  (`formula_human`, `depends_on`, and `formula_components` = `[{role:"addend", node_id:…}]`).
  **Fix** `blended.active_campaigns.formula_components` to the three platform metrics (not spend).
  Add `google_ads.active_campaigns` (7 addends) and `meta_ads.active_campaigns` (3 addends).
- `data/metric_registry.rare_seeds.csv` — add rows + `formula_components` column (col 29) +
  `mart_model` mapping (e.g. `mart_google_campaign_performance`, `mart_meta_campaign_performance`,
  `mart_campaign_matrix`).
- `harness/seed/platforms.json` — add `google_display`, `google_demand_gen`, `google_other` under
  `google_ads`; `meta_other` under `meta_ads` (keep `supports_actions` consistent with siblings).
- Rebuild: `kg build` (Phase-0 auto-backup, wipe, rebuild; Phase 2 draws the additive
  `DECOMPOSES_INTO` from `formula_components`). Use `kg build --smoke` first for a fast blended-only
  sanity pass.

## Phase 5 — dc-kg: lineage edge promotion + drift-guard (dc-kg-only; parallelizable)

- **`promote_lineage_edges()`** in `harness/agentic/enrich.py` (after `migrate_edge_ledger`,
  ~line 662): read live Metric nodes → `lineage.{shared_mart,shared_column,lineage}_candidates()` →
  drop pairs already linked by `DECOMPOSES_INTO` → write each as
  `INFLUENCES` with `relation="mart_lineage"` via `arbitration.append_edge_evidence()`.
  - `mart_lineage` is **already** in `INFLUENCES_RELATIONS` (`harness/kg/models.py:~1157`);
    `upsert_edge`/`append_edge_evidence` accept it — no arbitration change needed.
  - Add policy `"INFLUENCES:mart_lineage"` to `harness/ingest/edge_scoring.py` `_POLICY` (~line 39).
  - **Idempotency:** use a **fixed** timestamp/`event_id` (like `migrate_edge_ledger`, enrich.py:583),
    NOT `datetime.now()`, so re-runs don't duplicate evidence.
  - Register in `cmd_enrich` (`harness/cli/kg.py:~1094-1121`) with a `--no-lineage` flag.
- **`audit_mart_drift()`** drift-guard: reuse `_mart_sql_path()` (`harness/mcp/graph_server.py:~1682`)
  to flag metrics whose `mart_model`/`mart_source` no longer resolves to a dbt SQL file. Surface as
  MCP tool + `kg audit-mart-drift` CLI subcommand. Run after every snapshot refresh.

## Phase 6 — Verify end-to-end

- **BC_2:** run backend; `curl /api/v1/google-search/metrics/active_campaigns` (+ youtube/pmax/
  shopping/display/demand_gen + platform google_ads/meta_ads/klaviyo + meta prospecting/retargeting).
  Reconciliation SQL: assert `sum(children) == parent` for google_ads, meta_ads, and blended.
- **dc-kg:**
  - `kg audit-mart-drift` → expect clean.
  - `kg enrich --dry-run` then `kg enrich`; `kg status` → edge count materially higher.
  - `kg lookup Metric blended.active_campaigns` → decomposes into google_ads/meta_ads/klaviyo (role
    addend, conf 1.0); each platform → its networks.
  - MCP `mcp__graph__validate_edge_candidate` on a couple of new edges; `mcp__graph__lookup_node`.
  - Open the kg-canvas app (`app/kg-canvas`, localhost:5174): Active Campaigns now shows the
    platform→network fan-out; `mart_lineage` edges appear under the causal layer (low confidence).

## Critical files

**BC_2 (author + dbt):**
- `backend/app/repositories/google_shopping.py` (template), `google_search.py`, `google_youtube.py`,
  `google_pmax.py`, `campaign_matrix.py`, `meta_prospecting.py`, `meta_retargeting.py`
- `dbt/models/intermediate/marketing/int_meta__campaign_funnel_stage.sql` (new),
  `dbt/models/marts/marketing/mart_meta_campaign_performance.sql`
- `backend/scripts/metric_catalog/build_catalog.py`, `unique_metrics_catalog.csv`
- `backend/scripts/cleanup_stale_snowflake_schemas.py`, new `backend/scripts/verify_marts.py`

**dc-kg (ingest + edges):**
- `data/metric_nodes.rare_seeds.json`, `data/metric_registry.rare_seeds.csv`,
  `harness/seed/platforms.json`
- `harness/agentic/enrich.py`, `harness/cli/kg.py`, `harness/ingest/edge_scoring.py`,
  `harness/marts/lineage.py` (reuse), `harness/mcp/graph_server.py` (drift-guard, reuse
  `_mart_sql_path`)

## Risks / open items
- **Mart reconciliation:** `MART_CAMPAIGN_MATRIX` vs `MART_GOOGLE_CAMPAIGN_PERFORMANCE` may not share
  an identical campaign universe → parent/children may not sum. Validate in Phase 1; absorb residual
  into "other" or align sources.
- **`unique_metrics_catalog.csv` regeneration path is unconfirmed** (post-processor vs hand-edit) —
  resolve in Phase 3 before dc-kg sync.
- **Meta classifier coverage:** campaigns with no custom audience + no name signal fall to `OTHER`;
  acceptable given the exhaustive-bucket decision, but report the `OTHER` share to gauge accuracy.
- **Predicate change to `google_shopping.active_campaigns`** alters an existing metric's value
  (spend>0 → activity-OR). Intended, but call it out in the BC_2 PR.
- **dbt build hits live Snowflake** — Phase 2 depends on Phase 0 (populated source tables).
