"""Tickets — lightweight task tracking for agents."""

import hashlib
import json
from pathlib import Path

TICKETS_DIR = Path(__file__).parent.parent / "var" / "tickets"


def init_tickets_storage():
    """Create the tickets directory and an empty index if needed."""
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = TICKETS_DIR / "_tickets_index.json"
    if not index_path.exists():
        index_path.write_text("{}")


def load_tickets_index() -> dict:
    """Load the tickets index from disk."""
    index_path = TICKETS_DIR / "_tickets_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_tickets_index(index: dict):
    """Save the tickets index to disk."""
    index_path = TICKETS_DIR / "_tickets_index.json"
    index_path.write_text(json.dumps(index, indent=2))


def generate_ticket_id(title: str, timestamp: float) -> str:
    """Generate a ticket ID like TK-A1B2C3 from title and timestamp."""
    raw = f"{title}:{timestamp}"
    hex_hash = hashlib.sha1(raw.encode()).hexdigest()[:6].upper()
    return f"TK-{hex_hash}"
