"""Initialization, broadcast, and persistence helpers for the webapp."""

import json
import time

from lib.docs import slugify, DEFAULT_FOLDERS, DEFAULT_FOLDER_ACCESS
from lib.personas import DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, PERSONAS
from lib.gitlab import GITLAB_DIR, init_gitlab_storage, load_repos_index, save_repos_index
from lib.tickets import init_tickets_storage, load_tickets_index

from lib.webapp.state import (
    CHAT_LOG, DOCS_DIR, LOGS_DIR,
    _RESULT_MSG_RE, _RESULT_JSON_RE,
    _INPUT_TOKENS_RE, _OUTPUT_TOKENS_RE, _CACHE_CREATE_RE, _CACHE_READ_RE,
    _messages, _lock, _subscribers, _sub_lock,
    _channels, _channel_members, _channel_lock,
    _docs_index, _docs_lock,
    _folders, _folder_access, _folder_lock,
    _gitlab_repos, _gitlab_commits, _gitlab_lock,
    _tickets, _tickets_lock,
    _recaps,
    _agent_online, _agent_last_activity, _agent_online_lock,
    _agent_thoughts, _agent_thoughts_lock,
    _agent_verbosity,
)


def _parse_usage_from_logs() -> dict:
    """Parse token usage and cost data from agent log files in var/logs/.

    Returns dict with 'totals' (session-wide) and 'agents' (per-agent list).
    """
    agents: dict[str, dict] = {}

    if not LOGS_DIR.is_dir():
        return {"totals": {"input_tokens": 0, "output_tokens": 0,
                           "total_cost_usd": 0.0, "api_calls": 0},
                "agents": []}

    for log_path in sorted(LOGS_DIR.glob("*.log")):
        # Derive agent name from log filename (reverse of agent_runner's naming)
        agent_name = log_path.stem.replace("_", " ")

        agent_data = {
            "name": agent_name,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost_usd": 0.0,
            "api_calls": 0,
        }

        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _RESULT_MSG_RE.search(line) or _RESULT_JSON_RE.search(line)
                    if not m:
                        continue
                    agent_data["api_calls"] += 1

                    # Parse cost
                    cost_str = m.group("cost")
                    if cost_str and cost_str not in ("None", "null"):
                        try:
                            agent_data["total_cost_usd"] += float(cost_str)
                        except ValueError:
                            pass

                    # Extract token counts directly from line text.
                    # Input = input_tokens + cache_creation + cache_read
                    for pat in (_INPUT_TOKENS_RE, _CACHE_CREATE_RE, _CACHE_READ_RE):
                        tok_m = pat.search(line)
                        if tok_m:
                            agent_data["input_tokens"] += int(tok_m.group(1))

                    tok_m = _OUTPUT_TOKENS_RE.search(line)
                    if tok_m:
                        agent_data["output_tokens"] += int(tok_m.group(1))
        except OSError:
            continue

        if agent_data["api_calls"] > 0:
            agents[agent_name] = agent_data

    # Compute session totals
    totals = {
        "input_tokens": sum(a["input_tokens"] for a in agents.values()),
        "output_tokens": sum(a["output_tokens"] for a in agents.values()),
        "total_cost_usd": round(sum(a["total_cost_usd"] for a in agents.values()), 6),
        "api_calls": sum(a["api_calls"] for a in agents.values()),
    }

    # Round per-agent costs
    for a in agents.values():
        a["total_cost_usd"] = round(a["total_cost_usd"], 6)

    return {
        "totals": totals,
        "agents": sorted(agents.values(), key=lambda a: a["total_cost_usd"], reverse=True),
    }


def _init_channels():
    """Initialize channels and memberships from persona defaults."""
    with _channel_lock:
        _channels.clear()
        _channel_members.clear()
        now = time.time()
        for ch_name, ch_info in DEFAULT_CHANNELS.items():
            _channels[ch_name] = {
                "description": ch_info["description"],
                "is_external": ch_info["is_external"],
                "created_at": now,
            }
            _channel_members[ch_name] = set()

        # Always create #system channel for operator messages (no agent membership)
        _channels["#system"] = {
            "description": "System events and operator messages",
            "is_external": False,
            "is_system": True,
            "created_at": now,
        }
        _channel_members["#system"] = set()

        # Create #dms channel for agent-to-agent DM visibility
        _channels["#dms"] = {
            "description": "Agent direct messages (operator visibility)",
            "is_external": False,
            "is_system": True,
            "created_at": now,
        }
        _channel_members["#dms"] = set()

        # Create #announcements channel for company-wide emails
        _channels["#announcements"] = {
            "description": "Company-wide announcements and emails",
            "is_external": False,
            "is_system": False,  # agents should see this channel
            "created_at": now,
        }
        # All agents are members of #announcements
        _channel_members["#announcements"] = set(PERSONAS.keys())

        # Create director channels for each persona
        for pk, p_info in PERSONAS.items():
            ch_name = f"#director-{pk}"
            display = p_info.get("display_name", pk)
            _channels[ch_name] = {
                "description": f"Private channel with {display}",
                "is_external": False,
                "is_director": True,
                "director_persona": pk,
                "created_at": now,
            }
            _channel_members[ch_name] = {pk}

        for persona_key, ch_set in DEFAULT_MEMBERSHIPS.items():
            # Auto-add all agents to #announcements
            ch_set.add("#announcements")
            for ch_name in ch_set:
                if ch_name in _channel_members:
                    _channel_members[ch_name].add(persona_key)


def _persist_message(msg: dict):
    """Append a message to chat.log."""
    with open(CHAT_LOG, "a") as f:
        f.write(json.dumps(msg) + "\n")


def _broadcast(msg: dict):
    """Send message to all SSE subscribers."""
    import queue as _queue
    data = json.dumps(msg)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _broadcast_channel_update(channel_name: str, members: list[str]):
    """Notify SSE subscribers that a channel's membership changed."""
    import queue as _queue
    data = json.dumps({
        "type": "channel_update",
        "channel": channel_name,
        "members": members,
    })
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# -- Document storage helpers --

def _init_folders():
    """Initialize folder registry from defaults."""
    with _folder_lock:
        _folders.clear()
        _folder_access.clear()
        _folders.update(DEFAULT_FOLDERS)
        for folder_name, access_set in DEFAULT_FOLDER_ACCESS.items():
            _folder_access[folder_name] = set(access_set)


def _init_docs():
    """Create docs/ dir with folder subdirs and load or migrate index."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Create subdirectories for each folder
    for folder_name in DEFAULT_FOLDERS:
        (DOCS_DIR / folder_name).mkdir(exist_ok=True)

    index_path = DOCS_DIR / "_index.json"
    with _docs_lock:
        _docs_index.clear()

        # Migrate flat .txt files into shared/ folder
        flat_files = [f for f in DOCS_DIR.glob("*.txt") if f.is_file()]
        if flat_files:
            shared_dir = DOCS_DIR / "shared"
            shared_dir.mkdir(exist_ok=True)
            for txt in flat_files:
                dest = shared_dir / txt.name
                if not dest.exists():
                    txt.rename(dest)
                else:
                    txt.unlink()

        if index_path.exists():
            try:
                data = json.loads(index_path.read_text())
                _docs_index.update(data)
            except (json.JSONDecodeError, OSError):
                pass

            # Migrate existing entries: add "folder" field if missing
            for slug, meta in _docs_index.items():
                if "folder" not in meta:
                    meta["folder"] = "shared"
            _save_index()

        if not _docs_index:
            # Fallback: scan .txt files in folder subdirectories
            for folder_name in DEFAULT_FOLDERS:
                folder_dir = DOCS_DIR / folder_name
                if not folder_dir.is_dir():
                    continue
                for txt in folder_dir.glob("*.txt"):
                    slug = txt.stem
                    stat = txt.stat()
                    content = txt.read_text(encoding="utf-8", errors="replace")
                    _docs_index[slug] = {
                        "slug": slug,
                        "title": slug.replace("-", " ").title(),
                        "folder": folder_name,
                        "created_at": stat.st_ctime,
                        "updated_at": stat.st_mtime,
                        "created_by": "unknown",
                        "size": stat.st_size,
                        "preview": content[:100],
                    }


def _save_index():
    """Persist _docs_index to docs/_index.json.  Caller must hold _docs_lock."""
    index_path = DOCS_DIR / "_index.json"
    index_path.write_text(json.dumps(_docs_index, indent=2))


def _broadcast_doc_event(action: str, doc_meta: dict):
    """Send a doc_event through the existing SSE subscribers."""
    import queue as _queue
    payload = {"type": "doc_event", "action": action}
    payload.update(doc_meta)
    data = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _broadcast_gitlab_event(action: str, data: dict):
    """Send a gitlab_event through the existing SSE subscribers."""
    import queue as _queue
    payload = {"type": "gitlab_event", "action": action}
    payload.update(data)
    raw = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(raw)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _init_gitlab():
    """Initialize GitLab storage and load repos/commits from disk."""
    init_gitlab_storage()
    with _gitlab_lock:
        _gitlab_repos.clear()
        _gitlab_commits.clear()
        index = load_repos_index()
        _gitlab_repos.update(index)
        # Load commit logs for each repo
        for repo_name in index:
            commits_path = GITLAB_DIR / repo_name / "_commits.json"
            if commits_path.exists():
                try:
                    _gitlab_commits[repo_name] = json.loads(commits_path.read_text())
                except (json.JSONDecodeError, OSError):
                    _gitlab_commits[repo_name] = []
            else:
                _gitlab_commits[repo_name] = []


def _init_tickets():
    """Initialize tickets storage and load tickets from disk."""
    init_tickets_storage()
    with _tickets_lock:
        _tickets.clear()
        index = load_tickets_index()
        _tickets.update(index)


def _init_agent_online():
    """Initialize agent online/offline state from PERSONAS."""
    with _agent_online_lock:
        _agent_online.clear()
        _agent_last_activity.clear()
        for key in PERSONAS:
            _agent_online[key] = True


def _load_chat_log():
    """Load messages from chat.log into _messages."""
    with _lock:
        _messages.clear()
    if not CHAT_LOG.exists():
        return
    with open(CHAT_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                with _lock:
                    _messages.append(msg)
            except json.JSONDecodeError:
                pass


def _reinitialize():
    """Re-initialize all subsystems from disk state."""
    _init_channels()
    _init_folders()
    _init_docs()
    _init_gitlab()
    _init_tickets()
    _init_agent_online()
    _load_chat_log()
    # Clear emails and recaps (restored separately by session load if needed)
    from lib.email import clear_inbox
    clear_inbox()
    from lib.memos import clear_memos
    clear_memos()
    from lib.blog import clear_blog
    clear_blog()
    _recaps.clear()


def _restore_session_extras(instance_name: str):
    """Re-restore session data that gets cleared by _load_scenario / _reinitialize.

    load_session() restores memos, events, emails, blog, and recaps, but
    _load_scenario() resets the event pool and _reinitialize() clears
    memos, emails, blog, and recaps. This function re-applies the saved data.
    """
    import json as _json
    from lib.session import INSTANCES_DIR

    instance_dir = INSTANCES_DIR / instance_name

    # Memos
    memos_path = instance_dir / "memos.json"
    if memos_path.exists():
        try:
            from lib.memos import restore_memos
            memo_data = _json.loads(memos_path.read_text())
            restore_memos(memo_data.get("threads", {}), memo_data.get("posts", []))
        except Exception:
            pass

    # Events (pool + log)
    pool_path = instance_dir / "event_pool.json"
    log_path = instance_dir / "event_log.json"
    if pool_path.exists():
        try:
            from lib.events import restore_events
            pool = _json.loads(pool_path.read_text())
            log = _json.loads(log_path.read_text()) if log_path.exists() else []
            restore_events(pool, log)
        except Exception:
            pass

    # Emails
    emails_path = instance_dir / "emails.json"
    if emails_path.exists():
        try:
            from lib.email import restore_inbox
            restore_inbox(_json.loads(emails_path.read_text()))
        except Exception:
            pass

    # Blog
    blog_path = instance_dir / "blog.json"
    if blog_path.exists():
        try:
            from lib.blog import restore_blog
            blog_data = _json.loads(blog_path.read_text())
            restore_blog(blog_data.get("posts", {}), blog_data.get("replies", []))
        except Exception:
            pass

    # Recaps
    recaps_path = instance_dir / "recaps.json"
    if recaps_path.exists():
        try:
            data = _json.loads(recaps_path.read_text())
            _recaps.clear()
            _recaps.extend(data)
        except Exception:
            pass

    # Agent thoughts
    thoughts_path = instance_dir / "thoughts.json"
    if thoughts_path.exists():
        try:
            thoughts = _json.loads(thoughts_path.read_text())
            with _agent_thoughts_lock:
                _agent_thoughts.clear()
                _agent_thoughts.update(thoughts)
        except Exception:
            pass


def _broadcast_tickets_event(action: str, data: dict):
    """Send a tickets_event through the existing SSE subscribers."""
    import queue as _queue
    payload = {"type": "tickets_event", "action": action}
    payload.update(data)
    raw = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(raw)
            except _queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _extract_snippet(content: str, query: str, context_chars: int = 80) -> str:
    """Extract a short snippet around the first occurrence of query in content."""
    lower = content.lower()
    idx = lower.find(query.lower())
    if idx == -1:
        return content[:160]
    start = max(0, idx - context_chars)
    end = min(len(content), idx + len(query) + context_chars)
    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet
