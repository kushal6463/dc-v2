"""Append-only JSONL event log for the ThoughtWire Causal Knowledge Graph.

Every graph mutation made by the arbitration writer (and other harness
components) is appended as one JSON object per line to ``data/events/events.jsonl``.
This is the durable audit / replay log referenced throughout the implementation
plan (sections 5c, 8).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from harness.kg.config import REPO_ROOT

#: Default event-log path: ``<repo>/data/events/events.jsonl``.
EVENTS_PATH: Path = REPO_ROOT / "data" / "events" / "events.jsonl"


def append_event(event: dict, path: Path | None = None) -> None:
    """Append a single event as one JSON line to the event log.

    A ``ts`` ISO-8601 UTC timestamp is added if the event does not already carry
    one. The parent directory is created on demand, and the line is flushed so
    the record is durable even on a long-running process.

    The log is intentionally schema-free (any JSON-serializable mapping is
    accepted), so it carries the M1/M2 mutation + ``agent_call`` events as well
    as the agentic-builder's PHASE-LEVEL events (:mod:`harness.agentic`). The
    phase events use a small, well-known shape so a reader can tally a build:

    * ``type`` — e.g. ``"build_start"`` | ``"build_phase"`` | ``"build_slices"``
      | ``"build_phase_error"`` | ``"build_done"``.
    * ``phase`` — the build phase as an ``int`` 0–4 (0 = backup/wipe/seed,
      1 = nodes, 2 = structural edges, 3 = weave causal, 4 = critique).
    * ``run_id`` — the build run id (the report is written to
      ``data/build-report.<run_id>.json``).
    * ``agent_id`` / ``slice`` — the per-slice agent label (``str``).
    * ``metric_count`` / ``edge_count`` — counts written by a slice (``int``).
    * ``duration_s`` — wall-clock seconds for the unit of work (``float``); the
      per-call SDK telemetry also carries ``duration_ms`` / ``cost_usd`` /
      ``num_turns`` / ``tool_calls``.

    Args:
        event: The event payload (any JSON-serializable mapping). A copy is
            written so the caller's dict is never mutated.
        path: Optional override for the log file; defaults to
            :data:`EVENTS_PATH`.
    """
    target = path or EVENTS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    record = dict(event)
    record.setdefault("ts", datetime.now(UTC).isoformat())

    line = json.dumps(record, default=str)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
