"""Ingestion orchestrator for the metric / UIComponent engine.

This is the fan-out / checkpoint layer of Milestone 2 (implementation plan
section 5b). It does two jobs:

* :func:`get_spine_context` — read the small, linkable spine slice (the Business
  root plus Domain / IntelligenceProduct ids) from Neo4j. This is the RAG context
  the proposer is allowed to link against; it is intentionally tiny (ids + names
  only) so the proposer's context window stays flat (plan section 6, tactic 5).
  This read is the only Neo4j access the proposer pipeline performs, and it is
  strictly read-only.
* :func:`ingest_dashboards` — drive :func:`harness.ingest.proposer.propose_for_dashboard`
  across many dashboards with a bounded :class:`asyncio.Semaphore`, persisting
  each dashboard's proposals via :func:`harness.store.proposals.write_proposals`
  and logging a per-dashboard checkpoint event via
  :func:`harness.store.jsonl.append_event`. Errors are captured per dashboard so
  one failure never aborts the whole run.

The orchestrator never writes Neo4j (only the proposal queue + event log); the
M1 arbitration writer remains the single graph writer, reached later via
:mod:`harness.ingest.apply`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from harness.ingest.proposer import propose_for_dashboard_with_cost
from harness.kg.config import get_settings
from harness.kg.driver import GraphDB, get_db
from harness.store.jsonl import append_event
from harness.store.proposals import (
    load_dashboard_proposals,
    new_run_id,
    write_proposals,
)

#: Default bound on concurrent per-dashboard proposer agents.
DEFAULT_CONCURRENCY: int = 6

#: Type of the optional live-canvas emit hook: ``emit(type, run_id=..., **data)``.
EmitHook = Callable[..., None]


def get_spine_context(db: Any = None) -> dict:
    """Read the linkable spine context (Business + Domains + Products) from Neo4j.

    Returns only the identifiers and human names the proposer may link against —
    never the full node payloads — so the agent's context window stays small. The
    query is read-only.

    Args:
        db: An optional connected :class:`~harness.kg.driver.GraphDB`; when
            ``None`` the process-wide singleton from
            :func:`~harness.kg.driver.get_db` is used.

    Returns:
        ``{"business": {...}, "domains": [{"domain_id", "name"}...],
        "products": [{"product_id", "display_name"}...],
        "platforms": [{"platform_id", "platform_name"}...]}``. Missing pieces
        come back as ``{}`` / ``[]`` so the proposer can always render the
        context (``platforms`` is ``[]`` today since none exist yet).
    """
    graph: GraphDB = db or get_db()
    business_id = get_settings().business_id

    business_rows = graph.read(
        "MATCH (b:Business {business_id: $bid}) "
        "RETURN b.business_id AS business_id, b.display_name AS display_name, "
        "b.business_type AS business_type",
        bid=business_id,
    )
    business: dict[str, Any] = business_rows[0] if business_rows else {}

    domain_rows = graph.read(
        "MATCH (d:Domain) "
        "RETURN d.domain_id AS domain_id, d.name AS name "
        "ORDER BY d.domain_id"
    )
    domains = [{"domain_id": r["domain_id"], "name": r["name"]} for r in domain_rows]

    product_rows = graph.read(
        "MATCH (p:IntelligenceProduct) "
        "RETURN p.product_id AS product_id, p.display_name AS display_name "
        "ORDER BY p.product_id"
    )
    products = [
        {"product_id": r["product_id"], "display_name": r["display_name"]}
        for r in product_rows
    ]

    # prompts._spine_summary reads spine['platforms']; return it so the two sides
    # agree. Returns [] today (no Platform nodes exist yet) but stays correct as
    # platforms are added later.
    platform_rows = graph.read(
        "MATCH (p:Platform) "
        "RETURN p.platform_id AS platform_id, p.platform_name AS platform_name "
        "ORDER BY p.platform_id"
    )
    platforms = [
        {"platform_id": r["platform_id"], "platform_name": r["platform_name"]}
        for r in platform_rows
    ]

    return {
        "business": business,
        "domains": domains,
        "products": products,
        "platforms": platforms,
    }


async def _propose_one(
    dashboard_id: str,
    *,
    run_id: str,
    semaphore: asyncio.Semaphore,
    db: Any,
    errors: list[dict[str, Any]],
    progress: dict[str, int],
    emit: EmitHook | None = None,
) -> tuple[int, float]:
    """Propose + persist one dashboard under the concurrency semaphore.

    Calls the proposer, writes the resulting proposals to the run's queue, and
    appends a checkpoint event. Any exception is captured into ``errors`` (so a
    single dashboard failure never aborts the run) and counts as zero proposals.

    When an ``emit`` hook is supplied it also publishes live-canvas events:
    ``agent_action`` (start), ``proposal_new`` per persisted proposal, and a
    monotonic ``ingest_progress`` once the dashboard finishes (success or error).

    Args:
        dashboard_id: The dashboard to harvest.
        run_id: The run this dashboard belongs to.
        semaphore: The shared concurrency bound.
        db: The shared read-only :class:`~harness.kg.driver.GraphDB`.
        errors: Mutable error sink; per-dashboard failures are appended here.
        progress: Mutable ``{"done", "total"}`` counter shared across the run so
            ``ingest_progress`` events report a stable, monotonic completion count
            even under concurrency.
        emit: Optional live-canvas emit hook (``None`` = silent, CLI-compatible).

    Returns:
        ``(proposals_written, cost_usd)`` for this dashboard (``(0, 0.0)`` on
        error).
    """
    async with semaphore:
        if emit is not None:
            emit(
                "agent_action",
                run_id=run_id,
                dashboard=dashboard_id,
                message=f"Proposing for {dashboard_id}",
            )
        try:
            proposals, cost_usd = await propose_for_dashboard_with_cost(
                dashboard_id, db=db
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-dashboard failures
            errors.append({"dashboard_id": dashboard_id, "error": str(exc)})
            append_event(
                {
                    "type": "ingest_dashboard_error",
                    "run_id": run_id,
                    "dashboard_id": dashboard_id,
                    "error": str(exc),
                }
            )
            if emit is not None:
                emit(
                    "error",
                    run_id=run_id,
                    message=f"{dashboard_id}: {exc}",
                )
                progress["done"] += 1
                emit(
                    "ingest_progress",
                    run_id=run_id,
                    dashboard=dashboard_id,
                    done=progress["done"],
                    total=progress["total"],
                )
            return 0, 0.0

        written = write_proposals(run_id, dashboard_id, proposals)
        append_event(
            {
                "type": "ingest_dashboard_checkpoint",
                "run_id": run_id,
                "dashboard_id": dashboard_id,
                "proposals": written,
                "cost_usd": cost_usd,
            }
        )
        if emit is not None:
            # Emit the persisted records (carrying proposal_id / review_state) so
            # the canvas can review them, not the pre-write drafts.
            for proposal in load_dashboard_proposals(run_id, dashboard_id):
                emit("proposal_new", run_id=run_id, proposal=proposal)
            progress["done"] += 1
            emit(
                "ingest_progress",
                run_id=run_id,
                dashboard=dashboard_id,
                done=progress["done"],
                total=progress["total"],
            )
        return written, cost_usd


async def ingest_dashboards(
    dashboard_ids: list[str],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    run_id: str | None = None,
    db: Any = None,
    emit: EmitHook | None = None,
) -> dict:
    """Propose for many dashboards concurrently, persisting one run.

    Drives :func:`harness.ingest.proposer.propose_for_dashboard` across all
    ``dashboard_ids`` with a bounded :class:`asyncio.Semaphore`, writing each
    dashboard's proposals into the run's queue and logging a checkpoint per
    dashboard. The shared :class:`~harness.kg.driver.GraphDB` is read-only here
    (the proposer only reads the spine context).

    Args:
        dashboard_ids: The dashboards to harvest, in order.
        concurrency: Max number of proposer agents running at once.
        run_id: The run id to write under; a fresh timestamped id is minted when
            ``None``.
        db: Optional shared :class:`~harness.kg.driver.GraphDB`; the singleton is
            used when ``None``.
        emit: Optional live-canvas emit hook ``emit(type, run_id=..., **data)``.
            When ``None`` (the default, used by the CLI) no canvas events fire and
            behaviour is unchanged. When supplied it publishes ``run_started``,
            per-dashboard ``agent_action`` / ``ingest_progress``, and
            ``proposal_new`` per persisted proposal.

    Returns:
        ``{"run_id": str, "dashboards": int, "proposals": int,
        "total_cost_usd": float, "errors": [{"dashboard_id", "error"}...]}``.
    """
    resolved_run = run_id or new_run_id()
    graph = db or get_db()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    errors: list[dict[str, Any]] = []
    progress: dict[str, int] = {"done": 0, "total": len(dashboard_ids)}

    append_event(
        {
            "type": "ingest_run_start",
            "run_id": resolved_run,
            "dashboards": len(dashboard_ids),
            "concurrency": concurrency,
        }
    )
    if emit is not None:
        emit("run_started", run_id=resolved_run, dashboards=len(dashboard_ids))

    results = await asyncio.gather(
        *(
            _propose_one(
                dashboard_id,
                run_id=resolved_run,
                semaphore=semaphore,
                db=graph,
                errors=errors,
                progress=progress,
                emit=emit,
            )
            for dashboard_id in dashboard_ids
        )
    )
    total_proposals = sum(written for written, _ in results)
    total_cost_usd = sum(cost for _, cost in results)

    summary = {
        "run_id": resolved_run,
        "dashboards": len(dashboard_ids),
        "proposals": total_proposals,
        "total_cost_usd": total_cost_usd,
        "errors": errors,
    }
    append_event({"type": "ingest_run_complete", **summary})
    return summary
