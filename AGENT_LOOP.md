# Agent Loop Architecture

## System Overview

The system is a multi-agent orchestration platform where 11 AI personas
(powered by Claude SDK via Vertex AI) collaborate through a Slack-like chat
interface. Two processes run independently:

- **Flask webapp** (`lib/webapp.py`) — REST API + SSE streaming + embedded web UI
- **Orchestrator** (`lib/orchestrator.py`) — async polling loop that drives agent responses

They communicate entirely through HTTP: the orchestrator is a client of
the webapp's REST API (via `lib/chat_client.py`).

### Launch

```bash
# Terminal 1 — Flask server
python main.py server [--port 5000] [--host 127.0.0.1]

# Terminal 2 — Agent orchestrator
python main.py chat [--personas pm,senior,architect] \
                    [--model sonnet|opus|haiku] \
                    [--max-rounds 5] \
                    [--max-auto-rounds 0] \
                    [--poll-interval 5.0] \
                    [--server-url http://127.0.0.1:5000]
```

Requires a `.env` file with Vertex AI credentials (`CLAUDE_CODE_USE_VERTEX=1`,
`CLOUD_ML_REGION`, `ANTHROPIC_VERTEX_PROJECT_ID`).

---

## File Map

```
main.py                      Entry point (server / chat subcommands)
lib/
  cli.py                     CLI argument parser
  webapp.py                  Flask server: REST API, SSE, embedded web UI
  orchestrator.py            Event loop, command execution, tiered agent dispatch
  agent_runner.py            Claude SDK session pool (persistent per persona)
  chat_client.py             HTTP client wrapping the webapp REST API
  personas.py                Persona registry, prompt builders
  response_schema.py         JSON response parser + command normalizer
  docs.py                    Document storage + folder access control
  gitlab.py                  Mock GitLab repo/commit storage
  tickets.py                 Ticket storage + ID generation
.claude/skills/
  <persona>/SKILL.md         Role instructions per persona (11 files)
docs/                        Runtime document storage (folders + _index.json)
gitlab/                      Runtime repo storage (files + _commits.json + _repos_index.json)
tickets/                     Runtime ticket storage (_tickets_index.json)
logs/                        Agent session logs (one file per persona)
```

---

## Personas

| Key | Display Name | Tier | Default Channels |
|-----|-------------|------|------------------|
| `senior` | Alex (Senior Eng) | 1 | #general, #engineering |
| `support` | Jordan (Support Eng) | 1 | #general, #engineering, #support, #support-external |
| `sales` | Taylor (Sales Eng) | 1 | #general, #sales, #sales-external, #marketing |
| `devops` | Casey (DevOps) | 1 | #general, #devops, #engineering, #support |
| `engmgr` | Marcus (Eng Manager) | 2 | #general, #engineering, #support, #devops |
| `architect` | Priya (Architect) | 2 | #general, #engineering |
| `pm` | Sarah (PM) | 2 | #general, #engineering, #sales, #support, #leadership, #marketing, #devops |
| `marketing` | Riley (Marketing) | 2 | #general, #marketing, #sales, #sales-external |
| `projmgr` | Nadia (Project Mgr) | 2 | #general, #engineering, #support, #leadership, #devops, #sales, #marketing |
| `ceo` | Dana (CEO) | 3 | #general, #leadership, #sales, #marketing |
| `cfo` | Morgan (CFO) | 3 | #general, #leadership, #sales |

---

## Core Loop (`run_orchestrator`)

The orchestrator polls `GET /api/messages?since=<last_id>` on a configurable
interval (default 5s). When it detects new human messages (filtered by
checking sender against the set of known agent display names), it identifies
which channels were touched and kicks off `_run_loop()`.

---

## Wave-Based Tiered Execution (`_run_loop`)

Within each trigger, agents respond in waves. Each wave runs agents through
3 organizational tiers sequentially:

| Tier | Agents | Role |
|------|--------|------|
| 1 | Senior Eng, Support, Sales, DevOps | ICs — execute first |
| 2 | PM, Eng Manager, Architect, Marketing, Project Mgr | Managers — synthesize |
| 3 | CEO, CFO | Executives — strategic only |

Agents within a tier also run sequentially, so each agent sees the previous
agent's response before deciding to contribute or pass. This ordering is
deliberate — it lets higher tiers react to IC-level work rather than
speaking first.

If an agent posts to a channel that wasn't in the original trigger set,
that channel becomes a trigger for the next wave, creating a ripple effect.
Waves continue up to `max_waves` (default 5).

---

## Autonomous Continuation

After the initial wave sequence completes, the orchestrator enters an
autonomous loop: if any channels received agent posts, it re-runs
`_run_loop` on those channels. This repeats until:

- All agents pass (quiescence)
- A new human message is detected (1-second check between rounds)
- `max_auto_rounds` is reached (0 = unlimited)

---

## Agent Sessions (`AgentPool` in `lib/agent_runner.py`)

Each persona gets a persistent Claude SDK session opened at startup.
Sessions receive an initial prompt with full role instructions, then are
reused for every turn via `pool.send(persona_key, prompt)`. This avoids
cold-start overhead per turn. If a session errors out, it's closed and
removed from the pool.

The SDK client is configured with `allowed_tools=["Read"]` and
`permission_mode="bypassPermissions"` — agents can read files but not
execute code.

Response text extraction handles both `AssistantMessage` (text blocks)
and `ResultMessage` (including `structured_output` for future JSON mode
support).

---

## Prompt Construction (`lib/personas.py`)

**Initial prompt** (sent once per session): persona-specific instructions
from `.claude/skills/{skill}/SKILL.md`, channel listing with memberships,
team roster, communication rules, compressed-time rules, and the JSON
response format specification with command reference tables.

**Turn prompt** (sent every turn): full message history (filtered to the
agent's visible channels), current document index, repo list, ticket queue,
channel membership reminder, and a channel-specific action prompt. External
channels get a customer-facing prompt; internal channels get a "discuss
freely" prompt. Both include a JSON format reminder.

---

## Response Processing Pipeline (`_process_agent_response`)

Agent responses are processed through a two-path pipeline: JSON first,
regex fallback.

### JSON Path (primary)

The orchestrator calls `parse_json_response()` from `lib/response_schema.py`
to attempt JSON parsing. This:

1. Strips markdown code fences (`` ```json ... ``` ``) if present
2. Validates the response is a JSON object with a valid `action` field
   (`respond`, `pass`, or `ready`)
3. Returns parsed dict, or `None` to trigger the regex fallback

On successful parse, `_process_json_response()` handles execution:

1. `normalize_commands()` splits the `commands` array by `type` field into
   `(doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join)`, flattening
   each command's `action` + `params` into the dict format the existing
   `_execute_*` functions expect
2. Commands are executed via `_execute_doc_commands()`,
   `_execute_gitlab_commands()`, `_execute_tickets_commands()`
3. Channel joins are processed
4. `extract_messages()` converts the `messages` array into a
   `{channel: text}` mapping for posting

### Regex Path (fallback)

If JSON parsing fails, `_process_regex_response()` handles the response
using the original regex-based extraction, processing raw text through
6 stages:

1. Doc commands — regex extracts `<<<DOC:CREATE/UPDATE/APPEND/SEARCH/READ>>>`
   blocks, executes via ChatClient, strips from text
2. GitLab commands — `<<<GITLAB:COMMIT/REPO_CREATE/TREE/FILE_READ/LOG>>>`,
   executed and stripped
3. Ticket commands — `<<<TICKETS:CREATE/UPDATE/COMMENT/DEPENDS/LIST>>>`,
   executed and stripped
4. Channel joins — `<<<CHANNEL:JOIN #name>>>`, executed and stripped
5. PASS check — if cleaned text is "PASS" or empty, agent is skipped
6. Multi-channel split — `[#channel-name]` markers on their own line split
   the response into per-channel posts

Both paths share logging helpers (`_log_doc_results`, `_log_gitlab_results`,
`_log_tickets_results`) and the same `_execute_*` functions.

### JSON Response Schema

```json
{"action": "respond", "messages": [...], "commands": [...]}
{"action": "pass"}
{"action": "ready"}
```

**Messages** (channel-routed chat text):
```json
{"channel": "#engineering", "text": "Here's my analysis..."}
```

**Commands** (structured params):
```json
{"type": "doc",     "action": "CREATE",  "params": {"folder": "shared", "title": "...", "content": "..."}}
{"type": "gitlab",  "action": "COMMIT",  "params": {"project": "...", "message": "...", "files": [{"path": "...", "content": "..."}]}}
{"type": "tickets", "action": "CREATE",  "params": {"title": "...", "assignee": "...", "priority": "high", "description": "..."}}
{"type": "channel", "action": "JOIN",    "params": {"channel": "#engineering"}}
```

After parsing, each channel post goes through a membership check (agent
must be a member of the target channel) before being posted via
`POST /api/messages`.

---

## Channel & Membership Model

9 default channels (7 internal, 2 external/customer-facing). Each persona
has a default membership set defined in `DEFAULT_MEMBERSHIPS`. Agents can
dynamically join channels via a channel `JOIN` command. Memberships are
re-fetched from the server at the start of each wave.

| Channel | Type | Description |
|---------|------|-------------|
| #general | Internal | Company-wide discussion |
| #engineering | Internal | Engineering team |
| #sales | Internal | Sales team |
| #support | Internal | Support team |
| #leadership | Internal | Executive leadership |
| #marketing | Internal | Marketing team |
| #devops | Internal | DevOps & infrastructure |
| #sales-external | External | Customer-facing sales |
| #support-external | External | Customer-facing support |

---

## Document Workspace (`lib/docs.py`)

Documents are stored on the filesystem at `docs/{folder}/{slug}.txt` with
metadata in `docs/_index.json`. Folders have access controls defined in
`DEFAULT_FOLDER_ACCESS` — each persona can only see/create docs in folders
they have access to.

**Folder types:** shared (all personas), public (all personas, customer-visible),
department (role-restricted), personal (single persona).

---

## GitLab Repositories (`lib/gitlab.py`)

Mock code hosting at `gitlab/{repo}/files/` with commit history in
`gitlab/{repo}/_commits.json` and repo metadata in `gitlab/_repos_index.json`.
Agents can create repos, commit files, browse trees, read files, and view
commit logs.

---

## Tickets (`lib/tickets.py`)

Lightweight task tracking at `tickets/_tickets_index.json`. Tickets have
ID (format `TK-XXXXXX`), title, description, status
(`open`/`in_progress`/`resolved`/`closed`), priority
(`low`/`medium`/`high`/`critical`), assignee, comments, and dependency
tracking (`blocked_by`/`blocks`).

---

## Webapp (`lib/webapp.py`)

Flask server with 35+ REST endpoints covering messages, channels, folders,
documents, GitLab repos, and tickets. All state is in-memory protected by
per-domain thread locks, with filesystem persistence (JSON files).

**SSE streaming:** Clients connect to `GET /api/messages/stream` and receive
real-time events for messages, channel updates, doc changes, GitLab events,
and ticket changes. Each subscriber gets a `queue.Queue(maxsize=256)` —
overflow silently drops events. Keepalive sent every 30s.

**Embedded web UI:** Single-page app served at `/` with dark theme, tabbed
channel navigation, document viewer, and ticket board.

---

## Data Flow

```
Human posts in UI -> POST /api/messages -> stored + SSE broadcast
                                              |
Orchestrator polls -> detects human message -> identifies trigger channels
                                              |
_run_loop: fetch memberships -> group agents by tier -> run sequentially
                                              |
Per agent: fetch history -> build_turn_prompt -> pool.send -> SDK query
                                              |
Response: parse JSON (or regex fallback) -> execute commands -> post messages
                                              |
New channels triggered? -> next wave. All pass? -> autonomous round or quiesce.
```

---

## Persistence

| Data | Storage | Format |
|------|---------|--------|
| Messages | In-memory + `chat.log` | JSON lines (append-only) |
| Documents | `docs/{folder}/{slug}.txt` + `docs/_index.json` | Text files + JSON index |
| Repos | `gitlab/{repo}/files/` + `_commits.json` | File tree + JSON |
| Tickets | `tickets/_tickets_index.json` | JSON |

All state is in-memory at runtime with JSON files as durable backup.
No database.

---

## Concurrency Model

- **Orchestrator:** single asyncio event loop, sequential agent execution
  within tiers
- **Webapp:** Flask dev server with threading, per-domain reentrant locks
  (messages, channels, docs, folders, gitlab, tickets, subscribers)
- **SSE:** per-client `queue.Queue(maxsize=256)` — subscribers silently
  dropped on overflow
- **HTTP calls** from orchestrator use synchronous `requests` library

---

## Key Architectural Tradeoffs

- **Sequential within tiers (not parallel)** — ensures each agent sees prior
  responses, but increases latency linearly with agent count

- **Full history per turn** — agents get complete context but prompt size
  grows unboundedly

- **In-memory state** — simple and fast, but not horizontally scalable and
  messages grow without bound unless manually cleared

- **Polling** — orchestrator polls the webapp rather than subscribing to SSE,
  adding up to `poll_interval` seconds of latency per human message

- **JSON-first with regex fallback** — agents are prompted to respond in
  structured JSON, but the regex parser remains as a safety net during
  migration, adding code surface but ensuring robustness
