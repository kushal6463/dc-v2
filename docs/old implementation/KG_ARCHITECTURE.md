# Knowledge Graph (KG) Architecture Guide

## Overview

The Knowledge Graph is a **directed acyclic graph (DAG) of causal relationships** between metrics in Decision Canvas OS. It enables rapid root cause analysis by allowing the agent to traverse causal connections instead of checking metrics randomly.

---

## Are We Using Neo4j?

**No, we're NOT using Neo4j.** We're using **NetworkX** — a much simpler Python library.

### Why NetworkX Instead of Neo4j?

#### What We're Using

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

#### Comparison Table

| Aspect | NetworkX | Neo4j |
|--------|----------|-------|
| **Setup** | `pip install networkx` | Standalone server, Docker |
| **Query latency** | <1ms (in-memory) | 10-50ms (over HTTP) |
| **Persistence** | JSON file (simple) | Graph database (complex) |
| **Scaling** | 476 nodes = negligible | Overkill for our size |
| **Learning curve** | 1 hour | 1 week |
| **Maintenance** | None | Server upkeep, backups |
| **Cost** | Free | Free (self-hosted) or $$$ (managed) |

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

## Why This Design?

### Reasoning

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

## How Queries Work (Real Code)

```python
# From src/kg/causal_edges.py
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

## KG Data Flow Example

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

## Key Insights

### Why NetworkX is the Right Choice

1. **Speed**: <1ms queries vs 10-50ms over HTTP
2. **Simplicity**: Pure Python, no external servers to manage
3. **Resilience**: Falls back to JSON if Snowflake unavailable
4. **Focused**: Only features we need (BFS/DFS), not enterprise bloat
5. **Cost**: Zero infrastructure cost

### The KG is Your Investigation Autopilot

Instead of blindly checking every metric, the agent:
1. Walks the graph to find likely root causes
2. Investigates in priority order (highest confidence first)
3. Stops when root cause found
4. Projects downstream impact
5. Recommends actions with confidence scores

**Without the KG**: Random investigation, 5+ minutes, low confidence
**With the KG**: Guided investigation, 10-30 seconds, 80%+ confidence

---

## Related Files

- `src/kg/auto_sync.py` — Keeps graph synchronized with Snowflake
- `src/kg/causal_edges.py` — Query interface (get_upstream_causes, get_downstream_effects)
- `.claude/mcp/build_cache.py` — Rebuilds graph from Snowflake/CSV
- `graph_cache_baker-creek.json` — Fallback JSON cache (50MB)
