# Decision Canvas OS - Module Architecture & Data Flow

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          POLICY BREACH DETECTED                              │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                           CONTEXT PACKAGER                                   │
│  (src/context_packager.py) - Builds complete agent injection payload         │
│                                                                               │
│  Input:  BreachContext(metric_id, severity, observed_value, ...)           │
│  Output: ContextPayload {                                                    │
│            metric_context (current + historical values),                    │
│            causal_graph (upstream/downstream dependencies),                 │
│            api_registry (828 endpoints with params),                        │
│            runbook_content (investigation playbook),                        │
│            related_incidents (from Beads memory)                            │
│          }                                                                    │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                          AGENT ROUTER                                        │
│        (src/api/agent_stream.py - Route to reactive vs proactive)           │
│                                                                               │
│         Message Type Analysis:                                              │
│         ├── "Why did..." / "investigate" / "root cause"  → PROACTIVE       │
│         ├── "What's our..." / "How much..." / "When"    → REACTIVE        │
│         └── Resume {packet_id}                           → PROACTIVE (resume)│
│                                    ↓                                          │
│         ┌─────────────────────────────────────────────┐                    │
│         │ SELECT APPROPRIATE SYSTEM PROMPT            │                    │
│         ├─────────────────────────────────────────────┤                    │
│         │ REACTIVE:                                   │                    │
│         │ - Answer questions with markdown            │                    │
│         │ - Use inline data tables                     │                    │
│         │ - No Decision Packets                        │                    │
│         │ - Page context aware                         │                    │
│         │                                              │                    │
│         │ PROACTIVE:                                  │                    │
│         │ - Deep investigation protocol                │                    │
│         │ - Walk causal graph                          │                    │
│         │ - Generate Decision Packet                   │                    │
│         │ - Multi-step approval routing                │                    │
│         └─────────────────────────────────────────────┘                    │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                        RUNTIME SELECTION                                     │
│        (src/runtime/__init__.py - Get runtime environment)                   │
│                                                                               │
│  ┌────────────────────────┐         ┌────────────────────────┐             │
│  │   ANTHROPIC_API_KEY    │         │  NO ANTHROPIC_API_KEY  │             │
│  │      IS SET            │         │   (Use Claude Code)    │             │
│  │          ↓             │         │          ↓             │             │
│  │  DirectAnthropicClient │         │  SubprocessClient      │             │
│  │                        │         │                        │             │
│  │ - Direct API call      │         │ - Spawn subprocess     │             │
│  │ - No MCP stdio needed  │         │ - MCP via pipes        │             │
│  │ - Tool routing in-proc │         │ - Windows path resolve │             │
│  │ - SSE streaming        │         │ - SSE streaming        │             │
│  └────────────────────────┘         └────────────────────────┘             │
│                │                              │                             │
│                └──────────────┬───────────────┘                             │
│                               ↓                                              │
│          Both implement: stream(prompt, message, tools) → AsyncIterator    │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                      CLAUDE AGENT EXECUTION                                  │
│                    (Streaming via SSE events)                                │
│                                                                               │
│  Available Tools:                                                           │
│  ├── get_metric(metric_id, filters) → Current value                        │
│  ├── get_upstream_causes(metric_id, depth) → Causal DAG traversal         │
│  ├── get_downstream_effects(metric_id) → Impact analysis                   │
│  ├── search_metrics(query) → Entity resolution                             │
│  ├── get_causal_path(from, to) → Explain relationships                    │
│  ├── list_runbooks() → Investigation playbooks                             │
│  ├── search_beads("[keywords]") → Similar past incidents                   │
│  └── forecast_impact(current, expected) → Revenue projections              │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                      INVESTIGATION PROTOCOL (Proactive)                      │
│                                                                               │
│  STEP 1: Triage (5s)                                                        │
│  ├─ Classify severity                                                       │
│  ├─ Search Beads for similar incidents                                      │
│  └─ Route to specialist agent                                               │
│                                                                               │
│  STEP 2: Data Quality Check (30s)                                           │
│  ├─ Verify freshness (Magento 1h, Google 4h, etc.)                        │
│  ├─ Check completeness (>95%)                                              │
│  └─ If invalid: STOP & escalate                                            │
│                                                                               │
│  STEP 3: Decompose Metric                                                   │
│  ├─ If ratio (ROAS), query numerator & denominator separately             │
│  └─ Identify anomalous component                                            │
│                                                                               │
│  STEP 4: Walk Causal Graph                                                  │
│  ├─ get_upstream_causes(metric_id, depth=5)                               │
│  ├─ Find where anomaly originates                                           │
│  └─ Build path: [root cause] → [intermediate] → [symptom]                 │
│                                                                               │
│  STEP 5: Isolate by Dimension                                               │
│  ├─ Drill: channel (Google/Meta), device (mobile/desktop)                │
│  ├─ Region, campaign, product                                              │
│  └─ Calculate contribution to total impact                                  │
│                                                                               │
│  STEP 6: Forecast Impact                                                    │
│  ├─ 24h, 7d, 30d revenue projections                                      │
│  └─ Trend: improving, stable, or degrading                                │
│                                                                               │
│  STEP 7: Recommend Actions                                                  │
│  ├─ Match root cause to runbook actions                                    │
│  ├─ Calculate risk level (low/medium/high)                                │
│  ├─ Estimate confidence (0-100%)                                           │
│  └─ Project impact if action taken                                         │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                        CAPSULE VALIDATOR                                     │
│  (src/capsule_validator.py - Validate Decision Packet schema)                │
│                                                                               │
│  Input: Raw Decision Packet JSON from agent                                 │
│                                                                               │
│  Validation Checks:                                                         │
│  ├─ All required fields present                                            │
│  ├─ Numeric values valid (no NaN, Inf)                                     │
│  ├─ Metric IDs exist in KG                                                 │
│  ├─ Risk levels in {low, medium, high}                                     │
│  ├─ Confidence in [0, 1]                                                    │
│  ├─ Impact math verified                                                    │
│  └─ Approver roles exist                                                    │
│                                                                               │
│  Output: Validated ✓ or Rejected ✗ with error details                     │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                      APPROVAL ROUTER                                         │
│  (src/approval_router.py - Route through approval chains)                    │
│                                                                               │
│  Risk Assessment:                                                           │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │ RISK LEVEL │ CONFIDENCE │ AUTONOMY │ APPROVAL CHAIN            │     │
│  ├──────────────────────────────────────────────────────────────────┤     │
│  │ LOW        │ Any        │ AUTO     │ (Immediate execution)     │     │
│  │ MEDIUM     │ ≥80%       │ PENDING  │ analyst → manager         │     │
│  │ MEDIUM     │ <80%       │ ALERT    │ (human decision)          │     │
│  │ HIGH       │ Any        │ ALERT    │ (human only)              │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                                                               │
│  Low-Risk Actions (Auto-Execute):                                           │
│  ├─ Send Slack alerts                                                       │
│  ├─ Add dashboard annotations                                               │
│  ├─ Adjust bids ≤10%                                                        │
│  └─ Create Beads entries                                                    │
│                                                                               │
│  Medium-Risk Actions (Approval Required):                                   │
│  ├─ Budget reductions 10-20%                                               │
│  ├─ Pause specific ad sets                                                  │
│  └─ Bid adjustments 10-25%                                                  │
│                                                                               │
│  High-Risk Actions (Human Decision):                                        │
│  ├─ Pause campaigns                                                         │
│  ├─ Budget reallocation >30%                                               │
│  ├─ Change bid strategy                                                     │
│  └─ Exit channels                                                           │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                      STORAGE & PERSISTENCE                                   │
│                                                                               │
│  PostgreSQL (via Repository Layer):                                         │
│  ├─ DC_DECISION_PACKETS   (Decision Packet records)                        │
│  ├─ DC_SESSIONS           (Agent conversation history)                     │
│  ├─ DC_POLICY_EVENTS      (Policy breach events)                           │
│  ├─ DC_TOKEN_USAGE        (API token consumption)                          │
│  └─ DC_TOOL_CALLS         (Tool invocation logs)                           │
│                                                                               │
│  Snowflake (Knowledge Graph):                                               │
│  ├─ CONFIG_ONTOLOGY_METRICS      (476 metrics + thresholds)               │
│  ├─ CONFIG_ONTOLOGY_CAUSAL_EDGES (315 causal edges)                       │
│  ├─ CONFIG_ONTOLOGY_POLICIES     (Generated policies)                      │
│  └─ CONFIG_DASHBOARDS/CHARTS     (BC_ANALYTICS structure)                 │
│                                                                               │
│  File System (Beads Memory):                                                │
│  └─ .beads/                      (Past incidents, learnings)               │
│                                    ↓                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                      RESPONSE & FEEDBACK LOOP                                │
│                                                                               │
│  For Reactive Queries:                                                      │
│  └─ Return: Markdown response with inline data                             │
│                                                                               │
│  For Proactive Investigations:                                              │
│  ├─ Return: Decision Packet JSON                                            │
│  ├─ UI Renders: The "5 Things"                                             │
│  │  ├─ WATCHING: Metric + baseline                                         │
│  │  ├─ HAPPENED: Anomaly details + trend chart                            │
│  │  ├─ CHECKED: Investigation steps (collapsible)                         │
│  │  ├─ RECOMMEND: Action cards with risk/confidence                       │
│  │  └─ IMPACT: Revenue impact (24h/7d/30d)                                │
│  │                                                                           │
│  ├─ User Actions:                                                          │
│  │  ├─ "APPROVE" → Execute action, record outcome                         │
│  │  ├─ "REJECT" → Skip action, record reasoning                           │
│  │  ├─ "DISCUSS" → Open comment thread                                     │
│  │  └─ "EXPLAIN" → Resume session (Why did you...?)                       │
│  │                                                                           │
│  └─ Learning Loop:                                                          │
│     ├─ feedback_processor.py → Extract decision pattern                    │
│     ├─ outcome_tracker.py    → Track T+7, T+30 outcomes                   │
│     ├─ threshold_calibrator  → Adjust policies based on feedback           │
│     └─ Beads                 → Record learnings for future                 │
│                                                                               │
│  Session Resumption:                                                        │
│  ├─ Load prior AgentSession from PostgreSQL                               │
│  ├─ Include reasoning trace + tool calls                                   │
│  ├─ Resume investigation with new question                                │
│  └─ Generate follow-up capsule                                            │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow: Breach to Decision Packet

```
POLICY ENGINE (Continuous Evaluation)
    |
    ├─→ Fetch metric from API: GET /api/marketing-mix/charts/roas-trending
    |       └─→ Response: { value: 2.5, historical: [...] }
    |
    ├─→ Compare against thresholds
    |       └─→ BREACH: ROAS 2.5 < threshold 3.0 (warning)
    |
    ├─→ Create PolicyBreach event
    |       └─→ Store in DC_POLICY_EVENTS table
    |
    └─→ Trigger Context Packager
            |
            ├─→ LOAD CONTEXT (60-200ms)
            |   ├─ Load knowledge graph (NetworkX cache, <1ms)
            |   ├─ Fetch metric metadata (config table, <10ms)
            |   ├─ Load causal edges for ROAS (graph traversal, <5ms)
            |   ├─ Fetch API registry entries (config file, <5ms)
            |   ├─ Load runbook content (yaml, <5ms)
            |   └─ Search Beads for similar incidents (grep, <30ms)
            |
            ├─→ BUILD INJECTION PAYLOAD
            |   ├─ Breach context (metric, severity, values)
            |   ├─ Upstream causes (deps that affect ROAS)
            |   ├─ Downstream effects (metrics affected by ROAS)
            |   ├─ API specs (endpoints, parameters)
            |   ├─ Chart info (how to visualize)
            |   ├─ Runbook (investigation steps)
            |   └─ Past incidents (context from similar)
            |
            └─→ STREAM TO AGENT
                |
                ├─→ RouteRequest()
                |   └─→ Pattern match → PROACTIVE agent
                |
                ├─→ Select system prompt (proactive_prompt.py)
                |
                ├─→ Get runtime
                |   ├─ If ANTHROPIC_API_KEY: DirectAnthropicClient
                |   └─ Else: SubprocessClient
                |
                ├─→ stream(system_prompt, user_message, tools)
                |   |
                |   ├─→ INVESTIGATION LOOP
                |   |   ├─ Agent: "Getting upstream causes..."
                |   |   ├─ Tool call: get_upstream_causes("roas", depth=5)
                |   |   ├─ Tool result: [{metric: "cpc", confidence: 0.85}, ...]
                |   |   ├─ Agent: "CPC spiked 45%. Getting root cause..."
                |   |   ├─ Tool call: get_upstream_causes("cpc", depth=3)
                |   |   ├─ Tool result: [{metric: "bid_strategy", ...}]
                |   |   ├─ Agent: "Found root cause: bid strategy changed"
                |   |   ├─ Tool call: forecast_impact(current=2.5, expected=4.0)
                |   |   ├─ Tool result: {impact_24h: -15000, impact_7d: -52500}
                |   |   └─ Agent: "Generating Decision Packet..."
                |   |
                |   └─→ GENERATE CAPSULE
                |       └─ JSON: {
                |           packet_id, watching, happened, checked,
                |           recommend, impact, metadata
                |         }
                |
                └─→ VALIDATE & ROUTE
                    |
                    ├─→ CapsuleValidator.validate()
                    |   ├─ Check schema
                    |   ├─ Verify metrics exist
                    |   ├─ Validate impact math
                    |   └─ Return: ✓ Valid / ✗ Error
                    |
                    ├─→ ApprovalRouter.route()
                    |   ├─ Assess risk level (medium: budget cuts)
                    |   ├─ Check confidence (0.82 ≥ 0.80)
                    |   └─ Route: analyst → manager approval
                    |
                    ├─→ SessionManager.save_session()
                    |   ├─ Store context + tool calls
                    |   ├─ Store reasoning trace
                    |   └─ Make resumable for "why?" questions
                    |
                    └─→ Return to UI
                        ├─ Packet JSON
                        ├─ Approval chain
                        └─ Render "5 Things"
```

---

## Module Dependency Graph

```
REQUEST
  ↓
api/agent_stream.py (Main entry point)
  ├→ context_packager.py (Build context)
  │   ├→ kg/causal_edges.py (Get upstream/downstream)
  │   ├→ entity_resolver.py (Resolve metric names)
  │   ├→ models/entity_mapping.py (Past resolutions)
  │   └→ Beads file system (Similar incidents)
  │
  ├→ runtime/__init__.py (Select runtime)
  │   ├→ runtime/anthropic_client.py (Direct API mode)
  │   └→ runtime/subprocess_client.py (Claude Code mode)
  │
  ├→ guardrails/input_filter.py (Sanitize input)
  │
  ├→ capsule_validator.py (Validate packet)
  │   └→ models/capsule.py (Packet schema)
  │
  ├→ approval_router.py (Route through approvals)
  │   ├→ approval_router.ApprovalChain
  │   └→ approval_router.ApprovalStep
  │
  ├→ session_manager.py (Persist sessions)
  │   ├→ db/repository.py (SessionRepository)
  │   └→ models/session.py (Session schema)
  │
  ├→ db/repository.py (Database access)
  │   ├→ db/connection.py (Snowflake connections)
  │   └→ models/*.py (All schemas)
  │
  └→ guardrails/response_filter.py (Sanitize output)

BACKGROUND PROCESSES
  ├→ policy_engine.py (Continuous evaluation)
  │   ├→ config/metric_snowflake_mapping.json
  │   └→ BC_ANALYTICS APIs
  │
  └→ learning/feedback_processor.py (Learn from feedback)
      └→ learning/outcome_tracker.py (Track T+7, T+30)
```

---

## Module Responsibility Matrix

| Module | Responsibility | Key Files | Dependencies |
|--------|----------------|-----------|--------------|
| **Context Packager** | Build agent injection payload | `context_packager.py` | KG, config, Beads |
| **Policy Engine** | Generate & evaluate policies | `policy_engine.py` | API registry, Snowflake |
| **Session Manager** | Persist & resume conversations | `session_manager.py` | PostgreSQL, file system |
| **Capsule Validator** | Validate Decision Packet schema | `capsule_validator.py` | models, KG |
| **Approval Router** | Route through approval chains | `approval_router.py` | config, database |
| **Entity Resolver** | Map names to canonical IDs | `entity_resolver.py` | config, database |
| **Runtime Layer** | Execute agent (API or subprocess) | `runtime/*.py` | anthropic SDK, Claude Code SDK |
| **API Layer** | HTTP endpoints | `api/*.py` | All above modules |
| **Database Layer** | CRUD operations | `db/*.py` | Snowflake, PostgreSQL |
| **Guardrails** | Input/output security filtering | `guardrails/*.py` | All layers |
| **Knowledge Graph** | Causal reasoning | `kg/*.py` | Snowflake, NetworkX |
| **Learning System** | Learn from feedback | `learning/*.py` | database, outcome tracking |

---

## Configuration File Ecosystem

```
config/
├─ api_registry.json              (828 endpoints, typed params)
├─ api_registry_google.json       (Google Ads/Shopping/GA4)
├─ api_registry_meta.json         (Meta Ads)
├─ api_registry_klaviyo.json      (Email)
├─ api_registry_core.json         (Core metrics)
├─ api_registry_customer.json     (Customer LTV)
├─ api_registry_planning.json     (Attribution)
├─ api_registry_product.json      (Inventory)
├─ api_registry_operations.json   (Finance)
│
├─ metric_api_mapping.json        (576 metrics → APIs)
├─ metric_snowflake_mapping.json  (38 metrics → Snowflake fallback)
├─ metric_data_access.json        (Which data access for each metric)
│
├─ chart_metric_mapping.json      (Charts → metrics)
├─ chart_embedding_index.json     (Vector embeddings for similarity)
├─ unified_chart_info.json        (Rich chart metadata)
│
├─ app_registry.json              (Multi-app discovery)
├─ app_registry.json              (App capabilities)
│
├─ policies_batch_1/2/3.json      (Generated policies)
├─ policy_progress.json           (Policy generation status)
├─ policy_staging.json            (Staging policies)
│
├─ generated_policies.json        (Full policy set)
├─ derived_policy_staging.json    (Derived metric policies)
├─ derived_policy_progress.json   (Derived progress)
│
├─ chain_validation_report.json   (Validation results)
└─ approval_chains.yaml           (Approval workflow config)
```

---

## Data Access Tiers

```
TIER 1: API (Preferred) - 92% of metrics
├─ 438 metrics mapped
├─ 590 endpoints
├─ 828 total across all products
├─ Latency: <500ms typical
└─ Via: BC_ANALYTICS backend (calls Snowflake internally)

TIER 2: Snowflake (Fallback) - 8% of metrics
├─ 38 metrics direct access
├─ Complex aggregations
├─ Latency: 1-3s typical
└─ Via: Direct Snowflake connector

TIER 3: Cache/Memory
├─ Knowledge graph (NetworkX) - <1ms queries
├─ Entity resolution cache (dict) - <1ms lookups
├─ Policy definitions (JSON) - <1ms access
└─ Beads (file grep) - <100ms search

Priority Order (when data stale/unavailable):
API → Snowflake → Cache → Beads (historical context)
```

---

## Agent Tool Ecosystem

### Reactive Agent Tools
```
READ-ONLY DATA ACCESS:
├─ get_metric(metric_id, filters) → current value
├─ get_chart(chart_id, filters) → chart data + rendering info
├─ search_metrics(query) → entity resolution
├─ list_dashboards() → available dashboards
├─ get_dashboard_context(dashboard_id) → current page metrics

KNOWLEDGE ACCESS:
├─ list_runbooks() → available investigation playbooks
├─ search_beads(keywords) → similar past incidents
└─ get_metric_definition(metric_id) → formula, interpretation
```

### Proactive Agent Tools
```
ALL REACTIVE TOOLS PLUS:

CAUSAL ANALYSIS:
├─ get_upstream_causes(metric_id, depth) → root causes
├─ get_downstream_effects(metric_id, depth) → impact
├─ find_causal_path(from_metric, to_metric) → explain link
├─ get_metric_decomposition(metric_id) → formula breakdown
└─ get_causal_edge_confidence(from, to) → relationship strength

FORECASTING:
├─ forecast_impact(current_value, expected_value) → projections
├─ get_historical_context(metric_id, days) → trend
└─ estimate_root_cause_impact(root_cause) → expected recovery

INVESTIGATION:
├─ isolate_anomaly(metric_id, dimensions) → segment drill-down
├─ get_policy(metric_id) → current thresholds
├─ search_similar_incidents(metric_id) → from Beads
└─ get_affected_segments(metric_id) → which slices impacted

ACTION PLANNING:
├─ suggest_runbook_actions(root_cause) → recommended actions
├─ assess_action_risk(action_description) → risk level
└─ estimate_success_probability(action, context) → confidence

CROSS-APP INTEGRATION:
├─ creative_iq.generate_landing_page() → new creative
├─ marketing_iq.run_visibility_audit() → SEO audit
└─ finance_iq.adjust_budget(amount) → budget action
```

---

## Approval Chain Examples

### Low-Risk Chain (Auto-Execute)
```
Analyst receives alert
    ↓ (no approval needed)
Execute immediately
    ├─ Send Slack: #marketing-alerts
    ├─ Adjust bids: ±5% on Google Shopping
    ├─ Create Beads entry: "ROAS low, bid reduction applied"
    └─ Log to DC_POLICY_EVENTS

Example Actions:
- Pause poorly performing ad set (ACOS >100%)
- Increase bids on high-performing keywords (CTR >5%)
- Send email to cart abandoners (Klaviyo)
```

### Medium-Risk Chain (Requires Approval)
```
Analyst reviews recommendation
    ↓
→ Manager approval (12-24h typical)
    ↓
Execute if approved
    ├─ Send Slack: #approvals-medium
    ├─ Action: Reduce budget 15% (Meta Prospecting)
    ├─ Timeline: Immediate
    └─ Review: Check metrics in 2 hours

Example Actions:
- Reduce daily budget 10-20%
- Pause campaign during off-hours
- Change bid strategy (automated → manual)
- Shift budget between channels (10-20%)
```

### High-Risk Chain (Human-Only)
```
Analyst generates recommendation
    ↓
Alert Director (no execution without explicit approval)
    ├─ Send Slack: @director in #approvals-high
    ├─ Email: Director + CMO
    ├─ Decision expected: <4 hours
    └─ If approved: Manual execution by director

Example Actions:
- Pause entire campaign (>$10k daily)
- Exit channel completely (30%+ budget)
- Change core KPI target
- Adjust pricing strategy
```

---

## Performance & Scaling Metrics

```
RESPONSE TIMES (Target):
├─ Context packaging: 100-200ms
├─ Policy evaluation: 50-100ms
├─ Agent streaming: First token 2-5s, rest <100ms/token
├─ Capsule validation: 10-20ms
├─ Approval routing: 5-10ms
└─ Total E2E (breach → capsule): 10-30 seconds

STORAGE:
├─ Decision Packets: ~2KB each, 1M possible = 2GB
├─ Sessions: ~10KB each, 100K max = 1GB
├─ Policy Events: ~500B each, 10M annually = 5GB
├─ Knowledge Graph: ~50MB in memory, ~500MB in JSON
└─ Total production: ~10GB Snowflake + 1GB PostgreSQL

THROUGHPUT:
├─ Policy evaluations: 476 metrics × 3 policies = 1,428/check
├─ Check frequency: hourly = 1,428/hour = 0.4/second (sustainable)
├─ Breaches/day: ~10-50 (seasonal, 40% in Q1)
├─ Concurrent investigations: 1-3 typical, max 10
└─ Concurrent users: 5-20 typical, max 50

SCALABILITY:
├─ Multi-client support: Central instance serves 4+ clients
├─ KG per client: 476 nodes → 500MB each, load on-demand
├─ Stateless API: Scale horizontally via load balancer
├─ Database: Snowflake/PostgreSQL handle scaling
└─ Runtime: Dual mode allows CPU-efficient fallback
```

---

## Testing Strategy

```
UNIT TESTS (Fast, In-Process)
├─ context_packager.py → 5 tests
├─ capsule_validator.py → 5 tests
├─ entity_resolver.py → 4 tests
├─ policy_engine.py → 4 tests
├─ approval_router.py → 3 tests
└─ Total: 21 tests, <5s runtime

INTEGRATION TESTS (Medium, External Services)
├─ API + Database → 4 tests
├─ Causal Graph + Snowflake → 3 tests
├─ Beads Search → 2 tests
└─ Total: 9 tests, 10-30s runtime

E2E TESTS (Slow, Full Stack)
├─ Breach → Context → Agent → Capsule → UI
├─ Session resumption ("Why did you...?")
├─ Approval workflow (multi-step)
├─ Learning loop (feedback → outcome)
└─ Total: 5 tests, 30-120s runtime

MANUAL TESTING
├─ UI rendering (Decision Canvas page)
├─ Chat interaction (reactive agent)
├─ Approval UI (multi-step workflow)
└─ Analytics charts (Nivo rendering)
```

---

## Environment Variables

```
CORE:
├─ ANTHROPIC_API_KEY         → Direct API mode (prod)
├─ DECISION_CANVAS_PATH      → Project root
├─ BC_ANALYTICS_PATH         → Sister repo location

DATABASE:
├─ SNOWFLAKE_ACCOUNT         → Snowflake domain
├─ SNOWFLAKE_USER            → Service account
├─ SNOWFLAKE_PRIVATE_KEY_PATH → RSA key for auth
├─ SNOWFLAKE_WAREHOUSE       → Compute warehouse
├─ SNOWFLAKE_DATABASE        → Client-specific DB
├─ SNOWFLAKE_SCHEMA          → Schema (default: PUBLIC)

POSTGRES:
├─ POSTGRES_DSN              → Connection string
├─ POSTGRES_USER
├─ POSTGRES_PASSWORD

FEATURE FLAGS:
├─ USE_DIRECT_API            → 1 = direct, 0 = subprocess
├─ DEBUG_MODE                → 1 = verbose logging
├─ GUARDRAILS_ENABLED        → 1 = strict, 0 = permissive

PATHS:
├─ SESSIONS_DIR              → Where to store session files
├─ BEADS_DIR                 → Where incidents stored
├─ CAPSULES_DIR              → Generated decision packets
```

---

This comprehensive architecture gives Decision Canvas OS the ability to be a true "digital MBA worker" — continuously watching metrics, investigating anomalies with causal reasoning, generating structured recommendations, and learning from human feedback to improve over time.
