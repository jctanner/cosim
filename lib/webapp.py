"""Flask chat server with SSE broadcast and web UI."""

import json
import time
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

from lib.docs import slugify, DEFAULT_FOLDERS, DEFAULT_FOLDER_ACCESS
from lib.personas import DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, PERSONAS
from lib.gitlab import GITLAB_DIR, init_gitlab_storage, load_repos_index, save_repos_index, generate_commit_id


CHAT_LOG = Path(__file__).parent.parent / "chat.log"
DOCS_DIR = Path(__file__).parent.parent / "docs"

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
  .msg-default .sender { color: #95a5a6; }

  /* -- Input area -- */
  #input-area { background: #16213e; padding: 10px 20px; border-top: 1px solid #0f3460;
                display: flex; gap: 8px; align-items: center; }
  #sender-select { background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
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
  #docs-toolbar { padding: 12px 20px; border-bottom: 1px solid #0f3460; background: #16213e; }
  #docs-search { width: 100%; max-width: 400px; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #docs-search:focus { border-color: #e94560; }
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
</style>
</head>
<body>
<div id="header">
  <h1>Organization Chat</h1>
  <button class="header-tab active" data-tab="chat">Chat</button>
  <button class="header-tab" data-tab="docs">Docs</button>
  <button class="header-tab" data-tab="gitlab">GitLab</button>
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
    </div>
    <div id="chat-area">
      <div id="channel-header">
        <span id="channel-title">#general</span>
        <span class="ch-desc" id="channel-desc"></span>
        <div id="channel-members"></div>
      </div>
      <div id="messages-panel"></div>
      <div id="input-area">
        <select id="sender-select">
          <option value="Consultant" selected>Consultant</option>
          <option value="Customer">Customer</option>
        </select>
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
      </div>
      <div id="docs-list">
        <div id="docs-empty">No documents yet.</div>
      </div>
      <div id="doc-viewer">
        <div id="doc-viewer-header">
          <button id="doc-back-btn">Back</button>
          <span id="doc-viewer-title"></span>
        </div>
        <div id="doc-viewer-content"></div>
      </div>
    </div>
  </div>
  <!-- GitLab tab -->
  <div id="gitlab-pane" class="tab-pane">
    <div id="gitlab-sidebar">
      <div class="gitlab-sidebar-section">Repositories</div>
      <div id="gitlab-repo-list"></div>
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
</div>
<script>
const messagesPanel = document.getElementById('messages-panel');
const input = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const senderSelect = document.getElementById('sender-select');
const channelTitle = document.getElementById('channel-title');
const channelDesc = document.getElementById('channel-desc');
const channelMembersEl = document.getElementById('channel-members');

let currentTab = 'chat';
let currentChannel = '#general';
let channelsData = {};
let messagesByChannel = {};
let unreadByChannel = {};
let seenIds = new Set();

const SENDER_CLASS_MAP = {
  'Sarah (PM)': 'msg-pm',
  'Marcus (Eng Manager)': 'msg-engmgr',
  'Priya (Architect)': 'msg-architect',
  'Alex (Senior Eng)': 'msg-senior',
  'Jordan (Support Eng)': 'msg-support',
  'Taylor (Sales Eng)': 'msg-sales',
  'Dana (CEO)': 'msg-ceo',
  'Morgan (CFO)': 'msg-cfo',
  'Riley (Marketing)': 'msg-marketing',
  'Casey (DevOps)': 'msg-devops',
};

const PERSONA_DISPLAY = {
  'pm': 'Sarah (PM)', 'engmgr': 'Marcus (Eng Mgr)', 'architect': 'Priya',
  'senior': 'Alex', 'support': 'Jordan', 'sales': 'Taylor',
  'ceo': 'Dana', 'cfo': 'Morgan',
  'marketing': 'Riley', 'devops': 'Casey',
};

const HUMAN_CLASS_MAP = {
  'Customer': 'msg-customer', 'Consultant': 'msg-customer',
  'Board Member': 'msg-board', 'Hacker': 'msg-hacker', 'God': 'msg-god',
  'Intern': 'msg-intern', 'Competitor': 'msg-competitor',
  'Regulator': 'msg-regulator', 'Investor': 'msg-investor', 'The Press': 'msg-press',
};

function senderClass(sender) {
  return HUMAN_CLASS_MAP[sender] || SENDER_CLASS_MAP[sender] || 'msg-default';
}

function isHumanSender(sender) {
  return sender in HUMAN_CLASS_MAP;
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
    if (target === 'docs') loadDocs();
    if (target === 'gitlab') loadRepos();
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
  intContainer.innerHTML = '';
  extContainer.innerHTML = '';

  Object.keys(channelsData).sort().forEach(name => {
    const ch = channelsData[name];
    const btn = document.createElement('button');
    btn.className = 'channel-btn' + (name === currentChannel ? ' active' : '');
    const badge = document.createElement('span');
    badge.className = 'unread-badge' + (unreadByChannel[name] > 0 && name !== currentChannel ? ' visible' : '');
    badge.textContent = unreadByChannel[name] || '';
    badge.id = 'badge-' + name.replace('#', '');
    btn.innerHTML = '<span>' + escapeHtml(name) + '</span>';
    btn.appendChild(badge);
    btn.addEventListener('click', () => switchChannel(name));
    if (ch.is_external) {
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
  updateSenderDropdown();
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
  const ch = channelsData[currentChannel];
  senderSelect.innerHTML = '';
  if (ch && ch.is_external) {
    senderSelect.innerHTML =
        '<option value="Customer" selected>Customer</option>'
      + '<option value="Consultant">Consultant</option>'
      + '<option value="Investor">Investor</option>'
      + '<option value="Competitor">Competitor</option>'
      + '<option value="The Press">The Press</option>'
      + '<option value="Regulator">Regulator</option>'
      + '<option value="Hacker">Hacker</option>';
  } else {
    senderSelect.innerHTML =
        '<option value="Consultant" selected>Consultant</option>'
      + '<option value="Board Member">Board Member</option>'
      + '<option value="Investor">Investor</option>'
      + '<option value="Intern">Intern</option>'
      + '<option value="God">God</option>'
      + '<option value="Hacker">Hacker</option>'
      + '<option value="Regulator">Regulator</option>'
      + '<option value="The Press">The Press</option>'
      + '<option value="Competitor">Competitor</option>';
  }
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
  div.className = 'msg ' + (isHumanSender(msg.sender) ? 'msg-customer' : 'msg-agent') + ' ' + cls;
  const ts = new Date(msg.timestamp * 1000).toLocaleTimeString();
  div.innerHTML = '<div class="sender">' + escapeHtml(msg.sender) + '</div>'
    + '<div class="content">' + renderMarkdown(msg.content) + '</div>'
    + '<div class="ts">' + ts + '</div>';
  messagesPanel.appendChild(div);
  messagesPanel.scrollTop = messagesPanel.scrollHeight;
}

function renderMessages() {
  messagesPanel.innerHTML = '';
  const msgs = messagesByChannel[currentChannel] || [];
  msgs.forEach(appendMessageEl);
}

async function loadMessages() {
  const resp = await fetch('/api/messages');
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
    } else {
      addMessage(data);
    }
  });
  es.onerror = () => { setTimeout(connectSSE, 2000); es.close(); };
}

async function send() {
  const content = input.value.trim();
  if (!content) return;
  const sender = senderSelect.value;
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
    card.innerHTML = '<div class="doc-card-meta">'
      + '<span class="doc-card-folder">' + escapeHtml(folder) + '</span>'
      + '</div>'
      + '<div class="doc-card-title">' + escapeHtml(doc.title || doc.slug) + '</div>'
      + '<div class="doc-card-preview">' + escapeHtml(doc.preview || '') + '</div>';
    card.addEventListener('click', () => viewDoc(folder, doc.slug));
    docsList.appendChild(card);
  });
}

async function viewDoc(folder, slug) {
  const resp = await fetch('/api/docs/' + encodeURIComponent(folder) + '/' + encodeURIComponent(slug));
  if (!resp.ok) return;
  const doc = await resp.json();
  docViewerTitle.textContent = doc.title || doc.slug;
  docViewerContent.innerHTML = renderMarkdown(doc.content || '');
  docViewer.classList.add('open');
  docsList.style.display = 'none';
  document.getElementById('docs-toolbar').style.display = 'none';
}

docBackBtn.addEventListener('click', () => {
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
});

let docsSearchTimer = null;
docsSearch.addEventListener('input', () => {
  clearTimeout(docsSearchTimer);
  docsSearchTimer = setTimeout(() => {
    const q = docsSearch.value.trim();
    loadDocs(q || undefined);
  }, 300);
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

// -- Init --
loadChannels().then(() => {
  updateChannelHeader();
  updateSenderDropdown();
  loadMessages();
  connectSSE();
});
loadFolders();
loadRepos();
</script>
</body>
</html>"""


def create_app() -> Flask:
    """Create and configure the Flask chat application."""
    app = Flask(__name__)

    # Clear chat log on startup
    with _lock:
        _messages.clear()
    if CHAT_LOG.exists():
        CHAT_LOG.unlink()
    print("Chat log cleared on startup")

    # Initialize channels, folders, and docs
    _init_channels()
    print(f"Channels initialized: {sorted(_channels.keys())}")

    _init_folders()
    print(f"Folders initialized: {sorted(_folders.keys())}")

    _init_docs()
    print(f"Docs directory ready: {DOCS_DIR}  ({len(_docs_index)} existing docs)")

    _init_gitlab()
    print(f"GitLab storage ready: {GITLAB_DIR}  ({len(_gitlab_repos)} existing repos)")

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
                result.append({
                    "name": name,
                    "description": info["description"],
                    "is_external": info["is_external"],
                    "members": members,
                })
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
            doc_path.write_text(content, encoding="utf-8")
            meta["updated_at"] = time.time()
            meta["size"] = len(content.encode("utf-8"))
            meta["preview"] = content[:100]
            _save_index()

        _broadcast_doc_event("updated", meta)
        return jsonify(meta)

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
            new_content = existing + "\n" + content
            doc_path.write_text(new_content, encoding="utf-8")
            meta["updated_at"] = time.time()
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
