"""Tool-calling agent runner for the agentic graph builder (spec section G).

This module owns two things:

* :func:`run_agent` — the single entry point every phase agent goes through. It
  resolves the ``ClaudeAgentOptions`` for the tool-calling path (attach the graph
  MCP server, ``permission_mode="bypassPermissions"``, the ``mcp__graph__*`` +
  doc-reading allowlist, the builtin-tool denylist, model ``claude-opus-4-8``)
  and drives ``query()`` through the tool-use loop via
  :func:`harness.agent.engine.propose_with_mcp`. When SDK tool-calling is NOT
  available (``KG_AGENT_NO_TOOLCALL=1``), it transparently falls back to the
  structured-output path — the agent returns ``{nodes, edges}`` JSON (via
  :func:`harness.agent.engine.propose_structured`) and a small applier writes
  them through arbitration (:func:`harness.kg.arbitration.write_node_model` /
  :func:`~harness.kg.arbitration.upsert_edge`) — so the writer is never
  reimplemented.

* :func:`run` — the top-level coroutine the CLI ``build`` subcommand drives. It
  runs Phase 0 (backup export → wipe → spine seed) deterministically, then
  delegates Phases 1–4 to :func:`harness.agentic.orchestrator.build`, and emits
  the final build report to ``data/build-report.<runId>.json``.

The SDK and the Neo4j driver are imported lazily *inside* functions so the
offline ``--dry-plan`` path can import this module (and read
:data:`SDK_TOOLCALL_SUPPORTED` / :func:`build_agent_options_preview`) with
neither dependency present.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from harness.agentic import prompts
from harness.kg.config import REPO_ROOT
from harness.store.jsonl import append_event

# ---------------------------------------------------------------------------
# Resolved SDK integration decision (from the discovery phase).
# ---------------------------------------------------------------------------

#: The builder model (per the spec / discovery recommendation).
BUILDER_MODEL: str = "claude-opus-4-8"

#: Whether the installed SDK supports tool-calling (the discovery decision was
#: ``true``). ``KG_AGENT_NO_TOOLCALL=1`` forces the structured-output fallback —
#: used for environments without tool-calling and for offline testing.
SDK_TOOLCALL_SUPPORTED: bool = not os.environ.get("KG_AGENT_NO_TOOLCALL")

#: The graph MCP server config (stdio). ``uv run python -m
#: harness.mcp.graph_server`` exposes the ``mcp__graph__*`` tools; ``PYTHONPATH``
#: is pinned to the repo root so the subprocess imports ``harness``.
GRAPH_MCP_SERVER: dict[str, Any] = {
    "type": "stdio",
    "command": "uv",
    "args": ["run", "python", "-m", "harness.mcp.graph_server"],
    "env": {"PYTHONPATH": str(REPO_ROOT)},
}

#: The full graph MCP server mapping passed to ``ClaudeAgentOptions.mcp_servers``.
MCP_SERVERS: dict[str, Any] = {"graph": GRAPH_MCP_SERVER}

#: The ``mcp__graph__*`` write + doc-reading tools the agent is allowed to call.
#: Everything else (notably the builtin Bash/Write/Edit/… in :data:`DISALLOWED_TOOLS`)
#: is denied. Tool names mirror :mod:`harness.mcp.graph_server`.
ALLOWED_TOOLS: list[str] = [
    # writes
    "mcp__graph__create_metric_node",
    "mcp__graph__create_business_node",
    "mcp__graph__create_domain_node",
    "mcp__graph__create_product_node",
    "mcp__graph__draw_edge",
    # graph reads
    "mcp__graph__lookup_node",
    "mcp__graph__search_nodes",
    "mcp__graph__kg_status",
    # doc-reading (read source files, never the graph)
    "mcp__graph__list_metrics",
    "mcp__graph__get_metric_source",
    "mcp__graph__get_bc2_sql",
    "mcp__graph__list_metrics_by_domain",
    "mcp__graph__list_metrics_by_scope",
    "mcp__graph__lookup_metric_notes",
    "mcp__graph__get_chart_registry_entry",
    "mcp__graph__inspect_bc2_sources",
]

#: Builtin SDK tools the agent must NOT use — the agent works ONLY through the
#: graph MCP server, never the filesystem / shell / web / subagents.
DISALLOWED_TOOLS: list[str] = [
    "Bash",
    "BashOutput",
    "KillBash",
    "KillShell",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "NotebookRead",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "ExitPlanMode",
    "SlashCommand",
]

#: Per-agent wall-clock timeout (seconds). A node/structural slice does many
#: tool calls, so it is generous; tunable via env.
_AGENT_TIMEOUT_S: float = float(os.environ.get("KG_AGENT_TIMEOUT_S", "1800"))

#: Per-agent hard cost cap (USD), forwarded to the SDK. ``None`` (default) = no
#: cap; set ``KG_AGENT_BUDGET_USD`` to bound spend per slice.
_AGENT_BUDGET_USD: float | None = (
    float(os.environ["KG_AGENT_BUDGET_USD"])
    if os.environ.get("KG_AGENT_BUDGET_USD")
    else None
)


def build_agent_options_preview() -> dict[str, Any]:
    """Return the resolved ``ClaudeAgentOptions`` as a plain dict (offline-safe).

    Renders exactly the options :func:`run_agent` would pass to the SDK, WITHOUT
    importing ``claude_agent_sdk`` or touching Neo4j — so ``--dry-plan`` can print
    the resolved configuration. The ``system_prompt`` / ``user_prompt`` are
    per-slice and are printed separately by the plan.

    Returns:
        A JSON-serializable mapping of the resolved SDK options.
    """
    return {
        "model": BUILDER_MODEL,
        "permission_mode": "bypassPermissions",
        "setting_sources": [],
        "mcp_servers": MCP_SERVERS,
        "allowed_tools": ALLOWED_TOOLS,
        "disallowed_tools": DISALLOWED_TOOLS,
        "max_budget_usd": _AGENT_BUDGET_USD,
        "per_call_timeout_s": _AGENT_TIMEOUT_S,
        "tool_calling": SDK_TOOLCALL_SUPPORTED,
        "fallback": "structured_output {nodes, edges} -> arbitration applier"
        if not SDK_TOOLCALL_SUPPORTED
        else None,
    }


# ---------------------------------------------------------------------------
# Structured-output fallback applier (writer is REUSED, never reimplemented).
# ---------------------------------------------------------------------------


def _apply_fallback(result: dict[str, Any], *, label: str) -> dict[str, int]:
    """Write a fallback agent's ``{nodes, edges}`` through the arbitration writer.

    Used only on the no-tool-calling path: the agent returns the nodes + edges it
    would have created and this applies them via
    :func:`harness.kg.arbitration.write_node_model` (nodes, built into a
    :class:`harness.kg.models.Metric`) and
    :func:`~harness.kg.arbitration.upsert_edge` (edges) — the SAME single writer
    the MCP tools use. Per-item failures are isolated so one bad row never aborts
    the slice.

    Args:
        result: The parsed agent reply (``{"nodes": [...], "edges": [...]}``).
        label: The slice label, for event attribution.

    Returns:
        ``{"nodes_written", "edges_written", "errors"}``.
    """
    from harness.kg.arbitration import upsert_edge, write_node_model
    from harness.kg.driver import get_db
    from harness.kg.models import Metric

    db = get_db()
    nodes_written = 0
    edges_written = 0
    errors = 0

    for raw in result.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        try:
            write_node_model(db, Metric(**raw))
            nodes_written += 1
        except Exception as exc:  # noqa: BLE001 — isolate per-node failures
            errors += 1
            append_event(
                {"type": "build_fallback_node_error", "label": label, "error": str(exc)}
            )

    for raw in result.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        try:
            upsert_edge(
                db,
                rel_type=str(raw.get("rel_type")),
                from_label=str(raw.get("from_label")),
                from_key=str(raw.get("from_key")),
                to_label=str(raw.get("to_label")),
                to_key=str(raw.get("to_key")),
                props=dict(raw.get("props") or {}),
            )
            edges_written += 1
        except Exception as exc:  # noqa: BLE001 — isolate per-edge failures
            errors += 1
            append_event(
                {"type": "build_fallback_edge_error", "label": label, "error": str(exc)}
            )

    return {
        "nodes_written": nodes_written,
        "edges_written": edges_written,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# run_agent — the single agent entry point for every phase.
# ---------------------------------------------------------------------------


async def run_agent(
    system: str,
    user: str,
    *,
    label: str,
    max_turns: int = 60,
) -> dict[str, Any]:
    """Run ONE phase agent and return its run telemetry.

    On the tool-calling path (the default; :data:`SDK_TOOLCALL_SUPPORTED`) it
    drives :func:`harness.agent.engine.propose_with_mcp` with the resolved
    builder options — the agent reads its source via the doc tools and WRITES the
    graph directly through ``mcp__graph__*``. On the fallback path it calls
    :func:`harness.agent.engine.propose_structured` with
    :data:`harness.agentic.prompts.FALLBACK_OUTPUT_SCHEMA` and applies the
    returned ``{nodes, edges}`` via :func:`_apply_fallback` (reusing the
    arbitration writer).

    Args:
        system: The phase system prompt.
        user: The per-slice task prompt.
        label: A slice label (e.g. ``"nodes:namespace=google_ads"``) used for cost
            attribution and event emission.
        max_turns: Tool-use turn budget for the tool-calling path.

    Returns:
        ``{"label", "cost_usd", "num_turns", "tool_calls", "applied"?, "text"?}``
        — ``applied`` is the :func:`_apply_fallback` summary on the fallback path.
    """
    from harness.agent import engine  # lazy: keeps --dry-plan offline-safe

    if SDK_TOOLCALL_SUPPORTED:
        run = await engine.propose_with_mcp(
            system_prompt=system,
            user_prompt=user,
            mcp_servers=MCP_SERVERS,
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=DISALLOWED_TOOLS,
            max_turns=max_turns,
            model=BUILDER_MODEL,
            per_call_timeout_s=_AGENT_TIMEOUT_S,
            max_budget_usd=_AGENT_BUDGET_USD,
            cwd=str(REPO_ROOT),
            label=label,
        )
        return {
            "label": label,
            "cost_usd": run.get("cost_usd"),
            "num_turns": run.get("num_turns"),
            "tool_calls": run.get("tool_calls"),
            "text": run.get("text"),
        }

    # Fallback: structured {nodes, edges} -> arbitration applier.
    result = await engine.propose_structured(
        system_prompt=system,
        user_prompt=user,
        schema=prompts.FALLBACK_OUTPUT_SCHEMA,
        model=BUILDER_MODEL,
        per_call_timeout_s=_AGENT_TIMEOUT_S,
        max_budget_usd=_AGENT_BUDGET_USD,
        dashboard=label,
    )
    applied = _apply_fallback(result, label=label)
    return {
        "label": label,
        "cost_usd": (result.get("_meta") or {}).get("cost_usd"),
        "num_turns": (result.get("_meta") or {}).get("num_turns"),
        "tool_calls": 0,
        "applied": applied,
    }


# ---------------------------------------------------------------------------
# Phase 0 (deterministic) + run() — the top-level CLI driver.
# ---------------------------------------------------------------------------


def _new_run_id() -> str:
    """Mint a timestamped build run id (``YYYYMMDDTHHMMSSZ``)."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def phase0_backup_and_seed(
    *, run_id: str, smoke: bool, resume: bool = False
) -> dict[str, Any]:
    """Phase 0 (deterministic): backup export → wipe → spine seed.

    The Neo4j backup is the ONLY safety net (dc-kg is not a git repo), so a live
    build ALWAYS exports first, then wipes, then re-seeds the tri-axis spine. The
    backup / wipe / driver imports are local so this is only reached on a live
    run (never from ``--dry-plan``). On a ``smoke`` build the wipe is skipped so a
    small subset can be layered onto the existing graph non-destructively. On a
    ``resume`` build the wipe is also skipped so the partially-built graph is kept
    and the orchestrator only fills in the missing buckets/edges.

    Args:
        run_id: The build run id (recorded in the phase-0 event).
        smoke: When ``True``, skip the destructive wipe (seed is idempotent).
        resume: When ``True``, skip the wipe to preserve a partial graph.

    Returns:
        ``{"backup_path", "wipe"?, "seed"}`` summary.
    """
    from harness.ingest.spine_seed import seed_spine
    from harness.kg.driver import get_db
    from harness.store import backup

    db = get_db()
    backup_path = backup.export_graph(db)
    append_event(
        {"type": "build_phase", "phase": 0, "run_id": run_id, "step": "backup",
         "path": str(backup_path)}
    )

    summary: dict[str, Any] = {"backup_path": str(backup_path)}
    if not smoke and not resume:
        summary["wipe"] = backup.wipe(db)
        append_event(
            {"type": "build_phase", "phase": 0, "run_id": run_id, "step": "wipe",
             **summary["wipe"]}
        )
    # Re-seed the spine (idempotent MERGE — safe whether or not we wiped).
    summary["seed"] = seed_spine()
    append_event(
        {"type": "build_phase", "phase": 0, "run_id": run_id, "step": "spine_seed",
         "nodes": len(summary["seed"])}
    )
    return summary


async def run(
    *,
    smoke: bool = False,
    namespaces: list[str] | None = None,
    dry_plan: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    """Drive the full agentic build (phases 0–4) and write the build report.

    This is the coroutine the CLI ``build`` subcommand runs. On a live build it:

    1. Phase 0 — :func:`phase0_backup_and_seed` (backup → wipe → seed).
    2. Phases 1–4 — delegates to :func:`harness.agentic.orchestrator.build`,
       which fans node agents across namespace/domain buckets (BARRIER), then
       structural-edge agents, then weave-causal agents, then the critique pass.
    3. Writes the merged report to ``data/build-report.<run_id>.json`` and emits a
       final ``build_done`` event.

    ``dry_plan=True`` never reaches this (the CLI renders the plan offline and
    does not call :func:`run`); it is accepted only so the signature matches the
    orchestrator's ``build`` for symmetry.

    Args:
        smoke: Build only one small namespace (the ``blended.*`` chain) and skip
            the destructive wipe.
        namespaces: Restrict the node phase to these ``source`` namespaces (e.g.
            ``["google_ads", "meta_ads"]``); ``None`` = all.
        dry_plan: Offline plan-only flag (handled by the CLI, not here).

    Returns:
        The final build-report dict (also written to disk).
    """
    from harness.agentic import orchestrator

    run_id = _new_run_id()
    append_event({"type": "build_start", "run_id": run_id, "smoke": smoke,
                  "namespaces": namespaces, "resume": resume,
                  "tool_calling": SDK_TOOLCALL_SUPPORTED})

    phase0 = phase0_backup_and_seed(run_id=run_id, smoke=smoke, resume=resume)

    report = await orchestrator.build(
        smoke=smoke,
        namespaces=namespaces,
        dry_plan=dry_plan,
        resume=resume,
        run_id=run_id,
    )
    report["phase0"] = phase0
    report["run_id"] = run_id

    report_path = REPO_ROOT / "data" / f"build-report.{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    import json

    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    report["report_path"] = str(report_path)
    append_event({"type": "build_done", "run_id": run_id,
                  "report_path": str(report_path),
                  "total_cost_usd": report.get("total_cost_usd")})
    return report
