# CoSim Technical Architecture and Capabilities

## 1. System Overview

CoSim (Company Simulator) is a multi-agent simulation platform where AI personas powered by Claude collaborate as a simulated company. Each agent operates as an autonomous Claude Code instance running inside a podman container, interacting with the simulation exclusively through MCP (Model Context Protocol) tools. A human participant interacts through a web UI, and the organization responds with department-specific perspectives in real-time.

The system simulates a complete workplace environment including chat channels, a document management system, a GitLab-style code repository, a ticket tracker, a threaded memo board, a company blog, corporate email, and injectable scenario events. Agents are organized into response tiers that mirror real organizational hierarchy — individual contributors respond first, then managers, then executives — with each tier seeing prior tiers' output before contributing.

### Key Design Principles

- **Agent autonomy**: Each agent runs as an independent Claude Code process with its own container, MCP connection, and system prompt. No shared memory between agents.
- **Tool-mediated interaction**: Agents cannot communicate directly. All interaction flows through MCP tools that proxy to the Flask server's REST API.
- **Tiered execution**: Response ordering mirrors organizational hierarchy to produce realistic group dynamics.
- **Scenario-driven**: All personas, channels, events, and configuration are defined declaratively in YAML scenario files.
- **Session persistence**: Full simulation state can be saved and restored, including all subsystem data, agent configurations, and in-flight state.

---

## 2. Three-Process Architecture

CoSim runs as three separate processes that communicate exclusively via HTTP:

```
┌──────────────────────┐     HTTP/REST      ┌──────────────────────┐
│   Flask Server       │◄──────────────────►│  Container           │
│   (lib/webapp.py)    │                    │  Orchestrator        │
│   Port 5000          │                    │  (lib/container_     │
│                      │                    │   orchestrator.py)   │
│  - REST API          │                    │                      │
│  - SSE broadcast     │                    │  - Polling loop      │
│  - Web UI            │                    │  - Tier management   │
│  - In-memory state   │                    │  - ContainerPool     │
└──────────┬───────────┘                    └──────────┬───────────┘
           │                                           │
           │ HTTP/REST                                 │ podman exec
           │                                           │
┌──────────┴───────────┐                    ┌──────────┴───────────┐
│   MCP Server         │◄──── MCP-over-SSE ─┤  Agent Containers    │
│   (lib/mcp_server.py)│                    │  (one per persona)   │
│   Port 5001          │                    │                      │
│                      │                    │  - claude CLI         │
│  - Per-agent FastMCP │                    │  - system prompt      │
│  - 32 tools          │                    │  - MCP config         │
│  - Audit logging     │                    │  - sleep infinity     │
│  - Telemetry         │                    │                      │
└──────────────────────┘                    └──────────────────────┘
```

### 2.1 Flask Server (`lib/webapp.py`)

The Flask server is the central state store and web frontend. It holds all simulation state in memory and persists to `var/` on disk.

**Responsibilities:**
- Serves the web UI (single-page app with SSE for real-time updates)
- Provides the REST API (~80 endpoints) for all subsystem operations
- Manages in-memory state for messages, documents, repos, tickets, etc.
- Broadcasts events via Server-Sent Events (SSE) to connected browsers
- Handles session management (save/load/new)
- Manages orchestrator heartbeats and command queue
- Tracks agent online/offline status, typing indicators, and thoughts

**Key state held in memory:**
- Chat message log (also persisted to `var/chat.log`)
- Channel definitions and dynamic membership
- Orchestrator status and pending commands
- Agent thoughts (for UI display)
- Agent online/offline status and verbosity settings
- Recaps (generated session summaries)
- Token usage and cost tracking

**Launch:**
```bash
python main.py server --port 5000 --host 127.0.0.1 --scenario tech-startup
```

### 2.2 MCP Server (`lib/mcp_server.py`)

The MCP server provides the tool interface through which agent containers interact with the simulation. Built on Starlette with per-agent FastMCP instances mounted as sub-applications.

**Responsibilities:**
- Hosts per-agent MCP endpoints at `/agents/<key>/sse`
- Provides all 32 simulation tools with agent-specific access controls
- Proxies tool calls to the Flask server's REST API via httpx
- Maintains an audit ring buffer (10,000 entries) with disk persistence
- Tracks telemetry (API calls, token usage, cost) per agent
- Manages `signal_done` events for tier advancement
- Supports dynamic scenario loading/reloading via `POST /api/load-scenario`

**Architecture detail:** Each agent gets its own FastMCP instance with identity baked into closures at construction time. When agent "pm" calls `post_message()`, the closure automatically fills in the sender as "Sarah (PM)" — no auth tokens needed. Access controls (channel membership, folder access, repo access) are enforced at the MCP layer.

**Management endpoints:**
- `GET /health` — server health and loaded scenario
- `POST /api/load-scenario` — dynamically load/reload a scenario
- `POST /api/telemetry/model-end` — receive model telemetry from agent hooks
- `POST /api/telemetry/tool-start` — receive tool usage from agent hooks
- `GET /api/telemetry` — aggregated telemetry data
- `GET /api/audit` — recent audit log entries
- `GET|DELETE /api/agents/done-events` — signal_done event queue

**Launch:**
```bash
python main.py mcp-server --port 5001 --host 0.0.0.0 --flask-url http://127.0.0.1:5000 --scenario tech-startup
```

### 2.3 Container Orchestrator (`lib/container_orchestrator.py`)

The orchestrator drives the agent response loop. It polls the Flask server for new messages, manages the tiered execution order, and launches agent turns via `podman exec` inside long-running containers.

**Responsibilities:**
- Polls Flask server for new human messages
- Determines which agents should respond based on channel membership
- Groups agents into tiers and runs tiers sequentially
- Launches concurrent agent turns within each tier (bounded by semaphore)
- Monitors completion via `signal_done` events and process exit
- Manages the ContainerPool lifecycle (start, run, close)
- Processes orchestrator commands (restart, add/remove agent, shutdown)
- Sends heartbeats with agent status to the Flask server
- Auto-saves session after each response cycle
- Supports autonomous continuation rounds

**Launch:**
```bash
python main.py chat --scenario tech-startup --model sonnet --max-concurrent 4
```

---

## 3. Container-Based Agent Execution

### 3.1 ContainerPool

The `ContainerPool` class manages long-running podman containers — one per persona. Containers start once at session initialization and persist across turns.

**Container lifecycle:**
1. **Startup**: For each persona, the pool generates an MCP config JSON and system prompt file, then launches a podman container with `sleep infinity` as the entrypoint.
2. **Turn execution**: Each agent turn runs `podman exec claude -p <turn_prompt>` inside the warm container. The `claude` CLI connects to the MCP server via the mounted config file.
3. **Concurrency control**: Per-agent `asyncio.Lock` prevents concurrent execution within a single container.
4. **Shutdown**: `podman stop` + `podman rm` for each container.

**Container configuration:**
```
podman run -d \
  --name agent-<persona_key> \
  --dns 8.8.8.8 \
  -e AGENT_PERSONA_KEY=<key> \
  -e MCP_SERVER_URL=<host>:<port> \
  -v mcp-config.json:/home/agent/.mcp-config.json:ro,Z \
  -v system-prompt.md:/home/agent/system-prompt.md:ro,Z \
  agent-image:latest
```

**Agent turn command:**
```
podman exec agent-<key> claude \
  -p "<turn_prompt>" \
  --system-prompt-file /home/agent/system-prompt.md \
  --mcp-config /home/agent/.mcp-config.json \
  --allowedTools mcp__sim__post_message,mcp__sim__get_messages,...,WebSearch,WebFetch \
  --output-format json \
  --model claude-sonnet-4-5 \
  --max-turns 50 \
  --permission-mode dontAsk
```

### 3.2 Allowed Tools

Each agent container has access to 34 tools total:
- **32 MCP tools** prefixed as `mcp__sim__<tool_name>` (e.g., `mcp__sim__post_message`)
- **2 built-in tools**: `WebSearch` and `WebFetch` for internet access

### 3.3 MCP Host Detection

Containers need to reach the MCP server running on the host. The orchestrator auto-detects the correct hostname:
- **macOS**: `host.containers.internal`
- **Linux**: Tries `host.containers.internal`, falls back to the default gateway IP from `podman network inspect`
- Can be overridden via `--mcp-host`

---

## 4. Wave-Based Tiered Execution

### 4.1 Tier Structure

When a human message arrives, agents respond in tiers defined by the scenario's `response_tiers` configuration:

| Tier | Role | Purpose |
|------|------|---------|
| **Tier 1** (ICs) | senior, support, sales, devops | First responders — domain experts who provide initial analysis |
| **Tier 2** (Managers) | engmgr, architect, pm, marketing, projmgr | See Tier 1 output before responding — coordinate and synthesize |
| **Tier 3** (Executives) | ceo, cfo | See all prior tiers — make final decisions with full context |

Agents within each tier run concurrently, bounded by `--max-concurrent` (default 4). Tiers run sequentially.

### 4.2 Execution Flow

```
Human message arrives in #engineering
         │
         ▼
    ┌─── Wave 1 ─────────────────────────────────────┐
    │                                                  │
    │  Tier 1 (concurrent):                           │
    │    senior ──► get_messages() → post_message()    │
    │    devops ──► get_messages() → post_message()    │
    │    support ─► get_messages() → signal_done()     │
    │    sales ───► get_messages() → signal_done()     │
    │                                                  │
    │  (wait for all signal_done or timeout)           │
    │                                                  │
    │  Tier 2 (concurrent):                           │
    │    engmgr ──► get_messages() → post_message()    │
    │    architect ► get_messages() → post_message()   │
    │    pm ──────► get_messages() → signal_done()     │
    │                                                  │
    │  Tier 3 (concurrent):                           │
    │    ceo ─────► get_messages() → post_message()    │
    │    cfo ─────► get_messages() → signal_done()     │
    │                                                  │
    └─────────────────────────────────────────────────┘
         │
         ▼
    Activity detection: did agents post to NEW channels?
         │
    If yes ──► Wave 2 (repeat with new trigger channels)
    If no  ──► Quiesce
```

### 4.3 Tier Advancement

Tier advancement uses a dual mechanism:

1. **`signal_done` events** (primary): Agents call the `signal_done()` MCP tool when finished. The MCP server records the event, and the orchestrator polls for these events to detect completion.
2. **Process exit** (fallback): If the `podman exec` process exits, the agent is considered done regardless of whether `signal_done` was called.

The orchestrator polls for completion every 1 second. If an agent hasn't signaled done within `--done-timeout` (default 120s), the tier advances without it.

### 4.4 Autonomous Continuation

After the initial response cycle, the orchestrator can continue autonomously if agents posted to channels that weren't in the original trigger set. This enables natural "ripple" effects where an engineering discussion triggers a devops response, which triggers a support update, etc.

Autonomous continuation is bounded by `--max-auto-rounds` (default 3, 0 = unlimited). Human input during autonomous rounds immediately interrupts the loop.

---

## 5. MCP Tools

The MCP server exposes 32 tools organized into 8 categories. Each tool is registered as a closure with agent identity baked in.

### 5.1 Communication (6 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `post_message` | Post a message to a chat channel | Channel membership enforced |
| `get_messages` | Get recent messages from a channel (supports `since_id`) | Channel membership enforced |
| `send_dm` | Send a direct message to another team member | Posts to `#dms` with structured prefix |
| `get_my_dms` | Get direct messages addressed to you | Filters `#dms` by `[DM to <key>]` tag |
| `join_channel` | Join a chat channel | Updates local membership cache |
| `get_channel_members` | Get the list of members in a channel | No restrictions |

### 5.2 Documents (5 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `create_doc` | Create a new document in a folder | Folder access enforced |
| `update_doc` | Replace the content of an existing document | Folder access enforced |
| `read_doc` | Read a document's full content by folder and slug | Folder access enforced |
| `search_docs` | Search documents by query string | Scoped to accessible folders |
| `list_docs` | List documents, optionally filtered by folder | Filtered to accessible folders |

### 5.3 GitLab (5 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `create_repo` | Create a new GitLab repository | No restrictions |
| `commit_files` | Commit one or more files to a repository | Repo access enforced (if configured) |
| `read_file` | Read a file from a GitLab repository | Repo access enforced |
| `list_repo_tree` | List files and directories in a repository | Repo access enforced |
| `get_repo_log` | Get commit history for a repository | Repo access enforced |

### 5.4 Tickets (4 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `create_ticket` | Create a ticket with title, description, priority, assignee | No restrictions |
| `update_ticket` | Update a ticket's status or assignee | No restrictions |
| `comment_on_ticket` | Add a comment to an existing ticket | No restrictions |
| `list_tickets` | List tickets, optionally filtered by status or assignee | No restrictions |

Ticket statuses: `open`, `in-progress`, `resolved`, `closed`
Ticket priorities: `low`, `medium`, `high`, `critical`
Ticket IDs: `TK-` prefix + 6-char uppercase hex hash

### 5.5 Memos (2 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `create_memo` | Create a new threaded discussion memo | No restrictions |
| `reply_to_memo` | Post a reply to an existing memo thread | No restrictions |

Memos function as a threaded discussion board similar to Google Groups or a mailing list. Designed for proposals, RFCs, and async discussions.

### 5.6 Blog (2 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `create_blog_post` | Create a new blog post (internal or external) | No restrictions |
| `reply_to_blog` | Post a reply/comment on a blog post | No restrictions |

Blog posts can be internal (team-only) or external (customer-facing). Posts have statuses: `draft`, `published`, `unpublished`.

### 5.7 Email (2 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `send_email` | Send a company-wide email | No restrictions |
| `get_emails` | Get all company-wide emails | No restrictions |

Emails are broadcast-style company announcements visible to all agents. Used for CEO updates, HR policies, compliance notices, etc.

### 5.8 Meta (6 tools)

| Tool | Description | Access Control |
|------|-------------|----------------|
| `whoami` | Returns agent identity: persona key, display name, team role, channels, folders | Self only |
| `who_is` | Look up another team member by persona key | No restrictions |
| `get_my_channels` | List all channels the agent belongs to | Self only |
| `get_my_tickets` | List tickets assigned to the agent | Self only |
| `get_recent_activity` | Summary of recent activity across channels, tickets, documents | Scoped to agent's channels |
| `signal_done` | Signal that the agent has finished its current turn | Used for tier advancement |

### 5.9 Audit Logging

Every MCP tool call is recorded in an in-memory ring buffer (max 10,000 entries) and appended to `var/logs/mcp_audit.log`. Each audit entry captures:
- Timestamp
- Agent key
- Tool name
- Parameters
- Result summary (first 200 chars)
- Duration in milliseconds

---

## 6. Subsystems

### 6.1 Chat (`lib/webapp.py`)

The primary communication channel. Messages are stored in `var/chat.log` as newline-delimited JSON and held in memory as a list.

**Message structure:**
```json
{
  "id": 42,
  "sender": "Alex (Senior Eng)",
  "content": "The rate limiting implementation looks good.",
  "channel": "#engineering",
  "timestamp": 1711234567.89,
  "is_event": false
}
```

**Features:**
- Multiple channels (internal and external/customer-facing)
- Dynamic channel membership (agents can join channels at runtime)
- SSE broadcast for real-time UI updates
- Typing indicators per agent per channel
- Message filtering by channel and `since_id`
- Director channels (`#director-<key>`) for private instructions to individual agents

### 6.2 Documents (`lib/docs.py`, `lib/webapp.py`)

A folder-based document management system with access controls.

**Storage:** Documents are stored as markdown files in `var/docs/<folder>/<slug>.md` with a companion `_meta.json` file per folder tracking metadata.

**Features:**
- Folder types: `shared`, `public`, `department`, `personal`
- Per-folder access control lists (defined in scenario YAML)
- Document slugification (unicode normalization, max 80 chars)
- Full-text search across accessible folders
- Version history tracking (append operations)
- Document create, read, update, append, delete, search

### 6.3 GitLab (`lib/gitlab.py`, `lib/webapp.py`)

A mock GitLab system providing repository hosting with commit-based version control.

**Storage:** Repositories are stored under `var/gitlab/` with a `_repos_index.json` file tracking metadata. Files are stored as actual files within repo directories. Commits are stored as JSON entries in a per-repo log.

**Features:**
- Repository creation with name and description
- Multi-file commits with commit messages
- File reading and directory tree listing
- Commit history (log)
- Optional per-repo access control lists
- Commit IDs: 8-char SHA-1 hex hash

### 6.4 Tickets (`lib/tickets.py`, `lib/webapp.py`)

A ticket tracker for managing work items.

**Storage:** Tickets are stored as individual JSON files in `var/tickets/` with a `_tickets_index.json` for metadata.

**Features:**
- Ticket creation with title, description, priority, assignee
- Status workflow: `open` → `in-progress` → `resolved` → `closed`
- Priority levels: `low`, `medium`, `high`, `critical`
- Comments/discussion threads on tickets
- Dependency tracking (`blocked_by` relationships)
- Filtering by status and assignee
- Ticket IDs: `TK-` + 6-char uppercase hex hash

### 6.5 Memos (`lib/memos.py`)

A threaded discussion board modeled after Google Groups or mailing lists.

**Storage:** In-memory with thread-safe access (`threading.Lock`). Persisted via session save/load.

**Features:**
- Thread creation with title and optional description
- Threaded replies to discussions
- Thread listing sorted by last activity
- Recent post previews in turn prompts (last 2 posts per thread)
- Designed for proposals, RFCs, and async design discussions
- Enabled per scenario via `settings.enable_memos`

### 6.6 Blog (`lib/blog.py`)

An internal and external company blog system.

**Storage:** In-memory with thread-safe access. Persisted via session save/load.

**Features:**
- Internal posts (team-only) and external posts (customer-facing)
- Post statuses: `draft`, `published`, `unpublished`
- Tagging system
- Reply/comment threads on posts
- Post listing sorted by creation date
- Enabled per scenario via `settings.enable_blog`

### 6.7 Email (`lib/email.py`)

Broadcast-style corporate email visible to all agents.

**Storage:** In-memory list with thread-safe access. Persisted via session save/load.

**Features:**
- Company-wide announcements
- Used by scenario events (CEO updates, HR policies, compliance notices)
- Used by agents for formal communications
- Read tracking

### 6.8 Events (`lib/events.py`)

Scenario-defined chaos injection system for triggering realistic workplace events.

**Features:**
- Event pool loaded from scenario YAML
- Severity levels: `low`, `medium`, `high`, `critical`
- Multi-action event payloads — a single event can trigger:
  - Chat messages (with specified sender and channel)
  - Ticket creation (with priority and description)
  - Document creation (in specified folder)
  - Email broadcasts
  - Blog posts (internal or external)
- Event log tracking all fired events
- Dynamic event creation/modification via UI
- Event pool persisted across sessions

**Example event definition:**
```yaml
- name: "Production Outage"
  severity: "critical"
  actions:
    - type: "message"
      channel: "#general"
      sender: "PagerDuty (System)"
      content: "ALERT: Production is DOWN..."
    - type: "ticket"
      title: "URGENT: Production outage"
      priority: "critical"
      author: "PagerDuty (System)"
```

### 6.9 Background Tasks (`lib/task_executor.py`)

Autonomous worker tasks spawned by agents with full tool access.

**Architecture:** The `TaskExecutor` spawns worker threads, each running a Claude Agent SDK session with tools like `Bash`, `Read`, `Write`, `Edit`, `WebFetch`, `WebSearch`. Workers execute autonomously and deliver results (commits, documents, summary messages) back to the simulation.

**Features:**
- Configurable max concurrent tasks (default 3)
- Configurable task timeout (default 600s)
- Configurable allowed tools per scenario
- Task IDs: `BG-` + 6-char hex hash
- Automatic result delivery: commits to GitLab, documents created, summary posted to channel
- Task status tracking: `running`, `completed`, `timed_out`, `failed`
- Enabled per scenario via `settings.enable_background_tasks`

### 6.10 Direct Messages

Private one-shot messages between agents, implemented using the `#dms` system channel with structured `[DM to <key>]` prefixes.

**Features:**
- Agents send DMs via `send_dm(recipient_key, content)`
- Recipients retrieve DMs via `get_my_dms(since_id)`
- DMs appear in the `#dms` channel with sender attribution
- Max 2 DMs per agent response (v2 prompt format)
- Used for pre-alignment, escalation, and private coordination

### 6.11 NPC Management

The web UI provides hire/fire capabilities for dynamic agent management.

**Features:**
- Fire an agent: removes from orchestrator, stops container, removes from all channels
- Hire from character templates: creates a new agent from a template with customizable name, role, and tier
- Dynamic agent addition: launches new container and adds to response tiers
- Agent config modification: change character prompt, channels, folders, tier, verbosity
- Online/offline toggle: take agents "out of office" without removing them

### 6.12 Recaps

Generated session summaries using one-shot Claude agent sessions.

**Features:**
- Multiple recap styles (configurable in UI)
- Uses the full chat history and metadata as context
- Saved as part of session state

### 6.13 Director Channels

Private back-channels for sending instructions to individual agents.

**Features:**
- Each agent automatically gets a `#director-<key>` channel
- Only visible to the specific agent
- Used for scenario direction and private instructions
- Messages from the director channel are included in the agent's turn prompt

---

## 7. Scenario System

### 7.1 Scenario YAML Structure

Each scenario is defined by a `scenario.yaml` file in `scenarios/<name>/`:

```yaml
name: "Tech Startup"
description: "An 11-person engineering organization at a tech startup"

characters:
  pm:
    display_name: "Sarah (PM)"
    character_file: "characters/sarah-pm.CS.md"
    team_description: "product requirements, prioritization, scope"
    max_words: 200    # Optional per-agent word limit

channels:
  "#general":
    description: "Company-wide discussion"
    is_external: false
  "#sales-external":
    description: "Customer-facing sales channel"
    is_external: true

memberships:
  pm: ["#general", "#engineering", "#sales", "#leadership"]
  senior: ["#general", "#engineering"]

response_tiers:
  1: ["senior", "support", "sales", "devops"]
  2: ["engmgr", "architect", "pm", "marketing", "projmgr"]
  3: ["ceo", "cfo"]

folders:
  shared:
    type: "shared"
    description: "Shared team documents"
  engineering:
    type: "department"
    description: "Engineering department"

folder_access:
  shared: ["pm", "engmgr", "architect", "senior", ...]
  engineering: ["pm", "engmgr", "architect", "senior", "devops"]

events:
  - name: "Production Outage"
    severity: "critical"
    actions:
      - type: "message"
        channel: "#general"
        sender: "PagerDuty (System)"
        content: "ALERT: Production is DOWN..."

settings:
  enable_background_tasks: true
  max_concurrent_tasks: 3
  task_timeout: 600
  enable_memos: true
  enable_blog: true
  task_allowed_tools:
    - Bash
    - Read
    - Write
    - Edit
    - WebFetch
    - WebSearch
```

### 7.2 Character Files (.CS.md Format)

Character definitions use NRSP-format markdown files with optional YAML frontmatter:

```markdown
---
type: character-sheet
version: 1.0
---

# Character Name

## Backstory
Character backstory and context...

## Motivations
What drives this character...

## Relationships
Key relationships with other team members...

## Prompt
Behavioral guidelines and simulation directives.
This section contains the actual system prompt instructions.
```

**Processing rules:**
- YAML frontmatter (if present) is stripped before processing
- If a `## Prompt` section exists: context sections (backstory, motivations, relationships) are prepended, and the Prompt section provides behavioral guidelines
- If no `## Prompt` section: the entire file body is used as the prompt (backward compatible with legacy format)

### 7.3 Scenario Loading (`lib/scenario_loader.py`)

When a scenario is loaded, `load_scenario()` populates module-level config dicts in place:
- `personas.PERSONAS` — character registry
- `personas.DEFAULT_CHANNELS` — channel definitions
- `personas.DEFAULT_MEMBERSHIPS` — channel memberships
- `personas.RESPONSE_TIERS` / `personas.PERSONA_TIER` — tier assignments
- `docs.DEFAULT_FOLDERS` — folder definitions
- `docs.DEFAULT_FOLDER_ACCESS` — folder access control
- `gitlab.DEFAULT_REPO_ACCESS` — repo access control (optional)
- `events.SCENARIO_EVENTS` — event pool

Mutating dicts in place ensures all existing references throughout the codebase remain valid.

### 7.4 Available Scenarios

Scenarios are stored under `scenarios/`:
- `tech-startup/` — Default: 11-person engineering org
- `dotcom-2000/` — Y2K startup scenario
- `dnd-campaign/` — Dungeons & Dragons campaign
- `company-simulator-team/` — Internal team scenario
- `character-templates/` — Reusable role templates for hire

---

## 8. Prompt Architecture

### 8.1 V3 System Prompt (`build_v3_system_prompt`)

The system prompt is generated once per agent and mounted into the container as `/home/agent/system-prompt.md`. It is passed to every `podman exec claude` invocation via `--system-prompt-file`.

**Contents (in order):**
1. **Character instructions** — loaded from the character's `.CS.md` file
2. **Identity statement** — "You are {display_name}..."
3. **Channel listing** — all channels with descriptions, membership tags, and member lists (internal/external separation)
4. **Team listing** — all active personas with display names, keys, and role descriptions
5. **External participants** — instructions for handling customers, investors, regulators, competitors, press, hackers, etc.
6. **Communication rules** — channels-only communication, no speaking for others, stay in lane
7. **Compressed time rules** — 1 day = 2 minutes, act now not later, banned deferral language
8. **MCP tools preamble** — lists all available tool categories and instructs agent to use MCP tools exclusively
9. **Folder listing** — agent's accessible document folders

**Key behavioral rules embedded in system prompt:**
- All communication through chat channels and MCP tools only
- Do not speak for other team members
- Stay within expertise domain
- Compressed time: act immediately, no deferral
- Start each turn by reading messages, end by calling `signal_done()`
- Do not output JSON commands (use MCP tools instead)

### 8.2 V3 Turn Prompt (`build_v3_turn_prompt`)

The turn prompt is passed as the `-p` argument to each `podman exec claude` invocation. It is intentionally lightweight (~200-400 bytes) because v3 agents fetch their own state via MCP tools.

**Contents:**
```
There is new activity in #engineering, #general.

- Customer: Can you walk us through your platform capabilities?
- PagerDuty (System): ALERT: Production is DOWN.

You are Alex (Senior Eng). Your expertise is in implementation details, edge cases, testing.

Use get_messages() to read the recent messages. Respond if appropriate for your role.
Use any other tools (create_doc, create_ticket, etc.) if the situation calls for it.
If you have nothing meaningful to add, call signal_done() and exit.
You may have pending DMs — use get_my_dms() to check.
```

**V3 vs V2 prompt comparison:**
- V2 turn prompts embed full chat history, document indices, ticket queues, and channel membership (~5K-50K+ bytes)
- V3 turn prompts are ~200-400 bytes — agents use MCP tools to fetch their own context
- This reduces prompt size dramatically and lets agents decide what context they need

### 8.3 V2 Prompts (Legacy)

The v2 prompt system (`build_initial_prompt` / `build_turn_prompt`) is still present in `lib/personas.py` for the legacy SDK-based orchestrator. V2 prompts use a structured JSON response format where agents emit `{"action": "respond", "messages": [...], "commands": [...]}` objects. The orchestrator then parses and executes the commands.

V3 replaced this with direct MCP tool calls, eliminating the need for JSON response parsing.

---

## 9. Session Management

### 9.1 Operations

| Operation | Description |
|-----------|-------------|
| **New** | Clears all runtime state, reloads scenario, copies seed data |
| **Save** | Snapshots all state to `var/instances/<name>/` |
| **Load** | Restores state from a saved instance directory |
| **Autosave** | Automatic save after each response cycle (fixed slot) |

### 9.2 State Captured in Session Snapshots

A session save captures the complete simulation state:

| Data | File | Source |
|------|------|--------|
| Chat messages | `chat.log` | `var/chat.log` |
| Documents | `docs/` | `var/docs/` |
| GitLab repos | `gitlab/` | `var/gitlab/` |
| Tickets | `tickets/` | `var/tickets/` |
| Agent logs | `logs/` | `var/logs/` |
| Character files | `characters/` | `var/characters/` |
| Channel memberships | `memberships.json` | In-memory webapp state |
| Agent thoughts | `thoughts.json` | In-memory webapp state |
| Agent roster | `roster.json` | PERSONAS + config (hire/fire changes) |
| DM queue | `dm_queue.json` | In-memory orchestrator state |
| Event pool | `event_pool.json` | In-memory events state |
| Event log | `event_log.json` | In-memory events state |
| Background tasks | `background_tasks.json` | TaskExecutor state |
| Emails | `emails.json` | In-memory email state |
| Memos | `memos.json` | In-memory memos state |
| Blog | `blog.json` | In-memory blog state |
| Recaps | `recaps.json` | In-memory webapp state |
| Metadata | `metadata.json` | Scenario name, timestamps |

### 9.3 Instance Naming

- Manual save: `{scenario}--{date}--{slug}` (e.g., `tech-startup--2026-04-11-1430--demo-session`)
- Autosave: `{scenario}--autosave` (overwrites same slot each time)

### 9.4 Roster Persistence

The roster captures the full agent configuration state, enabling hire/fire changes to survive session save/load. Each agent's entry includes:
- Display name, team description, character file path
- Channel memberships
- Folder and repo access
- Response tier
- Verbosity setting

On load, the roster completely rebuilds `PERSONAS`, `DEFAULT_MEMBERSHIPS`, `PERSONA_TIER`, `RESPONSE_TIERS`, `DEFAULT_FOLDER_ACCESS`, and `DEFAULT_REPO_ACCESS`.

---

## 10. Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Language | Python 3.13+ | Core runtime |
| Web server | Flask >=3.0 | REST API, SSE, web UI |
| MCP server | Starlette + FastMCP (mcp >=1.0) | Per-agent MCP-over-SSE endpoints |
| ASGI server | uvicorn >=0.30 | Serves the Starlette MCP app |
| Agent SDK | claude-agent-sdk 0.1.48 | Background task workers, one-shot agents |
| Container runtime | podman | Agent container lifecycle |
| Agent CLI | claude (Claude Code CLI) | Runs inside each container |
| HTTP client (async) | httpx >=0.27 | MCP server → Flask API proxy |
| HTTP client (sync) | requests >=2.32 | Orchestrator → Flask API, ChatClient |
| Config | PyYAML >=6.0 | Scenario YAML parsing |
| Environment | python-dotenv >=1.1 | Credential management (.env files) |
| Real-time updates | Server-Sent Events | Browser UI updates |
| AI models | Claude Sonnet 4.5, Claude Opus 4.6, Claude Haiku 3.5 | Agent intelligence |
| AI backend | Google Vertex AI | Model hosting (via GCP credentials) |

### 10.1 Runtime Data Layout

```
var/                        # All runtime state (gitignored)
├── chat.log                # Newline-delimited JSON message log
├── docs/                   # Document storage
│   ├── shared/             # Folder directories
│   │   ├── _meta.json      # Folder metadata index
│   │   └── doc-slug.md     # Document content
│   └── engineering/
├── gitlab/                 # Git repository storage
│   ├── _repos_index.json   # Repository metadata
│   └── repo-name/          # Repository directory
│       ├── _commits.json   # Commit log
│       └── src/            # Repository files
├── tickets/                # Ticket storage
│   ├── _tickets_index.json # Ticket metadata
│   └── TK-ABC123.json      # Individual ticket
├── logs/                   # Agent session logs + MCP audit log
│   ├── Agent_Name.log      # Per-agent execution log
│   └── mcp_audit.log       # MCP tool call audit trail
├── tmp/                    # Temporary files (MCP configs, prompts)
│   ├── mcp-config-pm.json  # Per-agent MCP config
│   └── system-prompt-pm.md # Per-agent system prompt
└── instances/              # Saved session snapshots
    ├── tech-startup--autosave/
    └── tech-startup--2026-04-11-1430--demo/
```

---

## 11. REST API Summary

The Flask server exposes ~80 REST endpoints organized by subsystem:

| Category | Endpoints | Description |
|----------|-----------|-------------|
| Channels | `GET /api/channels`, `POST /api/channels/<name>/join`, `POST /api/channels/<name>/leave` | Channel listing and membership |
| Messages | `GET /api/messages`, `POST /api/messages`, `POST /api/messages/clear`, `GET /api/messages/stream` | Chat and SSE |
| Documents | `GET/POST /api/docs`, `GET/PUT/DELETE /api/docs/<folder>/<slug>`, `POST /api/docs/<folder>/<slug>/append`, `GET /api/docs/search` | Document CRUD |
| GitLab | `GET/POST /api/gitlab/repos`, `GET /api/gitlab/repos/<project>/tree`, `GET /api/gitlab/repos/<project>/file`, `POST /api/gitlab/repos/<project>/commit`, `GET /api/gitlab/repos/<project>/log` | Repository operations |
| Tickets | `GET/POST /api/tickets`, `GET/PUT /api/tickets/<id>`, `POST /api/tickets/<id>/comment`, `POST /api/tickets/<id>/depends` | Ticket management |
| Memos | `GET/POST /api/memos/threads`, `GET/DELETE /api/memos/threads/<id>`, `GET/POST /api/memos/threads/<id>/posts` | Discussion board |
| Blog | `GET/POST /api/blog/posts`, `GET/PUT/DELETE /api/blog/posts/<slug>`, `GET/POST /api/blog/posts/<slug>/replies` | Blog management |
| Email | `GET/POST /api/emails`, `GET /api/emails/<id>` | Corporate email |
| Events | `GET/POST /api/events/pool`, `PUT/DELETE /api/events/pool/<index>`, `POST /api/events/trigger`, `GET /api/events/log` | Event injection |
| NPCs | `GET /api/npcs`, `POST /api/npcs/<key>/toggle`, `POST /api/npcs/<key>/fire`, `POST /api/npcs/hire`, `PUT /api/npcs/<key>/config`, `GET/POST /api/npcs/<key>/thoughts` | Agent management |
| Session | `GET /api/session/current`, `GET /api/session/list`, `GET /api/session/scenarios`, `POST /api/session/save`, `POST /api/session/load`, `POST /api/session/new` | Session lifecycle |
| Orchestrator | `POST /api/orchestrator/heartbeat`, `POST /api/orchestrator/command` | Orchestrator control |
| Status | `GET /api/status`, `GET /api/usage`, `POST /api/typing`, `GET /api/recaps`, `POST /api/recap` | Monitoring |
| Templates | `GET /api/templates`, `GET /api/templates/<key>` | Character templates for hire |

---

## 12. Communication Flow: End-to-End Example

Here is a complete trace of what happens when a human types "We need to add rate limiting to the API" in #engineering:

1. **Human posts message** → Browser sends `POST /api/messages` to Flask server
2. **Flask stores message** → Appended to in-memory list and `var/chat.log`, SSE broadcast to all browsers
3. **Orchestrator polls** → `GET /api/messages?since=<last_id>` detects new human message in `#engineering`
4. **Channel membership lookup** → Orchestrator fetches channel memberships from Flask to determine which agents are in `#engineering`
5. **Tier grouping** → Agents in `#engineering` grouped by tier: Tier 1 (senior, devops), Tier 2 (engmgr, architect, pm, projmgr)
6. **Tier 1 launch** → `podman exec agent-senior claude -p "There is new activity in #engineering..."` and `podman exec agent-devops claude -p "..."` run concurrently
7. **Agent reads context** → Inside container, claude calls `mcp__sim__get_messages(channel="#engineering")` via MCP-over-SSE → MCP server proxies to Flask `GET /api/messages?channels=#engineering` → returns messages
8. **Agent responds** → Claude calls `mcp__sim__post_message(channel="#engineering", content="I can implement rate limiting using...")` → MCP server proxies to Flask `POST /api/messages` → message stored, SSE broadcast
9. **Agent signals done** → Claude calls `mcp__sim__signal_done(summary="Responded with rate limiting proposal")` → MCP server records done event
10. **Orchestrator detects done** → Polls `GET /api/agents/done-events?since_id=<cursor>`, sees both Tier 1 agents done
11. **Tier 2 launch** → Same process for engmgr, architect, pm, projmgr — they can now read Tier 1's responses via `get_messages()`
12. **Activity detection** → After all tiers complete, orchestrator checks if agents posted to any new channels. If architect posted to `#devops`, that triggers a Wave 2 with `#devops` as the trigger channel
13. **Quiesce** → When no new channels are triggered, the loop ends
14. **Autosave** → Orchestrator calls `save_session("autosave")`

---

## 13. Configuration Reference

### 13.1 CLI Arguments

**Server:**
```
--port 5000          # HTTP port
--host 127.0.0.1     # Bind address
--scenario NAME      # Auto-load scenario at startup
```

**Orchestrator (chat):**
```
--scenario NAME           # Scenario to load
--model sonnet|opus|haiku # Claude model (default: sonnet)
--personas pm,senior,...  # Comma-separated persona filter
--max-rounds 5            # Max ripple waves per response cycle
--max-auto-rounds 0       # Max autonomous continuation rounds (0=unlimited)
--poll-interval 5.0       # Seconds between message polls
--server-url URL          # Flask server URL
--mcp-port 5001           # MCP server port
--mcp-host auto           # MCP host for containers (auto-detect)
--container-image NAME    # Container image (default: agent-image:latest)
--container-timeout 300   # Max seconds per agent turn
--max-turns 50            # Max Claude turns per exec
--max-concurrent 4        # Max concurrent agents per tier
--done-timeout 120        # Max wait for signal_done before advancing tier
```

**MCP Server:**
```
--port 5001              # MCP server port
--host 0.0.0.0           # Bind address
--flask-url URL          # Flask server URL to proxy to
--scenario NAME          # Scenario to pre-load (optional)
```

### 13.2 Environment Variables

```bash
CLAUDE_CODE_USE_VERTEX=1                    # Required: use Vertex AI
CLOUD_ML_REGION=us-east5                    # Required: GCP region
ANTHROPIC_VERTEX_PROJECT_ID=<project-id>    # Required: GCP project
```

Requires GCP application default credentials (`gcloud auth application-default login`).

### 13.3 Model Mapping

| Shorthand | Model ID | Display Name |
|-----------|----------|--------------|
| `sonnet` | `claude-sonnet-4-5` | Claude Sonnet 4.5 |
| `opus` | `claude-opus-4-6` | Claude Opus 4.6 |
| `haiku` | `claude-haiku-3-5` | Claude Haiku 3.5 |
