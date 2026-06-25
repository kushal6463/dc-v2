"""Run an ingestion in a CLEAN SUBPROCESS and stream events as JSONL on stdout.

Why this exists: the claude-agent-sdk spawns the ``claude`` CLI with
``--input-format stream-json`` and feeds the request over the child's stdin.
That stdin handshake only completes reliably from a clean **main-thread**
``asyncio.run`` loop. Driven from the API server's already-running event loop
(or a worker thread), the spawned ``claude`` blocks forever waiting for input.

So the API server launches THIS module as its own OS process (exactly how the
``kg`` CLI runs ingestion, which works), and reads the events it prints. Each
event line is prefixed with :data:`EVENT_PREFIX` so the parent can separate
real events from any incidental stdout (e.g. driver notifications).

Usage (invoked by harness.api.server):
    python -m harness.ingest.run_subprocess --run-id R --dashboards a,b|ALL \
        [--concurrency 6] [--auto-approve]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from harness.agent.engine import run_sync
from harness.ingest import apply as apply_mod
from harness.ingest.orchestrator import ingest_dashboards
from harness.ingest.prepass import all_dashboard_ids
from harness.kg.driver import get_db
from harness.store.proposals import load_dashboard_proposals, set_review_state

#: Line prefix marking a machine-readable event on stdout.
EVENT_PREFIX = "KGEVENT:"

#: When set (via --emit-url), each event is also POSTed here so a running canvas
#: server republishes it on its SSE channel (the reliable live-ingest path).
_EMIT_URL: str | None = None


def _detach_from_parent_claude_code() -> None:
    """Strip Claude-Code *nesting-context* env vars so the agent SDK's bundled
    ``claude`` runs STANDALONE (Keychain auth) instead of trying to attach to a
    parent Claude Code session.

    When this process is a descendant of a running Claude Code session,
    ``CLAUDECODE`` / ``CLAUDE_CODE_*`` are inherited; the bundled ``claude`` then
    tries to attach to that parent and hangs forever for a detached/server-spawned
    descendant. Removing them (but KEEPING any real auth token like
    ``CLAUDE_CODE_OAUTH_TOKEN``) makes the SDK authenticate normally.
    """
    keep = ("TOKEN", "OAUTH", "API", "KEY")
    for name in list(os.environ):
        if (
            name in ("CLAUDECODE", "CLAUDE_EFFORT")
            or (name.startswith("CLAUDE_CODE_") and not any(s in name for s in keep))
        ):
            os.environ.pop(name, None)


def _post_event(url: str, event: dict[str, Any]) -> None:
    """Best-effort POST of one event to the canvas server (never raises)."""
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(event).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:  # noqa: BLE001 — telemetry only; ingestion must not depend on it
        pass


def _emit(type: str, run_id: str | None = None, **data: Any) -> None:
    """Emit hook (same signature as EventBus.emit): JSONL on stdout (+ optional POST)."""
    event = {
        "type": type,
        "run_id": run_id,
        "ts": datetime.now(UTC).isoformat(),
        "data": data,
    }
    sys.stdout.write(EVENT_PREFIX + json.dumps(event) + "\n")
    sys.stdout.flush()
    if _EMIT_URL:
        _post_event(_EMIT_URL, event)


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the ingestion, emit events on stdout. Returns an exit code."""
    ap = argparse.ArgumentParser(prog="kg-ingest-subprocess")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dashboards", required=True, help='comma-separated ids, or "ALL"')
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument(
        "--emit-url",
        default=None,
        help="POST each event here (e.g. a running canvas server's /api/_event)",
    )
    ap.add_argument(
        "--prune",
        action="store_true",
        help="after ingest, delete Domains/chart-types that no Metric uses",
    )
    a = ap.parse_args(argv)

    global _EMIT_URL
    _EMIT_URL = a.emit_url

    # Must run BEFORE the agent SDK spawns the bundled ``claude`` (see docstring).
    _detach_from_parent_claude_code()

    ids = all_dashboard_ids() if a.dashboards == "ALL" else a.dashboards.split(",")

    try:
        summary = run_sync(
            ingest_dashboards(
                ids,
                concurrency=a.concurrency,
                run_id=a.run_id,
                db=get_db(),
                emit=_emit,
            )
        )
        if a.auto_approve:
            for dashboard_id in ids:
                for proposal in load_dashboard_proposals(a.run_id, dashboard_id):
                    set_review_state(a.run_id, str(proposal["proposal_id"]), "approved")
            summary = {
                **summary,
                "apply": apply_mod.apply_approved(get_db(), a.run_id, emit=_emit),
            }
        if a.prune:
            from harness.kg.reconcile import prune_empty_spine

            pruned = prune_empty_spine(get_db())
            summary = {**summary, "pruned": pruned}
            _emit("pruned", run_id=a.run_id, **pruned)
        _emit("run_done", run_id=a.run_id, summary=summary)
        return 0
    except Exception as exc:  # noqa: BLE001 — surface to the parent, never hang
        _emit("error", run_id=a.run_id, message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
