# Final Active Campaign Platform Decomposition Plan - Codex v2

## Summary

Use a merged implementation plan: **BC_2 API as source of truth + dc-kg graph safety/runtime overlay**.

This plan incorporates the useful parts of the updated Claude v2 plan while locking the final decisions:

- Active-campaign metrics and selected-period counts come from **BC_2 APIs**, not direct dc-kg Snowflake reads.
- BC_2's knowledge-graph relationship contract is extended with explicit KG semantics instead of relying on inference.
- Mart/shared-column lineage produces **review/provenance candidates only**, not applied causal `INFLUENCES` edges.
- Persistent graph structure is stable and incremental; selected-period counts are runtime-only.

The goal is to fix `blended.active_campaigns` so it decomposes into real platform and subchannel active-campaign metrics, while keeping the Causal Graph aligned with `docs/frd-docs/thoughtwire-frd.md`: live operational API ingestion, incremental merge, no wipe/rebuild target path, and governed graph writes.

## Implementation Changes

### BC_2 Metrics And Data Models

- Add real active-campaign metrics in BC_2 before dc-kg ingestion:
  - Platform metrics: `google_ads.active_campaigns`, `meta_ads.active_campaigns`, `klaviyo.active_campaigns`.
  - Google disjoint `CAMPAIGN_TYPE` children: search, youtube/video, shopping, display, demand_gen, performance_max, other.
  - Meta disjoint `FUNNEL_STAGE` children: prospecting, retargeting, other.
- Align every active-campaign predicate to Campaign Matrix:
  - `spend > 0 OR conversion_value > 0 OR impressions > 0 OR clicks > 0`.
  - Update `google_shopping.active_campaigns`, which currently uses `SPEND > 0` only.
- Add a Meta funnel-stage dbt model at campaign grain:
  - Audience metadata first: custom audience subtype maps lookalike to prospecting and CRM/pixel/retention/engagement to retargeting.
  - Name-pattern fallback second.
  - Objective fallback last.
  - Anything mixed or unresolved becomes `OTHER`.
- Validate additive counts:
  - `google_ads.active_campaigns = sum(CAMPAIGN_TYPE children including other)`.
  - `meta_ads.active_campaigns = sum(FUNNEL_STAGE children including other)`.
  - `blended.active_campaigns = google_ads + meta_ads + klaviyo`.

### BC_2 Relationship Contract

- Extend the BC_2 knowledge-graph relationships surface rather than overloading existing `component_of`.
- Add explicit KG semantics to relationship rows:
  - `kg_edge_type`, e.g. `DECOMPOSES_INTO`.
  - `kg_relation`, e.g. `rollup`.
  - `kg_role`, e.g. `addend`.
  - `kg_confidence`, e.g. `1.0`.
  - `kg_deterministic`, e.g. `true`.
  - `review_required`, e.g. `false` for deterministic active-campaign rollups.
- Emit active-campaign rollups through `/api/v1/master-config/config/knowledge-graph/relationships` so dc-kg can harvest them deterministically.
- Do not infer dc-kg semantics from BC_2's existing `relationship_type` values alone.

### dc-kg Ingestion And Graph Modeling

- Consume BC_2 OpenAPI/catalog/relationships incrementally.
- MERGE new Metric nodes and `DECOMPOSES_INTO` rollup edges through arbitration.
- Mark old wrong `active_campaigns -> spend` decomposition edges as deprecated; do not delete them.
- Do not hand-edit rare-seed files as the target implementation path.
- Do not use wipe/rebuild as the target path; keep any snapshot bridge explicitly temporary.
- Model only disjoint structural buckets as persistent `DECOMPOSES_INTO` rollups.
- Treat non-additive dimensions such as Google `AD_NETWORK_TYPE` and Meta `OBJECTIVE` as runtime overlays unless they are converted into disjoint campaign-grain buckets.

### Runtime Overlay And Canvas

- Add a read-only dc-kg breakdown endpoint that calls BC_2 APIs for selected-period counts.
- Return:
  - `anchor_metric_uid`
  - `date_from`
  - `date_to`
  - `counts_by_metric_uid`
  - `zero_count_metric_uids`
  - `stale`
  - `freshness_notes`
  - `source_endpoints`
- Apply counts only as runtime UI state.
- Show platform and subchannel fan-out for `Active Campaigns`.
- Dim zero-count buckets instead of removing them.
- Changing date ranges must create/delete/mutate zero persistent graph edges.

### Lineage And Audits

- Generate shared-mart, shared-column, and dbt-ref lineage candidates for review/provenance.
- Do not auto-apply lineage as `INFLUENCES:mart_lineage`.
- Causal `INFLUENCES` edges require evidence, discovery, review, or promoted learning.
- Add BC_2 mart verification before relying on endpoint counts.
- Keep stale Snowflake/schema cleanup as hygiene, not the presumed cause of the missing KG edges.
- Run live KG stale-node audit once Neo4j is reachable.

## Test Plan

- BC_2 endpoint tests for every new active-campaign metric and active predicate.
- dbt tests for Meta `FUNNEL_STAGE` accepted values and campaign-grain uniqueness.
- BC_2 relationship API tests verifying explicit KG fields are present for active-campaign rollups.
- Reconciliation tests:
  - Google platform count equals campaign-type children including `other`.
  - Meta platform count equals funnel-stage children including `other`.
  - Blended count equals platform children.
  - Network/objective overlays are not required to sum.
- dc-kg ingestion tests for incremental MERGE and deprecation of old spend edges.
- Runtime overlay tests verifying selected-period counts and zero-bucket dimming without graph mutation.
- Causal safety tests verifying lineage candidates and runtime overlays do not create applied `INFLUENCES`.
- UI tests verifying `Active Campaigns` shows platform/subchannel fan-out and updates counts on date changes.

## Assumptions

- Keep `docs/active-campaign-platform-decomposition-v2-claude.md` unchanged for comparison.
- Keep `docs/active-campaign-platform-decomposition-codex.md` unchanged as the v1 Codex plan.
- This file, `docs/active-campaign-platform-decomposition-v2-codex.md`, is the final implementation plan.
- Final locked decisions: BC_2 API only for runtime counts, extended relationship contract, lineage review/provenance only.
- Neo4j was offline during analysis, so live graph verification is a follow-up after services are reachable.
