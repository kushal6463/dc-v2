"""In-process event bus for the live-canvas SSE channel.

This is the fan-out layer that bridges the (async) ingestion engine and any
number of connected Server-Sent-Events clients. The engine calls :meth:`EventBus.emit`
(or passes ``bus.emit`` as the optional ``emit`` hook into
:func:`harness.ingest.orchestrator.ingest_dashboards` /
:func:`harness.ingest.apply.apply_approved`); the SSE endpoint
(:mod:`harness.api.sse`) iterates :meth:`EventBus.subscribe`.

The bus is deliberately tiny and lossy: each subscriber owns a bounded
:class:`asyncio.Queue` and :meth:`publish` is *non-blocking* — if a slow client's
queue is full the event is dropped for that client only (never blocking the
engine or other subscribers). A module-level singleton :data:`bus` is shared by
the whole process.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

#: Per-subscriber queue bound. Events beyond this for a slow client are dropped.
_QUEUE_MAXSIZE: int = 1000


class EventBus:
    """A non-blocking, in-process pub/sub bus for canvas events.

    Subscribers each hold a bounded :class:`asyncio.Queue`; :meth:`publish`
    delivers a (already-stamped) event dict to every subscriber without blocking,
    dropping for any subscriber whose queue is full.
    """

    def __init__(self) -> None:
        """Create an empty bus with no subscribers."""
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        #: The event loop the subscribers live on (captured on subscribe).
        self._loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: dict[str, Any]) -> None:
        """Deliver ``event`` to every subscriber, non-blocking (drops if full).

        Thread-safe: the engine runs ingestion in a worker thread (its own
        ``asyncio.run`` loop, mirroring the CLI), so ``emit`` is called off the
        SSE loop. When that happens we marshal delivery back onto the subscriber
        loop via :meth:`loop.call_soon_threadsafe`; called from the loop thread
        itself we deliver directly.

        Args:
            event: A fully-formed event dict (already carrying ``type`` / ``ts``).
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            # Called from another thread (or no running loop) — hop to the SSE loop.
            try:
                self._loop.call_soon_threadsafe(self._deliver, event)
            except RuntimeError:  # loop closed — nothing to deliver to
                pass
        else:
            self._deliver(event)

    def _deliver(self, event: dict[str, Any]) -> None:
        """Fan ``event`` out to every subscriber queue (runs on the SSE loop)."""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # slow client — drop for it only
                continue

    def emit(self, type: str, run_id: str | None = None, **data: Any) -> None:
        """Stamp ``ts`` (+ ``type`` / ``run_id``) onto an event and publish it.

        This is the convenience the engine's ``emit`` hook calls. It is a plain
        (non-async) method so it is safe to call from any context.

        Args:
            type: The event type (e.g. ``"proposal_new"``).
            run_id: The run this event belongs to, if any.
            **data: The event-type-specific payload (delivered under ``data``).
        """
        self.publish(
            {
                "type": type,
                "run_id": run_id,
                "ts": datetime.now(UTC).isoformat(),
                "data": data,
            }
        )

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        """Register a subscriber and yield its events until cancelled.

        Yields:
            Each event dict published while subscribed. The subscriber's queue is
            registered on entry and removed when the iterator is closed/cancelled
            (e.g. the SSE client disconnects).
        """
        # Capture the loop the SSE clients live on, so off-thread publishers
        # (the worker-thread ingest) can marshal events back to it.
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """Number of currently-registered subscribers (for diagnostics)."""
        return len(self._subscribers)


#: Process-wide singleton bus shared by the engine emit hooks and the SSE route.
bus = EventBus()
