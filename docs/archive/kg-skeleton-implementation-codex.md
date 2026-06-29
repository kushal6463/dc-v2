# KG Skeleton Implementation Plan (Codex)

Date: 2026-06-21  
Scope: graph-only V1 skeleton for `dc-kg`  
HTML companion: deferred until explicitly requested

## 1. Executive Summary

The immediate goal is to build a useful, deterministic Causal Graph skeleton in
`dc-kg`: a graph that connects metrics to metrics, dashboards, domains,
products, UI components, and source context without pretending that tenant
specific causal behavior is already known.

The core implementation should add scoped atomic metric nodes derived from chart
formulas and business endpoint metadata. Existing chart-entry `Metric` nodes
remain for compatibility, but they should decompose into the atomic metrics they
contain. For example, a chart/table called `campaign_performance_table` can keep
its current node, while it links to scoped atomic metrics such as
`metric:google-search:roas`, `metric:google-search:spend`,
`metric:google-search:revenue`, `metric:google-search:cpa`, and
`metric:google-search:ctr`.

`knowledgeGraph` should not be merged wholesale. Its value is in provider
patterns: formula/identity edges, additive rollups, funnel composition, model
structure, and later statistical discovery. `dc-kg` remains the Neo4j system of
record with arbitration, provenance, review, and canvas workflows.

## 2. Locked Decisions

- **Source truth for V1:** use chart registry, OpenAPI business endpoints, the
  existing spine, tenant override files, and optional BC Analytics artifacts.
  Exclude `master-config`.
- **Master-config conflict:** the FRD mentions master-config ontology and
  relationships as a deterministic source, but the current `dc-kg` schema and
  implementation intentionally exclude it as stale. V1 follows the current
  `dc-kg` stance and does not read master-config.
- **Metric identity:** use stable scoped atomic metric ids. Do not create one
  global `roas` or `revenue` node.
- **Chart-entry Metrics:** keep existing chart-entry Metrics as surface metrics.
  Link them to atomic metrics with `DECOMPOSES_INTO`.
- **Formula direction:** keep current `dc-kg` direction:
  `derived_or_surface_metric -[:DECOMPOSES_INTO]-> input_metric`.
- **RHS operands:** create atomic operand nodes when the operand is canonical and
  source-scoped, for example `revenue`, `spend`, `clicks`, `orders`,
  `conversions`, `impressions`.
- **Unclear operands:** log and continue. Do not invent unclear nodes.
- **Non-metric LHS terms:** filter status/recommendation/quadrant labels unless
  they are clearly measurable quantities.
- **Seasonality:** do not create seasonality edges in V1. Tenant or industry
  differences such as agriculture versus clothing should become edges only after
  statistical evidence, tenant overrides, or governed learning.
- **Tenant customization:** use global rules plus tenant-specific YAML/JSON
  overrides.
- **Platform nodes:** defer Platform node materialization. Store source/platform
  context on Metric fields in V1.
- **Auto-apply policy:** auto-apply exact-safe deterministic writes only through
  `harness.kg.arbitration`. Review/audit lower confidence or inferred outputs.
- **LLM fallback:** Phase 2, review-only. Do not use LLM output as deterministic
  truth in the first skeleton.
- **Canvas behavior:** show all nodes. Atomic nodes should be visible, not
  collapsed by default.
- **Coverage reporting:** every skeleton run must emit a coverage report.

## 3. FRD Grounding

The FRD frames the Causal Graph as the enterprise subconscious: the business body
an agent wakes into. It must answer five questions for any node:

1. What influences this?
2. What does this influence?
3. What policies or thresholds govern it?
4. How confident is each relationship?
5. What actions are eligible, by whom, and through what tools?

For this V1 implementation, the graph-only skeleton focuses on the first four
structural foundations:

- metrics and metric-to-metric relationships;
- dashboards, UI components, products, and domains;
- source/scope provenance;
- confidence and evidence attribution.

It does not implement Thoughtlets, Decision Capsules, Monitoring Contracts,
Learning Memory, or Context Packs yet. Those layers depend on a clean graph
substrate, so this plan intentionally builds the substrate first.

The FRD also requires auditability and graph integrity. That means even
auto-applied deterministic skeleton writes must flow through the existing
arbitration writer, carry source provenance, and remain reconstructable.

## 4. Current Repo Findings

`dc-kg` already has the right system-of-record shape:

- Neo4j graph storage.
- Pydantic node models.
- Allowed edge vocabulary.
- Deterministic prepass over `chart-registry.json` and `openapi.json`.
- LLM enrichment as proposals.
- Proposal queue and review canvas.
- Single arbitration writer for all graph mutation.
- Causal pass with formula decomposition, rollups, correlations, and optional
  LLM influence judgment.

The main gap is metric identity and granularity. Current ingestion often treats
each chart-registry entry as one `Metric`. That works for canvas display, but it
is too coarse for formula-driven metric-to-metric edges. Many chart entries are
containers or tables with multiple metrics inside, for example:

- `ROAS = Revenue / Spend`
- `CPA = Spend / Conversions`
- `CTR = Clicks / Impressions`
- `Conversion Rate = Conversions / Clicks`

The skeleton should keep the chart-entry node but add the actual atomic metrics
inside it.

## 5. knowledgeGraph Findings

`knowledgeGraph` does not leave this problem unsolved. It builds a file-based
graph using multiple edge providers:

- structural formula/identity edges;
- temporal/statistical discovery;
- model-structure edges;
- compositional funnel edges;
- cross-product additive rollups;
- alias edges.

Its current artifact has a useful mix of edge kinds, but it is not the right
system of record for `dc-kg` because it uses flat files, a different identity
scheme, and no Neo4j arbitration/review governance.

Reusable ideas:

- Formula and canonical identity rules.
- Additive-only rollups.
- Bounded funnel progression templates.
- Model-to-target structure.
- Statistical discovery as a later evidence feed.

Do not reuse:

- `scope.metric[.agg]` as the primary identity scheme.
- Flat-file graph export as source of truth.
- Direct edge writes with no review/arbitration discipline.
- Heavy statistical dependencies inside the default `dc-kg` runtime.

## 6. Atomic Metric Identity

Atomic metric identity must be stable when dashboards are added, removed, or
renamed. The identity should be based on source scope plus concept, not only the
current dashboard id.

Recommended fields on `Metric`:

- `is_atomic: bool`
- `source_scope: str | null`
- `platform_family: str | null`
- `dimension_scope: str | null`
- `parent_chart_metric_uid: str | null`
- `atomic_source: str | null`

Example identities:

```text
metric:google-search:roas
metric:google-search:spend
metric:google-youtube:roas
metric:google-youtube:views
metric:inventory:lost_revenue
metric:inventory:cart_adds
metric:ceo-pulse:revenue
metric:blended:revenue
```

Important identity rules:

- Google Search ROAS and YouTube ROAS are separate nodes.
- Same-concept metrics connect through explicit rollup or evidence edges, not
  through silent merge.
- `dashboard_ids[]` records where a metric appears today.
- If a dashboard disappears, deprecate the surface links, not necessarily the
  underlying atomic metric.
- Dimension-scoped metrics are created only when the formula/title/registry
  explicitly names the dimension, such as device, network, campaign type, or
  category.

## 7. Edge Policy

### Auto-applied exact-safe edges

These can be auto-applied through the arbitration writer:

- `DECOMPOSES_INTO` from a chart-entry Metric to atomic metrics it contains.
- `DECOMPOSES_INTO` from a derived atomic metric to canonical input operands.
- `SHOWN_ON` from atomic/chart Metrics to Dashboard.
- `VISUALIZES` from existing UIComponent type nodes to atomic/chart Metrics
  where chart type is known.
- `BELONGS_TO_DOMAIN` and `PART_OF_PRODUCT` where inherited or deterministically
  refined from parent dashboard/chart context.

### Review or audit-only edges

These should not auto-apply in V1:

- Additive rollups, unless later promoted after review.
- Compositional/funnel progression edges.
- Model-structure edges.
- Prose-derived metric candidates.
- LLM formula/prose parse candidates.
- Statistical `CORRELATES_WITH` edges.
- Any causal `INFLUENCES` or `CAUSES` edge.

### Confidence policy

- Formula decomposition: `confidence = 1.0`.
- Direct surface/spine links: `confidence = 1.0`.
- Additive rollup candidates: tiered confidence, review first.
- Prose/LLM candidates: below 1.0, review-only.
- Statistical candidates: confidence/evidence mass from measured evidence.

## 8. Formula Extraction

The parser should process every chart-registry formula string and split it into
formula statements where possible.

Examples:

```text
ROAS = Revenue / Spend
CPA = Spend / Conversions
CTR = (Clicks / Impressions) * 100
Conv Rate = (Conversions / Clicks) * 100
```

Expected output:

- atomic metric `roas`;
- atomic operands `revenue`, `spend`;
- `roas -[:DECOMPOSES_INTO]-> revenue`;
- `roas -[:DECOMPOSES_INTO]-> spend`;
- chart-entry metric `campaign_performance_table -[:DECOMPOSES_INTO]-> roas`;
- chart-entry metric `campaign_performance_table -[:DECOMPOSES_INTO]-> cpa`;
- chart-entry metric `campaign_performance_table -[:DECOMPOSES_INTO]-> ctr`;
- chart-entry metric `campaign_performance_table -[:DECOMPOSES_INTO]-> conversion_rate`.

Parsing rules:

- Formula LHS creates an atomic metric only if it is a measurable quantity.
- Formula RHS operands create atomic nodes only when clean/canonical.
- SQL functions, constants, CASE labels, thresholds, and status names are not
  metric operands.
- Multi-line formulas can create multiple atomic metrics.
- Ambiguous formulas are logged to audit output.

## 9. Prose and LLM Fallback

V1 uses formula text as the authoritative auto-applied extraction source.

Prose fields can be used as supporting evidence only:

- `formula_explanation`
- `how_to_read`
- `decisions_answered`
- narration text

Prose may create review candidates for:

- missing atomic metric suggestions;
- alias/synonym suggestions;
- classification hints;
- possible semantic links.

LLM fallback should be Phase 2 and review-only.

Pros:

- Handles messy formulas and prose.
- Can normalize aliases such as `CVR`, `Conv Rate`, and `Conversion %`.
- Can detect metrics inside complex tables or narrative explanations.

Drawbacks:

- Can hallucinate operands.
- Can over-merge scoped metrics.
- Can turn prose association into a false deterministic edge.
- Long context improves coverage but does not make output deterministic.

Recommended Phase 2 behavior:

- deterministic parser runs first;
- unresolved formulas are batched with local context;
- LLM emits parse candidates with rationale and confidence;
- candidates go to review/audit, not auto-apply.

## 10. BC Analytics Adapter

BC Analytics exists locally and can provide richer source artifacts. The skeleton
should support it as an optional adapter, not a required runtime dependency.

Useful BC artifacts:

- `dashboard-v2/public/chart-registry.json`
- `dbt/seeds/seed_config_chart_metric_mapping.csv`
- `dbt/seeds/seed_config_charts.csv`
- `dbt/models/**/*.sql`

Recommended command:

```bash
uv run kg import-bc-snapshots --bc-path ../BC_ANALYTICS
```

Purpose:

- refresh versioned `dc-kg` snapshots;
- keep skeleton runs reproducible from `dc-kg`;
- avoid coupling normal graph builds to a sibling checkout;
- use dbt SQL only for deterministic lineage/formula enrichment, not as served
  time-series data.

## 11. Tenant Overrides

Use tenant-specific YAML/JSON override files, for example:

```text
harness/seed/tenants/rare_seeds/skeleton_overrides.json
```

Override categories:

- alias groups;
- source scope mapping;
- platform family mapping;
- domain/product mapping overrides;
- formula parser exclusions;
- metric concept normalization;
- safe-rule toggles;
- future approved causal/statistical rules.

This supports a new tenant whose domains and relationships differ without
forking code. For example, seasonality differences between agriculture and
clothing should be represented later as tenant-specific evidence or governed
rules, not as global V1 skeleton edges.

## 12. Implementation Plan

### Phase 1: deterministic skeleton

Add `harness/ingest/skeleton.py` with pure functions for:

- loading source snapshots;
- loading tenant overrides;
- deriving source scope and platform family;
- parsing formula statements;
- normalizing atomic metric concepts;
- filtering non-metric LHS terms;
- resolving/creating canonical RHS operands;
- building node drafts;
- building exact-safe edge payloads;
- writing CSV/JSON audit artifacts;
- optionally applying safe writes through arbitration.

Add CLI commands:

```bash
uv run kg build-skeleton --tenant rare_seeds --dry-run
uv run kg build-skeleton --tenant rare_seeds --write-csv
uv run kg build-skeleton --tenant rare_seeds --apply-safe
```

### Phase 2: review candidates and LLM fallback

Add review-only candidate generation for:

- prose-derived atomic metrics;
- LLM formula parses;
- ambiguous aliases;
- additive rollups;
- compositional/funnel edges;
- model-structure edges.

### Phase 3: statistical evidence feed

Import `knowledgeGraph` temporal discovery outputs as edge proposals:

- `discovered_edges.<tenant>.csv` -> `CORRELATES_WITH` proposals;
- preserve lag, p-value, correlation, method, FDR flag, and sample size;
- do not auto-promote to `CAUSES`.

## 13. Required Artifacts

Each run should emit:

```text
data/skeleton/atomic_metric_registry.<tenant>.csv
data/skeleton/deterministic_edges.<tenant>.csv
data/skeleton/unresolved_operands.<tenant>.csv
data/skeleton/prose_candidates.<tenant>.csv
data/skeleton/coverage_report.<tenant>.json
```

Coverage report fields:

- source files and hashes;
- dashboards scanned;
- chart-entry metrics scanned;
- formula statements parsed;
- atomic metrics created;
- exact-safe edges created;
- unresolved operands;
- filtered non-metric LHS terms;
- prose candidates;
- skipped reasons;
- deprecated surface links;
- auto-applied writes;
- invalid payloads;
- missing endpoints.

## 14. Test Plan

Add no-DB tests for:

- formula statement splitting;
- multi-line formula extraction;
- LHS metric filtering;
- RHS canonical operand creation;
- scoped identity generation;
- Google Search and YouTube metrics staying separate;
- chart-entry Metric to atomic Metric `DECOMPOSES_INTO` edges;
- derived atomic Metric to operand `DECOMPOSES_INTO` edges;
- unresolved operand logging;
- tenant override application;
- BC adapter snapshot parsing with fixture files;
- coverage report counts.

Add integration tests for:

- `kg build-skeleton --dry-run`;
- `kg build-skeleton --write-csv`;
- `kg build-skeleton --apply-safe` idempotency;
- arbitration-only graph mutation.

Run:

```bash
uv run pytest harness/tests
```

## 15. Open Later

These are intentionally not V1:

- HTML companion file.
- Platform nodes and `SOURCES` edges as graph structure.
- Decision Capsules.
- Thoughtlets.
- Monitoring Contracts.
- Context Pack harness implementation.
- Evidence Ledger storage.
- LLM fallback implementation.
- Statistical discovery import.
- Automatic causal `INFLUENCES` or `CAUSES` edges.

