"""File-based proposal queue for the metric / UIComponent ingestion engine.

This is the pre-canvas durable store for the **proposals only -> arbitration is
the only writer** pipeline (implementation plan sections 5b/5c, schema section
8). Each ingestion *run* writes one JSONL file per dashboard under
:data:`PROPOSALS_DIR`; each line is a single proposal dict in the section-8
shape. Approval / rejection mutates a proposal's ``review_state`` in place by
rewriting its run's files, and :mod:`harness.ingest.apply` later reads the
``approved`` proposals and replays them through the M1 arbitration writer.

A proposal dict follows schema section 8::

    {
      "proposal_id": ..., "operation": "upsert",
      "target_label": ..., "target_id": ..., "key_field": ...,
      "source_kind": ..., "source_confidence": ..., "review_state": "proposed",
      "payload": {...}, "relationship_payloads": [...]
    }

Run ids are timestamp-derived (``run-YYYYmmddTHHMMSSZ``), never random, so the
"latest run" is simply the lexicographically greatest id.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from harness.kg.config import REPO_ROOT

#: Root directory for the file-based proposal queue.
PROPOSALS_DIR: Path = REPO_ROOT / "data" / "proposals"

#: Timestamp format for run ids (UTC, second resolution, ``Z`` suffix).
_RUN_TS_FORMAT = "%Y%m%dT%H%M%SZ"
#: Matches a run-id directory/file prefix (``run-YYYYmmddTHHMMSSZ``).
_RUN_ID_RE = re.compile(r"^run-\d{8}T\d{6}Z$")


def new_run_id() -> str:
    """Return a fresh, timestamp-derived run id (``run-YYYYmmddTHHMMSSZ``).

    The id is built from the current UTC time (not randomness) so run ids sort
    chronologically and :func:`latest_run_id` can pick the newest by string
    comparison.

    Returns:
        A run id of the form ``run-20260614T231500Z``.
    """
    return "run-" + datetime.now(UTC).strftime(_RUN_TS_FORMAT)


def _run_dir(run_id: str) -> Path:
    """Return (without creating) the directory holding ``run_id``'s files."""
    return PROPOSALS_DIR / run_id


def _dashboard_file(run_id: str, dashboard_id: str) -> Path:
    """Return the JSONL path for one dashboard's proposals within a run.

    The dashboard id is sanitized to a filesystem-safe slug so dashboard slugs
    containing unusual characters cannot escape the run directory.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", dashboard_id) or "dashboard"
    return _run_dir(run_id) / f"{safe}.jsonl"


def write_proposals(run_id: str, dashboard_id: str, proposals: list[dict]) -> int:
    """Persist a dashboard's proposals as one JSONL file within ``run_id``.

    Each proposal is stamped (in place on a copy) with a unique ``proposal_id``
    (if absent) and ``review_state="proposed"`` (always reset on write — a fresh
    write is always a fresh proposal), then written one-per-line. An empty
    ``proposals`` list still creates the (empty) run directory and file so the
    run is discoverable.

    Args:
        run_id: The run this dashboard belongs to (from :func:`new_run_id`).
        dashboard_id: The dashboard whose proposals these are.
        proposals: The list of section-8 proposal dicts to persist.

    Returns:
        The number of proposals written.
    """
    target = _dashboard_file(run_id, dashboard_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with target.open("w", encoding="utf-8") as fh:
        for raw in proposals:
            record = dict(raw)
            record.setdefault("proposal_id", f"kgp_{uuid.uuid4().hex[:12]}")
            record.setdefault("dashboard_id", dashboard_id)
            record["review_state"] = "proposed"
            fh.write(json.dumps(record, default=str) + "\n")
            written += 1
        fh.flush()
    return written


def load_dashboard_proposals(run_id: str, dashboard_id: str) -> list[dict]:
    """Load just one dashboard's persisted proposals within a run.

    Reads the single per-dashboard JSONL file (the same path
    :func:`write_proposals` wrote to), returning the stamped proposal dicts —
    each already carrying its ``proposal_id`` / ``review_state`` / ``run_id``.
    Used by the live-canvas emit hook so ``proposal_new`` events carry the
    reviewable ids. Returns ``[]`` if the file does not exist.

    Args:
        run_id: The run holding the dashboard's proposals.
        dashboard_id: The dashboard whose proposals to read.

    Returns:
        The persisted proposal dicts for that dashboard (with ``run_id`` set).
    """
    path = _dashboard_file(run_id, dashboard_id)
    if not path.is_file():
        return []
    proposals = _read_file(path)
    for proposal in proposals:
        proposal.setdefault("run_id", run_id)
    return proposals


def _iter_run_files(run_id: str):
    """Yield the JSONL proposal files for ``run_id`` in stable name order."""
    run_dir = _run_dir(run_id)
    if not run_dir.is_dir():
        return
    yield from sorted(run_dir.glob("*.jsonl"))


def _read_file(path: Path) -> list[dict]:
    """Parse one JSONL proposal file into a list of dicts (skips blank lines)."""
    proposals: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            proposals.append(json.loads(line))
    return proposals


def load_proposals(run_id: str | None = None, state: str | None = None) -> list[dict]:
    """Load proposals from a run, optionally filtered by ``review_state``.

    Args:
        run_id: The run to read; when ``None``, the latest run (by
            :func:`latest_run_id`) is used.
        state: When given, only proposals whose ``review_state`` equals this are
            returned.

    Returns:
        The matching proposal dicts (empty if the run does not exist or has no
        matches). Each carries a ``run_id`` key for convenience.
    """
    resolved = run_id or latest_run_id()
    if resolved is None:
        return []

    proposals: list[dict] = []
    for path in _iter_run_files(resolved):
        for proposal in _read_file(path):
            if state is not None and proposal.get("review_state") != state:
                continue
            proposal.setdefault("run_id", resolved)
            proposals.append(proposal)
    return proposals


def set_review_state(
    run_id: str,
    proposal_id: str,
    state: str,
    reason: str | None = None,
    payload: dict | None = None,
) -> bool:
    """Mutate one proposal's ``review_state`` in place, rewriting its file.

    Scans the run's files for the proposal, updates its ``review_state`` (and
    records ``review_reason`` / ``reviewed_at`` when a ``reason`` is given), then
    rewrites only the file that contained it. When a ``payload`` is supplied the
    proposal's ``payload`` is replaced with it before the rewrite, so a human's
    edit survives a reload AND is the payload the arbitration writer later
    applies (a human-edited proposal is approved with the edited payload — see
    the ``edit`` action in :mod:`harness.api.server`).

    Args:
        run_id: The run holding the proposal.
        proposal_id: The proposal to update.
        state: The new ``review_state`` (e.g. ``"approved"``, ``"rejected"``).
        reason: Optional human-readable reason (stored as ``review_reason``).
        payload: Optional replacement ``payload`` (used by the ``edit`` action).
            When ``None`` the existing payload is left untouched.

    Returns:
        ``True`` if the proposal was found and updated, ``False`` otherwise.
    """
    for path in _iter_run_files(run_id):
        proposals = _read_file(path)
        changed = False
        for proposal in proposals:
            if proposal.get("proposal_id") == proposal_id:
                proposal["review_state"] = state
                proposal["reviewed_at"] = datetime.now(UTC).isoformat()
                if reason is not None:
                    proposal["review_reason"] = reason
                if payload is not None:
                    proposal["payload"] = payload
                changed = True
        if changed:
            with path.open("w", encoding="utf-8") as fh:
                for proposal in proposals:
                    fh.write(json.dumps(proposal, default=str) + "\n")
                fh.flush()
            return True
    return False


def set_review_state_anywhere(
    proposal_id: str,
    state: str,
    reason: str | None = None,
    payload: dict | None = None,
) -> bool:
    """Like :func:`set_review_state` but search EVERY run for the proposal.

    The canvas may show a proposal whose run-id it no longer tracks (e.g. an
    auto-approve batch wrote it under a per-dashboard run). Scanning all runs
    lets a manual approve/reject/edit still find and update it, instead of 404.
    """
    if not PROPOSALS_DIR.is_dir():
        return False
    for child in sorted(PROPOSALS_DIR.iterdir(), reverse=True):
        if child.is_dir() and set_review_state(
            child.name, proposal_id, state, reason=reason, payload=payload
        ):
            return True
    return False


#: Review states the canvas treats as "pending" (awaiting a decision).
_PENDING_STATES = ("proposed", "pending")


def approve_all_pending(run_id: str) -> int:
    """Flip every still-pending proposal in ``run_id`` to ``approved``.

    Efficient bulk variant of :func:`set_review_state` for the canvas
    "Approve all" action: each run file is read and rewritten exactly once
    (instead of once per proposal), flipping any proposal whose ``review_state``
    is ``proposed``/``pending`` to ``approved`` and stamping ``reviewed_at``.
    Already-approved / rejected proposals are left untouched.

    Args:
        run_id: The run whose pending proposals should all be approved.

    Returns:
        The number of proposals flipped to ``approved``.
    """
    approved = 0
    for path in _iter_run_files(run_id):
        proposals = _read_file(path)
        changed = False
        for proposal in proposals:
            if proposal.get("review_state") in _PENDING_STATES:
                proposal["review_state"] = "approved"
                proposal["reviewed_at"] = datetime.now(UTC).isoformat()
                changed = True
                approved += 1
        if changed:
            with path.open("w", encoding="utf-8") as fh:
                for proposal in proposals:
                    fh.write(json.dumps(proposal, default=str) + "\n")
                fh.flush()
    return approved


def mark_review_state(run_id: str, proposal_ids: set[str], state: str) -> int:
    """Bulk-set ``review_state`` for a set of proposals in ``run_id`` (one pass/file).

    Used by the apply stage to flip written proposals to ``"applied"`` so the
    canvas stops counting them as still-to-apply (the "Apply approved (N)" / review
    panel read ``review_state``). Stamps ``reviewed_at``. Returns the count changed.
    """
    if not proposal_ids:
        return 0
    changed = 0
    for path in _iter_run_files(run_id):
        proposals = _read_file(path)
        dirty = False
        for proposal in proposals:
            if proposal.get("proposal_id") in proposal_ids:
                proposal["review_state"] = state
                proposal["reviewed_at"] = datetime.now(UTC).isoformat()
                dirty = True
                changed += 1
        if dirty:
            with path.open("w", encoding="utf-8") as fh:
                for proposal in proposals:
                    fh.write(json.dumps(proposal, default=str) + "\n")
                fh.flush()
    return changed


def latest_run_id() -> str | None:
    """Return the most recent run id, or ``None`` if no runs exist.

    Run directories are named ``run-YYYYmmddTHHMMSSZ``; the chronologically
    latest is the lexicographically greatest valid name.

    Returns:
        The newest run id, or ``None`` when :data:`PROPOSALS_DIR` has no runs.
    """
    if not PROPOSALS_DIR.is_dir():
        return None
    run_ids = [
        child.name
        for child in PROPOSALS_DIR.iterdir()
        if child.is_dir() and _RUN_ID_RE.match(child.name)
    ]
    return max(run_ids) if run_ids else None
