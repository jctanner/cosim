"""GitLab mock — simplified git repository hosting for agents."""

import hashlib
import json
from pathlib import Path


GITLAB_DIR = Path(__file__).parent.parent / "gitlab"


def init_gitlab_storage():
    """Create the gitlab directory and an empty repos index if needed."""
    GITLAB_DIR.mkdir(parents=True, exist_ok=True)
    index_path = GITLAB_DIR / "_repos_index.json"
    if not index_path.exists():
        index_path.write_text("{}")


def load_repos_index() -> dict:
    """Load the repos index from disk."""
    index_path = GITLAB_DIR / "_repos_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_repos_index(index: dict):
    """Save the repos index to disk."""
    index_path = GITLAB_DIR / "_repos_index.json"
    index_path.write_text(json.dumps(index, indent=2))


def generate_commit_id(message: str, author: str, timestamp: float) -> str:
    """Generate a short hex commit ID from message, author, and timestamp."""
    raw = f"{message}:{author}:{timestamp}"
    return hashlib.sha1(raw.encode()).hexdigest()[:8]
