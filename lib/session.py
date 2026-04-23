"""Session management — new, save, load simulation instances."""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
VAR_DIR = BASE_DIR / "var"
INSTANCES_DIR = VAR_DIR / "instances"
CHAT_LOG = VAR_DIR / "chat.log"
DOCS_DIR = VAR_DIR / "docs"
GITLAB_DIR = VAR_DIR / "gitlab"
TICKETS_DIR = VAR_DIR / "tickets"
LOGS_DIR = VAR_DIR / "logs"

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
    """Generate an instance directory name.

    If name is "autosave", use a fixed directory (no timestamp) so
    repeated auto-saves overwrite the same slot.
    """
    if name == "autosave":
        return f"{scenario}--autosave"
    date_str = datetime.now().strftime("%Y-%m-%d-%H%M")
    if name:
        slug = name.lower().replace(" ", "-")
        return f"{scenario}--{date_str}--{slug}"
    return f"{scenario}--{date_str}"


def _clear_runtime_dirs() -> None:
    """Remove runtime data directories and chat log."""
    if CHAT_LOG.exists():
        CHAT_LOG.unlink()
    for d in [DOCS_DIR, GITLAB_DIR, TICKETS_DIR, LOGS_DIR, VAR_DIR / "characters"]:
        if d.exists():
            shutil.rmtree(d)


def _get_channel_memberships() -> dict[str, list[str]]:
    """Get current channel memberships from webapp (import at call time to avoid circular)."""
    from lib.webapp import _channel_lock, _channel_members

    with _channel_lock:
        return {ch: sorted(members) for ch, members in _channel_members.items()}


def _get_agent_thoughts() -> dict[str, dict]:
    """Get current agent thoughts from webapp."""
    from lib.webapp import _agent_thoughts, _agent_thoughts_lock

    with _agent_thoughts_lock:
        return dict(_agent_thoughts)


def _get_roster() -> dict:
    """Get current roster state — all personas with their config."""
    from lib.docs import DEFAULT_FOLDER_ACCESS
    from lib.gitlab import DEFAULT_REPO_ACCESS
    from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS
    from lib.webapp import _agent_online_lock, _agent_verbosity

    roster = {}
    for key, p in PERSONAS.items():
        folders = [f for f, members in DEFAULT_FOLDER_ACCESS.items() if key in members]
        repos = [r for r, members in DEFAULT_REPO_ACCESS.items() if key in members] if DEFAULT_REPO_ACCESS else []
        with _agent_online_lock:
            verbosity = _agent_verbosity.get(key, "normal")
        roster[key] = {
            "display_name": p["display_name"],
            "team_description": p.get("team_description", ""),
            "character_file": p.get("character_file", ""),
            "channels": sorted(DEFAULT_MEMBERSHIPS.get(key, set())),
            "folders": sorted(folders),
            "repos": sorted(repos),
            "tier": PERSONA_TIER.get(key, 1),
            "verbosity": verbosity,
        }
    return roster


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

    for dirname in ["docs", "gitlab", "tickets", "logs", "characters"]:
        src = VAR_DIR / dirname
        if src.exists():
            shutil.copytree(src, instance_dir / dirname)

    # Save channel memberships
    memberships = _get_channel_memberships()
    (instance_dir / "memberships.json").write_text(json.dumps(memberships, indent=2))

    # Save agent thoughts
    thoughts = _get_agent_thoughts()
    if thoughts:
        (instance_dir / "thoughts.json").write_text(json.dumps(thoughts, indent=2))

    # Save roster (current PERSONAS state for hire/fire persistence)
    roster = _get_roster()
    (instance_dir / "roster.json").write_text(json.dumps(roster, indent=2))

    # Save DM queue
    try:
        from lib.container_orchestrator import get_dm_queue

        dm_data = get_dm_queue()
        if dm_data:
            (instance_dir / "dm_queue.json").write_text(json.dumps(dm_data, indent=2))
    except Exception:
        pass

    # Save events (pool + log)
    try:
        from lib.events import get_log_snapshot, get_pool_snapshot

        pool = get_pool_snapshot()
        log = get_log_snapshot()
        if pool:
            (instance_dir / "event_pool.json").write_text(json.dumps(pool, indent=2))
        if log:
            (instance_dir / "event_log.json").write_text(json.dumps(log, indent=2))
    except Exception:
        pass

    # Save background tasks
    try:
        from lib.task_executor import get_executor

        executor = get_executor()
        if executor:
            task_data = executor.get_all_tasks()
            if task_data:
                (instance_dir / "background_tasks.json").write_text(json.dumps(task_data, indent=2))
    except Exception:
        pass

    # Save emails
    try:
        from lib.email import get_inbox_snapshot

        emails = get_inbox_snapshot()
        if emails:
            (instance_dir / "emails.json").write_text(json.dumps(emails, indent=2))
    except Exception:
        pass

    # Save memos
    try:
        from lib.memos import get_memo_snapshot

        memo_data = get_memo_snapshot()
        if memo_data.get("threads") or memo_data.get("posts"):
            (instance_dir / "memos.json").write_text(json.dumps(memo_data, indent=2))
    except Exception:
        pass

    # Save blog
    try:
        from lib.blog import get_blog_snapshot

        blog_data = get_blog_snapshot()
        if blog_data.get("posts") or blog_data.get("replies"):
            (instance_dir / "blog.json").write_text(json.dumps(blog_data, indent=2))
    except Exception:
        pass

    # Save recaps
    try:
        from lib.webapp import _recaps

        if _recaps:
            (instance_dir / "recaps.json").write_text(json.dumps(_recaps, indent=2))
    except Exception:
        pass

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

    for dirname in ["docs", "gitlab", "tickets", "logs", "characters"]:
        src = instance_dir / dirname
        if src.exists():
            shutil.copytree(src, VAR_DIR / dirname)

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

    # Restore roster (hire/fire changes)
    roster_path = instance_dir / "roster.json"
    if roster_path.exists():
        try:
            from lib.docs import DEFAULT_FOLDER_ACCESS
            from lib.gitlab import DEFAULT_REPO_ACCESS
            from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS, RESPONSE_TIERS

            roster = json.loads(roster_path.read_text())

            # Rebuild PERSONAS from roster
            PERSONAS.clear()
            DEFAULT_MEMBERSHIPS.clear()
            PERSONA_TIER.clear()
            RESPONSE_TIERS.clear()

            for key, data in roster.items():
                PERSONAS[key] = {
                    "name": key,
                    "display_name": data["display_name"],
                    "team_description": data.get("team_description", ""),
                    "character_file": data.get("character_file", ""),
                }
                DEFAULT_MEMBERSHIPS[key] = set(data.get("channels", ["#general"]))
                tier = data.get("tier", 1)
                PERSONA_TIER[key] = tier
                RESPONSE_TIERS.setdefault(tier, [])
                if key not in RESPONSE_TIERS[tier]:
                    RESPONSE_TIERS[tier].append(key)

            # Restore verbosity
            from lib.webapp import _agent_online_lock, _agent_verbosity

            with _agent_online_lock:
                _agent_verbosity.clear()
                for key, data in roster.items():
                    if data.get("verbosity", "normal") != "normal":
                        _agent_verbosity[key] = data["verbosity"]

            # Rebuild folder access
            DEFAULT_FOLDER_ACCESS.clear()
            for key, data in roster.items():
                for folder in data.get("folders", []):
                    DEFAULT_FOLDER_ACCESS.setdefault(folder, set()).add(key)

            # Rebuild repo access
            DEFAULT_REPO_ACCESS.clear()
            for key, data in roster.items():
                for repo in data.get("repos", []):
                    DEFAULT_REPO_ACCESS.setdefault(repo, set()).add(key)

            print(f"  Roster restored: {len(roster)} agents")
        except Exception as e:
            print(f"  Roster restore failed: {e}")

    # Restore DM queue
    dm_path = instance_dir / "dm_queue.json"
    if dm_path.exists():
        try:
            from lib.container_orchestrator import set_dm_queue

            set_dm_queue(json.loads(dm_path.read_text()))
        except Exception:
            pass

    # Restore events (pool + log)
    try:
        from lib.events import init_event_pool, restore_events

        pool_path = instance_dir / "event_pool.json"
        log_path = instance_dir / "event_log.json"
        pool = json.loads(pool_path.read_text()) if pool_path.exists() else None
        log = json.loads(log_path.read_text()) if log_path.exists() else []
        if pool is not None:
            restore_events(pool, log)
        else:
            init_event_pool()  # fall back to scenario defaults
    except Exception:
        pass

    # Restore background tasks
    bg_path = instance_dir / "background_tasks.json"
    if bg_path.exists():
        try:
            from lib.task_executor import get_executor

            executor = get_executor()
            if executor:
                executor.restore_tasks(json.loads(bg_path.read_text()))
        except Exception:
            pass

    # Restore emails
    emails_path = instance_dir / "emails.json"
    if emails_path.exists():
        try:
            from lib.email import restore_inbox

            restore_inbox(json.loads(emails_path.read_text()))
        except Exception:
            pass

    # Restore memos
    memos_path = instance_dir / "memos.json"
    if memos_path.exists():
        try:
            from lib.memos import restore_memos

            memo_data = json.loads(memos_path.read_text())
            restore_memos(memo_data.get("threads", {}), memo_data.get("posts", []))
        except Exception:
            pass

    # Restore blog
    blog_path = instance_dir / "blog.json"
    if blog_path.exists():
        try:
            from lib.blog import restore_blog

            blog_data = json.loads(blog_path.read_text())
            restore_blog(blog_data.get("posts", {}), blog_data.get("replies", []))
        except Exception:
            pass

    # Restore recaps
    recaps_path = instance_dir / "recaps.json"
    if recaps_path.exists():
        try:
            from lib.webapp import _recaps

            data = json.loads(recaps_path.read_text())
            _recaps.clear()
            _recaps.extend(data)
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

    # Always reload scenario config on new session (ensures clean state)
    from lib.scenario_loader import load_scenario

    load_scenario(scenario)

    # Clear all runtime files
    _clear_runtime_dirs()

    # Clear all in-memory state
    try:
        from lib.events import clear_events, init_event_pool

        clear_events()
        init_event_pool()
    except Exception:
        pass
    try:
        from lib.email import clear_inbox

        clear_inbox()
    except Exception:
        pass
    try:
        from lib.memos import clear_memos

        clear_memos()
    except Exception:
        pass
    try:
        from lib.blog import clear_blog

        clear_blog()
    except Exception:
        pass
    try:
        from lib.webapp import _agent_online_lock, _agent_thoughts, _recaps

        _recaps.clear()
        with _agent_online_lock:
            _agent_thoughts.clear()
    except Exception:
        pass

    # Copy scenario seed data (docs, gitlab, tickets) if present
    from lib.scenario_loader import SCENARIOS_DIR

    scenario_dir = SCENARIOS_DIR / scenario
    for dirname in ["docs", "gitlab", "tickets"]:
        seed_dir = scenario_dir / dirname
        if seed_dir.exists():
            dst = VAR_DIR / dirname
            if not dst.exists():
                shutil.copytree(seed_dir, dst)
            else:
                # Merge seed files into existing dir
                for item in seed_dir.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(seed_dir)
                        target = dst / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if not target.exists():
                            shutil.copy2(item, target)
            print(f"  Seeded {dirname}/ from scenario")

    _current_session["scenario"] = scenario
    _current_session["instance_name"] = None
    _current_session["created_at"] = time.time()

    print(f"New session started: scenario={scenario}")
    return get_current_session()


def delete_session(instance_name: str) -> None:
    """Delete a saved session instance directory.

    Refuses to delete the currently loaded session.
    """
    current = _current_session.get("instance_name")
    if current and current == instance_name:
        raise RuntimeError("Cannot delete the currently loaded session")
    instance_dir = INSTANCES_DIR / instance_name
    if not instance_dir.exists():
        raise FileNotFoundError(f"Instance not found: {instance_name}")
    shutil.rmtree(instance_dir)
    print(f"Session deleted: {instance_dir}")


def rename_session(instance_name: str, new_name: str) -> dict:
    """Rename a saved session's display name (updates metadata.json).

    The instance directory on disk stays the same — only the
    display name stored in metadata changes.
    """
    instance_dir = INSTANCES_DIR / instance_name
    if not instance_dir.exists():
        raise FileNotFoundError(f"Instance not found: {instance_name}")
    meta_path = instance_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"No metadata.json in instance: {instance_name}")
    meta = json.loads(meta_path.read_text())
    meta["name"] = new_name
    meta_path.write_text(json.dumps(meta, indent=2))
    meta["instance_dir"] = instance_name
    print(f"Session renamed: {instance_name} -> {new_name}")
    return meta


def get_memberships_from_instance(instance_name: str) -> dict[str, list[str]] | None:
    """Load memberships.json from a saved instance, if it exists."""
    path = INSTANCES_DIR / instance_name / "memberships.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None
