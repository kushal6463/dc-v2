# Active Campaign Platform Decomposition — Final Plan v2 (Claude)

> **Status: decision-complete.** Merged + FRD-audited, and reconciled with
> `active-campaign-platform-decomposition-v2-codex.md` (the two v2 plans converged). Keeps
> **claude's** depth (BC_2 authoring, Meta audience classifier, lineage engine, drift-guard) and
> **codex's** discipline (stable structure vs runtime overlay, non-destructive, causal safety,
> config-driven), corrected against the FRD (`docs/frd-docs/thoughtwire-frd.md`). Pairs with
> `…-v2-codex.md`; the v1 claude/codex files are left unchanged for history.

## Context

`blended.active_campaigns` wrongly `DECOMPOSES_INTO` **spend** because the per-platform/per-network
active-campaign metrics were never authored in BC_2 (only `google_shopping` exists) — so "you can't
see any Google campaigns." The `kg build`/`enrich` pipeline is offline and never queries Snowflake,
so leftover Snowflake rows are **not** the cause (separate hygiene debt). Goal: author the real
per-platform/per-network `active_campaigns` metrics, fix the decomposition to be **additive +
exhaustive**, land it **without live Snowflake**, apply selected-period **counts as a runtime
overlay** (never persisted), add a **non-additive serving-network/objective overlay**, and densify
the graph with **review-gated** lineage edges.

### Key clarification — FRD-native ingestion needs NO live Snowflake for structure
Per FR-ING-014, graph **structure** is built from the **metric-relationships config** (primary edge
source) + **formula decomposition** (corroborating; `DECOMPOSES_INTO` pinned 1.0) + the **OpenAPI
spec/catalog**. Snowflake/marts sit *behind the API's metric-value endpoints* and only supply
**runtime values** (the counts) and **statistical discovery**. All structural inputs already exist
offline in the BC_2 snapshot → we can build the decomposition now; counts/discovery light up when
Snowflake is live.

## Resolved decisions (locked with user)

| # | Decision | Choice |
|---|---|---|
| 1 | Breakdown depth | Platform **+** network |
| 2 | Where metrics live | Real BC_2 endpoints first (live-API source of truth) |
| 3 | Landing path | **Offline-snapshot bridge + one dev-phase rebuild now**, then incremental MERGE; migrate to live-API ingestion later |
| 4 | Period counts | **Runtime overlay, never persisted**; zero buckets dimmed, not removed |
| 5 | Structural count semantics | **Additive by campaign type** (one campaign → one bucket; parts sum); marked distinct-count + provenance |
| 6 | "Active" predicate | Aligned to blended: `spend>0 OR conversion_value>0 OR impressions>0 OR clicks>0` |
| 7 | Google structural buckets | Exhaustive: search, youtube(video), shopping, display, demand_gen, pmax, other |
| 8 | Meta split | New dbt classifier (audience metadata → name → objective); prospecting / retargeting / other |
| 9 | Lineage edges | **Review-gated `INFLUENCES:mart_lineage`** — created but HELD for review, low mass, idempotent (not auto-active) |
| 10 | Serving-network / objective | **Non-additive runtime overlay** dimensions (NOT structural edges) |
| 11 | Snowflake | dc-kg drift-guard + clean BC_2 stale schemas + verify marts (hygiene, not the cause) |
| 12 | Platform extensibility | Config-driven families (Google/Meta/Klaviyo now; LinkedIn etc. via config) |

### Three separated layers (keep distinct)
- **Structural (persistent, additive `DECOMPOSES_INTO`, conf 1.0):**
  ```
  blended.active_campaigns = google_ads + meta_ads + klaviyo
  google_ads.active_campaigns = search + youtube + shopping + display + demand_gen + pmax + other
  meta_ads.active_campaigns  = prospecting + retargeting + other
  ```
- **Runtime overlay (NOT persisted):** per-period counts (additive structural buckets) **and**
  non-additive dimensions (Google `AD_NETWORK_TYPE`, Meta `OBJECTIVE`). Applied as KPI value /
  highlight / dimming. Changing the date MUST NOT create/delete/mutate any edge.
- **Causal (`INFLUENCES`):** real causation (`youtube.active_campaigns → youtube.spend →
  blended.revenue`) comes only from the discovery pipeline; lineage edges are review-gated. The
  rollup and the overlay **MUST NOT** emit applied `INFLUENCES` (FR-SCORE-001, codex causal-safety).

---

## Phase A — BC_2: author metrics + Meta dbt + declare relationships + catalog

Template: `backend/app/repositories/google_shopping.py::_fetch_active_campaigns_count()` (~L335-398).

- **Google network counts (no new dbt):** one `GROUP BY CAMPAIGN_TYPE` over
  `MART_GOOGLE_CAMPAIGN_PERFORMANCE` with the aligned predicate; map SEARCH/VIDEO/SHOPPING/DISPLAY/
  DEMAND_GEN/PERFORMANCE_MAX/else → search/youtube/shopping/display/demand_gen/pmax/other. Add
  `active_campaigns` to `VALID_METRICS` + `get_all_metrics()` in `google_search.py`,
  `google_youtube.py`, `google_pmax.py`; expose display/demand_gen/other via a platform-level
  `google_ads` breakdown endpoint. **Update** `google_shopping.active_campaigns` to the aligned
  OR-predicate (was `SPEND>0`).
- **Platform counts (already computed):** surface `campaign_matrix.py`'s existing
  `GOOGLE_COUNT`/`META_COUNT`/`KLAVIYO_COUNT` (~L327-330) as real
  `google_ads/meta_ads/klaviyo.active_campaigns` metrics.
- **Meta funnel-stage dbt model:** `dbt/models/intermediate/marketing/int_meta__campaign_funnel_stage.sql`
  (view, grain=campaign) → `FUNNEL_STAGE ∈ {PROSPECTING,RETARGETING,OTHER}`, one bucket per campaign:
  (1) audience metadata (`stg_meta_ads__ad_set_custom_audience` → `…custom_audience_history`:
  LOOKALIKE⇒prospecting; CRM_CUSTOM/PIXEL/RETENTION/ENGAGEMENT⇒retargeting); (2) name patterns
  (reuse `mart_meta_retargeting_daily.sql` regex); (3) objective fallback; else OTHER. Expose
  `FUNNEL_STAGE` on `MART_META_CAMPAIGN_PERFORMANCE`; add `meta_{prospecting,retargeting,other}.active_campaigns`.
- **Declare the rollup as a deterministic edge source (FR-ING-014):** add the additive rollup
  relationships (role=addend, marked distinct-count + provenance) to BC_2's metric-relationships
  config (`master-config/config/knowledge-graph/relationships`) — same content the snapshot seed
  carries now and the live API serves later.
- **Regenerate catalog:** `python -m backend.scripts.metric_catalog.build_catalog`. Open item:
  confirm the post-processor for root `unique_metrics_catalog.csv`.
- Build/validate dbt (live Snowflake — needs Phase D): `dbt build --target dev --select
  int_meta__campaign_funnel_stage mart_meta_campaign_performance`.

## Phase B — dc-kg: land structure via offline bridge, then go incremental

- **Bridge (now):** build structure from the **offline BC_2 snapshot** (spec + relationships config
  + formulas — no Snowflake). Update the dc-kg snapshot inputs (`data/metric_nodes.rare_seeds.json`,
  `data/metric_registry.rare_seeds.csv`) with the new metrics and **fix `blended.active_campaigns`'s
  `formula_components`** to the additive platform metrics (role=addend, not spend). Add the platform
  + network metrics with their additive `formula_components`. This snapshot-edit is an **explicit
  temporary bridge**, not the target architecture.
- **One dev-phase rebuild** to seed the new structure (the graph isn't operational yet — no live
  decision capsules — so FR-ING-010's "never rebuild" doesn't yet bite). After this, **operate
  incrementally**: additive MERGE via arbitration, mark superseded `active_campaigns→spend` edges
  `DEPRECATED` (never hard-delete).
- **Migration target (later):** replace snapshot editing with **live-API harvest → arbitration MERGE**
  (FR-ING-001/010) once the BC_2 API + Snowflake are live. Documented as the convergence path.
- **Config-driven families:** encode Google/Meta/Klaviyo (+ future LinkedIn) in
  `harness/seed/platforms.json` so new platforms are config, not code.
- **Lineage edges — REVIEW-GATED:** `promote_lineage_edges()` in `harness/agentic/enrich.py` →
  `lineage.{shared_mart,shared_column,lineage}_candidates()` → drop pairs already in
  `DECOMPOSES_INTO` → write `INFLUENCES` `relation="mart_lineage"` with **`review_state` HELD**, low
  `evidence_mass`, fixed timestamp/event_id (idempotent). They appear as **proposals awaiting review,
  not applied causal edges** (FR-ING-016 speculative-producers-governed; FR-SCORE-001). Policy
  `INFLUENCES:mart_lineage` in `edge_scoring.py` set to `held_review`; register in `cmd_enrich`
  (`harness/cli/kg.py`) with `--no-lineage`.
- **Drift-guard:** `audit_mart_drift()` reusing `_mart_sql_path()` (`harness/mcp/graph_server.py`)
  → MCP tool + `kg audit-mart-drift`.

## Phase C — Runtime overlay + canvas (counts, never persisted)

- **Breakdown read endpoint** returning selected-period data keyed by `metric_uid`, sourced from the
  **BC_2 API** (not direct Snowflake): `counts_by_metric_uid` (additive structural buckets),
  **non-additive overlay dimensions** (`AD_NETWORK_TYPE`, `OBJECTIVE`), `zero_count_metric_uids`,
  `stale`, `freshness_notes`, `source_marts`. Until Snowflake is live it returns stale/empty
  gracefully.
- **Canvas:** Active Campaigns shows platform + sub-network fan-out; runtime counts as KPI/highlight;
  **dim** zero-count buckets; non-additive overlay shown as a clearly-labelled separate dimension;
  **never** write counts to Neo4j; date changes **never** mutate edges.

## Phase D — BC_2 Snowflake hygiene (prereq for dbt + count validation)

- Add `BC_2/backend/scripts/verify_marts.py` (mirror `verify_endpoints.py`): assert
  `mart_tables_inventory.md` marts exist + non-empty in the active account.
- Dry-run then `--execute` `cleanup_stale_snowflake_schemas.py` (dup `KLAVIYO`/`magento`; row-count
  safety gate).
- Stale metrics (e.g. `blended.active_campaigns` history ends 2025-12-31) → mark **degraded**, never
  used to remove edges. `synthetic` badge is a UI marker, not deletion proof.

## Phase E — Verify

- **BC_2:** `curl` each endpoint; reconciliation SQL: `sum(structural children) == parent` for
  google_ads / meta_ads / blended (incl. `other`); **overlay dimensions NOT required to sum**. Report
  Meta `OTHER` share. dbt tests: `FUNNEL_STAGE` accepted-values + campaign-grain uniqueness.
- **dc-kg:** `kg audit-mart-drift` (clean); confirm new nodes/edges present and old spend edges
  `DEPRECATED` (not deleted); `kg enrich --dry-run` then `kg enrich`; confirm `mart_lineage` edges
  are **held for review**, not active; `kg lookup Metric blended.active_campaigns` (additive
  decomposition, role addend, conf 1.0); MCP `validate_edge_candidate`.
- **Canvas / regression:** fan-out renders; counts apply on date change with **zero** edge mutation;
  zero buckets dimmed; runtime overlay + lineage emit **zero applied** `INFLUENCES`.

## FRD conformance checklist
- FR-ING-001 (live-API source) — target via migration; bridge uses snapshot **explicitly temporary**.
- FR-ING-010 (incremental, deprecate-not-delete) — one dev rebuild now (graph pre-operational), then
  additive MERGE + deprecate. ✓
- FR-ING-013 (canonical identity, `SHOWN_ON`) — canonical `metric_uid`; reuse nodes. ✓
- FR-ING-014 (relationships endpoint primary; formula corroborating) — rollup declared in config. ✓
- FR-ING-016 (speculative producers governed) — lineage edges review-gated, not auto-promoted. ✓
- FR-SCORE-001 (causal confidence from ledger; `DECOMPOSES_INTO` pinned 1.0) — additive rollup 1.0;
  lineage held/low-mass; real causation from discovery. ✓
- FR-ARCH-001 (no junk-drawer) — non-additive dimensions kept in the runtime overlay, not the graph. ✓
- FR-CG-008 (one governed writer) — all writes via arbitration. ✓

## Critical files
- **BC_2:** `backend/app/repositories/{google_shopping,google_search,google_youtube,google_pmax,campaign_matrix,meta_prospecting,meta_retargeting}.py`;
  `dbt/models/intermediate/marketing/int_meta__campaign_funnel_stage.sql` (new);
  `dbt/models/marts/marketing/mart_meta_campaign_performance.sql`; metric-relationships config;
  `backend/scripts/metric_catalog/build_catalog.py`; `cleanup_stale_snowflake_schemas.py`, new `verify_marts.py`.
- **dc-kg:** `data/metric_nodes.rare_seeds.json`, `data/metric_registry.rare_seeds.csv` (bridge);
  `harness/agentic/enrich.py`, `harness/cli/kg.py`, `harness/ingest/edge_scoring.py`,
  `harness/marts/lineage.py` (reuse), `harness/mcp/graph_server.py` (drift-guard), arbitration MERGE
  path, `harness/seed/platforms.json`; `app/kg-canvas` (overlay + dimming).

## Risks / open items
- **Incremental-MERGE machinery:** after the one dev rebuild, going additive-only may need wiring
  beyond today's `kg build`; confirm before the post-bridge phase.
- **Mart reconciliation:** `MART_CAMPAIGN_MATRIX` vs `MART_GOOGLE_CAMPAIGN_PERFORMANCE` universes may
  differ → validate sum, absorb residual into `other`.
- **`unique_metrics_catalog.csv` regeneration path** unconfirmed — resolve in Phase A.
- **Meta classifier coverage:** no-audience + no-name campaigns → `OTHER`; report the share.
- **`google_shopping.active_campaigns` value changes** (spend>0 → activity-OR) — intended; note in PR.
- **Blank-file check:** no problematic empty docs found (only normal `__init__.py` / `.gitkeep`).
