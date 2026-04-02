"""Events — scenario event pool and event log for injecting chaos into simulations."""

import copy
import time
import threading


# Default event pool from scenario config (set by scenario_loader)
SCENARIO_EVENTS: list[dict] = []

# Runtime event pool (scenario defaults + user additions)
_event_pool: list[dict] = []
_event_log: list[dict] = []
_events_lock = threading.Lock()


def init_event_pool():
    """Initialize the runtime event pool from scenario defaults."""
    with _events_lock:
        _event_pool.clear()
        _event_pool.extend(copy.deepcopy(SCENARIO_EVENTS))
        _event_log.clear()


def get_event_pool() -> list[dict]:
    """Return the current event pool."""
    with _events_lock:
        return list(_event_pool)


def add_event(event: dict) -> int:
    """Add an event to the pool. Returns its index."""
    with _events_lock:
        _event_pool.append(event)
        return len(_event_pool) - 1


def update_event(index: int, event: dict):
    """Update an event in the pool by index."""
    with _events_lock:
        if 0 <= index < len(_event_pool):
            _event_pool[index] = event


def delete_event(index: int):
    """Remove an event from the pool by index."""
    with _events_lock:
        if 0 <= index < len(_event_pool):
            _event_pool.pop(index)


def fire_event(event: dict) -> dict:
    """Log a fired event. Returns the log entry."""
    entry = {
        "name": event.get("name", "Custom Event"),
        "severity": event.get("severity", "medium"),
        "actions": event.get("actions", []),
        "timestamp": time.time(),
    }
    with _events_lock:
        _event_log.append(entry)
    return entry


def get_event_log() -> list[dict]:
    """Return the event log."""
    with _events_lock:
        return list(_event_log)


def get_pool_snapshot() -> list[dict]:
    """Return a copy of the event pool for session save."""
    with _events_lock:
        return copy.deepcopy(_event_pool)


def get_log_snapshot() -> list[dict]:
    """Return a copy of the event log for session save."""
    with _events_lock:
        return list(_event_log)


def restore_events(pool: list[dict], log: list[dict]):
    """Restore event pool and log from session data."""
    with _events_lock:
        _event_pool.clear()
        _event_pool.extend(pool)
        _event_log.clear()
        _event_log.extend(log)


def clear_events():
    """Clear pool and log."""
    with _events_lock:
        _event_pool.clear()
        _event_log.clear()
