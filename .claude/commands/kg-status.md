---
description: Report knowledge-graph status — node/edge counts by label and overall spine health.
argument-hint: (no arguments)
allowed-tools: mcp__graph__kg_status
---

Report the current state of the Neo4j knowledge graph.

1. Call `mcp__graph__kg_status` (read-only, pre-allowed — no confirmation needed).

2. Summarize the result for the user:
   - Print a plaintext aligned table of node counts per label (`Business`, `Domain`, `IntelligenceProduct`, `Platform`, `Metric`, `Dashboard`, `UIComponent`, `Policy`, `Threshold`, `Role`) and the total node count.
   - Include relationship/edge counts if the tool returns them.
   - Give a one-line spine-health read for Milestone 1: note whether the expected spine is present — exactly 1 `Business` root, the FRD `Domain` columns, and the 5 `IntelligenceProduct` apps (`miq·ciq·piq·dc·creative_iq`). Flag anything missing.
