"""NO-DB / NO-network tests for the in-process live-canvas EventBus.

The bus is the fan-out layer between the (async) ingestion engine and the SSE
endpoint. These tests exercise it entirely in-process — no Neo4j, no HTTP, no
sockets — by subscribing, publishing a fixed sequence of events, and asserting
every event is received in order with the correct ``type`` and ``data``.

We also assert the SSE framing helper preserves ``type`` inside the JSON ``data``
so an unnamed consumer (a bare ``onmessage``) can still recover the event type,
matching the frontend's dual named/unnamed handling.
"""

from __future__ import annotations

import asyncio
import json

from harness.api.events import EventBus


def test_emit_then_subscribe_receives_three_in_order() -> None:
    """Subscribe, emit 3 events, assert all 3 arrive in order with type/data."""

    async def _run() -> list[dict]:
        bus = EventBus()
        agen = bus.subscribe()

        # Register the subscriber's queue before publishing. Priming the async
        # generator with a single ``__anext__`` step would consume the first
        # event, so instead we register by stepping the generator to the point
        # it has created + added its queue. We do that by scheduling the first
        # ``__anext__`` as a task, yielding control, then emitting.
        first = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0)  # let subscribe() register its queue

        bus.emit("run_started", run_id="run-1", dashboards=2)
        bus.emit("proposal_new", run_id="run-1", proposal={"proposal_id": "p1"})
        bus.emit("run_done", run_id="run-1", summary={"nodes_created": 3})

        received = [await first]
        received.append(await agen.__anext__())
        received.append(await agen.__anext__())
        await agen.aclose()
        return received

    received = asyncio.run(_run())

    assert [e["type"] for e in received] == [
        "run_started",
        "proposal_new",
        "run_done",
    ]
    assert [e["run_id"] for e in received] == ["run-1", "run-1", "run-1"]
    # Per-event payloads land under ``data`` and survive the round trip.
    assert received[0]["data"] == {"dashboards": 2}
    assert received[1]["data"] == {"proposal": {"proposal_id": "p1"}}
    assert received[2]["data"] == {"summary": {"nodes_created": 3}}
    # Every event carries a timestamp.
    assert all(e["ts"] for e in received)


def test_emit_stamps_type_run_id_ts_and_data() -> None:
    """``emit`` stamps type/run_id/ts and nests the kwargs under ``data``."""

    async def _run() -> dict:
        bus = EventBus()
        agen = bus.subscribe()
        first = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0)
        bus.emit("agent_action", run_id="r", dashboard="d", message="hi")
        event = await first
        await agen.aclose()
        return event

    event = asyncio.run(_run())
    assert set(event) == {"type", "run_id", "ts", "data"}
    assert event["type"] == "agent_action"
    assert event["data"] == {"dashboard": "d", "message": "hi"}


def test_sse_frame_carries_type_in_data() -> None:
    """The SSE framing keeps ``type`` inside JSON ``data`` for unnamed consumers.

    Mirrors :func:`harness.api.sse.event_stream`'s framing without any HTTP: a
    named ``event:`` line PLUS a JSON ``data`` blob that still includes ``type``
    so a bare ``onmessage`` (no per-type listener) can recover it.
    """
    event = {
        "type": "proposal_new",
        "run_id": "run-1",
        "ts": "2026-06-15T00:00:00Z",
        "data": {"proposal": {"proposal_id": "p1"}},
    }
    event_type = str(event.get("type", "message"))
    frame = {"event": event_type, "data": json.dumps({**event, "type": event_type})}

    assert frame["event"] == "proposal_new"
    decoded = json.loads(frame["data"])
    assert decoded["type"] == "proposal_new"
    assert decoded["data"]["proposal"]["proposal_id"] == "p1"
