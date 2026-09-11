"""Unit tests for invoice_agent/events.py - the in-memory SSE event bus
(Phase 8.5). No FastAPI, no graph, no network - see tests/test_api_stream.py
for the end-to-end version through the actual API.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from invoice_agent import events


@pytest.fixture(autouse=True)
def _reset_registry():
    events.reset()
    yield
    events.reset()


def test_open_channel_is_idempotent():
    assert events.open_channel("t1") is True
    assert events.open_channel("t1") is True
    assert events.has_channel("t1") is True


def test_open_channel_respects_max_channels(monkeypatch):
    monkeypatch.setattr(events, "MAX_CHANNELS", 2)
    assert events.open_channel("a") is True
    assert events.open_channel("b") is True
    assert events.open_channel("c") is False
    assert events.has_channel("c") is False


def test_sweep_stale_removes_old_channels_only(monkeypatch):
    events.open_channel("old")
    events.open_channel("fresh")
    events._channels["old"].last_activity -= 10_000  # simulate age

    removed = events.sweep_stale(max_age_seconds=100)

    assert removed == 1
    assert events.has_channel("old") is False
    assert events.has_channel("fresh") is True


def test_open_channel_sweeps_stale_before_rejecting(monkeypatch):
    monkeypatch.setattr(events, "MAX_CHANNELS", 1)
    events.open_channel("old")
    events._channels["old"].last_activity -= 10_000
    monkeypatch.setattr(events, "CHANNEL_TTL_SECONDS", 100)

    assert events.open_channel("new") is True
    assert events.has_channel("old") is False


def test_subscribe_unknown_thread_raises_channel_closed():
    async def _run():
        with pytest.raises(events.ChannelClosed):
            async for _item, _is_current in events.subscribe("nope"):
                pass

    asyncio.run(_run())


def test_publish_before_bind_loop_is_a_safe_no_op():
    events.open_channel("t1")
    events.publish("t1", "field", name="vendor_name", value="Acme")  # no loop bound, no raise


def test_publish_cross_thread_delivers_to_subscriber():
    """The core thread-safety property: publish() is called from a worker
    thread (as it would be from inside a graph node), and must deliver onto
    the loop bound via bind_loop(), not block or corrupt the queue.

    close_channel() itself never publishes a "run_end" event - it only
    wakes subscribers via the _DONE sentinel. Publishing the terminal event
    before closing is the caller's job (app/service.py's
    _publish_run_end), so this test does the same two-step explicitly.
    """

    async def _run():
        loop = asyncio.get_running_loop()
        events.bind_loop(loop)
        events.open_channel("t1")

        received: list[events.Event] = []

        async def _consume():
            async for item, _is_current in events.subscribe("t1"):
                if item is not None:
                    received.append(item)
                    if item.type == "run_end":
                        return

        consume_task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)  # let the subscriber attach before publishing

        def _publish_from_thread():
            events.publish("t1", "field", name="vendor_name", value="Acme")
            events.publish("t1", "run_end", status="completed")
            events.close_channel("t1")

        thread = threading.Thread(target=_publish_from_thread)
        thread.start()
        thread.join()

        await asyncio.wait_for(consume_task, timeout=2)
        return received

    received = asyncio.run(_run())
    types = [e.type for e in received]
    assert "field" in types
    assert types[-1] == "run_end"


def test_overflow_drops_oldest_and_marks_dropped():
    # Exercises _deliver() directly against a hand-built subscriber, not
    # through subscribe() - a real subscribe() generator, once advanced
    # past its (empty) backlog replay, immediately starts a genuine
    # `await queue.get()` as part of satisfying that first __anext__(),
    # which drains the very first delivered item concurrently with the
    # publish loop below and throws off the exact-count assertion this
    # test is making. No event loop is needed here: put_nowait/get_nowait/
    # full()/qsize() are all synchronous.
    events.open_channel("t1")
    channel = events._channels["t1"]
    subscriber = events._Subscriber(queue=asyncio.Queue(maxsize=events.MAX_SUBSCRIBER_QUEUE))
    channel.subscribers.append(subscriber)

    for i in range(events.MAX_SUBSCRIBER_QUEUE + 50):
        events._deliver(subscriber, events.Event(type="field", thread_id="t1", seq=i, payload={"i": i}))

    assert subscriber.queue.qsize() == events.MAX_SUBSCRIBER_QUEUE
    assert subscriber.dropped is True
    # The overflow marker itself occupies one slot alongside real events -
    # confirm it actually made it into the queue, not just the flag, and
    # that the queue never exceeded maxsize while inserting it (the exact
    # bug the two-separate-_put_drop_oldest-calls design fixes).
    remaining = subscriber.queue.qsize()
    queued_types = {subscriber.queue.get_nowait().type for _ in range(remaining)}
    assert "overflow" in queued_types
    assert "field" in queued_types


def test_close_channel_wakes_attached_subscriber_without_hanging():
    async def _run():
        loop = asyncio.get_running_loop()
        events.bind_loop(loop)
        events.open_channel("t1")

        async def _consume():
            items = []
            async for item, _is_current in events.subscribe("t1"):
                items.append(item)
            return items

        task = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        events.close_channel("t1")

        items = await asyncio.wait_for(task, timeout=2)
        assert items == []  # no events published, just an immediate close

    asyncio.run(_run())


def test_interrupt_does_not_close_channel():
    """Only run_end/interrupted (via GraphService, not this module directly)
    close a channel - events.py itself has no opinion on what "interrupted"
    means, it just never auto-closes on any particular event type."""
    events.open_channel("t1")
    events.publish("t1", "interrupted", status="needs_review")
    assert events.has_channel("t1") is True
