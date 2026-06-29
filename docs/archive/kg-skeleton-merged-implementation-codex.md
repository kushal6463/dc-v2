# KG Skeleton Merged Implementation Spec (Codex)

Date: 2026-06-21
Status: interview-approved coding spec
Scope: Knowledge Graph / Causal Graph implementation only
Supersedes: the earlier draft `docs/kg-skeleton-merged-implementation-codex.md`

## 0. Interview-Locked Decisions

These decisions came from the interview and are binding for this merged spec.

| Topic | Decision |
|---|---|
| Document shape | Coding spec, not just a comparison memo |
| Edge taxonomy | Adopt Claude's 2 metric-edge types: `DECOMPOSES_INTO` and `INFLUENCES`, with `relation` subtypes |
| Metric node model | Only meaningful business metrics are `Metric` nodes. No atomic/surface/business metric tiers |
| Master-config | Keep excluded; explicitly document the FRD conflict |
| Auto-apply | Auto-apply formula/component edges only, through arbitration |
| Cross-domain edges | Curated rules generate candidates; LLM judge/refuter verifies; human review still required |
| Stale deterministic edges | Deprecate/version stale deterministic edges, do not silently delete |
| `causal_role: constraint` | Allow as an upstream cause only through curated rules |
| Edge scoring | Assign confidence/evidence_mass at edge creation through deterministic policy; design full append-only ledger for later |
| BC_2 source | Use `/Users/kushal/Desktop/kal/BC_2` as the primary offline snapshot source, with validation/provenance |
| MCP/LLM tools | Proposal-only. Tools may parse and propose metric->metric and metric->other-node edges, but never direct-write graph edges |
| SourceProfile/live ingestion | Later phase, after skeleton correctness |
| `knowledgeGraph` | Borrow deterministic ideas now; use statistical discovery later as evidence feed |
| Aliases | Metadata first: `Metric.aliases[]` / `synonyms[]`; defer alias edges |
| Thoughtlets / Decision Capsules | Out of scope for this spec |
| UI | Redesign the existing KG canvas app end to end; no Thoughtlet/DC UI implementation |
| Existing merged draft | Replace it with this interview-approved spec |

## 1. Merge Principle

This spec does not randomly combine Claude and Codex ideas. It uses this merge
rule:

- Take Claude ideas when they are concrete fixes to the current implementation.
- Take Codex ideas when they improve the product-level KG shape and FRD coverage.
- Keep `dc-kg` as the Neo4j system of record.
- Keep all graph mutation behind proposals and the arbitration writer.
- Let MCP/LLM tools improve parsing and edge coverage, but keep them
  proposal-only.
- Do not merge `knowledgeGraph` wholesale.
- Do not let LLM output become deterministic truth.
- Do not implement Thoughtlets, Decision Capsules, Monitoring Contracts, or
  execution runtime in this KG phase.

## 2. Source Documents And What We Keep

### From `metric-skeleton-implementation-claude.md`

Keep:

- same-scope formula resolution;
- additive-safe rollups;
- canonical identity fallback rules;
- funnel/compositional templates;
- alias resolution ideas;
- deterministic skeleton CSV export;
- stale deterministic edge reconciliation;
- explicit day-2 add/edit/remove behavior;
- UI blast-radius awareness.

Change:

- Do adopt the updated Claude two-type metric-edge model, but with our stricter
  write policy: formula/component edges are the only auto-applied class.
- Do not auto-apply all deterministic edge classes.
- Do not implement Claude's full evidence ledger module now; use deterministic
  edge scoring props first.

### From `docs/kg-skeleton-implementation-codex.md`

Keep:

- meaningful scoped metric identity as core implementation;
- chart/table/card entries as UI/source context, not separate metric tiers;
- coverage report artifacts;
- tenant override files;
- LLM fallback as review-only;
- BC/analytics snapshot artifacts, not live runtime dependencies;
- `knowledgeGraph` as a later statistical feed.

Change:

- Do not build a completely separate skeleton system.
- The skeleton builder must feed the existing proposal -> review -> arbitration
  path and reuse existing causal code where practical.
- Upgrade BC data from optional enrichment to the primary offline source
  snapshot by reading `/Users/kushal/Desktop/kal/BC_2` seed/catalog/OpenAPI
  artifacts.

### From the FRD

Keep for this KG phase:

- Causal Graph answers upstream cause, downstream blast radius, confidence, and
  policy/threshold/action eligibility hooks.
- Causal edges need confidence and lag.
- Mutation must be governed and attributable.
- Ingestion should eventually become live OpenAPI + SourceProfile based.

Defer:

- Thoughtlet stream.
- Investigation Agent.
- Decision Capsule ledger.
- Approval graph.
- Monitoring Contracts.
- Full learning memory.
- Context Pack / reverse temporal context injection.

### From `knowledgeGraph`

Borrow now:

- same-scope structural resolution pattern;
- identity formulas;
- additive-only rollup discipline;
- funnel templates;
- statistical discovery handoff shape as a future design.

Do not borrow now:

- flat-file graph as source of truth;
- primary identity scheme;
- direct edge writes;
- heavy statistical dependencies in `dc-kg`;
- alias edges as authoritative KG writes.

### From the updated Claude KG skeleton doc

Keep:

- authored snapshot inputs as a first-class ingestion mode;
- path-score traversal as a KG deliverable;
- source-file hashes in coverage reports;
- deprecate-never-delete Day-2 behavior;
- explicit migration blast radius for edge taxonomy and canvas rendering.

Change:

- Use BC_2 snapshots as the primary authored input source instead of inventing
  all input files manually.
- Keep the full evidence ledger deferred, but add deterministic scoring at edge
  creation.
- Expand the UI work beyond Claude's two-file blast radius because the existing
  KG app needs edge review, diff review, meaningful metric inspection, and
  traversal.

## 3. Current Implementation Baseline

The current repo already has the correct core architecture:

- `harness/ingest/prepass.py`
  - reads `docs/frd-docs/chart-registry.json` and `openapi.json`;
  - excludes `master-config`, auth, settings, health, docs, redoc, and non-GET
    surfaces;
  - currently emits deterministic `Dashboard` and chart-entry `Metric` drafts.
    The target skeleton normalizes these into meaningful `Metric` nodes or
    UI/source references.
- `harness/ingest/proposer.py`
  - enriches drafts through an LLM;
  - writes proposal objects, not graph records.
- `harness/ingest/apply.py`
  - applies approved proposals only through arbitration.
- `harness/kg/arbitration.py`
  - is the single writer for nodes and edges.
- `harness/kg/models.py`
  - defines the core graph labels and allowed edge vocabulary.
- `harness/ingest/causal.py`
  - builds formula decomposition, rollups, static correlations, and optional
    LLM-verified influences.
- `app/kg-canvas`
  - displays graph data and proposal review workflows.

This is an extend-and-correct project, not a greenfield rewrite.

## 4. Current Gaps To Fix

### 4.1 Metric identity and node meaning

Today, one chart-registry entry usually becomes one `Metric`. That is too loose.
Many chart entries are not metrics; they are UI surfaces that display one or
many metrics:

- tables containing multiple metrics;
- derived formulas such as `ROAS = Revenue / Spend`;
- rankings such as top campaigns;
- multi-line formulas.

Required fix:

- create `Metric` nodes only for real business metrics;
- give every metric one meaningful scoped id;
- do not create a separate Metric node just because a chart/table/card exists;
- link dashboards and UI components to the metrics they show;
- link derived metrics to component metrics with `DECOMPOSES_INTO`.

### 4.2 Scope-blind formula resolution

Current `ConceptIndex.resolve()` chooses a broad representative. That can link a
Google Search metric to blended or YouTube operands.

Required fix:

- add `prefer_scope`;
- prefer exact `scope_key`;
- then same platform;
- then current broadest fallback;
- add tests for Google Search vs Google YouTube vs blended.

### 4.3 Additivity-blind rollups

Rollups must not sum ratios. Channel ROAS does not sum into blended ROAS.

Required fix:

- classify additive metrics;
- permit additive crossproduct rollups for count/currency/duration metrics;
- treat ratios as reaggregation candidates, not sum edges;
- review rollups before apply in this phase.

### 4.4 Missing deterministic identity fallback

Many formulas are absent or messy. Common metric identities should still be
recognized when safe.

Required fix:

- add canonical identities for ROAS, CPA, CPC, CPM, AOV, CTR, CVR, frequency,
  and related standard metrics;
- use same-scope resolution;
- log unresolved identity operands.

### 4.5 Cross-domain candidate gap

Current candidate generation mostly stays within one dashboard. This misses
business mechanisms such as:

- marketing demand increasing stockout risk;
- inventory availability affecting revenue;
- deliverability affecting campaign engagement;
- stockout suppressing conversion.

Required fix:

- add curated cross-domain rule candidates;
- allow `constraint` metrics only through those rules;
- use LLM judge/refuter for verification;
- keep all resulting `INFLUENCES` review-only.

### 4.6 Stale deterministic edges

Current edge writes are `MERGE`-only. If a formula changes, old deterministic
edges can remain.

Required fix:

- compute deterministic edge sets per run;
- compare against live deterministic edges;
- mark stale edges deprecated/versioned;
- do not silently delete;
- never auto-deprecate human-reviewed causal edges.

### 4.7 Edge scoring and future evidence ledger

Current edges carry `confidence`, `evidence_mass`, and sometimes
`evidence_count`, but there is no full append-only evidence ledger.

Required fix for this phase:

- score edges at creation through a deterministic policy based on source class;
- keep edge-level `confidence`, `evidence_mass`, `scoring_policy`,
  `mechanism`, `lag`/`temporal_lag`, and source props;
- make sure all review-only `INFLUENCES` edges carry confidence, evidence mass,
  mechanism, lag, and source;
- write the future ledger contract, but do not implement full ledger storage
  until Decision Capsules/outcome learning exist.

### 4.8 KG app UI

The canvas must support meaningful metric inspection, edge subtypes, review
gates, stale-edge diffs, and traversal. The current UI is useful but not enough
for the new KG skeleton workflow.

Required fix:

- redesign the KG canvas app;
- do not build Thoughtlet or Decision Capsule UI in this phase.

### 4.9 Source depth and parsing tools

Current `dc-kg` source snapshots are useful, but `BC_2` has richer offline
material:

- `dbt/seeds/seed_config_metrics.csv`
- `dbt/seeds/seed_config_chart_metric_mapping.csv`
- `dbt/seeds/seed_config_metric_relationships.csv`
- `dbt/seeds/seed_config_ontology_causal_edges.csv`
- `dbt/seeds/seed_config_ontology_metrics.csv`
- `dbt/seeds/seed_config_thresholds.csv`
- `dashboard-v2/public/chart-registry.json`
- `docs/metric-catalog/metrics_catalog.json`
- `docs/metric-catalog/formula_reconciliation.json`
- `openapi-1.json`

Required fix:

- add a BC_2 snapshot importer;
- validate noisy rows before proposal creation;
- use LLM/MCP parsing tools only to propose, explain, and validate edges;
- route every proposed graph mutation through the proposal queue and arbitration.

## 5. Target Graph Shape

### 5.1 Metric-only node model

There is only one metric node concept: `Metric`.

A `Metric` node represents a real business metric or signal. It does not
represent a chart, table, card, dashboard slot, or UI container unless that
surface is itself a real metric.

Examples of valid `Metric` nodes:

- `metric:google-search:roas`
- `metric:google-search:spend`
- `metric:google-search:revenue`
- `metric:google-youtube:roas`
- `metric:inventory:out_of_stock`
- `metric:blended:revenue`
- `metric:klaviyo-email:open_rate`
- `metric:klaviyo-email:click_rate`

Examples that should usually not be standalone `Metric` nodes:

- `campaign_performance_table`
- `top_campaigns_table`
- `line_chart`
- `dashboard_summary_card`

Those are UI/source concepts. They should be represented by `Dashboard`,
`UIComponent`, source props, or provenance fields, and linked to the metrics they
display.

### 5.2 Meaningful metric ID

Use one stable meaningful id for each metric:

```text
metric_uid = metric:<scope_key>:<metric_key>
```

Examples:

```text
metric:google-search:roas
metric:google-search:spend
metric:google-youtube:roas
metric:meta-ads:ctr
metric:inventory:out_of_stock
metric:blended:revenue
```

Required identity fields:

- `metric_uid`
- `canonical_id`
- `metric_id`
- `display_name`
- `scope_key`
- `metric_base`
- `concept_key`
- `aliases[]`
- `synonyms[]`
- `dashboard_ids[]`
- `component_ids[]`
- `endpoint_paths[]`
- `source_refs[]` or equivalent provenance fields if model support is added

### 5.3 Identity rules

- Never create one global `roas`, `spend`, or `revenue` node.
- Same concept across scopes stays separate.
- Cross-scope links are explicit edges, not silent merges.
- A chart/table/card can show many metrics; it does not become a metric just
  because it appears in the chart registry.
- If one chart entry maps exactly to one real metric, create or reuse that
  metric node and attach the chart/dashboard context to it.
- If one chart entry contains many metrics, create or reuse the real metric nodes
  and link the UI/source context to each.
- Dashboard removal should deprecate `SHOWN_ON`/`VISUALIZES` links or source refs,
  not delete the metric if the metric still exists elsewhere.
- Dimension-scoped metrics are created only when the source names the dimension
  clearly.

## 6. Source Inputs, MCP Tools, Edge Vocabulary, And Write Policy

### 6.1 Primary offline source snapshot

Use `/Users/kushal/Desktop/kal/BC_2` as the primary offline source snapshot.
Use `docs/frd-docs` as the current local snapshot and compatibility fixture.

BC_2 source priority:

- `dashboard-v2/public/chart-registry.json`
  - chart semantics, formula text, how-to-read, narration, decisions answered;
- `openapi-1.json`
  - endpoint paths, schemas, API surface;
- `docs/metric-catalog/metrics_catalog.json`
  - reconciled catalog entries, dashboard/chart/metric coverage, formulas;
- `docs/metric-catalog/formula_reconciliation.json`
  - formula disagreement/audit source;
- `dbt/seeds/seed_config_metrics.csv`
  - central metric definitions and formulas;
- `dbt/seeds/seed_config_chart_metric_mapping.csv`
  - chart-to-metric mappings;
- `dbt/seeds/seed_config_metric_relationships.csv`
  - deterministic component relationships;
- `dbt/seeds/seed_config_ontology_metrics.csv`
  - richer ontology fields;
- `dbt/seeds/seed_config_ontology_causal_edges.csv`
  - candidate causal/compute edges, validation required;
- `dbt/seeds/seed_config_thresholds.csv`
  - threshold candidates and policy hooks.

Authored files under `data/skeleton/inputs/` are still allowed, but as:

- normalized exports from BC_2;
- hand-authored overrides;
- test fixtures;
- tenant-specific corrections.

Important validation rule: BC_2 ontology causal rows are not trusted blindly.
Some rows can be parser artifacts or SQL tokens. Every row must resolve both
endpoints to real metrics, pass source-kind validation, and carry provenance
before it becomes a proposal.

### 6.2 MCP/LLM proposal tools

Add or extend MCP tools for KG skeleton authoring. These tools can parse, score,
explain, and propose. They must not write graph edges directly.

Recommended tools:

- `inspect_bc2_sources`
  - summarize available BC_2 files, counts, hashes, and obvious gaps;
- `propose_metric_nodes_from_bc2`
  - produce meaningful scoped `Metric` proposals;
- `propose_metric_edges_from_formula`
  - produce `DECOMPOSES_INTO` candidates from formula/relationship sources;
- `propose_metric_to_spine_edges`
  - propose `SHOWN_ON`, `VISUALIZES`, `BELONGS_TO_DOMAIN`, and
    `PART_OF_PRODUCT` links;
- `propose_influence_candidates`
  - propose curated-rule `INFLUENCES` candidates for LLM judge/refuter;
- `validate_edge_candidate`
  - verify endpoint existence, scope, source kind, confidence policy, and
    review gate;
- `explain_edge_candidate`
  - return why the edge exists, which source rows support it, and why it is
    auto-safe or review-only.

Write boundary:

- tools emit proposal JSON;
- proposals go through review/arbitration;
- no tool bypasses `harness/kg/arbitration.py`.

### 6.3 Adopt two metric-edge types

For metric-to-metric edges, adopt the updated Claude two-type model:

- `DECOMPOSES_INTO`
- `INFLUENCES`

Retire these as metric-to-metric edge types in the target skeleton:

- `ROLLS_UP_TO`
- `CORRELATES_WITH`
- `CAUSES`

Those meanings move into `relation` values and edge properties. Existing graph
data can be migrated or rebuilt, but new skeleton output should use the two
metric-edge types.

Spine, surface, governance, and RBAC edges stay unchanged:

- `SHOWN_ON`
- `VISUALIZES`
- `BELONGS_TO_DOMAIN`
- `PART_OF_PRODUCT`
- `GOVERNED_BY`
- `HAS_THRESHOLD`
- ownership/access edges

### 6.4 `DECOMPOSES_INTO` relations

Use `DECOMPOSES_INTO` for deterministic or structural metric relationships:

- `relation: formula`
  - formula RHS component;
  - confidence `1.0`;
  - auto-safe if exact parsed formula/component;
- `relation: component`
  - a metric is a component of another derived metric, or a source surface maps
    exactly to the displayed metric;
  - confidence `1.0`;
  - auto-safe if directly parsed from chart/formula structure;
- `relation: identity`
  - canonical identity fallback;
  - confidence `1.0`;
  - review required in this phase;
- `relation: rollup`
  - non-additive or ratio-safe reaggregation;
  - review required;
- `relation: crossproduct`
  - additive scoped metric to blended/coarser metric;
  - confidence by scoring policy, usually `0.9`;
  - review required;
- `relation: funnel`
  - bounded stage progression template;
  - confidence by scoring policy, usually `0.85`;
  - review required.

Do not use alias edges in this phase. Use `Metric.aliases[]` and
`Metric.synonyms[]` for resolution.

### 6.5 `INFLUENCES` relations

Use `INFLUENCES` for causal or statistical candidate relationships:

- `relation: curated_rule`
  - generated by curated business mechanism rules;
  - LLM judge/refuter verifies;
  - review required;
- `relation: llm_verified`
  - accepted by judge/refuter/self-consistency over real endpoints;
  - review required;
- `relation: statistical`
  - measured or imported statistical association;
  - review required;
- `relation: statistical_candidate`
  - future `knowledgeGraph` discovered candidate;
  - review required;
- `relation: promoted`
  - future human/learning promoted high-confidence causal relation.

`CAUSES` is not emitted in this skeleton. Promotion can be represented as
`INFLUENCES {relation: promoted}` until the product explicitly reintroduces a
separate `CAUSES` edge type.

### 6.6 Auto-apply rules

Auto-apply through arbitration only:

- exact formula/component `DECOMPOSES_INTO`;
- exact UI/source-to-metric links already produced by the existing ingestion
  path, such as `SHOWN_ON` and `VISUALIZES`.

Review required:

- identity fallback edges;
- rollups;
- crossproduct edges;
- funnel/compositional candidates;
- aliases/equivalence suggestions;
- all `INFLUENCES`;

### 6.7 Edge scoring policy

Add a lightweight deterministic scoring policy at edge creation. This is not
the full append-only evidence ledger.

Recommended module:

- `harness/ingest/edge_scoring.py`

Scoring policy:

| Edge class | Confidence | Evidence mass | Review |
|---|---:|---:|---|
| `DECOMPOSES_INTO relation=formula` exact parse | 1.0 | deterministic/pinned | auto-safe |
| `DECOMPOSES_INTO relation=component` exact chart/formula containment | 1.0 | deterministic/pinned | auto-safe |
| `DECOMPOSES_INTO relation=identity` canonical fallback | 1.0 | deterministic/pinned | review |
| `DECOMPOSES_INTO relation=crossproduct` additive-safe | 0.9 default or source strength | 1.0 | review |
| `DECOMPOSES_INTO relation=rollup` ratio-safe reaggregation | 0.9 default or source strength | 1.0 | review |
| `DECOMPOSES_INTO relation=funnel` curated template | 0.85 default | 1.0 | review |
| `INFLUENCES relation=curated_rule` before judge | rule prior, usually 0.5-0.7 | 1.0 | review |
| `INFLUENCES relation=llm_verified` | Beta agreement fold | judge sample mass | review |
| `INFLUENCES relation=statistical` | measured/stat source policy | sample/stability/FDR-derived later | review |

Every scored edge proposal should carry:

- `confidence`
- `evidence_mass`
- `scoring_policy`
- `source_kind`
- `source_ref`
- `source_confidence`
- `review_state`
- `mechanism` where applicable
- `temporal_lag` for `INFLUENCES`

The future full evidence ledger can later recompute these values from events.

### 6.8 Causal claim discipline

- Formula edges are mathematics, not causality.
- Rollup edges are aggregation, not causality.
- Funnel rules are not automatically causality unless the rule explicitly
  represents a business mechanism and passes review.
- `INFLUENCES` is the main review-only causal candidate type.
- `CAUSES` is not emitted in this phase.

## 7. Implementation Phases

### Phase 0: Baseline and safety harness

Goal: capture current behavior before changing it.

Tasks:

- Run `uv run pytest harness/tests`.
- Export current edge counts by type.
- Export current metric counts by dashboard/scope/concept.
- Export current causal pass proposals for a representative dashboard set.
- Inspect BC_2 source files and write hashes/counts for the primary offline
  snapshot.
- Identify noisy BC_2 relationship rows that do not resolve to valid metrics.
- Add baseline artifacts:
  - `data/skeleton/baseline_edges.<tenant>.csv`
  - `data/skeleton/baseline_resolution.<tenant>.json`
  - `data/skeleton/bc2_source_inventory.<tenant>.json`

No behavior changes in Phase 0.

### Phase 1: BC_2 snapshot importer and metric normalization builder

Goal: turn BC_2 plus current dc-kg snapshots into meaningful scoped `Metric`
nodes and exact formula/component edges.

Add:

- `harness/ingest/bc2_snapshot.py`
- `harness/ingest/skeleton.py`
- `harness/ingest/edge_scoring.py`
- `harness/tests/test_skeleton.py`

Core functions:

- `load_bc2_sources(...)`
- `hash_source_files(...)`
- `normalize_bc2_metric_catalog(...)`
- `normalize_bc2_chart_mapping(...)`
- `validate_bc2_relationship_rows(...)`
- `split_formula_statements(formula_text)`
- `normalize_metric_concept(text)`
- `is_metric_lhs(lhs, context)`
- `extract_rhs_operands(rhs)`
- `derive_scope(entry_or_metric)`
- `build_metric_drafts(...)`
- `build_component_edges(...)`
- `write_skeleton_artifacts(...)`

CLI:

```bash
uv run kg import-bc2-snapshot --bc-path /Users/kushal/Desktop/kal/BC_2 --tenant rare_seeds --dry-run
uv run kg build-skeleton --tenant rare_seeds --dry-run
uv run kg build-skeleton --tenant rare_seeds --write-csv
uv run kg build-skeleton --tenant rare_seeds --apply-safe
```

Artifacts:

- `data/skeleton/source_inventory.<tenant>.json`
- `data/skeleton/bc2_metric_catalog.<tenant>.csv`
- `data/skeleton/bc2_relationship_candidates.<tenant>.csv`
- `data/skeleton/metric_registry.<tenant>.csv`
- `data/skeleton/deterministic_edges.<tenant>.csv`
- `data/skeleton/unresolved_operands.<tenant>.csv`
- `data/skeleton/filtered_terms.<tenant>.csv`
- `data/skeleton/coverage_report.<tenant>.json`

Phase 1 auto-apply:

- formula/component `DECOMPOSES_INTO` only;
- still through proposal approval/arbitration path or an explicit
  `--apply-safe` path that uses the same writer.

### Phase 2: Scope-safe deterministic edge builders

Goal: fix correctness of `harness/ingest/causal.py`.

Modify `ConceptIndex`:

- `resolve(concept, prefer_scope=None, prefer_platform=None)`
- `_best(uids, prefer_scope=None, prefer_platform=None)`
- exact scope first;
- platform next;
- broadest fallback last.

Modify formula builders:

- pass subject `scope_key`;
- use normalized meaningful `Metric` nodes where available;
- emit unresolved operand events.

Add identity fallback:

- `IDENTITIES`
- `identity_edges(metrics, index)`

Identity examples:

- `roas -> revenue, spend`
- `cpa -> spend, conversions`
- `cpc -> spend, clicks`
- `cpm -> spend, impressions`
- `ctr -> clicks, impressions`
- `cvr -> conversions, clicks`
- `aov -> revenue, orders`
- `frequency -> impressions, reach`

Modify rollups:

- add `_is_additive(metric)`;
- additive metrics can propose `DECOMPOSES_INTO {relation: crossproduct}`;
- ratios propose review-only `DECOMPOSES_INTO {relation: rollup}`;
- no ratio gets `aggregation_method: sum`.

Alias handling:

- expand `_ALIAS_GROUPS`;
- store aliases/synonyms on `Metric`;
- use aliases for resolution;
- do not emit alias edges in this phase.

### Phase 3: Curated cross-domain influence candidates

Goal: generate better cross-domain candidate coverage without random edges.

Add:

- `harness/seed/concept_causal_rules.json`

Rule shape:

```json
{
  "rule_id": "deliverability_to_campaign_engagement",
  "source_concepts": ["deliverability", "inbox_placement", "bounce_rate"],
  "target_concepts": ["campaign_engagement", "open_rate", "click_rate", "ctor"],
  "relation": "curated_rule",
  "direction": "source_to_target",
  "required_source_keywords": ["deliverability", "bounce", "spam", "inbox"],
  "required_target_keywords": ["open", "click", "engagement", "campaign"],
  "mechanism_template": "Deliverability constrains whether recipients can open or click campaign messages.",
  "allow_constraint_source": true,
  "review_required": true
}
```

Candidate flow:

1. Resolve source concepts through `ConceptIndex` and aliases.
2. Resolve target concepts through `ConceptIndex` and aliases.
3. Check required evidence text from formula, title, explanation, how-to-read,
   decisions answered, narration, category, and metric role.
4. Emit a candidate only when both endpoints resolve and evidence passes.
5. Send candidate to LLM judge/refuter.
6. Fold judge agreement into edge props.
7. Write `INFLUENCES` proposal as review-only.

MCP/LLM integration:

- expose this as a proposal-only tool;
- tool output must include source rows, evidence keywords, endpoints, scoring
  policy, and rejection reasons;
- no MCP tool writes the graph directly.

Important:

- `constraint` can be a source only through rules that explicitly allow it.
- Role compatibility alone must not create cross-domain edges.
- LLM cannot invent endpoints.
- Rejected candidates are logged with reason codes.

Reason codes:

- `alias_resolved`
- `missing_source_endpoint`
- `missing_target_endpoint`
- `missing_required_source_evidence`
- `missing_required_target_evidence`
- `constraint_source_allowed`
- `constraint_source_rejected`
- `judge_rejected`
- `refuter_rejected`

### Phase 4: Stale deterministic edge versioning

Goal: make reruns converge without losing auditability.

Add:

- `reconcile_edges()` in `harness/kg/reconcile.py`

Inputs:

- current deterministic computed set;
- source kinds eligible for reconciliation;
- run id;
- tenant/source scope;
- dry-run flag.

Eligible deterministic source kinds:

- `formula_parse`
- `metric_formula_parse`
- `component_parse`
- `identity_fallback`

Review-first source kinds that should not auto-deprecate:

- `scope_rollup`
- `curated_rule`
- `llm_proposal`
- `statistical_proposal`
- `kg_discovery`
- `manual_review`

Behavior:

- If live deterministic edge is absent from the computed set, set:
  - `status: deprecated`
  - `deprecated_at`
  - `deprecated_by_run`
  - `deprecation_reason`
- Do not delete by default.
- Do not touch human-reviewed causal edges.
- Emit diff artifacts:
  - `data/skeleton/edge_diff.<tenant>.<run_id>.json`
  - `data/skeleton/deprecated_edges.<tenant>.<run_id>.csv`

CLI:

```bash
uv run kg run-causal --dry-run
uv run kg run-causal --reconcile
```

### Phase 5: KG canvas app redesign

Goal: redesign the existing KG app for meaningful metrics, relation subtypes,
review gates, edge diffs, and traversal.

Scope:

- existing `app/kg-canvas` app only;
- no Thoughtlet UI;
- no Decision Capsule UI;
- no product shell for future layers.

Required views/components:

- Graph overview
  - shows meaningful metric nodes only;
  - links dashboards/UI components/source context to the metrics they display;
  - renders the two metric-edge types with relation-specific styling:
    `DECOMPOSES_INTO` and `INFLUENCES`.
- Metric detail panel
  - shows meaningful metric identity;
  - formula;
  - operands;
  - aliases/synonyms;
  - source endpoints;
  - domain/product/platform fields;
  - upstream/downstream summaries.
- Edge detail panel
  - shows type, relation, confidence, evidence mass, lag, mechanism,
    source_kind, review_state, status, and deprecation metadata.
- Review queue
  - separates auto-safe formula edges, review-required structural candidates,
    statistical candidates, and influence candidates.
  - supports approve/reject/edit.
  - shows why a candidate exists.
- Edge diff review
  - added edges;
  - unchanged edges;
  - deprecated edges;
  - skipped/unresolved candidates.
- Filter toolbar
  - edge type (`DECOMPOSES_INTO` / `INFLUENCES` plus spine/surface edges);
  - relation subtype;
  - review state;
  - status active/deprecated;
  - confidence range;
  - source kind;
  - dashboard/source context.
- Traversal mode
  - upstream causes;
  - downstream blast radius;
  - structural decomposition;
  - rollup view.

Frontend files likely touched:

- `app/kg-canvas/src/lib/api.ts`
- `app/kg-canvas/src/store.ts`
- `app/kg-canvas/src/lib/graphTheme.ts`
- `app/kg-canvas/src/lib/graphLayout.ts`
- `app/kg-canvas/src/components/CanvasView.tsx`
- `app/kg-canvas/src/components/NodeDetail.tsx`
- `app/kg-canvas/src/components/ReviewQueue.tsx`
- `app/kg-canvas/src/components/Toolbar.tsx`
- new `app/kg-canvas/src/components/EdgeDetail.tsx`
- new `app/kg-canvas/src/components/EdgeDiffReview.tsx`

Backend API additions:

- include relation/status/deprecation props in graph payload;
- expose skeleton coverage report;
- expose edge diff report;
- expose traversal endpoints later in this phase:
  - `GET /api/traverse/upstream?metric_uid=...`
  - `GET /api/traverse/downstream?metric_uid=...`

### Phase 6: Statistical evidence feed, later

Goal: use `knowledgeGraph` only as a statistical evidence producer.

Not part of first code pass unless explicitly requested later.

Future importer:

```bash
uv run kg import-discovered-edges --tenant rare_seeds --csv data/discovered_edges.rare_seeds.csv
```

Mapping:

- FDR-passing temporal associations -> review-only
  `INFLUENCES {relation: statistical}`
- high-quality conditioned/oriented survivors -> review-only
  `INFLUENCES {relation: statistical_candidate}`

Carry:

- correlation or conditional correlation;
- p-value;
- lag;
- sample size;
- method;
- FDR flag;
- stability;
- evidence mass.

Do not import:

- `knowledgeGraph` structural formula edges;
- `knowledgeGraph` alias edges;
- `knowledgeGraph` crossproduct edges;
- `knowledgeGraph` flat graph identity.

### Phase 7: FRD SourceProfile ingestion, later

Goal: move from checked-in snapshots to FRD-compliant adaptive ingestion.

Not part of first code pass.

Future work:

- live OpenAPI acquisition;
- content hash;
- endpoint family classification;
- SourceProfile storage;
- SourceProfile human review;
- deterministic harvester;
- changed-family drift diff;
- multi-source registry;
- autonomous deterministic mode.

Current pilot stance:

- `docs/frd-docs/openapi.json` and `chart-registry.json` are source snapshots.
- `master-config` remains excluded.
- The FRD relationship endpoint conflict is documented, not implemented.

## 8. File-Level Work Plan

### New files

- `harness/ingest/bc2_snapshot.py`
- `harness/ingest/skeleton.py`
- `harness/ingest/edge_scoring.py`
- `harness/tests/test_skeleton.py`
- `harness/seed/concept_causal_rules.json`
- `harness/seed/tenants/rare_seeds/skeleton_overrides.json`
- `data/skeleton/inputs/` normalized BC_2 exports and optional overrides
- `app/kg-canvas/src/components/EdgeDetail.tsx`
- `app/kg-canvas/src/components/EdgeDiffReview.tsx`

### Modified files

- `harness/ingest/causal.py`
  - scope-aware `ConceptIndex`;
  - identity edges;
  - additive-safe rollups;
  - alias expansion;
  - curated rule candidates;
  - relation props;
  - run summaries.
- `harness/kg/models.py`
  - meaningful metric identity/source fields if needed;
  - `EDGE_TYPES` target for metric-to-metric edges: `DECOMPOSES_INTO` and
    `INFLUENCES`;
  - relation/scoring/deprecation prop support if validation is too strict.
- `harness/kg/reconcile.py`
  - deterministic edge deprecation/versioning.
- `harness/cli/kg.py`
  - `import-bc2-snapshot`;
  - `build-skeleton`;
  - `run-causal --dry-run`;
  - `run-causal --reconcile`;
  - `migrate-metric-edges`;
  - separate subject/candidate limits if needed.
- `harness/mcp/graph_server.py`
  - proposal-only skeleton tools for BC_2 inspection, node/edge candidate
    generation, validation, and explanation.
- `harness/api/server.py`
  - skeleton coverage endpoints;
  - edge diff endpoints;
  - graph payload relation/deprecation support.
- `harness/tests/test_causal.py`
  - update and extend for scope/additivity/rules.
- `app/kg-canvas/*`
  - redesign existing KG app around meaningful metrics, relation filters, review
    gates, and edge diff review.

## 9. Test Plan

### Unit tests

Add tests for:

- BC_2 source inventory and hash generation;
- BC_2 metric catalog normalization;
- BC_2 chart metric mapping normalization;
- BC_2 relationship row validation and noisy-row rejection;
- formula splitting;
- multi-line formula parsing;
- non-metric LHS filtering;
- canonical RHS operands;
- meaningful metric identity;
- chart/table/card entries not becoming metrics unless they map to a real
  metric;
- UI/source context linking to metrics;
- same-scope formula resolution;
- Google Search not linking to YouTube/blended operands;
- identity fallback;
- unresolved operand logging;
- additive classification;
- no ratio crossproduct sum;
- alias metadata resolution;
- curated cross-domain rule resolution;
- `constraint` allowed only through explicit rules;
- LLM candidate endpoint validation;
- rejected candidate reason codes;
- stale edge diff/deprecation.
- edge scoring policy by source class.
- MCP/proposal tools never direct-write.

### Integration tests

Add tests or smoke checks for:

```bash
uv run kg import-bc2-snapshot --bc-path /Users/kushal/Desktop/kal/BC_2 --tenant rare_seeds --dry-run
uv run kg build-skeleton --tenant rare_seeds --dry-run
uv run kg build-skeleton --tenant rare_seeds --write-csv
uv run kg build-skeleton --tenant rare_seeds --apply-safe
uv run kg run-causal --dry-run
uv run kg run-causal --reconcile
uv run kg migrate-metric-edges --dry-run
uv run pytest harness/tests
```

### UI checks

Use Playwright or equivalent to verify:

- graph renders meaningful metric nodes;
- filters do not blank the canvas unexpectedly;
- edge detail opens from graph click;
- review queue separates edge classes;
- edge diff view shows added/unchanged/deprecated/skipped;
- mobile layout has no text overlap;
- dark/light theme still works if retained.

## 10. Acceptance Criteria

The KG skeleton implementation is done when:

- BC_2 source inventory exists with file hashes/counts and validation results;
- only meaningful business metrics are created as `Metric` nodes;
- charts/tables/cards are represented as UI/source context, not separate metric
  tiers;
- formula/component edges are scoped correctly;
- Google Search does not decompose into YouTube or blended operands;
- ratio metrics are not summed in crossproduct rollups;
- canonical identity fallback creates reviewable or exact-safe edges;
- unresolved operands are reported, not silently dropped;
- cross-domain influence candidates exist for curated rules and pass judge/refuter
  before review;
- `constraint` source metrics are only used where a rule permits them;
- formula/component exact-safe edges can be auto-applied through arbitration;
- all other edge classes require review;
- stale deterministic edges are deprecated/versioned;
- confidence/evidence_mass/scoring_policy/lag/mechanism/source props are present
  where required;
- metric-to-metric target output uses only `DECOMPOSES_INTO` and `INFLUENCES`;
- MCP/LLM tools emit proposals only and cannot bypass arbitration;
- KG canvas redesign can inspect meaningful metrics, edge relations, review reasons,
  and edge diffs;
- Thoughtlets and Decision Capsules are not implemented in this phase.

## 11. Deferred But Documented

These are explicitly not in the first KG skeleton implementation:

- alias edges;
- full append-only evidence ledger;
- `knowledgeGraph` statistical importer;
- live SourceProfile ingestion;
- FRD master-config relationship endpoint ingestion;
- Thoughtlets;
- Decision Capsules;
- Monitoring Contracts;
- Context Pack;
- execution/actions runtime;
- automatic `CAUSES` creation.

## 12. Next Implementation Order

Recommended order:

1. Baseline current graph and tests.
2. Implement BC_2 snapshot inventory/import normalization.
3. Implement `harness/ingest/skeleton.py`.
4. Add deterministic edge scoring policy.
5. Add metric identity/source model support if required.
6. Add formula/component auto-safe proposal generation.
7. Fix `ConceptIndex` scope-aware resolution.
8. Add identity fallback and additive-safe rollup candidates under
   `DECOMPOSES_INTO` relations.
9. Add curated cross-domain rule candidates with LLM judge/refuter.
10. Add stale deterministic edge deprecation/versioning.
11. Add metric-edge migration/rebuild path to the two-type model.
12. Add proposal-only MCP tools for BC_2 parsing, edge validation, and edge
   explanation.
13. Redesign KG canvas for meaningful metrics, relation filters, review queue, edge
   details, and edge diff review.
14. Run full test and UI verification.

This order keeps the graph correct before increasing causal coverage, and keeps
the UI aligned with the new review and diff workflow.
