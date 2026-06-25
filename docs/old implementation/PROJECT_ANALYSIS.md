# Decision Canvas OS - Comprehensive Project Analysis

**Last Updated:** May 28, 2026  
**Project Status:** Production-Ready (Phases 1-5 Complete, UI Components in Progress)  
**Primary Client:** Baker Creek Heirloom Seeds

---

## Executive Summary

**Decision Canvas OS** is an AI-native business intelligence system that autonomously monitors 476+ business metrics, investigates anomalies using causal reasoning, and generates structured "Decision Packets" (the "5 Things") for human review and action. It's built as a sister repository to BC_ANALYTICS, calling its 590+ dashboard APIs rather than duplicating backend logic.

### Core Value Proposition
- **Automated Root Cause Analysis**: Walk causal graphs to find why metrics changed
- **Multi-Agent Collaboration**: 6 specialized agents (triage, data-quality, causal-analyst, forecaster, recommender, executive)
- **Decision Packets**: Structured outputs answering: WATCHING, HAPPENED, CHECKED, RECOMMEND, IMPACT
- **Risk-Based Actions**: Auto-execute low-risk fixes, request approval for medium, alert-only for high-risk
- **Learning Loop**: Track outcomes T+7 and T+30, calibrate policies based on performance

---

## Project Architecture Overview

### High-Level System Flow

```
Metric Breach Detected (Policy Engine)
         ↓
    Triage Agent (5 sec)
    - Classify severity
    - Search Beads for similar incidents
         ↓
    Data Quality Check (30 sec)
    - Verify freshness, completeness, schema
         ↓
    Context Packager
    - Load causal graph context
    - Fetch API specs & chart info
    - Build agent injection payload
         ↓
    Proactive Agent (Claude API)
    - Follow investigation protocol
    - Query upstream causes
    - Isolate by dimension
    - Forecast 24h/7d/30d impact
         ↓
    Decision Packet Generated
    - Schema: WATCHING, HAPPENED, CHECKED, RECOMMEND, IMPACT
    - Risk assessment for each action
         ↓
    Approval Router
    - Route through chain based on risk
    - Low: auto-execute
    - Medium: request approval
    - High: alert only
         ↓
    Execute or Escalate
    - Send Slack alerts
    - Create Beads entries
    - Return response to user
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Next.js 14 + TypeScript | Decision Canvas UI, reactive chat, approval queues |
| **Backend** | FastAPI (Python) | Agent streaming API, policy engine, approval routing |
| **AI Runtime** | Claude API + Claude Code SDK | Dual mode: direct API (production) or subprocess (dev) |
| **Knowledge Graph** | Snowflake + NetworkX | Causal DAG (315 edges, 476 nodes), in-memory cache |
| **Data Access** | Snowflake + BC_ANALYTICS APIs | 828+ endpoints across 55 dashboards |
| **Storage** | PostgreSQL + Snowflake | Sessions, decisions, approvals, token usage |
| **Message Queue** | File-based (JSON) | Beads system for incident memory |
| **Infrastructure** | Docker + Tailscale | Containerized, accessible via Tailscale VPN |

### Deployment Architecture

```
┌──────────────┐     HTTPS      ┌──────────────────┐   Tailscale   ┌─────────────────────────┐
│   Browser    │ ──────────────▶│   Azure VM       │─────────────▶│   India Machine (WSL2)  │
│              │                 │ 52.240.140.240   │              │ 100.78.153.91:2222      │
└──────────────┘                │ nginx reverse    │              │                         │
                                │ proxy            │              │ :3500 UI (Next.js)      │
https://rahil911.duckdns.org    │ rahil911.duckdns │              │ :8001 Backend (FastAPI)  │
                                │ .org             │              │ :8000 BC Analytics API   │
                                └──────────────────┘              └─────────────────────────┘
```

**Key Machines:**
- **Mac (Dev)**: Local development only - NEVER run servers here
- **Azure VM**: nginx reverse proxy + SSH jump host for Tailscale access
- **India (Production)**: Dell Precision 7810 (56 cores, 128GB RAM), all services run in tmux session

---

## Core Modules & Components

### 1. **Context Packager** (`src/context_packager.py` - 30,659 lines)

**Purpose**: Builds the complete context payload for agent injection when a policy breach is detected.

**Key Functions**:
- `package(breach_context)` → Injects metric data + causal graph + API specs + runbook
- `_load_knowledge_graph()` → Loads Snowflake causal edges into NetworkX DAG
- `_build_metric_context()` → Fetches current metric value from BC_ANALYTICS API
- `_get_upstream_causes()` → Traverses causal graph to find root causes
- `_get_downstream_effects()` → Projects impact across downstream metrics

**Data Structure**:
```python
@dataclass
class BreachContext:
    metric_id: str           # e.g., "roas"
    breach_type: str         # "threshold", "trend", "anomaly"
    severity: str            # "critical", "high", "medium", "low"
    observed_value: float    # Current value
    baseline_value: float    # Expected value
    delta_pct: float         # Percentage change
    detected_at: str         # ISO timestamp
    channel: Optional[str]   # e.g., "google_shopping"
    device: Optional[str]    # e.g., "mobile"
```

**Output**: Injection payload containing:
- Metric metadata + current/historical values
- Causal graph context (upstream/downstream nodes)
- API registry (828 endpoints with typed parameters)
- Runbook content (investigation playbooks)
- Related past incidents from Beads

---

### 2. **Policy Engine** (`src/policy_engine.py` - 21,855 lines)

**Purpose**: Generates and evaluates threshold/trend/anomaly policies against live data.

**Key Features**:
- **Policy Types**: Threshold (value > X), Trend (declining over N days), Anomaly (statistical deviation)
- **Auto-Generation**: Generates 476 policies (1-3 per metric) from config rules
- **Real-Time Evaluation**: Queries BC_ANALYTICS API, compares against thresholds
- **Breach Persistence**: Stores breaches to PostgreSQL `DC_POLICY_EVENTS` table with audit trail

**Data Model**:
```python
@dataclass
class Policy:
    policy_id: str              # e.g., "pol_roas_threshold_2.0"
    type: str                   # "threshold", "trend", "anomaly"
    condition: str              # "ROAS < 2.0"
    threshold_value: float      # 2.0
    comparison_operator: str    # "<", ">", "<=", ">="
    lookback_days: Optional[int] # For trend: days to look back
    severity: str               # "critical", "high", "medium", "low"
    check_frequency: str        # "hourly", "daily"
    active: bool                # Whether to evaluate
    description: str            # Human-readable description

@dataclass
class PolicyBreach:
    policy_id: str
    metric_id: str
    severity: str
    observed_value: float
    threshold_value: float
    breach_type: str            # "above_threshold", "below_threshold", "trend"
    message: str
    detected_at: str
```

**Evaluation Flow**:
1. Load active policies from `CONFIG_ONTOLOGY_POLICIES`
2. For each policy, fetch current metric value from API
3. Compare observed vs threshold
4. If breached, create `PolicyBreach` event
5. Persist to PostgreSQL (WS6A)

---

### 3. **Session Manager** (`src/session_manager.py` - 24,395 lines)

**Purpose**: Persists agent conversation sessions for resumption and debugging.

**Use Case**: User asks "Why did you recommend X?" → restore full investigation context

**Storage Backends**:
- **Snowflake** (Production): `DC_SESSIONS` table via `SessionRepository`
- **JSON Files** (Development): Fallback when Snowflake unavailable

**Data Model**:
```python
@dataclass
class AgentSession:
    session_id: str              # "sess_abc123..."
    packet_id: str               # Link to decision packet
    status: str                  # "active", "completed", "expired"
    created_at: str              # ISO timestamp
    last_active: str             # When last modified
    context_injected: Dict       # Full injection payload
    tool_calls: List[ToolCall]   # Sequence of tool invocations
    reasoning_trace: str         # Agent's thinking/reasoning
    artifacts: List[str]         # Generated outputs (charts, SQL, etc.)
    resumable: bool              # Can session be resumed?
    machine_id: str              # Which machine executed

@dataclass
class ToolCall:
    tool: str                    # "get_upstream_causes"
    input: Dict                  # Tool parameters
    output: Dict                 # Tool result
    timestamp: str               # When called
```

**Key Methods**:
- `save_session()` → Persist to Snowflake or JSON
- `load_session(session_id)` → Restore context for resumption
- `add_tool_call()` → Log each tool invocation
- `close_session()` → Mark as completed

---

### 4. **Approval Router** (`src/approval_router.py` - 12,688 lines)

**Purpose**: Routes decision packets through multi-step approval chains based on risk level.

**Risk Levels & Autonomy**:
| Risk Level | Confidence | Action |
|-----------|-----------|--------|
| Low | Any | Auto-execute (no approval needed) |
| Medium | ≥80% | Request approval before executing |
| Medium | <80% | Alert only, let human decide |
| High | Any | Alert only, human decides |

**Approval Chain Structure**:
```python
@dataclass
class ApprovalChain:
    id: str                      # "low_risk_chain"
    name: str                    # Human-readable name
    risk_level: str              # "low", "medium", "high"
    steps: List[ApprovalStep]    # Sequential approvers
    auto_approve_after_hours: Optional[int]  # Auto-approve if pending X hours
    escalate_after_hours: Optional[int]      # Escalate if pending X hours

@dataclass
class ApprovalStep:
    role: str                    # "analyst", "manager", "director"
    users: List[str]             # Specific users with this role
    notify_on_pending: bool      # Send notification when step reached
    status: ApprovalStatus       # "pending", "approved", "rejected"
    decided_by: Optional[str]    # Who made the decision
    decided_at: Optional[str]    # When decision made
    notes: Optional[str]         # Decision notes
```

**Example Chains**:
- **Low-Risk** (bid adjustments ±10%): Analyst only → Auto-execute
- **Medium-Risk** (budget cuts 10-20%): Analyst → Manager → Execute if approved
- **High-Risk** (pause campaigns): Analyst → Manager → Director → Human-only alert

**Integration**: Approval router is called by the API layer after Decision Packet generation to determine routing.

---

### 5. **Capsule Validator** (`src/capsule_validator.py` - 24,034 lines)

**Purpose**: Validates Decision Packet schema before storage or display.

**Decision Packet ("Capsule") Schema**:
```json
{
  "packet_id": "pkt_abc123...",
  "watching": {
    "metric_id": "roas",
    "metric_name": "Blended ROAS",
    "baseline_value": 4.0,
    "unit": "x",
    "slice": {
      "channel": "google_shopping",
      "device": "mobile"
    }
  },
  "happened": {
    "observed_value": 2.5,
    "delta_pct": -37.5,
    "delta_value": -1.5,
    "duration_hours": 12,
    "trend": "degrading",
    "message": "ROAS dropped 37.5% in Google Shopping mobile traffic"
  },
  "checked": [
    {
      "step": "1. Data quality",
      "finding": "✓ Data fresh (15 min latency)",
      "evidence": { "ga4_latency": 15, "facebook_latency": 45 }
    },
    {
      "step": "2. Decompose metric",
      "finding": "✓ Revenue stable, spend spiked",
      "evidence": { "revenue_change": 2, "spend_change": 65 }
    },
    {
      "step": "3. Root cause",
      "finding": "CPC spike → reduced daily budget",
      "evidence": { "cpc_change": 48, "budget_change": -15 }
    }
  ],
  "recommend": [
    {
      "action": "Revert bid strategy to lower CPC",
      "rationale": "Bids were auto-increased by smart bidding yesterday",
      "risk_level": "medium",
      "confidence": 0.82,
      "expected_impact_value": 12000,
      "expected_impact_roas": 3.5,
      "approval_chain": "medium_risk_chain"
    }
  ],
  "impact": {
    "revenue_impact_24h": -15000,
    "revenue_impact_7d": -52500,
    "revenue_impact_30d": -225000,
    "action_impact_if_taken": 225000,
    "action_impact_if_not_taken": -225000,
    "math": "12.5% of daily revenue × 18 remaining days in month"
  },
  "metadata": {
    "severity": "high",
    "created_at": "2026-02-01T14:30:00Z",
    "investigation_duration_seconds": 42,
    "agent_tokens": 4200,
    "status": "draft",
    "user_feedback": null
  }
}
```

**Validation Rules**:
1. All required fields present
2. Numeric values are valid (no NaN, Inf)
3. Metric IDs exist in knowledge graph
4. Risk levels are one of: low, medium, high
5. Confidence scores in [0, 1]
6. Impact calculations match documented formulas
7. Approver roles exist in system

---

### 6. **Entity Resolver** (`src/entity_resolver.py` - 13,010 lines)

**Purpose**: Maps raw metric/chart names to canonical IDs using multi-strategy matching.

**Resolution Strategy** (in priority order):
1. **Exact DB Match**: Check `dc.entity_mappings` table for past resolutions
2. **Normalized Match**: Lowercase + strip whitespace/hyphens/underscores
3. **Config Fuzzy Match**: Search `chart_metric_mapping.json` and `unified_chart_info.json`
4. **Abbreviation Expansion**: CVR → conversion-rate, AOV → avg-order-value, etc.

**Example Mappings**:
- Input: "cvr" → Output: "conversion-rate"
- Input: "ROAS (Google Shopping)" → Output: "roas", slice: {channel: "google_shopping"}
- Input: "email_open_rate" → Output: "email-open-rate"

**Result Structure**:
```python
@dataclass
class ResolvedEntity:
    canonical_id: str           # "roas"
    component_name: str         # "BlendedROASMetric"
    dashboard: str              # "marketing-mix"
    confidence: float           # 0.95
```

---

### 7. **Runtime Layer** (`src/runtime/` - 3 files)

**Purpose**: Pluggable agent execution — Direct Anthropic API (production) or Claude Code SDK subprocess (development).

#### 7a. **DirectAnthropicClient** (`anthropic_client.py`)
- Uses `anthropic.Client().messages.create()` API
- Converts MCP tool schemas to Anthropic API format
- Routes tool calls directly to Python functions (no MCP stdio)
- Streaming via Server-Sent Events

#### 7b. **SubprocessClient** (`subprocess_client.py`)
- Uses Claude Code SDK (spawns subprocess)
- MCP tools via stdio pipes
- Existing fallback mechanism

#### 7c. **Runtime Switcher** (`__init__.py`)
- `get_runtime()` factory: Returns DirectAnthropicClient if `ANTHROPIC_API_KEY` set, else SubprocessClient
- Both implement same interface: `async stream(system_prompt, user_message, tools) → AsyncIterator`

---

### 8. **API Layer** (`src/api/` - 6 files)

#### 8a. **Agent Streaming API** (`agent_stream.py` - 48,733 lines)
**Endpoints**:
- `POST /stream` → Stream agent responses for chat messages
- `POST /investigate` → Start proactive investigation for breach
- `POST /resume/{session_id}` → Resume previous investigation

**Request Body**:
```json
{
  "message": "Why did ROAS drop?",
  "packet_id": null,
  "page_context": {
    "dashboard_id": "marketing-mix",
    "visible_metrics": ["roas", "spend", "revenue"],
    "visible_charts": ["roas-trending"]
  },
  "user_id": "user_123",
  "tenant_id": "baker-creek"
}
```

**Response**: Server-Sent Events stream of events:
```
event: context_loaded
data: {...}

event: tool_call
data: {"tool": "get_upstream_causes", "input": {...}}

event: tool_result
data: {...}

event: capsule_generated
data: {...}

event: done
```

**Key Functions**:
- `route_request()` → Determine agent type (reactive vs proactive)
- `stream_agent_response()` → Main streaming logic
- `apply_guardrails()` → Security filtering

#### 8b. **Capsules Router** (`capsules.py` - 18,039 lines)
**Endpoints**:
- `GET /capsules` → List decision packets
- `GET /capsules/{packet_id}` → Fetch packet details
- `POST /capsules/{packet_id}/approve` → Approve packet
- `POST /capsules/{packet_id}/feedback` → Capture user feedback

#### 8c. **Policies Router** (`policies.py` - 26,497 lines)
**Endpoints**:
- `GET /policies` → List all policies
- `POST /policies` → Create new policy
- `POST /policies/evaluate` → Evaluate policies against live data
- `GET /breaches` → List recent policy breaches

#### 8d. **Sessions Router** (`sessions.py` - 15,985 lines)
**Endpoints**:
- `GET /sessions/{session_id}` → Retrieve session
- `POST /sessions/{session_id}/explain` → Ask for clarification
- `GET /sessions/{session_id}/artifacts` → Download generated SQL, charts, etc.

#### 8e. **Admin Routes** (`admin_routes.py` - 9,959 lines)
**Endpoints**:
- `POST /admin/rebuild-kg` → Rebuild knowledge graph cache
- `POST /admin/sync-policies` → Sync policies with Snowflake
- `GET /admin/health` → Health check

#### 8f. **Models Routes** (`models_routes.py` - 1,529 lines)
**Endpoints**:
- `GET /models` → List available Claude models

---

### 9. **Database Layer** (`src/db/` - 3 files)

#### 9a. **Connection Manager** (`connection.py`)
**Purpose**: Client-aware Snowflake connections (each client has their own DB).

**Key Logic**:
```python
def get_connection(client_id: str) -> SnowflakeConnection:
    # Convert client_id to DB name pattern
    db_name = f"CLIENT_{client_id.upper()}_DB"
    # Create connection with RSA key-pair auth
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key_path=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
        warehouse="COMPUTE_WH",
        database=db_name
    )
```

#### 9b. **Repository Layer** (`repository.py` - 27,087 lines)
**Classes**:
- `CapsuleRepository` → CRUD for `DC_DECISION_PACKETS`
- `SessionRepository` → CRUD for `DC_SESSIONS`
- `EventRepository` → CRUD for `DC_POLICY_EVENTS`
- `TokenUsageRepository` → Track API token consumption
- `ToolCallRepository` → Log tool invocations

**Database Schema**:
```sql
-- DC_DECISION_PACKETS
CREATE TABLE DC_DECISION_PACKETS (
    packet_id VARCHAR PRIMARY KEY,
    session_id VARCHAR,
    user_id VARCHAR,
    app_context VARCHAR,
    mode VARCHAR, -- 'live', 'backtest'
    status VARCHAR, -- 'draft', 'published', 'approved', 'rejected'
    metric_id VARCHAR,
    severity VARCHAR,
    packet_json VARIANT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    approved_by VARCHAR,
    approval_notes VARCHAR
);

-- DC_SESSIONS
CREATE TABLE DC_SESSIONS (
    session_id VARCHAR PRIMARY KEY,
    user_id VARCHAR,
    app_context VARCHAR,
    agent_type VARCHAR, -- 'reactive', 'proactive'
    status VARCHAR,
    message_history VARIANT,
    tool_calls VARIANT,
    created_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- DC_POLICY_EVENTS
CREATE TABLE DC_POLICY_EVENTS (
    event_id VARCHAR PRIMARY KEY,
    policy_id VARCHAR,
    metric_id VARCHAR,
    observed_value FLOAT,
    baseline_value FLOAT,
    delta_pct FLOAT,
    severity VARCHAR,
    packet_id VARCHAR,
    detected_at TIMESTAMP,
    resolved_at TIMESTAMP
);
```

---

### 10. **Learning System** (`src/learning/` - 2 files)

#### 10a. **Feedback Processor** (`feedback_processor.py`)
**Purpose**: Process human decisions on recommendations (approve/reject/modify).

**Input**:
```json
{
  "packet_id": "pkt_abc123",
  "action": "APPROVE",
  "reasoning": "CPC spike confirmed in GA4",
  "modified_recommendation": null,
  "user_id": "analyst_1"
}
```

**Processing**:
1. Load packet from `DC_DECISION_PACKETS`
2. Compare action (approve vs actual outcome)
3. Extract decision pattern (what factors influenced choice?)
4. Update outcome tracker

#### 10b. **Outcome Tracker** (`outcome_tracker.py`)
**Purpose**: Track T+7 and T+30 outcomes to measure recommendation accuracy.

**Metrics Tracked**:
- Did ROAS recover after recommendation? (T+1, T+7, T+30)
- Was recommendation risk assessment accurate?
- Did action prevent projected revenue loss?

**Calculation**:
```python
# Value Calculation
value_saved = {
    "if_approved": projected_impact_if_taken,
    "if_rejected": abs(projected_impact_if_not_taken),
    "actual": actual_metric_value_at_t30 - observed_value
}
```

---

### 11. **Knowledge Graph Module** (`src/kg/` - 2 files)

#### 11a. **Auto-Sync** (`auto_sync.py`)
**Purpose**: Keeps in-memory NetworkX graph synchronized with Snowflake.

**Sync Triggers**:
- On startup: Load from `CONFIG_ONTOLOGY_CAUSAL_EDGES`
- On API call: If graph older than TTL (5 min), refresh
- On change: Snowflake trigger pushes update to event stream

#### 11b. **Causal Edges** (`causal_edges.py`)
**Purpose**: Query and manipulate causal edges.

**Key Methods**:
- `get_upstream_causes(metric_id, depth=3)` → BFS to find root causes
- `get_downstream_effects(metric_id, depth=3)` → BFS to find impacts
- `find_causal_path(from, to)` → Shortest path between metrics
- `get_metric_decomposition(metric_id)` → Break down derived metrics

**Example**:
```python
# Root cause query
get_upstream_causes("roas", depth=3)
# Returns: [
#   {"metric_id": "revenue", "relationship": "numerator"},
#   {"metric_id": "spend", "relationship": "denominator"},
#   {"metric_id": "cpc", "relationship": "causes_spend_increase", "confidence": 0.85}
# ]
```

---

### 12. **Guardrails System** (`src/guardrails/` - 3+ files)

**Purpose**: Security filtering at input and output to prevent injection, prompt abuse, data leaks.

**Components**:
1. **InputFilter** → Sanitize user messages (no SQLi, code injection)
2. **ResponseFilter** → Remove sensitive data from agent output
3. **SandboxingPolicy** → Restrict agent to read-only operations

**Key Rules**:
- Block SQL keywords in user input (unless in backticks)
- Remove database credentials from tool outputs
- Prevent file write/execute operations
- Limit to whitelisted MCP tools only

---

## Key Configuration Files

### 1. **API Registry** (`config/api_registry.json`)
Maps metrics to BC_ANALYTICS API endpoints.

**Structure**:
```json
{
  "marketing-mix": {
    "roas-trending": {
      "endpoint": "/api/marketing-mix/charts/roas-trending",
      "method": "GET",
      "params": [
        {"name": "channel", "type": "string", "required": false, "enum": ["google", "meta", "tiktok"]},
        {"name": "device", "type": "string", "required": false, "enum": ["mobile", "desktop"]},
        {"name": "date_start", "type": "date", "required": true},
        {"name": "date_end", "type": "date", "required": true}
      ]
    }
  }
}
```

### 2. **Metric Snowflake Mapping** (`config/metric_snowflake_mapping.json`)
Links metrics to underlying Snowflake tables/columns for fallback data access.

**Structure**:
```json
{
  "roas": {
    "table": "analytics.marketing_metrics",
    "value_column": "blended_roas",
    "where_clause": "metric_date = CURRENT_DATE()",
    "aggregation": "AVG"
  }
}
```

### 3. **Chart Metric Mapping** (`config/chart_metric_mapping.json`)
Maps charts to metrics they contain.

**Structure**:
```json
[
  {
    "chart_id": "roas-trending",
    "dashboard": "marketing-mix",
    "metrics": ["roas", "roas_google", "roas_meta"],
    "chart_type": "line",
    "bc_component": "BlendedROASMetric"
  }
]
```

### 4. **Unified Chart Info** (`config/unified_chart_info.json`)
Rich metadata about each chart including audio narration, formulas, interpretation.

**Structure**:
```json
{
  "roas-trending": {
    "metric_id": "roas",
    "formula": "Revenue ÷ Spend",
    "formula_explanation": "For every $1 spent, we earn $X in revenue",
    "how_to_read": [
      "Green trend = improving performance",
      "Flat line = stable (good)"
    ],
    "audio_url": "/audio/roas-explained.mp3",
    "narration_text": "Blended ROAS measures..."
  }
}
```

### 5. **Policy Progress** (`config/policy_progress.json`)
Tracks which metrics have been assigned policies.

**Status**:
- 476 total metrics
- ~450 metrics have policies
- ~26 metrics pending policy generation

---

## Frontend Architecture

### UI Technology Stack
- **Framework**: Next.js 14 (React)
- **Styling**: Tailwind CSS + shadcn/ui
- **Charts**: Nivo (13 chart types) + D3.js for custom visualizations
- **State Management**: React hooks + Context API
- **Testing**: Vitest + Playwright (integration tests)

### Component Structure

#### **Decision Canvas Components** (`ui/src/components/decision-canvas/`)
1. **DecisionCanvas.tsx** - Main page composition
2. **FiveThings.tsx** - Renders WATCHING, HAPPENED, CHECKED, RECOMMEND, IMPACT sections
3. **EvidenceGrid.tsx** - Grid of chart components showing evidence
4. **ActionButtons.tsx** - Approve/Reject/Modify buttons with approval flow

#### **Chat Components** (`ui/src/components/chat/`)
1. **ChatInterface.tsx** - Reactive chat UI for questions
2. **ChatMessage.tsx** - Individual message bubbles
3. **MessageInput.tsx** - Input field with context-aware suggestions

#### **Approval Components** (`ui/src/components/approval/`)
1. **ApprovalQueue.tsx** - List of pending approvals
2. **ApprovalCard.tsx** - Individual approval with chain progress
3. **RiskBadge.tsx** - Visual indicator of risk level

#### **Chart Components** (`ui/src/components/charts/`)
- 37 specialized chart components (ROASMetric, ConversionRateChart, etc.)
- All use Nivo as underlying renderer
- Support interactive drill-down

#### **UI Primitives** (`ui/src/components/ui/`)
- Button, Card, Dialog, Dropdown, Select, Tabs, Tooltip
- Radix UI + Tailwind styling
- Consistent design system

### Key UI Pages

#### 1. **Decision Canvas Page** (`/canvas`)
Displays current/recent decision packets with:
- WATCHING section (metric + baseline)
- HAPPENED section (anomaly details with trend chart)
- CHECKED section (collapsible investigation steps)
- RECOMMEND section (action cards with risk badges)
- IMPACT section (24h/7d/30d revenue impact with math)
- Approval workflow buttons

#### 2. **Chat Page** (`/chat`)
- Question input box
- Reactive agent responses (markdown)
- "Package as Capsule" button for investigation offers
- Session memory (see past questions)

#### 3. **Approval Queue** (`/approvals`)
- List of pending approvals grouped by risk level
- Each approval shows: packet summary, recommender, chain progress
- Approve/Reject/Discuss buttons
- Notes field

#### 4. **History Page** (`/history`)
- Timeline of all decision packets
- Filter by metric, severity, status
- Drill-down to see feedback collected
- Outcome metrics (was it correct?)

---

## Investigation Protocol (Step-by-Step)

When a policy breach is detected, Decision Canvas follows this protocol:

### Step 1: **Triage** (5 seconds)
- Classify severity: critical (ROAS <2.0), high (ROAS <3.0), medium, low
- Identify primary metric and related metrics
- Search Beads for similar past incidents
- Route to appropriate agent

### Step 2: **Validate Data Quality** (30 seconds)
- Check freshness: Magento (1h SLA), Google Ads (4h SLA), Snowflake (4h SLA)
- Verify completeness: >95% for critical metrics
- Look for schema changes or known issues
- **If data invalid**: STOP and escalate data quality issue

### Step 3: **Decompose Metric**
- If ratio/derived metric, query each component
- Example: ROAS = Revenue ÷ Spend → Query both separately
- Identify which component is anomalous
- Focus investigation on anomalous part

### Step 4: **Walk Causal Graph** (Root Cause Analysis)
- Call `get_upstream_causes(metric_id, depth=5)` to find ancestors
- For each upstream metric, check for anomalies
- Continue upstream until root cause found
- Build the causal path: [root] → [intermediate] → [symptom]

**Example Trace**:
```
ROAS ↑ (Symptom)
  ← CPC ↓ (Intermediate)
    ← Bid Strategy Changed (Root Cause)
```

### Step 5: **Isolate by Dimension**
- Drill down by: channel (Google/Meta/TikTok), device (mobile/desktop), campaign, region
- Find which segment shows anomaly
- Calculate segment contribution to total impact

### Step 6: **Forecast Impact**
- Calculate: 24h, 7d, 30d revenue impact
- Determine trend: improving, stable, degrading
- Set urgency multiplier: 0.5 (improving) to 2.0 (critical degrading)

### Step 7: **Recommend Actions**
- Match root cause to runbook actions
- Calculate risk level for each action
- Estimate success probability (confidence)
- Identify do-not-do actions

### Step 8: **Execute or Escalate**
- **Low-risk**: Auto-execute (send Slack alert)
- **Medium-risk**: Request approval
- **High-risk**: Alert only, human decides

---

## Data Access Architecture

### 92% API-Mapped (Preferred)
- 438 metrics → BC_ANALYTICS dashboard APIs
- 590 endpoints across 55 dashboards
- Typed parameter schema in `api_registry.json`
- Fast response (<500ms typical)

### 8% Snowflake Fallback
- 38 metrics → Direct Snowflake query
- Used when: API unavailable, complex aggregations needed
- Schema defined in `metric_snowflake_mapping.json`
- Slower but reliable

### Data Freshness SLAs
| Source | SLA | Critical |
|--------|-----|----------|
| Magento | 1 hour | 4 hours |
| Google Ads | 4 hours | 8 hours |
| Meta Ads | 4 hours | 8 hours |
| GA4 | 4 hours | 12 hours |
| Klaviyo | 2 hours | 6 hours |

---

## Autonomy Rules & Action Execution

### Risk Assessment Framework

**Low-Risk Actions** (Auto-Execute):
- Slack alerts
- Dashboard annotations
- Bid adjustments ≤10%
- Beads entries
- **Execution**: Immediate, no approval needed

**Medium-Risk Actions** (Approval Required):
- Budget reductions 10-20%
- Pause specific ad sets
- Bid adjustments 10-25%
- **Execution**: After approval chain complete (typically 2-4 hours)

**High-Risk Actions** (Human Decision Only):
- Pause entire campaigns
- Major budget reallocation >30%
- Change bidding strategies
- Exit channels
- **Execution**: Alert sent, human decides

### Confidence Threshold
- **≥80% confidence** + Medium risk → Auto-request approval
- **<80% confidence** + Medium risk → Alert only
- **Any confidence** + High risk → Alert only

---

## Testing Coverage

### Test Files
```
tests/
├── test_e2e_api.py              # 5 E2E tests
├── test_enterprise_grade.py     # 34 comprehensive tests
├── test_st157_agent_features.py # 14 feature tests
├── test_st159_capsule_lifecycle.py # 12 workflow tests
├── test_st160_kg_entity.py      # 11 resolution tests
├── test_st161_cross_product.py  # 10 cross-app tests
└── test_v2_foundation.py        # 12 foundation tests
```

**Total**: 98+ tests covering:
- Context packaging ✅
- Policy evaluation ✅
- Capsule schema validation ✅
- Session persistence ✅
- Approval routing ✅
- Entity resolution ✅
- Cross-product actions ✅

### Running Tests
```bash
# All tests
pytest tests/ -v

# Specific test
pytest tests/test_enterprise_grade.py::test_context_enrichment -v

# Integration tests only
pytest -m integration

# Unit tests only
pytest -m "not integration"
```

---

## Infrastructure & Deployment

### Service Architecture

**3 Services Running (tmux session `e2e-test` on India WSL2)**:

| Port | Service | Command | Directory |
|------|---------|---------|-----------|
| 8000 | BC Analytics API | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | `BC_ANALYTICS/backend` |
| 8001 | Decision Canvas Backend | `uvicorn src.api.agent_stream:app --host 0.0.0.0 --port 8001` | `decision-canvas-os` |
| 3500 | Decision Canvas UI | `npm start` (port 3500) | `decision-canvas-os/ui` |

### Restarting Services

```bash
# Connect to tmux
ssh india-linux
tmux attach -t e2e-test

# Navigate panes with Ctrl+B then arrow keys
# Ctrl+B then d = detach

# Restart specific pane (example: pane 1)
tmux send-keys -t e2e-test:0.1 C-c Enter
tmux send-keys -t e2e-test:0.1 'cd ~/Projects/decision-canvas-os && source .venv/bin/activate && python -m uvicorn src.api.agent_stream:app --host 0.0.0.0 --port 8001' Enter
```

### Deployment (CI/CD)

**Workflow**: `git push` to master → GitHub Actions → Auto-deploy

**Process**:
1. GitHub Actions runs `.github/workflows/deploy.yml`
2. SSH to India via Azure jump host (ProxyJump)
3. `git pull` latest code
4. `pip install -r requirements.txt`
5. `npm install && npm run build` (cleans `.next` cache first)
6. Restart backend (pane 1) and UI (pane 2)
7. Health check backend at `:8001/health`

**What CI/CD Ignores** (no redeploy):
- `.beads/`, `capsules/`, `sessions/`, `docs/`, `*.md`, `scripts/`

**Auto-Start on Boot**:
```bash
# Crontab entry on India WSL2
@reboot /home/rahil/start-services.sh
```

---

## Beads (Memory System)

Decision Canvas uses Beads for incident memory and learning.

### Key Commands
```bash
# View all incidents
bd list

# Search for similar incidents
bd search "[metric_name] [breach_type]"

# Create new incident record
bd create "[metric] [breach] - [root_cause_summary]" --template metric-breach

# Link related incidents
bd dep add [new_id] [related_id]

# Close incident when resolved
bd close [id] --reason "[resolution]"
```

### When Investigation Finds Something New
- Search Beads: boost matching hypothesis by +20% confidence
- Record resolution: link to related past incidents
- Learn from patterns: did similar root causes occur before?

---

## Business Context: Baker Creek Heirloom Seeds

### Company Profile
- **Industry**: E-commerce (Seed/Gardening)
- **Specialty**: Rare, heirloom, open-pollinated seeds
- **Location**: Mansfield, Missouri
- **Seasonality**: Highly seasonal with Q1 peak

### Key Metrics & Targets

| Metric | Target | Warning | Critical |
|--------|--------|---------|----------|
| Blended ROAS | 4.0x | 3.0x | 2.0x |
| LTV:CAC Ratio | 3:1 | 2:1 | 1.5:1 |
| Conversion Rate | 2.5% | 2.0% | 1.5% |
| Email Open Rate | 25% | 20% | 15% |
| Cart Abandonment | 65% | 75% | 85% |

### Seasonality
- **Q1 (Jan-Mar)**: **PEAK** - 40% of annual revenue
- **Q2 (Apr-Jun)**: Transition - 25% of revenue
- **Q3 (Jul-Sep)**: Low - 15% of revenue
- **Q4 (Oct-Dec)**: Building - 20% of revenue

### Channel Mix
| Channel | Revenue Share | ROAS Target |
|---------|---------------|-------------|
| Google Shopping | 40% | 4.5x |
| Google Search | 25% | 4.0x |
| Meta Prospecting | 12% | 2.5x |
| Meta Retargeting | 8% | 8.0x |
| Email/Klaviyo | 15% | N/A (owned) |

---

## Key Differentiators & Innovation

1. **Causal Reasoning**: Walks causal DAG instead of just alerting on thresholds
2. **Multi-Agent Collaboration**: 6 specialized agents, not monolithic AI
3. **Structured Output**: Decision Packets with math, evidence, risk levels
4. **Learning Loop**: Learns from human feedback to improve future recommendations
5. **Dual Runtime**: Production-ready with both direct API and subprocess fallback
6. **Security-First**: Input filtering, output filtering, agent sandboxing, audit trails
7. **Multi-Client Support**: Central instance serves multiple clients with isolated DBs

---

## Development Status Summary

### ✅ Completed (Phases 1-5)
- [x] Policy engine (476 policies generated)
- [x] Context packager (knows how to build agent context)
- [x] Session manager (can resume investigations)
- [x] Capsule schema (validated Decision Packets)
- [x] Database layer (PostgreSQL + Snowflake repos)
- [x] Runtime switching (Direct API + Subprocess)
- [x] Security guardrails (input/output filtering)
- [x] Knowledge graph enrichment (315 edges, 476 nodes)
- [x] Agent separation (reactive vs proactive)
- [x] API registry (590 endpoints, 828 total)

### 🔄 In Progress (Phases 6-7)
- [ ] Decision Canvas UI components (FiveThings, EvidenceGrid)
- [ ] E2E integration testing (full breach → capsule → feedback flow)

### 📋 Planned (Phases 8-11)
- [ ] Reactive chat UI (Q&A interface)
- [ ] Approval workflows (multi-step routing)
- [ ] Historical backtesting (12-month simulation)
- [ ] Learning system (feedback processing, outcome tracking)

---

## Key Files Quick Reference

| File | Purpose | Lines |
|------|---------|-------|
| `src/context_packager.py` | Build agent injection context | 30,659 |
| `src/policy_engine.py` | Generate and evaluate policies | 21,855 |
| `src/session_manager.py` | Persist agent sessions | 24,395 |
| `src/approval_router.py` | Route through approval chains | 12,688 |
| `src/capsule_validator.py` | Validate Decision Packets | 24,034 |
| `src/entity_resolver.py` | Map names to canonical IDs | 13,010 |
| `src/api/agent_stream.py` | Main streaming API | 48,733 |
| `src/db/repository.py` | Database CRUD layer | 27,087 |
| `README.md` | Project overview | 110 |
| `architecture.md` | Deployment architecture | 213 |
| `.claude/CLAUDE.md` | System instructions | 250+ |
| `PROJECT_TRACKER.md` | Workstream tracker | 750+ |

---

## How to Get Started

### 1. **Understand the Flow**
   - Read this analysis
   - Read `.claude/CLAUDE.md` (system instructions)
   - Review `architecture.md` (deployment details)

### 2. **Run Tests**
   ```bash
   python3 -m pytest tests/ -v
   ```

### 3. **Start Services**
   ```bash
   ssh india-linux
   tmux attach -t e2e-test
   # Verify all 3 panes are running
   ```

### 4. **Test API**
   ```bash
   curl http://localhost:8001/health
   ```

### 5. **Access UI**
   ```
   http://localhost:3500
   ```

---

## Summary

**Decision Canvas OS** is a sophisticated AI-native BI system that automates metric anomaly investigation through causal reasoning, generates structured decision packets, and learns from human feedback. It's production-ready for policy-driven proactive investigations with multi-step approval routing, dual runtime support, and comprehensive security guardrails.

The system is architected as a "digital MBA worker" that answers the "5 Things" question for every business anomaly: WATCHING (what metric?), HAPPENED (what changed?), CHECKED (what did you investigate?), RECOMMEND (what should we do?), and IMPACT (what's the revenue impact?).
