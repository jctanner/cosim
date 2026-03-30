"""Flask chat server with SSE broadcast and web UI."""

import json
import re
import time
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

from lib.docs import slugify, DEFAULT_FOLDERS, DEFAULT_FOLDER_ACCESS
from lib.personas import DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, PERSONAS
from lib.gitlab import GITLAB_DIR, init_gitlab_storage, load_repos_index, save_repos_index, generate_commit_id
from lib.tickets import TICKETS_DIR, init_tickets_storage, load_tickets_index, save_tickets_index, generate_ticket_id
from lib.session import (
    save_session, load_session, new_session, list_sessions,
    get_current_session, set_scenario, get_memberships_from_instance,
)
from lib.scenario_loader import list_scenarios


CHAT_LOG = Path(__file__).parent.parent / "var" / "chat.log"
DOCS_DIR = Path(__file__).parent.parent / "var" / "docs"
LOGS_DIR = Path(__file__).parent.parent / "var" / "logs"

# Regexes to parse ResultMessage lines written by agent_runner.
# The usage dict contains nested sub-dicts, so we extract token counts
# directly from the line rather than trying to parse the full dict.
_RESULT_MSG_RE = re.compile(r"ResultMessage\(.*?total_cost_usd=(?P<cost>[0-9eE.+-]+|None)")
_INPUT_TOKENS_RE = re.compile(r"'input_tokens':\s*(\d+)")
_OUTPUT_TOKENS_RE = re.compile(r"'output_tokens':\s*(\d+)")
_CACHE_CREATE_RE = re.compile(r"'cache_creation_input_tokens':\s*(\d+)")
_CACHE_READ_RE = re.compile(r"'cache_read_input_tokens':\s*(\d+)")


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
                    m = _RESULT_MSG_RE.search(line)
                    if not m:
                        continue
                    agent_data["api_calls"] += 1

                    # Parse cost
                    cost_str = m.group("cost")
                    if cost_str and cost_str != "None":
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

# Agent online/offline state: persona_key -> True (online) / False (offline)
_agent_online: dict[str, bool] = {}
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

# Control signal for orchestrator (checked on each poll)
_orchestrator_command: dict = {"action": None}  # None, "restart", "shutdown"
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
<title>Organization Chat</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }

  /* -- Header with tabs -- */
  #header { background: #16213e; padding: 0 20px; border-bottom: 1px solid #0f3460;
            display: flex; align-items: stretch; gap: 0; }
  #header h1 { font-size: 18px; color: #e94560; display: flex; align-items: center; padding: 12px 16px 12px 0;
               border-right: 1px solid #0f3460; margin-right: 0; }
  .header-tab { padding: 12px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
                background: transparent; border: none; color: #888;
                border-bottom: 2px solid transparent; transition: all 0.15s ease; }
  .header-tab:hover { color: #e0e0e0; }
  .header-tab.active { color: #e94560; border-bottom-color: #e94560; }
  #session-controls { margin-left: auto; display: flex; align-items: center; gap: 6px; padding: 8px 0; }
  .session-btn { background: transparent; color: #888; border: 1px solid #333; padding: 6px 12px;
                 border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 600; }
  .session-btn:hover { border-color: #e94560; color: #e94560; }
  #session-load-select { background: #1a1a2e; color: #888; border: 1px solid #333; padding: 6px 8px;
                         border-radius: 6px; font-size: 12px; max-width: 200px; }
  #orch-status { display: flex; align-items: center; gap: 5px; margin-right: 8px;
                 padding: 4px 10px; border: 1px solid #333; border-radius: 6px; }
  #orch-label { font-size: 11px; color: #888; }
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
  #npcs-sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  #npcs-main { flex: 1; overflow-y: auto; padding: 20px; }
  #npcs-content { max-width: 1000px; }
  #npcs-empty { color: #666; text-align: center; padding: 40px; }
  .npc-tier-section { margin-bottom: 24px; }
  .npc-tier-header { font-size: 13px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: #888; margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid #333; }
  .npc-tier-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .npc-card { background: #1a1a2e; border: 1px solid #333; border-radius: 10px;
              padding: 14px 16px; flex: 1 1 160px; max-width: 220px; min-width: 160px;
              transition: border-color 0.15s; }
  .npc-card:hover { border-color: #555; }
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
  .npc-status-dot.offline { background: #666; }
  .npc-status-dot.disconnected { background: #444; }
  .npc-status-dot.unknown { background: #444; }
  .npc-card-state { font-size: 10px; color: #666; margin-left: auto; }
  .npc-card-name { font-size: 14px; font-weight: 700; color: #e0e0e0; }
  .npc-card-desc { font-size: 11px; color: #888; margin-bottom: 8px; line-height: 1.4; }
  .npc-card-section-label { font-size: 10px; font-weight: 600; text-transform: uppercase;
                           letter-spacing: 0.5px; color: #555; margin-bottom: 3px; margin-top: 6px; }
  .npc-card-tags { margin-bottom: 4px; line-height: 1.8; }
  .npc-tag { background: #111; color: #888; padding: 1px 6px; border-radius: 4px; font-size: 11px;
             margin-right: 3px; display: inline-block; }
  .npc-tag-folder { border-left: 2px solid #3498db; }
  .npc-toggle-btn { width: 100%; background: transparent; border: 1px solid #333;
                    color: #888; padding: 5px; border-radius: 6px; font-size: 11px;
                    cursor: pointer; transition: all 0.15s; }
  .npc-toggle-btn:hover { border-color: #e94560; color: #e94560; }
  .npc-toggle-btn.is-online:hover { border-color: #f39c12; color: #f39c12; }
  .npc-detail-tab { transition: all 0.15s; }
  .npc-detail-tab.active { background: #e94560; border-color: #e94560; color: #fff; }
  .thought-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid #1a1a2e;
                  font-size: 11px; color: #888; transition: background 0.1s; }
  .thought-item:hover { background: #1a1a3e; }
  .thought-item.active { background: #1a1a3e; color: #e0e0e0; border-left: 3px solid #e94560; }
  .thought-item-time { color: #555; font-size: 10px; }
  .thought-item-preview { color: #888; margin-top: 2px; overflow: hidden;
                          text-overflow: ellipsis; white-space: nowrap; }

  /* -- Usage tab -- */
  #usage-pane { padding: 0; flex-direction: row; }
  #usage-sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
                   display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .usage-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                           letter-spacing: 1px; color: #555; padding: 10px 14px 4px; }
  .usage-stat { padding: 4px 14px; font-size: 12px; color: #888; }
  .usage-stat strong { color: #e0e0e0; }
  #usage-main { flex: 1; overflow-y: auto; padding: 20px; }
  #usage-content { max-width: 1000px; }
  #usage-empty { color: #666; text-align: center; padding: 40px; }
  .usage-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .usage-card { background: #1a1a2e; border: 1px solid #333; border-radius: 10px;
                padding: 14px 16px; flex: 1 1 200px; max-width: 280px; min-width: 200px;
                transition: border-color 0.15s; }
  .usage-card:hover { border-color: #555; }
  .usage-card-name { font-size: 14px; font-weight: 700; color: #e0e0e0; margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid #333; }
  .usage-card-row { display: flex; justify-content: space-between; padding: 3px 0;
                    font-size: 12px; color: #888; }
  .usage-card-row .label { color: #666; }
  .usage-card-row .value { color: #e0e0e0; font-weight: 600; font-family: monospace; }
  .usage-card-row .value.cost { color: #2ecc71; }

  /* -- Modal overlay -- */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
                   z-index: 1000; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: #1a1a2e; border: 1px solid #333; border-radius: 12px;
           padding: 24px; min-width: 380px; max-width: 500px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
  .modal h2 { margin: 0 0 16px; font-size: 16px; color: #e94560; }
  .modal-field { margin-bottom: 14px; }
  .modal-field label { display: block; font-size: 12px; color: #888; margin-bottom: 4px; font-weight: 600;
                       text-transform: uppercase; letter-spacing: 0.5px; }
  .modal-field input, .modal-field select, .modal-field textarea {
    width: 100%; background: #111; color: #e0e0e0; border: 1px solid #333; padding: 8px 12px;
    border-radius: 8px; font-size: 14px; outline: none; box-sizing: border-box; }
  .modal-field input:focus, .modal-field select:focus, .modal-field textarea:focus { border-color: #e94560; }
  .modal-field textarea { resize: vertical; min-height: 60px; font-family: inherit; }
  .modal-field .field-hint { font-size: 11px; color: #555; margin-top: 4px; }
  .modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; }
  .modal-btn-primary { background: #e94560; color: #fff; border: none; padding: 8px 20px;
                       border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
  .modal-btn-primary:hover { background: #c0392b; }
  .modal-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .modal-btn-cancel { background: transparent; color: #888; border: 1px solid #333; padding: 8px 20px;
                      border-radius: 8px; cursor: pointer; font-size: 13px; }
  .modal-btn-cancel:hover { border-color: #e94560; color: #e94560; }
  .modal-status { font-size: 12px; color: #4fc3f7; margin-top: 10px; min-height: 16px; }

  /* -- Loading overlay -- */
  #loading-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8);
                     z-index: 2000; align-items: center; justify-content: center; flex-direction: column; gap: 12px; }
  #loading-overlay.open { display: flex; }
  #loading-overlay .spinner { width: 32px; height: 32px; border: 3px solid #333;
                              border-top-color: #e94560; border-radius: 50%;
                              animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #loading-text { color: #e0e0e0; font-size: 14px; }

  #main-layout { flex: 1; display: flex; overflow: hidden; }

  /* -- Sidebar -- */
  #sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
             display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: #555; padding: 10px 14px 4px; }
  .channel-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                 background: transparent; border: none; color: #999; padding: 5px 14px;
                 font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .channel-btn:hover { background: #1a1a3e; color: #e0e0e0; }
  .channel-btn.active { background: #1a1a3e; color: #fff; font-weight: 700; }
  .channel-btn .unread-badge { background: #e94560; color: #fff; font-size: 10px;
                               padding: 1px 6px; border-radius: 8px; margin-left: auto;
                               font-weight: 700; display: none; }
  .channel-btn .unread-badge.visible { display: inline; }
  .sidebar-divider { border: none; border-top: 1px solid #0f3460; margin: 6px 14px; }

  /* -- Tab panes -- */
  .tab-pane { display: none; flex: 1; overflow: hidden; }
  .tab-pane.active { display: flex; }
  #chat-pane { flex-direction: row; }
  #docs-pane { flex-direction: column; }
  #chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

  /* -- Chat tab -- */
  #channel-header { background: #16213e; padding: 8px 20px; border-bottom: 1px solid #0f3460;
                    font-size: 15px; font-weight: 700; color: #e0e0e0; }
  #channel-header .ch-desc { font-size: 12px; color: #888; font-weight: 400; margin-left: 10px; }
  #channel-members { font-size: 11px; color: #666; margin-top: 2px; }
  #messages-panel { flex: 1; overflow-y: auto; padding: 12px 20px; display: flex;
                    flex-direction: column; gap: 6px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; }
  .msg .sender { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
  .msg .content { font-size: 14px; word-break: break-word; }
  .msg .content h1 { font-size: 16px; margin: 8px 0 4px; color: #e0e0e0; }
  .msg .content h2 { font-size: 15px; margin: 6px 0 3px; color: #e0e0e0; }
  .msg .content h3 { font-size: 14px; margin: 5px 0 2px; color: #e0e0e0; }
  .msg .content p { margin: 4px 0; }
  .msg .content ul, .msg .content ol { margin: 4px 0 4px 20px; }
  .msg .content li { margin: 2px 0; }
  .msg .content strong { color: #fff; }
  .msg .content code { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; font-size: 13px; }
  .msg .content pre { background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; margin: 4px 0;
                      overflow-x: auto; }
  .msg .content pre code { background: none; padding: 0; }
  .msg .content hr { border: none; border-top: 1px solid #444; margin: 8px 0; }
  .msg .content input[type="checkbox"] { margin-right: 4px; }
  .msg .ts { font-size: 11px; color: #888; margin-top: 4px; }
  .msg-customer { align-self: flex-end; background: #0f3460; border-bottom-right-radius: 4px; }
  .msg-customer .sender { color: #4fc3f7; }
  .msg-board .sender { color: #ffd700; }
  .msg-hacker .sender { color: #00ff41; }
  .msg-god .sender { color: #ff6ff2; }
  .msg-intern .sender { color: #a8e6cf; }
  .msg-competitor .sender { color: #ff4444; }
  .msg-regulator .sender { color: #ff9800; }
  .msg-investor .sender { color: #7c4dff; }
  .msg-press .sender { color: #ffab40; }
  .msg-agent { align-self: flex-start; background: #1a1a3e; border: 1px solid #333; border-bottom-left-radius: 4px; }
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
  #persona-bar { background: #121a30; padding: 6px 20px; border-top: 1px solid #0f3460;
                 display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  /* -- Input area -- */
  #input-area { background: #16213e; padding: 10px 20px; border-top: 1px solid #0f3460;
                display: flex; gap: 8px; align-items: center; }
  #sender-name { background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #sender-name:focus { border-color: #e94560; }
  #sender-role, #sender-role-custom { background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                   padding: 8px 12px; border-radius: 8px; font-size: 14px; }
  #msg-input { flex: 1; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
               padding: 10px 14px; border-radius: 8px; font-size: 14px; outline: none; }
  #msg-input:focus { border-color: #e94560; }
  #send-btn { background: #e94560; color: white; border: none; padding: 10px 20px;
              border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; }
  #send-btn:hover { background: #c0392b; }
  #clear-btn { background: transparent; color: #888; border: 1px solid #333; padding: 10px 14px;
               border-radius: 8px; font-size: 14px; cursor: pointer; }
  #clear-btn:hover { border-color: #e94560; color: #e94560; }

  /* -- Docs tab -- */
  #docs-pane { padding: 0; flex-direction: row; }
  #docs-sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .docs-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                          letter-spacing: 1px; color: #555; padding: 10px 14px 4px; }
  .docs-sidebar-divider { border: none; border-top: 1px solid #0f3460; margin: 6px 14px; }
  .folder-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                background: transparent; border: none; color: #999; padding: 5px 14px;
                font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .folder-btn:hover { background: #1a1a3e; color: #e0e0e0; }
  .folder-btn.active { background: #1a1a3e; color: #fff; font-weight: 700; }
  #docs-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #docs-toolbar { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                  display: flex; align-items: center; }
  #docs-search { width: 100%; max-width: 400px; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #docs-search:focus { border-color: #e94560; }
  #new-doc-btn { background: #e94560; color: white; border: none; padding: 8px 16px;
                 border-radius: 8px; font-size: 13px; cursor: pointer; font-weight: 600;
                 margin-left: 8px; white-space: nowrap; }
  #new-doc-btn:hover { background: #c0392b; }
  #doc-editor { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #doc-editor-header { display: flex; align-items: center; justify-content: space-between;
                       padding: 10px 20px; border-bottom: 1px solid #0f3460; background: #16213e; }
  #doc-editor-header button { background: transparent; color: #e0e0e0; border: 1px solid #333;
                              padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-editor-save { background: #e94560 !important; border-color: #e94560 !important; font-weight: 600; }
  #doc-editor-save:hover { background: #c0392b !important; }
  #doc-editor-form { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 16px 20px; overflow-y: auto; }
  #doc-editor-title { background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                      padding: 10px 14px; border-radius: 8px; font-size: 16px; font-weight: 700; outline: none; }
  #doc-editor-title:focus { border-color: #e94560; }
  #doc-editor-folder { background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                       padding: 8px 12px; border-radius: 8px; font-size: 14px; width: 200px; }
  #doc-editor-content { flex: 1; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                        padding: 14px; border-radius: 8px; font-size: 14px; outline: none;
                        font-family: monospace; resize: none; min-height: 300px; }
  #doc-editor-content:focus { border-color: #e94560; }
  #docs-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  .doc-card { background: #1a1a2e; border: 1px solid #333; border-radius: 8px; padding: 12px 16px;
              margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .doc-card:hover { border-color: #e94560; }
  .doc-card-title { font-size: 14px; font-weight: 700; color: #4fc3f7; margin-bottom: 4px; }
  .doc-card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .doc-card-folder { font-size: 11px; background: #0f3460; color: #4fc3f7; padding: 2px 8px;
                     border-radius: 4px; font-weight: 600; }
  .doc-card-preview { font-size: 13px; color: #888; overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; }
  #docs-empty { color: #555; font-size: 14px; text-align: center; padding: 40px 20px; }
  #doc-viewer { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #doc-viewer.open { display: flex; }
  #doc-viewer-header { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                       display: flex; align-items: center; gap: 10px; }
  #doc-back-btn { background: transparent; border: 1px solid #333; color: #888; padding: 6px 12px;
                  border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-back-btn:hover { border-color: #e94560; color: #e94560; }
  #doc-viewer-title { font-size: 16px; font-weight: 700; color: #4fc3f7; }
  #doc-viewer-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                        color: #e0e0e0; line-height: 1.7; }
  #doc-viewer-content h1 { font-size: 20px; margin: 12px 0 8px; }
  #doc-viewer-content h2 { font-size: 17px; margin: 10px 0 6px; }
  #doc-viewer-content h3 { font-size: 15px; margin: 8px 0 4px; }
  #doc-viewer-content p { margin: 6px 0; }
  #doc-viewer-content ul, #doc-viewer-content ol { margin: 6px 0 6px 24px; }
  #doc-viewer-content li { margin: 3px 0; }
  #doc-viewer-content strong { color: #fff; }
  #doc-viewer-content code { background: rgba(255,255,255,0.1); padding: 2px 5px; border-radius: 3px; }
  #doc-viewer-content pre { background: rgba(0,0,0,0.3); padding: 12px; border-radius: 6px; margin: 6px 0;
                            overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  #doc-viewer-content pre code { background: none; padding: 0; }
  #doc-viewer-content hr { border: none; border-top: 1px solid #444; margin: 10px 0; }
  #doc-viewer-content input[type="checkbox"] { margin-right: 4px; }

  /* -- GitLab tab -- */
  #gitlab-pane { padding: 0; flex-direction: row; }
  #gitlab-sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
                    display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .gitlab-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                            letter-spacing: 1px; color: #555; padding: 10px 14px 4px; }
  .repo-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
              background: transparent; border: none; color: #999; padding: 5px 14px;
              font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .repo-btn:hover { background: #1a1a3e; color: #e0e0e0; }
  .repo-btn.active { background: #1a1a3e; color: #fff; font-weight: 700; }
  #gitlab-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #gitlab-header { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                   display: flex; align-items: center; gap: 12px; }
  #gitlab-repo-title { font-size: 16px; font-weight: 700; color: #4fc3f7; }
  #gitlab-repo-desc { font-size: 13px; color: #888; }
  .gitlab-toggle-btn { background: transparent; border: 1px solid #333; color: #888; padding: 6px 14px;
                       border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .gitlab-toggle-btn:hover { border-color: #e94560; color: #e94560; }
  .gitlab-toggle-btn.active { background: #0f3460; color: #4fc3f7; border-color: #4fc3f7; }
  #gitlab-toggle-bar { padding: 8px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                       display: flex; gap: 6px; }
  #gitlab-content { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #gitlab-empty { color: #555; font-size: 14px; text-align: center; padding: 40px 20px; }
  .gitlab-breadcrumbs { font-size: 13px; color: #888; margin-bottom: 12px; }
  .gitlab-breadcrumbs a { color: #4fc3f7; cursor: pointer; text-decoration: none; }
  .gitlab-breadcrumbs a:hover { text-decoration: underline; }
  .tree-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid #222;
               cursor: pointer; font-size: 14px; color: #e0e0e0; }
  .tree-item:hover { background: #1a1a3e; }
  .tree-item-icon { font-size: 14px; width: 20px; text-align: center; }
  .tree-item-name { flex: 1; }
  .gitlab-file-viewer { background: #111; border: 1px solid #333; border-radius: 6px; padding: 16px;
                        font-family: monospace; font-size: 13px; white-space: pre-wrap; word-break: break-word;
                        color: #e0e0e0; line-height: 1.6; }
  .commit-item { padding: 10px 12px; border-bottom: 1px solid #222; }
  .commit-item-id { font-family: monospace; font-size: 12px; color: #4fc3f7; margin-right: 8px; }
  .commit-item-msg { font-size: 14px; color: #e0e0e0; }
  .commit-item-meta { font-size: 12px; color: #666; margin-top: 4px; }

  /* -- Tickets tab -- */
  #tickets-pane { padding: 0; flex-direction: row; }
  #tickets-sidebar { width: 200px; min-width: 200px; background: #121a30; border-right: 1px solid #0f3460;
                     display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .tickets-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                             letter-spacing: 1px; color: #555; padding: 10px 14px 4px; }
  .tickets-filter-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                        background: transparent; border: none; color: #999; padding: 5px 14px;
                        font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .tickets-filter-btn:hover { background: #1a1a3e; color: #e0e0e0; }
  .tickets-filter-btn.active { background: #1a1a3e; color: #fff; font-weight: 700; }
  .tickets-filter-btn .tk-count { margin-left: auto; font-size: 11px; color: #666; }
  #tickets-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #tickets-header { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                    font-size: 15px; font-weight: 700; color: #e0e0e0; }
  #tickets-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #tickets-empty { color: #555; font-size: 14px; text-align: center; padding: 40px 20px; }
  .ticket-card { background: #1a1a2e; border: 1px solid #333; border-radius: 8px; padding: 12px 16px;
                 margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .ticket-card:hover { border-color: #e94560; }
  .ticket-card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .ticket-card-id { font-family: monospace; font-size: 11px; color: #888; }
  .ticket-card-title { font-size: 14px; font-weight: 700; color: #e0e0e0; flex: 1; }
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
  .tk-assignee { font-size: 11px; color: #4fc3f7; margin-left: auto; }
  #ticket-detail { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #ticket-detail.open { display: flex; }
  #ticket-detail-header { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e;
                          display: flex; align-items: center; gap: 10px; }
  #ticket-back-btn { background: transparent; border: 1px solid #333; color: #888; padding: 6px 12px;
                     border-radius: 6px; cursor: pointer; font-size: 13px; }
  #ticket-back-btn:hover { border-color: #e94560; color: #e94560; }
  #ticket-detail-title { font-size: 16px; font-weight: 700; color: #e0e0e0; }
  #ticket-detail-id { font-family: monospace; font-size: 12px; color: #888; margin-left: 8px; }
  #ticket-detail-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                           color: #e0e0e0; line-height: 1.7; }
  .tk-detail-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .tk-detail-field { font-size: 13px; color: #888; }
  .tk-detail-field strong { color: #e0e0e0; }
  .tk-detail-desc { background: #111; border: 1px solid #333; border-radius: 6px; padding: 12px;
                    margin-bottom: 16px; white-space: pre-wrap; word-break: break-word; }
  .tk-detail-deps { margin-bottom: 16px; font-size: 13px; }
  .tk-detail-deps span { color: #4fc3f7; font-family: monospace; cursor: pointer; }
  .tk-comments-header { font-size: 14px; font-weight: 700; color: #e0e0e0; margin-bottom: 8px;
                        border-bottom: 1px solid #333; padding-bottom: 4px; }
  .tk-comment { background: #111; border-left: 3px solid #0f3460; padding: 8px 12px; margin-bottom: 8px;
                border-radius: 0 6px 6px 0; }
  .tk-comment-author { font-size: 12px; font-weight: 700; color: #4fc3f7; }
  .tk-comment-time { font-size: 11px; color: #666; margin-left: 8px; }
  .tk-comment-text { font-size: 13px; color: #e0e0e0; margin-top: 4px; }
  #tk-create-btn { background: #e94560; color: #fff; border: none; padding: 6px 14px; border-radius: 6px;
                   cursor: pointer; font-size: 12px; font-weight: 600; margin-left: auto; }
  #tk-create-btn:hover { background: #c0392b; }
  #tk-create-form { display: none; background: #1a1a2e; border: 1px solid #333; border-radius: 8px;
                    padding: 16px; margin-bottom: 12px; }
  #tk-create-form.open { display: block; }
  .tk-form-row { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; }
  .tk-form-row label { font-size: 12px; color: #888; min-width: 70px; }
  .tk-form-input { flex: 1; background: #111; color: #e0e0e0; border: 1px solid #333; padding: 6px 10px;
                   border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-input:focus { border-color: #e94560; }
  .tk-form-select { background: #111; color: #e0e0e0; border: 1px solid #333; padding: 6px 10px;
                    border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-textarea { flex: 1; background: #111; color: #e0e0e0; border: 1px solid #333; padding: 6px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 60px;
                      font-family: inherit; }
  .tk-form-textarea:focus { border-color: #e94560; }
  .tk-form-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .tk-form-submit { background: #e94560; color: #fff; border: none; padding: 6px 16px; border-radius: 6px;
                    cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-form-submit:hover { background: #c0392b; }
  .tk-form-cancel { background: transparent; color: #888; border: 1px solid #333; padding: 6px 16px;
                    border-radius: 6px; cursor: pointer; font-size: 12px; }
  .tk-form-cancel:hover { border-color: #e94560; color: #e94560; }
  .tk-detail-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;
                       padding-bottom: 12px; border-bottom: 1px solid #333; }
  .tk-action-btn { background: transparent; border: 1px solid #333; color: #e0e0e0; padding: 5px 12px;
                   border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-action-btn:hover { border-color: #e94560; color: #e94560; }
  .tk-action-btn.primary { background: #0d47a1; border-color: #0d47a1; color: #90caf9; }
  .tk-action-btn.primary:hover { background: #1565c0; }
  .tk-action-btn.danger { border-color: #b71c1c; color: #ef9a9a; }
  .tk-action-btn.danger:hover { background: #b71c1c; color: #fff; }
  .tk-action-btn.success { border-color: #1b5e20; color: #a5d6a7; }
  .tk-action-btn.success:hover { background: #1b5e20; color: #fff; }
  .tk-assign-row { display: flex; gap: 8px; align-items: center; }
  .tk-assign-select { background: #111; color: #e0e0e0; border: 1px solid #333; padding: 4px 8px;
                      border-radius: 6px; font-size: 12px; }
  .tk-comment-input-area { display: flex; gap: 8px; margin-top: 12px; align-items: flex-start; }
  .tk-comment-input { flex: 1; background: #111; color: #e0e0e0; border: 1px solid #333; padding: 8px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 36px;
                      font-family: inherit; }
  .tk-comment-input:focus { border-color: #e94560; }
  .tk-comment-submit { background: #e94560; color: #fff; border: none; padding: 8px 14px; border-radius: 6px;
                       cursor: pointer; font-size: 12px; font-weight: 600; align-self: flex-end; }
  .tk-comment-submit:hover { background: #c0392b; }
</style>
</head>
<body>
<div id="header">
  <h1>Organization Chat</h1>
  <button class="header-tab active" data-tab="chat">Chat</button>
  <button class="header-tab" data-tab="docs">Docs</button>
  <button class="header-tab" data-tab="gitlab">GitLab</button>
  <button class="header-tab" data-tab="tickets">Tickets</button>
  <button class="header-tab" data-tab="npcs">NPCs</button>
  <button class="header-tab" data-tab="usage">Usage</button>
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
        <select id="sender-role">
          <option value="">No role</option>
          <option value="Consultant">Consultant</option>
          <option value="Customer">Customer</option>
          <option value="New Hire">New Hire</option>
          <option value="Board Member">Board Member</option>
          <option value="Intern">Intern</option>
          <option value="Vendor">Vendor</option>
          <option value="Investor">Investor</option>
          <option value="Auditor">Auditor</option>
          <option value="Competitor">Competitor</option>
          <option value="Regulator">Regulator</option>
          <option value="The Press">The Press</option>
          <option value="Hacker">Hacker</option>
          <option value="God">God</option>
          <option value="custom">Custom...</option>
        </select>
        <input id="sender-role-custom" type="text" placeholder="Custom role..." style="width:100px;display:none" />
      </div>
      <div id="input-area">
        <input id="msg-input" type="text" placeholder="Type a message..." autocomplete="off" />
        <button id="send-btn">Send</button>
        <button id="clear-btn" title="Clear chat">Clear</button>
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
            <span style="font-size:11px;color:#555">Author:</span>
            <input id="doc-author-name" type="text" placeholder="Your name..." style="width:120px;background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-author-role" style="background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px">
              <option value="">No role</option>
              <option value="Consultant">Consultant</option>
              <option value="Customer">Customer</option>
              <option value="New Hire">New Hire</option>
              <option value="Board Member">Board Member</option>
              <option value="Intern">Intern</option>
              <option value="Vendor">Vendor</option>
              <option value="Investor">Investor</option>
              <option value="Auditor">Auditor</option>
              <option value="Competitor">Competitor</option>
              <option value="Regulator">Regulator</option>
              <option value="The Press">The Press</option>
              <option value="Hacker">Hacker</option>
              <option value="God">God</option>
            </select>
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
            <button id="doc-edit-btn" class="session-btn" style="font-size:11px">Edit</button>
          </div>
        </div>
        <div id="doc-viewer-body" style="display:flex;flex:1;min-height:0;overflow:hidden">
          <div id="doc-viewer-content" style="flex:1;overflow-y:auto"></div>
          <div id="doc-history-panel" style="display:none;width:220px;min-width:220px;border-left:1px solid #333;background:#121a30;overflow-y:auto">
            <div style="padding:8px 12px;font-size:11px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.5px">Version History</div>
            <div id="doc-history-list"></div>
          </div>
        </div>
        <div id="doc-edit-area" style="display:none;flex:1;min-height:0;flex-direction:column;padding:12px 20px;gap:8px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:11px;color:#555">Editing as:</span>
            <input id="doc-edit-author-name" type="text" placeholder="Your name..." style="width:120px;background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-edit-author-role" style="background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px">
              <option value="">No role</option>
              <option value="Consultant">Consultant</option>
              <option value="Customer">Customer</option>
              <option value="New Hire">New Hire</option>
              <option value="Board Member">Board Member</option>
              <option value="Intern">Intern</option>
              <option value="Vendor">Vendor</option>
              <option value="Investor">Investor</option>
              <option value="Auditor">Auditor</option>
              <option value="Competitor">Competitor</option>
              <option value="Regulator">Regulator</option>
              <option value="The Press">The Press</option>
              <option value="Hacker">Hacker</option>
              <option value="God">God</option>
            </select>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button id="doc-edit-cancel" class="session-btn" style="font-size:11px">Cancel</button>
              <button id="doc-edit-save" class="session-btn" style="font-size:11px;background:#e94560;border-color:#e94560;color:#fff">Save</button>
            </div>
          </div>
          <textarea id="doc-edit-textarea" style="flex:1;background:#111;color:#e0e0e0;border:1px solid #333;padding:14px;border-radius:8px;font-size:14px;font-family:monospace;resize:none;outline:none"></textarea>
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
               style="width:100%;background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <input id="gl-new-repo-desc" type="text" placeholder="Description (optional)" autocomplete="off"
               style="width:100%;background:#111;color:#e0e0e0;border:1px solid #333;padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <div style="display:flex;gap:4px">
          <button id="gl-new-repo-cancel" class="session-btn" style="flex:1;font-size:11px">Cancel</button>
          <button id="gl-new-repo-save" class="session-btn" style="flex:1;font-size:11px;background:#e94560;border-color:#e94560;color:#fff">Create</button>
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
              <option value="Sarah (PM)">Sarah (PM)</option>
              <option value="Marcus (Eng Manager)">Marcus (Eng Manager)</option>
              <option value="Priya (Architect)">Priya (Architect)</option>
              <option value="Alex (Senior Eng)">Alex (Senior Eng)</option>
              <option value="Jordan (Support Eng)">Jordan (Support Eng)</option>
              <option value="Taylor (Sales Eng)">Taylor (Sales Eng)</option>
              <option value="Dana (CEO)">Dana (CEO)</option>
              <option value="Morgan (CFO)">Morgan (CFO)</option>
              <option value="Riley (Marketing)">Riley (Marketing)</option>
              <option value="Casey (DevOps)">Casey (DevOps)</option>
              <option value="Nadia (Project Mgr)">Nadia (Project Mgr)</option>
            </select>
          </div>
          <div class="tk-form-row">
            <label>Created by</label>
            <select class="tk-form-select" id="tk-form-author">
              <option value="Consultant" selected>Consultant</option>
              <option value="Customer">Customer</option>
              <option value="Sarah (PM)">Sarah (PM)</option>
              <option value="Marcus (Eng Manager)">Marcus (Eng Manager)</option>
              <option value="Priya (Architect)">Priya (Architect)</option>
              <option value="Alex (Senior Eng)">Alex (Senior Eng)</option>
              <option value="Jordan (Support Eng)">Jordan (Support Eng)</option>
              <option value="Taylor (Sales Eng)">Taylor (Sales Eng)</option>
              <option value="Dana (CEO)">Dana (CEO)</option>
              <option value="Morgan (CFO)">Morgan (CFO)</option>
              <option value="Riley (Marketing)">Riley (Marketing)</option>
              <option value="Casey (DevOps)">Casey (DevOps)</option>
              <option value="Nadia (Project Mgr)">Nadia (Project Mgr)</option>
              <option value="Board Member">Board Member</option>
              <option value="Investor">Investor</option>
              <option value="God">God</option>
            </select>
          </div>
          <div class="tk-form-row">
            <label>Description</label>
            <textarea class="tk-form-textarea" id="tk-form-desc" placeholder="Describe the work to be done..."></textarea>
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
          <span style="margin-left:auto;font-size:12px;color:#888;">Acting as</span>
          <select class="tk-form-select" id="tk-acting-as" style="font-size:12px;">
            <option value="Consultant" selected>Consultant</option>
            <option value="Customer">Customer</option>
            <option value="Sarah (PM)">Sarah (PM)</option>
            <option value="Marcus (Eng Manager)">Marcus (Eng Manager)</option>
            <option value="Priya (Architect)">Priya (Architect)</option>
            <option value="Alex (Senior Eng)">Alex (Senior Eng)</option>
            <option value="Jordan (Support Eng)">Jordan (Support Eng)</option>
            <option value="Taylor (Sales Eng)">Taylor (Sales Eng)</option>
            <option value="Dana (CEO)">Dana (CEO)</option>
            <option value="Morgan (CFO)">Morgan (CFO)</option>
            <option value="Riley (Marketing)">Riley (Marketing)</option>
            <option value="Casey (DevOps)">Casey (DevOps)</option>
            <option value="Nadia (Project Mgr)">Nadia (Project Mgr)</option>
            <option value="Board Member">Board Member</option>
            <option value="Investor">Investor</option>
            <option value="God">God</option>
          </select>
        </div>
        <div id="ticket-detail-content"></div>
      </div>
    </div>
  </div>
  <!-- NPCs tab -->
  <div id="npcs-pane" class="tab-pane">
    <div id="npcs-sidebar">
      <div class="sidebar-section">Scenario</div>
      <div id="npcs-scenario-info" style="padding:8px 14px;font-size:12px;color:#888;">No scenario loaded</div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Summary</div>
      <div id="npcs-summary" style="padding:8px 14px;font-size:12px;color:#888;"></div>
    </div>
    <div id="npcs-main">
      <div id="npcs-content">
        <div id="npcs-empty">No scenario loaded. Click New to start a session.</div>
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
  <div class="modal">
    <h2>Save Session</h2>
    <div class="modal-field">
      <label>Session Name (optional)</label>
      <input id="save-session-name" type="text" placeholder="e.g. before-demo" autocomplete="off" />
      <div class="field-hint">Leave blank to auto-generate from scenario + date</div>
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
  <div class="modal">
    <h2>Load Session</h2>
    <div class="modal-field">
      <label>Saved Sessions</label>
      <select id="load-session-select" size="6" style="height:auto"></select>
    </div>
    <div id="load-session-detail" style="font-size:12px;color:#888;min-height:30px;margin-bottom:8px"></div>
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
      <button class="session-btn npc-detail-tab" data-npc-tab="prompt">Prompt</button>
    </div>
    <div id="npc-detail-thoughts" style="flex:1;min-height:0;display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid #333">
      <div id="npc-thoughts-list" style="width:200px;min-width:200px;background:#121a30;overflow-y:auto;border-right:1px solid #333">
      </div>
      <div id="npc-thoughts-content" style="flex:1;overflow-y:auto;background:#111;padding:16px;font-size:13px;color:#ccc;white-space:pre-wrap;font-family:monospace;line-height:1.5">
        No thoughts recorded yet.
      </div>
    </div>
    <div id="npc-detail-prompt" style="flex:1;min-height:0;overflow-y:auto;background:#111;border-radius:8px;padding:16px;font-size:13px;color:#ccc;white-space:pre-wrap;font-family:monospace;line-height:1.5;display:none">
    </div>
  </div>
</div>

<!-- Loading Overlay -->
<div id="loading-overlay">
  <div class="spinner"></div>
  <div id="loading-text">Loading...</div>
</div>

<script>
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

// Color palette for agent personas (assigned round-robin on load)
const AGENT_COLORS = [
  '#e94560', '#f39c12', '#9b59b6', '#2ecc71', '#1abc9c',
  '#e67e22', '#f1c40f', '#3498db', '#e056a0', '#00bcd4', '#ff6b6b',
];

async function loadPersonas() {
  const resp = await fetch('/api/personas');
  const personas = await resp.json();
  SENDER_CLASS_MAP = {};
  PERSONA_DISPLAY = {};
  const keys = Object.keys(personas);
  keys.forEach((key, i) => {
    const p = personas[key];
    const cls = 'msg-agent-' + i;
    SENDER_CLASS_MAP[p.display_name] = cls;
    PERSONA_DISPLAY[key] = p.display_name;
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
    if (target === 'usage') loadUsage();
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
  div.innerHTML = '<div class="sender"' + senderStyle + '>' + escapeHtml(msg.sender) + '</div>'
    + '<div class="content">' + renderMarkdown(msg.content) + '</div>'
    + '<div class="ts">' + ts + '</div>';
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
    el.style.cssText = 'padding:4px 20px;font-size:12px;color:#888;font-style:italic;min-height:18px;';
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
document.getElementById('clear-btn').addEventListener('click', clearChat);
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
      + (author ? '<span style="font-size:11px;color:#888">' + escapeHtml(author) + '</span>' : '')
      + '</div>'
      + '<div class="doc-card-title">' + escapeHtml(doc.title || doc.slug) + '</div>'
      + (dateLine ? '<div style="font-size:10px;color:#555;margin-bottom:4px">' + dateLine + '</div>' : '')
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
    (meta ? '<div style="font-size:11px;color:#888;font-weight:400;margin-top:2px">' + escapeHtml(meta) + '</div>' : '');
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
  list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:#888">Loading...</div>';
  panel.style.display = '';
  const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug) + '/history');
  const history = await resp.json();
  list.innerHTML = '';
  if (!history.length) {
    list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:#888">No version history</div>';
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

const TK_ASSIGNEE_OPTIONS = [
  '', 'Sarah (PM)', 'Marcus (Eng Manager)', 'Priya (Architect)', 'Alex (Senior Eng)',
  'Jordan (Support Eng)', 'Taylor (Sales Eng)', 'Dana (CEO)', 'Morgan (CFO)',
  'Riley (Marketing)', 'Casey (DevOps)', 'Nadia (Project Mgr)',
];

function toggleCreateForm() {
  const form = document.getElementById('tk-create-form');
  form.classList.toggle('open');
  if (form.classList.contains('open')) {
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
  await fetch('/api/tickets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ title, description, priority, assignee, author }),
  });
  document.getElementById('tk-form-title').value = '';
  document.getElementById('tk-form-desc').value = '';
  document.getElementById('tk-form-priority').value = 'medium';
  document.getElementById('tk-form-assignee').value = '';
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
  loadChannels().then(() => {
    updateChannelHeader();
    updateSenderDropdown();
    loadMessages();
    connectSSE();
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
  if (oooCount > 0) summaryHtml += '<div style="color:#888">Out of office: ' + oooCount + '</div>';
  if (disconnectedCount > 0) summaryHtml += '<div style="color:#555">Disconnected: ' + disconnectedCount + '</div>';
  if (!summaryHtml) summaryHtml = '<div style="color:#555">No agents active</div>';
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
  'posting': 'Posting...', offline: 'Out of Office', disconnected: 'Disconnected',
  unknown: 'Unknown',
};

function createNPCCard(npc) {
  const card = document.createElement('div');
  const ls = npc.live_state || 'unknown';
  const lsCss = ls.replace(/ /g, '-');
  card.className = 'npc-card' + (npc.online ? '' : ' offline');
  card.innerHTML =
    '<div class="npc-card-header">' +
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
    '</div>';
  const btn = document.createElement('button');
  btn.className = 'npc-toggle-btn' + (npc.online ? ' is-online' : '');
  btn.textContent = npc.online ? 'Set Out of Office' : 'Bring Online';
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    await fetch('/api/npcs/' + encodeURIComponent(npc.key) + '/toggle', {method: 'POST'});
    loadNPCs();
  });
  card.appendChild(btn);
  card.style.cursor = 'pointer';
  card.addEventListener('click', (e) => {
    if (e.target === btn) return;  // don't open detail when clicking toggle
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
  document.getElementById('npc-detail-prompt').style.display = tab === 'prompt' ? '' : 'none';
}

async function loadNPCThoughts() {
  const list = document.getElementById('npc-thoughts-list');
  const content = document.getElementById('npc-thoughts-content');
  list.innerHTML = '';
  content.textContent = 'Loading...';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/thoughts');
  _npcThoughtsData = await resp.json();
  if (!_npcThoughtsData.length) {
    content.textContent = 'No thoughts recorded yet. This agent has not responded to any messages.';
    return;
  }
  // Render list items (newest first)
  const reversed = [..._npcThoughtsData].reverse();
  reversed.forEach((t, i) => {
    const idx = _npcThoughtsData.length - 1 - i;
    const item = document.createElement('div');
    item.className = 'thought-item' + (i === 0 ? ' active' : '');
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
  // Select the newest
  selectThought(_npcThoughtsData.length - 1);
}

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
  body.textContent = 'Loading...';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/prompt');
  const data = await resp.json();
  body.textContent = data.content || data.error || 'No prompt found.';
}

document.querySelectorAll('.npc-detail-tab').forEach(tab => {
  tab.addEventListener('click', async () => {
    _npcDetailTab = tab.dataset.npcTab;
    document.querySelectorAll('.npc-detail-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.npcTab === _npcDetailTab);
    });
    switchNPCDetailTab(_npcDetailTab);
    if (_npcDetailTab === 'thoughts') await loadNPCThoughts();
    else if (_npcDetailTab === 'prompt') await loadNPCPrompt();
  });
});

document.getElementById('npc-detail-close').addEventListener('click', () => {
  closeModal('npc-detail-modal');
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

async function loadUsage() {
  try {
    const resp = await fetch('/api/usage');
    const data = await resp.json();
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
    // silently ignore fetch errors
  }
}

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
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:999;background:#e94560;color:#fff;padding:10px 20px;font-size:13px;display:flex;align-items:center;justify-content:space-between;';
    const dismiss = document.createElement('button');
    dismiss.textContent = 'Dismiss';
    dismiss.style.cssText = 'background:rgba(0,0,0,0.3);color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-left:16px;';
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
  await loadPersonas();
  await loadChannels();
  await loadMessages();
  renderSidebar();
  renderMessages();
  loadFolders();
  loadDocs();
  loadRepos();
  loadTickets();
  loadNPCs();
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

document.getElementById('session-save-btn').addEventListener('click', () => {
  document.getElementById('save-session-name').value = '';
  document.getElementById('save-session-status').textContent = '';
  openModal('save-session-modal');
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
      status.style.color = '#e94560';
    }
  } finally {
    document.getElementById('save-session-confirm').disabled = false;
  }
});

// -- Load Session Modal --

let _sessionsCache = [];

async function refreshSessionsList() {
  const sel = document.getElementById('load-session-select');
  sel.innerHTML = '';
  const resp = await fetch('/api/session/list');
  _sessionsCache = await resp.json();
  if (_sessionsCache.length === 0) {
    const opt = document.createElement('option');
    opt.disabled = true;
    opt.textContent = 'No saved sessions';
    sel.appendChild(opt);
    document.getElementById('load-session-confirm').disabled = true;
    return;
  }
  _sessionsCache.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.instance_dir;
    const date = new Date((s.saved_at || s.created_at) * 1000).toLocaleString();
    opt.textContent = (s.name || s.instance_dir);
    opt.dataset.date = date;
    opt.dataset.scenario = s.scenario || '';
    sel.appendChild(opt);
  });
}

// Modal's session list selection
document.getElementById('load-session-select').addEventListener('change', (e) => {
  const val = e.target.value;
  const s = _sessionsCache.find(x => x.instance_dir === val);
  const detail = document.getElementById('load-session-detail');
  if (s) {
    const date = new Date((s.saved_at || s.created_at) * 1000).toLocaleString();
    detail.textContent = 'Scenario: ' + (s.scenario || '?') + '  |  Saved: ' + date;
    document.getElementById('load-session-confirm').disabled = false;
  } else {
    detail.textContent = '';
    document.getElementById('load-session-confirm').disabled = true;
  }
});

document.getElementById('load-session-select').addEventListener('dblclick', () => {
  document.getElementById('load-session-confirm').click();
});

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
    document.getElementById('load-session-detail').textContent = '';
    document.getElementById('load-session-confirm').disabled = true;
    await refreshSessionsList();
    openModal('load-session-modal');
  });
}

document.getElementById('load-session-cancel').addEventListener('click', () => closeModal('load-session-modal'));

document.getElementById('load-session-confirm').addEventListener('click', async () => {
  const sel = document.getElementById('load-session-select');
  const instance = sel.value;
  if (!instance) return;
  const status = document.getElementById('load-session-status');
  status.textContent = 'Loading session...';
  document.getElementById('load-session-confirm').disabled = true;
  closeModal('load-session-modal');
  showLoading('Loading session...');
  try {
    const resp = await fetch('/api/session/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instance}),
    });
    if (resp.ok) {
      await reloadAllState();
    } else {
      const err = await resp.json();
      hideLoading();
      openModal('load-session-modal');
      status.textContent = 'Error: ' + (err.error || 'unknown');
      status.style.color = '#e94560';
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
        # Return any pending command
        with _command_lock:
            cmd = dict(_orchestrator_command)
            if cmd["action"]:
                _orchestrator_command["action"] = None  # consume it
            return jsonify(cmd)

    @app.route("/api/orchestrator/command", methods=["POST"])
    def orchestrator_command():
        data = request.get_json(force=True)
        action = data.get("action")
        if action not in ("restart", "shutdown", None):
            return jsonify({"error": "invalid action"}), 400
        with _command_lock:
            _orchestrator_command["action"] = action
            _orchestrator_command.update({k: v for k, v in data.items() if k != "action"})
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
                toggled_online = _agent_online.get(key, True)
                # Determine live state from orchestrator heartbeat
                agent_info = agent_states.get(key, {})
                if not orch_connected:
                    live_state = "disconnected"
                elif not toggled_online:
                    live_state = "offline"
                else:
                    live_state = agent_info.get("state", "unknown")
                result.append({
                    "key": key,
                    "display_name": p["display_name"],
                    "team_description": p.get("team_description", ""),
                    "tier": PERSONA_TIER.get(key, 0),
                    "channels": channels,
                    "folders": folders,
                    "online": toggled_online,
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

    @app.route("/api/npcs/<key>/prompt", methods=["GET"])
    def get_agent_prompt(key):
        """Return the character file content for this agent."""
        from lib.personas import PERSONAS, load_persona_instructions
        if key not in PERSONAS:
            return jsonify({"error": "unknown agent"}), 404
        try:
            content = load_persona_instructions(key)
            return jsonify({"key": key, "content": content})
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
            }
        return jsonify(result)

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
            # Apply saved memberships on top of defaults
            memberships = get_memberships_from_instance(instance_name)
            if memberships:
                with _channel_lock:
                    for ch, members in memberships.items():
                        if ch in _channel_members:
                            _channel_members[ch] = set(members)
            # Signal orchestrator to restart with this session's scenario
            with _command_lock:
                _orchestrator_command["action"] = "restart"
                _orchestrator_command["scenario"] = scenario
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
                _orchestrator_command["action"] = "restart"
                _orchestrator_command["scenario"] = scenario or get_current_session().get("scenario")
            meta["restarting_agents"] = True
            return jsonify(meta)
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
