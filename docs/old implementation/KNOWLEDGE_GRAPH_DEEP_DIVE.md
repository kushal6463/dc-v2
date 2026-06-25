# Knowledge Graph Deep Dive - Complete Guide

## What is the Knowledge Graph?

The Knowledge Graph (KG) is a **directed acyclic graph (DAG) of causal relationships** between metrics. It answers questions like:

- "If ROAS drops, what metric caused it?" (upstream/root causes)
- "If CPC spikes, what metrics will be affected?" (downstream/impact)
- "How does spend affect revenue?" (causal path)

### Structure

- **476 nodes** = Metrics (ROAS, spend, CPC, conversion rate, etc.)
- **315 edges** = Causal relationships ("X causes Y", "X influences Y")
- **Confidence scores** = How strong is the relationship (0.5-0.95)

---

## What Do We Use It For?

### 1. Root Cause Analysis (Primary Use)

When ROAS drops to 2.5x, the system queries:

```python
get_upstream_causes("roas", depth=5)
# Returns: [
#   {"metric": "revenue", "relationship": "numerator"},
#   {"metric": "spend", "relationship": "denominator"},
#   {"metric": "cpc", "confidence": 0.85, "mechanism": "higher spend"},
#   {"metric": "bid_strategy", "confidence": 0.92, "mechanism": "auto-increase"}
# ]
```

**Result**: Find that **bid strategy changed yesterday** → root cause found in 5 seconds

### 2. Impact Forecasting (Secondary Use)

When we recommend a bid reduction, query:

```python
get_downstream_effects("cpc", depth=3)
# Returns what other metrics will improve:
# - spend (will decrease)
# - roas (will increase)
# - profit_margin (will increase)
```

### 3. Decision Confidence

Each recommendation's confidence is boosted by:
- How certain is the causal relationship? (edge confidence)
- How many alternative causes were ruled out?
- Did similar root causes occur before? (Beads match)

---

## How is it Built?

### Phase 1: Source Data (From Snowflake)

The KG loads from `CONFIG_ONTOLOGY_CAUSAL_EDGES` table:

```sql
SELECT 
  from_metric_id,      -- e.g., "bid_strategy"
  to_metric_id,        -- e.g., "cpc"
  relationship_type,   -- "causes", "correlates_with"
  confidence,          -- 0-1 score
  mechanism,           -- explanation (human-readable)
  discovered_date,     -- when relationship found
  evidence             -- supporting data
FROM CONFIG_ONTOLOGY_CAUSAL_EDGES
ORDER BY confidence DESC;
```

**Example rows**:

```
bid_strategy   → cpc              | causes   | 0.92 | "Manual bid increases reduce CPC"
cpc            → spend            | causes   | 0.88 | "Higher CPC = higher total spend"
spend          → roas             | causes   | 0.85 | "Spend is denominator in ROAS formula"
competitor_ads → cpc              | causes   | 0.72 | "Competitive bidding increases CPC"
seasonality    → conversion_rate  | causes   | 0.80 | "Q1 peak drives higher conversion"
```

### Phase 2: Load into Memory (`src/kg/auto_sync.py`)

On startup, the system:
1. Reads all 315 edges from Snowflake
2. Loads 476 metric nodes from `CONFIG_ONTOLOGY_METRICS`
3. Builds **NetworkX directed graph** in memory
4. Creates **adjacency index** for fast lookups

**Why NetworkX?**
- BFS/DFS traversal for root cause finding: <1ms
- In-memory so no I/O latency
- Handles cycles (causal feedback loops)

### Phase 3: Enrichment (`src/kg/causal_edges.py`)

Before agent uses the KG, enrich each node with:

```python
node = {
    "id": "roas",
    "name": "Blended ROAS",
    "category": "kpi",
    "formula": "Revenue / Spend",
    
    # API specs (how to fetch)
    "data_access": {
        "api": [
            {
                "endpoint": "/api/marketing-mix/charts/roas-trending",
                "method": "GET",
                "params": [
                    {"name": "channel", "enum": ["google", "meta", "tiktok"]},
                    {"name": "device", "enum": ["mobile", "desktop"]}
                ]
            }
        ],
        "snowflake_fallback": {
            "table": "analytics.marketing_metrics",
            "column": "blended_roas"
        }
    },
    
    # Chart info (how to visualize)
    "charts": [
        {
            "chart_id": "roas-trending",
            "bc_component": "BlendedROASMetric",
            "primitive_type": "kpi_card",
            "audio_url": "/audio/roas-explained.mp3"
        }
    ],
    
    # Thresholds
    "policies": [
        {"type": "threshold", "value": 4.0, "severity": "normal"},
        {"type": "threshold", "value": 3.0, "severity": "warning"},
        {"type": "threshold", "value": 2.0, "severity": "critical"}
    ]
}
```

---

## KG Usage Example

### Scenario: ROAS dropped to 2.5x

```python
# 1. AGENT QUERIES KG
from src.kg.causal_edges import get_upstream_causes

causes = get_upstream_causes("roas", depth=5)

# 2. KG TRAVERSAL (NetworkX BFS from ROAS node)
#    Layer 1: Direct parents
#      - revenue (formula numerator)
#      - spend (formula denominator)
#    Layer 2: What affects spend?
#      - cpc (higher CPC = higher spend)
#      - conversion_rate (affects volume)
#    Layer 3: What affects CPC?
#      - bid_strategy (higher bids = higher CPC)
#      - competitor_bids (market competition)
#    Layer 4: What changed recently?
#      - bid_strategy: YES ← ROOT CAUSE

# 3. RESULT RETURNED TO AGENT
#    [
#      {metric: "spend", confidence: 0.95, depth: 1},
#      {metric: "cpc", confidence: 0.88, depth: 2},
#      {metric: "bid_strategy", confidence: 0.92, depth: 3}
#    ]

# 4. AGENT INVESTIGATES
#    "Spend increased. Why? CPC spiked. Why? 
#     Bid strategy was auto-increased yesterday. 
#     ROOT CAUSE FOUND."
```

---

## How is the KG Maintained?

### Build Process (`build_cache.py`)

The KG is rebuilt periodically:

```bash
# Rebuild from CSV seeds (development)
python .claude/mcp/build_cache.py --source csv --client baker-creek

# Rebuild from live API (production)
python .claude/mcp/build_cache.py --source api --client baker-creek
```

**Output**: `graph_cache_baker-creek.json` (~50MB)

### Sync Strategy (`src/kg/auto_sync.py`)

- **Load on startup**: KG is loaded into memory
- **5-minute TTL**: If query older than 5 min, refresh from Snowflake
- **Event-driven**: If someone adds new edge via API, push update immediately
- **Fallback**: If Snowflake unavailable, use cached JSON file

---

## KG vs Metric Relationships

| Aspect | KG Edge | Regular Metric Relationship |
|--------|---------|---------------------------|
| **Causality** | A *causes* B (directional) | A *correlated with* B (bidirectional) |
| **Use** | Root cause analysis, impact forecasting | Monitoring, alerting |
| **Confidence** | 0.5-0.95 (subjective) | Statistical (correlation coefficient) |
| **Update frequency** | Quarterly (added by analysts) | Continuous (calculated from data) |
| **Size** | 315 edges | 476×476 = 226K possible correlations |

---

## Example KG Edges in Baker Creek

### TIER 1: Formula Decomposition

```
├─ revenue/spend → roas (confidence: 1.0, formula)
├─ orders × aov → revenue (confidence: 1.0, formula)
└─ clicks × cpc → spend (confidence: 1.0, formula)
```

### TIER 2: Channel Dynamics

```
├─ google_cpc → google_spend (confidence: 0.95)
├─ meta_impressions → meta_cpc (confidence: 0.88)
└─ conversion_rate → orders (confidence: 0.92)
```

### TIER 3: Business Levers

```
├─ bid_strategy → cpc (confidence: 0.92)
├─ daily_budget → impressions (confidence: 0.85)
├─ creative_quality → ctr (confidence: 0.78)
└─ email_frequency → unsubscribe_rate (confidence: 0.81)
```

### TIER 4: Market Factors

```
├─ competitor_ads → cpc (confidence: 0.72)
├─ seasonality → conversion_rate (confidence: 0.80)
├─ product_availability → aov (confidence: 0.65)
└─ brand_mentions → ctr (confidence: 0.55)
```

---

## How Agent Uses KG

### When investigating ROAS drop:

```
Agent: "ROAS dropped. Let me find root cause."

Step 1: get_upstream_causes("roas", depth=5)
  └─ NetworkX BFS from roas node
  └─ Returns: [spend, revenue, cpc, bid_strategy, ...]

Step 2: "Which upstream metric actually changed?"
  ├─ Query spend: stable ✗
  ├─ Query revenue: stable ✗
  ├─ Query cpc: UP 45% ✓ ANOMALY
  
Step 3: "What causes CPC to spike?"
  ├─ get_upstream_causes("cpc", depth=3)
  ├─ Returns: [bid_strategy, competitor_bids, ...]
  
Step 4: "Did bid strategy change?"
  ├─ Check: bid_strategy auto-increased yesterday ✓
  ├─ Confidence: 0.92
  └─ ROOT CAUSE FOUND

Step 5: "What will improve if we fix it?"
  ├─ get_downstream_effects("cpc", depth=2)
  ├─ Returns: [spend, roas, profit_margin]
  └─ Action: Revert bid strategy
```

---

## Architecture: How Snowflake → NetworkX Works

```
┌────────────────────────────────────┐
│   Snowflake                         │
│   CONFIG_ONTOLOGY_CAUSAL_EDGES     │
│   (315 rows, permanent storage)    │
│                                    │
│  from_metric | to_metric | conf   │
│  ─────────────────────────────────│
│  bid_strategy  | cpc     | 0.92   │
│  cpc           | spend   | 0.88   │
│  spend         | roas    | 0.85   │
│  (315 total)                      │
└────────────────────────────────────┘
        ↓ (On Startup + 5min TTL)
┌────────────────────────────────────┐
│   Python Memory                     │
│   NetworkX DiGraph                 │
│   (In-memory, <1ms queries)        │
│                                    │
│   Nodes: [roas, spend, cpc, ...]  │
│   Edges: 315 directed edges        │
│                                    │
│   graph.add_edge(from, to,         │
│                  weight=conf)      │
└────────────────────────────────────┘
        ↓ (On Query)
┌────────────────────────────────────┐
│   Agent Uses KG                    │
│   get_upstream_causes(metric, 5)   │
│   ← NetworkX BFS traversal         │
│   → Returns ancestors in order     │
└────────────────────────────────────┘
```

---

## Why NetworkX Instead of Neo4j?

### What We're Using

```python
# src/kg/auto_sync.py
import networkx as nx

# Load edges from Snowflake
edges = fetch_from_snowflake("CONFIG_ONTOLOGY_CAUSAL_EDGES")

# Build in-memory graph
graph = nx.DiGraph()  # Directed acyclic graph
for edge in edges:
    graph.add_edge(edge.from_metric, edge.to_metric, 
                   weight=edge.confidence)

# Query: Find root causes
def get_upstream_causes(metric_id, depth=5):
    ancestors = nx.ancestors(graph, metric_id)  # BFS traversal
    return sorted(ancestors, key=lambda x: depth_from(x, metric_id))
```

### Comparison Table

| Aspect | NetworkX | Neo4j |
|--------|----------|-------|
| **Setup** | `pip install networkx` | Standalone server, Docker |
| **Query latency** | <1ms (in-memory) | 10-50ms (over HTTP) |
| **Persistence** | JSON file (simple) | Graph database (complex) |
| **Scaling** | 476 nodes = negligible | Overkill for our size |
| **Learning curve** | 1 hour | 1 week |
| **Maintenance** | None | Server upkeep, backups |
| **Cost** | Free | Free (self-hosted) or $$$ (managed) |

### Why This Design?

**1. Graph is small: 476 nodes, 315 edges**
- Neo4j is designed for billions of nodes
- We're tiny — overkill to introduce that complexity

**2. Queries are simple:**
- "Give me ancestors" = BFS
- "Give me descendants" = DFS
- Neo4j is overkill for this
- NetworkX handles it natively

**3. Latency matters:**
- Investigation must feel instant (<30s end-to-end)
- NetworkX in-memory: <1ms
- Neo4j over HTTP: 10-50ms
- Every millisecond counts when chaining 10 queries

**4. No complex graph features needed:**
- No full-text search on edges ❌
- No property lookups ❌
- No real-time collaboration ❌
- Just BFS/DFS/shortest path ✅

---

## Query Implementation (Real Code)

### From `src/kg/causal_edges.py`

```python
class CausalGraph:
    def __init__(self):
        self.graph = nx.DiGraph()
        self._load_from_snowflake()
    
    def get_upstream_causes(self, metric_id, depth=5):
        """Find all metrics that cause this metric (root causes)"""
        # NetworkX ancestors = all nodes with paths TO metric_id
        ancestors = nx.ancestors(self.graph, metric_id)
        
        # Sort by distance (closest ancestors first = most direct causes)
        sorted_ancestors = sorted(
            ancestors,
            key=lambda x: nx.shortest_path_length(self.graph, x, metric_id)
        )
        
        # Return top N by depth
        return [
            {
                "metric": ancestor,
                "distance": nx.shortest_path_length(self.graph, ancestor, metric_id),
                "confidence": self.graph[ancestor][metric_id]["weight"]
            }
            for ancestor in sorted_ancestors[:depth]
        ]
    
    def get_downstream_effects(self, metric_id, depth=5):
        """Find all metrics affected by this metric (impact)"""
        # NetworkX descendants = all nodes reachable FROM metric_id
        descendants = nx.descendants(self.graph, metric_id)
        
        sorted_descendants = sorted(
            descendants,
            key=lambda x: nx.shortest_path_length(self.graph, metric_id, x)
        )
        
        return [
            {
                "metric": descendant,
                "distance": nx.shortest_path_length(self.graph, metric_id, descendant),
                "confidence": self.graph[metric_id][descendant]["weight"]
            }
            for descendant in sorted_descendants[:depth]
        ]
    
    def find_causal_path(self, from_metric, to_metric):
        """Find the path explaining how A affects B"""
        try:
            path = nx.shortest_path(self.graph, from_metric, to_metric)
            return {
                "path": path,
                "length": len(path) - 1,
                "explanation": " → ".join(path)
            }
        except nx.NetworkXNoPath:
            return None
```

---

## Persistence Strategy

The graph is **cached to JSON file** for resilience:

```
graph_cache_baker-creek.json (50MB)
├─ nodes: [
│   {id: "roas", name: "Blended ROAS", category: "kpi", ...},
│   {id: "spend", name: "Total Spend", ...},
│   ...
├─ edges: [
│   {from: "bid_strategy", to: "cpc", confidence: 0.92, ...},
│   ...
```

**Why**: If Snowflake is down during startup, load from JSON file instead. KG still works.

---

## When Would We Need Neo4j?

Switch to Neo4j only if:
- Graph grows to **100K+ nodes** (different product lines, external APIs)
- Need **complex graph algorithms** (PageRank, community detection)
- Need **real-time collaborative editing** (multiple teams updating edges)
- Need **full-text search** on edge descriptions
- Need **versioning/time-travel** ("what was the graph on Jan 1?")

**Current status**: None of these apply. NetworkX is perfect for our needs.

---

## Key Insight

**The KG is your investigation autopilot.** Instead of blindly checking every metric, the agent:
1. Walks the graph to find likely root causes
2. Investigates in priority order (highest confidence first)
3. Stops when root cause found
4. Projects downstream impact
5. Recommends actions with confidence scores

**Without the KG**: Random investigation, 5+ minutes, low confidence  
**With the KG**: Guided investigation, 10-30 seconds, 80%+ confidence

---

## Summary

```
┌─────────────────────────────────────────┐
│  Decision Canvas: "Simple is better"    │
├─────────────────────────────────────────┤
│  Storage: Snowflake (permanent)         │
│  Cache: NetworkX in-memory (<1ms)       │
│  Fallback: JSON file (resilience)       │
│  Queries: BFS/DFS (reason about         │
│           causality in 10-30s)          │
└─────────────────────────────────────────┘
```

We use **Snowflake as the source of truth** (permanent storage), **NetworkX as the runtime engine** (fast queries), and **JSON as the fallback** (resilience). It's a 3-tier approach that's simple, fast, and reliable.

---

## Related Files & Tools

| File | Purpose |
|------|---------|
| `src/kg/auto_sync.py` | Keeps graph synchronized with Snowflake |
| `src/kg/causal_edges.py` | Query interface (get_upstream_causes, etc.) |
| `.claude/mcp/build_cache.py` | Rebuilds graph from Snowflake/CSV |
| `graph_cache_baker-creek.json` | Fallback JSON cache (50MB) |
| `CONFIG_ONTOLOGY_CAUSAL_EDGES` | Snowflake table with 315 edges |
| `CONFIG_ONTOLOGY_METRICS` | Snowflake table with 476 nodes |

---

## Quick Commands

```bash
# View current KG
cat graph_cache_baker-creek.json | head -100

# Rebuild KG from Snowflake
python .claude/mcp/build_cache.py --source api --client baker-creek

# Rebuild KG from CSV (dev)
python .claude/mcp/build_cache.py --source csv --client baker-creek

# Check sync status
curl http://localhost:8001/health
```

---

## Performance Metrics

- **Graph load time**: <500ms (Snowflake → NetworkX)
- **Upstream causes query**: <1ms (BFS on 315 edges)
- **Downstream effects query**: <1ms (DFS on 315 edges)
- **Shortest path query**: <2ms (Dijkstra's algorithm)
- **Cache hit rate**: 99%+ (5-minute TTL)
- **Fallback latency**: Negligible (JSON file in-memory)

---

This is the foundation of Decision Canvas OS's intelligence: a lightweight, fast, and resilient causal graph that enables rapid root cause analysis and impact forecasting.
