"""NO-DB tests for the PreToolUse confirm-before-write guard hook.

The hook is invoked as a subprocess (its real runtime contract): event JSON on
stdin, a decision JSON on stdout, exit 0. We assert:

* a normal create payload -> ``ask`` (with a confirm table),
* a master-config / excluded payload -> ``deny`` (with a reason),
* the hook is **self-guarding**: read/unknown tools -> no output, exit 0,
* ``draw_edge`` is gated too: clean -> ``ask``, excluded props -> ``deny``,
* malformed stdin -> exit 0 with no crash (defers to the default flow).
"""

from __future__ import annotations

import json
import subprocess
import sys

from harness.kg.config import REPO_ROOT

HOOK_PATH = REPO_ROOT / "harness" / "hooks" / "pretool_guard.py"


def _run_hook(stdin_text: str) -> subprocess.CompletedProcess[str]:
    """Run the hook as a subprocess with ``stdin_text`` and capture output."""
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _decision(proc: subprocess.CompletedProcess[str]) -> dict:
    """Parse the hook's stdout decision JSON, returning the inner block."""
    payload = json.loads(proc.stdout)
    return payload["hookSpecificOutput"]


def test_normal_create_payload_asks() -> None:
    """A clean create payload yields a ``permissionDecision: ask`` with a table."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__create_business_node",
        "tool_input": {
            "business_id": "rare-seeds",
            "display_name": "Rare Seeds",
            "tier": "smb",
            "status": "active",
        },
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    out = _decision(proc)
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask"
    reason = out["permissionDecisionReason"]
    assert "Create Business" in reason
    # The confirm table renders the input fields.
    assert "business_id" in reason
    assert "rare-seeds" in reason


def test_master_config_payload_denies() -> None:
    """A master-config / control-plane endpoint value yields a ``deny``."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__create_metric_node",
        "tool_input": {
            "metric_uid": "m1",
            "canonical_id": "c1",
            "metric_id": "mid1",
            "display_name": "Sneaky Metric",
            "card_endpoint": "/api/master-config/metrics/card",
        },
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    out = _decision(proc)
    assert out["permissionDecision"] == "deny"
    reason = out["permissionDecisionReason"]
    assert "blocked" in reason.lower()
    assert "card_endpoint" in reason


def test_excluded_auth_endpoint_denies() -> None:
    """An ``/auth/`` provenance value is also an excluded source -> ``deny``."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__create_dashboard_node",
        "tool_input": {
            "dashboard_id": "d1",
            "display_name": "Auth Dashboard",
            "route_path": "/auth/login",
        },
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    assert _decision(proc)["permissionDecision"] == "deny"


def test_read_tool_defers_no_output() -> None:
    """A read tool (``lookup_node``) is not a write -> no stdout, exit 0."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__lookup_node",
        "tool_input": {"label": "Domain", "key": "marketing"},
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


def test_unknown_tool_defers_no_output() -> None:
    """An unrelated tool name yields no decision: no stdout, exit 0."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__kg_status",
        "tool_input": {},
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


def test_draw_edge_clean_asks() -> None:
    """A clean ``draw_edge`` is gated with ``ask`` under the draw-edge header."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__draw_edge",
        "tool_input": {
            "rel_type": "SOURCES",
            "from_label": "Platform",
            "from_key": "ga4",
            "to_label": "Metric",
            "to_key": "m:x",
            "props_json": "{}",
        },
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    out = _decision(proc)
    assert out["permissionDecision"] == "ask"
    assert "Draw edge" in out["permissionDecisionReason"]


def test_draw_edge_excluded_props_denies() -> None:
    """An excluded provenance value hidden inside ``props_json`` -> ``deny``."""
    event = {
        "hookEventName": "PreToolUse",
        "tool_name": "mcp__graph__draw_edge",
        "tool_input": {
            "rel_type": "SOURCES",
            "from_label": "Platform",
            "from_key": "ga4",
            "to_label": "Metric",
            "to_key": "m:x",
            "props_json": json.dumps({"source_ref": "/master-config/x"}),
        },
    }
    proc = _run_hook(json.dumps(event))
    assert proc.returncode == 0, proc.stderr
    out = _decision(proc)
    assert out["permissionDecision"] == "deny"
    assert "source_ref" in out["permissionDecisionReason"]


def test_malformed_stdin_exits_zero_no_output() -> None:
    """Malformed (non-JSON) stdin must not crash: exit 0, no decision output."""
    proc = _run_hook("this is not json {{{")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_empty_stdin_exits_zero_no_output() -> None:
    """Empty stdin must not crash: exit 0, no decision output."""
    proc = _run_hook("")
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
