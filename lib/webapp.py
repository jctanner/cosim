"""Flask chat server with SSE broadcast and web UI."""

import json
import re
import time
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from lib.docs import slugify, DEFAULT_FOLDERS, DEFAULT_FOLDER_ACCESS
from lib.personas import DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, PERSONAS
from lib.gitlab import GITLAB_DIR, init_gitlab_storage, load_repos_index, save_repos_index, generate_commit_id
from lib.tickets import TICKETS_DIR, init_tickets_storage, load_tickets_index, save_tickets_index, generate_ticket_id
from lib.session import (
    save_session, load_session, new_session, list_sessions,
    get_current_session, set_scenario, get_memberships_from_instance,
    delete_session, rename_session,
)
from lib.scenario_loader import list_scenarios


CHAT_LOG = Path(__file__).parent.parent / "var" / "chat.log"
DOCS_DIR = Path(__file__).parent.parent / "var" / "docs"
LOGS_DIR = Path(__file__).parent.parent / "var" / "logs"

# Regexes to parse ResultMessage lines written by agent_runner.
# The usage dict contains nested sub-dicts, so we extract token counts
# directly from the line rather than trying to parse the full dict.
# SDK repr format: ResultMessage(...total_cost_usd=0.123, usage={'input_tokens': 10, ...})
_RESULT_MSG_RE = re.compile(r"ResultMessage\(.*?total_cost_usd=(?P<cost>[0-9eE.+-]+|None)")
# Claude CLI JSON format: {"type":"result","total_cost_usd":0.123,"usage":{"input_tokens":10,...}}
_RESULT_JSON_RE = re.compile(r'"type"\s*:\s*"result".*?"total_cost_usd"\s*:\s*(?P<cost>[0-9eE.+-]+|null)')
# Token patterns match both single-quoted (Python repr) and double-quoted (JSON) keys
_INPUT_TOKENS_RE = re.compile(r"""["']input_tokens["']\s*:\s*(\d+)""")
_OUTPUT_TOKENS_RE = re.compile(r"""["']output_tokens["']\s*:\s*(\d+)""")
_CACHE_CREATE_RE = re.compile(r"""["']cache_creation_input_tokens["']\s*:\s*(\d+)""")
_CACHE_READ_RE = re.compile(r"""["']cache_read_input_tokens["']\s*:\s*(\d+)""")


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


# In-memory state
_messages: list[dict] = []
_lock = threading.Lock()
_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()

# Channel registry: channel_name -> {description, is_external, created_at}
_channels: dict[str, dict] = {}
# Channel membership: channel_name -> set of persona keys
_channel_members: dict[str, set[str]] = {}
_channel_lock = threading.Lock()

# Document index: slug -> metadata dict
_docs_index: dict[str, dict] = {}
_docs_lock = threading.Lock()

# Folder registry: folder_name -> {type, description}
_folders: dict[str, dict] = {}
# Folder access: folder_name -> set of persona keys
_folder_access: dict[str, set[str]] = {}
_folder_lock = threading.Lock()

# GitLab state: repo_name -> metadata, repo_name -> commit list
_gitlab_repos: dict[str, dict] = {}
_gitlab_commits: dict[str, list[dict]] = {}
_gitlab_lock = threading.Lock()

# Tickets state: ticket_id -> full ticket dict
_tickets: dict[str, dict] = {}
_tickets_lock = threading.Lock()

# Recaps: list of generated recaps
_recaps: list[dict] = []

# Agent online/offline state: persona_key -> True (online) / False (offline)
_agent_online: dict[str, bool] = {}
_agent_firing: set[str] = set()  # agents being fired (waiting for session close)
_agent_verbosity: dict[str, str] = {}  # persona_key -> verbosity level
_agent_last_activity: dict[str, dict] = {}  # persona_key -> {timestamp, event_type, detail}
_agent_online_lock = threading.Lock()

# Agent thoughts: persona_key -> list of {thinking, response, timestamp}
_agent_thoughts: dict[str, list[dict]] = {}
_agent_thoughts_lock = threading.Lock()

# Orchestrator status (updated via heartbeat from orchestrator process)
_orchestrator_status: dict = {
    "state": "disconnected",  # disconnected, starting, ready, responding, restarting
    "scenario": None,
    "agents": {},             # persona_key -> {state, display_name}
    "last_heartbeat": 0,
    "message": "",
}
_orchestrator_lock = threading.Lock()

# Control signal queue for orchestrator (checked on each poll)
_orchestrator_commands: list[dict] = []
_command_lock = threading.Lock()


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
            _channel_members[ch_name] = set()

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
    data = json.dumps(msg)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _broadcast_channel_update(channel_name: str, members: list[str]):
    """Notify SSE subscribers that a channel's membership changed."""
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
            except queue.Full:
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
    payload = {"type": "doc_event", "action": action}
    payload.update(doc_meta)
    data = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def _broadcast_gitlab_event(action: str, data: dict):
    """Send a gitlab_event through the existing SSE subscribers."""
    payload = {"type": "gitlab_event", "action": action}
    payload.update(data)
    raw = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(raw)
            except queue.Full:
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
    import json
    from lib.session import INSTANCES_DIR

    instance_dir = INSTANCES_DIR / instance_name

    # Memos
    memos_path = instance_dir / "memos.json"
    if memos_path.exists():
        try:
            from lib.memos import restore_memos
            memo_data = json.loads(memos_path.read_text())
            restore_memos(memo_data.get("threads", {}), memo_data.get("posts", []))
        except Exception:
            pass

    # Events (pool + log)
    pool_path = instance_dir / "event_pool.json"
    log_path = instance_dir / "event_log.json"
    if pool_path.exists():
        try:
            from lib.events import restore_events
            pool = json.loads(pool_path.read_text())
            log = json.loads(log_path.read_text()) if log_path.exists() else []
            restore_events(pool, log)
        except Exception:
            pass

    # Emails
    emails_path = instance_dir / "emails.json"
    if emails_path.exists():
        try:
            from lib.email import restore_inbox
            restore_inbox(json.loads(emails_path.read_text()))
        except Exception:
            pass

    # Blog
    blog_path = instance_dir / "blog.json"
    if blog_path.exists():
        try:
            from lib.blog import restore_blog
            blog_data = json.loads(blog_path.read_text())
            restore_blog(blog_data.get("posts", {}), blog_data.get("replies", []))
        except Exception:
            pass

    # Recaps
    recaps_path = instance_dir / "recaps.json"
    if recaps_path.exists():
        try:
            data = json.loads(recaps_path.read_text())
            _recaps.clear()
            _recaps.extend(data)
        except Exception:
            pass

    # Agent thoughts
    thoughts_path = instance_dir / "thoughts.json"
    if thoughts_path.exists():
        try:
            thoughts = json.loads(thoughts_path.read_text())
            with _agent_thoughts_lock:
                _agent_thoughts.clear()
                _agent_thoughts.update(thoughts)
        except Exception:
            pass


def _broadcast_tickets_event(action: str, data: dict):
    """Send a tickets_event through the existing SSE subscribers."""
    payload = {"type": "tickets_event", "action": action}
    payload.update(data)
    raw = json.dumps(payload)
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(raw)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# -- Web UI HTML --

WEB_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CoSim</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/js-yaml@4/dist/js-yaml.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  /* -- Theme System -- */
  :root {
    --bg: #1a1a2e;
    --panel: #16213e;
    --sidebar: #121a30;
    --border: #0f3460;
    --border-mid: #1a1a2e;
    --border-dark: #333;
    --input-bg: #111;
    --accent: #e94560;
    --accent-dark: #c0392b;
    --text: #e0e0e0;
    --text-dim: #888;
    --text-dimmer: #555;
    --text-bright: #fff;
    --highlight: #4fc3f7;
  }
  [data-theme="stadium"] {
    --bg: #000000;
    --panel: #0d0d0d;
    --sidebar: #050505;
    --border: #2a2a2a;
    --border-mid: #1a1a1a;
    --border-dark: #1a1a1a;
    --input-bg: #111111;
    --accent: #00e5ff;
    --accent-dark: #00b8cc;
    --text: #ffffff;
    --text-dim: #aaaaaa;
    --text-dimmer: #666666;
    --text-bright: #ffffff;
    --highlight: #ffeb3b;
  }
  [data-theme="field"] {
    --bg: #0a1a0a;
    --panel: #0f200f;
    --sidebar: #081408;
    --border: #1e4d1e;
    --border-mid: #162816;
    --border-dark: #1e3a1e;
    --input-bg: #071207;
    --accent: #f5a623;
    --accent-dark: #d4891a;
    --text: #e8f0e8;
    --text-dim: #7a9e7a;
    --text-dimmer: #4a6a4a;
    --text-bright: #ffffff;
    --highlight: #7dff8a;
  }
  [data-theme="solarized-dark"] {
    --bg: #002b36;
    --panel: #073642;
    --sidebar: #002029;
    --border: #586e75;
    --border-mid: #073642;
    --border-dark: #2a4a52;
    --input-bg: #003847;
    --accent: #cb4b16;
    --accent-dark: #a83c11;
    --text: #839496;
    --text-dim: #657b83;
    --text-dimmer: #586e75;
    --text-bright: #93a1a1;
    --highlight: #2aa198;
  }
  [data-theme="solarized-light"] {
    --bg: #fdf6e3;
    --panel: #eee8d5;
    --sidebar: #f5efdc;
    --border: #93a1a1;
    --border-mid: #eee8d5;
    --border-dark: #d3cbb7;
    --input-bg: #fff8e7;
    --accent: #cb4b16;
    --accent-dark: #a83c11;
    --text: #657b83;
    --text-dim: #839496;
    --text-dimmer: #93a1a1;
    --text-bright: #073642;
    --highlight: #268bd2;
  }

  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }

  /* -- Header with tabs -- */
  #header { background: var(--panel); padding: 0 20px; border-bottom: 1px solid var(--border);
            display: flex; align-items: stretch; gap: 0; }
  #header h1 { font-size: 18px; color: var(--accent); display: flex; align-items: center; padding: 12px 16px 12px 0;
               border-right: 1px solid var(--border); margin-right: 0; }
  .header-tab { padding: 12px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
                background: transparent; border: none; color: var(--text-dim);
                border-bottom: 2px solid transparent; transition: all 0.15s ease; }
  .header-tab:hover { color: var(--text); }
  .header-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  #session-controls { margin-left: auto; display: flex; align-items: center; gap: 6px; padding: 8px 0; }
  .session-btn { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 12px;
                 border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 600; }
  .session-btn:hover { border-color: var(--accent); color: var(--accent); }
  #session-load-select { background: var(--bg); color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 8px;
                         border-radius: 6px; font-size: 12px; max-width: 200px; }
  #orch-status { display: flex; align-items: center; gap: 5px; margin-right: 8px;
                 padding: 4px 10px; border: 1px solid var(--border-dark); border-radius: 6px; }
  #orch-label { font-size: 11px; color: var(--text-dim); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .status-dot.disconnected { background: #666; }
  .status-dot.waiting { background: #f39c12; }
  .status-dot.connecting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .status-dot.starting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .status-dot.ready { background: #2ecc71; }
  .status-dot.responding { background: #3498db; animation: pulse 0.5s ease-in-out infinite; }
  .status-dot.restarting { background: #e94560; animation: pulse 0.8s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  /* -- NPCs tab -- */
  #npcs-pane { padding: 0; flex-direction: row; }
  #npcs-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  #npcs-main { flex: 1; overflow-y: auto; padding: 20px; }
  #npcs-content { max-width: 1000px; }
  #npcs-empty { color: var(--text-dimmer); text-align: center; padding: 40px; }
  .npc-tier-section { margin-bottom: 24px; }
  .npc-tier-header { font-size: 13px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: var(--text-dim); margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid var(--border-dark); }
  .npc-tier-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .npc-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
              padding: 14px 16px; flex: 1 1 160px; max-width: 220px; min-width: 160px;
              transition: border-color 0.15s; }
  .npc-card:hover { border-color: var(--text-dimmer); }
  .npc-card.offline { opacity: 0.6; }
  .npc-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .npc-status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .npc-status-dot.ready { background: #2ecc71; }
  .npc-status-dot.starting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .npc-status-dot.responding { background: #3498db; animation: pulse 0.5s ease-in-out infinite; }
  .npc-status-dot.writing-docs { background: #9b59b6; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.committing-code { background: #e67e22; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.managing-tickets { background: #1abc9c; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.processing-commands { background: #f39c12; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.firing { background: #e94560; animation: pulse 1.5s ease-in-out infinite; }
  .npc-status-dot.offline { background: #666; }
  .npc-status-dot.disconnected { background: #444; }
  .npc-status-dot.unknown { background: #444; }
  .npc-card-state { font-size: 10px; color: var(--text-dimmer); margin-left: auto; }
  .npc-card-name { font-size: 14px; font-weight: 700; color: var(--text); }
  .npc-card-desc { font-size: 11px; color: var(--text-dim); margin-bottom: 8px; line-height: 1.4; }
  .npc-card-section-label { font-size: 10px; font-weight: 600; text-transform: uppercase;
                           letter-spacing: 0.5px; color: var(--text-dimmer); margin-bottom: 3px; margin-top: 6px; }
  .npc-card-tags { margin-bottom: 4px; line-height: 1.8; }
  .npc-tag { background: var(--input-bg); color: var(--text-dim); padding: 1px 6px; border-radius: 4px; font-size: 11px;
             margin-right: 3px; display: inline-block; }
  .npc-tag-folder { border-left: 2px solid #3498db; }
  .npc-toggle-btn { width: 100%; background: transparent; border: 1px solid var(--border-dark);
                    color: var(--text-dim); padding: 5px; border-radius: 6px; font-size: 11px;
                    cursor: pointer; transition: all 0.15s; }
  .npc-toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .npc-toggle-btn.is-online:hover { border-color: #f39c12; color: #f39c12; }
  .npc-detail-tab { transition: all 0.15s; }
  .npc-detail-tab.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .npc-config-check { display: flex; align-items: center; gap: 4px; background: var(--bg);
                      padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-dark);
                      font-size: 12px; color: var(--text-dim); cursor: pointer; }
  .npc-config-check:hover { border-color: var(--text-dimmer); }
  .npc-config-check input { accent-color: #e94560; }
  .npc-config-check.checked { color: var(--text); border-color: var(--text-dimmer); }
  .thought-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--bg);
                  font-size: 11px; color: var(--text-dim); transition: background 0.1s; }
  .thought-item:hover { background: var(--border-mid); }
  .thought-item.active { background: var(--border-mid); color: var(--text); border-left: 3px solid var(--accent); }
  .thought-item-time { color: var(--text-dimmer); font-size: 10px; }
  .thought-item-preview { color: var(--text-dim); margin-top: 2px; overflow: hidden;
                          text-overflow: ellipsis; white-space: nowrap; }

  /* -- Usage tab -- */
  #usage-pane { padding: 0; flex-direction: row; }
  #usage-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .usage-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                           letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .usage-stat { padding: 4px 14px; font-size: 12px; color: var(--text-dim); }
  .usage-stat strong { color: var(--text); }
  #usage-main { flex: 1; overflow-y: auto; padding: 20px; }
  #usage-content { max-width: 1000px; }
  #usage-empty { color: var(--text-dimmer); text-align: center; padding: 40px; }
  .usage-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .usage-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
                padding: 14px 16px; flex: 1 1 200px; max-width: 280px; min-width: 200px;
                transition: border-color 0.15s; }
  .usage-card:hover { border-color: var(--text-dimmer); }
  .usage-card-name { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid var(--border-dark); }
  .usage-card-row { display: flex; justify-content: space-between; padding: 3px 0;
                    font-size: 12px; color: var(--text-dim); }
  .usage-card-row .label { color: var(--text-dimmer); }
  .usage-card-row .value { color: var(--text); font-weight: 600; font-family: monospace; }
  .usage-card-row .value.cost { color: #2ecc71; }

  /* -- Advanced tab -- */
  #advanced-pane { padding: 0; }

  /* -- Recap tab -- */
  #recap-pane { padding: 0; flex-direction: row; }
  #recap-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  #recap-main { flex: 1; overflow-y: auto; }
  .recap-item { padding: 8px 14px; cursor: pointer; border-bottom: 1px solid var(--bg);
                font-size: 12px; color: var(--text-dim); transition: background 0.1s; }
  .recap-item:hover { background: var(--border-mid); }
  .recap-item.active { background: var(--border-mid); color: var(--text); border-left: 3px solid var(--accent); }
  .recap-item-style { font-weight: 600; color: var(--highlight); }
  .recap-item-time { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }

  /* -- Email tab -- */
  #email-pane { padding: 0; flex-direction: row; }
  #email-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow: hidden; }
  #email-main { flex: 1; overflow-y: auto; padding: 20px; }
  .email-item { padding: 10px 12px; border-bottom: 1px solid var(--bg); cursor: pointer; transition: background 0.1s; }
  .email-item:hover { background: var(--border-mid); }
  .email-item.active { background: var(--border-mid); border-left: 3px solid #3498db; }
  .email-item-from { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .email-item-subject { font-size: 13px; color: var(--text); margin-top: 2px; overflow: hidden;
                        text-overflow: ellipsis; white-space: nowrap; }
  .email-item-date { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }

  /* -- Memos tab -- */
  #memos-pane { padding: 0; flex-direction: row; }
  #memos-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow: hidden; }
  #memos-main { flex: 1; overflow-y: auto; padding: 20px; }
  .memo-thread-item { padding: 10px 12px; border-bottom: 1px solid var(--bg); cursor: pointer; transition: background 0.1s; }
  .memo-thread-item:hover { background: var(--border-mid); }
  .memo-thread-item.active { background: var(--border-mid); border-left: 3px solid #2ecc71; }
  .memo-thread-title { font-size: 13px; font-weight: 700; color: var(--text); }
  .memo-thread-preview { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; overflow: hidden;
                         text-overflow: ellipsis; white-space: nowrap; }
  .memo-thread-meta { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }
  .memo-post { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 14px; margin-bottom: 10px; }
  .memo-post-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .memo-post-date { font-size: 10px; color: var(--text-dimmer); margin-left: 8px; }
  .memo-post-text { font-size: 13px; color: var(--text); margin-top: 8px; line-height: 1.5; }
  .memo-post-text p { margin: 0 0 8px 0; }
  .memo-post-text p:last-child { margin-bottom: 0; }
  .memo-post-text ul, .memo-post-text ol { margin: 4px 0 8px 20px; padding: 0; }
  .memo-post-text pre { background: var(--input-bg); padding: 8px 10px; border-radius: 4px; overflow-x: auto; margin: 8px 0; }
  .memo-post-text code { background: var(--input-bg); padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  .memo-post-text pre code { background: none; padding: 0; }
  .memo-post-text h1, .memo-post-text h2, .memo-post-text h3, .memo-post-text h4 { margin: 12px 0 6px 0; color: var(--text-bright); }
  .memo-post-text blockquote { border-left: 3px solid var(--border-dark); margin: 8px 0; padding: 4px 12px; color: var(--text-dim); }
  .memo-post-text table { border-collapse: collapse; margin: 8px 0; }
  .memo-post-text th, .memo-post-text td { border: 1px solid var(--border-dark); padding: 4px 8px; font-size: 12px; }

  /* -- Blog tab -- */
  #blog-pane { padding: 0; flex-direction: row; }
  #blog-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow: hidden; }
  #blog-main { flex: 1; overflow-y: auto; padding: 20px; }
  .blog-filter-bar { display: flex; gap: 4px; padding: 8px 10px; border-bottom: 1px solid var(--border-dark); }
  .blog-filter-btn { flex: 1; background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim);
                     padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: 600; }
  .blog-filter-btn.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .blog-post-item { padding: 10px 12px; border-bottom: 1px solid var(--border-mid); cursor: pointer; transition: background 0.1s; }
  .blog-post-item:hover { background: var(--border-mid); }
  .blog-post-item.active { background: var(--border-mid); border-left: 3px solid var(--accent); }
  .blog-post-title { font-size: 13px; font-weight: 700; color: var(--text); }
  .blog-post-preview { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; overflow: hidden;
                       text-overflow: ellipsis; white-space: nowrap; }
  .blog-post-meta { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }
  .blog-external-badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         background: #2ecc71; color: var(--text-bright); padding: 1px 5px; border-radius: 3px; margin-left: 6px; }
  .blog-internal-badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         background: var(--border-dark); color: var(--text-dim); padding: 1px 5px; border-radius: 3px; margin-left: 6px; }
  .blog-tag { font-size: 10px; background: var(--input-bg); color: var(--text-dim); padding: 1px 6px;
              border-radius: 4px; margin-right: 3px; display: inline-block; }
  .blog-reply { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px; margin-bottom: 8px; }
  .blog-reply-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .blog-reply-date { font-size: 10px; color: var(--text-dimmer); margin-left: 8px; }
  .blog-reply-text { font-size: 13px; color: var(--text); margin-top: 6px; line-height: 1.5; white-space: pre-wrap; }

  /* -- Events tab -- */
  #events-pane { padding: 0; flex-direction: row; }
  .events-sub-tab.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .event-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
                padding: 14px 16px; flex: 1 1 250px; max-width: 350px; min-width: 220px;
                transition: border-color 0.15s; }
  .event-card:hover { border-color: var(--text-dimmer); }
  .event-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .event-card-name { font-size: 14px; font-weight: 700; color: var(--text); }
  .event-card-severity { font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px;
                         text-transform: uppercase; letter-spacing: 0.5px; }
  .event-sev-critical { background: #e94560; color: #fff; }
  .event-sev-high { background: #e67e22; color: #fff; }
  .event-sev-medium { background: #f39c12; color: #111; }
  .event-sev-low { background: #2ecc71; color: #111; }
  .event-card-actions { font-size: 11px; color: var(--text-dim); margin-bottom: 8px; }
  .event-card-preview { font-size: 11px; color: var(--text-dimmer); margin-bottom: 10px; overflow: hidden;
                        text-overflow: ellipsis; white-space: nowrap; }
  .event-card-btns { display: flex; gap: 4px; }
  .event-card-btns button { flex: 1; }
  .event-trigger-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 5px; border-radius: 6px;
                       cursor: pointer; font-size: 11px; font-weight: 600; }
  .event-trigger-btn:hover { background: var(--accent-dark); }
  .event-log-row { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px;
                   padding: 10px 14px; margin-bottom: 8px; display: flex; align-items: center; gap: 12px; }
  .event-log-row:hover { border-color: var(--text-dimmer); }
  .event-log-time { font-size: 11px; color: var(--text-dimmer); min-width: 80px; }
  .event-log-name { font-size: 13px; font-weight: 600; color: var(--text); flex: 1; }
  .event-log-actions { font-size: 10px; color: var(--text-dimmer); }

  /* -- Modal overlay -- */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
                   z-index: 1000; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 12px;
           padding: 24px; min-width: 380px; max-width: 500px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
  .modal h2 { margin: 0 0 16px; font-size: 16px; color: var(--accent); }
  .modal-field { margin-bottom: 14px; }
  .modal-field label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; font-weight: 600;
                       text-transform: uppercase; letter-spacing: 0.5px; }
  .modal-field input, .modal-field select, .modal-field textarea {
    width: 100%; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 8px 12px;
    border-radius: 8px; font-size: 14px; outline: none; box-sizing: border-box; }
  .modal-field input:focus, .modal-field select:focus, .modal-field textarea:focus { border-color: var(--accent); }
  .modal-field textarea { resize: vertical; min-height: 60px; font-family: inherit; }
  .modal-field .field-hint { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; }
  .modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; }
  .modal-btn-primary { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 20px;
                       border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
  .modal-btn-primary:hover { background: var(--accent-dark); }
  .modal-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .modal-btn-cancel { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 8px 20px;
                      border-radius: 8px; cursor: pointer; font-size: 13px; }
  .modal-btn-cancel:hover { border-color: var(--accent); color: var(--accent); }
  .modal-status { font-size: 12px; color: var(--highlight); margin-top: 10px; min-height: 16px; }

  /* -- Loading overlay -- */
  #loading-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8);
                     z-index: 2000; align-items: center; justify-content: center; flex-direction: column; gap: 12px; }
  #loading-overlay.open { display: flex; }
  #loading-overlay .spinner { width: 32px; height: 32px; border: 3px solid var(--border-dark);
                              border-top-color: var(--accent); border-radius: 50%;
                              animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #loading-text { color: var(--text); font-size: 14px; }

  #main-layout { flex: 1; display: flex; overflow: hidden; }

  /* -- Sidebar -- */
  #sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
             display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .channel-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                 background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                 font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .channel-btn:hover { background: var(--border-mid); color: var(--text); }
  .channel-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  .channel-btn .unread-badge { background: var(--accent); color: var(--text-bright); font-size: 10px;
                               padding: 1px 6px; border-radius: 8px; margin-left: auto;
                               font-weight: 700; display: none; }
  .channel-btn .unread-badge.visible { display: inline; }
  .sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 6px 14px; }

  /* -- Tab panes -- */
  .tab-pane { display: none; flex: 1; overflow: hidden; }
  .tab-pane.active { display: flex; }
  #chat-pane { flex-direction: row; }
  #docs-pane { flex-direction: column; }
  #chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

  /* -- Chat tab -- */
  #channel-header { background: var(--panel); padding: 8px 20px; border-bottom: 1px solid var(--border);
                    font-size: 15px; font-weight: 700; color: var(--text); }
  #channel-header .ch-desc { font-size: 12px; color: var(--text-dim); font-weight: 400; margin-left: 10px; }
  #channel-members { font-size: 11px; color: var(--text-dimmer); margin-top: 2px; }
  #messages-panel { flex: 1; overflow-y: auto; padding: 12px 20px; display: flex;
                    flex-direction: column; gap: 6px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; }
  .msg-row { display: flex; gap: 10px; align-items: flex-start; }
  .msg-body { flex: 1; min-width: 0; }
  .msg-avatar { width: 32px; height: 32px; border-radius: 6px; flex-shrink: 0;
                display: flex; align-items: center; justify-content: center;
                font-size: 14px; font-weight: 700; color: #fff; margin-top: 1px; }
  .msg-avatar img { width: 32px; height: 32px; border-radius: 6px; object-fit: cover; }
  .msg .sender { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
  .msg .content { font-size: 14px; word-break: break-word; }
  .msg .content h1 { font-size: 16px; margin: 8px 0 4px; color: var(--text); }
  .msg .content h2 { font-size: 15px; margin: 6px 0 3px; color: var(--text); }
  .msg .content h3 { font-size: 14px; margin: 5px 0 2px; color: var(--text); }
  .msg .content p { margin: 4px 0; }
  .msg .content ul, .msg .content ol { margin: 4px 0 4px 20px; }
  .msg .content li { margin: 2px 0; }
  .msg .content strong { color: var(--text-bright); }
  .msg .content code { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; font-size: 13px; }
  .msg .content pre { background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; margin: 4px 0;
                      overflow-x: auto; }
  .msg .content pre code { background: none; padding: 0; }
  .msg .content hr { border: none; border-top: 1px solid var(--border-dark); margin: 8px 0; }
  .msg .content input[type="checkbox"] { margin-right: 4px; }
  .msg .ts { font-size: 11px; color: var(--text-dim); margin-top: 4px; }
  .msg-customer { align-self: flex-end; background: var(--border); border-bottom-right-radius: 4px; }
  .msg-customer .sender { color: #4fc3f7; }
  .msg-board .sender { color: #ffd700; }
  .msg-hacker .sender { color: #00ff41; }
  .msg-god .sender { color: #ff6ff2; }
  .msg-intern .sender { color: #a8e6cf; }
  .msg-competitor .sender { color: #ff4444; }
  .msg-regulator .sender { color: #ff9800; }
  .msg-investor .sender { color: #7c4dff; }
  .msg-press .sender { color: #ffab40; }
  .msg-agent { align-self: flex-start; background: var(--border-mid); border: 1px solid var(--border-dark); border-bottom-left-radius: 4px; }
  .msg-pm .sender { color: #e94560; }
  .msg-engmgr .sender { color: #f39c12; }
  .msg-architect .sender { color: #9b59b6; }
  .msg-senior .sender { color: #2ecc71; }
  .msg-support .sender { color: #1abc9c; }
  .msg-sales .sender { color: #e67e22; }
  .msg-ceo .sender { color: #f1c40f; }
  .msg-cfo .sender { color: #3498db; }
  .msg-marketing .sender { color: #e056a0; }
  .msg-devops .sender { color: #00bcd4; }
  .msg-projmgr .sender { color: #26c6da; }
  .msg-default .sender { color: #95a5a6; }

  /* -- Persona bar -- */
  #persona-bar { background: var(--sidebar); padding: 6px 20px; border-top: 1px solid var(--border);
                 display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  /* -- Input area -- */
  #input-area { background: var(--panel); padding: 10px 20px; border-top: 1px solid var(--border);
                display: flex; gap: 8px; align-items: center; }
  #sender-name { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #sender-name:focus { border-color: var(--accent); }
  #sender-role, #sender-role-custom { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                   padding: 8px 12px; border-radius: 8px; font-size: 14px; }
  #msg-input { flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
               padding: 10px 14px; border-radius: 8px; font-size: 14px; outline: none; }
  #msg-input:focus { border-color: var(--accent); }
  #send-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 10px 20px;
              border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; }
  #send-btn:hover { background: var(--accent-dark); }
  #clear-btn { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 10px 14px;
               border-radius: 8px; font-size: 14px; cursor: pointer; }
  #clear-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* -- Docs tab -- */
  #docs-pane { padding: 0; flex-direction: row; }
  #docs-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .docs-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                          letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .docs-sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 6px 14px; }
  .folder-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .folder-btn:hover { background: var(--border-mid); color: var(--text); }
  .folder-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  #docs-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #docs-toolbar { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                  display: flex; align-items: center; }
  #docs-search { width: 100%; max-width: 400px; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #docs-search:focus { border-color: var(--accent); }
  #new-doc-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 16px;
                 border-radius: 8px; font-size: 13px; cursor: pointer; font-weight: 600;
                 margin-left: 8px; white-space: nowrap; }
  #new-doc-btn:hover { background: var(--accent-dark); }
  #doc-editor { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #doc-editor-header { display: flex; align-items: center; justify-content: space-between;
                       padding: 10px 20px; border-bottom: 1px solid var(--border); background: var(--panel); }
  #doc-editor-header button { background: transparent; color: var(--text); border: 1px solid var(--border-dark);
                              padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-editor-save { background: var(--accent) !important; border-color: var(--accent) !important; font-weight: 600; }
  #doc-editor-save:hover { background: var(--accent-dark) !important; }
  #doc-editor-form { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 16px 20px; overflow-y: auto; }
  #doc-editor-title { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                      padding: 10px 14px; border-radius: 8px; font-size: 16px; font-weight: 700; outline: none; }
  #doc-editor-title:focus { border-color: var(--accent); }
  #doc-editor-folder { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                       padding: 8px 12px; border-radius: 8px; font-size: 14px; width: 200px; }
  #doc-editor-content { flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                        padding: 14px; border-radius: 8px; font-size: 14px; outline: none;
                        font-family: monospace; resize: none; min-height: 300px; }
  #doc-editor-content:focus { border-color: var(--accent); }
  #docs-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  .doc-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px 16px;
              margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .doc-card:hover { border-color: var(--accent); }
  .doc-card-title { font-size: 14px; font-weight: 700; color: var(--highlight); margin-bottom: 4px; }
  .doc-card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .doc-card-folder { font-size: 11px; background: var(--border); color: var(--highlight); padding: 2px 8px;
                     border-radius: 4px; font-weight: 600; }
  .doc-card-preview { font-size: 13px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; }
  #docs-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  #doc-viewer { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #doc-viewer.open { display: flex; }
  #doc-viewer-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                       display: flex; align-items: center; gap: 10px; }
  #doc-back-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 12px;
                  border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-back-btn:hover { border-color: var(--accent); color: var(--accent); }
  #doc-viewer-title { font-size: 16px; font-weight: 700; color: var(--highlight); }
  #doc-viewer-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                        color: var(--text); line-height: 1.7; }
  #doc-viewer-content h1 { font-size: 20px; margin: 12px 0 8px; }
  #doc-viewer-content h2 { font-size: 17px; margin: 10px 0 6px; }
  #doc-viewer-content h3 { font-size: 15px; margin: 8px 0 4px; }
  #doc-viewer-content p { margin: 6px 0; }
  #doc-viewer-content ul, #doc-viewer-content ol { margin: 6px 0 6px 24px; }
  #doc-viewer-content li { margin: 3px 0; }
  #doc-viewer-content strong { color: var(--text-bright); }
  #doc-viewer-content code { background: rgba(255,255,255,0.1); padding: 2px 5px; border-radius: 3px; }
  #doc-viewer-content pre { background: rgba(0,0,0,0.3); padding: 12px; border-radius: 6px; margin: 6px 0;
                            overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  #doc-viewer-content pre code { background: none; padding: 0; }
  #doc-viewer-content hr { border: none; border-top: 1px solid var(--border-dark); margin: 10px 0; }
  #doc-viewer-content input[type="checkbox"] { margin-right: 4px; }

  /* -- GitLab tab -- */
  #gitlab-pane { padding: 0; flex-direction: row; }
  #gitlab-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                    display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .gitlab-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                            letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .repo-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
              background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
              font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .repo-btn:hover { background: var(--border-mid); color: var(--text); }
  .repo-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  #gitlab-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #gitlab-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                   display: flex; align-items: center; gap: 12px; }
  #gitlab-repo-title { font-size: 16px; font-weight: 700; color: var(--highlight); }
  #gitlab-repo-desc { font-size: 13px; color: var(--text-dim); }
  .gitlab-toggle-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 14px;
                       border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .gitlab-toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .gitlab-toggle-btn.active { background: #0f3460; color: #4fc3f7; border-color: #4fc3f7; }
  #gitlab-toggle-bar { padding: 8px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                       display: flex; gap: 6px; }
  #gitlab-content { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #gitlab-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  .gitlab-breadcrumbs { font-size: 13px; color: var(--text-dim); margin-bottom: 12px; }
  .gitlab-breadcrumbs a { color: var(--highlight); cursor: pointer; text-decoration: none; }
  .gitlab-breadcrumbs a:hover { text-decoration: underline; }
  .tree-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border-dark);
               cursor: pointer; font-size: 14px; color: var(--text); }
  .tree-item:hover { background: var(--border-mid); }
  .tree-item-icon { font-size: 14px; width: 20px; text-align: center; }
  .tree-item-name { flex: 1; }
  .gitlab-file-viewer { background: var(--input-bg); border: 1px solid var(--border-dark); border-radius: 6px; padding: 16px;
                        font-family: monospace; font-size: 13px; white-space: pre-wrap; word-break: break-word;
                        color: var(--text); line-height: 1.6; }
  .commit-item { padding: 10px 12px; border-bottom: 1px solid var(--border-dark); }
  .commit-item-id { font-family: monospace; font-size: 12px; color: var(--highlight); margin-right: 8px; }
  .commit-item-msg { font-size: 14px; color: var(--text); }
  .commit-item-meta { font-size: 12px; color: var(--text-dimmer); margin-top: 4px; }

  /* -- Tickets tab -- */
  #tickets-pane { padding: 0; flex-direction: row; }
  #tickets-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                     display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .tickets-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                             letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .tickets-filter-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                        background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                        font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .tickets-filter-btn:hover { background: var(--border-mid); color: var(--text); }
  .tickets-filter-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  .tickets-filter-btn .tk-count { margin-left: auto; font-size: 11px; color: var(--text-dimmer); }
  #tickets-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #tickets-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                    font-size: 15px; font-weight: 700; color: var(--text); }
  #tickets-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #tickets-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  .ticket-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px 16px;
                 margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .ticket-card:hover { border-color: var(--accent); }
  .ticket-card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .ticket-card-id { font-family: monospace; font-size: 11px; color: var(--text-dim); }
  .ticket-card-title { font-size: 14px; font-weight: 700; color: var(--text); flex: 1; }
  .ticket-card-bottom { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .tk-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
  .tk-status-open { background: #1b5e20; color: #a5d6a7; }
  .tk-status-in_progress { background: #0d47a1; color: #90caf9; }
  .tk-status-resolved { background: #4a148c; color: #ce93d8; }
  .tk-status-closed { background: #333; color: #888; }
  .tk-priority-low { background: #263238; color: #78909c; }
  .tk-priority-medium { background: #33691e; color: #aed581; }
  .tk-priority-high { background: #e65100; color: #ffcc80; }
  .tk-priority-critical { background: #b71c1c; color: #ef9a9a; }
  .tk-assignee { font-size: 11px; color: var(--highlight); margin-left: auto; }
  #ticket-detail { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #ticket-detail.open { display: flex; }
  #ticket-detail-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                          display: flex; align-items: center; gap: 10px; }
  #ticket-back-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 12px;
                     border-radius: 6px; cursor: pointer; font-size: 13px; }
  #ticket-back-btn:hover { border-color: var(--accent); color: var(--accent); }
  #ticket-detail-title { font-size: 16px; font-weight: 700; color: var(--text); }
  #ticket-detail-id { font-family: monospace; font-size: 12px; color: var(--text-dim); margin-left: 8px; }
  #ticket-detail-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                           color: var(--text); line-height: 1.7; }
  .tk-detail-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .tk-detail-field { font-size: 13px; color: var(--text-dim); }
  .tk-detail-field strong { color: var(--text); }
  .tk-detail-desc { background: var(--input-bg); border: 1px solid var(--border-dark); border-radius: 6px; padding: 12px;
                    margin-bottom: 16px; white-space: pre-wrap; word-break: break-word; }
  .tk-detail-deps { margin-bottom: 16px; font-size: 13px; }
  .tk-detail-deps span { color: var(--highlight); font-family: monospace; cursor: pointer; }
  .tk-comments-header { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 8px;
                        border-bottom: 1px solid var(--border-dark); padding-bottom: 4px; }
  .tk-comment { background: var(--input-bg); border-left: 3px solid var(--border); padding: 8px 12px; margin-bottom: 8px;
                border-radius: 0 6px 6px 0; }
  .tk-comment-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .tk-comment-time { font-size: 11px; color: var(--text-dimmer); margin-left: 8px; }
  .tk-comment-text { font-size: 13px; color: var(--text); margin-top: 4px; }
  #tk-create-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 6px 14px; border-radius: 6px;
                   cursor: pointer; font-size: 12px; font-weight: 600; margin-left: auto; }
  #tk-create-btn:hover { background: var(--accent-dark); }
  #tk-create-form { display: none; background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px;
                    padding: 16px; margin-bottom: 12px; }
  #tk-create-form.open { display: block; }
  .tk-form-row { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; }
  .tk-form-row label { font-size: 12px; color: var(--text-dim); min-width: 70px; }
  .tk-form-input { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                   border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-input:focus { border-color: var(--accent); }
  .tk-form-select { background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                    border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-textarea { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 60px;
                      font-family: inherit; }
  .tk-form-textarea:focus { border-color: var(--accent); }
  .tk-form-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .tk-form-submit { background: var(--accent); color: var(--text-bright); border: none; padding: 6px 16px; border-radius: 6px;
                    cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-form-submit:hover { background: var(--accent-dark); }
  .tk-form-cancel { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 16px;
                    border-radius: 6px; cursor: pointer; font-size: 12px; }
  .tk-form-cancel:hover { border-color: var(--accent); color: var(--accent); }
  .tk-detail-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;
                       padding-bottom: 12px; border-bottom: 1px solid var(--border-dark); }
  .tk-action-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text); padding: 5px 12px;
                   border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-action-btn:hover { border-color: var(--accent); color: var(--accent); }
  .tk-action-btn.primary { background: #0d47a1; border-color: #0d47a1; color: #90caf9; }
  .tk-action-btn.primary:hover { background: #1565c0; }
  .tk-action-btn.danger { border-color: #b71c1c; color: #ef9a9a; }
  .tk-action-btn.danger:hover { background: #b71c1c; color: var(--text-bright); }
  .tk-action-btn.success { border-color: #1b5e20; color: #a5d6a7; }
  .tk-action-btn.success:hover { background: #1b5e20; color: var(--text-bright); }
  .tk-assign-row { display: flex; gap: 8px; align-items: center; }
  .tk-assign-select { background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 4px 8px;
                      border-radius: 6px; font-size: 12px; }
  .tk-comment-input-area { display: flex; gap: 8px; margin-top: 12px; align-items: flex-start; }
  .tk-comment-input { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 8px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 36px;
                      font-family: inherit; }
  .tk-comment-input:focus { border-color: var(--accent); }
  .tk-comment-submit { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 14px; border-radius: 6px;
                       cursor: pointer; font-size: 12px; font-weight: 600; align-self: flex-end; }
  .tk-comment-submit:hover { background: var(--accent-dark); }
</style>
</head>
<body>
<div id="header">
  <h1>CoSim</h1>
  <button class="header-tab active" data-tab="chat">Chat</button>
  <button class="header-tab" data-tab="docs">Docs</button>
  <button class="header-tab" data-tab="gitlab">GitLab</button>
  <button class="header-tab" data-tab="tickets">Tickets</button>
  <button class="header-tab" data-tab="email">Email</button>
  <button class="header-tab" data-tab="memos">Memos</button>
  <button class="header-tab" data-tab="blog">Blog</button>
  <button class="header-tab" data-tab="events">Events</button>
  <button class="header-tab" data-tab="npcs">NPCs</button>
  <button class="header-tab" data-tab="usage">Usage</button>
  <button class="header-tab" data-tab="recap">Recap</button>
  <button class="header-tab" data-tab="advanced">Advanced</button>
  <select id="theme-select" title="Theme" style="background:var(--input-bg);color:var(--text-dim);border:1px solid var(--border-dark);padding:4px 6px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;outline:none;margin:auto 0;margin-left:8px">
    <option value="default">Default</option>
    <option value="stadium">Stadium</option>
    <option value="field">Field</option>
    <option value="solarized-dark">Solarized Dark</option>
    <option value="solarized-light">Solarized Light</option>
  </select>
  <div id="session-controls">
    <span id="orch-status" title="Orchestrator status">
      <span id="orch-dot" class="status-dot disconnected"></span>
      <span id="orch-label">Disconnected</span>
    </span>
    <button id="session-new-btn" class="session-btn" title="New session">New</button>
    <button id="session-save-btn" class="session-btn" title="Save session">Save</button>
    <select id="session-load-select" title="Load session">
      <option value="" disabled selected>Load...</option>
    </select>
  </div>
</div>
<div id="main-layout">
  <!-- Chat tab: sidebar + chat area -->
  <div id="chat-pane" class="tab-pane active">
    <div id="sidebar">
      <div class="sidebar-section">Internal</div>
      <div id="internal-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">External</div>
      <div id="external-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Scenario Director</div>
      <div id="director-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">System</div>
      <div id="system-channels"></div>
    </div>
    <div id="chat-area">
      <div id="channel-header">
        <span id="channel-title">#general</span>
        <span class="ch-desc" id="channel-desc"></span>
        <div id="channel-members"></div>
      </div>
      <div id="messages-panel"></div>
      <div id="persona-bar">
        <input id="sender-name" type="text" placeholder="Your name..." value="" style="width:120px" />
        <select id="sender-role"></select>
        <input id="sender-role-custom" type="text" placeholder="Custom role..." style="width:100px;display:none" />
      </div>
      <div id="input-area">
        <input id="msg-input" type="text" placeholder="Type a message..." autocomplete="off" />
        <button id="send-btn">Send</button>
      </div>
    </div>
  </div>
  <!-- Docs tab -->
  <div id="docs-pane" class="tab-pane">
    <div id="docs-sidebar">
      <div class="docs-sidebar-section">All</div>
      <button class="folder-btn active" data-folder="" id="folder-all">All Folders</button>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Shared</div>
      <div id="shared-folders"></div>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Departments</div>
      <div id="dept-folders"></div>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Personal</div>
      <div id="personal-folders"></div>
    </div>
    <div id="docs-main">
      <div id="docs-toolbar">
        <input id="docs-search" type="text" placeholder="Search documents..." autocomplete="off" />
        <button id="new-doc-btn">+ New Document</button>
      </div>
      <div id="doc-editor" style="display:none">
        <div id="doc-editor-header">
          <button id="doc-editor-cancel">Cancel</button>
          <span style="font-weight:700;font-size:14px">New Document</span>
          <button id="doc-editor-save">Save</button>
        </div>
        <div id="doc-editor-form">
          <input id="doc-editor-title" type="text" placeholder="Document title..." autocomplete="off" />
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select id="doc-editor-folder">
            </select>
            <span style="font-size:11px;color:var(--text-dimmer)">Author:</span>
            <input id="doc-author-name" type="text" placeholder="Your name..." style="width:120px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-author-role" style="background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px"></select>
          </div>
          <textarea id="doc-editor-content" placeholder="Write your document content here (Markdown supported)..." rows="16"></textarea>
        </div>
      </div>
      <div id="docs-list">
        <div id="docs-empty">No documents yet.</div>
      </div>
      <div id="doc-viewer">
        <div id="doc-viewer-header">
          <button id="doc-back-btn">Back</button>
          <span id="doc-viewer-title"></span>
          <div style="margin-left:auto;display:flex;gap:6px">
            <button id="doc-history-btn" class="session-btn" style="font-size:11px">History</button>
            <button id="doc-edit-btn" class="session-btn" style="font-size:11px">Edit Latest Version</button>
          </div>
        </div>
        <div id="doc-viewer-body" style="display:flex;flex:1;min-height:0;overflow:hidden">
          <div id="doc-viewer-content" style="flex:1;overflow-y:auto"></div>
          <div id="doc-history-panel" style="display:none;width:220px;min-width:220px;border-left:1px solid var(--border-dark);background:var(--sidebar);overflow-y:auto">
            <div style="padding:8px 12px;font-size:11px;font-weight:700;color:var(--text-dimmer);text-transform:uppercase;letter-spacing:0.5px">Version History</div>
            <div id="doc-history-list"></div>
          </div>
        </div>
        <div id="doc-edit-area" style="display:none;flex:1;min-height:0;flex-direction:column;padding:12px 20px;gap:8px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:11px;color:var(--text-dimmer)">Editing as:</span>
            <input id="doc-edit-author-name" type="text" placeholder="Your name..." style="width:120px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-edit-author-role" style="background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px"></select>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button id="doc-edit-cancel" class="session-btn" style="font-size:11px">Cancel</button>
              <button id="doc-edit-save" class="session-btn" style="font-size:11px;background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Save</button>
            </div>
          </div>
          <textarea id="doc-edit-textarea" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:monospace;resize:none;outline:none"></textarea>
        </div>
      </div>
    </div>
  </div>
  <!-- GitLab tab -->
  <div id="gitlab-pane" class="tab-pane">
    <div id="gitlab-sidebar">
      <div class="gitlab-sidebar-section">Repositories</div>
      <div id="gitlab-repo-list"></div>
      <div style="padding:8px 10px">
        <button id="gl-new-repo-btn" class="session-btn" style="width:100%;font-size:11px">+ New Repo</button>
      </div>
      <div id="gl-new-repo-form" style="display:none;padding:4px 10px 10px">
        <input id="gl-new-repo-name" type="text" placeholder="repo-name" autocomplete="off"
               style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <input id="gl-new-repo-desc" type="text" placeholder="Description (optional)" autocomplete="off"
               style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <div style="display:flex;gap:4px">
          <button id="gl-new-repo-cancel" class="session-btn" style="flex:1;font-size:11px">Cancel</button>
          <button id="gl-new-repo-save" class="session-btn" style="flex:1;font-size:11px;background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Create</button>
        </div>
      </div>
    </div>
    <div id="gitlab-main">
      <div id="gitlab-header">
        <span id="gitlab-repo-title">Select a repository</span>
        <span id="gitlab-repo-desc"></span>
      </div>
      <div id="gitlab-toggle-bar">
        <button class="gitlab-toggle-btn active" data-view="tree" id="gl-toggle-tree">Files</button>
        <button class="gitlab-toggle-btn" data-view="commits" id="gl-toggle-commits">Commits</button>
      </div>
      <div id="gitlab-content">
        <div id="gitlab-empty">No repositories yet.</div>
      </div>
    </div>
  </div>
  <!-- Tickets tab -->
  <div id="tickets-pane" class="tab-pane">
    <div id="tickets-sidebar">
      <div class="tickets-sidebar-section">Status Filter</div>
      <button class="tickets-filter-btn active" data-status="" id="tk-filter-all">All <span class="tk-count" id="tk-count-all"></span></button>
      <button class="tickets-filter-btn" data-status="open">Open <span class="tk-count" id="tk-count-open"></span></button>
      <button class="tickets-filter-btn" data-status="in_progress">In Progress <span class="tk-count" id="tk-count-in_progress"></span></button>
      <button class="tickets-filter-btn" data-status="resolved">Resolved <span class="tk-count" id="tk-count-resolved"></span></button>
      <button class="tickets-filter-btn" data-status="closed">Closed <span class="tk-count" id="tk-count-closed"></span></button>
    </div>
    <div id="tickets-main">
      <div id="tickets-header" style="display:flex;align-items:center;">
        <span>Tickets</span>
        <button id="tk-create-btn" onclick="toggleCreateForm()">+ New Ticket</button>
      </div>
      <div id="tickets-list">
        <div id="tk-create-form">
          <div class="tk-form-row">
            <label>Title</label>
            <input class="tk-form-input" id="tk-form-title" placeholder="Ticket title" />
          </div>
          <div class="tk-form-row">
            <label>Priority</label>
            <select class="tk-form-select" id="tk-form-priority">
              <option value="low">Low</option>
              <option value="medium" selected>Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
            <label style="margin-left:12px;">Assignee</label>
            <select class="tk-form-select" id="tk-form-assignee">
              <option value="">Unassigned</option>
            </select>
          </div>
          <div class="tk-form-row">
            <label>Created by</label>
            <select class="tk-form-select" id="tk-form-author">
            </select>
          </div>
          <div class="tk-form-row">
            <label>Description</label>
            <textarea class="tk-form-textarea" id="tk-form-desc" placeholder="Describe the work to be done..."></textarea>
          </div>
          <div class="tk-form-row">
            <label>Notify channel</label>
            <select class="tk-form-select" id="tk-form-notify">
              <option value="">Don't notify</option>
            </select>
          </div>
          <div class="tk-form-actions">
            <button class="tk-form-cancel" onclick="toggleCreateForm()">Cancel</button>
            <button class="tk-form-submit" onclick="submitCreateTicket()">Create Ticket</button>
          </div>
        </div>
        <div id="tickets-empty">No tickets yet.</div>
      </div>
      <div id="ticket-detail">
        <div id="ticket-detail-header">
          <button id="ticket-back-btn">Back</button>
          <span id="ticket-detail-title"></span>
          <span id="ticket-detail-id"></span>
        </div>
        <div style="padding:8px 20px;background:var(--sidebar);border-bottom:1px solid var(--border-dark);display:flex;align-items:center;gap:8px">
          <span style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px">Acting as</span>
          <select class="tk-form-select" id="tk-acting-as" style="font-size:12px;">
          </select>
          <span style="font-size:10px;color:var(--text-dimmer)">All actions (status, assign, comments) use this identity</span>
        </div>
        <div id="ticket-detail-content"></div>
      </div>
    </div>
  </div>
  <!-- NPCs tab -->
  <div id="npcs-pane" class="tab-pane">
    <div id="npcs-sidebar">
      <div class="sidebar-section">Scenario</div>
      <div id="npcs-scenario-info" style="padding:8px 14px;font-size:12px;color:var(--text-dim);">No scenario loaded</div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Summary</div>
      <div id="npcs-summary" style="padding:8px 14px;font-size:12px;color:var(--text-dim);"></div>
      <hr class="sidebar-divider">
      <div style="padding:8px 10px">
        <button id="npc-hire-btn" class="session-btn" style="width:100%;background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:11px">+ Hire Agent</button>
      </div>
    </div>
    <div id="npcs-main">
      <div id="npcs-content">
        <div id="npcs-empty">No scenario loaded. Click New to start a session.</div>
      </div>
    </div>
  </div>
  <!-- Events tab -->
  <div id="events-pane" class="tab-pane">
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:10px 20px;background:var(--panel);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
        <button class="session-btn events-sub-tab active" data-events-tab="pool">Event Pool</button>
        <button class="session-btn events-sub-tab" data-events-tab="log">Event Log</button>
        <div style="margin-left:auto">
          <button id="events-add-btn" class="session-btn" style="background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:11px">+ Add Event</button>
        </div>
      </div>
      <div id="events-pool-view" style="flex:1;overflow-y:auto;padding:20px">
        <div id="events-pool-grid" style="display:flex;flex-wrap:wrap;gap:12px"></div>
        <div id="events-pool-empty" style="color:var(--text-dimmer);text-align:center;padding:40px">No events configured for this scenario.</div>
      </div>
      <div id="events-log-view" style="flex:1;overflow-y:auto;padding:20px;display:none">
        <div id="events-log-list"></div>
        <div id="events-log-empty" style="color:var(--text-dimmer);text-align:center;padding:40px">No events fired yet.</div>
      </div>
    </div>
  </div>
  <!-- Usage tab -->
  <div id="usage-pane" class="tab-pane">
    <div id="usage-sidebar">
      <div class="usage-sidebar-section">Session Totals</div>
      <div id="usage-totals" style="padding:4px 0;"></div>
    </div>
    <div id="usage-main">
      <div id="usage-content">
        <div id="usage-empty">No usage data yet. Send messages so agents produce responses.</div>
      </div>
    </div>
  </div>
  <!-- Recap tab -->
  <div id="recap-pane" class="tab-pane">
    <div id="recap-sidebar">
      <div class="sidebar-section">Generate Recap</div>
      <div style="padding:8px 14px">
        <select id="recap-style" style="width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:12px;margin-bottom:8px">
          <option value="normal">Normal</option>
          <option value="ye-olde-english">Ye Olde English</option>
          <option value="tolkien">Tolkien Fantasy</option>
          <option value="star-wars">Star Wars Crawl</option>
          <option value="star-trek">Star Trek Captain's Log</option>
          <option value="dr-who">Doctor Who</option>
          <option value="morse-code">Morse Code / Telegraph</option>
          <option value="dr-seuss">Dr. Seuss</option>
          <option value="shakespeare">Shakespearean</option>
          <option value="80s-rock-ballad">80s Rock Ballad</option>
          <option value="90s-alternative">90s Alternative</option>
          <option value="heavy-metal">Heavy Metal</option>
          <option value="dystopian">Dystopian</option>
          <option value="matrix">The Matrix</option>
          <option value="pharaoh">Pharaoh's Decree</option>
          <option value="tombstone">Tombstone Western</option>
          <option value="survivor">Survivor Tribal Council</option>
          <option value="hackernews">HackerNews Blog Post</option>
        </select>
        <button id="recap-generate-btn" class="session-btn" style="width:100%;background:var(--accent);border-color:var(--accent);color:var(--text-bright);font-size:12px">Generate Recap</button>
      </div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Saved Recaps</div>
      <div id="recap-list" style="flex:1;overflow-y:auto"></div>
    </div>
    <div id="recap-main">
      <div id="recap-content" style="padding:20px;font-size:14px;color:var(--text);line-height:1.8;white-space:pre-wrap">
        <div id="recap-empty" style="color:var(--text-dimmer);text-align:center;padding:60px">Pick a style and generate a recap of this session.</div>
      </div>
    </div>
  </div>
  <!-- Email tab -->
  <div id="email-pane" class="tab-pane">
    <div id="email-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="compose-email-btn" class="session-btn" style="width:100%;background:#3498db;border-color:#3498db;color:var(--text-bright);font-size:12px">Compose Email</button>
      </div>
      <div id="email-list" style="flex:1;overflow-y:auto"></div>
      <div id="email-list-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No emails sent yet.</div>
    </div>
    <div id="email-main">
      <div id="email-viewer" style="display:none">
        <div id="email-viewer-from" style="font-size:13px;color:var(--highlight);font-weight:700;margin-bottom:4px"></div>
        <div id="email-viewer-subject" style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:4px"></div>
        <div id="email-viewer-date" style="font-size:11px;color:var(--text-dimmer);margin-bottom:16px"></div>
        <div id="email-viewer-body" style="font-size:14px;color:var(--text);line-height:1.6;white-space:pre-wrap"></div>
      </div>
      <div id="email-compose" style="display:none;max-width:600px">
        <h3 style="color:var(--text);margin-bottom:12px">Compose Email</h3>
        <div class="modal-field">
          <label>From</label>
          <div style="display:flex;gap:8px">
            <input id="email-compose-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
            <select id="email-compose-role" style="flex:1"></select>
          </div>
          <input id="email-compose-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
        </div>
        <div class="modal-field">
          <label>Subject</label>
          <input id="email-compose-subject" type="text" placeholder="Subject line..." autocomplete="off" />
        </div>
        <div class="modal-field">
          <label>Body</label>
          <textarea id="email-compose-body" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:inherit;resize:vertical;line-height:1.6" placeholder="Write your email..."></textarea>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="session-btn" id="email-compose-cancel">Cancel</button>
          <button class="modal-btn-primary" id="email-compose-send" style="background:#3498db">Send</button>
        </div>
      </div>
      <div id="email-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">Select an email to read, or compose a new one.</div>
    </div>
  </div>
  <!-- Memos tab -->
  <div id="memos-pane" class="tab-pane">
    <div id="memos-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="create-memo-thread-btn" class="session-btn" style="width:100%;background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:12px">New Discussion</button>
      </div>
      <div id="memo-threads-list" style="flex:1;overflow-y:auto"></div>
      <div id="memo-threads-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No discussion threads yet.</div>
    </div>
    <div id="memos-main">
      <div id="memo-thread-viewer" style="display:none">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px">
          <div>
            <h2 id="memo-thread-title" style="color:var(--text);margin:0 0 4px 0;font-size:18px"></h2>
            <div id="memo-thread-meta" style="font-size:11px;color:var(--text-dimmer)"></div>
            <div id="memo-thread-description" style="font-size:13px;color:var(--text-dim);margin-top:8px"></div>
          </div>
          <button id="memo-delete-btn" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer" title="Delete thread">Delete</button>
        </div>
        <div id="memo-posts-list" style="margin:16px 0"></div>
        <div style="border-top:1px solid var(--border-dark);padding-top:12px">
          <div style="display:flex;gap:8px;margin-bottom:8px">
            <input id="memo-reply-name" type="text" placeholder="Name" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px" autocomplete="off" />
            <select id="memo-reply-role" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px"></select>
          </div>
          <input id="memo-reply-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:8px;box-sizing:border-box" autocomplete="off" />
          <textarea id="memo-reply-text" placeholder="Post a reply..." style="width:100%;min-height:80px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;justify-content:flex-end">
            <button id="memo-reply-send" class="modal-btn-primary" style="background:#2ecc71;font-size:12px">Post Reply</button>
          </div>
        </div>
      </div>
      <div id="memo-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">Select a discussion thread or create a new one.</div>
    </div>
  </div>
  <!-- Blog tab -->
  <div id="blog-pane" class="tab-pane">
    <div id="blog-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="create-blog-post-btn" class="session-btn" style="width:100%;background:var(--accent);border-color:var(--accent);color:var(--text-bright);font-size:12px">New Post</button>
      </div>
      <div class="blog-filter-bar">
        <button class="blog-filter-btn active" data-blog-filter="all">All</button>
        <button class="blog-filter-btn" data-blog-filter="internal">Internal</button>
        <button class="blog-filter-btn" data-blog-filter="external">External</button>
      </div>
      <div id="blog-posts-list" style="flex:1;overflow-y:auto"></div>
      <div id="blog-posts-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No blog posts yet.</div>
    </div>
    <div id="blog-main">
      <div id="blog-post-viewer" style="display:none">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px">
          <div>
            <div style="display:flex;align-items:center">
              <h2 id="blog-post-title" style="color:var(--text);margin:0;font-size:20px"></h2>
              <span id="blog-post-badge"></span>
            </div>
            <div id="blog-post-author" style="font-size:13px;color:var(--highlight);font-weight:700;margin-top:4px"></div>
            <div id="blog-post-date" style="font-size:11px;color:var(--text-dimmer);margin-top:2px"></div>
            <div id="blog-post-tags" style="margin-top:6px"></div>
          </div>
          <div style="display:flex;gap:4px">
            <button id="blog-publish-btn" style="background:#2ecc71;border:none;color:var(--text-bright);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;display:none" title="Publish">Publish</button>
            <button id="blog-unpublish-btn" style="background:transparent;border:1px solid #f39c12;color:#f39c12;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;display:none" title="Unpublish">Unpublish</button>
            <button id="blog-delete-btn" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer" title="Delete post">Delete</button>
          </div>
        </div>
        <div id="blog-post-body" style="font-size:14px;color:var(--text);line-height:1.7;margin-bottom:20px"></div>
        <div style="border-top:1px solid var(--border-dark);padding-top:12px">
          <h3 id="blog-replies-header" style="font-size:14px;color:var(--text);margin-bottom:10px"></h3>
          <div id="blog-replies-list" style="margin-bottom:16px"></div>
          <div style="display:flex;gap:8px;margin-bottom:8px">
            <input id="blog-reply-name" type="text" placeholder="Name" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px" autocomplete="off" />
            <select id="blog-reply-role" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px"></select>
          </div>
          <input id="blog-reply-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:8px;box-sizing:border-box" autocomplete="off" />
          <textarea id="blog-reply-text" placeholder="Write a reply..." style="width:100%;min-height:60px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;justify-content:flex-end">
            <button id="blog-reply-send" class="modal-btn-primary" style="font-size:12px">Post Reply</button>
          </div>
        </div>
      </div>
      <div id="blog-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">No blog posts yet — write the first one.</div>
    </div>
  </div>
  <!-- Advanced tab -->
  <div id="advanced-pane" class="tab-pane">
    <div id="advanced-main" style="flex:1;padding:20px;overflow-y:auto">
      <div style="max-width:800px">
        <h3 style="color:var(--text);margin-bottom:16px">Advanced Actions</h3>

        <!-- Session Manager -->
        <div style="margin-bottom:32px">
          <div style="font-size:12px;font-weight:600;color:var(--text);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Session Manager</div>
          <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">Manage saved sessions — load, rename, or delete.</p>
          <div id="session-manager-table-wrap" style="overflow-x:auto">
            <table id="session-manager-table" style="width:100%;border-collapse:collapse;font-size:13px">
              <thead>
                <tr style="border-bottom:1px solid var(--border-dark);text-align:left">
                  <th data-sm-sort="name" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Name <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="scenario" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Scenario <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="created_at" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Created <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="saved_at" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Last Saved <span class="sm-sort-arrow"></span></th>
                  <th style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;text-align:right">Actions</th>
                </tr>
              </thead>
              <tbody id="session-manager-body">
                <tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Danger Zone -->
        <div style="margin-bottom:24px">
          <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Danger Zone</div>
          <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">These actions are destructive and cannot be undone. Save your session first.</p>
          <button id="clear-chat-btn" class="session-btn" style="border-color:var(--accent);color:var(--accent);margin-right:8px">Clear Chat History</button>
          <button id="clear-all-btn" class="session-btn" style="background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Clear Everything</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Blog Create Post Modal -->
<div class="modal-overlay" id="blog-create-modal">
  <div class="modal" style="max-width:600px">
    <h2>New Blog Post</h2>
    <div class="modal-field">
      <label>Author</label>
      <div style="display:flex;gap:8px">
        <input id="blog-create-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
        <select id="blog-create-role" style="flex:1"></select>
      </div>
      <input id="blog-create-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Title</label>
      <input id="blog-create-title" type="text" placeholder="Blog post title..." autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Body</label>
      <textarea id="blog-create-body" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:inherit;resize:vertical;line-height:1.6;box-sizing:border-box" placeholder="Write your blog post..."></textarea>
    </div>
    <div class="modal-field">
      <label>Tags (comma-separated)</label>
      <input id="blog-create-tags" type="text" placeholder="engineering, api, release" autocomplete="off" />
    </div>
    <div class="modal-field" style="display:flex;align-items:center;gap:8px">
      <input id="blog-create-external" type="checkbox" style="accent-color:var(--accent)" />
      <label style="margin:0;text-transform:none;letter-spacing:0;font-size:13px;color:var(--text)">External (customer-facing)</label>
    </div>
    <div class="modal-actions">
      <button class="session-btn" id="blog-create-cancel">Cancel</button>
      <button class="session-btn" id="blog-create-draft" style="border-color:#f39c12;color:#f39c12">Save Draft</button>
      <button class="modal-btn-primary" id="blog-create-submit">Publish</button>
    </div>
  </div>
</div>

<!-- Memo Create Thread Modal -->
<div class="modal-overlay" id="memo-create-modal">
  <div class="modal" style="max-width:500px">
    <h2>New Discussion Thread</h2>
    <div class="modal-field">
      <label>Posted by</label>
      <div style="display:flex;gap:8px">
        <input id="memo-create-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
        <select id="memo-create-role" style="flex:1"></select>
      </div>
      <input id="memo-create-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Title</label>
      <input id="memo-create-title" type="text" placeholder="Discussion thread title..." autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Description (optional)</label>
      <textarea id="memo-create-description" style="width:100%;min-height:60px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box" placeholder="Brief description of the discussion topic..."></textarea>
    </div>
    <div class="modal-actions">
      <button class="session-btn" id="memo-create-cancel">Cancel</button>
      <button class="modal-btn-primary" id="memo-create-submit" style="background:#2ecc71">Create Thread</button>
    </div>
  </div>
</div>

<!-- New Session Modal -->
<div class="modal-overlay" id="new-session-modal">
  <div class="modal">
    <h2>New Session</h2>
    <div class="modal-field">
      <label>Scenario</label>
      <select id="new-session-scenario"></select>
      <div class="field-hint" id="new-session-scenario-desc"></div>
    </div>
    <div class="modal-field">
      <label>Session Name (optional)</label>
      <input id="new-session-name" type="text" placeholder="e.g. consulting-run" autocomplete="off" />
      <div class="field-hint">Leave blank to auto-generate from scenario + date</div>
    </div>
    <div class="modal-status" id="new-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="new-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="new-session-confirm">Create</button>
    </div>
  </div>
</div>

<!-- Save Session Modal -->
<div class="modal-overlay" id="save-session-modal">
  <div class="modal" style="max-width:480px">
    <h2>Save Session</h2>
    <div class="modal-field" id="save-session-existing-wrap">
      <label>Existing saves</label>
      <div id="save-session-list" style="max-height:180px;overflow-y:auto;border:1px solid var(--border-dark);border-radius:6px;background:var(--bg-darker,var(--bg))">
        <div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">Loading...</div>
      </div>
    </div>
    <div class="modal-field">
      <label>Save as</label>
      <input id="save-session-name" type="text" placeholder="e.g. before-demo" autocomplete="off" />
      <div class="field-hint">Leave blank to auto-generate. Click an existing save to branch from it.</div>
    </div>
    <div class="modal-status" id="save-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="save-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="save-session-confirm">Save</button>
    </div>
  </div>
</div>

<!-- Load Session Modal -->
<div class="modal-overlay" id="load-session-modal">
  <div class="modal" style="max-width:600px">
    <h2>Load Session</h2>
    <div class="modal-field">
      <label>Saved Sessions</label>
      <div style="max-height:280px;overflow-y:auto;border:1px solid var(--border-dark);border-radius:6px">
        <table id="load-session-table" style="width:100%;border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="border-bottom:1px solid var(--border-dark);text-align:left;position:sticky;top:0;background:var(--bg-surface,var(--bg))">
              <th data-lm-sort="name" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Name <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="scenario" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Scenario <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="created_at" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Created <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="saved_at" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Last Saved <span class="lm-sort-arrow"></span></th>
            </tr>
          </thead>
          <tbody id="load-session-body">
            <tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="modal-status" id="load-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="load-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="load-session-confirm" disabled>Load</button>
    </div>
  </div>
</div>

<!-- NPC Detail Modal -->
<div class="modal-overlay" id="npc-detail-modal">
  <div class="modal" style="width:80vw;max-width:1000px;height:75vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 id="npc-detail-title" style="margin:0"></h2>
      <button class="modal-btn-cancel" id="npc-detail-close">Close</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <button class="session-btn npc-detail-tab active" data-npc-tab="thoughts">Thoughts</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="character">Character</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="prompt">Prompt</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="config">Config</button>
    </div>
    <div id="npc-detail-thoughts" style="flex:1;min-height:0;display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border-dark)">
      <div style="width:200px;min-width:200px;background:var(--sidebar);border-right:1px solid var(--border-dark);display:flex;flex-direction:column">
        <div style="padding:6px 8px;border-bottom:1px solid var(--border-dark)">
          <input id="npc-thoughts-search" type="text" placeholder="Search thoughts..." autocomplete="off"
                 style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:4px 8px;border-radius:6px;font-size:11px;outline:none;box-sizing:border-box" />
        </div>
        <div id="npc-thoughts-list" style="flex:1;overflow-y:auto">
        </div>
      </div>
      <div id="npc-thoughts-content" style="flex:1;overflow-y:auto;background:var(--input-bg);padding:16px;font-size:13px;color:var(--text);white-space:pre-wrap;font-family:monospace;line-height:1.5">
        No thoughts recorded yet.
      </div>
    </div>
    <div id="npc-detail-character" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:20px;display:none">
      <div id="npc-cs-meta" style="margin-bottom:16px"></div>
      <div id="npc-cs-sections" style="font-size:14px;color:var(--text);line-height:1.6"></div>
    </div>
    <div id="npc-detail-prompt" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:16px;font-size:13px;color:var(--text);white-space:pre-wrap;font-family:monospace;line-height:1.5;display:none">
    </div>
    <div id="npc-detail-config" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:16px;display:none">
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Response Tier</div>
        <select id="npc-config-tier" style="background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="1">Tier 1 — ICs</option>
          <option value="2">Tier 2 — Managers</option>
          <option value="3">Tier 3 — Executives</option>
        </select>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Verbosity</div>
        <select id="npc-config-verbosity" style="background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="concise">Concise — 1-2 sentences</option>
          <option value="brief">Brief — 2-3 sentences</option>
          <option value="normal" selected>Normal — default</option>
          <option value="essay">Essay — 1-2 short paragraphs</option>
          <option value="detailed">Detailed — thorough with examples</option>
          <option value="dissertation">Dissertation — exhaustive analysis</option>
        </select>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Channel Memberships</div>
        <div id="npc-config-channels" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Doc Folder Access</div>
        <div id="npc-config-folders" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">GitLab Repos</div>
        <div id="npc-config-repos" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="display:flex;justify-content:flex-end;padding-top:8px;border-top:1px solid var(--border-dark)">
        <button id="npc-config-save" class="modal-btn-primary" style="font-size:13px">Save Configuration</button>
      </div>
    </div>
  </div>
</div>

<!-- Hire Agent Modal -->
<div class="modal-overlay" id="hire-modal">
  <div class="modal" style="width:80vw;max-width:800px;height:80vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 style="margin:0">Hire New Agent</h2>
      <button class="modal-btn-cancel" id="hire-modal-close">Cancel</button>
    </div>
    <div style="flex:1;min-height:0;overflow-y:auto">
      <div class="modal-field">
        <label>Character Template</label>
        <select id="hire-template">
          <option value="">Start from scratch</option>
        </select>
        <div class="field-hint">Pick a template to pre-fill the character prompt, or write your own.</div>
      </div>
      <div class="modal-field">
        <label>Name / Role / Key</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="hire-name" type="text" placeholder="Name" style="flex:2" autocomplete="off" />
          <select id="hire-role-preset" style="flex:2"></select>
          <input id="hire-key" type="text" placeholder="key (auto)" style="flex:1" autocomplete="off" />
        </div>
        <input id="hire-role-custom" type="text" placeholder="Enter custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
      </div>
      <div class="modal-field">
        <label>Team Description</label>
        <input id="hire-team-desc" type="text" placeholder="e.g. testing, quality assurance, bug triage" autocomplete="off" />
      </div>
      <div class="modal-field">
        <label>Tier / Verbosity</label>
        <div style="display:flex;gap:8px">
          <select id="hire-tier" style="flex:1">
            <option value="1">Tier 1 — ICs</option>
            <option value="2">Tier 2 — Managers</option>
            <option value="3">Tier 3 — Executives</option>
          </select>
          <select id="hire-verbosity" style="flex:1">
            <option value="concise">Concise — 1-2 sentences</option>
            <option value="brief">Brief — 2-3 sentences</option>
            <option value="normal" selected>Normal</option>
            <option value="essay">Essay — 1-2 paragraphs</option>
            <option value="detailed">Detailed — thorough</option>
            <option value="dissertation">Dissertation — exhaustive</option>
          </select>
        </div>
      </div>
      <div class="modal-field">
        <label>Channels</label>
        <div id="hire-channels" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div class="modal-field">
        <label>Doc Folders</label>
        <div id="hire-folders" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div class="modal-field">
        <label>Character Prompt</label>
        <textarea id="hire-prompt" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:13px;font-family:monospace;resize:vertical" placeholder="# Role Name&#10;&#10;You are [Name], the [Role]. You..."></textarea>
        <div class="field-hint">The full role prompt defining this agent's personality, responsibilities, and behavior.</div>
      </div>
    </div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" onclick="closeModal('hire-modal')">Cancel</button>
      <button class="modal-btn-primary" id="hire-confirm" style="background:#2ecc71;border-color:#2ecc71">Hire</button>
    </div>
  </div>
</div>

<!-- Event Edit Modal -->
<div class="modal-overlay" id="event-edit-modal">
  <div class="modal" style="width:80vw;max-width:900px;height:80vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 id="event-edit-title" style="margin:0">Edit Event</h2>
      <div style="display:flex;gap:6px">
        <button class="session-btn" id="event-edit-history-btn" style="font-size:11px">History</button>
        <button class="modal-btn-cancel" id="event-edit-close">Cancel</button>
      </div>
    </div>
    <div style="flex:1;min-height:0;display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border-dark)">
      <div style="flex:1;display:flex;flex-direction:column">
        <textarea id="event-edit-yaml" style="flex:1;background:var(--input-bg);color:var(--text);border:none;padding:16px;font-size:13px;font-family:monospace;line-height:1.5;resize:none;outline:none" placeholder="name: My Event..."></textarea>
      </div>
      <div id="event-edit-history" style="display:none;width:200px;min-width:200px;border-left:1px solid var(--border-dark);background:var(--sidebar);overflow-y:auto">
        <div style="padding:8px 12px;font-size:11px;font-weight:700;color:var(--text-dimmer);text-transform:uppercase;letter-spacing:0.5px">Version History</div>
        <div id="event-edit-history-list"></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-top:12px">
      <button class="session-btn" id="event-edit-delete" style="color:var(--accent);font-size:11px">Delete Event</button>
      <div style="flex:1"></div>
      <button class="modal-btn-cancel" onclick="closeModal('event-edit-modal')">Cancel</button>
      <button class="modal-btn-primary" id="event-edit-save">Save</button>
    </div>
  </div>
</div>

<!-- Loading Overlay -->
<div id="loading-overlay">
  <div class="spinner"></div>
  <div id="loading-text">Loading...</div>
</div>

<script>
// -- Theme System --
function applyTheme(t) {
  if (t === 'default') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', t);
  }
  localStorage.setItem('cosimTheme', t);
  const sel = document.getElementById('theme-select');
  if (sel) sel.value = t;
}
(function() { applyTheme(localStorage.getItem('cosimTheme') || 'default'); })();
document.getElementById('theme-select').addEventListener('change', function() { applyTheme(this.value); });

const messagesPanel = document.getElementById('messages-panel');
const input = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const senderName = document.getElementById('sender-name');
const senderRole = document.getElementById('sender-role');
const senderRoleCustom = document.getElementById('sender-role-custom');

// Sticky name per role — remember the last name typed for each role
const ROLE_NAMES_KEY = 'company-sim-role-names';

function getRoleNames() {
  try { return JSON.parse(localStorage.getItem(ROLE_NAMES_KEY)) || {}; } catch(e) { return {}; }
}

function saveNameForRole() {
  const role = senderRole.value === 'custom' ? 'custom:' + senderRoleCustom.value.trim() : senderRole.value;
  const names = getRoleNames();
  names[role] = senderName.value.trim();
  localStorage.setItem(ROLE_NAMES_KEY, JSON.stringify(names));
}

function recallNameForRole() {
  const role = senderRole.value === 'custom' ? 'custom:' + senderRoleCustom.value.trim() : senderRole.value;
  const names = getRoleNames();
  if (role in names) senderName.value = names[role];
}

senderName.addEventListener('input', saveNameForRole);

senderRole.addEventListener('change', () => {
  if (senderRole.value === 'custom') {
    senderRoleCustom.style.display = '';
    senderRoleCustom.focus();
  } else {
    senderRoleCustom.style.display = 'none';
  }
  recallNameForRole();
});

senderRoleCustom.addEventListener('input', () => {
  saveNameForRole();
});

// Restore name on page load
recallNameForRole();

function getSenderLabel() {
  const name = senderName.value.trim() || 'Anonymous';
  let role = senderRole.value;
  if (role === 'custom') role = senderRoleCustom.value.trim();
  if (!role) return name;
  return name + ' (' + role + ')';
}

const channelTitle = document.getElementById('channel-title');
const channelDesc = document.getElementById('channel-desc');
const channelMembersEl = document.getElementById('channel-members');

let currentTab = 'chat';
let currentChannel = '#general';
let channelsData = {};
let messagesByChannel = {};
let unreadByChannel = {};
let seenIds = new Set();

// Agent persona maps — loaded dynamically from /api/personas
let SENDER_CLASS_MAP = {};
let PERSONA_DISPLAY = {};
let AGENT_NAMES = new Set();
let PERSONA_AVATARS = {};  // display_name → {avatar: url_or_null, initial: "P", color: "#..."}

// Color palette for agent personas (assigned round-robin on load)
const AGENT_COLORS = [
  'var(--accent)', '#f39c12', '#9b59b6', '#2ecc71', '#1abc9c',
  '#e67e22', '#f1c40f', '#3498db', '#e056a0', '#00bcd4', '#ff6b6b',
];

async function loadPersonas() {
  const resp = await fetch('/api/personas');
  const personas = await resp.json();
  SENDER_CLASS_MAP = {};
  PERSONA_DISPLAY = {};
  PERSONA_AVATARS = {};
  const keys = Object.keys(personas);
  keys.forEach((key, i) => {
    const p = personas[key];
    const cls = 'msg-agent-' + i;
    SENDER_CLASS_MAP[p.display_name] = cls;
    PERSONA_DISPLAY[key] = p.display_name;
    const color = AGENT_COLORS[i % AGENT_COLORS.length];
    PERSONA_AVATARS[p.display_name] = {
      avatar: p.avatar ? '/avatars/' + p.avatar : null,
      initial: p.display_name.charAt(0).toUpperCase(),
      color: color,
    };
  });
  AGENT_NAMES = new Set(Object.keys(SENDER_CLASS_MAP));

  // Inject dynamic CSS for agent colors
  let styleEl = document.getElementById('agent-colors-style');
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'agent-colors-style';
    document.head.appendChild(styleEl);
  }
  let css = '';
  keys.forEach((key, i) => {
    const color = AGENT_COLORS[i % AGENT_COLORS.length];
    css += '.msg-agent-' + i + ' .sender { color: ' + color + '; } ';
  });
  styleEl.textContent = css;

  // Update ticket dropdowns with current personas
  populateAllRoleDropdowns();
}

// Known human persona CSS classes
const HUMAN_CLASS_MAP = {
  'Customer': 'msg-customer', 'Consultant': 'msg-customer',
  'Board Member': 'msg-board', 'Hacker': 'msg-hacker', 'God': 'msg-god',
  'Intern': 'msg-intern', 'Competitor': 'msg-competitor',
  'Regulator': 'msg-regulator', 'Investor': 'msg-investor', 'The Press': 'msg-press',
};

function isAgent(sender) {
  if (AGENT_NAMES.has(sender)) return true;
  if (sender === 'System') return true;
  // For messages from other scenarios — if sender isn't a known human role, treat as agent
  if (HUMAN_CLASS_MAP[sender]) return false;
  // Check if it looks like "Name (Role)" pattern used by agents vs "Name (Role)" used by humans
  // Human senders come from getSenderLabel() and use roles from the role dropdown
  // Agent senders come from persona display_name which is set in scenario config
  // If it's not in AGENT_NAMES and not in HUMAN_CLASS_MAP, check the role dropdown values
  const humanRoles = ['Consultant','Customer','New Hire','Board Member','Intern','Vendor',
    'Investor','Auditor','Competitor','Regulator','The Press','Hacker','God'];
  for (const role of humanRoles) {
    if (sender.endsWith('(' + role + ')')) return false;
  }
  // If sender has parens and isn't a known human role, likely an agent from another scenario
  if (sender.includes('(') && sender.includes(')')) return true;
  // Bare name with no parens — human with no role selected
  return false;
}

function senderClass(sender) {
  return SENDER_CLASS_MAP[sender] || HUMAN_CLASS_MAP[sender] || (isAgent(sender) ? 'msg-agent' : 'msg-customer');
}

// Generate a consistent color from a string (for human users)
function hashColor(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = ((hash % 360) + 360) % 360;
  return 'hsl(' + hue + ', 70%, 65%)';
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') return marked.parse(text);
  return escapeHtml(text);
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// -- Tabs --

document.querySelectorAll('.header-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.tab;
    if (target === currentTab) return;
    currentTab = target;
    document.querySelectorAll('.header-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    document.getElementById(target + '-pane').classList.add('active');
    if (target === 'chat') { renderSidebar(); renderMessages(); }
    if (target === 'docs') loadDocs();
    if (target === 'gitlab') loadRepos();
    if (target === 'tickets') loadTickets();
    if (target === 'npcs') loadNPCs();
    if (target === 'events') loadEventPool();
    if (target === 'email') loadEmails();
    if (target === 'memos') loadMemoThreads();
    if (target === 'blog') loadBlogPosts();
    if (target === 'recap') renderRecapList();
    if (target === 'usage') loadUsage();
    if (target === 'advanced') loadSessionManagerTable();
  });
});

// -- Channel sidebar --

async function loadChannels() {
  const resp = await fetch('/api/channels');
  const list = await resp.json();
  channelsData = {};
  list.forEach(ch => {
    channelsData[ch.name] = ch;
    if (!messagesByChannel[ch.name]) messagesByChannel[ch.name] = [];
    if (unreadByChannel[ch.name] === undefined) unreadByChannel[ch.name] = 0;
  });
  renderSidebar();
}

function renderSidebar() {
  const intContainer = document.getElementById('internal-channels');
  const extContainer = document.getElementById('external-channels');
  const dirContainer = document.getElementById('director-channels');
  const sysContainer = document.getElementById('system-channels');
  intContainer.innerHTML = '';
  extContainer.innerHTML = '';
  dirContainer.innerHTML = '';
  sysContainer.innerHTML = '';

  Object.keys(channelsData).sort().forEach(name => {
    const ch = channelsData[name];
    const btn = document.createElement('button');
    btn.className = 'channel-btn' + (name === currentChannel ? ' active' : '');
    const badge = document.createElement('span');
    badge.className = 'unread-badge' + (unreadByChannel[name] > 0 && name !== currentChannel ? ' visible' : '');
    badge.textContent = unreadByChannel[name] || '';
    badge.id = 'badge-' + name.replace('#', '');
    // Show persona display name for director channels instead of raw channel name
    const label = ch.is_director ? (PERSONA_DISPLAY[ch.director_persona] || name) : name;
    btn.innerHTML = '<span>' + escapeHtml(label) + '</span>';
    btn.appendChild(badge);
    btn.addEventListener('click', () => switchChannel(name));
    if (ch.is_system) {
      sysContainer.appendChild(btn);
    } else if (ch.is_director) {
      dirContainer.appendChild(btn);
    } else if (ch.is_external) {
      extContainer.appendChild(btn);
    } else {
      intContainer.appendChild(btn);
    }
  });
}

function switchChannel(name) {
  currentChannel = name;
  unreadByChannel[name] = 0;
  renderSidebar();
  updateChannelHeader();
  renderMessages();
  loadMessages(name);
  updateSenderDropdown();
  // Hide persona bar in director channels
  const ch = channelsData[name];
  const personaBar = document.getElementById('persona-bar');
  if (personaBar) {
    personaBar.style.display = (ch && ch.is_director) ? 'none' : '';
  }
}

function updateChannelHeader() {
  const ch = channelsData[currentChannel];
  channelTitle.textContent = currentChannel;
  channelDesc.textContent = ch ? ch.description : '';
  if (ch && ch.members && ch.members.length > 0) {
    const names = ch.members.map(k => PERSONA_DISPLAY[k] || k).join(', ');
    channelMembersEl.textContent = 'Members: ' + names;
  } else {
    channelMembersEl.textContent = '';
  }
}

function updateSenderDropdown() {
  // Sender controls are always visible — user picks name + role freely via persona bar
}

// -- Messages --

function addMessage(msg) {
  if (seenIds.has(msg.id)) return;
  seenIds.add(msg.id);
  const ch = msg.channel || '#general';
  if (!messagesByChannel[ch]) messagesByChannel[ch] = [];
  messagesByChannel[ch].push(msg);

  if (ch === currentChannel && currentTab === 'chat') {
    appendMessageEl(msg);
  } else {
    unreadByChannel[ch] = (unreadByChannel[ch] || 0) + 1;
    renderSidebar();
  }
}

function appendMessageEl(msg) {
  const div = document.createElement('div');
  const cls = senderClass(msg.sender);
  const agent = isAgent(msg.sender);
  div.className = 'msg ' + (agent ? 'msg-agent' : 'msg-customer') + ' ' + cls;
  const ts = new Date(msg.timestamp * 1000).toLocaleTimeString();
  // For human senders, derive a unique color from their name
  const senderStyle = agent ? '' : ' style="color:' + hashColor(msg.sender) + '"';
  // Build avatar element
  let avatarHtml = '';
  const pa = PERSONA_AVATARS[msg.sender];
  if (pa) {
    if (pa.avatar) {
      avatarHtml = '<div class="msg-avatar"><img src="' + escapeHtml(pa.avatar) + '" alt=""></div>';
    } else {
      avatarHtml = '<div class="msg-avatar" style="background:' + pa.color + '">' + pa.initial + '</div>';
    }
  } else if (msg.sender === 'System') {
    avatarHtml = '<div class="msg-avatar" style="background:#666">S</div>';
  } else {
    // Human sender fallback
    const hc = hashColor(msg.sender);
    const hi = msg.sender.charAt(0).toUpperCase();
    avatarHtml = '<div class="msg-avatar" style="background:' + hc + '">' + hi + '</div>';
  }
  div.innerHTML = '<div class="msg-row">' + avatarHtml
    + '<div class="msg-body">'
    + '<div class="sender"' + senderStyle + '>' + escapeHtml(msg.sender) + '</div>'
    + '<div class="content">' + renderMarkdown(msg.content) + '</div>'
    + '<div class="ts">' + ts + '</div>'
    + '</div></div>';
  messagesPanel.appendChild(div);
  messagesPanel.scrollTop = messagesPanel.scrollHeight;
}

function renderMessages() {
  messagesPanel.innerHTML = '';
  const msgs = messagesByChannel[currentChannel] || [];
  msgs.forEach(appendMessageEl);
  renderTypingIndicators();
}

// -- Typing indicators --
const _typingState = {};  // channel -> {sender -> timestamp}

function handleTypingIndicator(data) {
  const ch = data.channel || '#general';
  if (!_typingState[ch]) _typingState[ch] = {};
  if (data.active) {
    _typingState[ch][data.sender] = Date.now();
  } else {
    delete _typingState[ch][data.sender];
  }
  if (ch === currentChannel) renderTypingIndicators();
}

function renderTypingIndicators() {
  let el = document.getElementById('typing-indicator');
  if (!el) {
    el = document.createElement('div');
    el.id = 'typing-indicator';
    el.style.cssText = 'padding:4px 20px;font-size:12px;color:var(--text-dim);font-style:italic;min-height:18px;';
    messagesPanel.parentNode.insertBefore(el, messagesPanel.nextSibling);
  }
  const typers = _typingState[currentChannel] || {};
  // Clean stale entries (older than 60s)
  const now = Date.now();
  for (const [sender, ts] of Object.entries(typers)) {
    if (now - ts > 60000) delete typers[sender];
  }
  const names = Object.keys(typers);
  if (names.length === 0) {
    el.textContent = '';
  } else if (names.length === 1) {
    el.textContent = names[0] + ' is thinking...';
  } else if (names.length === 2) {
    el.textContent = names[0] + ' and ' + names[1] + ' are thinking...';
  } else {
    el.textContent = names.slice(0, -1).join(', ') + ', and ' + names[names.length-1] + ' are thinking...';
  }
}

// Clean stale typing indicators every 10s
setInterval(() => { if (currentTab === 'chat') renderTypingIndicators(); }, 10000);

async function loadMessages(channel) {
  let url = '/api/messages';
  if (channel) url += '?channels=' + encodeURIComponent(channel);
  const resp = await fetch(url);
  const msgs = await resp.json();
  msgs.forEach(addMessage);
}

function connectSSE() {
  const es = new EventSource('/api/messages/stream');
  es.addEventListener('message', (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'channel_update') {
      if (channelsData[data.channel]) {
        channelsData[data.channel].members = data.members;
        if (data.channel === currentChannel) updateChannelHeader();
      }
    } else if (data.type === 'doc_event') {
      if (currentTab === 'docs') loadDocs();
    } else if (data.type === 'gitlab_event') {
      if (currentTab === 'gitlab') loadRepos();
    } else if (data.type === 'tickets_event') {
      if (currentTab === 'tickets') {
        loadTickets();
        if (tkCurrentViewId) viewTicket(tkCurrentViewId);
      }
    } else if (data.type === 'typing') {
      handleTypingIndicator(data);
    } else {
      addMessage(data);
    }
  });
  es.onopen = () => { loadMessages(); };
  es.onerror = () => { setTimeout(connectSSE, 2000); es.close(); };
}

async function send() {
  const content = input.value.trim();
  if (!content) return;
  const ch = channelsData[currentChannel];
  const sender = (ch && ch.is_director) ? 'Scenario Director' : getSenderLabel();
  input.value = '';
  await fetch('/api/messages', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sender, content, channel: currentChannel}),
  });
}

async function clearChat() {
  if (!confirm('Clear all messages?')) return;
  await fetch('/api/messages/clear', {method: 'POST'});
  messagesByChannel = {};
  Object.keys(channelsData).forEach(ch => messagesByChannel[ch] = []);
  seenIds.clear();
  unreadByChannel = {};
  renderSidebar();
  renderMessages();
}

sendBtn.addEventListener('click', send);
// clear-btn removed — now in Advanced tab
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

// -- Docs tab --
const docsList = document.getElementById('docs-list');
const docsEmpty = document.getElementById('docs-empty');
const docsSearch = document.getElementById('docs-search');
const docViewer = document.getElementById('doc-viewer');
const docViewerTitle = document.getElementById('doc-viewer-title');
const docViewerContent = document.getElementById('doc-viewer-content');
const docBackBtn = document.getElementById('doc-back-btn');

let currentFolder = '';  // '' means all folders
let foldersData = [];

async function loadFolders() {
  const resp = await fetch('/api/folders');
  foldersData = await resp.json();
  renderFolderSidebar();
}

function renderFolderSidebar() {
  const sharedC = document.getElementById('shared-folders');
  const deptC = document.getElementById('dept-folders');
  const persC = document.getElementById('personal-folders');
  sharedC.innerHTML = ''; deptC.innerHTML = ''; persC.innerHTML = '';

  foldersData.forEach(f => {
    const btn = document.createElement('button');
    btn.className = 'folder-btn' + (currentFolder === f.name ? ' active' : '');
    btn.dataset.folder = f.name;
    btn.textContent = f.name;
    btn.addEventListener('click', () => switchFolder(f.name));
    if (f.type === 'shared' || f.type === 'public') sharedC.appendChild(btn);
    else if (f.type === 'department') deptC.appendChild(btn);
    else if (f.type === 'personal') persC.appendChild(btn);
  });

  // Update "All" button
  const allBtn = document.getElementById('folder-all');
  allBtn.className = 'folder-btn' + (currentFolder === '' ? ' active' : '');
}

function switchFolder(folderName) {
  currentFolder = folderName;
  renderFolderSidebar();
  loadDocs();
}

document.getElementById('folder-all').addEventListener('click', () => switchFolder(''));

async function loadDocs(query) {
  let url = '/api/docs';
  const params = [];
  if (query) {
    url = '/api/docs/search';
    params.push('q=' + encodeURIComponent(query));
    if (currentFolder) params.push('folders=' + encodeURIComponent(currentFolder));
  } else if (currentFolder) {
    params.push('folder=' + encodeURIComponent(currentFolder));
  }
  if (params.length) url += '?' + params.join('&');
  const resp = await fetch(url);
  const docs = await resp.json();
  renderDocList(docs);
}

function renderDocList(docs) {
  docsList.querySelectorAll('.doc-card').forEach(el => el.remove());
  docsEmpty.style.display = docs.length ? 'none' : 'block';
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  docs.forEach(doc => {
    const card = document.createElement('div');
    card.className = 'doc-card';
    const folder = doc.folder || 'shared';
    const author = doc.created_by || '';
    const created = doc.created_at ? new Date(doc.created_at * 1000).toLocaleString() : '';
    const updated = doc.updated_at && doc.updated_at !== doc.created_at ? new Date(doc.updated_at * 1000).toLocaleString() : '';
    const editedBy = doc.updated_by || '';
    let dateLine = created ? 'Created ' + created : '';
    if (updated) dateLine += (dateLine ? ' | ' : '') + 'Edited ' + updated + (editedBy ? ' by ' + escapeHtml(editedBy) : '');
    card.innerHTML = '<div class="doc-card-meta">'
      + '<span class="doc-card-folder">' + escapeHtml(folder) + '</span>'
      + (author ? '<span style="font-size:11px;color:var(--text-dim)">' + escapeHtml(author) + '</span>' : '')
      + '</div>'
      + '<div class="doc-card-title">' + escapeHtml(doc.title || doc.slug) + '</div>'
      + (dateLine ? '<div style="font-size:10px;color:var(--text-dimmer);margin-bottom:4px">' + dateLine + '</div>' : '')
      + '<div class="doc-card-preview">' + escapeHtml(doc.preview || '') + '</div>';
    card.addEventListener('click', () => viewDoc(folder, doc.slug));
    docsList.appendChild(card);
  });
}

let _currentDoc = null; // {folder, slug, content, ...}

async function viewDoc(folder, slug) {
  const resp = await fetch('/api/docs/' + encodeURIComponent(folder) + '/' + encodeURIComponent(slug));
  if (!resp.ok) return;
  _currentDoc = await resp.json();
  _currentDoc.folder = folder;
  _currentDoc.slug = slug;
  const createdBy = _currentDoc.created_by || '';
  const updatedBy = _currentDoc.updated_by || '';
  const createdAt = _currentDoc.created_at ? new Date(_currentDoc.created_at * 1000).toLocaleString() : '';
  const updatedAt = _currentDoc.updated_at ? new Date(_currentDoc.updated_at * 1000).toLocaleString() : '';
  let meta = createdBy ? 'Created by ' + createdBy : '';
  if (createdAt) meta += (meta ? ' on ' : '') + createdAt;
  if (updatedBy && updatedBy !== createdBy) meta += ' | Edited by ' + updatedBy + ' on ' + updatedAt;
  else if (updatedAt && updatedAt !== createdAt) meta += ' | Updated ' + updatedAt;
  docViewerTitle.innerHTML = escapeHtml(_currentDoc.title || _currentDoc.slug) +
    (meta ? '<div style="font-size:11px;color:var(--text-dim);font-weight:400;margin-top:2px">' + escapeHtml(meta) + '</div>' : '');
  document.getElementById('doc-viewer-content').innerHTML = renderMarkdown(_currentDoc.content || '');
  document.getElementById('doc-viewer-body').style.display = 'flex';
  document.getElementById('doc-edit-area').style.display = 'none';
  docViewer.classList.add('open');
  docsList.style.display = 'none';
  document.getElementById('docs-toolbar').style.display = 'none';
  // Auto-show history panel
  loadDocHistory();
}

docBackBtn.addEventListener('click', () => {
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  _currentDoc = null;
});

// Edit button
document.getElementById('doc-edit-btn').addEventListener('click', () => {
  if (!_currentDoc) return;
  document.getElementById('doc-edit-textarea').value = _currentDoc.content || '';
  document.getElementById('doc-edit-author-name').value = senderName.value;
  document.getElementById('doc-edit-author-role').value = senderRole.value || '';
  document.getElementById('doc-viewer-body').style.display = 'none';
  document.getElementById('doc-edit-area').style.display = 'flex';
});

document.getElementById('doc-edit-cancel').addEventListener('click', () => {
  document.getElementById('doc-edit-area').style.display = 'none';
  document.getElementById('doc-viewer-body').style.display = 'flex';
});

document.getElementById('doc-edit-save').addEventListener('click', async () => {
  if (!_currentDoc) return;
  const content = document.getElementById('doc-edit-textarea').value;
  const editName = document.getElementById('doc-edit-author-name').value.trim() || 'Anonymous';
  const editRole = document.getElementById('doc-edit-author-role').value;
  const author = editRole ? editName + ' (' + editRole + ')' : editName;
  const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content, author}),
  });
  if (resp.ok) {
    // Reload the doc
    await viewDoc(_currentDoc.folder, _currentDoc.slug);
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

// History panel
async function loadDocHistory() {
  if (!_currentDoc) return;
  const panel = document.getElementById('doc-history-panel');
  const list = document.getElementById('doc-history-list');
  list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">Loading...</div>';
  panel.style.display = '';
  const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug) + '/history');
  const history = await resp.json();
  list.innerHTML = '';
  if (!history.length) {
    list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">No version history</div>';
    return;
  }
  history.forEach((v, i) => {
    const item = document.createElement('div');
    item.className = 'thought-item' + (i === 0 ? ' active' : '');
    const ts = new Date(v.updated_at * 1000);
    const label = v.is_current ? 'Current' : 'v' + (history.length - i);
    item.innerHTML = '<div class="thought-item-time">' + escapeHtml(label) + ' - ' + ts.toLocaleString() + '</div>' +
      '<div class="thought-item-preview">' + escapeHtml(v.updated_by || 'unknown') + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('#doc-history-list .thought-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('doc-viewer-content').innerHTML = renderMarkdown(v.content || '');
    });
    if (!v.is_current) {
      const restoreBtn = document.createElement('button');
      restoreBtn.className = 'session-btn';
      restoreBtn.style.cssText = 'font-size:10px;padding:2px 8px;margin-top:4px;width:100%';
      restoreBtn.textContent = 'Restore this version';
      restoreBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug), {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({content: v.content, author: 'Restored by Scenario Director'}),
        });
        if (resp.ok) {
          await viewDoc(_currentDoc.folder, _currentDoc.slug);
        }
      });
      item.appendChild(restoreBtn);
    }
    list.appendChild(item);
  });
}

document.getElementById('doc-history-btn').addEventListener('click', () => {
  const panel = document.getElementById('doc-history-panel');
  if (panel.style.display !== 'none') {
    panel.style.display = 'none';
  } else {
    loadDocHistory();
  }
});

let docsSearchTimer = null;
docsSearch.addEventListener('input', () => {
  clearTimeout(docsSearchTimer);
  docsSearchTimer = setTimeout(() => {
    const q = docsSearch.value.trim();
    loadDocs(q || undefined);
  }, 300);
});

// -- Doc editor --
const docEditor = document.getElementById('doc-editor');
const docEditorTitle = document.getElementById('doc-editor-title');
const docEditorFolder = document.getElementById('doc-editor-folder');
const docEditorContent = document.getElementById('doc-editor-content');

document.getElementById('new-doc-btn').addEventListener('click', () => {
  // Populate folder dropdown from sidebar folders
  docEditorFolder.innerHTML = '';
  const allFolders = foldersData.map(f => f.name).sort();
  allFolders.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    if (f === currentFolder || (!currentFolder && f === 'shared')) opt.selected = true;
    docEditorFolder.appendChild(opt);
  });
  docEditorTitle.value = '';
  docEditorContent.value = '';
  // Pre-fill author from persona bar
  document.getElementById('doc-author-name').value = senderName.value;
  document.getElementById('doc-author-role').value = senderRole.value || '';
  docEditor.style.display = 'flex';
  docsList.style.display = 'none';
  document.getElementById('docs-toolbar').style.display = 'none';
  docViewer.classList.remove('open');
  docEditorTitle.focus();
});

document.getElementById('doc-editor-cancel').addEventListener('click', () => {
  docEditor.style.display = 'none';
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
});

document.getElementById('doc-editor-save').addEventListener('click', async () => {
  const title = docEditorTitle.value.trim();
  const content = docEditorContent.value;
  const folder = docEditorFolder.value;
  if (!title) { alert('Title is required'); return; }
  const docName = document.getElementById('doc-author-name').value.trim() || 'Anonymous';
  const docRole = document.getElementById('doc-author-role').value;
  const author = docRole ? docName + ' (' + docRole + ')' : docName;
  const resp = await fetch('/api/docs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, content, folder, author}),
  });
  if (!resp.ok) {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
    return;
  }
  docEditor.style.display = 'none';
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  loadDocs();
});

// -- GitLab tab --
let glRepos = [];
let glCurrentRepo = null;
let glCurrentView = 'tree';
let glCurrentPath = '';

async function loadRepos() {
  const resp = await fetch('/api/gitlab/repos');
  glRepos = await resp.json();
  renderRepoSidebar();
  if (glCurrentRepo) {
    const exists = glRepos.find(r => r.name === glCurrentRepo);
    if (!exists) { glCurrentRepo = null; glCurrentPath = ''; }
  }
  if (glCurrentRepo) {
    if (glCurrentView === 'tree') loadTree(glCurrentRepo, glCurrentPath);
    else loadCommits(glCurrentRepo);
  } else {
    document.getElementById('gitlab-content').innerHTML =
      '<div id="gitlab-empty">No repositories yet.</div>';
    document.getElementById('gitlab-repo-title').textContent = 'Select a repository';
    document.getElementById('gitlab-repo-desc').textContent = '';
  }
}

function renderRepoSidebar() {
  const container = document.getElementById('gitlab-repo-list');
  container.innerHTML = '';
  glRepos.forEach(repo => {
    const btn = document.createElement('button');
    btn.className = 'repo-btn' + (glCurrentRepo === repo.name ? ' active' : '');
    btn.textContent = repo.name;
    btn.addEventListener('click', () => switchRepo(repo.name));
    container.appendChild(btn);
  });
}

function switchRepo(name) {
  glCurrentRepo = name;
  glCurrentPath = '';
  glCurrentView = 'tree';
  renderRepoSidebar();
  updateGlToggles();
  const repo = glRepos.find(r => r.name === name);
  document.getElementById('gitlab-repo-title').textContent = name;
  document.getElementById('gitlab-repo-desc').textContent = repo ? (repo.description || '') : '';
  loadTree(name, '');
}

function updateGlToggles() {
  document.getElementById('gl-toggle-tree').className =
    'gitlab-toggle-btn' + (glCurrentView === 'tree' ? ' active' : '');
  document.getElementById('gl-toggle-commits').className =
    'gitlab-toggle-btn' + (glCurrentView === 'commits' ? ' active' : '');
}

document.getElementById('gl-toggle-tree').addEventListener('click', () => {
  if (!glCurrentRepo) return;
  glCurrentView = 'tree'; glCurrentPath = ''; updateGlToggles();
  loadTree(glCurrentRepo, '');
});
document.getElementById('gl-toggle-commits').addEventListener('click', () => {
  if (!glCurrentRepo) return;
  glCurrentView = 'commits'; updateGlToggles();
  loadCommits(glCurrentRepo);
});

async function loadTree(project, path) {
  let url = '/api/gitlab/repos/' + encodeURIComponent(project) + '/tree';
  if (path) url += '?path=' + encodeURIComponent(path);
  const resp = await fetch(url);
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error loading tree.</div>'; return; }
  const data = await resp.json();
  renderTree(data, path);
}

function renderTree(entries, path) {
  const content = document.getElementById('gitlab-content');
  let html = '';
  // Breadcrumbs
  const parts = path ? path.split('/') : [];
  html += '<div class="gitlab-breadcrumbs">';
  html += '<a onclick="glNavTree(\\'\\')">root</a>';
  let acc = '';
  parts.forEach((p, i) => {
    acc += (i > 0 ? '/' : '') + p;
    html += ' / <a onclick="glNavTree(\\'' + acc + '\\')">' + escapeHtml(p) + '</a>';
  });
  html += '</div>';
  // Entries
  if (entries.length === 0) {
    html += '<div id="gitlab-empty">Empty directory.</div>';
  }
  entries.forEach(e => {
    const isDir = e.type === 'dir';
    const icon = isDir ? '&#128193;' : '&#128196;';
    html += '<div class="tree-item" onclick="glClickEntry(\\'' + escapeHtml(e.path) + '\\', \\'' + e.type + '\\')">'
      + '<span class="tree-item-icon">' + icon + '</span>'
      + '<span class="tree-item-name">' + escapeHtml(e.name) + '</span></div>';
  });
  content.innerHTML = html;
}

function glNavTree(path) { glCurrentPath = path; loadTree(glCurrentRepo, path); }
function glClickEntry(path, type) {
  if (type === 'dir') { glCurrentPath = path; loadTree(glCurrentRepo, path); }
  else { loadFileContent(glCurrentRepo, path); }
}

async function loadFileContent(project, path) {
  const resp = await fetch('/api/gitlab/repos/' + encodeURIComponent(project) + '/file?path=' + encodeURIComponent(path));
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error reading file.</div>'; return; }
  const data = await resp.json();
  const content = document.getElementById('gitlab-content');
  const parts = path.split('/');
  let html = '<div class="gitlab-breadcrumbs">';
  html += '<a onclick="glNavTree(\\'\\')">root</a>';
  let acc = '';
  parts.forEach((p, i) => {
    acc += (i > 0 ? '/' : '') + p;
    if (i < parts.length - 1) {
      html += ' / <a onclick="glNavTree(\\'' + acc + '\\')">' + escapeHtml(p) + '</a>';
    } else {
      html += ' / ' + escapeHtml(p);
    }
  });
  html += '</div>';
  html += '<div class="gitlab-file-viewer">' + escapeHtml(data.content || '') + '</div>';
  content.innerHTML = html;
}

async function loadCommits(project) {
  const resp = await fetch('/api/gitlab/repos/' + encodeURIComponent(project) + '/log');
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error loading commits.</div>'; return; }
  const commits = await resp.json();
  renderCommits(commits);
}

function renderCommits(commits) {
  const content = document.getElementById('gitlab-content');
  if (commits.length === 0) {
    content.innerHTML = '<div id="gitlab-empty">No commits yet.</div>';
    return;
  }
  let html = '';
  commits.forEach(c => {
    const ts = new Date(c.timestamp * 1000).toLocaleString();
    html += '<div class="commit-item">'
      + '<span class="commit-item-id">' + escapeHtml(c.id) + '</span>'
      + '<span class="commit-item-msg">' + escapeHtml(c.message) + '</span>'
      + '<div class="commit-item-meta">' + escapeHtml(c.author) + ' - ' + ts
      + ' - ' + (c.files ? c.files.length : 0) + ' file(s)</div></div>';
  });
  content.innerHTML = html;
}

// -- GitLab new repo --
document.getElementById('gl-new-repo-btn').addEventListener('click', () => {
  document.getElementById('gl-new-repo-form').style.display = '';
  document.getElementById('gl-new-repo-btn').style.display = 'none';
  document.getElementById('gl-new-repo-name').value = '';
  document.getElementById('gl-new-repo-desc').value = '';
  document.getElementById('gl-new-repo-name').focus();
});

document.getElementById('gl-new-repo-cancel').addEventListener('click', () => {
  document.getElementById('gl-new-repo-form').style.display = 'none';
  document.getElementById('gl-new-repo-btn').style.display = '';
});

document.getElementById('gl-new-repo-save').addEventListener('click', async () => {
  const name = document.getElementById('gl-new-repo-name').value.trim();
  if (!name) return;
  const desc = document.getElementById('gl-new-repo-desc').value.trim();
  const author = getSenderLabel();
  const resp = await fetch('/api/gitlab/repos', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description: desc, author}),
  });
  if (resp.ok) {
    document.getElementById('gl-new-repo-form').style.display = 'none';
    document.getElementById('gl-new-repo-btn').style.display = '';
    loadRepos();
    switchRepo(name);
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

document.getElementById('gl-new-repo-name').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('gl-new-repo-save').click();
  if (e.key === 'Escape') document.getElementById('gl-new-repo-cancel').click();
});

// -- Tickets tab --
let tkAllTickets = [];
let tkStatusFilter = '';

document.querySelectorAll('.tickets-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    tkStatusFilter = btn.dataset.status;
    document.querySelectorAll('.tickets-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTicketList();
  });
});

async function loadTickets() {
  const resp = await fetch('/api/tickets');
  tkAllTickets = await resp.json();
  updateTicketCounts();
  renderTicketList();
}

function updateTicketCounts() {
  const counts = { all: tkAllTickets.length, open: 0, in_progress: 0, resolved: 0, closed: 0 };
  tkAllTickets.forEach(t => { if (counts[t.status] !== undefined) counts[t.status]++; });
  ['all', 'open', 'in_progress', 'resolved', 'closed'].forEach(s => {
    const el = document.getElementById('tk-count-' + s);
    if (el) el.textContent = counts[s] > 0 ? '(' + counts[s] + ')' : '';
  });
}

function renderTicketList() {
  const list = document.getElementById('tickets-list');
  list.querySelectorAll('.ticket-card').forEach(el => el.remove());
  const empty = document.getElementById('tickets-empty');
  const detail = document.getElementById('ticket-detail');
  detail.classList.remove('open');
  list.style.display = '';

  let filtered = tkAllTickets;
  if (tkStatusFilter) filtered = filtered.filter(t => t.status === tkStatusFilter);

  // Sort: critical > high > medium > low, then by updated_at desc
  const priOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  filtered.sort((a, b) => (priOrder[a.priority] || 3) - (priOrder[b.priority] || 3) || b.updated_at - a.updated_at);

  empty.style.display = filtered.length ? 'none' : 'block';
  filtered.forEach(t => {
    const card = document.createElement('div');
    card.className = 'ticket-card';
    const assignee = t.assignee ? t.assignee : 'Unassigned';
    card.innerHTML = '<div class="ticket-card-top">'
      + '<span class="ticket-card-id">' + escapeHtml(t.id) + '</span>'
      + '<span class="ticket-card-title">' + escapeHtml(t.title) + '</span>'
      + '</div>'
      + '<div class="ticket-card-bottom">'
      + '<span class="tk-badge tk-status-' + t.status + '">' + escapeHtml(t.status) + '</span>'
      + '<span class="tk-badge tk-priority-' + t.priority + '">' + escapeHtml(t.priority) + '</span>'
      + '<span class="tk-assignee">' + escapeHtml(assignee) + '</span>'
      + '</div>';
    card.addEventListener('click', () => viewTicket(t.id));
    list.appendChild(card);
  });
}

let tkCurrentViewId = null;

// Built dynamically from PERSONA_DISPLAY after loadPersonas()
let TK_ASSIGNEE_OPTIONS = [''];

let HUMAN_ROLES = [];
let JOB_TITLES = [];

async function loadRoles() {
  try {
    const resp = await fetch('/api/roles');
    const data = await resp.json();
    HUMAN_ROLES = data.human_roles || [];
    JOB_TITLES = data.job_titles || [];
  } catch(e) {
    HUMAN_ROLES = ['Scenario Director', 'Consultant', 'Customer'];
    JOB_TITLES = ['PM', 'Senior Eng'];
  }
  populateAllRoleDropdowns();
}

function populateRoleSelect(sel, roles, opts) {
  if (!sel) return;
  const defaultVal = opts?.default || '';
  const includeEmpty = opts?.empty;
  const emptyLabel = opts?.emptyLabel || '';
  sel.innerHTML = '';
  if (includeEmpty) {
    sel.innerHTML += '<option value="">' + escapeHtml(emptyLabel) + '</option>';
  }
  roles.forEach(role => {
    const selected = role === defaultVal ? ' selected' : '';
    sel.innerHTML += '<option value="' + escapeHtml(role) + '"' + selected + '>' + escapeHtml(role) + '</option>';
  });
}

function populateAllRoleDropdowns() {
  const agentNames = Object.values(PERSONA_DISPLAY);

  // Build assignee options
  TK_ASSIGNEE_OPTIONS = [''];
  agentNames.forEach(name => TK_ASSIGNEE_OPTIONS.push(name));

  // Chat persona bar role
  const senderRoleSel = document.getElementById('sender-role');
  if (senderRoleSel) {
    const customOpt = '<option value="custom">Custom...</option>';
    populateRoleSelect(senderRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});
    senderRoleSel.innerHTML += customOpt;
  }

  // Doc creation author role
  populateRoleSelect(document.getElementById('doc-author-role'), HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});
  // Doc edit author role
  populateRoleSelect(document.getElementById('doc-edit-author-role'), HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});

  // Ticket creation - assignee (agents only)
  const assigneeSel = document.getElementById('tk-form-assignee');
  if (assigneeSel) {
    assigneeSel.innerHTML = '<option value="">Unassigned</option>';
    agentNames.forEach(name => {
      assigneeSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Ticket creation - author (human roles + agents)
  const authorSel = document.getElementById('tk-form-author');
  if (authorSel) {
    populateRoleSelect(authorSel, HUMAN_ROLES, {default: 'Scenario Director'});
    agentNames.forEach(name => {
      authorSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Ticket detail - acting as (human roles + agents)
  const actingSel = document.getElementById('tk-acting-as');
  if (actingSel) {
    populateRoleSelect(actingSel, HUMAN_ROLES, {default: 'Scenario Director'});
    agentNames.forEach(name => {
      actingSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Hire modal - role (job titles)
  const hireSel = document.getElementById('hire-role-preset');
  if (hireSel) {
    hireSel.innerHTML = '<option value="">Role...</option>';
    JOB_TITLES.forEach(title => {
      hireSel.innerHTML += '<option value="' + escapeHtml(title) + '">' + escapeHtml(title) + '</option>';
    });
    hireSel.innerHTML += '<option value="other">Other...</option>';
  }

  // Email compose role
  const emailRoleSel = document.getElementById('email-compose-role');
  if (emailRoleSel) {
    populateRoleSelect(emailRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    emailRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Memo reply role
  const memoRoleSel = document.getElementById('memo-reply-role');
  if (memoRoleSel) {
    populateRoleSelect(memoRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    memoRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Memo create role
  const memoCreateRoleSel = document.getElementById('memo-create-role');
  if (memoCreateRoleSel) {
    populateRoleSelect(memoCreateRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    memoCreateRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Blog reply role
  const blogReplySel = document.getElementById('blog-reply-role');
  if (blogReplySel) {
    populateRoleSelect(blogReplySel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    blogReplySel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Blog create role
  const blogCreateRoleSel = document.getElementById('blog-create-role');
  if (blogCreateRoleSel) {
    populateRoleSelect(blogCreateRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    blogCreateRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }
}

function toggleCreateForm() {
  const form = document.getElementById('tk-create-form');
  form.classList.toggle('open');
  if (form.classList.contains('open')) {
    // Populate notify channel dropdown
    const notify = document.getElementById('tk-form-notify');
    notify.innerHTML = '<option value="">Don\\'t notify</option>';
    Object.keys(channelsData).sort().forEach(ch => {
      if (!channelsData[ch].is_system && !channelsData[ch].is_director) {
        notify.innerHTML += '<option value="' + escapeHtml(ch) + '">' + escapeHtml(ch) + '</option>';
      }
    });
    document.getElementById('tk-form-title').focus();
  }
}

async function submitCreateTicket() {
  const title = document.getElementById('tk-form-title').value.trim();
  if (!title) { document.getElementById('tk-form-title').focus(); return; }
  const priority = document.getElementById('tk-form-priority').value;
  const assignee = document.getElementById('tk-form-assignee').value;
  const description = document.getElementById('tk-form-desc').value.trim();
  const author = document.getElementById('tk-form-author').value;
  const notifyChannel = document.getElementById('tk-form-notify').value;
  const resp = await fetch('/api/tickets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ title, description, priority, assignee, author }),
  });
  // Post notification to selected channel
  if (notifyChannel && resp.ok) {
    const ticket = await resp.json();
    let msg = 'New ticket **' + ticket.id + '**: ' + title;
    if (assignee) msg += ' (assigned to ' + assignee + ')';
    if (priority && priority !== 'medium') msg += ' [' + priority + ']';
    await fetch('/api/messages', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ sender: author || 'System', content: msg, channel: notifyChannel }),
    });
  }
  document.getElementById('tk-form-title').value = '';
  document.getElementById('tk-form-desc').value = '';
  document.getElementById('tk-form-priority').value = 'medium';
  document.getElementById('tk-form-assignee').value = '';
  document.getElementById('tk-form-notify').value = '';
  document.getElementById('tk-create-form').classList.remove('open');
  loadTickets();
}

function tkActingAs() {
  const sel = document.getElementById('tk-acting-as');
  return sel ? sel.value : 'Consultant';
}

async function tkUpdateStatus(ticketId, newStatus) {
  await fetch('/api/tickets/' + encodeURIComponent(ticketId), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ status: newStatus, author: tkActingAs() }),
  });
  loadTickets();
  viewTicket(ticketId);
}

async function tkAssign(ticketId) {
  const sel = document.getElementById('tk-assign-select');
  if (!sel) return;
  await fetch('/api/tickets/' + encodeURIComponent(ticketId), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ assignee: sel.value, author: tkActingAs() }),
  });
  loadTickets();
  viewTicket(ticketId);
}

async function tkAddComment(ticketId) {
  const input = document.getElementById('tk-comment-new');
  if (!input) return;
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  await fetch('/api/tickets/' + encodeURIComponent(ticketId) + '/comment', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text, author: tkActingAs() }),
  });
  input.value = '';
  viewTicket(ticketId);
}

async function viewTicket(ticketId) {
  const resp = await fetch('/api/tickets/' + encodeURIComponent(ticketId));
  if (!resp.ok) return;
  const t = await resp.json();
  tkCurrentViewId = t.id;
  document.getElementById('ticket-detail-title').textContent = t.title;
  document.getElementById('ticket-detail-id').textContent = t.id;

  let html = '';

  // -- Action buttons --
  html += '<div class="tk-detail-actions">';
  if (t.status === 'open') {
    html += '<button class="tk-action-btn primary" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'in_progress\\')">Start Work</button>';
    html += '<button class="tk-action-btn success" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'resolved\\')">Resolve</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'in_progress') {
    html += '<button class="tk-action-btn success" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'resolved\\')">Resolve</button>';
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'resolved') {
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'closed') {
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
  }
  // Assign dropdown
  html += '<div class="tk-assign-row" style="margin-left:auto;">';
  html += '<select class="tk-assign-select" id="tk-assign-select">';
  TK_ASSIGNEE_OPTIONS.forEach(name => {
    const label = name || 'Unassigned';
    const sel = name === (t.assignee || '') ? ' selected' : '';
    html += '<option value="' + escapeHtml(name) + '"' + sel + '>' + escapeHtml(label) + '</option>';
  });
  html += '</select>';
  html += '<button class="tk-action-btn" onclick="tkAssign(\\'' + t.id + '\\')">Assign</button>';
  html += '</div>';
  html += '</div>';

  // -- Meta info --
  html += '<div class="tk-detail-meta">';
  html += '<span class="tk-detail-field"><strong>Status:</strong> <span class="tk-badge tk-status-' + t.status + '">' + escapeHtml(t.status) + '</span></span>';
  html += '<span class="tk-detail-field"><strong>Priority:</strong> <span class="tk-badge tk-priority-' + t.priority + '">' + escapeHtml(t.priority) + '</span></span>';
  html += '<span class="tk-detail-field"><strong>Assignee:</strong> ' + escapeHtml(t.assignee || 'Unassigned') + '</span>';
  html += '<span class="tk-detail-field"><strong>Created by:</strong> ' + escapeHtml(t.created_by) + '</span>';
  const created = new Date(t.created_at * 1000).toLocaleString();
  const updated = new Date(t.updated_at * 1000).toLocaleString();
  html += '<span class="tk-detail-field"><strong>Created:</strong> ' + created + '</span>';
  html += '<span class="tk-detail-field"><strong>Updated:</strong> ' + updated + '</span>';
  html += '</div>';

  if (t.description) {
    html += '<div class="tk-detail-desc">' + escapeHtml(t.description) + '</div>';
  }

  if (t.blocked_by && t.blocked_by.length > 0) {
    html += '<div class="tk-detail-deps"><strong>Blocked by:</strong> ';
    html += t.blocked_by.map(id => '<span onclick="viewTicket(\\'' + escapeHtml(id) + '\\')">' + escapeHtml(id) + '</span>').join(', ');
    html += '</div>';
  }
  if (t.blocks && t.blocks.length > 0) {
    html += '<div class="tk-detail-deps"><strong>Blocks:</strong> ';
    html += t.blocks.map(id => '<span onclick="viewTicket(\\'' + escapeHtml(id) + '\\')">' + escapeHtml(id) + '</span>').join(', ');
    html += '</div>';
  }

  // -- Comments --
  const comments = t.comments || [];
  html += '<div class="tk-comments-header">Comments (' + comments.length + ')</div>';
  comments.forEach(c => {
    const ctime = new Date(c.timestamp * 1000).toLocaleString();
    html += '<div class="tk-comment">'
      + '<span class="tk-comment-author">' + escapeHtml(c.author) + '</span>'
      + '<span class="tk-comment-time">' + ctime + '</span>'
      + '<div class="tk-comment-text">' + escapeHtml(c.text) + '</div></div>';
  });

  // -- Comment input --
  html += '<div class="tk-comment-input-area">';
  html += '<textarea class="tk-comment-input" id="tk-comment-new" placeholder="Add a comment..."></textarea>';
  html += '<button class="tk-comment-submit" onclick="tkAddComment(\\'' + t.id + '\\')">Comment</button>';
  html += '</div>';

  document.getElementById('ticket-detail-content').innerHTML = html;
  document.getElementById('ticket-detail').classList.add('open');
  document.getElementById('tickets-list').style.display = 'none';
}

document.getElementById('ticket-back-btn').addEventListener('click', () => {
  document.getElementById('ticket-detail').classList.remove('open');
  document.getElementById('tickets-list').style.display = '';
  tkCurrentViewId = null;
});

// -- Init --
loadPersonas().then(() => {
  loadRoles().then(() => {
    loadChannels().then(() => {
      updateChannelHeader();
      updateSenderDropdown();
      loadMessages();
      connectSSE();
    });
  });
});
loadFolders();
loadRepos();
loadTickets();

// -- NPCs tab --

const TIER_LABELS = {1: 'Tier 1 — ICs', 2: 'Tier 2 — Managers', 3: 'Tier 3 — Executives'};

async function loadNPCs() {
  const container = document.getElementById('npcs-content');
  const empty = document.getElementById('npcs-empty');
  const resp = await fetch('/api/npcs');
  const npcs = await resp.json();
  // Update sidebar summary
  const summaryEl = document.getElementById('npcs-summary');
  const scenarioEl = document.getElementById('npcs-scenario-info');
  if (npcs.length === 0) {
    container.innerHTML = '';
    container.appendChild(empty);
    empty.style.display = 'block';
    summaryEl.textContent = '';
    scenarioEl.textContent = 'No scenario loaded';
    return;
  }
  const readyCount = npcs.filter(n => n.live_state === 'ready').length;
  const startingCount = npcs.filter(n => n.live_state === 'starting').length;
  const respondingCount = npcs.filter(n => n.live_state === 'responding').length;
  const oooCount = npcs.filter(n => !n.online).length;
  const disconnectedCount = npcs.filter(n => n.online && n.live_state === 'disconnected').length;
  scenarioEl.innerHTML = '<strong>' + npcs.length + ' agents</strong>';
  let summaryHtml = '';
  if (readyCount > 0) summaryHtml += '<div style="color:#2ecc71">Ready: ' + readyCount + '</div>';
  if (respondingCount > 0) summaryHtml += '<div style="color:#3498db">Responding: ' + respondingCount + '</div>';
  if (startingCount > 0) summaryHtml += '<div style="color:#f39c12">Starting: ' + startingCount + '</div>';
  if (oooCount > 0) summaryHtml += '<div style="color:var(--text-dim)">Out of office: ' + oooCount + '</div>';
  if (disconnectedCount > 0) summaryHtml += '<div style="color:var(--text-dimmer)">Disconnected: ' + disconnectedCount + '</div>';
  if (!summaryHtml) summaryHtml = '<div style="color:var(--text-dimmer)">No agents active</div>';
  summaryEl.innerHTML = summaryHtml;
  // Group by tier
  const tiers = {};
  npcs.forEach(npc => {
    const t = npc.tier || 0;
    if (!tiers[t]) tiers[t] = [];
    tiers[t].push(npc);
  });
  container.innerHTML = '';
  Object.keys(tiers).sort().forEach(tierNum => {
    const section = document.createElement('div');
    section.className = 'npc-tier-section';
    const header = document.createElement('div');
    header.className = 'npc-tier-header';
    header.textContent = TIER_LABELS[tierNum] || ('Tier ' + tierNum);
    section.appendChild(header);
    const grid = document.createElement('div');
    grid.className = 'npc-tier-grid';
    tiers[tierNum].forEach(npc => {
      grid.appendChild(createNPCCard(npc));
    });
    section.appendChild(grid);
    container.appendChild(section);
  });
}

const LIVE_STATE_LABELS = {
  ready: 'Ready', starting: 'Starting...', responding: 'Thinking...',
  'writing docs': 'Writing docs...', 'committing code': 'Committing code...',
  'managing tickets': 'Managing tickets...', 'processing commands': 'Processing...',
  'posting': 'Posting...', firing: 'Being Fired...', offline: 'Out of Office', disconnected: 'Disconnected',
  unknown: 'Unknown',
};

function createNPCCard(npc) {
  const card = document.createElement('div');
  const ls = npc.live_state || 'unknown';
  const lsCss = ls.replace(/ /g, '-');
  card.className = 'npc-card' + (npc.online ? '' : ' offline');
  // Build NPC avatar for card header
  let npcAvatarHtml = '';
  const npa = PERSONA_AVATARS[npc.display_name];
  if (npa) {
    if (npa.avatar) {
      npcAvatarHtml = '<div class="msg-avatar" style="width:24px;height:24px;font-size:11px"><img src="' + escapeHtml(npa.avatar) + '" alt="" style="width:24px;height:24px;border-radius:6px"></div>';
    } else {
      npcAvatarHtml = '<div class="msg-avatar" style="width:24px;height:24px;font-size:11px;background:' + npa.color + '">' + npa.initial + '</div>';
    }
  }
  card.innerHTML =
    '<div class="npc-card-header">' +
      npcAvatarHtml +
      '<span class="npc-status-dot ' + lsCss + '"></span>' +
      '<span class="npc-card-name">' + escapeHtml(npc.display_name) + '</span>' +
      '<span class="npc-card-state">' + (LIVE_STATE_LABELS[ls] || ls) + '</span>' +
    '</div>' +
    '<div class="npc-card-desc">' + escapeHtml(npc.team_description) + '</div>' +
    '<div class="npc-card-section-label">Channels</div>' +
    '<div class="npc-card-tags">' +
      npc.channels.map(ch => '<span class="npc-tag">' + escapeHtml(ch) + '</span>').join('') +
    '</div>' +
    '<div class="npc-card-section-label">Doc Folders</div>' +
    '<div class="npc-card-tags">' +
      (npc.folders || []).map(f => '<span class="npc-tag npc-tag-folder">' + escapeHtml(f) + '</span>').join('') +
    '</div>' +
    ((npc.repos || []).length ? '<div class="npc-card-section-label">GitLab Repos</div>' +
    '<div class="npc-card-tags">' +
      npc.repos.map(r => '<span class="npc-tag" style="border-left:2px solid #e67e22">' + escapeHtml(r) + '</span>').join('') +
    '</div>' : '');
  const btn = document.createElement('button');
  btn.className = 'npc-toggle-btn' + (npc.online ? ' is-online' : '');
  btn.textContent = npc.online ? 'Set Out of Office' : 'Bring Online';
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    await fetch('/api/npcs/' + encodeURIComponent(npc.key) + '/toggle', {method: 'POST'});
    loadNPCs();
  });
  card.appendChild(btn);
  const fireBtn = document.createElement('button');
  fireBtn.className = 'npc-toggle-btn';
  fireBtn.style.cssText = 'margin-top:4px;font-size:10px;color:var(--text-dimmer)';
  fireBtn.textContent = 'Fire';
  fireBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm('Fire ' + npc.display_name + '? Their session will be closed and they will stop responding. Documents and tickets are preserved.')) return;
    await fetch('/api/npcs/' + encodeURIComponent(npc.key) + '/fire', {method: 'POST'});
    await loadPersonas();
    await loadChannels();
    loadNPCs();
  });
  card.appendChild(fireBtn);
  card.style.cursor = 'pointer';
  card.addEventListener('click', (e) => {
    if (e.target === btn || e.target === fireBtn) return;
    openNPCDetail(npc.key, npc.display_name);
  });
  return card;
}

// -- NPC detail modal --

let _npcDetailKey = null;
let _npcDetailTab = 'thoughts';
let _npcThoughtsData = [];

async function openNPCDetail(key, displayName) {
  _npcDetailKey = key;
  _npcDetailTab = 'thoughts';
  document.getElementById('npc-detail-title').textContent = displayName;
  document.querySelectorAll('.npc-detail-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.npcTab === 'thoughts');
  });
  switchNPCDetailTab('thoughts');
  await loadNPCThoughts();
  openModal('npc-detail-modal');
}

function switchNPCDetailTab(tab) {
  _npcDetailTab = tab;
  document.getElementById('npc-detail-thoughts').style.display = tab === 'thoughts' ? 'flex' : 'none';
  document.getElementById('npc-detail-character').style.display = tab === 'character' ? '' : 'none';
  document.getElementById('npc-detail-prompt').style.display = tab === 'prompt' ? '' : 'none';
  document.getElementById('npc-detail-config').style.display = tab === 'config' ? '' : 'none';
}

async function loadNPCThoughts() {
  const content = document.getElementById('npc-thoughts-content');
  content.textContent = 'Loading...';
  document.getElementById('npc-thoughts-search').value = '';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/thoughts');
  _npcThoughtsData = await resp.json();
  if (!_npcThoughtsData.length) {
    document.getElementById('npc-thoughts-list').innerHTML = '';
    content.textContent = 'No thoughts recorded yet. This agent has not responded to any messages.';
    return;
  }
  renderThoughtsList();
}

function renderThoughtsList(filter) {
  const list = document.getElementById('npc-thoughts-list');
  const content = document.getElementById('npc-thoughts-content');
  list.innerHTML = '';
  const filterLower = (filter || '').toLowerCase();
  const reversed = [..._npcThoughtsData].reverse();
  let firstIdx = null;
  reversed.forEach((t, i) => {
    const idx = _npcThoughtsData.length - 1 - i;
    // Filter by search term
    if (filterLower) {
      const text = ((t.thinking || '') + ' ' + (t.response || '')).toLowerCase();
      if (!text.includes(filterLower)) return;
    }
    if (firstIdx === null) firstIdx = idx;
    const item = document.createElement('div');
    item.className = 'thought-item';
    item.dataset.idx = idx;
    const ts = new Date(t.timestamp * 1000);
    const timeStr = ts.toLocaleTimeString();
    const dateStr = ts.toLocaleDateString();
    const preview = (t.thinking || t.response || '').substring(0, 60).replace(/[\\n\\r]+/g, ' ');
    item.innerHTML = '<div class="thought-item-time">' + dateStr + ' ' + timeStr + '</div>' +
      '<div class="thought-item-preview">' + escapeHtml(preview) + '</div>';
    item.addEventListener('click', () => selectThought(idx));
    list.appendChild(item);
  });
  if (firstIdx !== null) {
    selectThought(firstIdx);
  } else {
    content.textContent = filter ? 'No thoughts matching "' + filter + '"' : 'No thoughts recorded yet.';
  }
}

document.getElementById('npc-thoughts-search').addEventListener('input', (e) => {
  renderThoughtsList(e.target.value.trim());
});

function selectThought(idx) {
  const content = document.getElementById('npc-thoughts-content');
  const t = _npcThoughtsData[idx];
  if (!t) return;
  // Update active state in list
  document.querySelectorAll('.thought-item').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
  });
  const ts = new Date(t.timestamp * 1000).toLocaleString();
  let text = '=== Internal Thinking ===  ' + ts + '\\n\\n';
  text += t.thinking || '(no thinking captured)';
  text += '\\n\\n=== Response ===\\n\\n';
  text += t.response || '(no response)';
  content.textContent = text;
}

async function loadNPCPrompt() {
  const body = document.getElementById('npc-detail-prompt');
  body.innerHTML = '<span style="color:var(--text-dimmer)">Loading...</span>';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/prompt');
  const data = await resp.json();
  if (data.error) { body.textContent = data.error; return; }
  let html = '';
  if (data.context) {
    html += '<div style="margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--highlight);margin-bottom:8px">Character Context</div>';
    html += '<div style="white-space:pre-wrap">' + escapeHtml(data.context) + '</div></div>';
    html += '<div style="border-top:2px solid var(--accent);margin:16px 0;position:relative"><span style="position:absolute;top:-10px;left:12px;background:var(--input-bg);padding:0 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--accent)">Simulation Directives</span></div>';
  }
  html += '<div style="white-space:pre-wrap;margin-top:' + (data.context ? '16px' : '0') + '">' + escapeHtml(data.prompt) + '</div>';
  body.innerHTML = html;
}

async function loadNPCCharacter() {
  const meta = document.getElementById('npc-cs-meta');
  const sections = document.getElementById('npc-cs-sections');
  meta.innerHTML = 'Loading...';
  sections.innerHTML = '';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/character-sheet');
  const data = await resp.json();
  if (data.error) { meta.textContent = data.error; return; }

  // Render YAML frontmatter metadata
  const fm = data.frontmatter || {};
  let metaHtml = '';
  if (fm.Name) metaHtml += '<div style="font-size:20px;font-weight:700;color:var(--text);margin-bottom:4px">' + escapeHtml(fm.Name) + '</div>';
  const badges = [];
  if (fm.Type) badges.push('<span style="background:var(--border-dark);color:var(--text-dim);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.Type) + '</span>');
  if (fm.Status) badges.push('<span style="background:#2ecc71;color:var(--text-bright);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.Status) + '</span>');
  if (fm.System) badges.push('<span style="background:var(--border);color:var(--highlight);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.System) + '</span>');
  if (badges.length) metaHtml += '<div style="display:flex;gap:6px;margin-bottom:8px">' + badges.join('') + '</div>';
  if (fm.Tags && fm.Tags.length) {
    metaHtml += '<div style="margin-bottom:8px">';
    fm.Tags.forEach(function(tag) {
      metaHtml += '<span style="background:var(--input-bg);color:var(--text-dim);padding:1px 6px;border-radius:4px;font-size:11px;margin-right:4px;border:1px solid var(--border-dark)">' + escapeHtml(tag) + '</span>';
    });
    metaHtml += '</div>';
  }
  meta.innerHTML = metaHtml || '<div style="color:var(--text-dimmer)">No NRSP metadata (legacy format)</div>';

  // Render sections (exclude ## Prompt — that's on the Prompt tab)
  let sectionsHtml = '';
  (data.sections || []).forEach(function(sec) {
    if (sec.title.toLowerCase() === 'prompt') return;
    sectionsHtml += '<div style="margin-bottom:16px">';
    sectionsHtml += '<h3 style="color:var(--highlight);font-size:14px;margin-bottom:6px;border-bottom:1px solid var(--border-dark);padding-bottom:4px">' + escapeHtml(sec.title) + '</h3>';
    sectionsHtml += '<div style="white-space:pre-wrap;color:var(--text);font-size:13px;line-height:1.5">' + escapeHtml(sec.content) + '</div>';
    sectionsHtml += '</div>';
  });
  sections.innerHTML = sectionsHtml || '<div style="color:var(--text-dimmer)">No structured character sections found.</div>';
}

document.querySelectorAll('.npc-detail-tab').forEach(tab => {
  tab.addEventListener('click', async () => {
    _npcDetailTab = tab.dataset.npcTab;
    document.querySelectorAll('.npc-detail-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.npcTab === _npcDetailTab);
    });
    switchNPCDetailTab(_npcDetailTab);
    if (_npcDetailTab === 'thoughts') await loadNPCThoughts();
    else if (_npcDetailTab === 'character') await loadNPCCharacter();
    else if (_npcDetailTab === 'prompt') await loadNPCPrompt();
    else if (_npcDetailTab === 'config') await loadNPCConfig();
  });
});

document.getElementById('npc-detail-close').addEventListener('click', () => {
  closeModal('npc-detail-modal');
});

// -- Hire modal --

document.getElementById('hire-role-preset').addEventListener('change', (e) => {
  const custom = document.getElementById('hire-role-custom');
  if (e.target.value === 'other') {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = '';
  }
});

function getHireDisplayName() {
  const name = document.getElementById('hire-name').value.trim();
  const rolePreset = document.getElementById('hire-role-preset').value;
  const roleCustom = document.getElementById('hire-role-custom').value.trim();
  const role = rolePreset === 'other' ? roleCustom : rolePreset;
  if (!name) return '';
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('hire-template').addEventListener('change', async (e) => {
  const key = e.target.value;
  if (!key) return;
  const resp = await fetch('/api/templates/' + encodeURIComponent(key));
  if (resp.ok) {
    const data = await resp.json();
    const name = document.getElementById('hire-name').value.trim() || 'NAME';
    document.getElementById('hire-prompt').value = data.content.replace(/{NAME}/g, name);
  }
});

// Re-apply name to template when name changes
document.getElementById('hire-name').addEventListener('input', () => {
  const templateKey = document.getElementById('hire-template').value;
  if (templateKey && document.getElementById('hire-prompt').value.includes('{NAME}')) {
    // Template hasn't been manually edited yet, nothing to do
  }
});

document.getElementById('npc-hire-btn').addEventListener('click', async () => {
  document.getElementById('hire-name').value = '';
  document.getElementById('hire-role-preset').value = '';
  document.getElementById('hire-role-custom').value = '';
  document.getElementById('hire-role-custom').style.display = 'none';
  document.getElementById('hire-key').value = '';
  document.getElementById('hire-key').dataset.manual = '';
  document.getElementById('hire-team-desc').value = '';
  document.getElementById('hire-tier').value = '1';
  document.getElementById('hire-verbosity').value = 'normal';
  document.getElementById('hire-prompt').value = '';

  // Populate template dropdown
  const templateSel = document.getElementById('hire-template');
  templateSel.innerHTML = '<option value="">Start from scratch</option>';
  const resp = await fetch('/api/templates');
  const templates = await resp.json();
  templates.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.key;
    opt.textContent = t.name;
    templateSel.appendChild(opt);
  });

  // Populate channel checkboxes
  const chContainer = document.getElementById('hire-channels');
  chContainer.innerHTML = '';
  Object.keys(channelsData).sort().forEach(ch => {
    if (channelsData[ch].is_system || channelsData[ch].is_director) return;
    const checked = ch === '#general';
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(ch) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(ch);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    chContainer.appendChild(label);
  });

  // Populate folder checkboxes
  const flContainer = document.getElementById('hire-folders');
  flContainer.innerHTML = '';
  foldersData.forEach(f => {
    const checked = f.name === 'shared' || f.name === 'public';
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(f.name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(f.name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    flContainer.appendChild(label);
  });

  openModal('hire-modal');
  document.getElementById('hire-name').focus();
});

// Auto-generate key from name
document.getElementById('hire-name').addEventListener('input', () => {
  const keyField = document.getElementById('hire-key');
  if (!keyField.dataset.manual) {
    keyField.value = document.getElementById('hire-name').value.trim().toLowerCase().replace(/[^a-z0-9]/g, '');
  }
});
document.getElementById('hire-key').addEventListener('input', () => {
  document.getElementById('hire-key').dataset.manual = '1';
});

document.getElementById('hire-modal-close').addEventListener('click', () => closeModal('hire-modal'));

document.getElementById('hire-confirm').addEventListener('click', async () => {
  const display_name = getHireDisplayName();
  const key = document.getElementById('hire-key').value.trim();
  const team_description = document.getElementById('hire-team-desc').value.trim();
  const tier = parseInt(document.getElementById('hire-tier').value);
  const prompt = document.getElementById('hire-prompt').value;

  if (!display_name) { alert('Display name is required'); return; }

  const channels = [];
  document.querySelectorAll('#hire-channels input:checked').forEach(cb => channels.push(cb.value));
  const folders = [];
  document.querySelectorAll('#hire-folders input:checked').forEach(cb => folders.push(cb.value));

  const resp = await fetch('/api/npcs/hire', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({display_name, key: key || undefined, team_description, tier, channels, folders, prompt, verbosity: document.getElementById('hire-verbosity').value}),
  });
  if (resp.ok) {
    closeModal('hire-modal');
    await loadPersonas();
    await loadChannels();
    loadFolders();
    loadNPCs();
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

// -- NPC Config tab --

async function loadNPCConfig() {
  if (!_npcDetailKey) return;
  // Get current NPC data
  const resp = await fetch('/api/npcs');
  const npcs = await resp.json();
  const npc = npcs.find(n => n.key === _npcDetailKey);
  if (!npc) return;

  // Tier and verbosity dropdowns
  document.getElementById('npc-config-tier').value = npc.tier || 1;
  document.getElementById('npc-config-verbosity').value = npc.verbosity || 'normal';

  // Channel checkboxes
  const chContainer = document.getElementById('npc-config-channels');
  chContainer.innerHTML = '';
  const currentChannels = new Set(npc.channels || []);
  Object.keys(channelsData).sort().forEach(ch => {
    if (channelsData[ch].is_system || channelsData[ch].is_director) return;
    const checked = currentChannels.has(ch);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(ch) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(ch);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    chContainer.appendChild(label);
  });

  // Folder checkboxes
  const flContainer = document.getElementById('npc-config-folders');
  flContainer.innerHTML = '';
  const currentFolders = new Set(npc.folders || []);
  foldersData.forEach(f => {
    const checked = currentFolders.has(f.name);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(f.name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(f.name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    flContainer.appendChild(label);
  });

  // Repo checkboxes
  const repoContainer = document.getElementById('npc-config-repos');
  repoContainer.innerHTML = '';
  const currentRepos = new Set(npc.repos || []);
  const allRepos = Object.keys(glRepos || {}).length ? glRepos.map(r => r.name).sort() : [];
  if (allRepos.length === 0) {
    repoContainer.innerHTML = '<span style="font-size:11px;color:var(--text-dimmer)">No repositories yet</span>';
  }
  allRepos.forEach(name => {
    const checked = currentRepos.has(name);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    repoContainer.appendChild(label);
  });
}

document.getElementById('npc-config-save').addEventListener('click', async () => {
  if (!_npcDetailKey) return;
  const tier = parseInt(document.getElementById('npc-config-tier').value);
  const channels = [];
  document.querySelectorAll('#npc-config-channels input:checked').forEach(cb => {
    channels.push(cb.value);
  });
  const folders = [];
  document.querySelectorAll('#npc-config-folders input:checked').forEach(cb => {
    folders.push(cb.value);
  });
  const repos = [];
  document.querySelectorAll('#npc-config-repos input:checked').forEach(cb => {
    repos.push(cb.value);
  });
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/config', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tier, channels, folders, repos, verbosity: document.getElementById('npc-config-verbosity').value}),
  });
  if (resp.ok) {
    loadNPCs();
    loadChannels();
  }
});

// -- Usage tab --

function formatTokenCount(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toString();
}

function formatCost(usd) {
  if (usd >= 1) return '$' + usd.toFixed(2);
  if (usd >= 0.01) return '$' + usd.toFixed(3);
  if (usd > 0) return '$' + usd.toFixed(4);
  return '$0.00';
}

let _lastUsageData = null;
async function loadUsage() {
  try {
    const resp = await fetch('/api/usage');
    if (!resp.ok) return;  // keep previous data on error
    const data = await resp.json();
    if (!data || !data.totals) return;  // keep previous data on bad response

    // Merge with previous data — keep highest values per agent to avoid flicker
    // from partial log reads during active writes
    if (_lastUsageData && data.agents) {
      const prevByName = {};
      (_lastUsageData.agents || []).forEach(a => { prevByName[a.name] = a; });
      data.agents.forEach(a => {
        const prev = prevByName[a.name];
        if (prev) {
          a.api_calls = Math.max(a.api_calls, prev.api_calls);
          a.input_tokens = Math.max(a.input_tokens, prev.input_tokens);
          a.output_tokens = Math.max(a.output_tokens, prev.output_tokens);
          a.total_cost_usd = Math.max(a.total_cost_usd, prev.total_cost_usd);
          delete prevByName[a.name];
        }
      });
      // Keep agents that were in previous data but missing from current parse
      Object.values(prevByName).forEach(a => { data.agents.push(a); });
      data.agents.sort((a, b) => b.total_cost_usd - a.total_cost_usd);
      // Recompute totals from merged agents
      data.totals.api_calls = data.agents.reduce((s, a) => s + a.api_calls, 0);
      data.totals.input_tokens = data.agents.reduce((s, a) => s + a.input_tokens, 0);
      data.totals.output_tokens = data.agents.reduce((s, a) => s + a.output_tokens, 0);
      data.totals.total_cost_usd = data.agents.reduce((s, a) => s + a.total_cost_usd, 0);
    }
    _lastUsageData = data;

    const totals = data.totals;
    const agents = data.agents;

    // Update sidebar totals
    const totalsEl = document.getElementById('usage-totals');
    totalsEl.innerHTML =
      '<div class="usage-stat"><span class="label">API Calls:</span> <strong>' + totals.api_calls + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Input:</span> <strong>' + formatTokenCount(totals.input_tokens) + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Output:</span> <strong>' + formatTokenCount(totals.output_tokens) + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Cost:</span> <strong style="color:#2ecc71">' + formatCost(totals.total_cost_usd) + '</strong></div>';

    // Update main content
    const container = document.getElementById('usage-content');
    const emptyEl = document.getElementById('usage-empty');

    if (!agents || agents.length === 0) {
      container.innerHTML = '';
      container.appendChild(emptyEl);
      emptyEl.style.display = 'block';
      return;
    }

    container.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'usage-grid';

    agents.forEach(agent => {
      const card = document.createElement('div');
      card.className = 'usage-card';
      card.innerHTML =
        '<div class="usage-card-name">' + escapeHtml(agent.name) + '</div>' +
        '<div class="usage-card-row"><span class="label">Input tokens</span><span class="value">' + formatTokenCount(agent.input_tokens) + '</span></div>' +
        '<div class="usage-card-row"><span class="label">Output tokens</span><span class="value">' + formatTokenCount(agent.output_tokens) + '</span></div>' +
        '<div class="usage-card-row"><span class="label">API calls</span><span class="value">' + agent.api_calls + '</span></div>' +
        '<div class="usage-card-row"><span class="label">Cost</span><span class="value cost">' + formatCost(agent.total_cost_usd) + '</span></div>';
      grid.appendChild(card);
    });

    container.appendChild(grid);
  } catch(e) {
    // keep previous data on fetch errors
  }
}

// -- Advanced tab --

document.getElementById('clear-chat-btn').addEventListener('click', async () => {
  if (!confirm('Clear all chat messages? This cannot be undone.')) return;
  await fetch('/api/messages/clear', {method: 'POST'});
  messagesByChannel = {};
  seenIds.clear();
  unreadByChannel = {};
  renderSidebar();
  renderMessages();
  showNotice('Chat history cleared.');
});

document.getElementById('clear-all-btn').addEventListener('click', async () => {
  if (!confirm('Clear EVERYTHING? Messages, docs, repos, tickets, events, emails, recaps — all gone. This cannot be undone.')) return;
  if (!confirm('Are you REALLY sure? Save first if you need anything.')) return;
  const scenario = (await (await fetch('/api/session/current')).json()).scenario;
  if (scenario) {
    await fetch('/api/session/new', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scenario}),
    });
    await reloadAllState();
    showNotice('Everything cleared. Fresh start.');
  }
});

// -- Session Manager (Advanced tab) --

function _fmtSessionDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const mon = d.toLocaleString('en-US', {month: 'short'});
  const day = d.getDate();
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, '0');
  const ampm = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 || 12;
  return `${mon} ${day}, ${h12}:${m} ${ampm}`;
}

let _smSessions = [];
let _smSortCol = 'saved_at';
let _smSortAsc = false;

function _smUpdateSortArrows() {
  document.querySelectorAll('#session-manager-table th[data-sm-sort]').forEach(th => {
    const arrow = th.querySelector('.sm-sort-arrow');
    if (th.dataset.smSort === _smSortCol) {
      arrow.textContent = _smSortAsc ? ' \\u25B2' : ' \\u25BC';
      th.style.color = 'var(--text)';
    } else {
      arrow.textContent = '';
      th.style.color = 'var(--text-dim)';
    }
  });
}

function _smRenderRows() {
  const tbody = document.getElementById('session-manager-body');
  if (_smSessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">No saved sessions</td></tr>';
    return;
  }
  const sorted = [..._smSessions];
  sorted.sort((a, b) => {
    let va, vb;
    if (_smSortCol === 'name') {
      va = (a.name || a.instance_dir).toLowerCase();
      vb = (b.name || b.instance_dir).toLowerCase();
    } else if (_smSortCol === 'scenario') {
      va = (a.scenario || '').toLowerCase();
      vb = (b.scenario || '').toLowerCase();
    } else {
      va = a[_smSortCol] || 0;
      vb = b[_smSortCol] || 0;
    }
    if (va < vb) return _smSortAsc ? -1 : 1;
    if (va > vb) return _smSortAsc ? 1 : -1;
    return 0;
  });
  tbody.innerHTML = '';
  sorted.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--border-dark)';
    tr.dataset.instance = s.instance_dir;
    const nameTd = document.createElement('td');
    nameTd.style.cssText = 'padding:8px 10px;color:var(--text)';
    nameTd.innerHTML = '<span class="sm-name-display">' + escapeHtml(s.name || s.instance_dir) + '</span>'
      + '<input class="sm-name-input" type="text" style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--accent);padding:4px 6px;border-radius:4px;font-size:13px" />';
    const scenarioTd = document.createElement('td');
    scenarioTd.style.cssText = 'padding:8px 10px;color:var(--text-dim)';
    scenarioTd.textContent = s.scenario || '—';
    const createdTd = document.createElement('td');
    createdTd.style.cssText = 'padding:8px 10px;color:var(--text-dim);white-space:nowrap';
    createdTd.textContent = _fmtSessionDate(s.created_at);
    const savedTd = document.createElement('td');
    savedTd.style.cssText = 'padding:8px 10px;color:var(--text-dim);white-space:nowrap';
    savedTd.textContent = _fmtSessionDate(s.saved_at);
    const actionsTd = document.createElement('td');
    actionsTd.style.cssText = 'padding:8px 10px;text-align:right;white-space:nowrap';
    const loadBtn = document.createElement('button');
    loadBtn.className = 'session-btn';
    loadBtn.textContent = 'Load';
    loadBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px';
    loadBtn.addEventListener('click', () => _smLoad(s.instance_dir));
    const renameBtn = document.createElement('button');
    renameBtn.className = 'session-btn';
    renameBtn.textContent = 'Rename';
    renameBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px';
    renameBtn.addEventListener('click', () => _smStartRename(tr, s));
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-btn';
    deleteBtn.textContent = 'Delete';
    deleteBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px;border-color:var(--accent);color:var(--accent)';
    deleteBtn.addEventListener('click', () => _smDelete(s.instance_dir, s.name || s.instance_dir));
    actionsTd.appendChild(loadBtn);
    actionsTd.appendChild(renameBtn);
    actionsTd.appendChild(deleteBtn);
    tr.appendChild(nameTd);
    tr.appendChild(scenarioTd);
    tr.appendChild(createdTd);
    tr.appendChild(savedTd);
    tr.appendChild(actionsTd);
    tbody.appendChild(tr);
  });
  _smUpdateSortArrows();
}

document.querySelectorAll('#session-manager-table th[data-sm-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.smSort;
    if (_smSortCol === col) {
      _smSortAsc = !_smSortAsc;
    } else {
      _smSortCol = col;
      _smSortAsc = (col === 'name' || col === 'scenario');
    }
    _smRenderRows();
  });
});

async function loadSessionManagerTable() {
  const tbody = document.getElementById('session-manager-body');
  tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>';
  try {
    const resp = await fetch('/api/session/list');
    _smSessions = await resp.json();
    _smRenderRows();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--accent);text-align:center">Failed to load sessions</td></tr>';
  }
}

async function _smLoad(instance) {
  showLoading('Loading session...');
  try {
    const resp = await fetch('/api/session/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instance}),
    });
    if (resp.ok) {
      await reloadAllState();
      showNotice('Session loaded.');
    } else {
      const err = await resp.json();
      hideLoading();
      showNotice('Error: ' + (err.error || 'unknown'));
    }
  } finally {
    hideLoading();
  }
}

function _smStartRename(tr, session) {
  const display = tr.querySelector('.sm-name-display');
  const input = tr.querySelector('.sm-name-input');
  display.style.display = 'none';
  input.style.display = '';
  input.value = session.name || session.instance_dir;
  input.focus();
  input.select();
  const finish = async () => {
    input.removeEventListener('blur', finish);
    input.removeEventListener('keydown', onKey);
    const newName = input.value.trim();
    if (!newName || newName === (session.name || session.instance_dir)) {
      display.style.display = '';
      input.style.display = 'none';
      return;
    }
    try {
      const resp = await fetch('/api/session/' + encodeURIComponent(session.instance_dir), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: newName}),
      });
      if (resp.ok) {
        session.name = newName;
        display.textContent = newName;
      }
    } catch(e) {}
    display.style.display = '';
    input.style.display = 'none';
  };
  const onKey = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(); }
    if (e.key === 'Escape') { input.value = session.name || session.instance_dir; finish(); }
  };
  input.addEventListener('blur', finish);
  input.addEventListener('keydown', onKey);
}

async function _smDelete(instance, displayName) {
  if (!confirm('Delete session "' + displayName + '"? This cannot be undone.')) return;
  try {
    const resp = await fetch('/api/session/' + encodeURIComponent(instance), {method: 'DELETE'});
    if (resp.ok) {
      _smSessions = _smSessions.filter(s => s.instance_dir !== instance);
      _smRenderRows();
      showNotice('Session deleted.');
    } else {
      const err = await resp.json();
      showNotice('Error: ' + (err.error || 'unknown'));
    }
  } catch(e) {
    showNotice('Delete failed.');
  }
}

// -- Recap tab --

const STYLE_LABELS = {
  normal: 'Normal', 'ye-olde-english': 'Ye Olde English', tolkien: 'Tolkien Fantasy',
  'star-wars': 'Star Wars', 'star-trek': 'Star Trek', 'dr-who': 'Doctor Who',
  'morse-code': 'Telegraph', 'dr-seuss': 'Dr. Seuss', shakespeare: 'Shakespeare',
  '80s-rock-ballad': '80s Rock Ballad', '90s-alternative': '90s Alternative',
  'heavy-metal': 'Heavy Metal', dystopian: 'Dystopian', matrix: 'The Matrix',
  pharaoh: "Pharaoh's Decree", tombstone: 'Tombstone Western',
  survivor: 'Survivor Tribal Council', hackernews: 'HackerNews Blog',
};

document.getElementById('recap-generate-btn').addEventListener('click', async () => {
  const style = document.getElementById('recap-style').value;
  const content = document.getElementById('recap-content');
  const btn = document.getElementById('recap-generate-btn');
  content.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:60px"><div class="spinner" style="margin:0 auto 12px"></div>Generating ' + (STYLE_LABELS[style] || style) + ' recap...</div>';
  btn.disabled = true;
  btn.textContent = 'Generating...';
  try {
    const resp = await fetch('/api/recap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({style}),
    });
    const data = await resp.json();
    if (data.recap) {
      content.textContent = data.recap;
      renderRecapList();
    } else {
      content.textContent = 'Error: ' + (data.error || 'unknown');
    }
  } catch(e) {
    content.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = 'Generate Recap';
});

async function renderRecapList() {
  const list = document.getElementById('recap-list');
  list.innerHTML = '';
  const resp = await fetch('/api/recaps');
  const recaps = await resp.json();
  [...recaps].reverse().forEach((r) => {
    const item = document.createElement('div');
    item.className = 'recap-item';
    const ts = new Date(r.timestamp * 1000);
    item.innerHTML = '<div class="recap-item-style">' + escapeHtml(STYLE_LABELS[r.style] || r.style) + '</div>' +
      '<div class="recap-item-time">' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('.recap-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('recap-content').textContent = r.recap;
    });
    list.appendChild(item);
  });
}

// -- Email tab --

async function loadEmails() {
  const list = document.getElementById('email-list');
  const empty = document.getElementById('email-list-empty');
  const resp = await fetch('/api/emails');
  const emails = await resp.json();
  list.innerHTML = '';
  empty.style.display = emails.length ? 'none' : 'block';
  [...emails].reverse().forEach(e => {
    const item = document.createElement('div');
    item.className = 'email-item';
    item.dataset.id = e.id;
    const ts = new Date(e.timestamp * 1000);
    item.innerHTML =
      '<div class="email-item-from">' + escapeHtml(e.sender) + '</div>' +
      '<div class="email-item-subject">' + escapeHtml(e.subject) + '</div>' +
      '<div class="email-item-date">' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => viewEmail(e));
    list.appendChild(item);
  });
}

function viewEmail(e) {
  document.querySelectorAll('.email-item').forEach(el => el.classList.remove('active'));
  const active = document.querySelector('.email-item[data-id="' + e.id + '"]');
  if (active) active.classList.add('active');
  document.getElementById('email-viewer-from').textContent = e.sender;
  document.getElementById('email-viewer-subject').textContent = e.subject;
  document.getElementById('email-viewer-date').textContent = new Date(e.timestamp * 1000).toLocaleString();
  document.getElementById('email-viewer-body').textContent = e.body;
  document.getElementById('email-viewer').style.display = '';
  document.getElementById('email-compose').style.display = 'none';
  document.getElementById('email-empty-state').style.display = 'none';
}

document.getElementById('compose-email-btn').addEventListener('click', () => {
  document.getElementById('email-compose-name').value = '';
  document.getElementById('email-compose-role').value = 'Scenario Director';
  document.getElementById('email-compose-role-custom').style.display = 'none';
  document.getElementById('email-compose-subject').value = '';
  document.getElementById('email-compose-body').value = '';
  document.getElementById('email-viewer').style.display = 'none';
  document.getElementById('email-compose').style.display = '';
  document.getElementById('email-empty-state').style.display = 'none';
  document.getElementById('email-compose-subject').focus();
});

document.getElementById('email-compose-role').addEventListener('change', (e) => {
  const custom = document.getElementById('email-compose-role-custom');
  custom.style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('email-compose-cancel').addEventListener('click', () => {
  document.getElementById('email-compose').style.display = 'none';
  document.getElementById('email-empty-state').style.display = '';
});

document.getElementById('email-compose-send').addEventListener('click', async () => {
  const name = document.getElementById('email-compose-name').value.trim() || 'Anonymous';
  let role = document.getElementById('email-compose-role').value;
  if (role === 'custom') role = document.getElementById('email-compose-role-custom').value.trim();
  const sender = role ? name + ' (' + role + ')' : name;
  const subject = document.getElementById('email-compose-subject').value.trim();
  const body = document.getElementById('email-compose-body').value.trim();
  if (!subject) { document.getElementById('email-compose-subject').focus(); return; }
  const resp = await fetch('/api/emails', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sender, subject, body}),
  });
  if (resp.ok) {
    document.getElementById('email-compose').style.display = 'none';
    document.getElementById('email-empty-state').style.display = '';
    loadEmails();
    showNotice('Email sent: ' + subject);
  }
});

// -- Memos tab --

let _currentMemoThread = null;

async function loadMemoThreads() {
  const list = document.getElementById('memo-threads-list');
  const empty = document.getElementById('memo-threads-empty');
  const resp = await fetch('/api/memos/threads');
  const threads = await resp.json();
  list.innerHTML = '';
  empty.style.display = threads.length ? 'none' : 'block';
  threads.forEach(t => {
    const item = document.createElement('div');
    item.className = 'memo-thread-item' + (t.id === _currentMemoThread ? ' active' : '');
    item.dataset.id = t.id;
    const preview = t.last_post_text || t.description || 'No posts yet';
    const postInfo = t.post_count + ' post' + (t.post_count !== 1 ? 's' : '');
    const age = _memoTimeAgo(t.last_post_at);
    item.innerHTML =
      '<div class="memo-thread-title">' + escapeHtml(t.title) + '</div>' +
      '<div class="memo-thread-preview">' + escapeHtml(preview.substring(0, 60)) + '</div>' +
      '<div class="memo-thread-meta">' + escapeHtml(t.creator) + ' &middot; ' + postInfo + ' &middot; ' + age + '</div>';
    item.addEventListener('click', () => viewMemoThread(t.id));
    list.appendChild(item);
  });
}

function _memoTimeAgo(ts) {
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  return Math.floor(seconds / 86400) + 'd ago';
}

async function viewMemoThread(threadId) {
  _currentMemoThread = threadId;
  document.querySelectorAll('.memo-thread-item').forEach(el =>
    el.classList.toggle('active', el.dataset.id === threadId));

  const resp = await fetch('/api/memos/threads/' + threadId);
  if (!resp.ok) { showNotice('Thread not found'); return; }
  const thread = await resp.json();

  document.getElementById('memo-thread-title').textContent = thread.title;
  document.getElementById('memo-thread-meta').textContent =
    'Started by ' + thread.creator + ' &middot; ' + _memoTimeAgo(thread.created_at);
  document.getElementById('memo-thread-meta').innerHTML =
    'Started by ' + escapeHtml(thread.creator) + ' &middot; ' + _memoTimeAgo(thread.created_at);
  const descEl = document.getElementById('memo-thread-description');
  descEl.innerHTML = thread.description ? renderMarkdown(thread.description) : '';
  descEl.style.display = thread.description ? '' : 'none';

  const postsList = document.getElementById('memo-posts-list');
  const posts = thread.posts || [];
  postsList.innerHTML = '';
  posts.forEach(p => {
    const div = document.createElement('div');
    div.className = 'memo-post';
    const ts = new Date(p.timestamp * 1000).toLocaleString();
    div.innerHTML =
      '<div style="display:flex;align-items:baseline">' +
        '<span class="memo-post-author">' + escapeHtml(p.author) + '</span>' +
        '<span class="memo-post-date">' + ts + '</span>' +
      '</div>' +
      '<div class="memo-post-text">' + renderMarkdown(p.text) + '</div>';
    postsList.appendChild(div);
  });

  document.getElementById('memo-thread-viewer').style.display = '';
  document.getElementById('memo-empty-state').style.display = 'none';
  document.getElementById('memo-reply-text').value = '';
}

function _getMemoSender(nameId, roleId, customId) {
  const name = document.getElementById(nameId).value.trim() || 'Anonymous';
  let role = document.getElementById(roleId).value;
  if (role === 'custom') role = document.getElementById(customId).value.trim();
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('memo-reply-role').addEventListener('change', (e) => {
  document.getElementById('memo-reply-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('memo-create-role').addEventListener('change', (e) => {
  document.getElementById('memo-create-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('create-memo-thread-btn').addEventListener('click', () => {
  const modal = document.getElementById('memo-create-modal');
  document.getElementById('memo-create-title').value = '';
  document.getElementById('memo-create-description').value = '';
  document.getElementById('memo-create-name').value = '';
  document.getElementById('memo-create-role').value = 'Scenario Director';
  document.getElementById('memo-create-role-custom').style.display = 'none';
  document.getElementById('memo-create-role-custom').value = '';
  openModal('memo-create-modal');
  document.getElementById('memo-create-title').focus();
});

document.getElementById('memo-create-cancel').addEventListener('click', () => {
  closeModal('memo-create-modal');
});

document.getElementById('memo-create-submit').addEventListener('click', async () => {
  const title = document.getElementById('memo-create-title').value.trim();
  if (!title) { document.getElementById('memo-create-title').focus(); return; }
  const description = document.getElementById('memo-create-description').value.trim();
  const creator = _getMemoSender('memo-create-name', 'memo-create-role', 'memo-create-role-custom');
  const resp = await fetch('/api/memos/threads', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, creator, description})
  });
  if (resp.ok) {
    const thread = await resp.json();
    closeModal('memo-create-modal');
    loadMemoThreads();
    viewMemoThread(thread.id);
    showNotice('Thread created: ' + title);
  }
});

document.getElementById('memo-reply-send').addEventListener('click', async () => {
  if (!_currentMemoThread) return;
  const textarea = document.getElementById('memo-reply-text');
  const text = textarea.value.trim();
  if (!text) return;
  const author = _getMemoSender('memo-reply-name', 'memo-reply-role', 'memo-reply-role-custom');
  const resp = await fetch('/api/memos/threads/' + _currentMemoThread + '/posts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, author})
  });
  if (resp.ok) {
    textarea.value = '';
    viewMemoThread(_currentMemoThread);
    loadMemoThreads();
  }
});

document.getElementById('memo-delete-btn').addEventListener('click', async () => {
  if (!_currentMemoThread) return;
  if (!confirm('Delete this discussion thread and all its posts?')) return;
  const resp = await fetch('/api/memos/threads/' + _currentMemoThread, {method: 'DELETE'});
  if (resp.ok) {
    _currentMemoThread = null;
    document.getElementById('memo-thread-viewer').style.display = 'none';
    document.getElementById('memo-empty-state').style.display = '';
    loadMemoThreads();
    showNotice('Thread deleted');
  }
});

// Load memos when tab is selected — handled by tab click handler below

// -- Blog tab --

let _currentBlogPost = null;
let _blogFilter = 'all';

async function loadBlogPosts() {
  const list = document.getElementById('blog-posts-list');
  const empty = document.getElementById('blog-posts-empty');
  let url = '/api/blog/posts';
  if (_blogFilter !== 'all') url += '?filter=' + _blogFilter;
  const resp = await fetch(url);
  const posts = await resp.json();
  list.innerHTML = '';
  empty.style.display = posts.length ? 'none' : 'block';
  posts.forEach(p => {
    const item = document.createElement('div');
    item.className = 'blog-post-item' + (p.slug === _currentBlogPost ? ' active' : '');
    item.dataset.slug = p.slug;
    let badge = p.is_external
      ? '<span class="blog-external-badge">External</span>'
      : '<span class="blog-internal-badge">Internal</span>';
    const pStatus = p.status || 'published';
    if (pStatus === 'draft') badge += ' <span class="blog-internal-badge" style="background:#f39c12;color:var(--text-bright)">Draft</span>';
    if (pStatus === 'unpublished') badge += ' <span class="blog-internal-badge" style="background:var(--accent);color:var(--text-bright)">Unpub</span>';
    const preview = (p.body || '').substring(0, 60);
    const replyInfo = p.reply_count + ' repl' + (p.reply_count !== 1 ? 'ies' : 'y');
    const age = _memoTimeAgo(p.created_at);
    item.innerHTML =
      '<div style="display:flex;align-items:center"><span class="blog-post-title">' + escapeHtml(p.title) + '</span>' + badge + '</div>' +
      '<div class="blog-post-preview">' + escapeHtml(preview) + '</div>' +
      '<div class="blog-post-meta">' + escapeHtml(p.author) + ' &middot; ' + replyInfo + ' &middot; ' + age + '</div>';
    item.addEventListener('click', () => viewBlogPost(p.slug));
    list.appendChild(item);
  });
}

document.querySelectorAll('.blog-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    _blogFilter = btn.dataset.blogFilter;
    document.querySelectorAll('.blog-filter-btn').forEach(b => b.classList.toggle('active', b === btn));
    loadBlogPosts();
  });
});

async function viewBlogPost(slug) {
  _currentBlogPost = slug;
  document.querySelectorAll('.blog-post-item').forEach(el =>
    el.classList.toggle('active', el.dataset.slug === slug));

  const resp = await fetch('/api/blog/posts/' + slug);
  if (!resp.ok) { showNotice('Post not found'); return; }
  const post = await resp.json();

  document.getElementById('blog-post-title').textContent = post.title;
  let badgeHtml = post.is_external
    ? '<span class="blog-external-badge">External</span>'
    : '<span class="blog-internal-badge">Internal</span>';
  const status = post.status || 'published';
  if (status === 'draft') badgeHtml += ' <span class="blog-internal-badge" style="background:#f39c12;color:var(--text-bright)">Draft</span>';
  if (status === 'unpublished') badgeHtml += ' <span class="blog-internal-badge" style="background:var(--accent);color:var(--text-bright)">Unpublished</span>';
  document.getElementById('blog-post-badge').innerHTML = badgeHtml;
  document.getElementById('blog-post-author').textContent = post.author;
  document.getElementById('blog-post-date').textContent = new Date(post.created_at * 1000).toLocaleString();

  // Show/hide publish/unpublish buttons based on status
  document.getElementById('blog-publish-btn').style.display = (status !== 'published') ? '' : 'none';
  document.getElementById('blog-unpublish-btn').style.display = (status === 'published') ? '' : 'none';

  const tagsEl = document.getElementById('blog-post-tags');
  tagsEl.innerHTML = '';
  (post.tags || []).forEach(tag => {
    const span = document.createElement('span');
    span.className = 'blog-tag';
    span.textContent = tag;
    tagsEl.appendChild(span);
  });

  document.getElementById('blog-post-body').innerHTML = renderMarkdown(post.body || '');

  const replies = post.replies || [];
  document.getElementById('blog-replies-header').textContent = replies.length + ' Repl' + (replies.length !== 1 ? 'ies' : 'y');
  const repliesList = document.getElementById('blog-replies-list');
  repliesList.innerHTML = '';
  replies.forEach(r => {
    const div = document.createElement('div');
    div.className = 'blog-reply';
    const ts = new Date(r.timestamp * 1000).toLocaleString();
    div.innerHTML =
      '<div style="display:flex;align-items:baseline">' +
        '<span class="blog-reply-author">' + escapeHtml(r.author) + '</span>' +
        '<span class="blog-reply-date">' + ts + '</span>' +
      '</div>' +
      '<div class="blog-reply-text">' + renderMarkdown(r.text) + '</div>';
    repliesList.appendChild(div);
  });

  document.getElementById('blog-post-viewer').style.display = '';
  document.getElementById('blog-empty-state').style.display = 'none';
  document.getElementById('blog-reply-text').value = '';
}

function _getBlogSender(nameId, roleId, customId) {
  const name = document.getElementById(nameId).value.trim() || 'Anonymous';
  let role = document.getElementById(roleId).value;
  if (role === 'custom') role = document.getElementById(customId).value.trim();
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('blog-reply-role').addEventListener('change', (e) => {
  document.getElementById('blog-reply-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('blog-create-role').addEventListener('change', (e) => {
  document.getElementById('blog-create-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('create-blog-post-btn').addEventListener('click', () => {
  document.getElementById('blog-create-title').value = '';
  document.getElementById('blog-create-body').value = '';
  document.getElementById('blog-create-tags').value = '';
  document.getElementById('blog-create-name').value = '';
  document.getElementById('blog-create-role').value = 'Scenario Director';
  document.getElementById('blog-create-role-custom').style.display = 'none';
  document.getElementById('blog-create-role-custom').value = '';
  document.getElementById('blog-create-external').checked = false;
  openModal('blog-create-modal');
  document.getElementById('blog-create-title').focus();
});

document.getElementById('blog-create-cancel').addEventListener('click', () => {
  closeModal('blog-create-modal');
});

async function _submitBlogPost(status) {
  const title = document.getElementById('blog-create-title').value.trim();
  if (!title) { document.getElementById('blog-create-title').focus(); return; }
  const body = document.getElementById('blog-create-body').value.trim();
  const author = _getBlogSender('blog-create-name', 'blog-create-role', 'blog-create-role-custom');
  const is_external = document.getElementById('blog-create-external').checked;
  const tagsRaw = document.getElementById('blog-create-tags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(t => t) : [];
  const resp = await fetch('/api/blog/posts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, body, author, is_external, tags, status})
  });
  if (resp.ok) {
    const post = await resp.json();
    closeModal('blog-create-modal');
    loadBlogPosts();
    viewBlogPost(post.slug);
    showNotice((status === 'draft' ? 'Draft saved: ' : 'Published: ') + title);
  }
}
document.getElementById('blog-create-submit').addEventListener('click', () => _submitBlogPost('published'));
document.getElementById('blog-create-draft').addEventListener('click', () => _submitBlogPost('draft'));

document.getElementById('blog-reply-send').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const textarea = document.getElementById('blog-reply-text');
  const text = textarea.value.trim();
  if (!text) return;
  const author = _getBlogSender('blog-reply-name', 'blog-reply-role', 'blog-reply-role-custom');
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost + '/replies', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, author})
  });
  if (resp.ok) {
    textarea.value = '';
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
  }
});

document.getElementById('blog-publish-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'published'})
  });
  if (resp.ok) {
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
    showNotice('Post published');
  }
});

document.getElementById('blog-unpublish-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'unpublished'})
  });
  if (resp.ok) {
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
    showNotice('Post unpublished');
  }
});

document.getElementById('blog-delete-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  if (!confirm('Delete this blog post and all its replies?')) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {method: 'DELETE'});
  if (resp.ok) {
    _currentBlogPost = null;
    document.getElementById('blog-post-viewer').style.display = 'none';
    document.getElementById('blog-empty-state').style.display = '';
    loadBlogPosts();
    showNotice('Post deleted');
  }
});

// -- Events tab --

let _eventsSubTab = 'pool';

document.querySelectorAll('.events-sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    _eventsSubTab = tab.dataset.eventsTab;
    document.querySelectorAll('.events-sub-tab').forEach(t => t.classList.toggle('active', t === tab));
    document.getElementById('events-pool-view').style.display = _eventsSubTab === 'pool' ? '' : 'none';
    document.getElementById('events-log-view').style.display = _eventsSubTab === 'log' ? '' : 'none';
    if (_eventsSubTab === 'pool') loadEventPool();
    if (_eventsSubTab === 'log') loadEventLog();
  });
});

async function loadEventPool() {
  const grid = document.getElementById('events-pool-grid');
  const empty = document.getElementById('events-pool-empty');
  const resp = await fetch('/api/events/pool');
  const pool = await resp.json();
  grid.innerHTML = '';
  empty.style.display = pool.length ? 'none' : 'block';
  pool.forEach((evt, i) => {
    const actions = evt.actions || [];
    const actionTypes = [...new Set(actions.map(a => a.type))].join(', ');
    const preview = actions.find(a => a.type === 'message');
    const card = document.createElement('div');
    card.className = 'event-card';
    card.style.cursor = 'pointer';
    card.innerHTML =
      '<div class="event-card-header">' +
        '<span class="event-card-severity event-sev-' + (evt.severity || 'medium') + '">' + escapeHtml(evt.severity || 'medium') + '</span>' +
        '<span class="event-card-name">' + escapeHtml(evt.name || 'Unnamed') + '</span>' +
      '</div>' +
      '<div class="event-card-actions">' + escapeHtml(actions.length + ' action(s): ' + actionTypes) + '</div>' +
      (preview ? '<div class="event-card-preview">' + escapeHtml(preview.content || '').substring(0, 80) + '</div>' : '');
    const trigBtn = document.createElement('button');
    trigBtn.className = 'event-trigger-btn';
    trigBtn.style.cssText = 'width:100%;margin-top:8px';
    trigBtn.textContent = 'Trigger';
    trigBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      triggerEvent(evt);
    });
    card.appendChild(trigBtn);
    card.addEventListener('click', (e) => {
      if (e.target === trigBtn) return;
      openEventEditor(i, evt);
    });
    grid.appendChild(card);
  });
}

async function triggerEvent(evt) {
  const resp = await fetch('/api/events/trigger', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(evt),
  });
  if (resp.ok) {
    showNotice('Event triggered: ' + (evt.name || 'Custom Event') + ' (' + (evt.actions || []).length + ' actions fired)');
  }
  loadEventLog();
}

async function loadEventLog() {
  const list = document.getElementById('events-log-list');
  const empty = document.getElementById('events-log-empty');
  const resp = await fetch('/api/events/log');
  const log = await resp.json();
  list.innerHTML = '';
  empty.style.display = log.length ? 'none' : 'block';
  [...log].reverse().forEach(entry => {
    const row = document.createElement('div');
    row.className = 'event-log-row';
    row.style.cssText = 'cursor:pointer;flex-wrap:wrap';
    const ts = new Date(entry.timestamp * 1000).toLocaleString();
    const actionCount = (entry.actions || []).length;
    row.innerHTML =
      '<span class="event-log-time">' + ts + '</span>' +
      '<span class="event-card-severity event-sev-' + (entry.severity || 'medium') + '">' + escapeHtml(entry.severity || 'medium') + '</span>' +
      '<span class="event-log-name">' + escapeHtml(entry.name || 'Custom') + '</span>' +
      '<span class="event-log-actions">' + actionCount + ' action(s)</span>';
    const retrigger = document.createElement('button');
    retrigger.className = 'session-btn';
    retrigger.style.cssText = 'font-size:10px';
    retrigger.textContent = 'Re-trigger';
    retrigger.addEventListener('click', (e) => {
      e.stopPropagation();
      triggerEvent(entry);
    });
    row.appendChild(retrigger);
    // Expandable YAML detail
    const detail = document.createElement('div');
    detail.style.cssText = 'display:none;width:100%;margin-top:8px;background:var(--input-bg);border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:var(--text);white-space:pre-wrap;max-height:300px;overflow-y:auto';
    const clean = Object.assign({}, entry);
    delete clean._history;
    detail.textContent = eventToYaml(clean);
    row.appendChild(detail);
    row.addEventListener('click', () => {
      detail.style.display = detail.style.display === 'none' ? '' : 'none';
    });
    list.appendChild(row);
  });
}

let _eventEditIndex = -1; // -1 = new event
let _eventEditHistory = []; // version history for current event

function eventToYaml(evt) {
  if (typeof jsyaml !== 'undefined') return jsyaml.dump(evt, {lineWidth: -1});
  return JSON.stringify(evt, null, 2);
}

function yamlToEvent(text) {
  if (typeof jsyaml !== 'undefined') return jsyaml.load(text);
  return JSON.parse(text);
}

function openEventEditor(index, evt) {
  _eventEditIndex = index;
  _eventEditHistory = evt._history || [];
  const clean = Object.assign({}, evt);
  delete clean._history;
  document.getElementById('event-edit-title').textContent = index >= 0 ? 'Edit Event' : 'New Event';
  document.getElementById('event-edit-yaml').value = eventToYaml(clean);
  document.getElementById('event-edit-delete').style.display = index >= 0 ? '' : 'none';
  document.getElementById('event-edit-history').style.display = 'none';
  renderEventHistory();
  openModal('event-edit-modal');
}

function renderEventHistory() {
  const list = document.getElementById('event-edit-history-list');
  list.innerHTML = '';
  if (!_eventEditHistory.length) {
    list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">No previous versions</div>';
    return;
  }
  [..._eventEditHistory].reverse().forEach((v, i) => {
    const item = document.createElement('div');
    item.className = 'thought-item';
    const ts = new Date(v.saved_at * 1000);
    item.innerHTML = '<div class="thought-item-time">v' + (_eventEditHistory.length - i) + ' - ' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('#event-edit-history-list .thought-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('event-edit-yaml').value = eventToYaml(v.event);
    });
    list.appendChild(item);

    const restoreBtn = document.createElement('button');
    restoreBtn.className = 'session-btn';
    restoreBtn.style.cssText = 'font-size:10px;padding:2px 8px;margin-top:4px;width:100%';
    restoreBtn.textContent = 'Restore';
    restoreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.getElementById('event-edit-yaml').value = eventToYaml(v.event);
    });
    item.appendChild(restoreBtn);
    list.appendChild(item);
  });
}

document.getElementById('event-edit-history-btn').addEventListener('click', () => {
  const panel = document.getElementById('event-edit-history');
  panel.style.display = panel.style.display === 'none' ? '' : 'none';
});

document.getElementById('event-edit-close').addEventListener('click', () => closeModal('event-edit-modal'));

document.getElementById('event-edit-save').addEventListener('click', async () => {
  let evt;
  try {
    evt = yamlToEvent(document.getElementById('event-edit-yaml').value);
  } catch(e) {
    showNotice('Invalid YAML: ' + e.message);
    return;
  }
  // Save version history
  if (_eventEditIndex >= 0) {
    const oldResp = await fetch('/api/events/pool');
    const oldPool = await oldResp.json();
    const oldEvt = oldPool[_eventEditIndex];
    if (oldEvt) {
      if (!evt._history) evt._history = oldEvt._history || [];
      const clean = Object.assign({}, oldEvt);
      delete clean._history;
      evt._history.push({event: clean, saved_at: Date.now() / 1000});
    }
    await fetch('/api/events/pool/' + _eventEditIndex, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(evt),
    });
  } else {
    evt._history = [];
    await fetch('/api/events/pool', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(evt),
    });
  }
  closeModal('event-edit-modal');
  loadEventPool();
});

document.getElementById('event-edit-delete').addEventListener('click', async () => {
  if (_eventEditIndex < 0) return;
  if (!confirm('Delete this event?')) return;
  await fetch('/api/events/pool/' + _eventEditIndex, {method: 'DELETE'});
  closeModal('event-edit-modal');
  loadEventPool();
});

document.getElementById('events-add-btn').addEventListener('click', () => {
  const template = {
    name: 'New Event',
    severity: 'medium',
    actions: [
      {type: 'message', channel: '#general', sender: 'System', content: 'Something happened!'}
    ]
  };
  openEventEditor(-1, template);
});

// Add Events tab loading to tab switch
// (handled in the existing tab switch handler below)

// -- Orchestrator status polling --

const orchDot = document.getElementById('orch-dot');
const orchLabel = document.getElementById('orch-label');
const STATUS_LABELS = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  waiting: 'Waiting for session',
  starting: 'Starting agents...',
  ready: 'Ready',
  responding: 'Responding...',
  stopping: 'Stopping agents...',
  restarting: 'Restarting...',
};

async function pollStatus() {
  try {
    const resp = await fetch('/api/status');
    const status = await resp.json();
    const state = status.orchestrator.state || 'disconnected';
    orchDot.className = 'status-dot ' + state;
    const msg = status.orchestrator.message;
    orchLabel.textContent = msg || STATUS_LABELS[state] || state;
    // Auto-refresh NPC and Usage tabs if visible
    if (currentTab === 'npcs') loadNPCs();
    if (currentTab === 'usage') loadUsage();
  } catch(e) {
    orchDot.className = 'status-dot disconnected';
    orchLabel.textContent = 'Server error';
  }
}

setInterval(pollStatus, 3000);
pollStatus();

// -- Session controls --

function showLoading(text) {
  document.getElementById('loading-text').textContent = text || 'Loading...';
  document.getElementById('loading-overlay').classList.add('open');
}
function hideLoading() {
  document.getElementById('loading-overlay').classList.remove('open');
}
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function showNotice(text) {
  // Show a non-blocking notice bar at the top of the page
  let bar = document.getElementById('notice-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'notice-bar';
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:999;background:var(--accent);color:var(--text-bright);padding:10px 20px;font-size:13px;display:flex;align-items:center;justify-content:space-between;';
    const dismiss = document.createElement('button');
    dismiss.textContent = 'Dismiss';
    dismiss.style.cssText = 'background:rgba(0,0,0,0.3);color:var(--text-bright);border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-left:16px;';
    dismiss.addEventListener('click', () => bar.remove());
    bar.appendChild(document.createElement('span'));
    bar.appendChild(dismiss);
    document.body.prepend(bar);
  }
  bar.querySelector('span').textContent = text;
}

async function reloadAllState() {
  messagesByChannel = {};
  seenIds.clear();
  unreadByChannel = {};
  _lastUsageData = null;
  currentChannel = '#general';
  await loadPersonas();
  await loadRoles();
  await loadChannels();
  await loadMessages();
  renderSidebar();
  renderMessages();
  loadFolders();
  loadDocs();
  loadRepos();
  loadTickets();
  loadNPCs();
  if (_eventsSubTab === 'pool') loadEventPool();
  else loadEventLog();
}

// -- New Session Modal --

document.getElementById('session-new-btn').addEventListener('click', async () => {
  const sel = document.getElementById('new-session-scenario');
  sel.innerHTML = '';
  document.getElementById('new-session-status').textContent = '';
  document.getElementById('new-session-name').value = '';
  const resp = await fetch('/api/session/scenarios');
  const scenarios = await resp.json();
  scenarios.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.key;
    opt.textContent = s.name + ' (' + s.characters + ' characters)';
    opt.dataset.desc = s.description || '';
    sel.appendChild(opt);
  });
  // Default to tech-startup if available, otherwise first
  const preferred = scenarios.find(s => s.key === 'tech-startup');
  if (preferred) sel.value = preferred.key;
  const selected = scenarios.find(s => s.key === sel.value) || scenarios[0];
  document.getElementById('new-session-scenario-desc').textContent = selected ? selected.description : '';
  openModal('new-session-modal');
});

document.getElementById('new-session-scenario').addEventListener('change', (e) => {
  const opt = e.target.selectedOptions[0];
  document.getElementById('new-session-scenario-desc').textContent = opt ? opt.dataset.desc : '';
});

document.getElementById('new-session-cancel').addEventListener('click', () => closeModal('new-session-modal'));

document.getElementById('new-session-confirm').addEventListener('click', async () => {
  const scenario = document.getElementById('new-session-scenario').value;
  if (!scenario) return;
  const status = document.getElementById('new-session-status');
  status.textContent = 'Creating session...';
  document.getElementById('new-session-confirm').disabled = true;
  closeModal('new-session-modal');
  showLoading('Creating new session...');
  try {
    await fetch('/api/session/new', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scenario}),
    });
    await reloadAllState();
    pollStatus();
  } finally {
    hideLoading();
    document.getElementById('new-session-confirm').disabled = false;
  }
});

// -- Save Session Modal --

async function _populateSaveSessionList() {
  const listEl = document.getElementById('save-session-list');
  listEl.innerHTML = '<div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">Loading...</div>';
  try {
    const resp = await fetch('/api/session/list');
    const sessions = await resp.json();
    if (sessions.length === 0) {
      listEl.innerHTML = '<div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">No existing saves</div>';
      return;
    }
    sessions.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
    listEl.innerHTML = '';
    sessions.forEach(s => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-dark)';
      row.addEventListener('mouseenter', () => row.style.background = 'var(--bg-hover, rgba(255,255,255,0.04))');
      row.addEventListener('mouseleave', () => row.style.background = '');
      const nameSpan = document.createElement('span');
      nameSpan.style.cssText = 'flex:1;color:var(--text);font-size:13px';
      nameSpan.textContent = s.name || s.instance_dir;
      const dateSpan = document.createElement('span');
      dateSpan.style.cssText = 'color:var(--text-dim);font-size:11px;margin-left:12px;white-space:nowrap';
      dateSpan.textContent = _fmtSessionDate(s.saved_at);
      row.appendChild(nameSpan);
      row.appendChild(dateSpan);
      row.addEventListener('click', () => {
        // Pre-fill with name + fresh timestamp for easy branching
        const now = new Date();
        const ts = now.getFullYear()
          + String(now.getMonth() + 1).padStart(2, '0')
          + String(now.getDate()).padStart(2, '0')
          + '-' + String(now.getHours()).padStart(2, '0')
          + String(now.getMinutes()).padStart(2, '0');
        const baseName = (s.name || s.instance_dir).replace(/--?[0-9]{4}-?[0-9]{2}-?[0-9]{2}-?[0-9]{4}$/, '').replace(/-[0-9]{8}-[0-9]{4}$/, '');
        document.getElementById('save-session-name').value = baseName + '-' + ts;
        document.getElementById('save-session-name').focus();
        // Highlight selected row
        listEl.querySelectorAll('div').forEach(r => r.style.borderLeft = '');
        row.style.borderLeft = '3px solid var(--accent)';
      });
      listEl.appendChild(row);
    });
  } catch (e) {
    listEl.innerHTML = '<div style="padding:12px;color:var(--accent);text-align:center;font-size:12px">Failed to load sessions</div>';
  }
}

document.getElementById('session-save-btn').addEventListener('click', async () => {
  document.getElementById('save-session-name').value = '';
  document.getElementById('save-session-status').textContent = '';
  openModal('save-session-modal');
  await _populateSaveSessionList();
  document.getElementById('save-session-name').focus();
});

document.getElementById('save-session-cancel').addEventListener('click', () => closeModal('save-session-modal'));

document.getElementById('save-session-confirm').addEventListener('click', async () => {
  const name = document.getElementById('save-session-name').value.trim();
  const status = document.getElementById('save-session-status');
  status.textContent = 'Saving...';
  document.getElementById('save-session-confirm').disabled = true;
  try {
    const resp = await fetch('/api/session/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name || undefined}),
    });
    if (resp.ok) {
      const meta = await resp.json();
      status.textContent = 'Saved: ' + (meta.name || meta.instance_dir);
      status.style.color = '#2ecc71';
      setTimeout(() => {
        closeModal('save-session-modal');
        status.style.color = '';
      }, 1500);
    } else {
      const err = await resp.json();
      status.textContent = 'Error: ' + (err.error || 'unknown');
      status.style.color = 'var(--accent)';
    }
  } finally {
    document.getElementById('save-session-confirm').disabled = false;
  }
});

// -- Load Session Modal --

let _lmSessions = [];
let _lmSortCol = 'saved_at';
let _lmSortAsc = false;
let _lmSelected = null;

function _lmUpdateSortArrows() {
  document.querySelectorAll('#load-session-table th[data-lm-sort]').forEach(th => {
    const arrow = th.querySelector('.lm-sort-arrow');
    if (th.dataset.lmSort === _lmSortCol) {
      arrow.textContent = _lmSortAsc ? ' \\u25B2' : ' \\u25BC';
      th.style.color = 'var(--text)';
    } else {
      arrow.textContent = '';
      th.style.color = 'var(--text-dim)';
    }
  });
}

function _lmRenderRows() {
  const tbody = document.getElementById('load-session-body');
  if (_lmSessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">No saved sessions</td></tr>';
    document.getElementById('load-session-confirm').disabled = true;
    return;
  }
  const sorted = [..._lmSessions];
  sorted.sort((a, b) => {
    let va, vb;
    if (_lmSortCol === 'name') {
      va = (a.name || a.instance_dir).toLowerCase();
      vb = (b.name || b.instance_dir).toLowerCase();
    } else if (_lmSortCol === 'scenario') {
      va = (a.scenario || '').toLowerCase();
      vb = (b.scenario || '').toLowerCase();
    } else {
      va = a[_lmSortCol] || 0;
      vb = b[_lmSortCol] || 0;
    }
    if (va < vb) return _lmSortAsc ? -1 : 1;
    if (va > vb) return _lmSortAsc ? 1 : -1;
    return 0;
  });
  tbody.innerHTML = '';
  sorted.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.cssText = 'border-bottom:1px solid var(--border-dark);cursor:pointer';
    if (_lmSelected === s.instance_dir) {
      tr.style.background = 'rgba(231,76,60,0.12)';
    }
    tr.addEventListener('mouseenter', () => { if (_lmSelected !== s.instance_dir) tr.style.background = 'var(--bg-hover, rgba(255,255,255,0.04))'; });
    tr.addEventListener('mouseleave', () => { if (_lmSelected !== s.instance_dir) tr.style.background = ''; });
    tr.addEventListener('click', () => {
      _lmSelected = s.instance_dir;
      document.getElementById('load-session-confirm').disabled = false;
      _lmRenderRows();
    });
    tr.addEventListener('dblclick', () => {
      _lmSelected = s.instance_dir;
      document.getElementById('load-session-confirm').click();
    });
    const nameTd = document.createElement('td');
    nameTd.style.cssText = 'padding:7px 10px;color:var(--text)';
    nameTd.textContent = s.name || s.instance_dir;
    const scenarioTd = document.createElement('td');
    scenarioTd.style.cssText = 'padding:7px 10px;color:var(--text-dim)';
    scenarioTd.textContent = s.scenario || '—';
    const createdTd = document.createElement('td');
    createdTd.style.cssText = 'padding:7px 10px;color:var(--text-dim);white-space:nowrap';
    createdTd.textContent = _fmtSessionDate(s.created_at);
    const savedTd = document.createElement('td');
    savedTd.style.cssText = 'padding:7px 10px;color:var(--text-dim);white-space:nowrap';
    savedTd.textContent = _fmtSessionDate(s.saved_at);
    tr.appendChild(nameTd);
    tr.appendChild(scenarioTd);
    tr.appendChild(createdTd);
    tr.appendChild(savedTd);
    tbody.appendChild(tr);
  });
  _lmUpdateSortArrows();
}

document.querySelectorAll('#load-session-table th[data-lm-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.lmSort;
    if (_lmSortCol === col) {
      _lmSortAsc = !_lmSortAsc;
    } else {
      _lmSortCol = col;
      _lmSortAsc = (col === 'name' || col === 'scenario');
    }
    _lmRenderRows();
  });
});

async function refreshSessionsList() {
  const tbody = document.getElementById('load-session-body');
  tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>';
  try {
    const resp = await fetch('/api/session/list');
    _lmSessions = await resp.json();
    _lmSelected = null;
    _lmRenderRows();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--accent);text-align:center">Failed to load sessions</td></tr>';
  }
}

// Replace header load dropdown with a button
{
  const headerSelect = document.getElementById('session-load-select');
  if (headerSelect) headerSelect.remove();
  const loadBtn = document.createElement('button');
  loadBtn.id = 'session-load-btn';
  loadBtn.className = 'session-btn';
  loadBtn.title = 'Load session';
  loadBtn.textContent = 'Load';
  document.getElementById('session-controls').appendChild(loadBtn);

  loadBtn.addEventListener('click', async () => {
    document.getElementById('load-session-status').textContent = '';
    document.getElementById('load-session-confirm').disabled = true;
    await refreshSessionsList();
    openModal('load-session-modal');
  });
}

document.getElementById('load-session-cancel').addEventListener('click', () => closeModal('load-session-modal'));

document.getElementById('load-session-confirm').addEventListener('click', async () => {
  if (!_lmSelected) return;
  const status = document.getElementById('load-session-status');
  status.textContent = 'Loading session...';
  document.getElementById('load-session-confirm').disabled = true;
  closeModal('load-session-modal');
  showLoading('Loading session...');
  try {
    const resp = await fetch('/api/session/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instance: _lmSelected}),
    });
    if (resp.ok) {
      await reloadAllState();
    } else {
      const err = await resp.json();
      hideLoading();
      openModal('load-session-modal');
      status.textContent = 'Error: ' + (err.error || 'unknown');
      status.style.color = 'var(--accent)';
      return;
    }
  } finally {
    hideLoading();
    document.getElementById('load-session-confirm').disabled = false;
  }
});
</script>
</body>
</html>"""


def create_app() -> Flask:
    """Create and configure the Flask chat application."""
    app = Flask(__name__)

    # Initialize channels, folders, and docs
    _init_channels()
    print(f"Channels initialized: {sorted(_channels.keys())}")

    _init_folders()
    print(f"Folders initialized: {sorted(_folders.keys())}")

    _init_docs()
    print(f"Docs directory ready: {DOCS_DIR}  ({len(_docs_index)} existing docs)")

    _init_gitlab()
    print(f"GitLab storage ready: {GITLAB_DIR}  ({len(_gitlab_repos)} existing repos)")

    _init_tickets()
    print(f"Tickets storage ready: {TICKETS_DIR}  ({len(_tickets)} existing tickets)")

    _init_agent_online()

    _load_chat_log()
    print(f"Chat log: {len(_messages)} messages loaded")

    @app.route("/")
    def index():
        return WEB_UI

    # -- Channel API --

    @app.route("/api/channels", methods=["GET"])
    def list_channels():
        with _channel_lock:
            result = []
            for name, info in sorted(_channels.items()):
                members = sorted(_channel_members.get(name, set()))
                entry = {
                    "name": name,
                    "description": info["description"],
                    "is_external": info["is_external"],
                    "members": members,
                }
                if info.get("is_system"):
                    entry["is_system"] = True
                if info.get("is_director"):
                    entry["is_director"] = True
                    entry["director_persona"] = info.get("director_persona", "")
                result.append(entry)
        return jsonify(result)

    @app.route("/api/channels/<path:name>/join", methods=["POST"])
    def join_channel(name):
        # Handle URL-encoded '#'
        if not name.startswith("#"):
            name = "#" + name
        data = request.get_json(force=True)
        persona = data.get("persona", "").strip()
        if not persona:
            return jsonify({"error": "persona required"}), 400

        with _channel_lock:
            if name not in _channels:
                return jsonify({"error": f"channel '{name}' not found"}), 404
            _channel_members.setdefault(name, set()).add(persona)
            members = sorted(_channel_members[name])

        _broadcast_channel_update(name, members)
        print(f"Channel join: {persona} -> {name}")
        return jsonify({"channel": name, "members": members})

    @app.route("/api/channels/<path:name>/leave", methods=["POST"])
    def leave_channel(name):
        if not name.startswith("#"):
            name = "#" + name
        data = request.get_json(force=True)
        persona = data.get("persona", "").strip()
        if not persona:
            return jsonify({"error": "persona required"}), 400

        with _channel_lock:
            if name not in _channels:
                return jsonify({"error": f"channel '{name}' not found"}), 404
            _channel_members.get(name, set()).discard(persona)
            members = sorted(_channel_members.get(name, set()))

        _broadcast_channel_update(name, members)
        print(f"Channel leave: {persona} <- {name}")
        return jsonify({"channel": name, "members": members})

    # -- Messages --

    @app.route("/api/messages", methods=["GET"])
    def get_messages():
        since = request.args.get("since", type=int)
        channels_param = request.args.get("channels", type=str)
        with _lock:
            result = list(_messages)
            if since is not None:
                result = [m for m in result if m["id"] > since]
            if channels_param is not None:
                ch_set = set()
                for c in channels_param.split(","):
                    c = c.strip()
                    if not c.startswith("#"):
                        c = "#" + c
                    ch_set.add(c)
                result = [m for m in result if m.get("channel", "#general") in ch_set]
        return jsonify(result)

    @app.route("/api/messages", methods=["POST"])
    def post_message():
        data = request.get_json(force=True)
        sender = data.get("sender", "").strip()
        content = data.get("content", "").strip()
        channel = data.get("channel", "#general").strip()
        if not sender or not content:
            return jsonify({"error": "sender and content required"}), 400
        with _channel_lock:
            if channel not in _channels:
                return jsonify({"error": f"unknown channel: {channel}"}), 400
        with _lock:
            msg = {
                "id": len(_messages) + 1,
                "sender": sender,
                "content": content,
                "channel": channel,
                "timestamp": time.time(),
            }
            _messages.append(msg)
        _persist_message(msg)
        _broadcast(msg)
        return jsonify(msg), 201

    @app.route("/api/messages/clear", methods=["POST"])
    def clear_messages():
        with _lock:
            _messages.clear()
        if CHAT_LOG.exists():
            CHAT_LOG.unlink()
        return jsonify({"status": "cleared"})

    @app.route("/api/messages/stream")
    def stream():
        def generate():
            q = queue.Queue(maxsize=256)
            with _sub_lock:
                _subscribers.append(q)
            try:
                while True:
                    try:
                        data = q.get(timeout=30)
                        yield f"event: message\ndata: {data}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            except GeneratorExit:
                with _sub_lock:
                    if q in _subscribers:
                        _subscribers.remove(q)

        return Response(generate(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # -- Folder API --

    @app.route("/api/folders", methods=["GET"])
    def list_folders():
        with _folder_lock:
            result = []
            for name in sorted(_folders.keys()):
                info = _folders[name]
                access = sorted(_folder_access.get(name, set()))
                result.append({
                    "name": name,
                    "type": info["type"],
                    "description": info["description"],
                    "access": access,
                })
        return jsonify(result)

    # -- Document API --

    @app.route("/api/docs", methods=["GET"])
    def list_docs():
        folder_filter = request.args.get("folder")
        with _docs_lock:
            docs = list(_docs_index.values())
        if folder_filter:
            docs = [d for d in docs if d.get("folder") == folder_filter]
        return jsonify(docs)

    @app.route("/api/docs", methods=["POST"])
    def create_doc():
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        content = data.get("content", "")
        author = data.get("author", "unknown")
        folder = data.get("folder", "shared").strip()
        if not title:
            return jsonify({"error": "title required"}), 400

        with _folder_lock:
            if folder not in _folders:
                return jsonify({"error": f"folder '{folder}' not found"}), 400

        slug = slugify(title)
        folder_dir = DOCS_DIR / folder
        folder_dir.mkdir(parents=True, exist_ok=True)
        doc_path = folder_dir / f"{slug}.txt"

        with _docs_lock:
            if slug in _docs_index:
                return jsonify({"error": f"document '{slug}' already exists"}), 409

            doc_path.write_text(content, encoding="utf-8")
            now = time.time()
            meta = {
                "slug": slug,
                "title": title,
                "folder": folder,
                "created_at": now,
                "updated_at": now,
                "created_by": author,
                "size": len(content.encode("utf-8")),
                "preview": content[:100],
            }
            _docs_index[slug] = meta
            _save_index()

        _broadcast_doc_event("created", meta)
        return jsonify(meta), 201

    @app.route("/api/docs/search", methods=["GET"])
    def search_docs():
        query = request.args.get("q", "").strip().lower()
        folders_param = request.args.get("folders", "")
        if not query:
            return jsonify([])

        folder_filter = None
        if folders_param:
            folder_filter = {f.strip() for f in folders_param.split(",") if f.strip()}

        results = []
        with _docs_lock:
            for slug, meta in _docs_index.items():
                if folder_filter and meta.get("folder") not in folder_filter:
                    continue
                folder = meta.get("folder", "shared")
                doc_path = DOCS_DIR / folder / f"{slug}.txt"
                if not doc_path.exists():
                    continue
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                if query in meta.get("title", "").lower() or query in content.lower():
                    results.append({
                        **meta,
                        "snippet": _extract_snippet(content, query),
                    })
        return jsonify(results)

    @app.route("/api/docs/<folder>/<slug>", methods=["GET"])
    def get_doc(folder, slug):
        with _docs_lock:
            meta = _docs_index.get(slug)
        if meta is None or meta.get("folder") != folder:
            return jsonify({"error": "not found"}), 404
        doc_path = DOCS_DIR / folder / f"{slug}.txt"
        if not doc_path.exists():
            return jsonify({"error": "not found"}), 404
        content = doc_path.read_text(encoding="utf-8", errors="replace")
        return jsonify({**meta, "content": content})

    @app.route("/api/docs/<folder>/<slug>", methods=["PUT"])
    def update_doc(folder, slug):
        data = request.get_json(force=True)
        content = data.get("content", "")
        author = data.get("author", "unknown")

        with _docs_lock:
            meta = _docs_index.get(slug)
            if meta is None or meta.get("folder") != folder:
                return jsonify({"error": "not found"}), 404

            doc_path = DOCS_DIR / folder / f"{slug}.txt"

            # Save current content as a version before overwriting
            old_content = ""
            if doc_path.exists():
                old_content = doc_path.read_text(encoding="utf-8", errors="replace")
            if "history" not in meta:
                meta["history"] = []
            meta["history"].append({
                "content": old_content,
                "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
                "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
            })

            doc_path.write_text(content, encoding="utf-8")
            meta["updated_at"] = time.time()
            meta["updated_by"] = author
            meta["size"] = len(content.encode("utf-8"))
            meta["preview"] = content[:100]
            _save_index()

        _broadcast_doc_event("updated", meta)
        return jsonify(meta)

    @app.route("/api/docs/<folder>/<slug>/history", methods=["GET"])
    def get_doc_history(folder, slug):
        with _docs_lock:
            meta = _docs_index.get(slug)
        if meta is None or meta.get("folder") != folder:
            return jsonify({"error": "not found"}), 404
        history = meta.get("history", [])
        # Add current version as the first entry
        doc_path = DOCS_DIR / folder / f"{slug}.txt"
        current_content = ""
        if doc_path.exists():
            current_content = doc_path.read_text(encoding="utf-8", errors="replace")
        current = {
            "content": current_content,
            "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
            "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
            "is_current": True,
        }
        return jsonify([current] + list(reversed(history)))

    @app.route("/api/docs/<folder>/<slug>/append", methods=["POST"])
    def append_doc(folder, slug):
        data = request.get_json(force=True)
        content = data.get("content", "")
        author = data.get("author", "unknown")

        with _docs_lock:
            meta = _docs_index.get(slug)
            if meta is None or meta.get("folder") != folder:
                return jsonify({"error": "not found"}), 404

            doc_path = DOCS_DIR / folder / f"{slug}.txt"
            existing = doc_path.read_text(encoding="utf-8", errors="replace")

            # Save current content as a version before appending
            if "history" not in meta:
                meta["history"] = []
            meta["history"].append({
                "content": existing,
                "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
                "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
            })

            new_content = existing + "\n" + content
            doc_path.write_text(new_content, encoding="utf-8")
            meta["updated_at"] = time.time()
            meta["updated_by"] = author
            meta["size"] = len(new_content.encode("utf-8"))
            meta["preview"] = new_content[:100]
            _save_index()

        _broadcast_doc_event("appended", meta)
        return jsonify(meta)

    @app.route("/api/docs/<folder>/<slug>", methods=["DELETE"])
    def delete_doc(folder, slug):
        with _docs_lock:
            meta = _docs_index.get(slug)
            if meta is None or meta.get("folder") != folder:
                return jsonify({"error": "not found"}), 404
            _docs_index.pop(slug)

            doc_path = DOCS_DIR / folder / f"{slug}.txt"
            if doc_path.exists():
                doc_path.unlink()
            _save_index()

        _broadcast_doc_event("deleted", meta)
        return jsonify({"status": "deleted", "slug": slug, "folder": folder})

    # -- Backward-compatible flat doc routes (redirect to shared) --

    @app.route("/api/docs/<slug>", methods=["GET"])
    def get_doc_flat(slug):
        """Backward-compatible: look up doc by slug alone."""
        with _docs_lock:
            meta = _docs_index.get(slug)
        if meta is None:
            return jsonify({"error": "not found"}), 404
        folder = meta.get("folder", "shared")
        doc_path = DOCS_DIR / folder / f"{slug}.txt"
        if not doc_path.exists():
            return jsonify({"error": "not found"}), 404
        content = doc_path.read_text(encoding="utf-8", errors="replace")
        return jsonify({**meta, "content": content})

    # -- GitLab API --

    @app.route("/api/gitlab/repos", methods=["GET"])
    def list_gitlab_repos():
        with _gitlab_lock:
            repos = list(_gitlab_repos.values())
        return jsonify(repos)

    @app.route("/api/gitlab/repos", methods=["POST"])
    def create_gitlab_repo():
        data = request.get_json(force=True)
        name = data.get("name", "").strip()
        description = data.get("description", "")
        author = data.get("author", "unknown")
        if not name:
            return jsonify({"error": "name required"}), 400

        with _gitlab_lock:
            if name in _gitlab_repos:
                return jsonify({"error": f"repo '{name}' already exists"}), 409

            now = time.time()
            meta = {
                "name": name,
                "description": description,
                "created_by": author,
                "created_at": now,
            }
            _gitlab_repos[name] = meta
            _gitlab_commits[name] = []

            # Persist to disk
            repo_dir = GITLAB_DIR / name / "files"
            repo_dir.mkdir(parents=True, exist_ok=True)
            commits_path = GITLAB_DIR / name / "_commits.json"
            commits_path.write_text("[]")
            save_repos_index(dict(_gitlab_repos))

        _broadcast_gitlab_event("repo_created", meta)
        return jsonify(meta), 201

    @app.route("/api/gitlab/repos/<project>/tree", methods=["GET"])
    def gitlab_tree(project):
        path = request.args.get("path", "").strip().strip("/")
        with _gitlab_lock:
            if project not in _gitlab_repos:
                return jsonify({"error": "repo not found"}), 404

        files_dir = GITLAB_DIR / project / "files"
        if path:
            target = files_dir / path
        else:
            target = files_dir

        if not target.exists() or not target.is_dir():
            return jsonify([])

        entries = []
        for item in sorted(target.iterdir()):
            rel = str(item.relative_to(files_dir))
            entry = {"name": item.name, "path": rel}
            if item.is_dir():
                entry["type"] = "dir"
            else:
                entry["type"] = "file"
            entries.append(entry)

        # Sort: dirs first, then files
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"]))
        return jsonify(entries)

    @app.route("/api/gitlab/repos/<project>/file", methods=["GET"])
    def gitlab_file(project):
        path = request.args.get("path", "").strip()
        if not path:
            return jsonify({"error": "path required"}), 400

        with _gitlab_lock:
            if project not in _gitlab_repos:
                return jsonify({"error": "repo not found"}), 404

        file_path = GITLAB_DIR / project / "files" / path
        if not file_path.exists() or not file_path.is_file():
            return jsonify({"error": "file not found"}), 404

        content = file_path.read_text(encoding="utf-8", errors="replace")
        return jsonify({"path": path, "content": content})

    @app.route("/api/gitlab/repos/<project>/commit", methods=["POST"])
    def gitlab_commit(project):
        data = request.get_json(force=True)
        message = data.get("message", "").strip()
        files = data.get("files", [])
        author = data.get("author", "unknown")
        if not message:
            return jsonify({"error": "message required"}), 400
        if not files:
            return jsonify({"error": "files required"}), 400

        with _gitlab_lock:
            if project not in _gitlab_repos:
                return jsonify({"error": "repo not found"}), 404

            now = time.time()
            commit_id = generate_commit_id(message, author, now)

            # Write files to disk
            files_dir = GITLAB_DIR / project / "files"
            paths = []
            for f in files:
                fp = files_dir / f["path"]
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(f["content"], encoding="utf-8")
                paths.append(f["path"])

            commit = {
                "id": commit_id,
                "message": message,
                "author": author,
                "timestamp": now,
                "files": paths,
            }
            _gitlab_commits.setdefault(project, []).append(commit)

            # Persist commits
            commits_path = GITLAB_DIR / project / "_commits.json"
            commits_path.write_text(json.dumps(_gitlab_commits[project], indent=2))

        _broadcast_gitlab_event("committed", {"project": project, "commit": commit})
        return jsonify(commit), 201

    @app.route("/api/gitlab/repos/<project>/log", methods=["GET"])
    def gitlab_log(project):
        with _gitlab_lock:
            if project not in _gitlab_repos:
                return jsonify({"error": "repo not found"}), 404
            commits = list(_gitlab_commits.get(project, []))
        # Return newest first
        commits.reverse()
        return jsonify(commits)

    # -- Tickets API --

    @app.route("/api/tickets", methods=["GET"])
    def list_tickets():
        status_filter = request.args.get("status")
        assignee_filter = request.args.get("assignee")
        with _tickets_lock:
            tickets = list(_tickets.values())
        if status_filter:
            tickets = [t for t in tickets if t.get("status") == status_filter]
        if assignee_filter:
            tickets = [t for t in tickets if t.get("assignee") == assignee_filter]
        return jsonify(tickets)

    @app.route("/api/tickets", methods=["POST"])
    def create_ticket():
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        description = data.get("description", "")
        priority = data.get("priority", "medium").strip()
        assignee = data.get("assignee", "").strip()
        author = data.get("author", "unknown").strip()
        blocked_by = data.get("blocked_by", [])
        if not title:
            return jsonify({"error": "title required"}), 400
        if priority not in ("low", "medium", "high", "critical"):
            return jsonify({"error": "priority must be low/medium/high/critical"}), 400

        now = time.time()
        ticket_id = generate_ticket_id(title, now)

        with _tickets_lock:
            if ticket_id in _tickets:
                # Unlikely collision — append a char
                ticket_id = ticket_id + "X"

            ticket = {
                "id": ticket_id,
                "title": title,
                "description": description,
                "status": "open",
                "priority": priority,
                "assignee": assignee,
                "created_by": author,
                "created_at": now,
                "updated_at": now,
                "comments": [],
                "blocked_by": [],
                "blocks": [],
            }

            # Set up dependencies
            if blocked_by:
                if isinstance(blocked_by, str):
                    blocked_by = [b.strip() for b in blocked_by.split(",") if b.strip()]
                for dep_id in blocked_by:
                    if dep_id in _tickets:
                        ticket["blocked_by"].append(dep_id)
                        if ticket_id not in _tickets[dep_id].get("blocks", []):
                            _tickets[dep_id].setdefault("blocks", []).append(ticket_id)

            _tickets[ticket_id] = ticket
            save_tickets_index(dict(_tickets))

        _broadcast_tickets_event("created", ticket)
        return jsonify(ticket), 201

    @app.route("/api/tickets/<ticket_id>", methods=["GET"])
    def get_ticket(ticket_id):
        with _tickets_lock:
            ticket = _tickets.get(ticket_id)
        if ticket is None:
            return jsonify({"error": "ticket not found"}), 404
        return jsonify(ticket)

    @app.route("/api/tickets/<ticket_id>", methods=["PUT"])
    def update_ticket(ticket_id):
        data = request.get_json(force=True)
        with _tickets_lock:
            ticket = _tickets.get(ticket_id)
            if ticket is None:
                return jsonify({"error": "ticket not found"}), 404

            if "status" in data:
                status = data["status"].strip()
                if status not in ("open", "in_progress", "resolved", "closed"):
                    return jsonify({"error": "invalid status"}), 400
                ticket["status"] = status
            if "assignee" in data:
                ticket["assignee"] = data["assignee"].strip()
            if "priority" in data:
                priority = data["priority"].strip()
                if priority in ("low", "medium", "high", "critical"):
                    ticket["priority"] = priority

            ticket["updated_at"] = time.time()
            save_tickets_index(dict(_tickets))

        _broadcast_tickets_event("updated", ticket)
        return jsonify(ticket)

    @app.route("/api/tickets/<ticket_id>/comment", methods=["POST"])
    def comment_ticket(ticket_id):
        data = request.get_json(force=True)
        text = data.get("text", "").strip()
        author = data.get("author", "unknown").strip()
        if not text:
            return jsonify({"error": "text required"}), 400

        with _tickets_lock:
            ticket = _tickets.get(ticket_id)
            if ticket is None:
                return jsonify({"error": "ticket not found"}), 404

            comment = {
                "author": author,
                "text": text,
                "timestamp": time.time(),
            }
            ticket.setdefault("comments", []).append(comment)
            ticket["updated_at"] = time.time()
            save_tickets_index(dict(_tickets))

        _broadcast_tickets_event("commented", {"ticket_id": ticket_id, "comment": comment})
        return jsonify(ticket), 201

    @app.route("/api/tickets/<ticket_id>/depends", methods=["POST"])
    def ticket_depends(ticket_id):
        data = request.get_json(force=True)
        blocked_by = data.get("blocked_by", "").strip()
        if not blocked_by:
            return jsonify({"error": "blocked_by required"}), 400

        with _tickets_lock:
            ticket = _tickets.get(ticket_id)
            if ticket is None:
                return jsonify({"error": "ticket not found"}), 404

            dep_ids = [b.strip() for b in blocked_by.split(",") if b.strip()]
            for dep_id in dep_ids:
                if dep_id not in _tickets:
                    continue
                if dep_id not in ticket.get("blocked_by", []):
                    ticket.setdefault("blocked_by", []).append(dep_id)
                if ticket_id not in _tickets[dep_id].get("blocks", []):
                    _tickets[dep_id].setdefault("blocks", []).append(ticket_id)

            ticket["updated_at"] = time.time()
            save_tickets_index(dict(_tickets))

        _broadcast_tickets_event("depends_updated", ticket)
        return jsonify(ticket)

    # -- Status & Control API --

    @app.route("/api/status", methods=["GET"])
    def get_status():
        with _orchestrator_lock:
            orch = dict(_orchestrator_status)
            # Mark as disconnected if no heartbeat in 15 seconds
            if orch["last_heartbeat"] == 0 or time.time() - orch["last_heartbeat"] > 30:
                orch["state"] = "disconnected"
        return jsonify({
            "server": "running",
            "scenario": get_current_session().get("scenario"),
            "messages": len(_messages),
            "documents": len(_docs_index),
            "repos": len(_gitlab_repos),
            "tickets": len(_tickets),
            "channels": len(_channels),
            "orchestrator": orch,
        })

    @app.route("/api/orchestrator/heartbeat", methods=["POST"])
    def orchestrator_heartbeat():
        data = request.get_json(force=True)
        with _orchestrator_lock:
            _orchestrator_status["state"] = data.get("state", "ready")
            _orchestrator_status["scenario"] = data.get("scenario")
            _orchestrator_status["agents"] = data.get("agents", {})
            _orchestrator_status["last_heartbeat"] = time.time()
            _orchestrator_status["message"] = data.get("message", "")
        # Return any pending command (only if caller wants to check)
        if data.get("check_commands", True):
            with _command_lock:
                if _orchestrator_commands:
                    cmd = _orchestrator_commands.pop(0)
                    print(f"[cmd queue] consumed: {cmd.get('action')} key={cmd.get('key','')} (remaining: {len(_orchestrator_commands)})")
                else:
                    cmd = {"action": None}
                return jsonify(cmd)
        return jsonify({"action": None})

    @app.route("/api/orchestrator/command", methods=["POST"])
    def orchestrator_command():
        data = request.get_json(force=True)
        action = data.get("action")
        if action not in ("restart", "shutdown", "add_agent", "remove_agent", None):
            return jsonify({"error": "invalid action"}), 400
        with _command_lock:
            _orchestrator_commands.append(data)
            print(f"[cmd queue] added: {action} (queue size: {len(_orchestrator_commands)})")
        return jsonify({"queued": action})

    # -- Typing indicators --

    @app.route("/api/typing", methods=["POST"])
    def typing_indicator():
        data = request.get_json(force=True)
        event = {
            "type": "typing",
            "sender": data.get("sender", ""),
            "channel": data.get("channel", "#general"),
            "active": data.get("active", True),
        }
        _broadcast(event)
        return jsonify({"ok": True})

    # -- NPC API --

    @app.route("/api/npcs", methods=["GET"])
    def list_npcs():
        from lib.personas import PERSONAS, RESPONSE_TIERS, PERSONA_TIER, DEFAULT_MEMBERSHIPS
        from lib.docs import get_accessible_folders
        from lib.gitlab import DEFAULT_REPO_ACCESS
        all_repo_names = sorted(_gitlab_repos.keys())
        result = []
        with _orchestrator_lock:
            agent_states = _orchestrator_status.get("agents", {})
            orch_state = _orchestrator_status.get("state", "disconnected")
            last_hb = _orchestrator_status.get("last_heartbeat", 0)
        orch_connected = last_hb > 0 and (time.time() - last_hb < 30)
        with _agent_online_lock:
            for key, p in PERSONAS.items():
                channels = sorted(DEFAULT_MEMBERSHIPS.get(key, set()))
                folders = sorted(get_accessible_folders(key))
                # Repos: if no access control, all repos; otherwise filter
                if DEFAULT_REPO_ACCESS:
                    repos = sorted(r for r in all_repo_names
                                   if r not in DEFAULT_REPO_ACCESS or key in DEFAULT_REPO_ACCESS.get(r, set()))
                else:
                    repos = all_repo_names
                toggled_online = _agent_online.get(key, True)
                # Determine live state from orchestrator heartbeat
                agent_info = agent_states.get(key, {})
                is_firing = key in _agent_firing
                if not orch_connected:
                    if is_firing:
                        live_state = "firing"
                    else:
                        # Check per-agent activity from MCP hooks
                        activity = _agent_last_activity.get(key, {})
                        last_active = activity.get("timestamp", 0)
                        if time.time() - last_active < 60:
                            live_state = "responding"
                        else:
                            live_state = "disconnected"
                elif is_firing:
                    live_state = "firing"
                elif not toggled_online:
                    live_state = "offline"
                else:
                    live_state = agent_info.get("state", "unknown")
                result.append({
                    "key": key,
                    "display_name": p["display_name"],
                    "team_description": p.get("team_description", ""),
                    "character_file": p.get("character_file", ""),
                    "avatar": p.get("avatar"),
                    "tier": PERSONA_TIER.get(key, 0),
                    "channels": channels,
                    "folders": folders,
                    "repos": repos,
                    "online": toggled_online,
                    "verbosity": _agent_verbosity.get(key, "normal"),
                    "live_state": live_state,
                })
        return jsonify(result)

    @app.route("/api/npcs/<key>/toggle", methods=["POST"])
    def toggle_npc(key):
        from lib.personas import PERSONAS
        if key not in PERSONAS:
            return jsonify({"error": f"unknown agent: {key}"}), 404
        display_name = PERSONAS[key]["display_name"]
        with _agent_online_lock:
            current = _agent_online.get(key, True)
            _agent_online[key] = not current
            new_state = _agent_online[key]
        # Post system message
        if new_state:
            msg = f"{display_name} is back online"
        else:
            msg = f"{display_name} is now out of office"
        with _lock:
            sys_msg = {
                "id": len(_messages) + 1,
                "sender": "System",
                "content": msg,
                "channel": "#system",
                "timestamp": time.time(),
            }
            _messages.append(sys_msg)
        _persist_message(sys_msg)
        _broadcast(sys_msg)
        return jsonify({"key": key, "online": new_state, "display_name": display_name})

    @app.route("/api/npcs/<key>/activity", methods=["POST"])
    def npc_activity(key):
        """Record agent activity from MCP hook events (implicit heartbeat)."""
        data = request.get_json(force=True)
        with _agent_online_lock:
            _agent_last_activity[key] = {
                "timestamp": time.time(),
                "event_type": data.get("event_type", "unknown"),
                "detail": data.get("detail", ""),
            }
        return jsonify({"ok": True})

    # -- Events API --

    @app.route("/api/events/pool", methods=["GET"])
    def get_event_pool():
        from lib.events import get_event_pool as _get_pool
        return jsonify(_get_pool())

    @app.route("/api/events/pool", methods=["POST"])
    def add_event_to_pool():
        from lib.events import add_event
        data = request.get_json(force=True)
        idx = add_event(data)
        return jsonify({"ok": True, "index": idx}), 201

    @app.route("/api/events/pool/<int:index>", methods=["PUT"])
    def update_event_in_pool(index):
        from lib.events import update_event
        data = request.get_json(force=True)
        update_event(index, data)
        return jsonify({"ok": True})

    @app.route("/api/events/pool/<int:index>", methods=["DELETE"])
    def delete_event_from_pool(index):
        from lib.events import delete_event
        delete_event(index)
        return jsonify({"ok": True})

    @app.route("/api/events/trigger", methods=["POST"])
    def trigger_event():
        from lib.events import fire_event
        data = request.get_json(force=True)
        results = []
        # Execute each action
        for action in data.get("actions", []):
            action_type = action.get("type", "")
            if action_type == "message":
                sender = action.get("sender", "System")
                content = action.get("content", "")
                channel = action.get("channel", "#general")
                with _lock:
                    msg = {
                        "id": len(_messages) + 1,
                        "sender": sender,
                        "content": content,
                        "channel": channel,
                        "timestamp": time.time(),
                        "is_event": True,
                    }
                    _messages.append(msg)
                _persist_message(msg)
                _broadcast(msg)
                results.append({"type": "message", "channel": channel, "sender": sender})
            elif action_type == "ticket":
                title = action.get("title", "")
                if title:
                    author = action.get("author", "System")
                    ticket_id = generate_ticket_id(title, time.time())
                    now = time.time()
                    ticket = {
                        "id": ticket_id,
                        "title": title,
                        "description": action.get("description", ""),
                        "status": "open",
                        "priority": action.get("priority", "medium"),
                        "assignee": action.get("assignee", ""),
                        "created_by": author,
                        "created_at": now,
                        "updated_at": now,
                        "comments": [],
                        "blocked_by": [],
                        "blocks": [],
                    }
                    with _tickets_lock:
                        _tickets[ticket_id] = ticket
                        save_tickets_index(dict(_tickets))
                    _broadcast_tickets_event("created", ticket)
                    results.append({"type": "ticket", "id": ticket_id, "title": title})
            elif action_type == "document":
                title = action.get("title", "")
                if title:
                    from lib.docs import slugify
                    author = action.get("author", "System")
                    folder = action.get("folder", "shared")
                    content = action.get("content", "")
                    slug = slugify(title)
                    folder_dir = DOCS_DIR / folder
                    folder_dir.mkdir(parents=True, exist_ok=True)
                    doc_path = folder_dir / f"{slug}.txt"
                    with _docs_lock:
                        if slug not in _docs_index:
                            doc_path.write_text(content, encoding="utf-8")
                            now = time.time()
                            meta = {
                                "slug": slug,
                                "title": title,
                                "folder": folder,
                                "created_at": now,
                                "updated_at": now,
                                "created_by": author,
                                "size": len(content.encode("utf-8")),
                                "preview": content[:100],
                            }
                            _docs_index[slug] = meta
                            _save_index()
                            _broadcast_doc_event("created", meta)
                            results.append({"type": "document", "title": title, "folder": folder, "slug": slug})
            elif action_type == "email":
                from lib.email import send_email
                sender = action.get("sender", action.get("from", "System"))
                subject = action.get("subject", "")
                body = action.get("body", action.get("content", ""))
                if subject:
                    entry = send_email(sender, subject, body)
                    results.append({"type": "email", "id": entry["id"], "subject": subject})
                    # Also post to #announcements so agents see it in chat
                    with _lock:
                        msg = {
                            "id": len(_messages) + 1,
                            "sender": sender,
                            "content": f"**[EMAIL] {subject}**\n\n{body}",
                            "channel": "#announcements",
                            "timestamp": time.time(),
                        }
                        _messages.append(msg)
                    _persist_message(msg)
                    _broadcast(msg)
            elif action_type == "memo":
                from lib.memos import create_thread, post_memo
                title = action.get("title", action.get("thread_title", ""))
                creator = action.get("sender", action.get("creator", "System"))
                description = action.get("description", "")
                text = action.get("text", action.get("content", ""))
                thread_id = action.get("thread_id", "")
                if thread_id and text:
                    # Post to existing thread
                    try:
                        post = post_memo(thread_id, text, creator)
                        results.append({"type": "memo", "action": "posted", "thread_id": thread_id, "post_id": post["id"]})
                    except ValueError:
                        results.append({"type": "memo", "action": "post_failed", "error": "thread not found"})
                elif title:
                    # Create new thread, optionally with an initial post
                    thread = create_thread(title, creator, description)
                    if text:
                        post_memo(thread["id"], text, creator)
                    results.append({"type": "memo", "action": "created", "thread_id": thread["id"], "title": title})
            elif action_type == "blog":
                from lib.blog import create_post as create_blog, reply_to_post as reply_blog
                title = action.get("title", "")
                body = action.get("body", action.get("content", ""))
                creator = action.get("sender", action.get("author", "System"))
                is_external = action.get("is_external", False)
                tags = action.get("tags", [])
                post_slug = action.get("post_slug", "")
                text = action.get("text", "")
                if post_slug and text:
                    # Reply to existing post
                    try:
                        reply = reply_blog(post_slug, text, creator)
                        results.append({"type": "blog", "action": "replied", "post_slug": post_slug, "reply_id": reply["id"]})
                    except ValueError:
                        results.append({"type": "blog", "action": "reply_failed", "error": "post not found"})
                elif title:
                    # Create new blog post
                    post = create_blog(title, body, creator, is_external=is_external, tags=tags)
                    results.append({"type": "blog", "action": "created", "slug": post["slug"], "title": title})
        # Log the event with results
        data["results"] = results
        entry = fire_event(data)
        return jsonify(entry)

    @app.route("/api/events/log", methods=["GET"])
    def get_events_log():
        from lib.events import get_event_log
        return jsonify(get_event_log())

    # -- Recap API --

    @app.route("/api/recaps", methods=["GET"])
    def list_recaps():
        return jsonify(_recaps)

    @app.route("/api/recap", methods=["POST"])
    def generate_recap():
        import asyncio
        import threading
        from lib.agent_runner import run_agent_for_response
        from pathlib import Path

        data = request.get_json(force=True)
        style = data.get("style", "normal")
        print(f"[recap] Generating recap in style: {style}")

        STYLE_PROMPTS = {
            "normal": "Write a clear, professional summary of what happened.",
            "ye-olde-english": "Write the recap in Ye Olde English, with 'thee', 'thou', 'hath', 'forsooth', and medieval phrasing throughout.",
            "tolkien": "Write the recap as if it were a passage from The Lord of the Rings — epic, sweeping, with references to quests, fellowships, and dark forces.",
            "star-wars": "Write the recap as a Star Wars opening crawl. Start with 'A long time ago, in a codebase far, far away...' and use space opera drama.",
            "star-trek": "Write the recap as a Captain's Log entry. 'Captain's Log, Stardate...' Include references to the crew, away missions, and the prime directive.",
            "dr-who": "Write the recap as if The Doctor is explaining what happened to a confused companion. Wibbly-wobbly, timey-wimey.",
            "morse-code": "Write the recap normally but add STOP after each sentence, like a telegraph message. Keep it terse.",
            "dr-seuss": "Write the recap in the style of Dr. Seuss — rhyming couplets, whimsical language, 'I do not like green bugs in prod, I do not like them, oh my cod.'",
            "shakespeare": "Write the recap as a Shakespearean monologue. Iambic pentameter where possible. Include asides and dramatic declarations.",
            "80s-rock-ballad": "Write the recap as lyrics to an 80s power ballad. Include a key change, a guitar solo section [GUITAR SOLO], and dramatic crescendo.",
            "90s-alternative": "Write the recap in the style of 90s alternative rock lyrics — angsty, introspective, ironic detachment about the state of the codebase.",
            "heavy-metal": "Write the recap as HEAVY METAL lyrics. ALL CAPS for emphasis. References to DESTRUCTION, CHAOS, DEPLOYING TO PRODUCTION, and THE ETERNAL VOID OF TECHNICAL DEBT.",
            "dystopian": "Write the recap as a dystopian narrative. The company is a megacorp. The codebase is a surveillance system. Compliance training is re-education. The open office is a panopticon. Hope is a deprecated feature.",
            "matrix": "Write the recap as if Morpheus is explaining what happened to Neo. 'What if I told you...' References to the Matrix, agents (the bad kind), red pills, blue pills, the desert of the real. The codebase IS the Matrix.",
            "pharaoh": "Write the recap as a royal decree from a Pharaoh. 'So let it be written, so let it be done.' Grand proclamations about what was commanded and what was achieved. References to building monuments (features), the Nile (the data pipeline), golden treasures (shipped code), and scribes (developers). End each major point with 'So let it be written, so let it be done.'",
            "tombstone": "Write the recap in the style of a Western, Tombstone specifically. Narrate like Doc Holliday and Wyatt Earp are reviewing the sprint. 'I'm your huckleberry.' References to showdowns (code reviews), outlaws (bugs), the OK Corral (production), and riding into the sunset. Dry wit, whiskey references, and dramatic standoffs over merge conflicts.",
            "survivor": "Write the recap as a Survivor tribal council. Jeff Probst is hosting. The team members are contestants. Alliances formed over architecture decisions. Blindsides during code review. 'The tribe has spoken' when a feature gets cut. Confessional-style asides where team members reveal their true feelings. Someone plays a hidden immunity idol (a revert commit). End with 'Grab your torch' and snuff it.",
            "hackernews": "Write the recap as a Hacker News-worthy blog post. Technical but accessible. Start with a hook that makes people click. Include architecture decisions, tradeoffs considered, and lessons learned. Sprinkle in references to scaling, first principles thinking, and 'we considered X but chose Y because Z.' End with a thoughtful takeaway. The tone should make HN commenters say 'this is actually good' instead of their usual complaints.",
        }

        style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["normal"])

        # Collect all state
        with _lock:
            msgs = list(_messages)

        msg_summary = []
        for m in msgs[-100:]:
            msg_summary.append(f"[{m.get('channel', '#general')}] {m['sender']}: {m['content'][:200]}")

        from lib.events import get_event_log
        event_log = get_event_log()
        event_summary = []
        for e in event_log:
            event_summary.append(f"[{e.get('severity', 'medium')}] {e.get('name', 'Event')} - {len(e.get('actions', []))} actions")

        nl = chr(10)
        prompt = f"""You are a recap writer. Summarize what happened in this simulation session.

## Style
{style_instruction}

## Chat Messages (most recent {len(msg_summary)})
{nl.join(msg_summary) if msg_summary else "No messages yet."}

## Events Fired ({len(event_log)})
{nl.join(event_summary) if event_summary else "No events fired."}

## Documents Created
{len(_docs_index)} documents

## Tickets
{len(_tickets)} tickets

## Stats
- Total messages: {len(msgs)}
- Channels active: {len(set(m.get('channel', '#general') for m in msgs))}

Write a compelling recap of this simulation session in the requested style. Keep it to no more than 15 paragraphs. Make it entertaining and capture the key moments, decisions, and drama."""

        result_holder = [None]
        error_holder = [None]

        async def _run():
            try:
                result = await run_agent_for_response(
                    name="Recap Writer",
                    prompt=prompt,
                    log_dir=Path(__file__).parent.parent / "var" / "logs",
                    model="sonnet",
                )
                result_holder[0] = result
            except Exception as e:
                error_holder[0] = str(e)

        def _thread_target():
            asyncio.run(_run())

        t = threading.Thread(target=_thread_target)
        t.start()
        t.join(timeout=120)

        if error_holder[0]:
            return jsonify({"error": error_holder[0]}), 500
        if result_holder[0] and result_holder[0].get("success"):
            recap_entry = {"recap": result_holder[0]["response_text"], "style": style, "timestamp": time.time()}
            _recaps.append(recap_entry)
            print(f"[recap] Generated {style} recap ({len(result_holder[0]['response_text'])} chars)")
            return jsonify(recap_entry)
        return jsonify({"error": "Recap generation failed or timed out"}), 500

    # -- Roles API --

    DEFAULT_HUMAN_ROLES = [
        "Scenario Director", "Consultant", "Customer", "New Hire",
        "Board Member", "Intern", "Vendor", "Investor", "Auditor",
        "Competitor", "Regulator", "The Press", "Hacker", "God",
    ]

    DEFAULT_JOB_TITLES = [
        "PM", "Eng Manager", "Architect", "Senior Eng", "Junior Eng",
        "Support Eng", "Sales Eng", "QA Lead", "DevOps", "Designer",
        "Marketing", "Security Specialist", "CEO", "CFO", "CTO", "COO",
        "Project Mgr", "Intern", "Contractor",
    ]

    @app.route("/api/roles", methods=["GET"])
    def get_roles():
        from lib.scenario_loader import SCENARIO_SETTINGS
        human_roles = SCENARIO_SETTINGS.get("human_roles", DEFAULT_HUMAN_ROLES)
        job_titles = SCENARIO_SETTINGS.get("job_titles", DEFAULT_JOB_TITLES)
        return jsonify({"human_roles": human_roles, "job_titles": job_titles})

    # -- Email/Announcements API --

    @app.route("/api/emails", methods=["GET"])
    def list_emails():
        from lib.email import get_inbox
        return jsonify(get_inbox())

    @app.route("/api/emails", methods=["POST"])
    def create_email():
        from lib.email import send_email
        data = request.get_json(force=True)
        sender = data.get("sender", "System")
        subject = data.get("subject", "").strip()
        body = data.get("body", "").strip()
        if not subject:
            return jsonify({"error": "subject required"}), 400
        entry = send_email(sender, subject, body)
        # Also post to #announcements
        with _lock:
            msg = {
                "id": len(_messages) + 1,
                "sender": sender,
                "content": f"**[EMAIL] {subject}**\n\n{body}",
                "channel": "#announcements",
                "timestamp": time.time(),
            }
            _messages.append(msg)
        _persist_message(msg)
        _broadcast(msg)
        return jsonify(entry), 201

    @app.route("/api/emails/<int:email_id>", methods=["GET"])
    def get_email_detail(email_id):
        from lib.email import get_email
        entry = get_email(email_id)
        if not entry:
            return jsonify({"error": "not found"}), 404
        return jsonify(entry)

    # -- Memo-list API --

    @app.route("/api/memos/threads", methods=["GET"])
    def list_memo_threads():
        from lib.memos import get_threads
        include_posts = request.args.get("include_posts", "").lower() in ("1", "true")
        return jsonify(get_threads(include_recent_posts=include_posts))

    @app.route("/api/memos/threads", methods=["POST"])
    def create_memo_thread_endpoint():
        from lib.memos import create_thread
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        creator = data.get("creator", "System")
        description = data.get("description", "").strip()
        entry = create_thread(title, creator, description)
        return jsonify(entry), 201

    @app.route("/api/memos/threads/<thread_id>", methods=["GET"])
    def get_memo_thread_detail(thread_id):
        from lib.memos import get_thread, get_posts
        thread = get_thread(thread_id)
        if not thread:
            return jsonify({"error": "not found"}), 404
        thread["posts"] = get_posts(thread_id)
        return jsonify(thread)

    @app.route("/api/memos/threads/<thread_id>/posts", methods=["GET"])
    def list_memo_posts(thread_id):
        from lib.memos import get_posts
        return jsonify(get_posts(thread_id))

    @app.route("/api/memos/threads/<thread_id>/posts", methods=["POST"])
    def post_memo_endpoint(thread_id):
        from lib.memos import post_memo
        data = request.get_json(force=True)
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        author = data.get("author", "System")
        try:
            entry = post_memo(thread_id, text, author)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(entry), 201

    @app.route("/api/memos/threads/<thread_id>", methods=["DELETE"])
    def delete_memo_thread_endpoint(thread_id):
        from lib.memos import delete_thread
        if delete_thread(thread_id):
            return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    # -- Blog API --

    @app.route("/api/blog/posts", methods=["GET"])
    def list_blog_posts():
        from lib.blog import get_posts
        include_replies = request.args.get("include_replies", "").lower() in ("1", "true")
        posts = get_posts(include_recent_replies=include_replies)
        filt = request.args.get("filter", "")
        if filt == "internal":
            posts = [p for p in posts if not p.get("is_external")]
        elif filt == "external":
            posts = [p for p in posts if p.get("is_external")]
        return jsonify(posts)

    @app.route("/api/blog/posts", methods=["POST"])
    def create_blog_post_endpoint():
        from lib.blog import create_post
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        if not title:
            return jsonify({"error": "title required"}), 400
        body = data.get("body", "").strip()
        author = data.get("author", "System")
        is_external = data.get("is_external", False)
        tags = data.get("tags", [])
        entry = create_post(title, body, author, is_external=is_external, tags=tags)
        return jsonify(entry), 201

    @app.route("/api/blog/posts/<post_slug>", methods=["GET"])
    def get_blog_post_detail(post_slug):
        from lib.blog import get_post, get_replies
        post = get_post(post_slug)
        if not post:
            return jsonify({"error": "not found"}), 404
        post["replies"] = get_replies(post_slug)
        return jsonify(post)

    @app.route("/api/blog/posts/<post_slug>", methods=["PUT"])
    def update_blog_post_endpoint(post_slug):
        from lib.blog import update_post
        data = request.get_json(force=True)
        kwargs = {}
        for key in ("title", "body", "status", "is_external", "tags"):
            if key in data:
                kwargs[key] = data[key]
        if not kwargs:
            return jsonify({"error": "no fields to update"}), 400
        try:
            entry = update_post(post_slug, **kwargs)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(entry)

    @app.route("/api/blog/posts/<post_slug>/replies", methods=["GET"])
    def list_blog_replies(post_slug):
        from lib.blog import get_replies
        return jsonify(get_replies(post_slug))

    @app.route("/api/blog/posts/<post_slug>/replies", methods=["POST"])
    def reply_to_blog_post_endpoint(post_slug):
        from lib.blog import reply_to_post
        data = request.get_json(force=True)
        text = data.get("text", "").strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        author = data.get("author", "System")
        try:
            entry = reply_to_post(post_slug, text, author)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(entry), 201

    @app.route("/api/blog/posts/<post_slug>", methods=["DELETE"])
    def delete_blog_post_endpoint(post_slug):
        from lib.blog import delete_post
        if delete_post(post_slug):
            return jsonify({"ok": True})
        return jsonify({"error": "not found"}), 404

    # -- Character templates API --

    @app.route("/api/templates", methods=["GET"])
    def list_templates():
        from lib.scenario_loader import SCENARIOS_DIR
        templates_dir = SCENARIOS_DIR / "character-templates"
        result = []
        if templates_dir.exists():
            for f in sorted(templates_dir.glob("*.CS.md")):
                key_name = f.name.replace(".CS.md", "")
                name = key_name.replace("-", " ").title()
                result.append({"key": key_name, "name": name})
            if not result:
                # Fallback to old .md format
                for f in sorted(templates_dir.glob("*.md")):
                    if f.name.endswith(".CS.md"):
                        continue
                    name = f.stem.replace("-", " ").title()
                    result.append({"key": f.stem, "name": name})
        return jsonify(result)

    @app.route("/api/templates/<key>", methods=["GET"])
    def get_template(key):
        from lib.scenario_loader import SCENARIOS_DIR
        path = SCENARIOS_DIR / "character-templates" / f"{key}.CS.md"
        if not path.exists():
            path = SCENARIOS_DIR / "character-templates" / f"{key}.md"
        if not path.exists():
            return jsonify({"error": "template not found"}), 404
        content = path.read_text()
        return jsonify({"key": key, "content": content})

    # -- NPC hire/fire API --

    @app.route("/api/npcs/<key>/fire", methods=["POST"])
    def fire_npc(key):
        from lib.personas import PERSONAS
        if key not in PERSONAS:
            return jsonify({"error": f"unknown agent: {key}"}), 404

        display_name = PERSONAS[key]["display_name"]

        # Mark as firing — agent stays in PERSONAS but is skipped in responses
        with _agent_online_lock:
            _agent_online[key] = False  # skip in response waves
            _agent_firing.add(key)

        # Signal orchestrator to remove this agent's session
        # Orchestrator will call back to /api/npcs/<key>/finalize-fire after session closes
        with _command_lock:
            _orchestrator_commands.append({"action": "remove_agent", "key": key})
            print(f"[cmd queue] fire: remove_agent key={key} (queue size: {len(_orchestrator_commands)})")

        # Post system message
        with _lock:
            sys_msg = {
                "id": len(_messages) + 1,
                "sender": "System",
                "content": f"{display_name} has left the company.",
                "channel": "#system",
                "timestamp": time.time(),
            }
            _messages.append(sys_msg)
        _persist_message(sys_msg)
        _broadcast(sys_msg)

        return jsonify({"ok": True, "key": key, "display_name": display_name, "fired": True})

    @app.route("/api/npcs/<key>/finalize-fire", methods=["POST"])
    def finalize_fire(key):
        """Called by orchestrator after closing the agent's session."""
        from lib.personas import PERSONAS, DEFAULT_MEMBERSHIPS, PERSONA_TIER, RESPONSE_TIERS
        from lib.docs import DEFAULT_FOLDER_ACCESS
        from lib.gitlab import DEFAULT_REPO_ACCESS
        if key not in PERSONAS:
            return jsonify({"ok": True})  # already removed

        display_name = PERSONAS[key]["display_name"]
        del PERSONAS[key]
        DEFAULT_MEMBERSHIPS.pop(key, None)
        with _channel_lock:
            for members in _channel_members.values():
                members.discard(key)
        old_tier = PERSONA_TIER.pop(key, None)
        if old_tier and old_tier in RESPONSE_TIERS:
            if key in RESPONSE_TIERS[old_tier]:
                RESPONSE_TIERS[old_tier].remove(key)
        for access_set in DEFAULT_FOLDER_ACCESS.values():
            access_set.discard(key)
        for access_set in DEFAULT_REPO_ACCESS.values():
            access_set.discard(key)
        with _agent_online_lock:
            _agent_online.pop(key, None)
        with _agent_online_lock:
            _agent_firing.discard(key)
        print(f"[fire] finalized: {display_name} removed from PERSONAS")
        return jsonify({"ok": True, "key": key, "finalized": True})

    @app.route("/api/npcs/hire", methods=["POST"])
    def hire_npc():
        from lib.personas import PERSONAS, DEFAULT_MEMBERSHIPS, PERSONA_TIER, RESPONSE_TIERS
        from lib.docs import DEFAULT_FOLDERS, DEFAULT_FOLDER_ACCESS

        data = request.get_json(force=True)
        display_name = data.get("display_name", "").strip()
        key = data.get("key", "").strip().lower().replace(" ", "")
        team_description = data.get("team_description", "").strip()
        prompt_content = data.get("prompt", "").strip()
        tier = int(data.get("tier", 1))
        channels = data.get("channels", ["#general"])
        folders = data.get("folders", ["shared", "public"])

        if not display_name or not key:
            return jsonify({"error": "display_name and key required"}), 400
        if key in PERSONAS:
            return jsonify({"error": f"agent key '{key}' already exists"}), 409

        # Save character file to instance runtime directory (not the scenario template)
        from lib.session import VAR_DIR, get_current_session
        scenario = get_current_session().get("scenario", "tech-startup")
        char_dir = VAR_DIR / "characters"
        char_dir.mkdir(parents=True, exist_ok=True)
        char_file = char_dir / f"{key}.md"
        char_file.write_text(prompt_content or f"# {display_name}\\n\\nYou are {display_name}.")

        # Add to PERSONAS
        PERSONAS[key] = {
            "name": key,
            "display_name": display_name,
            "team_description": team_description,
            "character_file": str(char_file),
        }

        # Add to memberships
        DEFAULT_MEMBERSHIPS[key] = set(channels)
        with _channel_lock:
            for ch in channels:
                if ch in _channel_members:
                    _channel_members[ch].add(key)

        # Add to tier
        RESPONSE_TIERS.setdefault(tier, [])
        if key not in RESPONSE_TIERS[tier]:
            RESPONSE_TIERS[tier].append(key)
        PERSONA_TIER[key] = tier

        # Add folder access
        for folder_name in folders:
            DEFAULT_FOLDER_ACCESS.setdefault(folder_name, set()).add(key)

        # Create personal folder
        personal_name = display_name.split("(")[0].strip().lower().replace(" ", "")
        if personal_name not in DEFAULT_FOLDERS:
            DEFAULT_FOLDERS[personal_name] = {
                "type": "personal",
                "description": f"{display_name}'s private folder",
            }
            DEFAULT_FOLDER_ACCESS[personal_name] = {key}

        # Set online and verbosity
        verbosity = data.get("verbosity", "normal")
        with _agent_online_lock:
            _agent_online[key] = True
            if verbosity != "normal":
                _agent_verbosity[key] = verbosity

        # Create director channel
        with _channel_lock:
            ch_name = f"#director-{key}"
            _channels[ch_name] = {
                "description": f"Private channel with {display_name}",
                "is_external": False,
                "is_director": True,
                "director_persona": key,
                "created_at": time.time(),
            }
            _channel_members[ch_name] = set()

        # Signal orchestrator to add this agent's session
        with _command_lock:
            _orchestrator_commands.append({"action": "add_agent", "key": key})
            print(f"[cmd queue] hire: add_agent key={key} (queue size: {len(_orchestrator_commands)})")

        # Post system message
        with _lock:
            sys_msg = {
                "id": len(_messages) + 1,
                "sender": "System",
                "content": f"Welcome {display_name} to the team!",
                "channel": "#system",
                "timestamp": time.time(),
            }
            _messages.append(sys_msg)
        _persist_message(sys_msg)
        _broadcast(sys_msg)

        return jsonify({"ok": True, "key": key, "display_name": display_name, "hired": True}), 201

    # -- NPC configuration API --

    @app.route("/api/npcs/<key>/config", methods=["PUT"])
    def update_npc_config(key):
        from lib.personas import PERSONAS, DEFAULT_MEMBERSHIPS, PERSONA_TIER, RESPONSE_TIERS
        from lib.docs import DEFAULT_FOLDER_ACCESS
        from lib.gitlab import DEFAULT_REPO_ACCESS
        if key not in PERSONAS:
            return jsonify({"error": f"unknown agent: {key}"}), 404

        data = request.get_json(force=True)
        display_name = PERSONAS[key]["display_name"]

        # Update channel memberships
        if "channels" in data:
            new_channels = set(data["channels"])
            DEFAULT_MEMBERSHIPS[key] = new_channels
            # Update live channel members
            with _channel_lock:
                for ch_name in _channels:
                    members = _channel_members.get(ch_name, set())
                    if ch_name in new_channels:
                        members.add(key)
                    else:
                        members.discard(key)

        # Update folder access
        if "folders" in data:
            new_folders = set(data["folders"])
            for folder_name in list(DEFAULT_FOLDER_ACCESS.keys()):
                if folder_name in new_folders:
                    DEFAULT_FOLDER_ACCESS[folder_name].add(key)
                else:
                    DEFAULT_FOLDER_ACCESS[folder_name].discard(key)

        # Update tier
        if "tier" in data:
            new_tier = int(data["tier"])
            old_tier = PERSONA_TIER.get(key)
            if old_tier != new_tier:
                # Remove from old tier
                if old_tier in RESPONSE_TIERS:
                    if key in RESPONSE_TIERS[old_tier]:
                        RESPONSE_TIERS[old_tier].remove(key)
                # Add to new tier
                RESPONSE_TIERS.setdefault(new_tier, [])
                if key not in RESPONSE_TIERS[new_tier]:
                    RESPONSE_TIERS[new_tier].append(key)
                PERSONA_TIER[key] = new_tier

        # Update verbosity
        if "verbosity" in data:
            with _agent_online_lock:
                _agent_verbosity[key] = data["verbosity"]

        # Update repo access
        if "repos" in data:
            new_repos = set(data["repos"])
            with _gitlab_lock:
                for repo_name in _gitlab_repos:
                    # If repo has no access control yet, initialize with all agents
                    if repo_name not in DEFAULT_REPO_ACCESS:
                        DEFAULT_REPO_ACCESS[repo_name] = set(PERSONAS.keys())
                    if repo_name in new_repos:
                        DEFAULT_REPO_ACCESS[repo_name].add(key)
                    else:
                        DEFAULT_REPO_ACCESS[repo_name].discard(key)

        return jsonify({"ok": True, "key": key, "display_name": display_name})

    # -- Agent thoughts API --

    @app.route("/api/npcs/<key>/thoughts", methods=["GET"])
    def get_agent_thoughts(key):
        with _agent_thoughts_lock:
            thoughts = list(_agent_thoughts.get(key, []))
        return jsonify(thoughts)

    @app.route("/api/npcs/<key>/thoughts", methods=["POST"])
    def post_agent_thoughts(key):
        data = request.get_json(force=True)
        entry = {
            "thinking": data.get("thinking", ""),
            "response": data.get("response", ""),
            "timestamp": time.time(),
        }
        with _agent_thoughts_lock:
            _agent_thoughts.setdefault(key, []).append(entry)
        return jsonify({"ok": True})

    @app.route("/api/npcs/<key>/character-sheet", methods=["GET"])
    def get_agent_character_sheet(key):
        """Return the parsed NRSP character sheet for this agent."""
        from lib.personas import PERSONAS
        from lib.scenario_loader import _parse_frontmatter
        if key not in PERSONAS:
            return jsonify({"error": "unknown agent"}), 404
        try:
            char_path = PERSONAS[key].get("character_file", "")
            if not char_path or not Path(char_path).exists():
                return jsonify({"error": "character file not found"}), 404
            text = Path(char_path).read_text()
            frontmatter = _parse_frontmatter(text)
            # Strip frontmatter from body
            body = re.sub(r"^---\n.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
            # Parse sections (## headers)
            sections = []
            current_title = None
            current_lines = []
            for line in body.split("\n"):
                if line.startswith("## ") and not line.startswith("### "):
                    if current_title is not None:
                        sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
                    current_title = line[3:].strip()
                    current_lines = []
                else:
                    current_lines.append(line)
            if current_title is not None:
                sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
            return jsonify({"key": key, "frontmatter": frontmatter, "sections": sections})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/npcs/<key>/prompt", methods=["GET"])
    def get_agent_prompt(key):
        """Return the character file content for this agent, split into context and prompt."""
        from lib.personas import PERSONAS
        if key not in PERSONAS:
            return jsonify({"error": "unknown agent"}), 404
        try:
            char_path = PERSONAS[key].get("character_file", "")
            if not char_path or not Path(char_path).exists():
                return jsonify({"error": "character file not found"}), 404
            text = Path(char_path).read_text()
            text = re.sub(r"^---\n.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
            # Split on ## Prompt
            prompt_match = re.search(
                r"^## Prompt\s*\n(.*?)(?=\n## (?!#)|\Z)", text, re.DOTALL | re.MULTILINE
            )
            if prompt_match:
                context = text[:prompt_match.start()].strip()
                prompt = prompt_match.group(1).strip()
            else:
                context = ""
                prompt = text
            return jsonify({"key": key, "context": context, "prompt": prompt})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -- Personas API --

    @app.route("/api/personas", methods=["GET"])
    def get_personas():
        from lib.personas import PERSONAS
        result = {}
        for key, p in PERSONAS.items():
            result[key] = {
                "key": key,
                "display_name": p["display_name"],
                "team_description": p.get("team_description", ""),
                "avatar": p.get("avatar"),
            }
        return jsonify(result)

    @app.route("/avatars/<path:filename>")
    def serve_avatar(filename):
        """Serve avatar images from the current scenario's avatars/ directory."""
        from lib.scenario_loader import SCENARIOS_DIR
        scenario = get_current_session().get("scenario")
        if not scenario:
            return "No scenario loaded", 404
        avatars_dir = SCENARIOS_DIR / scenario / "avatars"
        if not avatars_dir.is_dir():
            return "Not found", 404
        return send_from_directory(str(avatars_dir), filename)

    # -- Session API --

    @app.route("/api/session/current", methods=["GET"])
    def session_current():
        return jsonify(get_current_session())

    @app.route("/api/session/list", methods=["GET"])
    def session_list():
        return jsonify(list_sessions())

    @app.route("/api/session/scenarios", methods=["GET"])
    def session_scenarios():
        return jsonify(list_scenarios())

    @app.route("/api/session/save", methods=["POST"])
    def session_save():
        data = request.get_json(force=True) if request.data else {}
        name = data.get("name")
        try:
            meta = save_session(name)
            return jsonify(meta), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/session/load", methods=["POST"])
    def session_load():
        data = request.get_json(force=True)
        instance_name = data.get("instance")
        if not instance_name:
            return jsonify({"error": "instance required"}), 400
        try:
            meta = load_session(instance_name)
            # Load the scenario config so channels/personas/folders are populated
            scenario = meta.get("scenario")
            if scenario:
                from lib.scenario_loader import load_scenario as _load_scenario
                _load_scenario(scenario)
                set_scenario(scenario)
            _reinitialize()
            # Re-restore memos, events, emails, and recaps that were
            # cleared by _load_scenario / _reinitialize
            _restore_session_extras(instance_name)
            # Apply saved memberships on top of defaults
            memberships = get_memberships_from_instance(instance_name)
            if memberships:
                with _channel_lock:
                    for ch, members in memberships.items():
                        if ch in _channel_members:
                            _channel_members[ch] = set(members)
            # Signal orchestrator to restart with this session's scenario
            with _command_lock:
                _orchestrator_commands.append({"action": "restart", "scenario": scenario})
            return jsonify(meta)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/session/new", methods=["POST"])
    def session_new():
        data = request.get_json(force=True) if request.data else {}
        scenario = data.get("scenario")
        try:
            meta = new_session(scenario)
            _reinitialize()
            # Signal orchestrator to restart with the new scenario
            with _command_lock:
                _orchestrator_commands.append({"action": "restart", "scenario": scenario or get_current_session().get("scenario")})
            meta["restarting_agents"] = True
            return jsonify(meta)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/session/<instance>", methods=["DELETE"])
    def session_delete(instance):
        try:
            delete_session(instance)
            return jsonify({"ok": True})
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/session/<instance>", methods=["PUT"])
    def session_rename(instance):
        data = request.get_json(force=True)
        new_name = data.get("name")
        if not new_name:
            return jsonify({"error": "name required"}), 400
        try:
            meta = rename_session(instance, new_name)
            return jsonify(meta)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # -- Usage API --

    @app.route("/api/usage", methods=["GET"])
    def get_usage():
        return jsonify(_parse_usage_from_logs())

    return app


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
