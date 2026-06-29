# Finalised ThoughtWire Graph Schema - Codex

Status: final V1 implementation contract  
DB target: Neo4j  
Tenancy model: one Neo4j database per tenant  
Final V1 stance: compact six-label graph

This document finalises the ThoughtWire knowledge graph schema after comparing:

- `docs/frd-docs/thoughtwire-frd.md`
- `docs/thoughtwire-kg-schema-codex.md`
- `docs/thoughtwire-kg-v1-node-details-codex.html`
- `docs/thoughtwire-kg-schema-claude.md`
- `docs/thoughtwire-kg-schema-claude.html`
- `docs/thoughtwire-kg-schema-codex.html`
- `docs/old implementation/*.md`

## 1. Final Verdict

The best V1 implementation is the compact Codex six-label schema from `thoughtwire-kg-schema-codex.md`, reinforced by the visual node-detail model in `thoughtwire-kg-v1-node-details-codex.html`.

Use exactly these Neo4j node labels in V1:

1. `Metric`
2. `Dashboard`
3. `UIComponent`
4. `Policy`
5. `Threshold`
6. `Role`

Everything else is a field, edge property, external runtime concern, non-graph ledger, or V2 graph label.

### Which Existing Document Is Best

| Document | Verdict | Reason |
|---|---|---|
| `thoughtwire-kg-schema-codex.md` | Best implementation baseline | Clean six-label V1 contract, practical RBAC, endpoint field merge, Neo4j constraints, and build checklist. |
| `thoughtwire-kg-v1-node-details-codex.html` | Best visual V1 explanation | Clearly communicates the six operational labels and merged-field strategy. |
| `thoughtwire-kg-schema-claude.md` | Best analytical reference | Strong reasoning on causal edges, evidence scoring, type corrections, and long-term learning, but it reintroduces `IntelligenceProduct` and `Domain` as supporting nodes, which makes V1 less compact. |
| `thoughtwire-kg-schema-claude.html` | Useful visual analysis | Good for stakeholder explanation, but its supporting product/domain node model should not be the final V1 implementation contract. |
| `thoughtwire-kg-schema-codex.html` | Stale | Still shows a broad future-state graph with tenant/product/domain/concept/endpoint nodes and an incorrect hash-match claim. |
| `old implementation/*.md` | Historical only | NetworkX/Snowflake DAG architecture conflicts with the current FRD requirement for live OpenAPI ingestion, governed mutation, temporal-lagged edges, and evidence-ledger confidence. |

## 2. Source Facts Verified

Checked local files:

| Source | Verified fact |
|---|---:|
| `docs/frd-docs/openapi.json` paths | 902 |
| OpenAPI operations | 923 |
| OpenAPI GET operations | 877 |
| OpenAPI non-GET operations | 46 |
| OpenAPI component schemas | 463 |
| `docs/frd-docs/chart-registry.json` entries | 646 |
| Chart registry entries with narration text | 558 |
| Chart registry entries with required chart metadata | 646 |

Important OpenAPI endpoint confirmed:

```text
GET /api/v1/master-config/config/knowledge-graph/relationships
```

This endpoint must be treated as a primary deterministic edge source for V1 ingestion. It is not a normal business metric endpoint, but it is also not disposable control-plane noise.

## 3. What Is Missing From The Project

The current workspace contains documentation only. There is no application source code in `/Users/kushal/Desktop/kal/dc-kg`, and this directory is not currently a Git repository from the shell's point of view.

Missing documentation and implementation pieces:

| Gap | Impact |
|---|---|
| No Markdown counterpart for `thoughtwire-kg-v1-node-details-codex.html` | The best visual V1 node-detail artifact is not editable/reviewable as Markdown. |
| Doc drift across Codex and Claude files | Different files disagree on whether product/domain/endpoint/concept are V1 nodes or fields. |
| Stale broad Codex HTML | It still describes a future-state graph, not the compact V1 implementation. |
| No Neo4j implementation files | Constraints, indexes, load scripts, and graph read/write code are absent from this workspace. |
| No SourceProfile ingestion implementation | FRD-required adaptive OpenAPI classification is not present here. |
| No deterministic harvester | There is no code that executes an approved SourceProfile and emits proposals. |
| No arbitration writer | FRD requires proposals to be deduped, validated, and promoted by one writer; that implementation is missing. |
| No relationship-endpoint ingestion | The confirmed `master-config/config/knowledge-graph/relationships` endpoint is not wired into a final V1 build. |
| No completeness coverage report | FRD requires dashboard/metric coverage reconciliation so "every metric is in the graph" is checkable. |
| No RBAC-before-context enforcement | The docs specify it, but no application path enforces it here. |
| No evidence-ledger scoring implementation | Causal edge confidence must be folded from evidence records, not typed directly. |

## 4. V1 Implementation Contract

### 4.1 Node Labels

These are the only V1 graph labels:

| Label | Purpose |
|---|---|
| `Metric` | Business signal, source lineage, endpoint references, formula/explanation, causal role, governance summary. |
| `Dashboard` | Product/domain surface grouping and access boundary. |
| `UIComponent` | Chart, card, table, KPI, or narrative surface, primarily sourced from the chart registry. |
| `Policy` | Monitoring, interpretation, access, escalation, or action rule. |
| `Threshold` | Numeric, band, percentile, or seasonal boundary for metric health. |
| `Role` | Graph-native RBAC subject for context filtering, editing, and approval. |

### 4.2 Not V1 Labels

| Candidate | V1 treatment |
|---|---|
| `Tenant` | Runtime/database context. One Neo4j database per tenant. |
| `IntelligenceProduct` / `Product` | `product_id` field and permission edge scopes. |
| `Domain` | `domain_id`, `department`, and permission edge scopes. |
| `MetricConcept` | `concept_key`, `concept_name`, `metric_base`, `aliases`. |
| `Endpoint` | Endpoint fields on `Metric`, `Dashboard`, `UIComponent`, plus `EXPOSED_BY` edge metadata. |
| `Connector` | `connector_ids`, `source_platforms`, `source_set`, `mart_sources`. |
| `Principal` / `Person` | External auth maps the session to one or more `role_key` values. |
| `Formula` / `Dimension` | Fields in V1; promote later only if traversal is needed. |
| `EvidenceEvent` | Required evidence records for scoring, but not a V1 graph label. |
| `DecisionCapsule`, `Thoughtlet`, `MonitoringContract`, `LearningCandidate`, `PromotedMemory`, `GraphVersion`, `Action`, `Tool`, `ApprovalRule` | V2 graph labels or separate ledgers/services. |

## 5. Metric Identity Rules

The FRD requires canonical-first metric identity.

Rules:

1. Use the central-library metric id as the primary `Metric.metric_id` whenever a dashboard metric resolves to the central library.
2. A metric exposed by many dashboards must be one `Metric` node linked to many dashboards/components.
3. If a dashboard-local raw id does not resolve to a central metric, namespace it as `<dashboard>-<id>`, for example `meta-overview-roas`.
4. Raw API paths are never identities. Store them as endpoint fields or `EXPOSED_BY` relationship metadata.
5. Use `concept_key` to group semantically equivalent metrics and rollups without adding a `MetricConcept` label.

Recommended `Metric` identity fields:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `metric_id` | string | yes | Stable canonical id inside the tenant DB. |
| `canonical_key` | string | yes | Machine-safe lookup key, usually derived from the central id. |
| `display_name` | string | yes | Human-readable label. |
| `concept_key` | string | yes | Shared semantic group, such as `revenue`, `roas`, `orders`. |
| `aliases` | string[] | recommended | Path slugs, registry ids, synonyms. |
| `product_id` | string | yes | Product ownership as a field, not a node. |
| `domain_id` | string | yes | Business domain as a field, not a node. |
| `card_endpoint_path` | string | recommended | Current/scalar metric endpoint. |
| `series_endpoint_path` | string | recommended | Trend/chart endpoint. |
| `openapi_schema_refs` | string[] | optional | Response schema references. |

## 6. V1 Edge Catalog

Use explicit edge names. Do not add V1 edges that require non-V1 node labels.

| Edge | From -> To | Purpose | Required/important properties |
|---|---|---|---|
| `CONTAINS_COMPONENT` | `Dashboard -> UIComponent` | Dashboard composition | `section_id`, `order`, `visibility`, `source`, `confidence` |
| `VISUALIZES` | `UIComponent -> Metric` | Component shows a metric | `match_type`, `axis_role`, `confidence`, `source` |
| `SHOWN_ON` | `Metric -> Dashboard` | Metric appears on a dashboard | `is_primary`, `source`, `confidence` |
| `EXPOSED_BY` | `Metric -> Dashboard|UIComponent` | Existing surface exposes/fetches the metric | `endpoint_path`, `endpoint_role`, `method`, `source_profile_id`, `schema_ref`, `confidence` |
| `HAS_THRESHOLD` | `Metric -> Threshold` | Metric boundary | `is_default`, `segment_context`, `priority`, `confidence` |
| `GOVERNED_BY` | `Metric -> Policy` | Policy applies to metric | `priority`, `effective_from`, `effective_to`, `status` |
| `EXPLAINS_THRESHOLD` | `Policy -> Threshold` | Policy explains threshold intent | `explanation_type`, `confidence` |
| `OWNS` | `Role -> Metric|Policy|Threshold|Dashboard` | Accountable owner | `ownership_type`, `priority`, `source` |
| `INFLUENCES` | `Metric -> Metric` | Plausible driver relationship | `confidence`, `evidence_mass`, `lag_min_hours`, `lag_max_hours`, `mechanism`, `review_state` |
| `CAUSES` | `Metric -> Metric` | Approved causal relationship | `confidence`, `evidence_mass`, `lag_min_hours`, `lag_max_hours`, `mechanism`, `review_state` |
| `CORRELATES_WITH` | `Metric -> Metric` | Statistical association only | `correlation`, `p_value`, `lag_hours`, `sample_size`, `source` |
| `INHERITS_FROM` | `Role -> Role` | Permission inheritance | `priority`, `source` |
| `CAN_VIEW_METRIC` | `Role -> Metric` | Metric visibility/traversal | common permission properties |
| `CAN_VIEW_DASHBOARD` | `Role -> Dashboard` | Dashboard visibility | common permission properties |
| `CAN_VIEW_COMPONENT` | `Role -> UIComponent` | Component visibility | common permission properties |
| `CAN_EDIT_POLICY` | `Role -> Policy` | Policy edit permission | common permission properties |
| `CAN_EDIT_THRESHOLD` | `Role -> Threshold` | Threshold edit permission | common permission properties |
| `CAN_APPROVE_CHANGE` | `Role -> Policy|Threshold` | Approval authority | common permission properties |

Common permission edge properties:

| Property | Type | Purpose |
|---|---|---|
| `effect` | enum | `allow` or `deny`; explicit deny wins. |
| `permission` | string | `view`, `explain`, `traverse`, `edit`, `approve`, `manage`. |
| `priority` | integer | Conflict resolution. |
| `allowed_fields` | string[] | Fields the role may see. |
| `masked_fields` | string[] | Fields always redacted. |
| `product_scope_ids` | string[] | Product scoping without product nodes. |
| `domain_scope_ids` | string[] | Domain scoping without domain nodes. |
| `row_filter_json` | json | Segment filters such as platform, region, campaign, SKU. |
| `condition_json` | json | Time windows, environment checks, approval conditions. |
| `approval_required` | boolean | Whether an edit/request needs approval. |
| `valid_from`, `valid_to` | datetime | Permission lifecycle. |

## 7. Ingestion Policy

The final V1 must follow the FRD ingestion model:

1. Acquire an OpenAPI 3.x spec from a URL, base URL, or file upload.
2. Hash the spec and group operations into endpoint families.
3. Auto-exclude obvious infrastructure endpoints.
4. Run the Ingestion Brain once per changed spec hash to produce a `SourceProfile`.
5. Let a human review and override the SourceProfile.
6. Run a deterministic harvester from the approved SourceProfile.
7. Emit proposals only. The harvester must not write directly to Neo4j.
8. Let arbitration dedupe, validate, promote, deprecate disappeared entities, and write the graph.
9. Emit a completeness coverage report per ingestion run.

### Endpoint Treatment

| Endpoint family | V1 treatment |
|---|---|
| Dashboard metric endpoints | Promote/update `Metric`, `Dashboard`, `SHOWN_ON`, `EXPOSED_BY`. |
| Dashboard chart endpoints | Promote/update `UIComponent`, `VISUALIZES`, `EXPOSED_BY`. |
| Dashboard metadata endpoints | Promote/update `Dashboard` fields. |
| Chart registry entries | Promote/update `UIComponent`; resolve `VISUALIZES` after classification/review. |
| `master-config/config/metrics` | Central metric library source for canonical identity. |
| `master-config/config/knowledge-graph/relationships` | Primary deterministic edge source. |
| `master-config/config/thresholds` and policy surfaces | Governance evidence/config for `Threshold` and `Policy`. |
| Auth/admin/docs/health/status endpoints | Exclude unless explicitly human-overridden. |
| POST/PUT/PATCH/DELETE endpoints | Exclude from V1 business graph harvest; later candidates for governed `Tool`/`Action`. |

### Relationship Endpoint Rule

The relationship endpoint is not optional. It must feed deterministic proposals for metric-to-metric relationships. Those proposals still flow through the evidence ledger and review rules:

- formula decomposition can become deterministic high-confidence relationships;
- ontology/catalog relationships can become `INFLUENCES` or reviewed `CAUSES`;
- correlations must enter as `CORRELATES_WITH` unless reviewed/promoted;
- no correlation may auto-promote into causality.

## 8. Evidence Scoring

V1 must implement evidence scoring now, even if evidence records are stored outside Neo4j.

Requirement:

```text
alpha = 0.5
beta = 0.5
for each supporting evidence record: alpha += weight
for each refuting evidence record: beta += weight
confidence = alpha / (alpha + beta)
evidence_mass = alpha + beta
```

Edge confidence must never be typed in manually as final truth. Agents, humans, statistical jobs, and imported relationship sources may add evidence. They may not overwrite the folded score.

Suggested evidence tiers:

| Tier | Weight | Notes |
|---|---:|---|
| `prior` / `llm` | 1 | Hypothesis or imported weak prior. |
| `observational` | 2-5 | Correlation/statistical evidence, scaled by quality. |
| `quasi_experimental` | 5 | Natural experiment or diff-in-diff. |
| `interventional` | 8 | Approved action moved a lever and result followed. |
| `human` | 10 | Domain expert confirmation/refutation. |
| `outcome` | 8 | Monitoring contract predicted-vs-actual reconciliation. |
| `formula` | pinned 1.0 | Deterministic math edge, outside the fold. |

Traversal path score:

```text
path_score = product(edge.confidence) * product(lag_plausibility) * min(data_quality)
```

Always expose both `confidence` and `evidence_mass` in agent context. A score of `0.80` from one weak source is not equivalent to `0.80` from many outcome-confirmed observations.

## 9. RBAC Before Context

The application must not run unrestricted graph traversal and filter afterward.

Correct sequence:

1. Authenticate the user/service outside Neo4j.
2. Resolve one or more `role_key` values.
3. Resolve inherited roles.
4. Query only allowed graph neighborhoods.
5. Apply field masking, product/domain scopes, and row filters.
6. Send only allowed context to the agent.

If a causal path crosses restricted territory, the agent may say that a restricted dependency exists, but it must not reveal the hidden node's name, value, endpoint, policy, or owner.

## 10. Neo4j Constraints And Indexes

V1 constraints:

```cypher
CREATE CONSTRAINT metric_id IF NOT EXISTS
FOR (n:Metric) REQUIRE n.metric_id IS UNIQUE;

CREATE CONSTRAINT dashboard_id IF NOT EXISTS
FOR (n:Dashboard) REQUIRE n.dashboard_id IS UNIQUE;

CREATE CONSTRAINT component_id IF NOT EXISTS
FOR (n:UIComponent) REQUIRE n.component_id IS UNIQUE;

CREATE CONSTRAINT policy_id IF NOT EXISTS
FOR (n:Policy) REQUIRE n.policy_id IS UNIQUE;

CREATE CONSTRAINT threshold_id IF NOT EXISTS
FOR (n:Threshold) REQUIRE n.threshold_id IS UNIQUE;

CREATE CONSTRAINT role_key IF NOT EXISTS
FOR (n:Role) REQUIRE n.role_key IS UNIQUE;
```

Recommended indexes:

```cypher
CREATE INDEX metric_product_domain IF NOT EXISTS
FOR (n:Metric) ON (n.product_id, n.domain_id);

CREATE INDEX metric_concept_scope IF NOT EXISTS
FOR (n:Metric) ON (n.concept_key, n.scope);

CREATE INDEX metric_source IF NOT EXISTS
FOR (n:Metric) ON (n.source_set);

CREATE INDEX dashboard_product IF NOT EXISTS
FOR (n:Dashboard) ON (n.product_id, n.domain_id);

CREATE INDEX component_dashboard IF NOT EXISTS
FOR (n:UIComponent) ON (n.dashboard_id);

CREATE INDEX policy_metric IF NOT EXISTS
FOR (n:Policy) ON (n.metric_id, n.is_active);

CREATE INDEX role_auth IF NOT EXISTS
FOR (n:Role) ON (n.auth_role, n.status);
```

Do not create V1 constraints for non-label concepts.

## 11. V2 Graph Labels And Why They Are Deferred

The FRD requires these concepts, but they should not become V1 graph labels until the compact context graph is trusted.

| V2 label/concept | FRD purpose | Deferral reason |
|---|---|---|
| `Thoughtlet` | Atomic observation anchored to graph nodes. | Requires the V1 graph to exist first. |
| `DecisionCapsule` | Append-mostly decision memory. | Belongs to the decision ledger layer, not the initial structural KG. |
| `MonitoringContract` | Watches an executed decision as a bundle. | Requires execution and capsule lifecycle. |
| `LearningCandidate` / `PromotedMemory` | Governed learning pipeline. | Needs approval/outcome workflows. |
| `GraphVersion` | Reconstruct what the graph believed at decision time. | Add when graph mutation starts. |
| `Action` / `Tool` | Sanctioned real-world execution. | POST/PUT/PATCH/DELETE surfaces are excluded from V1 harvest. |
| `ApprovalRule` | Court/approval path logic. | Start with Role permission edges; promote if approval traversal becomes complex. |
| `EvidenceEvent` / `CausalRelation` | Reified evidence and causal relation history. | Evidence scoring is required now, but records may live outside Neo4j until independently queryable. |

## 12. Build Order

1. Create the six Neo4j constraints and recommended indexes.
2. Implement SourceProfile storage keyed by spec hash.
3. Implement deterministic endpoint-family exclusion and classification review.
4. Implement deterministic harvest that emits proposals only.
5. Implement arbitration as the only graph writer.
6. Load `Dashboard` and `UIComponent` from chart registry and OpenAPI surface discovery.
7. Load `Metric` using canonical-first identity from central library and dashboard-local fallback.
8. Create `SHOWN_ON`, `EXPOSED_BY`, `CONTAINS_COMPONENT`, and `VISUALIZES` surface edges.
9. Load reviewed `Policy` and `Threshold` nodes and governance edges.
10. Seed `Role` nodes and permission edges.
11. Enforce RBAC-before-context in every agent read path.
12. Ingest `master-config/config/knowledge-graph/relationships` as deterministic edge proposals.
13. Implement evidence-ledger scoring and causal traversal with lag and evidence mass.
14. Emit ingestion coverage reports.
15. Defer memory, monitoring, action, and approval graph labels until V1 is trusted.

## 13. Acceptance Criteria

- The V1 graph contains no node labels outside `Metric`, `Dashboard`, `UIComponent`, `Policy`, `Threshold`, and `Role`.
- Product, domain, endpoint, connector, metric concept, tenant, and principal are not graph labels in V1.
- Metric identity is canonical-first with dashboard-local namespacing only as fallback.
- Every promoted `UIComponent` has a chart registry or OpenAPI source reference.
- Every endpoint reference is a field or `EXPOSED_BY` edge property, not an `Endpoint` node.
- Every agent context query is role-filtered before context leaves Neo4j/application code.
- Every causal edge has confidence, evidence mass, temporal lag, mechanism, review state, and source attribution.
- Correlation never auto-promotes to causality.
- Every ingestion run emits proposal counts, promotion counts, exclusion counts, and coverage gaps.
- Disappeared entities are deprecated, not deleted.

## 14. Final Implementation Choice

The proper V1 KG is the compact Codex implementation, amended with the FRD ingestion and scoring requirements that were missing from the compact docs.

In one sentence:

```text
Build a six-label, tenant-isolated Neo4j graph that gives agents role-filtered metric, surface, policy, threshold, and causal context, while keeping endpoint/product/domain/concept/user/memory complexity outside the V1 graph until there is real traversal pressure.
```
