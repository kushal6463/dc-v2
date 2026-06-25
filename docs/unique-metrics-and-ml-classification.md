# Unique Metrics Catalog + ML Classification (Thoughtwire ↔ dc-kg reconciliation)

> Companion to **`data/unique_metrics_catalog.rare_seeds.csv`** and **`data/metric_nodes.rare_seeds.json`**.
> Generated from the live Thoughtwire product (BC_ANALYTICS). **These are new reference artifacts — this repo's
> authoritative `data/metric_registry.rare_seeds.csv` was NOT modified.**

## `metric_nodes.rare_seeds.json` — the KG-ingestion source of truth

The intended **source of truth for future knowledge-graph construction** (it is NOT itself a graph — no synthetic
nodes or roll-up edges). Shape: `{_meta, metrics, input_nodes}`.
- **`metrics`** — the **325** canonical metrics, each ONE node keyed by its sub-platform-aware `metric_id`. Every
  metric carries: `title, source, platform, sub_platform, product, domain, concept, unit, aggregation, polarity,
  is_derived, is_kpi, is_ml, formula_human, depends_on, formula_components, aliases, dashboards, ml_model/ml_task/
  ml_entity, source_code_ref, dependency_confidence, description`.
- **`depends_on` / `formula_components`** are **code-grounded** — derived by reading the actual backend
  repository/SQL (`backend/app/repositories/<slug>.py`, `_build_<metric>_metric`) for EVERY metric, so a metric's
  inputs are the **specific sub-platform ids** (`google_youtube.roas` → `[google_youtube.conversion_value,
  google_youtube.spend]`, never a generic `google_ads.*`/`blended.*`). `source_code_ref` records where each was found.
- **`input_nodes`** (**161**: 96 raw `source_field` columns + 65 ML model-output `intermediate`s) document what
  `depends_on` points to when the input is not itself a catalog metric. Documentation only — not graph edges.
- **`aliases`** = the duplicate metric ids folded into each canonical metric during dedup.
- Counts: 325 metrics (149 ML, 199 derived) · 161 input_nodes · 443 dependency links · 0 unresolved.

The CSV mirrors the same data (one row per metric) with added columns `platform, sub_platform, domain, unit,
aggregation, polarity, is_derived, is_kpi, depends_on, dependency_confidence, source_code_ref, aliases`.

## What this is

A deduplicated, **source/domain-aware** master list of every metric across all Thoughtwire products
(MarketingIQ, CustomerIQ, ProductIQ, **StoreFrontIQ**) including the ML/Predictions models, plus a
metric-vs-chart-vs-table classification of the ML endpoints.

Built from two sources:
- **BC_ANALYTICS `dashboard-v2/public/chart-registry.json`** (989 entries — the live frontend registry).
- **dc-kg `data/metric_registry.rare_seeds.csv`** (464 nodes — this repo's KG-feeding catalog; 190 metrics).

Every entry was **(re)classified by LLM** from its title / formula / how-to-read — the registry's `entity_type`
field was used only as a hint, because it is sometimes wrong. (8 entries were reclassified.)

## Headline numbers

| | Count |
|---|---|
| Classified entries (chart-registry) | 989 → **402 metrics / 464 charts / 123 tables** |
| **Unique metrics** (LLM dedup, sub-platform-aware) | **325** (analytical 176 · ML 149) |
| ML metric entries (→ unique) | 158 → **149 unique** |
| Source | **100% BC-extracted** (frontend chart-registry); dc-kg is a cross-reference only |
| Also present in dc-kg KG | 123 of 325 (`in_dc_kg=true`) |
| Products | MarketingIQ 125 · CustomerIQ 110 · ProductIQ 78 · **StoreFrontIQ 12** |

> **Dedup is LLM-driven, not name-keyed.** An earlier deterministic pass keyed unique metrics on `source.base`,
> which over-merged sub-channels (Google Search/Shopping/YouTube/PMax spend collapsed into one `google_ads.spend`;
> Meta prospecting/retargeting/creative; Klaviyo email vs SMS). The dedup identity **and** product were then
> reassigned by per-source LLM agents from each metric's scope/title/formula. Result: every platform AND
> sub-platform is distinct (e.g. `google_search.spend`, `google_youtube.roas`, `meta_retargeting.spend`,
> `sms.click_rate`), and product reflects the native home (CLV/churn → CustomerIQ even when shown on a store
> dashboard; AOV/orders/fulfillment → StoreFrontIQ).

## Unique-ID & dedup scheme

- **`metric_uid` = `<source-or-subplatform>.<base>`** — the platform is embedded so nothing collapses across
  platforms OR sub-platforms: `google_ads.spend` (Google overview) ≠ `google_search.spend` ≠ `google_shopping.spend`
  ≠ `google_youtube.spend` ≠ `meta_ads.spend` ≠ `meta_retargeting.spend` ≠ `blended.total_ad_spend`; `email.click_rate`
  ≠ `sms.click_rate`. (Covers the "ads spend from Meta vs Google vs Google YouTube" case.)
- The same metric appearing on N dashboards **with the same scope** collapses to ONE row (`dashboards` column lists them);
  the same `metric_key` appearing under different **sub-platform** scopes is kept as separate metrics.
- **Synonyms are linked, not blindly merged.** True synonyms found (same computation, same source):
  - `magento.average_ltv` → alias of **`magento.clv`** (both = gross revenue ÷ customers). *This is the
    `customer_ltv` ≡ `clv` case.*
  - `magento.avg_orders_per_customer` → alias of **`magento.orders_per_customer`**.
  - Different aggregations of one concept stay **distinct**: `magento.clv` vs `magento.total_clv` vs
    `magento.clv_at_risk` vs `magento.predicted_clv` vs `magento.customers_with_clv`.
  - **0 cross-source merges**; **0 ML merges** (per-model `model_auc`, `customers_scored`, `top10_concentration`
    etc. are different models, kept separate).

## CSV columns

`metric_uid, title, source, product, is_ml, ml_task, ml_entity, concept, n_dashboards, dashboards,
merged_from, dc_kg_node_id, in_chart_registry, in_dc_kg, agreement, formula`

`merged_from` lists the original registry metric_keys folded into this uid (synonym dedup transparency).
`agreement` ∈ { `both` (123), `only_chart_registry` (202), `only_dc_kg` (40), `type_mismatch` (4) }.

## StoreFrontIQ — the change we found

StoreFrontIQ was promoted (2026-06-15) from MIQ's former *Orders* section to a top-level product, comprising the
three magento dashboards (`magento-store-overview`, `magento-orders-fulfillment`, `magento-customer-analytics`).
**This repo's authoritative `metric_registry.rare_seeds.csv` does not reflect it** — those dashboards' metrics are
tagged `Marketing IQ` / `Customer IQ` / `Product IQ` (0 as StoreFront). In the new catalog the **12 store-native**
StoreFrontIQ metrics are tagged `product=StoreFrontIQ`:

`magento.aov, magento.avg_days_between_orders, magento.cancellation_rate, magento.cart_abandonment_rate,
magento.completed_orders, magento.gross_revenue, magento.guest_checkout_rate, magento.orders,
magento.processing_orders, magento.refund_rate, magento.unique_customers, magento.units_sold`

> Note: customer-centric metrics that also *appear* on the magento-customer-analytics dashboard (CLV, churn risk,
> repeat rate, new customers) are tagged **CustomerIQ**, their native home — the LLM assigned product by where each
> metric primarily belongs, not merely by which dashboard surfaces it.

> To apply this to the KG, re-tag the `product` of these nodes' dc-kg counterparts (e.g. `ecom.aov`/`store.aov`,
> `ecom.revenue`, `ecom.active_customers`, …) to StoreFrontIQ. Left as a proposal, not applied.

## Reconciliation with the dc-kg registry

The catalog is **BC-only** (325 metrics). dc-kg is a *cross-reference*, not a source — the `in_dc_kg` / `dc_kg_node_id`
columns flag overlap with this repo's KG, but nothing is imported from dc-kg into the catalog.

- **123 of the 325** BC metrics also exist in this repo's KG registry (`in_dc_kg=true`; 146 dc-kg nodes map to them,
  since dc-kg splits some by scope/aggregation).
- **202** BC metrics have no dc-kg counterpart yet (the 149 ML metrics — the KG skeleton predates the ML build — plus
  the newly-split sub-platform metrics and a few newer analytical metrics). These are KG-population candidates.
- For reference, this repo's registry has ~67 metric nodes the frontend does NOT expose as metrics (generic
  placeholders, dc-kg-specific nodes, or endpoints the app renders as charts) — intentionally **left out** of the
  BC catalog. Plus 4 metric/chart disagreements (`blended.marketing_spend`, `blended.monthly_pacing`,
  `blended.todays_pacing`, `blended.weekly_pacing` — dc-kg calls them metrics, the app renders them as charts).

## ML / Predictions classification

The **Predictions** sections are the ML endpoints (`ml-*`). Across **37 ML dashboards**:
**158 metrics · 169 charts · 10 tables.**

| Product | ML dashboards | Metrics | Charts | Tables |
|---|---:|---:|---:|---:|
| CustomerIQ | 19 | 83 | 80 | 3 |
| ProductIQ | 15 | 62 | 66 | 7 |
| MarketingIQ | 3 | 13 | 23 | 0 |
| **Total** | **37** | **158** | **169** | **10** |

ML metrics come mainly from each model's `/summary` endpoint (plus `/horizon-comparison`, `/revenue-at-risk`,
`/accuracy`, `/channels/{c}/marginal-roas`, `/actions`); `/distribution`, `/segments`, `/feature-importance`,
`/forecast`, `/heatmap` are charts; `/customers`, `/products`, `/categories` are row-level tables.
The full per-dashboard breakdown and the Predictions-vs-Performance mapping are in the BC_ANALYTICS doc
`openapi_metrics_charts_inventory.md`. All ML metrics carry `is_ml=true` in the CSV.

## Method

**Every judgment is LLM-made; only the row-grouping, joins, and counting are code.** Parallel subagents did:
- **12 classifiers** over the 989 registry entries → metric/chart/table (registry `entity_type` used as a hint only; 8 reclassified).
- **6 per-source dedup agents** → each non-ML metric's sub-platform-aware `metric_uid` + owning `product` + canonical title (replaces the earlier deterministic `source.base` keying that over-merged sub-channels).
- **3 synonym detectors** + **1 cross-catalog fuzzy reconciler** + **1 sub-platform split adjudicator**.

Grouping by the LLM-assigned `metric_uid`, the dc-kg join, and the endpoint-audit join are deterministic — they
carry out the LLM's decisions, they don't make them.
