"""Session management — new, save, load simulation instances."""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).parent.parent
INSTANCES_DIR = BASE_DIR / "instances"
CHAT_LOG = BASE_DIR / "chat.log"
DOCS_DIR = BASE_DIR / "docs"
GITLAB_DIR = BASE_DIR / "gitlab"
TICKETS_DIR = BASE_DIR / "tickets"

# Current session state
_current_session: dict = {
    "scenario": None,
    "instance_name": None,
    "created_at": None,
}


def set_scenario(scenario_name: str) -> None:
    """Set the current scenario name (called at startup)."""
    _current_session["scenario"] = scenario_name
    _current_session["created_at"] = time.time()


def get_current_session() -> dict:
    """Return current session metadata."""
    return dict(_current_session)


def list_sessions() -> list[dict]:
    """List all saved instances with their metadata."""
    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    sessions = []
    for d in sorted(INSTANCES_DIR.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                meta["instance_dir"] = d.name
                sessions.append(meta)
            except (json.JSONDecodeError, OSError):
                pass
    return sessions


def _generate_instance_name(scenario: str, name: str | None) -> str:
    """Generate an instance directory name."""
    date_str = datetime.now().strftime("%Y-%m-%d-%H%M")
    if name:
        slug = name.lower().replace(" ", "-")
        return f"{scenario}--{date_str}--{slug}"
    return f"{scenario}--{date_str}"


def _clear_runtime_dirs() -> None:
    """Remove runtime data directories and chat log."""
    if CHAT_LOG.exists():
        CHAT_LOG.unlink()
    for d in [DOCS_DIR, GITLAB_DIR, TICKETS_DIR]:
        if d.exists():
            shutil.rmtree(d)


def _get_channel_memberships() -> dict[str, list[str]]:
    """Get current channel memberships from webapp (import at call time to avoid circular)."""
    from lib.webapp import _channel_members, _channel_lock
    with _channel_lock:
        return {ch: sorted(members) for ch, members in _channel_members.items()}


def _get_agent_thoughts() -> dict[str, dict]:
    """Get current agent thoughts from webapp."""
    from lib.webapp import _agent_thoughts, _agent_thoughts_lock
    with _agent_thoughts_lock:
        return dict(_agent_thoughts)


def save_session(name: str | None = None) -> dict:
    """Save current state to an instance directory. Returns metadata."""
    scenario = _current_session["scenario"]
    if not scenario:
        raise RuntimeError("No scenario loaded")

    INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
    instance_name = _generate_instance_name(scenario, name)
    instance_dir = INSTANCES_DIR / instance_name

    if instance_dir.exists():
        shutil.rmtree(instance_dir)
    instance_dir.mkdir(parents=True)

    # Copy runtime state
    if CHAT_LOG.exists():
        shutil.copy2(CHAT_LOG, instance_dir / "chat.log")

    for dirname in ["docs", "gitlab", "tickets"]:
        src = BASE_DIR / dirname
        if src.exists():
            shutil.copytree(src, instance_dir / dirname)

    # Save channel memberships
    memberships = _get_channel_memberships()
    (instance_dir / "memberships.json").write_text(json.dumps(memberships, indent=2))

    # Save agent thoughts
    thoughts = _get_agent_thoughts()
    if thoughts:
        (instance_dir / "thoughts.json").write_text(json.dumps(thoughts, indent=2))

    # Write metadata
    now = time.time()
    meta = {
        "name": name or instance_name,
        "scenario": scenario,
        "created_at": _current_session.get("created_at", now),
        "saved_at": now,
        "description": "",
    }
    (instance_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    _current_session["instance_name"] = instance_name
    print(f"Session saved: {instance_dir}")
    return {**meta, "instance_dir": instance_name}


def load_session(instance_name: str) -> dict:
    """Load an instance, replacing current runtime state. Returns metadata."""
    instance_dir = INSTANCES_DIR / instance_name
    if not instance_dir.exists():
        raise FileNotFoundError(f"Instance not found: {instance_dir}")

    meta_path = instance_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"No metadata.json in instance: {instance_dir}")

    meta = json.loads(meta_path.read_text())

    # Clear current runtime
    _clear_runtime_dirs()

    # Restore files
    chat_src = instance_dir / "chat.log"
    if chat_src.exists():
        shutil.copy2(chat_src, CHAT_LOG)

    for dirname in ["docs", "gitlab", "tickets"]:
        src = instance_dir / dirname
        if src.exists():
            shutil.copytree(src, BASE_DIR / dirname)

    # Restore agent thoughts
    thoughts_path = instance_dir / "thoughts.json"
    if thoughts_path.exists():
        try:
            from lib.webapp import _agent_thoughts, _agent_thoughts_lock
            thoughts = json.loads(thoughts_path.read_text())
            with _agent_thoughts_lock:
                _agent_thoughts.clear()
                _agent_thoughts.update(thoughts)
        except Exception:
            pass

    # Update current session tracking
    _current_session["scenario"] = meta.get("scenario", _current_session["scenario"])
    _current_session["instance_name"] = instance_name
    _current_session["created_at"] = meta.get("created_at", time.time())

    print(f"Session loaded: {instance_dir}")
    return {**meta, "instance_dir": instance_name}


def new_session(scenario_name: str | None = None) -> dict:
    """Clear all runtime state for a fresh start. Returns metadata."""
    scenario = scenario_name or _current_session.get("scenario")
    if not scenario:
        raise RuntimeError("No scenario specified")

    # If switching scenarios, reload the scenario config
    if scenario != _current_session.get("scenario"):
        from lib.scenario_loader import load_scenario
        load_scenario(scenario)

    _clear_runtime_dirs()

    _current_session["scenario"] = scenario
    _current_session["instance_name"] = None
    _current_session["created_at"] = time.time()

    print(f"New session started: scenario={scenario}")
    return get_current_session()


def get_memberships_from_instance(instance_name: str) -> dict[str, list[str]] | None:
    """Load memberships.json from a saved instance, if it exists."""
    path = INSTANCES_DIR / instance_name / "memberships.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None
