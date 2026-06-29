"""Subprocess entry point for governance extraction (clean-env agent run).

Reads a request JSON object on stdin (``{"text", "metric_uid"?, "metric_name"?}``),
runs :func:`harness.governance.extract.extract_governance` to completion in a
clean main-thread ``asyncio.run``, and prints exactly one result line prefixed
with ``KGEXTRACT:``. The API route (:mod:`harness.api.server`) spawns this with
``uv run python -m harness.governance.extract_subprocess`` and parses that line —
the same pattern ingestion uses, because the agent SDK's bundled ``claude`` hangs
when driven in-process under uvicorn.

On failure it still prints a ``KGEXTRACT:`` line carrying an ``error`` so the
caller always gets a parseable result.
"""

from __future__ import annotations

import json
import sys

#: Stdout marker the API route greps for (mirrors ingestion's ``KGEVENT:``).
RESULT_PREFIX = "KGEXTRACT:"


def main() -> None:
    """Read the request on stdin, extract, and print one ``KGEXTRACT:`` line."""
    raw = sys.stdin.read()
    try:
        req = json.loads(raw) if raw.strip() else {}
        text = req.get("text") or ""
        if not text.strip():
            raise ValueError("no text provided")
        # Imported lazily so a bad request fails fast without spinning the SDK.
        from harness.agent.engine import run_sync
        from harness.governance.extract import extract_governance

        result = run_sync(
            extract_governance(
                text=text,
                metric_uid=req.get("metric_uid"),
                metric_name=req.get("metric_name"),
            )
        )
    except Exception as exc:  # noqa: BLE001 — always return a parseable line
        result = {"policy": {}, "threshold": {}, "error": str(exc)}
    print(RESULT_PREFIX + json.dumps(result))


if __name__ == "__main__":
    main()
