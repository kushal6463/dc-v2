"""Claude Agent SDK wrapper for the metric / UIComponent ingestion engine.

This is the thin engine layer that the proposer / orchestrator call to get a
*single* schema-constrained JSON object back from a Claude sub-agent
(implementation plan sections 5b, 6). It uses subscription OAuth auth (the
``claude-agent-sdk`` reuses the logged-in CLI credentials in a subprocess;
``ANTHROPIC_API_KEY`` is intentionally **not** set) and runs every call with
``setting_sources=[]`` so the proposer does **not** load this project's hooks,
MCP servers, or skills.

Structured-output mechanism (empirically verified against installed SDK
0.2.101)
-----------------------------------------------------------------------------
The SDK accepts ``ClaudeAgentOptions(output_format={"type": "json_schema",
"schema": <schema>})`` and, when the model complies, populates
:attr:`ResultMessage.structured_output` with the parsed object. Verified
behaviour on this SDK:

* ``output_format`` json_schema **does** yield a clean dict in
  ``ResultMessage.structured_output`` (e.g. ``{"proposals": []}``) — but the CLI
  consumes one extra turn for the structured-output mechanism, so it requires a
  turn budget of **>= 2** (with ``max_turns=1`` the CLI raises "Reached maximum
  number of turns").
* The portable fallback — instruct the model to emit a single fenced
  ````json ... ```` block and parse it out of ``ResultMessage.result`` — works
  reliably at ``max_turns=1`` and is cheaper.

So this module tries the ``output_format`` path first (giving it the +1 turn it
needs) and uses the parsed ``structured_output`` when present; otherwise it
falls back to extracting and parsing a fenced JSON block from the assistant
text. Either way it returns a parsed ``dict`` or raises a clear error.

Reliability / cost controls
---------------------------
Each call is wrapped in an :func:`asyncio.timeout` (``per_call_timeout_s``) so a
hung subprocess raises :class:`TimeoutError` (treated as transient -> retried),
accepts an optional ``max_budget_usd`` hard cost cap forwarded to the SDK, and
surfaces the per-call ``total_cost_usd`` / ``num_turns`` (via an ``agent_call``
event and a ``_meta`` key on the return) so the orchestrator can tally spend.
Deterministic / terminal failures (turn-limit, auth) raise
:class:`TerminalAgentError` and are *not* retried; only genuinely transient
failures use the ``[5, 15, 45]``-second backoff.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Coroutine
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)
from claude_agent_sdk.types import McpServerConfig, McpStdioServerConfig

from harness.store.jsonl import append_event

#: Retry backoff schedule (seconds) for transient SDK / API errors.
_BACKOFF_SCHEDULE: tuple[int, ...] = (5, 15, 45)

#: Default wall-clock timeout (seconds) around a single SDK query loop.
_DEFAULT_PER_CALL_TIMEOUT_S: float = 180.0

#: Result subtypes that are *terminal* (deterministic) — retrying cannot help, so
#: we fail fast rather than burn the backoff schedule. Covers turn-limit and the
#: auth / login failure conditions the CLI surfaces.
_TERMINAL_RESULT_SUBTYPES: frozenset[str] = frozenset(
    {
        "error_max_turns",
        "error_auth",
        "error_login",
        "error_invalid_api_key",
        "error_permission",
    }
)

#: Matches the first fenced ```json ... ``` (or bare ``` ... ```) code block.
_FENCED_JSON_RE = re.compile(
    r"```(?:json)?\s*(?P<body>.*?)```",
    re.DOTALL | re.IGNORECASE,
)


class StructuredOutputError(RuntimeError):
    """Raised when the agent fails to return parseable JSON after all retries."""


class TerminalAgentError(StructuredOutputError):
    """Raised for a deterministic / terminal agent failure that must NOT retry.

    Covers turn-limit, auth / login failure and similar conditions where the same
    request would fail identically on retry — re-raised immediately past the
    backoff loop.
    """


def run_sync[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine to completion from synchronous CLI code.

    A thin wrapper over :func:`asyncio.run` so callers in the (synchronous) CLI
    can drive the async :func:`propose_structured` / orchestrator coroutines.

    Args:
        coro: The coroutine to run.

    Returns:
        The coroutine's result.
    """
    return asyncio.run(coro)


def _extract_text(messages: list[Any]) -> str:
    """Concatenate the text of every :class:`TextBlock` in assistant messages."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
    return "\n".join(parts)


def _parse_fenced_json(text: str) -> dict[str, Any] | None:
    """Extract and parse the first fenced JSON code block from ``text``.

    Tries each fenced block in order (the model may emit prose first), returning
    the first that parses to a JSON object. Falls back to parsing the whole
    string as JSON when no fenced block parses.

    Returns:
        The parsed object, or ``None`` if nothing parseable was found.
    """
    for match in _FENCED_JSON_RE.finditer(text):
        body = match.group("body").strip()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        # A bare top-level array is a valid proposals reply; wrap it so it is
        # not treated as unparsed.
        if isinstance(parsed, list):
            return {"proposals": parsed}
    # No fenced block parsed — try the raw text as a last resort.
    stripped = text.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"proposals": parsed}
    return None


async def _run_query(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    max_turns: int,
    model: str | None,
    per_call_timeout_s: float = _DEFAULT_PER_CALL_TIMEOUT_S,
    max_budget_usd: float | None = None,
    dashboard: str | None = None,
) -> dict[str, Any]:
    """Run a single agent query and return the parsed structured-output dict.

    Tries the ``output_format`` json_schema mechanism first (giving it the extra
    turn it needs on this SDK), reading :attr:`ResultMessage.structured_output`;
    if that is absent, falls back to parsing a fenced JSON block out of the
    assistant text.

    The whole ``async for`` loop is wrapped in :func:`asyncio.timeout` so a hung
    SDK subprocess raises :class:`TimeoutError` (treated as transient by the
    caller's retry loop). When ``max_budget_usd`` is set it is passed straight to
    the SDK as a hard cost cap. The result's ``total_cost_usd`` / ``num_turns``
    are logged via :func:`~harness.store.jsonl.append_event` for cost
    observability and returned alongside the parsed object.

    Raises:
        TerminalAgentError: If the result is a deterministic / terminal error
            (turn-limit, auth) that must not be retried.
        StructuredOutputError: If the result is a transient error or no JSON
            could be parsed from either mechanism.
        TimeoutError: If the query exceeds ``per_call_timeout_s`` (transient).
    """
    # The json_schema mechanism consumes one extra turn on this SDK, so ensure a
    # budget of at least 2 turns for it while still honouring a larger request.
    turn_budget = max(max_turns + 1, 2)

    option_kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "max_turns": turn_budget,
        "model": model,
        "setting_sources": [],  # do NOT load this project's hooks / MCP / skills
    }
    # json_schema constrained generation is preferred, but can stall the SDK on
    # some content; KG_NO_JSON_SCHEMA=1 falls back to free-form + fenced-JSON parse.
    if not os.environ.get("KG_NO_JSON_SCHEMA"):
        option_kwargs["output_format"] = {"type": "json_schema", "schema": schema}
    if max_budget_usd is not None:
        option_kwargs["max_budget_usd"] = max_budget_usd  # SDK hard cost cap

    options = ClaudeAgentOptions(**option_kwargs)

    messages: list[Any] = []
    result_msg: ResultMessage | None = None
    # Wall-clock timeout around the whole stream; a hang raises TimeoutError,
    # which the caller treats as transient and retries.
    async with asyncio.timeout(per_call_timeout_s):
        async for message in query(prompt=user_prompt, options=options):
            messages.append(message)
            if isinstance(message, ResultMessage):
                result_msg = message

    if result_msg is None:
        raise StructuredOutputError("Agent produced no ResultMessage.")

    # Cost observability: surface per-call spend + turns regardless of outcome.
    cost_usd = result_msg.total_cost_usd
    num_turns = result_msg.num_turns
    append_event(
        {
            "type": "agent_call",
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "dashboard": dashboard,
            "subtype": result_msg.subtype,
            "is_error": result_msg.is_error,
        }
    )

    if result_msg.is_error:
        # Fail fast on deterministic / terminal conditions (turn-limit, auth);
        # an HTTP 4xx with subtype "success" is also a clear non-transient error.
        terminal = (
            result_msg.subtype in _TERMINAL_RESULT_SUBTYPES
            or (
                result_msg.api_error_status is not None
                and 400 <= result_msg.api_error_status < 500
                and result_msg.api_error_status != 429
            )
        )
        message = (
            f"Agent returned an error result (subtype={result_msg.subtype!r}, "
            f"api_error_status={result_msg.api_error_status!r})."
        )
        if terminal:
            raise TerminalAgentError(message)
        raise StructuredOutputError(message)

    # 1) Preferred: the SDK-parsed structured output from output_format.
    structured = result_msg.structured_output
    if isinstance(structured, dict):
        return _with_cost(structured, cost_usd, num_turns)

    # 2) Fallback: a fenced JSON block in the assistant text / final result.
    text = result_msg.result or _extract_text(messages)
    parsed = _parse_fenced_json(text or "")
    if parsed is not None:
        return _with_cost(parsed, cost_usd, num_turns)

    raise StructuredOutputError(
        "Agent returned no structured_output and no parseable JSON block."
    )


def _with_cost(
    parsed: dict[str, Any], cost_usd: float | None, num_turns: int | None
) -> dict[str, Any]:
    """Attach per-call cost metadata to a parsed result under ``_meta``.

    The orchestrator can read ``result["_meta"]["cost_usd"]`` to tally spend
    across dashboards; the proposer reads only ``result["proposals"]`` and
    ignores ``_meta``.
    """
    meta = {"cost_usd": cost_usd, "num_turns": num_turns}
    if isinstance(parsed.get("_meta"), dict):
        parsed["_meta"].update(meta)
    else:
        parsed = {**parsed, "_meta": meta}
    return parsed


async def propose_structured(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    max_turns: int = 1,
    model: str | None = None,
    per_call_timeout_s: float = _DEFAULT_PER_CALL_TIMEOUT_S,
    max_budget_usd: float | None = None,
    dashboard: str | None = None,
) -> dict[str, Any]:
    """Call the agent and return a JSON object matching ``schema``.

    Drives the Claude Agent SDK with ``setting_sources=[]`` (no project hooks /
    MCP / skills) and a json-schema ``output_format``. Returns the parsed dict
    from :attr:`ResultMessage.structured_output` when the model complies,
    otherwise from a fenced ```json``` block in the assistant text. Retries
    *genuinely transient* errors (process spawn, network / rate-limit, timeout)
    with a ``[5, 15, 45]``-second backoff; deterministic / terminal failures
    (turn-limit, auth — :class:`TerminalAgentError`) are re-raised immediately.

    Args:
        system_prompt: The system prompt establishing the agent's role/rules.
        user_prompt: The task prompt (the per-dashboard drafts + spine context).
        schema: A JSON Schema describing the expected object (a top-level
            ``"proposals"`` array for the proposer).
        max_turns: Logical turn budget; the engine adds one internally for the
            structured-output mechanism (default 1 -> 2 effective turns).
        model: Optional model id override (defaults to the SDK's default model).
        per_call_timeout_s: Wall-clock timeout around each SDK query loop; a
            timeout is transient and is retried.
        max_budget_usd: Optional hard cost cap passed to the SDK per call.
        dashboard: Optional dashboard id, recorded in the cost event for tallying.

    Returns:
        The parsed JSON object (roughly validated to match ``schema`` — it is a
        ``dict``; the proposer does the field-level normalization). A ``_meta``
        key carries the per-call ``cost_usd`` / ``num_turns``.

    Raises:
        TerminalAgentError: On a deterministic / terminal failure (no retry).
        StructuredOutputError: If no parseable JSON is produced after retries.
    """
    # Allow a process-wide proposer model override via env (speed/cost tuning);
    # an explicit ``model`` arg still wins.
    if model is None:
        model = os.environ.get("KG_PROPOSER_MODEL") or None

    last_error: Exception | None = None
    # One initial attempt (delay 0) plus one retry per backoff entry.
    for delay in (0, *_BACKOFF_SCHEDULE):
        if delay:
            await asyncio.sleep(delay)
        try:
            result = await _run_query(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=schema,
                max_turns=max_turns,
                model=model,
                per_call_timeout_s=per_call_timeout_s,
                max_budget_usd=max_budget_usd,
                dashboard=dashboard,
            )
        except TerminalAgentError:
            # Deterministic / terminal (turn-limit, auth) — retrying cannot help.
            raise
        except Exception as exc:  # noqa: BLE001 — retry genuinely transient ones
            last_error = exc
            continue
        return result

    raise StructuredOutputError(
        f"propose_structured failed after {len(_BACKOFF_SCHEDULE) + 1} attempts: "
        f"{last_error}"
    ) from last_error


async def _run_mcp_query(
    *,
    system_prompt: str,
    user_prompt: str,
    mcp_servers: dict[str, McpServerConfig],
    allowed_tools: list[str] | None,
    disallowed_tools: list[str] | None,
    max_turns: int,
    model: str | None,
    per_call_timeout_s: float,
    max_budget_usd: float | None,
    cwd: str | None,
    label: str | None,
) -> dict[str, Any]:
    """Run a single tool-calling agent query, driving the SDK tool-use loop.

    Unlike :func:`_run_query` (structured JSON output), this attaches an MCP
    server and lets the agent *act* (call ``mcp__graph__*`` write tools) over
    many turns; the SDK runs the tool-use loop internally (each tool call is
    auto-approved by ``permission_mode="bypassPermissions"``), and we consume the
    stream until the terminal :class:`ResultMessage`. ``setting_sources=[]`` keeps
    the agent from loading this project's own hooks / skills / MCP config — only
    the explicitly-passed ``mcp_servers`` are exposed.

    Returns:
        ``{"text", "cost_usd", "num_turns", "duration_ms", "tool_calls",
        "subtype"}`` — the assistant's final text plus run telemetry.

    Raises:
        TerminalAgentError: On a deterministic / terminal error (turn-limit,
            auth, permission) that must not be retried.
        StructuredOutputError: On a transient error result.
        TimeoutError: If the run exceeds ``per_call_timeout_s`` (transient).
    """
    option_kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "mcp_servers": mcp_servers,
        "permission_mode": "bypassPermissions",  # auto-approve every tool call
        "max_turns": max(max_turns, 2),
        "model": model,
        "setting_sources": [],  # do NOT load this project's hooks / MCP / skills
    }
    if allowed_tools is not None:
        option_kwargs["allowed_tools"] = allowed_tools
    if disallowed_tools is not None:
        option_kwargs["disallowed_tools"] = disallowed_tools
    if max_budget_usd is not None:
        option_kwargs["max_budget_usd"] = max_budget_usd
    if cwd is not None:
        option_kwargs["cwd"] = cwd

    options = ClaudeAgentOptions(**option_kwargs)

    messages: list[Any] = []
    result_msg: ResultMessage | None = None
    tool_calls = 0
    async with asyncio.timeout(per_call_timeout_s):
        async for message in query(prompt=user_prompt, options=options):
            messages.append(message)
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # ToolUseBlock has a ``name``; count agent actions for telemetry.
                    if getattr(block, "name", None) and hasattr(block, "input"):
                        tool_calls += 1
            if isinstance(message, ResultMessage):
                result_msg = message

    if result_msg is None:
        raise StructuredOutputError("Agent produced no ResultMessage.")

    cost_usd = result_msg.total_cost_usd
    num_turns = result_msg.num_turns
    duration_ms = result_msg.duration_ms
    append_event(
        {
            "type": "agent_call",
            "cost_usd": cost_usd,
            "num_turns": num_turns,
            "duration_ms": duration_ms,
            "tool_calls": tool_calls,
            "label": label,
            "subtype": result_msg.subtype,
            "is_error": result_msg.is_error,
        }
    )

    if result_msg.is_error:
        terminal = (
            result_msg.subtype in _TERMINAL_RESULT_SUBTYPES
            or (
                result_msg.api_error_status is not None
                and 400 <= result_msg.api_error_status < 500
                and result_msg.api_error_status != 429
            )
        )
        message = (
            f"Agent returned an error result (subtype={result_msg.subtype!r}, "
            f"api_error_status={result_msg.api_error_status!r})."
        )
        if terminal:
            raise TerminalAgentError(message)
        raise StructuredOutputError(message)

    text = result_msg.result or _extract_text(messages)
    return {
        "text": text or "",
        "cost_usd": cost_usd,
        "num_turns": num_turns,
        "duration_ms": duration_ms,
        "tool_calls": tool_calls,
        "subtype": result_msg.subtype,
    }


async def propose_with_mcp(
    *,
    system_prompt: str,
    user_prompt: str,
    mcp_servers: dict[str, McpServerConfig],
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    max_turns: int = 60,
    model: str | None = None,
    per_call_timeout_s: float = _DEFAULT_PER_CALL_TIMEOUT_S,
    max_budget_usd: float | None = None,
    cwd: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Drive a tool-calling agent that writes the graph through an MCP server.

    Mirrors :func:`propose_structured` (same subscription-OAuth auth,
    ``setting_sources=[]``, ``[5, 15, 45]``-second transient-retry backoff, and
    :class:`TerminalAgentError` fast-fail) but configures the agent for the
    agentic-builder tool-use loop: it attaches ``mcp_servers`` (the graph MCP
    server exposing ``mcp__graph__*``), auto-approves every tool call with
    ``permission_mode="bypassPermissions"``, restricts the toolset via
    ``allowed_tools`` / ``disallowed_tools``, and runs many turns so the agent can
    read its source and write nodes + edges. The SDK runs the tool-use loop
    internally; this returns the agent's final text + run telemetry.

    Args:
        system_prompt: The phase system prompt (role + rules).
        user_prompt: The per-slice task prompt.
        mcp_servers: MCP server config mapping (e.g. ``{"graph": {...}}``); the
            graph server exposes the ``mcp__graph__*`` read/write tools.
        allowed_tools: Explicit tool allowlist (e.g. the ``mcp__graph__*`` write +
            doc-reading tools). ``None`` lets the SDK default apply.
        disallowed_tools: Builtin tools to forbid (Bash / Write / Edit / …).
        max_turns: Tool-use turn budget (the agent needs many turns to build a
            slice; default 60).
        model: Model id (the orchestrator passes ``"claude-opus-4-8"``).
        per_call_timeout_s: Wall-clock timeout around the whole run (transient).
        max_budget_usd: Optional hard per-run cost cap forwarded to the SDK.
        cwd: Optional working directory for the SDK subprocess.
        label: Optional slice label recorded in the cost event for tallying.

    Returns:
        ``{"text", "cost_usd", "num_turns", "duration_ms", "tool_calls",
        "subtype"}``.

    Raises:
        TerminalAgentError: On a deterministic / terminal failure (no retry).
        StructuredOutputError: If the run keeps failing after the retry backoff.
    """
    if model is None:
        model = os.environ.get("KG_BUILDER_MODEL") or None

    last_error: Exception | None = None
    for delay in (0, *_BACKOFF_SCHEDULE):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await _run_mcp_query(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                mcp_servers=mcp_servers,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                max_turns=max_turns,
                model=model,
                per_call_timeout_s=per_call_timeout_s,
                max_budget_usd=max_budget_usd,
                cwd=cwd,
                label=label,
            )
        except TerminalAgentError:
            raise
        except Exception as exc:  # noqa: BLE001 — retry genuinely transient ones
            last_error = exc
            continue

    raise StructuredOutputError(
        f"propose_with_mcp failed after {len(_BACKOFF_SCHEDULE) + 1} attempts: "
        f"{last_error}"
    ) from last_error


# Re-exported for callers that build their own McpServerConfig dicts.
__all__ = [
    "McpServerConfig",
    "McpStdioServerConfig",
    "StructuredOutputError",
    "TerminalAgentError",
    "propose_structured",
    "propose_with_mcp",
    "run_sync",
]
