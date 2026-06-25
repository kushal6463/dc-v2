#!/usr/bin/env python3
"""PreToolUse confirm-before-write guard for the ``graph`` MCP server.

This is a Claude Code ``PreToolUse`` hook (stdlib-only: ``json``, ``sys``,
``re`` — no third-party imports, no DB access). It is **self-guarding**: it
emits a decision *only* for the known graph **write** tools and defers (prints
nothing, exits 0) on everything else, so it is safe under the broad
``mcp__graph__.*`` matcher.

A tool is a graph write when its name matches:

* ``mcp__graph__create_*`` — a node create, or
* ``mcp__graph__draw_edge`` — an edge create.

For **any** other tool name (e.g. ``mcp__graph__lookup_node`` /
``search_nodes`` / ``kg_status``, or anything else entirely) the hook prints
**nothing** and exits 0 — the default permission flow (and the
``permissions.allow`` allowlist for the read tools) takes over.

For a graph write the hook decides:

1. **Exclusion guard.** If any field of ``tool_input`` carries an endpoint /
   path / provenance value (``card_endpoint``, ``series_endpoint``,
   ``endpoint_paths``, ``query_endpoint_path``, ``source_ref``,
   ``source_kind``, ``default_endpoint_path``, ``metadata_endpoint_path``, or
   any value that *looks* like a URL path) that matches an **excluded**
   pattern — ``master-config`` / ``/master/`` / ``/auth/`` / ``/settings/`` /
   ``/health`` / ``/docs`` / ``/redoc`` / ``/openapi.json`` / a non-dashboard
   ``/admin/`` path / or an HTTP write method (``POST``/``PUT``/``PATCH``/
   ``DELETE``) — the write is **denied** with a reason naming the offending
   value. For ``draw_edge`` the ``props_json`` value is parsed (when it is a
   JSON string) and its contents are screened too, so excluded provenance
   (``source_ref``, ``lineage_ref``, ...) hidden inside the props map is
   caught. These are the master-config-free ingestion exclusions from the
   schema (``final-schema-claude.md §8``).

2. **Confirm.** Otherwise the proposed write's fields are rendered as a clean,
   aligned plaintext field table under an action header
   (``Create <Thing> — review fields, then approve:`` for a node create,
   ``Draw edge — review, then approve:`` for an edge) and the write is gated
   with ``permissionDecision: 'ask'`` (the table-then-confirm UX).

Contract (Claude Code 2.1.x):

* stdin is the hook event JSON (``tool_name``, ``tool_input``, ...).
* stdout (on a decision) is
  ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "ask"|"deny"|"allow",
  "permissionDecisionReason": "<plaintext>"}}``.
* exit 0 with stdout → the decision JSON is parsed.
* exit 0 with **no** stdout → fall back to the default permission flow.

On **any** exception this prints nothing and exits 0, so a hook bug can never
block legitimate tool use — it simply defers to the default flow.
"""

from __future__ import annotations

import json
import re
import sys

# --- Tool classification ----------------------------------------------------
#
# The hook only emits a decision for the known graph WRITE tools; for any other
# tool name it defers (no stdout, exit 0). A write is either a node create
# (``mcp__graph__create_<thing>_node``) or the edge tool
# (``mcp__graph__draw_edge``).
_CREATE_NODE_RE = re.compile(r"^mcp__graph__create_.+$")
_DRAW_EDGE_NAME = "mcp__graph__draw_edge"

# --- Label resolution -------------------------------------------------------
#
# Node-create tools are named ``mcp__graph__create_<thing>_node``. Map the
# ``<thing>`` segment to its schema node label so the confirm header reads
# naturally (e.g. ``create_product_node`` -> ``IntelligenceProduct``).
_LABEL_BY_THING: dict[str, str] = {
    "business": "Business",
    "domain": "Domain",
    "product": "IntelligenceProduct",
    "intelligence_product": "IntelligenceProduct",
    "platform": "Platform",
    "metric": "Metric",
    "dashboard": "Dashboard",
    "ui_component": "UIComponent",
    "uicomponent": "UIComponent",
    "component": "UIComponent",
    "policy": "Policy",
    "threshold": "Threshold",
    "role": "Role",
}

# --- Exclusion configuration ------------------------------------------------
#
# Keys whose values are endpoint / path / provenance strings and so must be
# screened against the excluded patterns.
_PROVENANCE_KEYS: frozenset[str] = frozenset(
    {
        "card_endpoint",
        "series_endpoint",
        "endpoint_paths",
        "query_endpoint_path",
        "default_endpoint_path",
        "metadata_endpoint_path",
        "api_base_url_ref",
        "route_path",
        "route_prefixes",
        "source_ref",
        "source_kind",
        "source_registry",
        "lineage_ref",
    }
)

# HTTP write methods are never harvested into the graph (governed Tool/Action,
# V2). A leading method token on a provenance value denies the create.
_HTTP_WRITE_RE = re.compile(r"(?:^|[\s:])(POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)

# Excluded path fragments. ``/admin/`` is handled separately so dashboard admin
# routes (``/admin/dashboards``) stay allowed.
_EXCLUDED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("master-config endpoint", re.compile(r"master[-_]?config", re.IGNORECASE)),
    ("/master/ endpoint", re.compile(r"/master/", re.IGNORECASE)),
    ("/auth/ endpoint", re.compile(r"/auth(?:/|\b)", re.IGNORECASE)),
    ("/settings/ endpoint", re.compile(r"/settings(?:/|\b)", re.IGNORECASE)),
    ("/health endpoint", re.compile(r"/health(?:/|\b|z)", re.IGNORECASE)),
    ("/docs endpoint", re.compile(r"/docs(?:/|\b)", re.IGNORECASE)),
    ("/redoc endpoint", re.compile(r"/redoc(?:/|\b)", re.IGNORECASE)),
    ("/openapi.json endpoint", re.compile(r"/openapi\.json\b", re.IGNORECASE)),
)

# A non-dashboard ``/admin/`` path: ``/admin/`` NOT immediately followed by
# ``dashboard``.
_ADMIN_NON_DASHBOARD_RE = re.compile(r"/admin/(?!dashboard)", re.IGNORECASE)

# Heuristic: a value that "looks like a URL path" — has a slash-delimited
# segment structure (e.g. ``/foo/bar`` or ``foo/bar/baz``) or is an http(s)
# URL. Used to screen values under keys not in ``_PROVENANCE_KEYS``.
_PATHLIKE_RE = re.compile(r"^(?:https?://|/)|/[^/\s]+/")

# Truncate long values in the confirm table to keep the field aligned/clean.
_MAX_VALUE_LEN = 80


def _resolve_label(tool_name: str) -> str:
    """Derive a human node label from an ``mcp__graph__create_<thing>`` name.

    Accepts both ``create_<thing>_node`` and bare ``create_<thing>`` shapes.
    """
    name = tool_name.rsplit("__", 1)[-1] if tool_name else ""
    match = re.fullmatch(r"create_(.+?)(?:_node)?", name)
    thing = match.group(1) if match else name
    return _LABEL_BY_THING.get(thing, thing.replace("_", " ").title() or "Node")


def _iter_str_values(value: object):
    """Yield every string leaf inside a (possibly nested) value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for sub in value.values():
            yield from _iter_str_values(sub)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_str_values(item)


def _excluded_reason(text: str) -> str | None:
    """Return an exclusion reason if ``text`` matches an excluded pattern, else None."""
    if _HTTP_WRITE_RE.search(text):
        return "HTTP write method (POST/PUT/PATCH/DELETE)"
    if _ADMIN_NON_DASHBOARD_RE.search(text):
        return "non-dashboard /admin/ endpoint"
    for label, pattern in _EXCLUDED_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _maybe_parse_json_props(key: str, raw: object) -> object:
    """Expand a JSON-string props value (e.g. ``draw_edge``'s ``props_json``).

    ``draw_edge`` carries edge properties as a JSON *string* under
    ``props_json``. Parse it so its contents (which may include provenance keys
    like ``source_ref`` / ``lineage_ref``) are screened, and re-key the parsed
    map's entries onto their own keys for a precise offending-field report. If
    the value is not a JSON-encoded object, it is returned unchanged.
    """
    if key == "props_json" and isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(parsed, dict):
            return parsed
    return raw


def _scan_exclusions(tool_input: dict) -> tuple[str, str, str] | None:
    """Scan ``tool_input`` for an excluded endpoint/path/provenance value.

    Returns ``(field, offending_value, reason)`` for the first offending value,
    or ``None`` if nothing is excluded. Provenance-named keys are always
    screened; other keys are screened only when their value looks path-like. A
    ``props_json`` JSON string (``draw_edge``) is parsed and its entries are
    screened as if they were top-level fields.
    """
    for key, raw in tool_input.items():
        value = _maybe_parse_json_props(key, raw)
        # When props_json expands to a dict, screen each entry under its own key
        # so provenance keys (source_ref, lineage_ref, ...) are detected and
        # named precisely; otherwise screen the value under the original key.
        entries = value.items() if isinstance(value, dict) else ((key, value),)
        for entry_key, entry_val in entries:
            is_provenance = entry_key in _PROVENANCE_KEYS
            for text in _iter_str_values(entry_val):
                if not text:
                    continue
                if not is_provenance and not _PATHLIKE_RE.search(text):
                    continue
                reason = _excluded_reason(text)
                if reason is not None:
                    return entry_key, text, reason
    return None


def _format_value(value: object) -> str:
    """Render a tool-input value as a single-line string for the field table."""
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        text = ", ".join(_format_value(v) for v in value)
        text = f"[{text}]"
    elif isinstance(value, dict):
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else:
        text = str(value)
    text = " ".join(text.split())  # collapse newlines/whitespace
    if len(text) > _MAX_VALUE_LEN:
        text = text[: _MAX_VALUE_LEN - 1] + "…"
    return text


def _render_table(header: str, tool_input: dict) -> str:
    """Render a clean, left-aligned plaintext field table for the confirm prompt.

    Args:
        header: The action header line (already action-specific).
        tool_input: The proposed write's fields.
    """
    if not tool_input:
        return f"{header}\n  (no fields provided)"
    rows = [(key, _format_value(tool_input[key])) for key in sorted(tool_input)]
    width = max(len(key) for key, _ in rows)
    lines = [f"  {key.ljust(width)} : {value}" for key, value in rows]
    return header + "\n" + "\n".join(lines)


def _decision(decision: str, reason: str) -> dict:
    """Build the PreToolUse hook-output JSON for a permission decision."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def main() -> int:
    """Read the hook event, emit a deny/ask decision, and return the exit code.

    Self-guarding: a decision is emitted **only** for the known graph write
    tools (``mcp__graph__create_*`` and ``mcp__graph__draw_edge``). For any
    other tool name this returns 0 having written nothing, deferring to the
    default permission flow.
    """
    raw = sys.stdin.read()
    event = json.loads(raw)

    tool_name = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Classify the tool. Only known graph WRITE tools get a decision.
    is_edge = tool_name == _DRAW_EDGE_NAME
    is_create = bool(_CREATE_NODE_RE.match(tool_name))
    if not (is_edge or is_create):
        # Read tool / unknown tool: print nothing, defer to the default flow.
        return 0

    if is_edge:
        action = "Draw edge"
        confirm_header = "Draw edge — review, then approve:"
    else:
        label = _resolve_label(tool_name)
        action = f"Create {label}"
        confirm_header = f"Create {label} — review fields, then approve:"

    hit = _scan_exclusions(tool_input)
    if hit is not None:
        field, value, reason = hit
        shown = value if len(value) <= 200 else value[:199] + "…"
        deny_reason = (
            f"{action} blocked — excluded source.\n"
            f"  reason : {reason}\n"
            f"  field  : {field}\n"
            f"  value  : {shown}\n"
            "This value comes from a master-config / control-plane / write "
            "endpoint, which is never ingested into the graph."
        )
        decision = _decision("deny", deny_reason)
    else:
        decision = _decision("ask", _render_table(confirm_header, tool_input))

    sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Never block tool use on a hook bug: print nothing, defer to the
        # default permission flow.
        sys.exit(0)
