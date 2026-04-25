"""GitLab mock — simplified git repository hosting for agents."""

import hashlib
import json
from pathlib import Path

GITLAB_DIR = Path(__file__).parent.parent / "var" / "gitlab"

# Repo access: repo_name -> set of persona keys allowed (empty = all have access)
DEFAULT_REPO_ACCESS: dict[str, set[str]] = {}


def get_accessible_repos(persona_key: str, all_repos: list[dict]) -> list[dict]:
    """Filter repos to only those this persona can access.

    If DEFAULT_REPO_ACCESS is empty or a repo has no entry, all personas can access it.
    """
    if not DEFAULT_REPO_ACCESS:
        return all_repos
    return [
        r
        for r in all_repos
        if r.get("name", "") not in DEFAULT_REPO_ACCESS
        or persona_key in DEFAULT_REPO_ACCESS.get(r.get("name", ""), set())
    ]


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


# --- Merge Request helpers ---

def load_merge_requests(project: str) -> list[dict]:
    """Load merge requests for a project from disk."""
    mr_path = GITLAB_DIR / project / "_merge_requests.json"
    if not mr_path.exists():
        return []
    try:
        return json.loads(mr_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def save_merge_requests(project: str, mrs: list[dict]):
    """Save merge requests for a project to disk."""
    mr_path = GITLAB_DIR / project / "_merge_requests.json"
    mr_path.write_text(json.dumps(mrs, indent=2))


def next_mr_id(mrs: list[dict]) -> str:
    """Generate the next MR ID for a project (e.g. !1, !2, !3)."""
    if not mrs:
        return "!1"
    max_num = max(int(mr["id"].lstrip("!")) for mr in mrs)
    return f"!{max_num + 1}"
