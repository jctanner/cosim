"""Flask chat server with SSE broadcast and web UI."""

import json
import time
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request

from lib.docs import slugify
from lib.personas import DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, PERSONAS


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

def _init_docs():
    """Create docs/ dir and load _index.json or scan existing .txt files."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = DOCS_DIR / "_index.json"
    with _docs_lock:
        _docs_index.clear()
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text())
                _docs_index.update(data)
            except (json.JSONDecodeError, OSError):
                pass
        if not _docs_index:
            # Fallback: scan existing .txt files
            for txt in DOCS_DIR.glob("*.txt"):
                slug = txt.stem
                stat = txt.stat()
                content = txt.read_text(encoding="utf-8", errors="replace")
                _docs_index[slug] = {
                    "slug": slug,
                    "title": slug.replace("-", " ").title(),
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

  #header { background: #16213e; padding: 12px 20px; border-bottom: 1px solid #0f3460;
            display: flex; align-items: center; gap: 12px; }
  #header h1 { font-size: 18px; color: #e94560; }
  #header span { font-size: 13px; color: #888; }
  #docs-toggle { padding: 5px 12px; border-radius: 6px; font-size: 13px; cursor: pointer;
                 border: 1px solid #333; background: #1a1a2e; color: #888;
                 font-weight: 600; margin-left: auto; transition: all 0.15s ease; }
  #docs-toggle:hover { border-color: #e94560; color: #e94560; }
  #docs-toggle.active { border-color: #e94560; color: #e94560; background: #2a0f18; }

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

  /* -- Chat panel -- */
  #chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
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
  .msg-agent { align-self: flex-start; background: #1a1a3e; border: 1px solid #333; border-bottom-left-radius: 4px; }
  .msg-pm .sender { color: #e94560; }
  .msg-engmgr .sender { color: #f39c12; }
  .msg-architect .sender { color: #9b59b6; }
  .msg-senior .sender { color: #2ecc71; }
  .msg-support .sender { color: #1abc9c; }
  .msg-sales .sender { color: #e67e22; }
  .msg-ceo .sender { color: #f1c40f; }
  .msg-cfo .sender { color: #3498db; }
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

  /* -- Docs panel -- */
  #docs-panel { width: 320px; min-width: 320px; background: #121a30; border-left: 1px solid #0f3460;
                display: none; flex-direction: column; overflow: hidden; }
  #docs-panel.open { display: flex; }
  #docs-panel-header { padding: 12px 16px; border-bottom: 1px solid #0f3460; }
  #docs-panel-header h3 { font-size: 14px; color: #e94560; margin-bottom: 8px; }
  #docs-search { width: 100%; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333;
                 padding: 6px 10px; border-radius: 6px; font-size: 13px; outline: none; }
  #docs-search:focus { border-color: #e94560; }
  #docs-list { flex: 1; overflow-y: auto; padding: 8px; }
  .doc-card { background: #1a1a2e; border: 1px solid #333; border-radius: 8px; padding: 10px 12px;
              margin-bottom: 6px; cursor: pointer; transition: border-color 0.15s ease; }
  .doc-card:hover { border-color: #e94560; }
  .doc-card-title { font-size: 13px; font-weight: 700; color: #4fc3f7; margin-bottom: 4px; }
  .doc-card-preview { font-size: 12px; color: #888; overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; }
  #doc-viewer { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #doc-viewer.open { display: flex; }
  #doc-viewer-header { padding: 10px 16px; border-bottom: 1px solid #0f3460;
                       display: flex; align-items: center; gap: 8px; }
  #doc-back-btn { background: transparent; border: 1px solid #333; color: #888; padding: 4px 10px;
                  border-radius: 6px; cursor: pointer; font-size: 12px; }
  #doc-back-btn:hover { border-color: #e94560; color: #e94560; }
  #doc-viewer-title { font-size: 14px; font-weight: 700; color: #4fc3f7; }
  #doc-viewer-content { flex: 1; overflow-y: auto; padding: 12px 16px; font-size: 13px;
                        color: #e0e0e0; line-height: 1.6; }
  #doc-viewer-content h1 { font-size: 17px; margin: 10px 0 6px; }
  #doc-viewer-content h2 { font-size: 15px; margin: 8px 0 4px; }
  #doc-viewer-content h3 { font-size: 14px; margin: 6px 0 3px; }
  #doc-viewer-content p { margin: 4px 0; }
  #doc-viewer-content ul, #doc-viewer-content ol { margin: 4px 0 4px 20px; }
  #doc-viewer-content li { margin: 2px 0; }
  #doc-viewer-content strong { color: #fff; }
  #doc-viewer-content code { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; }
  #doc-viewer-content pre { background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; margin: 4px 0;
                            overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  #doc-viewer-content pre code { background: none; padding: 0; }
  #doc-viewer-content hr { border: none; border-top: 1px solid #444; margin: 8px 0; }
  #doc-viewer-content input[type="checkbox"] { margin-right: 4px; }
  #docs-empty { color: #555; font-size: 13px; text-align: center; padding: 24px 16px; }
</style>
</head>
<body>
<div id="header">
  <h1>Organization Chat</h1>
  <span>Multi-channel agent collaboration</span>
  <button id="docs-toggle">Docs</button>
</div>
<div id="main-layout">
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
  <div id="docs-panel">
    <div id="docs-panel-header">
      <h3>Shared Documents</h3>
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
<script>
const messagesPanel = document.getElementById('messages-panel');
const input = document.getElementById('msg-input');
const btn = document.getElementById('send-btn');
const senderSelect = document.getElementById('sender-select');
const channelTitle = document.getElementById('channel-title');
const channelDesc = document.getElementById('channel-desc');
const channelMembersEl = document.getElementById('channel-members');

let currentChannel = '#general';
let channelsData = {};         // name -> {description, is_external, members}
let messagesByChannel = {};    // channel -> [msg, ...]
let unreadByChannel = {};      // channel -> count
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
};

const PERSONA_DISPLAY = {
  'pm': 'Sarah (PM)', 'engmgr': 'Marcus (Eng Mgr)', 'architect': 'Priya',
  'senior': 'Alex', 'support': 'Jordan', 'sales': 'Taylor',
  'ceo': 'Dana', 'cfo': 'Morgan',
};

function senderClass(sender) {
  if (sender === 'Customer' || sender === 'Consultant') return 'msg-customer';
  return SENDER_CLASS_MAP[sender] || 'msg-default';
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
    senderSelect.innerHTML = '<option value="Customer" selected>Customer</option>'
                           + '<option value="Consultant">Consultant</option>';
  } else {
    senderSelect.innerHTML = '<option value="Consultant" selected>Consultant</option>';
  }
}

// -- Messages --

function addMessage(msg) {
  if (seenIds.has(msg.id)) return;
  seenIds.add(msg.id);
  const ch = msg.channel || '#general';
  if (!messagesByChannel[ch]) messagesByChannel[ch] = [];
  messagesByChannel[ch].push(msg);

  if (ch === currentChannel) {
    appendMessageEl(msg);
  } else {
    unreadByChannel[ch] = (unreadByChannel[ch] || 0) + 1;
    renderSidebar();
  }
}

function appendMessageEl(msg) {
  const div = document.createElement('div');
  const cls = senderClass(msg.sender);
  div.className = 'msg ' + (msg.sender === 'Customer' || msg.sender === 'Consultant' ? 'msg-customer' : 'msg-agent') + ' ' + cls;
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
      if (docsPanel.classList.contains('open')) loadDocs();
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

btn.addEventListener('click', send);
document.getElementById('clear-btn').addEventListener('click', clearChat);
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

// -- Docs panel --
const docsToggle = document.getElementById('docs-toggle');
const docsPanel = document.getElementById('docs-panel');
const docsList = document.getElementById('docs-list');
const docsEmpty = document.getElementById('docs-empty');
const docsSearch = document.getElementById('docs-search');
const docViewer = document.getElementById('doc-viewer');
const docViewerTitle = document.getElementById('doc-viewer-title');
const docViewerContent = document.getElementById('doc-viewer-content');
const docBackBtn = document.getElementById('doc-back-btn');

docsToggle.addEventListener('click', () => {
  docsToggle.classList.toggle('active');
  docsPanel.classList.toggle('open');
  if (docsPanel.classList.contains('open')) loadDocs();
});

async function loadDocs(query) {
  let url = '/api/docs';
  if (query) url = '/api/docs/search?q=' + encodeURIComponent(query);
  const resp = await fetch(url);
  const docs = await resp.json();
  renderDocList(docs);
}

function renderDocList(docs) {
  docsList.querySelectorAll('.doc-card').forEach(el => el.remove());
  docsEmpty.style.display = docs.length ? 'none' : 'block';
  docs.forEach(doc => {
    const card = document.createElement('div');
    card.className = 'doc-card';
    card.innerHTML = '<div class="doc-card-title">' + escapeHtml(doc.title || doc.slug) + '</div>'
      + '<div class="doc-card-preview">' + escapeHtml(doc.preview || '') + '</div>';
    card.addEventListener('click', () => viewDoc(doc.slug));
    docsList.appendChild(card);
  });
}

async function viewDoc(slug) {
  const resp = await fetch('/api/docs/' + encodeURIComponent(slug));
  if (!resp.ok) return;
  const doc = await resp.json();
  docViewerTitle.textContent = doc.title || doc.slug;
  docViewerContent.innerHTML = renderMarkdown(doc.content || '');
  docViewer.classList.add('open');
  docsList.style.display = 'none';
  document.getElementById('docs-panel-header').style.display = 'none';
}

docBackBtn.addEventListener('click', () => {
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-panel-header').style.display = '';
});

let docsSearchTimer = null;
docsSearch.addEventListener('input', () => {
  clearTimeout(docsSearchTimer);
  docsSearchTimer = setTimeout(() => {
    const q = docsSearch.value.trim();
    loadDocs(q || undefined);
  }, 300);
});

// -- Init --
loadChannels().then(() => {
  updateChannelHeader();
  updateSenderDropdown();
  loadMessages();
  connectSSE();
});
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

    # Initialize channels and docs
    _init_channels()
    print(f"Channels initialized: {sorted(_channels.keys())}")

    _init_docs()
    print(f"Docs directory ready: {DOCS_DIR}  ({len(_docs_index)} existing docs)")

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

    # -- Document API --

    @app.route("/api/docs", methods=["GET"])
    def list_docs():
        with _docs_lock:
            return jsonify(list(_docs_index.values()))

    @app.route("/api/docs", methods=["POST"])
    def create_doc():
        data = request.get_json(force=True)
        title = data.get("title", "").strip()
        content = data.get("content", "")
        author = data.get("author", "unknown")
        if not title:
            return jsonify({"error": "title required"}), 400

        slug = slugify(title)
        doc_path = DOCS_DIR / f"{slug}.txt"

        with _docs_lock:
            if slug in _docs_index:
                return jsonify({"error": f"document '{slug}' already exists"}), 409

            doc_path.write_text(content, encoding="utf-8")
            now = time.time()
            meta = {
                "slug": slug,
                "title": title,
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
        if not query:
            return jsonify([])

        results = []
        with _docs_lock:
            for slug, meta in _docs_index.items():
                doc_path = DOCS_DIR / f"{slug}.txt"
                if not doc_path.exists():
                    continue
                content = doc_path.read_text(encoding="utf-8", errors="replace")
                if query in meta.get("title", "").lower() or query in content.lower():
                    results.append({
                        **meta,
                        "snippet": _extract_snippet(content, query),
                    })
        return jsonify(results)

    @app.route("/api/docs/<slug>", methods=["GET"])
    def get_doc(slug):
        with _docs_lock:
            meta = _docs_index.get(slug)
        if meta is None:
            return jsonify({"error": "not found"}), 404
        doc_path = DOCS_DIR / f"{slug}.txt"
        if not doc_path.exists():
            return jsonify({"error": "not found"}), 404
        content = doc_path.read_text(encoding="utf-8", errors="replace")
        return jsonify({**meta, "content": content})

    @app.route("/api/docs/<slug>", methods=["PUT"])
    def update_doc(slug):
        data = request.get_json(force=True)
        content = data.get("content", "")
        author = data.get("author", "unknown")

        with _docs_lock:
            meta = _docs_index.get(slug)
            if meta is None:
                return jsonify({"error": "not found"}), 404

            doc_path = DOCS_DIR / f"{slug}.txt"
            doc_path.write_text(content, encoding="utf-8")
            meta["updated_at"] = time.time()
            meta["size"] = len(content.encode("utf-8"))
            meta["preview"] = content[:100]
            _save_index()

        _broadcast_doc_event("updated", meta)
        return jsonify(meta)

    @app.route("/api/docs/<slug>/append", methods=["POST"])
    def append_doc(slug):
        data = request.get_json(force=True)
        content = data.get("content", "")
        author = data.get("author", "unknown")

        with _docs_lock:
            meta = _docs_index.get(slug)
            if meta is None:
                return jsonify({"error": "not found"}), 404

            doc_path = DOCS_DIR / f"{slug}.txt"
            existing = doc_path.read_text(encoding="utf-8", errors="replace")
            new_content = existing + "\n" + content
            doc_path.write_text(new_content, encoding="utf-8")
            meta["updated_at"] = time.time()
            meta["size"] = len(new_content.encode("utf-8"))
            meta["preview"] = new_content[:100]
            _save_index()

        _broadcast_doc_event("appended", meta)
        return jsonify(meta)

    @app.route("/api/docs/<slug>", methods=["DELETE"])
    def delete_doc(slug):
        with _docs_lock:
            meta = _docs_index.pop(slug, None)
            if meta is None:
                return jsonify({"error": "not found"}), 404

            doc_path = DOCS_DIR / f"{slug}.txt"
            if doc_path.exists():
                doc_path.unlink()
            _save_index()

        _broadcast_doc_event("deleted", meta)
        return jsonify({"status": "deleted", "slug": slug})

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
