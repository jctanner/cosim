# Subsystem Architecture Guide

How to build a new subsystem for the Company Simulator. Every subsystem (docs, tickets, email, memos, blog) follows the same structural pattern. This document codifies those patterns so new subsystems are consistent, complete, and don't break existing ones.

## The Pattern

A subsystem is a self-contained feature that agents and humans can interact with. Each subsystem has exactly these layers:

```
lib/<module>.py          State management (the source of truth)
lib/response_schema.py   Command parsing (agent JSON -> flat dicts)
lib/personas.py          Prompt injection (agents learn about the subsystem)
lib/chat_client.py       HTTP client (orchestrator -> server)
lib/orchestrator.py      Command execution (process agent commands)
lib/webapp.py            REST API + UI tab + event action type
lib/session.py           Persistence (save/load/clear)
scenario.yaml            Feature flag + sample events
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

## Layer 2: Command Parsing (`lib/response_schema.py`)

Agents issue commands via JSON. The parser splits them by type.

### What to change

1. **Return tuple** — add your type to the end. The tuple grows by one.
2. **New list** — `your_cmds = []`
3. **New elif** — `elif cmd_type == "your_type": your_cmds.append(flat)`
4. **Empty return** — add one more `[]` to the empty return

The flat dict merges `action` + `params`:
```python
{"type": "blog", "action": "CREATE", "params": {"title": "..."}}
# becomes:
{"action": "CREATE", "title": "..."}
```

### Critical: update ALL callers

The return tuple is unpacked in `orchestrator.py:_process_json_response()`. When you add an element, you MUST update the unpack there or everything breaks with a `ValueError: not enough values to unpack`.

## Layer 3: Prompt Injection (`lib/personas.py`)

Agents only know about subsystems that appear in their prompts. Two integration points:

### Initial prompt (command docs)

```python
def _build_your_command_docs() -> str:
    """Return command docs if feature is enabled."""
    from lib.scenario_loader import get_settings
    if not get_settings().get("enable_your_feature", False):
        return ""
    return """
**Your commands** (`type: "your_type"`):

| action | params |
|--------|--------|
| `CREATE` | ... |
| `READ` | ... |
| `LIST` | ... |
"""

def _build_your_command_example() -> str:
    """Return example if feature is enabled."""
    from lib.scenario_loader import get_settings
    if not get_settings().get("enable_your_feature", False):
        return ""
    return """,
    {{"type": "your_type", "action": "CREATE", "params": {{...}}}}"""
```

Wire into `build_initial_prompt()`:
- Add `{_build_your_command_docs()}` after the last command docs block
- Add `{_build_your_command_example()}` at the end of the example JSON

### Turn prompt (state section)

```python
def _build_your_section(items: list[dict] | None) -> str:
    """Build a section showing subsystem state for agents."""
    from lib.scenario_loader import get_settings
    if not get_settings().get("enable_your_feature", False):
        return ""
    # ... build section with item summaries
```

Add a parameter to `build_turn_prompt()` and insert the section into `parts`.

### Prompt size discipline

The turn prompt grows with every subsystem. Rules:
- Cap items shown (10-20 max)
- Cap text previews (200 chars)
- READ/LIST results are SILENT (no system messages). Agents see data in the turn prompt section.
- Only CREATE/UPDATE actions broadcast short notifications to chat.

This is critical. The memo-list READ bug taught us: dumping full content into permanent chat messages causes "prompt too long" errors because every agent re-ingests the entire chat history every turn.

## Layer 4: HTTP Client (`lib/chat_client.py`)

Thin wrappers around REST calls. The orchestrator uses these to talk to the server.

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

def create_item(self, ...) -> dict:
    resp = requests.post(
        f"{self.base_url}/api/your/items",
        json={...},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
```

- GET methods that feed the turn prompt should have a `try/except` returning empty on failure (graceful degradation)
- POST/PUT methods should let exceptions propagate (the orchestrator catches them)

## Layer 5: Command Execution (`lib/orchestrator.py`)

Two functions per subsystem:

### `_execute_*_commands(client, commands, author) -> list[dict]`

Iterates commands, calls ChatClient methods, returns result dicts with `{"action": ..., "ok": True/False, ...}`.

### `_log_*_results(client, persona, results)`

Decides what to broadcast and what to keep silent:
- **CREATE/UPDATE/POST** — broadcast a short `[Subsystem] Person did X` notification via `_post_system()`
- **READ/LIST** — silent (just `print()` for server logs)

### Integration in `_process_json_response()`

```python
if your_cmds:
    if on_activity:
        on_activity("doing your thing")
    results = _execute_your_commands(client, your_cmds, author)
    _log_your_results(client, persona, results)
```

### Integration in `_run_autonomous_round()`

Fetch subsystem data for turn prompts:
```python
your_data = client.get_items(include_sub=True)
# ... pass to build_turn_prompt(your_items=your_data)
```

## Layer 6: REST API + UI (`lib/webapp.py`)

### API endpoints

Standard CRUD pattern:
```
GET    /api/your/items              — list
POST   /api/your/items              — create
GET    /api/your/items/<id>         — detail (include sub-items)
PUT    /api/your/items/<id>         — update
DELETE /api/your/items/<id>         — delete
GET    /api/your/items/<id>/subs    — list sub-items
POST   /api/your/items/<id>/subs    — create sub-item
```

### `_reinitialize()`

Add `clear_*()` call. This is called on "Clear Everything" and session operations.

### Event action type

In `trigger_event()`, add an `elif action_type == "your_type":` block that creates items from event data.

### UI tab

**Tab button placement**: in the header, ordered logically by information type.

**Tab pane placement**: MUST be inside `#main-layout` div before its closing `</div>` which comes right before `<!-- Advanced tab -->`. Getting this wrong causes bottom-alignment bugs.

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

### Tab click handler

```javascript
if (target === 'your') loadYourItems();
```

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
- Command docs don't appear in agent prompts
- Turn prompt section doesn't appear
- UI tab still renders (shows empty state) — simpler than conditional tab rendering
- API endpoints still work (allows manual testing)

### Sample events

Every scenario that enables the feature should have 1-2 sample events that exercise it. Events create content that agents can react to.

## Checklist

When building a new subsystem, complete every item:

- [ ] `lib/<module>.py` — state module with CRUD, clear, snapshot, restore
- [ ] `lib/response_schema.py` — new command type in `normalize_commands()`, tuple grows by 1
- [ ] `lib/personas.py` — command docs (gated), command example, turn prompt section, `build_turn_prompt()` parameter
- [ ] `lib/chat_client.py` — HTTP client methods (GET with graceful fallback, POST/PUT propagate errors)
- [ ] `lib/orchestrator.py` — execute + log functions, unpack new tuple element, fetch data for turn prompt
- [ ] `lib/webapp.py` — API endpoints, `_reinitialize()`, event action type, UI tab (CSS + HTML + JS), modal, role dropdowns
- [ ] `lib/session.py` — save, load, new_session clear
- [ ] Scenario YAMLs — feature flag + sample events per scenario
- [ ] Seeded gitlab — copy updated files + add commit entry (for company-simulator-team)
- [ ] Syntax check — `python -c "import ast; ast.parse(open('lib/<file>.py').read())"` on all modified files

## Common Pitfalls

1. **Tab pane outside `#main-layout`** — causes bottom-alignment. Always inside, before `<!-- Advanced tab -->`.
2. **`\n` in JS strings inside Python** — use space or `\\n`, not `\n`, or the JS breaks. Validate with `node --check`.
3. **Tuple unpack mismatch** — adding to `normalize_commands()` return without updating `_process_json_response()` unpack.
4. **READ dumping to chat** — never post full content as system messages. It persists in chat history and bloats every agent's turn prompt.
5. **`prompt()` for user input** — use modals. Browser popups are bad UX.
6. **Missing `_reinitialize()` call** — "Clear Everything" won't clear your subsystem's state.
7. **Missing `new_session()` clear** — old data leaks across scenario switches.
8. **Hardcoded colors in new CSS** — use `var(--bg)`, `var(--text)`, etc. Only semantic colors stay hardcoded.
