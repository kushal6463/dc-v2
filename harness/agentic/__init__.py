"""Agentic graph builder (section G of the executable spec).

This package ports ContextLayer's agentic ingestion discipline
(``survey → deep-dive → weave → critique``) to the dc-kg metric/edge layer. An
LLM reads every metric from the offline source files and constructs the graph
(nodes + edges) **itself**, auto-approved, via the ``mcp__graph__*`` write
tools — replacing the removed deterministic skeleton / causal / edge-seed
construction.

Modules:

* :mod:`harness.agentic.prompts` — the phase system prompts (NODE / STRUCTURAL /
  WEAVE / CRITIQUE) and the per-phase user-prompt builders, adapted from the
  ContextLayer ``CINEMATIC_SYSTEM`` / ``WEAVE_SYSTEM`` / ``CRITIQUE_SYSTEM``
  discipline to the metric ontology + the locked schema decisions.
* :mod:`harness.agentic.engine` — :func:`run_agent`, the tool-calling agent
  runner. Configures :class:`ClaudeAgentOptions` with the graph MCP server,
  ``permission_mode="bypassPermissions"``, the ``mcp__graph__*`` + doc-reading
  allowlist, and the builtin-tool denylist, then drives ``query()`` through the
  tool-use loop until the run finishes. A structured-output fallback
  (``{nodes, edges}`` JSON via :func:`harness.agent.engine.propose_structured`
  + an arbitration applier) is provided for environments without SDK
  tool-calling.
* :mod:`harness.agentic.orchestrator` — :func:`build`, the phased-parallel
  driver. Phase 0 seeds the spine; phase 1 fans node-creation agents across
  ~8–12 namespace/domain buckets (BARRIER); phase 2 draws structural edges;
  phase 3 weaves causal edges; phase 4 critiques and writes the build report.
* :mod:`harness.agentic.runner` — :func:`run`, the top-level coroutine the CLI
  ``build`` subcommand drives (backup → wipe → seed → phases 1-4 → report).

Imports are kept lazy (the ``claude_agent_sdk`` and the Neo4j driver are
imported *inside* functions, never at module import time) so the offline
``--dry-plan`` path can import this package and render the plan with neither the
SDK nor Neo4j present.
"""

from __future__ import annotations
