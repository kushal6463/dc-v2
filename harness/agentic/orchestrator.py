"""Phased-parallel orchestrator for the agentic graph builder (spec section G).

Drives the build as a sequence of phases, each fanned across bounded-concurrent
agents (mirroring :mod:`harness.ingest.orchestrator`'s
:class:`asyncio.Semaphore` + per-slice error isolation + ``append_event``
checkpoints):

* **Phase 1 — nodes** (parallel ~8–12 agents): the 325 metrics are sliced into
  namespace/domain buckets (:func:`slice_metrics`); each agent creates its
  slice's nodes and attaches them to the spine. **BARRIER** (``asyncio.gather``)
  before any edge phase, so every node exists before edges are drawn.
* **Phase 2 — structural edges** (parallel): ``DECOMPOSES_INTO`` from formulas.
* **Phase 3 — weave causal** (parallel): ``INFLUENCES`` from reasoning.
* **Phase 4 — critique** (single agent): finds + reports loops / orphans /
  leaves and de-dupes causal-vs-structural; its findings are merged with
  Cypher-derived counts into the build report.

:func:`build` is the async entry point the runner calls; it returns the report
dict (the runner writes it to ``data/build-report.<runId>.json``). Slicing reads
ONLY the offline catalog JSON (no SDK, no Neo4j) so :func:`plan` and the CLI
``--dry-plan`` path are offline-safe; the SDK / driver are imported lazily inside
the live phase runners.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from harness.agentic import prompts
from harness.agentic.runner import run_agent
from harness.kg.config import REPO_ROOT
from harness.store.jsonl import append_event

#: The offline metric catalog (evidence; the agents re-read slices via the MCP
#: doc tools — this copy is only for deterministic, offline SLICING).
_METRIC_NODES_PATH = REPO_ROOT / "data" / "metric_nodes.rare_seeds.json"

#: Default bound on concurrent phase agents (mirrors the ingest pipeline).
DEFAULT_CONCURRENCY: int = int(os.environ.get("KG_BUILD_CONCURRENCY", "8"))

#: Namespaces large enough to split by domain into multiple buckets (so no agent
#: gets an oversized slice). ``ml`` (~149) and ``magento`` (~46) dominate.
_SPLIT_BY_DOMAIN: frozenset[str] = frozenset({"ml", "magento"})

#: Namespace excluded from the node set entirely (the 8 ``operational.*``
#: metrics are dropped per the locked OpenAPI-filter decision).
_DROP_NAMESPACE: str = "operational"

#: The smoke slice: the ``blended.*`` ROAS chain (one small namespace), per the
#: spec's smoke-build target.
_SMOKE_NAMESPACE: str = "blended"

#: Max metrics per node-phase bucket; any larger bucket is chunked into parts so
#: no single agent gets an oversized slice (e.g. ml/ml_modeling ~105 -> 3 parts).
_MAX_BUCKET: int = 35

#: The linkable spine-id allowlist surfaced to the node-phase agents. Kept in
#: sync with ``harness/seed/spine_seed.json`` + ``harness/seed/platforms.json``.
SPINE_IDS: dict[str, list[str]] = {
    "domains": [
        "finance", "marketing", "operations", "service", "customer",
        "product", "supply_chain", "hr", "data_it",
    ],
    "products": ["miq", "ciq", "piq", "storefront_iq", "dc", "creative_iq"],
    "platforms": [
        "ga4", "google_ads", "google_search", "google_youtube", "google_pmax",
        "google_shopping", "meta_ads", "meta_prospecting", "meta_retargeting",
        "meta_creative", "klaviyo", "magento",
    ],
}


# ---------------------------------------------------------------------------
# Slicing — deterministic, offline (reads only the catalog JSON).
# ---------------------------------------------------------------------------


def _load_catalog() -> dict[str, Any]:
    """Read + parse the offline metric-node catalog (``metrics`` map)."""
    return json.loads(_METRIC_NODES_PATH.read_text(encoding="utf-8"))


def slice_metrics(
    *, smoke: bool = False, namespaces: list[str] | None = None
) -> list[dict[str, Any]]:
    """Slice the metric set into ~8–12 namespace/domain buckets (offline).

    Each bucket is a node-phase agent's worklist. Buckets are built from the
    catalog ``source`` namespace; the big namespaces in :data:`_SPLIT_BY_DOMAIN`
    are further split by ``domain`` so no agent gets an oversized slice. The 8
    ``operational.*`` metrics are dropped. Reads only the offline catalog JSON —
    no SDK, no Neo4j — so it is safe from the ``--dry-plan`` path.

    Args:
        smoke: When ``True``, return a single small bucket — the ``blended.*``
            chain — for a fast end-to-end smoke build.
        namespaces: Restrict to these ``source`` namespaces; ``None`` = all
            (minus the dropped ``operational`` namespace).

    Returns:
        A list of bucket dicts ``{"label", "namespace", "domain"?, "metric_ids",
        "count"}`` in deterministic order.
    """
    catalog = _load_catalog()
    metrics: dict[str, Any] = catalog.get("metrics") or {}

    # Group metric ids by (namespace, domain), dropping operational.
    grouped: dict[tuple[str, str], list[str]] = {}
    for metric_id, entry in metrics.items():
        source = entry.get("source") or ""
        if source == _DROP_NAMESPACE or metric_id.split(".")[0] == _DROP_NAMESPACE:
            continue
        if smoke and source != _SMOKE_NAMESPACE:
            continue
        if namespaces and source != "" and source not in namespaces:
            continue
        domain = entry.get("domain") or ""
        grouped.setdefault((source, domain), []).append(metric_id)

    # Assemble buckets: split-by-domain namespaces become one bucket per domain;
    # everything else collapses to one bucket per namespace.
    buckets_by_ns: dict[str, dict[str, Any]] = {}
    split_buckets: list[dict[str, Any]] = []
    for (namespace, domain), ids in sorted(grouped.items()):
        if namespace in _SPLIT_BY_DOMAIN:
            split_buckets.append(
                {
                    "label": f"namespace={namespace},domain={domain or 'none'}",
                    "namespace": namespace,
                    "domain": domain or None,
                    "metric_ids": sorted(ids),
                    "count": len(ids),
                }
            )
        else:
            bucket = buckets_by_ns.setdefault(
                namespace,
                {
                    "label": f"namespace={namespace}",
                    "namespace": namespace,
                    "domain": None,
                    "metric_ids": [],
                    "count": 0,
                },
            )
            bucket["metric_ids"].extend(ids)

    for bucket in buckets_by_ns.values():
        bucket["metric_ids"] = sorted(bucket["metric_ids"])
        bucket["count"] = len(bucket["metric_ids"])

    ordered = sorted(
        [*buckets_by_ns.values(), *split_buckets],
        key=lambda b: (b["namespace"], b.get("domain") or ""),
    )
    ordered = [b for b in ordered if b["count"] > 0]

    # Chunk any oversized bucket into <= _MAX_BUCKET parts so no single node-phase
    # agent gets an unmanageable slice (e.g. ml/ml_modeling ~105 -> 3 parts). Small
    # buckets (incl. the smoke blended bucket) pass through unchanged.
    chunked: list[dict[str, Any]] = []
    for b in ordered:
        ids = b["metric_ids"]
        if len(ids) <= _MAX_BUCKET:
            chunked.append(b)
            continue
        n_parts = (len(ids) + _MAX_BUCKET - 1) // _MAX_BUCKET
        for i in range(n_parts):
            part = ids[i * _MAX_BUCKET : (i + 1) * _MAX_BUCKET]
            chunked.append(
                {
                    "label": f"{b['label']} (part {i + 1}/{n_parts})",
                    "namespace": b["namespace"],
                    "domain": b["domain"],
                    "metric_ids": part,
                    "count": len(part),
                }
            )
    return chunked


# ---------------------------------------------------------------------------
# Plan (offline) — used by ``--dry-plan``.
# ---------------------------------------------------------------------------


def plan(
    *, smoke: bool = False, namespaces: list[str] | None = None
) -> dict[str, Any]:
    """Build the offline phase plan (slices + per-phase prompts) WITHOUT running.

    Returns everything the ``--dry-plan`` path prints: the resolved phase list,
    the node-phase slices, a representative user prompt per phase, and the phase
    system prompts. No SDK, no Neo4j — pure functions over the catalog + the
    prompt builders.

    Args:
        smoke: Plan a smoke build (single ``blended.*`` bucket, wipe skipped).
        namespaces: Restrict the node phase to these namespaces.

    Returns:
        ``{"smoke", "namespaces", "slices", "phases": [{phase, label,
        system_prompt, sample_user_prompt}], "spine_ids"}``.
    """
    slices = slice_metrics(smoke=smoke, namespaces=namespaces)
    sample = slices[0] if slices else {
        "label": "namespace=blended", "namespace": "blended",
        "domain": None, "metric_ids": [], "count": 0,
    }

    phase_entries: list[dict[str, Any]] = []
    # Phase 0 is deterministic (no agent prompt).
    phase_entries.append(
        {
            "phase": 0,
            "label": prompts.PHASE_LABEL[0],
            "description": "backup export -> wipe (skipped on --smoke) -> spine seed",
            "system_prompt": None,
            "sample_user_prompt": None,
        }
    )
    for phase in (1, 2, 3, 4):
        if phase == 1:
            user = prompts.build_user_prompt_for_phase(
                1, slice_label=sample["label"], namespace=sample["namespace"],
                domain=sample.get("domain"), spine_ids=SPINE_IDS,
            )
        elif phase in (2, 3):
            user = prompts.build_user_prompt_for_phase(
                phase, slice_label=sample["label"], namespace=sample["namespace"],
                domain=sample.get("domain"),
            )
        else:  # critique is graph-wide (single agent, no slice)
            user = prompts.build_user_prompt_for_phase(4, slice_label="(whole graph)")
        # Phases 1-3 fan one agent per node-slice bucket; phase 4 is a single
        # graph-wide critique agent.
        parallel_agents = 1 if phase == 4 else len(slices)
        phase_entries.append(
            {
                "phase": phase,
                "label": prompts.PHASE_LABEL[phase],
                "parallel_agents": parallel_agents,
                "barrier_after": phase in (1, 2, 3),
                "system_prompt": prompts.PHASE_SYSTEM[phase],
                "sample_user_prompt": user,
            }
        )

    return {
        "smoke": smoke,
        "namespaces": namespaces,
        "slices": slices,
        "phases": phase_entries,
        "spine_ids": SPINE_IDS,
    }


# ---------------------------------------------------------------------------
# Live phase runners (parallel, semaphore-bounded; lazy SDK import via run_agent).
# ---------------------------------------------------------------------------


async def _run_slice(
    *,
    phase: int,
    bucket: dict[str, Any],
    semaphore: asyncio.Semaphore,
    run_id: str,
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
) -> float:
    """Run one phase agent for one bucket under the concurrency semaphore.

    Builds the phase's system + user prompt for this bucket, drives the agent via
    :func:`harness.agentic.runner.run_agent`, and records a per-slice checkpoint
    event. A per-slice failure is captured into ``errors`` (so one slice never
    aborts the phase) and contributes zero cost.

    Returns:
        The slice's ``cost_usd`` (``0.0`` on error).
    """
    async with semaphore:
        label = f"{prompts.PHASE_LABEL[phase]}:{bucket['label']}"
        if phase == 1:
            user = prompts.build_user_prompt_for_phase(
                1, slice_label=bucket["label"], namespace=bucket["namespace"],
                domain=bucket.get("domain"), metric_ids=bucket["metric_ids"],
                spine_ids=SPINE_IDS,
            )
        else:  # phase 2 (structural) or 3 (weave): same slice selectors
            focus = bucket.get("focus")
            user = prompts.build_user_prompt_for_phase(
                phase, slice_label=bucket["label"], namespace=bucket["namespace"],
                domain=bucket.get("domain"), metric_ids=bucket["metric_ids"],
                focus=focus,
            )
        append_event(
            {"type": "build_phase", "phase": phase, "run_id": run_id,
             "step": "slice_start", "slice": bucket["label"],
             "metrics": bucket["count"]}
        )
        try:
            outcome = await run_agent(
                prompts.PHASE_SYSTEM[phase], user, label=label,
            )
        except Exception as exc:  # noqa: BLE001 — isolate per-slice failures
            errors.append({"phase": phase, "slice": bucket["label"], "error": str(exc)})
            append_event(
                {"type": "build_phase_error", "phase": phase, "run_id": run_id,
                 "slice": bucket["label"], "error": str(exc)}
            )
            return 0.0

        results.append(outcome)
        cost = float(outcome.get("cost_usd") or 0.0)
        append_event(
            {"type": "build_phase", "phase": phase, "run_id": run_id,
             "step": "slice_done", "slice": bucket["label"],
             "cost_usd": cost, "tool_calls": outcome.get("tool_calls")}
        )
        return cost


async def _run_phase(
    *,
    phase: int,
    buckets: list[dict[str, Any]],
    concurrency: int,
    run_id: str,
) -> dict[str, Any]:
    """Run all buckets of one phase concurrently (BARRIER at ``asyncio.gather``).

    Returns:
        ``{"phase", "agents", "cost_usd", "errors", "results"}``.
    """
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    append_event(
        {"type": "build_phase", "phase": phase, "run_id": run_id, "step": "start",
         "agents": len(buckets), "concurrency": concurrency}
    )
    costs = await asyncio.gather(
        *(
            _run_slice(
                phase=phase, bucket=bucket, semaphore=semaphore,
                run_id=run_id, results=results, errors=errors,
            )
            for bucket in buckets
        )
    )
    total = float(sum(costs))
    append_event(
        {"type": "build_phase", "phase": phase, "run_id": run_id, "step": "barrier",
         "agents": len(buckets), "cost_usd": total, "errors": len(errors)}
    )
    return {"phase": phase, "agents": len(buckets), "cost_usd": total,
            "errors": errors, "results": results}


async def _run_critique(*, run_id: str) -> dict[str, Any]:
    """Run the single graph-wide critique agent (phase 4).

    Returns:
        ``{"phase": 4, "cost_usd", "findings", "errors"}`` — ``findings`` is the
        parsed JSON audit object the critique agent returns (loops / orphans /
        leaves / duplicates / repairs), or ``{}`` if it could not be parsed.
    """
    append_event({"type": "build_phase", "phase": 4, "run_id": run_id, "step": "start"})
    user = prompts.build_user_prompt_for_phase(4, slice_label="(whole graph)")
    findings: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    cost = 0.0
    try:
        outcome = await run_agent(prompts.PHASE_SYSTEM[4], user, label="critique")
        cost = float(outcome.get("cost_usd") or 0.0)
        # The tool-calling path returns the audit JSON in ``text``; the fallback
        # path has no free-form text (it returns ``applied``), so findings stay {}
        # and the orchestrator's Cypher counts carry the report.
        findings = _parse_critique(outcome.get("text") or "") or {}
    except Exception as exc:  # noqa: BLE001
        errors.append({"phase": 4, "error": str(exc)})
        append_event({"type": "build_phase_error", "phase": 4, "run_id": run_id,
                      "error": str(exc)})
    append_event({"type": "build_phase", "phase": 4, "run_id": run_id,
                  "step": "barrier", "cost_usd": cost})
    return {"phase": 4, "cost_usd": cost, "findings": findings, "errors": errors}


def _parse_critique(text: str) -> dict[str, Any] | None:
    """Best-effort parse of the critique agent's JSON audit object from its text."""
    import re

    text = (text or "").strip()
    if not text:
        return None
    # Try a fenced block first, then the whole string.
    fence = re.search(r"```(?:json)?\s*(?P<body>\{.*?\})\s*```", text, re.DOTALL)
    candidate = fence.group("body") if fence else text
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Cypher-derived report counts (live; lazy driver import).
# ---------------------------------------------------------------------------


def _graph_report() -> dict[str, Any]:
    """Read counts-by-label / counts-by-edge + leaf/orphan lists from Neo4j.

    Complements the critique agent's findings with deterministic Cypher counts so
    the build report is authoritative even if the agent under-reports. Lazy
    driver import keeps the offline path clean.

    Returns:
        ``{"node_counts", "edge_counts", "orphans", "leaves"}``.
    """
    from harness.kg.driver import get_db

    db = get_db()
    node_counts = {
        row["label"]: row["c"]
        for row in db.read(
            "MATCH (n) UNWIND labels(n) AS label "
            "RETURN label, count(*) AS c ORDER BY label"
        )
    }
    edge_counts = {
        row["rel"]: row["c"]
        for row in db.read(
            "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS c ORDER BY rel"
        )
    }
    # Orphans: Metric nodes with no spine attachment.
    orphans = [
        row["uid"]
        for row in db.read(
            "MATCH (m:Metric) WHERE NOT (m)-[:BELONGS_TO_DOMAIN|PART_OF_PRODUCT|SOURCES]->() "
            "RETURN m.metric_uid AS uid LIMIT 500"
        )
    ]
    # Leaves: Metric nodes with no metric<->metric edge (structural or causal).
    leaves = [
        row["uid"]
        for row in db.read(
            "MATCH (m:Metric) "
            "WHERE NOT (m)-[:DECOMPOSES_INTO|INFLUENCES]-(:Metric) "
            "RETURN m.metric_uid AS uid LIMIT 500"
        )
    ]
    return {"node_counts": node_counts, "edge_counts": edge_counts,
            "orphans": orphans, "leaves": leaves}


# ---------------------------------------------------------------------------
# build — the async entry point the runner drives for phases 1–4.
# ---------------------------------------------------------------------------


def _existing_metric_uids() -> set[str]:
    """Return the set of ``metric_uid``s already present in the graph (resume).

    Lazy driver import keeps the offline/``--dry-plan`` path clean. Used by a
    ``resume`` build to skip node buckets that are already fully materialized so
    a rate-limited run picks up where it left off instead of rebuilding from
    scratch.
    """
    from harness.kg.driver import get_db

    return {
        row["uid"]
        for row in get_db().read("MATCH (m:Metric) RETURN m.metric_uid AS uid")
    }


async def build(
    *,
    smoke: bool = False,
    namespaces: list[str] | None = None,
    dry_plan: bool = False,
    resume: bool = False,
    run_id: str = "adhoc",
    concurrency: int = DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Run phases 1–4 and return the merged build report.

    Phase 0 (backup / wipe / seed) is handled by the runner *before* this is
    called. This fans the node agents across :func:`slice_metrics` buckets and
    BARRIERS, then the structural agents (same buckets) and BARRIERS, then the
    weave agents, then the single critique agent — emitting progress via
    :func:`harness.store.jsonl.append_event` like the existing pipeline. Finally
    it reads Cypher counts and merges them with the critique findings into the
    report.

    ``dry_plan=True`` short-circuits to :func:`plan` (no SDK, no Neo4j) so the
    function is usable for an offline plan as well as a live build.

    Args:
        smoke: Smoke build — one ``blended.*`` bucket.
        namespaces: Restrict the node phase to these namespaces.
        dry_plan: Return the offline plan instead of building.
        run_id: The build run id (from the runner).
        concurrency: Max concurrent phase agents.

    Returns:
        The build-report dict (counts by label/edge, loops, leaf/orphan lists,
        per-phase cost, total cost).
    """
    if dry_plan:
        return plan(smoke=smoke, namespaces=namespaces)

    buckets = slice_metrics(smoke=smoke, namespaces=namespaces)
    append_event({"type": "build_slices", "run_id": run_id,
                  "buckets": [b["label"] for b in buckets],
                  "metrics": sum(b["count"] for b in buckets)})

    # Resume: skip node buckets already fully materialized (a rate-limited run
    # picks up where it left off). Edge phases always re-run on every bucket —
    # the arbitration writer MERGEs, so re-drawing an existing edge is a no-op,
    # and the edge phases are the ones that fail first under a budget cap.
    node_buckets = buckets
    skipped: list[str] = []
    if resume:
        present = _existing_metric_uids()
        node_buckets = []
        for b in buckets:
            if all(uid in present for uid in b["metric_ids"]):
                skipped.append(b["label"])
            else:
                node_buckets.append(b)
        append_event({"type": "build_resume", "run_id": run_id,
                      "skipped_node_buckets": skipped,
                      "node_buckets": [b["label"] for b in node_buckets]})

    # Phase 1 — nodes (parallel, BARRIER). On resume, only the not-yet-complete
    # buckets are (re)built.
    phase1 = await _run_phase(phase=1, buckets=node_buckets, concurrency=concurrency,
                              run_id=run_id)
    # Phase 2 — structural edges (parallel, BARRIER). Same buckets; all nodes now exist.
    phase2 = await _run_phase(phase=2, buckets=buckets, concurrency=concurrency,
                              run_id=run_id)
    # Phase 3 — weave causal (parallel). Same buckets (each agent reasons over its slice).
    phase3 = await _run_phase(phase=3, buckets=buckets, concurrency=concurrency,
                              run_id=run_id)
    # Phase 4 — critique (single graph-wide agent).
    phase4 = await _run_critique(run_id=run_id)

    # Merge Cypher counts with the critique findings into the final report.
    graph = _graph_report()
    findings = phase4.get("findings") or {}
    total_cost = sum(
        float(p.get("cost_usd") or 0.0) for p in (phase1, phase2, phase3, phase4)
    )

    report = {
        "smoke": smoke,
        "namespaces": namespaces,
        "buckets": [{"label": b["label"], "count": b["count"]} for b in buckets],
        "node_counts": graph["node_counts"],
        "edge_counts": graph["edge_counts"],
        "loops": findings.get("loops") or [],
        "orphans": findings.get("orphans") or graph["orphans"],
        "leaves": findings.get("leaves") or graph["leaves"],
        "causal_structural_duplicates": findings.get("causal_structural_duplicates") or [],
        "critique_notes": findings.get("notes"),
        "phases": {
            "1_nodes": {"agents": phase1["agents"], "cost_usd": phase1["cost_usd"],
                        "errors": phase1["errors"]},
            "2_structural": {"agents": phase2["agents"], "cost_usd": phase2["cost_usd"],
                             "errors": phase2["errors"]},
            "3_weave": {"agents": phase3["agents"], "cost_usd": phase3["cost_usd"],
                        "errors": phase3["errors"]},
            "4_critique": {"cost_usd": phase4["cost_usd"], "errors": phase4["errors"]},
        },
        "total_cost_usd": total_cost,
    }
    return report
