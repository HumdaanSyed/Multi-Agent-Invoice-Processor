"""In-memory event bus for streaming a run's progress over SSE (Phase 8.5).

**Single-worker only.** This registry is a plain module-level dict guarded
by a `threading.Lock` - it has no cross-process visibility at all. That's
consistent with this project's existing single-Uvicorn-worker deployment
constraint (CLAUDE.md: "Run a single Uvicorn worker on 1GB RAM instances"),
but it is a real ceiling, not an implementation detail to paper over: a
second worker process would maintain its own independent registry, and a
publish in one process would never reach a subscriber connected to another.
Scaling to multiple workers means moving this to Redis pub/sub (or an
equivalent shared broker), not adding locking here - locking only helps
threads that share memory.

Import-time side-effect-free (a dict, a Lock, and nothing else) - this
module is imported from invoice_agent/graph.py, which the eval harness and
MCP ingestion also import, so it must never open a connection or do I/O at
import time.

**Design summary** (see docs/api.md for the full event-type contract):

- A channel is opened *before* any graph work exists (`POST /invoices/reserve`,
  or a resume) - never lazily on first subscribe. That makes "no channel and
  no checkpointed thread" a real 404, not a connection that hangs forever
  waiting for events nobody will ever publish.
- `publish()` is called from inside graph nodes running on a worker thread
  (FastAPI runs every graph-touching handler in a threadpool - see
  app/service.py's module docstring). It must never raise (a bus failure
  must never break invoice processing) and must never block (blocking the
  publisher could stall extraction, or deadlock against the very event loop
  it's handing work to).
- A channel survives an `interrupt()` - only the SSE *connection* closes.
  The resume path re-enters at `human_review -> validator` and needs
  somewhere to keep publishing. Only a genuine terminal outcome
  (`completed`/`skipped`/`failed`) closes the channel.
- Multiple subscribers per channel are supported (fan-out) - React 18
  StrictMode double-mounts components in dev, so two concurrent
  `EventSource` connections to the same thread_id is routine, not a
  hypothetical.
- Every published event is kept in a bounded history so a subscriber that
  attaches late (or reconnects with `Last-Event-ID`) still sees everything.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger("invoice_agent.events")

HEARTBEAT_SECONDS = 15.0
MAX_SUBSCRIBER_QUEUE = 256
MAX_HISTORY = 512
MAX_CHANNELS = 64
CHANNEL_TTL_SECONDS = 1800.0
TERMINAL_EVENT_TYPES = frozenset({"run_end", "interrupted"})

_DONE = object()  # sentinel pushed to a subscriber queue on close_channel()


class ChannelClosed(Exception):
    """Raised by subscribe() when thread_id has no open channel."""

    def __init__(self, thread_id: str) -> None:
        super().__init__(f"No open channel for thread_id={thread_id!r}")
        self.thread_id = thread_id


@dataclass(frozen=True)
class Event:
    type: str
    thread_id: str
    seq: int
    payload: dict

    def to_sse(self) -> str:
        import json

        data = json.dumps({"thread_id": self.thread_id, **self.payload})
        return f"id: {self.seq}\nevent: {self.type}\ndata: {data}\n\n"


@dataclass
class _Subscriber:
    queue: "asyncio.Queue[Any]"
    dropped: bool = False


@dataclass
class _Channel:
    seq: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY))
    subscribers: list = field(default_factory=list)
    last_activity: float = field(default_factory=time.time)
    closed: bool = False


_channels: dict[str, _Channel] = {}
_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None


def bind_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    """Register the event loop publish() must deliver onto.

    Called once in app/main.py's lifespan (`bind_loop(asyncio.get_running_loop())`
    on startup, `bind_loop(None)` in the finally on shutdown) - that lifespan
    coroutine itself runs *on* the loop being captured. Every subsequent
    publish() crosses a thread boundary (worker thread -> this loop) via
    `loop.call_soon_threadsafe`, never a bare `queue.put_nowait`, because
    `asyncio.Queue` is not thread-safe.
    """
    global _loop
    _loop = loop


def open_channel(thread_id: str) -> bool:
    """Idempotent: create a channel for thread_id if one doesn't exist.

    Returns False only if the registry is at MAX_CHANNELS and a sweep didn't
    free room - callers (POST /invoices/reserve) translate that to a 503
    ServerBusy rather than silently proceeding unstreamed.
    """
    with _lock:
        if thread_id in _channels:
            _channels[thread_id].last_activity = time.time()
            return True
        if len(_channels) >= MAX_CHANNELS:
            _sweep_stale_locked(CHANNEL_TTL_SECONDS)
            if len(_channels) >= MAX_CHANNELS:
                return False
        _channels[thread_id] = _Channel()
        return True


def has_channel(thread_id: str) -> bool:
    with _lock:
        return thread_id in _channels


def backlog_length(thread_id: str, last_event_id: Optional[int] = None) -> int:
    """How many history entries subscribe() would replay for thread_id
    right now, before any live events. The SSE route uses this to tell a
    *stale* terminal event apart from a *current* one while replaying
    history: a run that interrupted, then got resumed (which re-enters at
    human_review -> validator, per invoice_agent/graph.py's unconditional
    edge, so a second pass republishes its own validation_start/checks/
    interrupted or run_end) leaves an earlier "interrupted" sitting
    mid-history with more, newer events after it - only the *last* replayed
    item closing the connection is correct; an early one closing it would
    truncate the reply before the resume's own events ever get sent.

    Called immediately before subscribe() in the same coroutine, with no
    `await` between the two calls - so there's no window for a publish to
    land in between and desync the count from what subscribe() actually
    replays.
    """
    with _lock:
        channel = _channels.get(thread_id)
        if channel is None:
            return 0
        return sum(1 for e in channel.history if last_event_id is None or e.seq > last_event_id)


def publish(thread_id: str, event_type: str, **payload: Any) -> None:
    """Publish an event to thread_id's channel, if one is open.

    Never raises and never blocks - this is called from inside graph nodes
    on the invoice-processing critical path. A no-op (not an error) when:
    no channel is open (the common case for evals/MCP ingestion/scripts,
    none of which reserve a channel), no loop is bound (nothing is serving
    SSE at all - e.g. `scripts/run_graph.py`), or delivery otherwise fails.
    """
    try:
        with _lock:
            channel = _channels.get(thread_id)
            if channel is None or channel.closed:
                return
            seq = channel.seq
            channel.seq += 1
            channel.last_activity = time.time()
            event = Event(type=event_type, thread_id=thread_id, seq=seq, payload=payload)
            channel.history.append(event)
            subscribers = list(channel.subscribers)

        loop = _loop
        if loop is None:
            return
        for subscriber in subscribers:
            loop.call_soon_threadsafe(_deliver, subscriber, event)
    except Exception:  # noqa: BLE001 - a bus failure must never break processing
        logger.debug("events.publish failed for thread_id=%s type=%s", thread_id, event_type, exc_info=True)


def _put_drop_oldest(queue: "asyncio.Queue[Any]", item: Any) -> None:
    """Put `item`, evicting the single oldest queued item first if full.
    Each call handles exactly one insertion - callers that need to insert
    more than one item (an overflow marker plus the event that triggered
    it) call this once per item, so the queue's maxsize is never
    momentarily exceeded by inserting two items for one eviction."""
    try:
        queue.put_nowait(item)
        return
    except asyncio.QueueFull:
        pass
    try:
        queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        pass  # another producer refilled it between get and put - give up silently


def _deliver(subscriber: _Subscriber, event: Event) -> None:
    """Runs on the bound loop (via call_soon_threadsafe - never concurrent
    with itself or with the subscriber's own `queue.get()`, since asyncio
    only ever runs one callback/coroutine step at a time on a given loop,
    so checking `queue.full()` then acting on it here is race-free).
    Bounded queue, drop-oldest on overflow so the queue can never make
    publish() block, and so the most recent (most important - often the
    terminal) event is the one that survives, not the one discarded."""
    queue = subscriber.queue
    if queue.full() and not subscriber.dropped:
        subscriber.dropped = True
        _put_drop_oldest(
            queue, Event(type="overflow", thread_id=event.thread_id, seq=event.seq, payload={})
        )
    _put_drop_oldest(queue, event)


def close_channel(thread_id: str) -> None:
    """Remove thread_id's channel, waking every attached subscriber first.

    The only code path that removes a registry entry. Called exactly once
    per run, from the terminal branch of GraphService's post-invoke publish
    - never from an interrupt (see module docstring) and never from a
    client disconnect (that only detaches one subscriber, in subscribe()'s
    finally, not the channel itself - another tab, or a reconnect, must
    still work).
    """
    with _lock:
        channel = _channels.pop(thread_id, None)
        if channel is None:
            return
        channel.closed = True
        subscribers = list(channel.subscribers)

    loop = _loop
    for subscriber in subscribers:
        if loop is not None:
            loop.call_soon_threadsafe(_put_done, subscriber.queue)
        else:
            _put_done(subscriber.queue)


def _put_done(queue: "asyncio.Queue[Any]") -> None:
    try:
        queue.put_nowait(_DONE)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(_DONE)
        except asyncio.QueueFull:
            pass


def sweep_stale(max_age_seconds: float = CHANNEL_TTL_SECONDS) -> int:
    """Remove channels whose last_activity exceeds max_age_seconds.

    Called opportunistically at the top of POST /invoices/reserve - same
    pattern as app/uploads.py's sweep_old_uploads at the top of POST
    /invoices, no scheduler, no background thread. Covers the two genuine
    leak paths: reserved-but-never-uploaded, and interrupted-but-never-
    resumed (both leave a channel with no terminal event ever published).
    """
    with _lock:
        return _sweep_stale_locked(max_age_seconds)


def _sweep_stale_locked(max_age_seconds: float) -> int:
    cutoff = time.time() - max_age_seconds
    stale = [tid for tid, ch in _channels.items() if ch.last_activity < cutoff]
    for tid in stale:
        del _channels[tid]
    return len(stale)


def reset() -> None:
    """Test-only: clear the registry and unbind the loop between tests."""
    with _lock:
        _channels.clear()
    global _loop
    _loop = None


async def subscribe(thread_id: str, *, last_event_id: Optional[int] = None) -> AsyncIterator[Optional[Event]]:
    """Attach to thread_id's channel and yield events as they arrive.

    Yields the channel's history (filtered to `seq > last_event_id` when
    given, for an EventSource reconnect), then live events, then returns
    when the channel is closed. Yields `None` on each idle heartbeat
    timeout (not a sleep between real events - `wait_for`'s timeout is a
    read timeout on an otherwise-blocking get(); a real event is still
    forwarded the instant it arrives). Raises ChannelClosed if no channel
    is open for thread_id - the caller (the SSE route) treats that as
    "fall back to a one-shot snapshot instead."
    """
    with _lock:
        channel = _channels.get(thread_id)
        if channel is None:
            raise ChannelClosed(thread_id)
        queue: "asyncio.Queue[Any]" = asyncio.Queue(maxsize=MAX_SUBSCRIBER_QUEUE)
        subscriber = _Subscriber(queue=queue)
        channel.subscribers.append(subscriber)
        backlog = [e for e in channel.history if last_event_id is None or e.seq > last_event_id]

    try:
        for event in backlog:
            yield event
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield None
                continue
            if item is _DONE:
                return
            yield item
    finally:
        with _lock:
            try:
                channel.subscribers.remove(subscriber)
            except ValueError:
                pass
