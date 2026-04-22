# Subsystem Architecture Guide

How to build a new subsystem for the Company Simulator. Every subsystem (docs, tickets, email, memos, blog) follows the same structural pattern. This document codifies those patterns so new subsystems are consistent, complete, and don't break existing ones.

## Architecture Overview (v3)

The simulator runs as three processes:

1. **Flask Server** (`lib/webapp/`) — REST API, SSE broadcast, web UI, in-memory state
2. **MCP Server** (`lib/mcp_server.py`) — FastMCP tool server, one endpoint per agent
3. **Container Orchestrator** (`lib/container_orchestrator.py`) — manages podman containers, tier-based execution

Agents interact with the simulation exclusively via **MCP tools** served by the MCP server. The MCP server proxies tool calls to the Flask server's REST API. There is no JSON command parsing layer — agents call tools directly.

## The Pattern

A subsystem is a self-contained feature that agents and humans can interact with. Each subsystem has exactly these layers:

```
lib/<module>.py              State management (the source of truth)
lib/mcp_server.py            MCP tools (agent-callable functions)
lib/personas.py              Prompt context (agents see subsystem state)
lib/chat_client.py           HTTP client (orchestrator -> server)
lib/webapp/routes/<name>.py  REST API (Flask Blueprint)
lib/webapp/template.py       UI tab (HTML/CSS/JS)
lib/webapp/helpers.py        Initialization (_reinitialize)
lib/session.py               Persistence (save/load/clear)
scenario.yaml                Feature flag + sample events
```

Every layer is required. Skip one and the subsystem is incomplete.

## Layer 1: State Module (`lib/<module>.py`)

The core data store. Thread-safe, in-memory, with snapshot/restore for session persistence.

### Required elements

```python
"""One-line description of the subsystem."""

import re
import time
import threading

# Module-level state
_items: dict[str, dict] = {}      # primary data (keyed by slug/id)
_sub_items: list[dict] = []       # secondary data (replies, comments, etc.)
_lock = threading.Lock()

# Slug helper (if items have titles)
def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] if slug else "item"
```

### Required functions

| Function | Purpose |
|----------|---------|
| `create_*(...)` | Create a new primary item. Returns the item dict. |
| `get_items(...)` | List all items, sorted. Optional `include_recent_*` for turn prompt data. |
| `get_item(id)` | Get a single item by ID/slug. Returns dict or None. |
| `delete_item(id)` | Delete item + associated sub-items. Returns bool. |
| `clear_*()` | Wipe all state. Called by `_reinitialize()` and `new_session()`. |
| `get_*_snapshot()` | Return a serializable copy for session save. |
| `restore_*(...)` | Restore from session data. |

If the subsystem has sub-items (replies, comments):

| Function | Purpose |
|----------|---------|
| `create_sub_item(parent_id, ...)` | Add a sub-item. Raises `ValueError` if parent not found. |
| `get_sub_items(parent_id)` | List sub-items for a parent, oldest first. |

### Thread safety rules

- All reads and writes go through `with _lock:`
- Return copies (`dict(item)`), never references
- List mutations use `_sub_items[:] = [...]` for in-place replacement on delete

### ID generation

- Title-based items: `f"{_slugify(title)}-{int(time.time())}"`
- Sequential items: `len(_items) + 1`
- Always include a timestamp in the item dict

## Layer 2: MCP Tools (`lib/mcp_server.py`)

Agents interact with subsystems via MCP tools. Each tool is a function registered on the per-agent FastMCP instance.

### Adding tools

In `lib/mcp_server.py`, add tool functions inside the agent MCP builder. Each tool gets the agent's identity baked in via closure:

```python
@mcp.tool()
async def create_your_item(title: str, body: str) -> str:
    """Create a new item in the your-subsystem."""
    resp = await client.post(f"{flask_url}/api/your/items",
        json={"title": title, "body": body, "author": display_name})
    data = resp.json()
    return f"Created: {data.get('title')} (id: {data.get('id')})"
```

### Register tool names

Add tool names to `MCP_TOOL_NAMES` list in `lib/container_orchestrator.py`:

```python
MCP_TOOL_NAMES = [
    # ... existing tools ...
    # Your subsystem
    "create_your_item", "list_your_items", "read_your_item",
]
```

### Tool naming conventions

- Use snake_case: `create_blog_post`, `list_memos`, `reply_to_memo`
- Prefix with action: `create_`, `list_`, `get_`, `read_`, `update_`, `delete_`
- Return human-readable strings, not JSON — the agent reads the output directly

## Layer 3: Prompt Context (`lib/personas.py`)

Agents see subsystem state in their turn prompts. This is how they discover what exists and decide whether to interact.

### Turn prompt section

```python
def _build_your_section(items: list[dict] | None) -> str:
    """Build a section showing subsystem state for agents."""
    from lib.scenario_loader import get_settings
    if not get_settings().get("enable_your_feature", False):
        return ""
    # ... build section with item summaries
```

Add a parameter to `build_v3_turn_prompt()` and insert the section into `parts`.

### Prompt size discipline

The turn prompt grows with every subsystem. Rules:
- Cap items shown (10-20 max)
- Cap text previews (200 chars)
- Agents discover details by calling MCP tools (read, list) — don't dump everything in the prompt

## Layer 4: HTTP Client (`lib/chat_client.py`)

Thin wrappers around REST calls. The container orchestrator uses these to fetch state for turn prompts.

### Pattern

```python
def get_items(self, include_sub: bool = False) -> list[dict]:
    try:
        params = {}
        if include_sub:
            params["include_sub"] = "1"
        resp = requests.get(f"{self.base_url}/api/your/items", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []
```

- GET methods that feed the turn prompt should have a `try/except` returning empty on failure (graceful degradation)

## Layer 5: REST API (`lib/webapp/routes/<name>.py`)

Each subsystem gets its own Flask Blueprint in the routes directory.

### Blueprint pattern

```python
"""Your subsystem API routes."""
from flask import Blueprint, request, jsonify

bp = Blueprint("your_subsystem", __name__)

@bp.route("/api/your/items", methods=["GET"])
def list_items():
    from lib.your_module import get_items
    return jsonify(get_items())

@bp.route("/api/your/items", methods=["POST"])
def create_item():
    from lib.your_module import create_item
    data = request.get_json(force=True)
    # ... validate and create
    return jsonify(entry), 201
```

### Register the blueprint

In `lib/webapp/__init__.py`:
```python
from lib.webapp.routes.your_subsystem import bp as your_bp
app.register_blueprint(your_bp)
```

### Standard CRUD endpoints

```
GET    /api/your/items              — list
POST   /api/your/items              — create
GET    /api/your/items/<id>         — detail (include sub-items)
PUT    /api/your/items/<id>         — update
DELETE /api/your/items/<id>         — delete
GET    /api/your/items/<id>/subs    — list sub-items
POST   /api/your/items/<id>/subs    — create sub-item
```

### Initialization (`lib/webapp/helpers.py`)

Add `clear_*()` call to `_reinitialize()`. This is called on "Clear Everything" and session operations.

### Event action type

In the events route (`lib/webapp/routes/events.py`), add an `elif action_type == "your_type":` block in `trigger_event()` that creates items from event data.

## Layer 6: UI Tab (`lib/webapp/template.py`)

### Tab button

Add to the header tab bar, ordered logically by information type.

### Tab pane

MUST be inside `#main-layout` div before `<!-- Advanced tab -->`. Getting this wrong causes bottom-alignment bugs.

**Layout**: sidebar + main (same pattern as Email, Memos, Blog):
```html
<div id="your-pane" class="tab-pane">
  <div id="your-sidebar">...</div>
  <div id="your-main">...</div>
</div>
```

**CSS**: use CSS variables (`var(--bg)`, `var(--text)`, etc.) for all theme-eligible colors. Keep semantic colors (status indicators, severity badges) hardcoded.

**JS functions**: `loadYourItems()`, `viewYourItem(id)`, `createYourItem()` + wire to tab click handler.

**Identity fields**: if humans can author content, include Name + Role dropdowns (same pattern as email compose, memo reply). Add role dropdown to `populateAllRoleDropdowns()`.

**Modals**: use `openModal()`/`closeModal()` with the `.modal-overlay` pattern. Never use browser `prompt()`.

## Layer 7: Session Persistence (`lib/session.py`)

Three touch points:

### `save_session()`
```python
try:
    from lib.your_module import get_snapshot
    data = get_snapshot()
    if data.get("items"):
        (instance_dir / "your.json").write_text(json.dumps(data, indent=2))
except Exception:
    pass
```

### `load_session()`
```python
path = instance_dir / "your.json"
if path.exists():
    try:
        from lib.your_module import restore
        data = json.loads(path.read_text())
        restore(data.get("items", {}), data.get("sub_items", []))
    except Exception:
        pass
```

### `new_session()`
```python
try:
    from lib.your_module import clear
    clear()
except Exception:
    pass
```

## Layer 8: Feature Flag (`scenario.yaml`)

### Settings

```yaml
settings:
  enable_your_feature: true
```

Accessed via `get_settings().get("enable_your_feature", False)` in personas.py. When disabled:
- Turn prompt section doesn't appear
- MCP tools still registered (agents just won't know to use them)
- UI tab still renders (shows empty state) — simpler than conditional tab rendering
- API endpoints still work (allows manual testing)

### Sample events

Every scenario that enables the feature should have 1-2 sample events that exercise it. Events create content that agents can react to.

## Checklist

When building a new subsystem, complete every item:

- [ ] `lib/<module>.py` — state module with CRUD, clear, snapshot, restore
- [ ] `lib/mcp_server.py` — MCP tools for agent interaction
- [ ] `lib/container_orchestrator.py` — add tool names to `MCP_TOOL_NAMES`
- [ ] `lib/personas.py` — turn prompt section (gated by feature flag)
- [ ] `lib/chat_client.py` — HTTP client methods for orchestrator
- [ ] `lib/webapp/routes/<name>.py` — Flask Blueprint with CRUD endpoints
- [ ] `lib/webapp/__init__.py` — register the blueprint
- [ ] `lib/webapp/helpers.py` — add `clear_*()` to `_reinitialize()`
- [ ] `lib/webapp/template.py` — UI tab (CSS + HTML + JS), modal, role dropdowns
- [ ] `lib/webapp/routes/events.py` — event action type
- [ ] `lib/session.py` — save, load, new_session clear
- [ ] Scenario YAMLs — feature flag + sample events per scenario
- [ ] Seeded gitlab — copy updated files + add commit entry (for company-simulator-team)

## Common Pitfalls

1. **Tab pane outside `#main-layout`** — causes bottom-alignment. Always inside, before `<!-- Advanced tab -->`.
2. **Missing `MCP_TOOL_NAMES` entry** — tool exists in MCP server but agent containers can't access it.
3. **`_reinitialize()` ordering** — blog save bug: `_reinitialize()` cleared state before session restore re-applied it. Ensure restore happens AFTER reinitialize.
4. **Prompt bloat** — don't dump full content in turn prompts. Show summaries, let agents use MCP tools for details.
5. **`prompt()` for user input** — use modals. Browser popups are bad UX.
6. **Missing `new_session()` clear** — old data leaks across scenario switches.
7. **Hardcoded colors in new CSS** — use `var(--bg)`, `var(--text)`, etc. Only semantic colors stay hardcoded.
8. **Old command references in character files** — character prompts should reference MCP tool names (`list_repo_tree`, `read_file`), not old JSON actions (`TREE`, `FILE_READ`).
