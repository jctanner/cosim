"""Jobs — system-owned execution ledger for agent-submitted scripts."""

import hashlib
import json
from pathlib import Path

JOBS_DIR = Path(__file__).parent.parent / "var" / "jobs"


def init_jobs_storage():
    """Create the jobs directory and an empty runs index if needed."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = JOBS_DIR / "_runs_index.json"
    if not index_path.exists():
        index_path.write_text("{}")


def load_runs_index() -> dict:
    """Load the runs index from disk."""
    index_path = JOBS_DIR / "_runs_index.json"
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_runs_index(index: dict):
    """Save the runs index to disk."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = JOBS_DIR / "_runs_index.json"
    index_path.write_text(json.dumps(index, indent=2))


def generate_run_id(repo: str, path: str, timestamp: float) -> str:
    """Generate a run ID like RUN-A1B2C3 from repo, path, and timestamp."""
    raw = f"{repo}:{path}:{timestamp}"
    hex_hash = hashlib.sha1(raw.encode()).hexdigest()[:6].upper()
    return f"RUN-{hex_hash}"


def get_runs_snapshot(runs: dict) -> list[dict]:
    """Return all run records for session persistence."""
    return list(runs.values())


def restore_runs(data: list[dict]) -> dict:
    """Load run records from a saved session. Mark queued/running as abandoned."""
    runs = {}
    for record in data:
        if record.get("status") in ("queued", "running"):
            record["status"] = "abandoned"
        run_id = record.get("run_id")
        if run_id:
            runs[run_id] = record
    return runs
