"""Corporate email — company-wide announcements visible to all agents."""

import threading
import time

_inbox: list[dict] = []
_inbox_lock = threading.Lock()


def send_email(sender: str, subject: str, body: str) -> dict:
    """Send a company-wide email. Returns the email entry."""
    entry = {
        "id": len(_inbox) + 1,
        "sender": sender,
        "subject": subject,
        "body": body,
        "timestamp": time.time(),
        "read_by": [],
    }
    with _inbox_lock:
        _inbox.append(entry)
    return entry


def get_inbox() -> list[dict]:
    """Return all emails."""
    with _inbox_lock:
        return list(_inbox)


def get_email(email_id: int) -> dict | None:
    """Return a single email by ID."""
    with _inbox_lock:
        for e in _inbox:
            if e["id"] == email_id:
                return dict(e)
    return None


def clear_inbox():
    """Clear all emails."""
    with _inbox_lock:
        _inbox.clear()


def get_inbox_snapshot() -> list[dict]:
    """Return a copy for session save."""
    with _inbox_lock:
        return list(_inbox)


def restore_inbox(data: list[dict]):
    """Restore inbox from session data."""
    with _inbox_lock:
        _inbox.clear()
        _inbox.extend(data)
