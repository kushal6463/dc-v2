"""Server-Sent-Events endpoint wiring for the live canvas.

Bridges the in-process :class:`~harness.api.events.EventBus` to an
``sse-starlette`` :class:`~sse_starlette.sse.EventSourceResponse`. Each bus event
dict is serialized to JSON and pushed to the connected client as a single SSE
``data:`` frame; the stream ends cleanly when the client disconnects (the
subscriber's queue is then unregistered by :meth:`EventBus.subscribe`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from harness.api.events import EventBus, bus


async def event_stream(
    request: Request, event_bus: EventBus | None = None
) -> EventSourceResponse:
    """Return an :class:`EventSourceResponse` streaming the bus to one client.

    Args:
        request: The incoming Starlette/FastAPI request (used to detect client
            disconnects so the subscriber is torn down promptly).
        event_bus: The bus to subscribe to; defaults to the process singleton
            :data:`~harness.api.events.bus`.

    Returns:
        An ``sse-starlette`` response that yields one JSON-encoded SSE frame per
        published event for the lifetime of the connection.
    """
    source = event_bus or bus

    async def _generator() -> AsyncIterator[dict[str, str]]:
        async for event in source.subscribe():
            if await request.is_disconnected():
                break
            # Emit a NAMED frame (`event: <type>`) for clients that register a
            # per-type listener, but ALSO serialize the whole event dict — which
            # always carries its own ``type`` key (see ``EventBus.emit``) — as
            # the JSON ``data`` so unnamed consumers (a bare ``onmessage``) can
            # still recover the type. The frontend handles both forms.
            event_type = str(event.get("type", "message"))
            yield {"event": event_type, "data": json.dumps({**event, "type": event_type})}

    return EventSourceResponse(_generator())
