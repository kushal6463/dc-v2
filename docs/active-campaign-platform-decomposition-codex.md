# Active Campaign Platform Decomposition - Codex Plan

## Summary

Fix `blended.active_campaigns` by separating stable graph structure from selected-period Snowflake counts.

The persistent KG should always know that blended active campaigns decomposes into platform and subchannel campaign counts. The selected period should only update counts, highlights, and dimming, not create or delete graph edges. Actual causal influence from active campaign count into spend, ROAS, revenue, etc. must go through the time-series discovery pipeline as `INFLUENCES`, not through formula or rollup edges.

## Key Changes

- Add a config-driven active-campaign breakdown builder in `dc-kg`.
- First configured platform families: Google Ads, Meta Ads, Klaviyo.
- Future platforms like LinkedIn should be added by config or mart mapping, not by changing the graph algorithm.
- Parent node stays `metric:blended:active_campaigns`.
- Platform children:
  - `metric:google_ads:active_campaigns`
  - `metric:meta_ads:active_campaigns`
  - `metric:klaviyo:active_campaigns`
- Google subchannels use separate scoped metric nodes such as:
  - `metric:google_search:active_campaigns`
  - `metric:google_youtube:active_campaigns`
  - `metric:google_shopping:active_campaigns`
  - `metric:google_performance_max:active_campaigns`
- Meta subchannels use objective buckets, with scoped nodes like `metric:meta_objective_sales:active_campaigns`.

Generate stable `DECOMPOSES_INTO` rollup edges:

- `blended.active_campaigns -> platform active_campaigns`.
- `google_ads.active_campaigns -> Google subchannel active_campaigns`.
- `meta_ads.active_campaigns -> Meta objective active_campaigns`.
- Mark count rollups as non-additive distinct counts: sub-bucket counts are not blindly summed because campaigns can overlap across dimensions.

Add a `dc-kg` read-only Snowflake overlay API:

- Query BC_2-style marts for the selected date window.
- Use the same active predicate as Campaign Matrix: campaign has spend, revenue/conversion value, impressions, or clicks.
- Return counts keyed by `metric_uid`, plus freshness and staleness metadata.
- Keep zero-count buckets in the graph but render them dimmed.

Add canvas overlay behavior:

- When `Active Campaigns` is selected, show platform and subchannel nodes together.
- Apply selected-period counts from the overlay API.
- Do not persist runtime counts into Neo4j.
- Do not mutate structural edges when the user changes dates.

Add stale-data audit:

- Check live KG once Neo4j is online for metrics with stale `history_end`, `data_stale=true`, or empty availability.
- Current repo evidence already shows `blended.active_campaigns` history ends at `2025-12-31`, so it should be marked degraded, not used to remove edges.
- The screenshot's `synthetic` badge is a UI chart reveal marker, not proof that Snowflake deletion caused the missing edges.

## API / Types

Add endpoint:

```text
GET /api/active-campaign-breakdown?metric_uid=metric:blended:active_campaigns&date_from=YYYY-MM-DD&date_to=YYYY-MM-DD
```

Response shape:

- `anchor_metric_uid`
- `date_from`
- `date_to`
- `counts_by_metric_uid`
- `zero_count_metric_uids`
- `stale`
- `freshness_notes`
- `source_marts`

Extend frontend metric props or overlay state with optional runtime-only fields:

- `active_campaign_count`
- `active_campaign_window`
- `runtime_zero_count`
- `runtime_data_stale`

## Test Plan

- Unit test the breakdown config generates Google, Meta, and Klaviyo metric nodes and rollup edges.
- Unit test adding a fake LinkedIn config creates nodes and edges without code changes.
- API test with mocked Snowflake results verifies counts, zero buckets, and stale metadata.
- UI test verifies zero-count nodes are dimmed, not removed.
- Regression test verifies changing date ranges does not create or delete persistent KG edges.
- Causal safety test verifies runtime overlay never emits `INFLUENCES`; causal edges only come from the discovery pipeline.

## Assumptions

- Use Graph + dim overlay as the first visible result.
- Keep Snowflake access in `dc-kg` via a read-only overlay API.
- Include Google, Meta, and Klaviyo now; LinkedIn and future platforms are config additions.
- Neo4j was offline during planning, so live stale-node confirmation must happen after Neo4j is reachable.
