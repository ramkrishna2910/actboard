"""Thread-safe event bus for pipeline visualization. No-op when disabled."""

import queue
import time

_event_queue: queue.Queue = queue.Queue()
_enabled: bool = False
_history: list = []


def enable():
    global _enabled
    _enabled = True


def is_enabled() -> bool:
    return _enabled


def emit(event_type: str, node: str, **kwargs):
    if not _enabled:
        return
    event = {"type": event_type, "node": node, "ts": time.time(), **kwargs}
    _event_queue.put(event)
    _history.append(event)


def drain() -> list[dict]:
    events = []
    while True:
        try:
            events.append(_event_queue.get_nowait())
        except queue.Empty:
            break
    return events


def get_history() -> list[dict]:
    return list(_history)
