# How the dc-kg knowledge graph was built (LLM-constructed)

_Build run `20260625T173728Z` · 317 metrics · 190 structural + 123 causal edges · loops 0 · orphans 0 · leaves 61 · total agent cost $156.44_

Every node and edge in this graph was created by an LLM (Claude Opus) reasoning over the source documents — there is **no deterministic/rule-based construction**. The harness only seeds the fixed spine and supplies read/write tools; the model decides every metric classification and every edge.

## The spine (seeded deterministically, before the LLM runs)

A fixed backbone is upserted first (idempotent) so the LLM has stable ids to attach to. **These are the only non-LLM nodes.**

- **1 Business** root (`rare-seeds`)
- **9 Domains** (business functions): `data_it` (105), `marketing` (92), `customer` (67), `finance` (27), `product` (25), `operations` (1), `service` (0), `supply_chain` (0), `hr` (0)
- **6 IntelligenceProducts**: `miq` (117), `ciq` (110), `piq` (78), `storefront_iq` (12), `dc` (0), `creative_iq` (0)
- **5 Platforms**: `magento`="StoreFront IQ" (58), `google_ads`="Google Ads" (56), `meta_ads`="Meta Ads" (46), `ga4`="Google Analytics 4" (36), `klaviyo`="Klaviyo" (13)

_(Counts in parentheses = metrics attached to that spine node.)_

## The 5-phase LLM build

The build runs phased-parallel: ~13 agents per phase, sliced by namespace/domain (max 35 metrics each), with a barrier between phases so every edge endpoint exists before edges are drawn.

| Phase | What the LLM does | Tools used |
|---|---|---|
| **0 · seed** | (deterministic) backup → wipe → seed the spine above | `spine_seed` |
| **1 · nodes** | Read each metric's joined evidence; create its `Metric` node with all fields; classify `node_kind`/`ml_kind`; attach to spine (domain/product/platform) | read: `list_metrics`,`get_metric_source`,`get_bc2_sql` · write: `create_metric_node`,`draw_edge` |
| **2 · structural** | Draw `DECOMPOSES_INTO` edges from each formula, tagging each component's arithmetic `role` | `get_metric_source` (formula_components) · `draw_edge` |
| **3 · weave** | Draw causal `INFLUENCES` edges with a confidence tier + a named mechanism + cross-domain flag | `get_metric_source` (notes/depends_on) · `draw_edge` |
| **4 · critique** | Audit the finished graph: report loops/orphans/leaves, de-dupe causal-vs-structural | `kg_status`,`search_nodes` |

## How each METRIC NODE was created (phase 1)

For every metric the LLM called `get_metric_source(metric_id)` — which joins the catalog entry + the `metric_registry` SQL row + the chart-registry entry + the filtered OpenAPI endpoint — and filled the node from that evidence (never invented). Key derivations:

- **`node_kind`**: `source_field`→`input`; `constant.*`→`constant`; ML or non-derived measure→`metric`; derived with ≤3 deps→`intermediary`, else `metric`.
- **`has_endpoint`**: true when a live card/series endpoint exists (endpoint-less inputs still live in the graph; the UI dims them).
- **`ml_kind`**: a model output→`prediction`; a model-quality score→`performance`.
- **spine ids**: catalog `domain`→domain id; product name→`{MarketingIQ→miq, CustomerIQ→ciq, ProductIQ→piq, StoreFrontIQ→storefront_iq}`; `source` namespace→platform id.

## How each EDGE was created + matched

### Structural — `DECOMPOSES_INTO` (definitional, phase 2)

Drawn from a composite metric to each component of its formula (`roas = revenue/spend` → `roas→revenue` role `numerator`, `roas→spend` role `denominator`). **Confidence is always 1.0** (a formula is exact, not a guess). The `role` is what lets traversal derive a **sign** (denominator/subtrahend = −1, everything else = +1), so blast-radius knows whether an input pushes a metric up or down. Role distribution in this build:

| role | count | sign |
|---|---|---|
| denominator | 61 | −1 |
| component | 49 | +1 |
| numerator | 38 | +1 |
| addend | 20 | +1 |
| factor | 12 | +1 |
| subtrahend | 9 | −1 |
| driver | 1 | +1 |

### Causal — `INFLUENCES` (phase 3) and the 0.8 / 0.6 / 0.4 question

A causal edge means *a change in the source plausibly produces a change in the target through a concrete mechanism* — NOT a formula identity. Every causal edge **must name a real mechanism** (no mechanism → no edge). The LLM picks ONE of three confidence tiers (we deliberately do not trust finer numbers, and pair them with a **low `evidence_mass`** so a future statistical pass over Snowflake data overrides them cleanly):

| tier | meaning | this build |
|---|---|---|
| **0.8** | direct, well-named mechanism, high confidence | 28 edges |
| **0.6** | plausible mechanism, weaker evidence | 73 edges |
| **0.4** | defensible but speculative | 22 edges |

Observed distribution skews to **0.6 (moderate)** — 73 of 123. If you want sharper separation in blast-radius we can widen the tiers to 0.9/0.6/0.3 (one-line change in `harness/agentic/prompts.py:CAUSAL_TIERS`). Edges flagged `cross_domain` when cause and effect sit in different domains (e.g. `meta_ads.roas` [marketing] → `derived.gross_margin` [finance]).

## Metric summary (all 317)

**By kind:** metric **218**, intermediary **99** — total 317. _(both are 'metrics' in the catalog; `intermediary` = aggregates like `blended.total_ad_spend` that other metrics build on.)_

**ML metrics (149):** prediction 143, performance 6.

**By namespace (metric id prefix):**

| namespace | metrics |
|---|---|
| ml | 149 |
| magento | 46 |
| blended | 28 |
| ga4 | 25 |
| google_ads | 9 |
| google_youtube | 8 |
| email | 7 |
| meta_retargeting | 6 |
| meta_ads | 5 |
| meta_creative | 5 |
| google_pmax | 5 |
| sms | 5 |
| meta_prospecting | 5 |
| google_search | 5 |
| derived | 5 |
| google_shopping | 4 |

**By domain / product / platform:** see the spine section above.

## Known gaps / refinement candidates

1. **`chart_type` is empty** — the chart-registry has no chart-type field (only `entity_type`), so the LLM had nothing to fill it from. A separate chart-type catalog is being built from the BC_2 frontend to backfill this.
2. **A few `component` structural edges are loose** — some action/recommendation metrics (e.g. `scale_campaigns`) were attached as a `component` of `roas`; those are causal, not definitional. Concentrated in the `blended` namespace; a targeted re-weave can fix.
3. **ML domain skew** — 105 metrics landed in `data_it` (the ML modeling domain) rather than their business domain (customer/product/marketing). Defensible, but a business-domain reclassification for ML metrics may read better.
4. **Sub-platform granularity** — the spine has 5 parent platforms; the catalog carries finer sub-platforms (`google_search`, `google_youtube`, `google_pmax`, `google_shopping`, `meta_prospecting`, `meta_retargeting`, `meta_creative`) folded under them. Adding sub-platform nodes is a possible next step.
