# Agent Loop Architecture v3: MCP-Driven Autonomous Agents

**Status:** Proposal / RFC
**Date:** 2026-04-09
**Authors:** jtanner, claude

## Summary

Replace the current orchestrator-driven parse-and-execute agent loop with autonomous long-running Claude Code processes that interact with the simulation world directly via MCP tools. The orchestrator simplifies from a dispatch/parse/execute engine to a lightweight notification bus. Agents stop producing structured JSON responses and instead take action by calling tools — the same way Claude Code natively operates.

## Motivation

### Problems with the Current Architecture (v2)

**1. Claude Agent SDK fragility**

The SDK maintains stateful sessions that are problematic in multi-agent scenarios. `CancelledError` propagation, session state corruption, and one agent's crash affecting the pool are recurring issues. `agent_runner.py` has defensive code for this (close session on any exception, no auto-restart), but the fundamental problem is managing N concurrent stateful SDK sessions in a single process.

In other projects using the same stack, we've found that `subprocess.run("claude -p ...")` is more reliable than the SDK for subagent/task execution due to process isolation.

**2. Structured JSON response parsing is fragile**

Agents must respond with `{"action": "respond", "messages": [...], "commands": [...]}`. When they don't (and they frequently don't), we fall back to regex parsing of legacy `<<<>>>` command blocks. `response_schema.py` has multiple fallback strategies: strip markdown fences, extract first-`{`-to-last-`}`, merge multiple fenced blocks, JSON repair for unescaped quotes. This is inherently brittle and fights against how LLMs naturally want to operate.

**3. The orchestrator does too much**

The orchestrator currently: polls for messages, builds turn prompts, dispatches agents, parses responses, executes commands (docs, gitlab, tickets, DMs, tasks, memos, blog, channels), posts messages on the agent's behalf, tracks channel ripple effects, and manages wave ordering. This coupling makes the system hard to extend — adding a new subsystem requires changes to `response_schema.py` (parsing), `orchestrator.py` (execution), `personas.py` (prompt injection), and `session.py` (persistence).

**4. No agent isolation — agents can escape their environment**

Agents running via the Claude Agent SDK (or Claude Code with Bash access) operate on the host filesystem. Nothing prevents an agent from running `ls ../`, reading `var/logs/` to see other agents' thoughts, writing directly to `var/docs/` to bypass folder access controls, or manipulating the host process. The structured command protocol (`doc`, `gitlab`, `tickets`) is an honor system — agents are *asked* to use commands, but they have full Bash access and can sidestep them entirely. In practice, agents have been observed writing to `./shared/` or `./var/docs/` directly instead of using the docs API, breaking the access control model.

**5. Agents can't do multi-step workflows**

In v2, each agent gets one turn per wave: receive prompt, produce response, done. If an agent wants to read a document, analyze it, then create a new document based on it — that's three operations that must be crammed into a single JSON response. Agents can't observe the results of their actions and adapt within a turn.

### What Works Well (Keep)

- **Flask server as shared state backend** — REST API, SSE broadcast, in-memory state with disk persistence
- **Scenario system** — YAML-driven personas, channels, folders, events
- **Character files (.CS.md)** — NRSP format for persona definition
- **Subsystem modules** — docs, gitlab, tickets, memos, email, blog all have clean REST APIs
- **Session save/load** — full state snapshots
- **Web UI** — real-time observation and human interaction

## Proposed Architecture (v3)

### Core Idea

Each agent is a **long-running Claude Code process** (`claude -p` or interactive mode) with **MCP tools** that map to the Flask server's REST API. Agents interact with the world directly — posting messages, creating documents, committing code — by calling tools, not by producing structured output for the orchestrator to parse and execute.

The orchestrator becomes a **notification bus**: it detects new activity (human messages, events) and notifies relevant agents. Agents wake up, assess the situation via API calls, decide what to do, and take action.

### Component Overview

```
┌──────────────────────────────────────────────────────────────┐
│  Host                                                        │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Flask Server (lib/webapp.py)                          │  │
│  │  REST API, SSE, Web UI, in-memory state                │  │
│  │  Port 5000: Web UI + REST (human / web browser)        │  │
│  └──────────────┬────────────────────────────────────────┘  │
│                 │ localhost (internal calls)                  │
│  ┌──────────────┴────────────────────────────────────────┐  │
│  │  MCP Server (lib/mcp_server.py) — async (FastAPI)     │  │
│  │  Port 5001: MCP-over-SSE endpoint (agent containers)  │  │
│  │    - Authenticates agents by AGENT_PERSONA_KEY        │  │
│  │    - Enforces per-agent access control                │  │
│  │    - Translates MCP tool calls → Flask REST API       │  │
│  │    - Audit logs all tool calls                        │  │
│  └──────────────┬──────────────────┬─────────────────────┘  │
│                 │                  │                          │
│  ┌──────────────┴──────────────┐   │                         │
│  │  Notification Bus           │   │                         │
│  │  (lib/notifier.py)          │   │                         │
│  │  - polls Flask for activity │   │                         │
│  │  - notifies agents via      │   │                         │
│  │    docker exec stdin        │   │                         │
│  │  - manages container        │   │                         │
│  │    lifecycle                │   │                         │
│  └──────┬──────────────────────┘   │                         │
│         │                          │                          │
└─────────┼──────────────────────────┼──────────────────────────┘
          │ docker exec              │ MCP-over-SSE (HTTP)
          │ (stdin injection)        │ (tool calls + responses)
          │                          │
   ┌ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┼─ ─ ─ ─ ─ agent-net ─ ┐
          │                          │
   │ ┌────▼────┐  ┌────────────┐  ┌─▼──────────┐            │
     │container│  │ container  │  │ container  │
   │ │Agent 1  │  │ Agent 2    │  │ Agent N    │             │
     │(claude) │  │ (claude)   │  │ (claude)   │
   │ │         │  │            │  │            │             │
     │ MCP ────┼──┼── client ──┼──┼──► :5001   │
   │ │ client  │  │            │  │            │             │
     │         │  │            │  │            │
   │ │scratch/ │  │ scratch/   │  │ scratch/   │             │
     │(tmpfs)  │  │ (tmpfs)    │  │ (tmpfs)    │
   │ └─────────┘  └────────────┘  └────────────┘             │
     no host fs    no host fs      no host fs
   │ no other      no other        no other                  │
     containers    containers      containers
   └ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┘
```

**Key architectural decisions:**

**1. MCP server runs on the host, not inside containers.**

Agents connect to it over the container network via HTTP/SSE. This means:

- **No tool code inside containers** — agents cannot read, modify, or replace tool implementations
- **All access control enforced server-side** — the agent sends `create_doc(folder="synthesis", ...)`, the server checks if that persona has access before executing
- **Single audit point** — every tool call passes through the MCP server, logged with agent identity
- **Agents are pure consumers** — they have an MCP client config pointing to `http://mcp-server:5001` and nothing else

If tool scripts lived inside the containers, an agent could inspect them, understand the API contract, and call the Flask REST API directly — bypassing MCP entirely. Keeping tools server-side means the agent's only interface is the MCP protocol, and the server decides what's allowed.

**2. MCP server is a separate async process (FastAPI/Starlette), not embedded in Flask.**

Flask is synchronous (WSGI). The MCP server needs to handle concurrent SSE connections from multiple agent containers and async tool call processing. Rather than fighting Flask's threading model, the MCP server runs as a lightweight async process (FastAPI or raw Starlette) on a separate port. It proxies tool calls to Flask's REST API over localhost.

This gives us:
- **Flask unchanged** — all existing endpoints, web UI, SSE, in-memory state management stay as-is. No migration risk.
- **Async MCP handling** — native `async/await` for concurrent agent connections, SSE streams, and tool call processing
- **Clean separation** — Flask owns state and the human-facing interface (port 5000). MCP server owns the agent-facing protocol (port 5001). They communicate over localhost HTTP.
- **Independent scaling** — if Flask ever becomes a bottleneck, it can be replaced or scaled independently without touching the MCP server

The MCP server is small — it's a translation layer, not a state store. Each tool handler is roughly: authenticate agent → check permissions → call Flask REST API → return result. FastAPI with Pydantic models for tool schemas makes this straightforward.

**State consistency: no shared database needed.** Flask is the single source of truth for all simulation state (messages, docs, repos, tickets, etc.). The MCP server holds zero simulation state — every tool call is a stateless proxy to Flask's REST API. The only data the MCP server loads is the scenario config (`scenario.yaml`) at startup for authentication and access control rules, which are read-only for the duration of a session. If the MCP server crashes and restarts, it reloads the config and is immediately operational — nothing to recover or synchronize.

```
Tool call flow:

  Agent container                MCP Server (:5001)           Flask (:5000)
       │                              │                            │
       │ create_doc(folder, title,    │                            │
       │            content)          │                            │
       ├─────────────────────────────►│                            │
       │                              │ 1. Verify AGENT_PERSONA_KEY│
       │                              │ 2. Check folder_access     │
       │                              │    (from scenario.yaml)    │
       │                              │ 3. Log to audit trail      │
       │                              │                            │
       │                              │ POST /api/docs             │
       │                              │ {title, folder, content,   │
       │                              │  author=persona_key}       │
       │                              ├───────────────────────────►│
       │                              │                            │ create in memory
       │                              │                            │ persist to var/
       │                              │         200 OK {slug, ...} │
       │                              │◄───────────────────────────┤
       │     {success, slug}          │                            │
       │◄─────────────────────────────┤                            │
       │                              │                            │
```

This is the same pattern as the v2 orchestrator's `ChatClient` — a REST client that calls Flask endpoints. The MCP server just adds an MCP protocol frontend and a permission enforcement layer in front of the same API.

**Why not rewrite Flask as FastAPI?** `lib/webapp.py` is the largest file in the project with dozens of endpoints, SSE management, in-memory state, and thread-safe locking. Rewriting it is high-risk, high-effort, and unnecessary — Flask handles its current job (human-facing web server) fine. The new requirement (agent-facing MCP protocol) is better served by a new, purpose-built async component.

### MCP Tool Surface

All tools are implemented server-side in `lib/mcp_server.py`. No tool code exists inside agent containers — agents only have an MCP client configuration pointing to the host endpoint. The MCP server translates tool calls into internal Flask REST API calls, enforcing authentication and access control on every request.

**Communication:**
| Tool | Description |
|------|-------------|
| `post_message(channel, text)` | Post a message to a channel |
| `get_messages(channel?, since_id?, limit?)` | Read recent messages, optionally filtered |
| `send_dm(to, text)` | Send a direct message to another agent |
| `get_my_dms()` | Check for pending direct messages |
| `join_channel(channel)` | Join a new channel |
| `get_channel_members(channel)` | See who's in a channel |

**Documents:**
| Tool | Description |
|------|-------------|
| `create_doc(title, folder, content)` | Create a new document |
| `update_doc(slug, content)` | Update an existing document |
| `read_doc(slug)` | Read a document's content |
| `search_docs(query, folders?)` | Search across documents |
| `list_docs(folder?)` | List documents in a folder |

**GitLab:**
| Tool | Description |
|------|-------------|
| `create_repo(name, description)` | Create a new repository |
| `commit_files(repo, message, files)` | Commit files to a repo |
| `read_file(repo, path, ref?)` | Read a file from a repo |
| `list_repo_tree(repo, path?)` | List files in a repo |
| `get_repo_log(repo, limit?)` | View commit history |

**Tickets:**
| Tool | Description |
|------|-------------|
| `create_ticket(title, description, priority, assignee?)` | Create a ticket |
| `update_ticket(id, status?, assignee?, priority?)` | Update a ticket |
| `comment_on_ticket(id, text)` | Add a comment |
| `list_tickets(status?, assignee?)` | List tickets |

**Memos & Blog:**
| Tool | Description |
|------|-------------|
| `create_memo(title, text)` | Start a discussion thread |
| `reply_to_memo(memo_id, text)` | Reply to a thread |
| `create_blog_post(title, body, tags?, is_external?)` | Publish a blog post |
| `reply_to_blog(slug, text)` | Comment on a blog post |

**Email:**
| Tool | Description |
|------|-------------|
| `send_email(subject, body)` | Send company-wide email |
| `get_emails(since?)` | Read recent emails |

**Research / External:**
| Tool | Description |
|------|-------------|
| `web_search(query)` | Search the web |
| `web_fetch(url, prompt?)` | Fetch and analyze a URL |

**Background Work:**
| Tool | Description |
|------|-------------|
| `request_background_task(goal, context?, report_to?)` | Request a background container to perform long-running work (web research, code generation, etc.). Fire-and-forget — results published to simulation via docs/commits/messages. |
| `list_background_tasks()` | Check status of previously requested background tasks |

**Meta / Situational Awareness:**
| Tool | Description |
|------|-------------|
| `get_my_channels()` | List channels I'm a member of |
| `get_my_tickets()` | List tickets assigned to me |
| `get_recent_activity(minutes?)` | Summary of recent activity across channels |
| `whoami()` | Return my persona key, role, team, accessible folders |
| `who_is(persona_key)` | Look up another agent's role and status |
| `signal_done()` | Signal that I've finished responding to the current notification |

### Container Isolation

Each agent runs in its own container. The container is the enforcement boundary — MCP tools are the agent's **only** interface to the simulation world. Agents cannot bypass the API by writing to the host filesystem.

**What the container provides:**
- Claude Code binary (or whichever harness)
- MCP client configuration pointing to the host MCP server
- Agent identity (persona key, injected via environment variable)
- Network access to the MCP server and Flask API (host network or bridged)
- An empty scratch workspace for the agent's own use (ephemeral, not shared)

**What the container blocks:**
- No host filesystem mounts — no access to `./var/`, `./shared/`, `./scenarios/`, or any host paths
- No access to other agents' containers or processes
- No access to other agents' logs, thoughts, or internal state
- No ability to write directly to `var/docs/` — must use `create_doc()` MCP tool, which enforces folder access control
- No ability to read files from `var/gitlab/` — must use `read_file()` MCP tool, which enforces repo access control

**Why this matters:**

In v2, folder access control in `lib/docs.py` is enforced at the API level, but agents with Bash access can `cat var/docs/leadership/*.json` and bypass it entirely. Containerization makes the API the only path — access control becomes real enforcement, not an honor system.

**Container image:**

A minimal image with Claude Code and dependencies. No build tools, compilers, or utilities beyond what Claude Code needs to run. Agents that need to build prototypes (like Sam in the research-lab scenario) do so inside their container's ephemeral scratch space, then commit results to the simulation's GitLab via `commit_files()`.

```dockerfile
FROM python:3.13-slim  # or node-based depending on Claude Code runtime
RUN pip install claude-code  # or npm install

# Inject hook configuration for telemetry reporting
COPY hooks.json /home/agent/.claude/hooks.json

# No host mounts, no volume shares
# MCP config and identity injected at container start
ENV AGENT_PERSONA_KEY=""
ENV MCP_SERVER_URL=""
```

**Container runtime options:**
- **Docker/Podman** — standard, well-understood, works everywhere
- **rootless Podman** — no daemon, no root, better for shared dev machines
- **Apple Containers** — macOS-native (SCION supports this), relevant for local dev

**Scratch workspace:**

Each container gets an ephemeral workspace directory. Agents can use Bash, Read, Write, Edit within this space — it's their scratchpad for drafting documents, writing prototype code, etc. But nothing in this workspace is visible to other agents or the simulation. To make work visible, agents must use MCP tools:
- Draft a doc locally → `create_doc()` to publish it
- Write prototype code locally → `commit_files()` to commit it to GitLab
- Generate a report locally → `post_message()` to share findings in a channel

This cleanly separates the agent's private working space from the shared simulation state.

**Relationship to SCION:**

This is architecturally identical to SCION's container model (`pkg/agent/run.go`), but with a different tool surface. SCION gives agents access to real git worktrees and real filesystems. We give agents access to simulated workplace APIs. Both use containers as the isolation boundary.

### Agent Lifecycle

**Startup:**
```
1. Read scenario.yaml, load character files
2. For each persona:
   a. Build system prompt from .CS.md (character info + ## Prompt section)
   b. Start container with:
      - AGENT_PERSONA_KEY=<key>
      - MCP_SERVER_URL=<host_mcp_endpoint>
      - System prompt injected via --system-prompt flag or mounted config
   c. Inside container: claude --system-prompt <persona_prompt> --mcp-server <server_config>
   d. Agent receives initial prompt: "You are [role]. Use your tools to interact."
   e. Agent calls whoami() and get_my_channels() to orient itself
   f. Agent signals ready
```

**Steady State:**
```
1. Notification bus detects new human message in #channel
2. Bus notifies agents who are members of #channel
3. Each agent:
   a. Calls get_messages(channel, since_id) to see what happened
   b. Decides whether to respond or pass (based on persona prompt)
   c. Takes action autonomously — no artificial limits on what they do:
      - Search the web multiple times, following threads of inquiry
      - Read existing docs, analyze them, create new docs
      - Commit code, read it back, iterate, commit again
      - Post findings to channels, DM other agents
      - Request background tasks for long-running work
   d. Calls signal_done() when finished (self-determined, not externally capped)
```

Agents are autonomous within their notification cycle. Claude Code drives the tool-calling loop natively — the agent decides what to do, how many tool calls to make, and when it's done. There is no per-turn action budget. An agent doing 30 web searches, creating 3 documents, and committing a prototype in a single cycle is expected behavior, not an edge case.

**Notification Mechanism — Options (choose one):**

| Method | How it works | Pros | Cons |
|--------|-------------|------|------|
| **stdin injection** | Write notification text to the subprocess's stdin | Simple, no dependencies | Requires careful stream management |
| **File-based signal** | Write to a watched file; agent has a `check_notifications()` tool it calls | Clean separation | Requires polling from agent side |
| **MCP notification** | Use MCP protocol's native notification mechanism | Standards-based | MCP notification support varies |
| **Polling from agent** | Agent periodically calls `get_recent_activity()` | No notification infra needed | Wasteful, latency tradeoff |
| **HTTP callback** | Lightweight HTTP server in agent process | Reliable delivery | Additional complexity per agent |

**Recommended approach:** stdin injection for simplicity. The notification is a plain-text message like:

```
[NOTIFICATION] New activity in #briefing. Use get_messages() to see what happened.
```

Claude Code processes accept follow-up input on stdin. The agent sees this as a new user message and acts on it.

### Agent Autonomy & Background Work

A core design goal of v3 is that agents are **creative and self-directed**, not constrained by a rigid turn structure. When notified, an agent should be free to go deep — research extensively, write documents, build prototypes, explore tangential threads — without waiting for permission or a next turn in a chat loop.

**Within a notification cycle, agents can:**
- Make unlimited MCP tool calls (web search, doc creation, code commits, etc.)
- Do multi-step workflows: search → analyze → draft doc → refine → publish → post summary
- Follow threads of inquiry that emerge from their research
- Create multiple artifacts (documents, commits, tickets, memos) in a single cycle
- Decide on their own when they're done

**Background tasks — agents requesting more work:**

Agents can spawn background work via a `request_background_task(goal, context, report_to)` MCP tool. This doesn't directly create a container — it posts a request to the container manager, which spins up a new ephemeral container with its own Claude Code process and the same MCP tool access. The background container runs independently, publishes results to the simulation (docs, commits, messages), and shuts down when done.

```
Agent "director" is notified of a new topic in #briefing
  → Reads the message
  → Creates a research plan document
  → Requests background task: "Search the web for an overview of [topic]"
  → Posts decomposition to #research
  → Calls signal_done()

Meanwhile, background container spins up:
  → Runs web searches via MCP tools
  → Creates a findings document in the research folder
  → Posts a summary to #research
  → Container exits

Other agents see the findings doc and summary on their next notification cycle.
```

**What this is NOT:**
- Agents do not directly manage other agents' lifecycles (no spawning containers, no killing siblings)
- Agents do not coordinate background tasks — fire and forget
- Background containers cannot spawn further background containers (max depth = 1, enforced by container manager)

**Guardrails on background tasks (enforced by container manager, not by the agent):**
- `max_concurrent_tasks` per scenario (default: 5)
- `task_timeout` per task (default: 900 seconds)
- Max depth of 1 — background tasks cannot spawn further background tasks
- Background containers have the same security hardening as primary agent containers

This preserves the autonomy of the agent (it decides what to research and when to spawn background work) while keeping resource management centralized (the container manager enforces limits).

### Tier Ordering

The current wave-based tier system (T1 → T2 → T3) provides realistic workplace dynamics where managers see IC responses before weighing in. In v3, strict tier ordering is harder because agents act autonomously.

**Proposed approach: staggered notification with done-signaling**

```
1. Human posts to #briefing
2. Notify Tier 1 agents → they act → each calls signal_done()
3. When all Tier 1 agents have signaled done (or timeout):
   Notify Tier 2 agents → they act → each calls signal_done()
4. When all Tier 2 agents have signaled done (or timeout):
   Notify Tier 3 agents → they act → signal_done()
```

The notification bus tracks `signal_done()` calls per tier and advances to the next tier when all agents in the current tier have completed (or a configurable timeout expires).

**Alternative: relaxed ordering**

For scenarios where strict ordering isn't important (e.g., the research-lab scenario where all Tier 2 researchers work independently), skip tier gating entirely and notify all agents simultaneously. Let the conversation be organic.

This could be a per-scenario setting:
```yaml
settings:
  tier_ordering: strict    # staggered with done-signaling
  # tier_ordering: relaxed # notify all simultaneously
```

### Loop Prevention

Without the orchestrator controlling who speaks when, we need guardrails against infinite loops (Agent A posts → Agent B reacts → Agent A reacts → ...).

**Rules:**
1. Agents are only notified on **human messages and system events**, never on other agents' messages (same as current `_is_agent_message` filter)
2. Per-agent cooldown: minimum N seconds between notifications to the same agent
3. `signal_done()` is mandatory — agent must explicitly end its turn
4. Background tasks cannot spawn further background tasks (max depth = 1)

Note: there is **no per-notification action budget**. Agents are free to make as many tool calls as they need within a cycle. The guardrails are about preventing cascading *notifications between agents*, not about limiting what an individual agent does during its turn.

**Exception — directed re-engagement:** If an agent wants another agent to respond to something, they use `send_dm()`. The notification bus can deliver DMs as a new notification to the recipient, but with a flag indicating it's agent-originated (so it doesn't cascade further).

### Context Management

**Problem:** Long-running Claude Code processes accumulate context. After many turns of tool calls and responses, the context window fills up.

**Mitigations:**
1. **Claude Code's built-in summarization** — Claude Code already handles context window management via conversation compaction
2. **Periodic restart** — After N notification cycles, gracefully restart the agent process with a fresh system prompt. The agent loses conversational memory but retains world state via API calls
3. **Lean notifications** — Keep notification text minimal ("new activity in #channel") so the agent fetches only what it needs via tools rather than receiving large context dumps
4. **Stateless by design** — Since all state lives in the Flask server (messages, docs, tickets, etc.), agent processes are disposable. Restart is cheap

### Observability: Token Usage & Agent Telemetry

In v2, `agent_runner.py` captures token usage directly from the Claude SDK response object. In v3, agents are Claude Code processes inside containers — we never see the LLM API responses. Token data is inside the agent process, invisible to the host.

**Recommended approach: Claude Code hooks**

Claude Code supports [hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) — shell commands that fire on lifecycle events (`model-end`, `tool-start`, `tool-end`, etc.). SCION uses this same mechanism (`sciontool hook model-end`) for model call counting and limit enforcement.

We inject a hook configuration into each container at build time. On every `model-end` event, the hook reports usage data back to the MCP server:

```json
// .claude/hooks.json (injected into container image)
{
  "hooks": {
    "model-end": {
      "command": "curl -s http://mcp-server:5001/api/telemetry/model-end -H 'X-Agent-Key: ${AGENT_PERSONA_KEY}' -d @-",
      "timeout": 5000
    },
    "tool-start": {
      "command": "curl -s http://mcp-server:5001/api/telemetry/tool-start -H 'X-Agent-Key: ${AGENT_PERSONA_KEY}' -d @-",
      "timeout": 5000
    }
  }
}
```

Claude Code passes event metadata (model, token counts, tool name, duration) as stdin JSON to the hook command. The hook `curl`s it to the MCP server, which logs it and exposes it via the web UI.

**What this captures:**
- Per-agent, per-request input/output token counts
- Model used per request
- Tool call frequency and duration
- Thinking/reasoning token usage (extended thinking)
- Total cost per agent, per notification cycle, per session

**Why hooks, not other approaches:**

| Approach | Problem |
|----------|---------|
| MCP tool (`report_usage()`) | LLM doesn't know its own token counts — can't self-report |
| `--output-format json` + stdout parsing | Requires capturing and parsing container stdout; fragile, couples to Claude Code output format |
| Vertex AI billing API | Per-project aggregates, hard to correlate back to individual agents without custom labels |
| LLM traffic proxy | Full request/response interception; high complexity, latency, and security surface |
| **Claude Code hooks** | **Automatic, no agent cooperation needed, fires on every model call, structured event data, injected at build time** |

**Telemetry endpoint on MCP server:**

The MCP server exposes `/api/telemetry/*` endpoints (not MCP tools — these are raw HTTP from hook scripts). It aggregates usage data and exposes it to:
- Flask server (for web UI display — "thoughts" panel, usage dashboard)
- Session save/load (usage stats persisted with session snapshots)
- Container manager (for limit enforcement if needed)

**Web UI integration:**

The existing web UI already has a "thoughts" panel showing agent reasoning. In v3, this is populated by hook telemetry rather than SDK response objects. The `model-end` hook captures thinking text (if available in the event payload), and the MCP server forwards it to Flask for SSE broadcast to the UI.

### What Gets Removed

| Current Component | v3 Status |
|-------------------|-----------|
| `lib/response_schema.py` | **Removed entirely** — no more JSON/regex response parsing |
| `lib/agent_runner.py` (AgentPool, ClaudeSDKClient) | **Replaced** — subprocess management instead of SDK sessions |
| Command execution in `lib/orchestrator.py` | **Removed** — agents execute their own commands via MCP |
| `build_turn_prompt()` in `lib/personas.py` | **Simplified** — no more per-turn context assembly; agents fetch their own context |
| Structured response protocol | **Removed** — no more `{"action": "respond", "messages": [...]}` |

### What Gets Added

| New Component | Purpose |
|---------------|---------|
| MCP server (`lib/mcp_server.py`) | Async (FastAPI/Starlette) process on port 5001; translates MCP tool calls to Flask REST API; authenticates agents, enforces access control, audit logs all calls |
| Notification bus (`lib/notifier.py`) | Detects new activity, notifies relevant agents via `docker exec` stdin, tracks `signal_done()` per tier |
| Agent container manager (`lib/agent_containers.py`) | Build/start/stop/restart agent containers with security hardening, health monitoring, staged shutdown |
| Container image (`Dockerfile.agent`) | Minimal image with Claude Code + MCP client config + telemetry hooks — no tool code, no host filesystem access |
| Telemetry hooks (`.claude/hooks.json`) | Injected into container image; fires on `model-end` / `tool-start` events, reports usage data to MCP server |

### What's Unchanged

| Component | Notes |
|-----------|-------|
| `lib/webapp.py` | REST API unchanged; MCP server proxies to it over localhost |
| `lib/docs.py`, `lib/gitlab.py`, `lib/tickets.py`, etc. | Subsystem modules unchanged |
| `lib/session.py` | Session save/load unchanged |
| `lib/events.py` | Events fire via notification bus instead of orchestrator |
| `lib/scenario_loader.py` | Unchanged |
| `lib/personas.py` | Simplified — only builds initial system prompt from .CS.md |
| `scenarios/` | Character files unchanged; scenario.yaml gains optional `tier_ordering` setting |
| Web UI | Unchanged — still reads from Flask server via SSE |

## Migration Path

### Phase 1: MCP Server + Container Image

Build both together — they are co-dependent.

**MCP Server (`lib/mcp_server.py`):**
- Async process (FastAPI/Starlette) on port 5001, separate from Flask
- Exposes simulation tools via MCP-over-SSE protocol
- Proxies tool calls to Flask REST API (port 5000) over localhost
- Authenticates each connection by `AGENT_PERSONA_KEY`
- Enforces per-agent access control (folder access, channel membership, repo permissions)
- Audit logs every tool call

**Container Image (`Dockerfile.agent`):**
- Minimal image: Claude Code + MCP client config
- Read-only root filesystem, ephemeral scratch workspace
- All capabilities dropped, no privilege escalation
- Network restricted to MCP server + Vertex AI endpoints

**Validation:**
- Start Flask server, start MCP server, start one agent in a container
- Verify agent can call MCP tools and interact with simulation
- Verify agent **cannot** access host filesystem, other containers, or bypass access controls
- Verify agent **cannot** reach cloud metadata or arbitrary network endpoints

Key decisions at this phase:
- Container runtime: Docker, Podman, or both
- Networking: isolated bridge network with explicit allows
- MCP transport: SSE over HTTP (container connects to host endpoint) is simplest for containers
- Credential injection: how Claude Code authenticates with Vertex AI from inside the container (read-only mount of application default credentials, or proxy via MCP)

### Phase 2: Agent Container Manager

Build `lib/agent_containers.py` that manages agent containers — build image, start containers with persona-specific config and security hardening (see Security Considerations), inject notifications via `docker exec` stdin, stream logs from containers, health monitoring (container alive + agent heartbeat via `signal_done()`), graceful restart with staged shutdown.

### Phase 3: Notification Bus

Build `lib/notifier.py` — poll for new messages (reuse existing logic from `orchestrator.py`), notify relevant agents via container stdin injection, track `signal_done()` per tier with timeout-based lease release, advance tiers. Per-agent notification queue with actor-style mutex to prevent concurrent turn corruption.

### Phase 4: Integration

Wire phases 1-3 together. Run alongside v2 orchestrator initially (different port, same server) for comparison testing. Both v2 and v3 can operate against the same Flask server since the REST API is unchanged.

### Phase 5: Deprecate v2

Once v3 is validated, remove `response_schema.py`, simplify `orchestrator.py` to the notification bus, remove SDK dependency.

## Comparison with SCION

This architecture converges toward SCION's model but retains our richer simulation layer:

| Aspect | SCION | v3 Proposal |
|--------|-------|-------------|
| Agent isolation | Docker containers | Docker/Podman containers (same) |
| Agent I/O | tmux paste buffer | stdin injection via `docker exec` |
| Tool access | Harness-native (Claude Code's built-in tools) | MCP tools wrapping simulation API — **only** interface to the world |
| Filesystem access | Real git worktrees mounted into containers | Ephemeral scratch space only — no host mounts, no shared state |
| Coordination substrate | Git worktrees (filesystem) | Channels, docs, tickets, memos (richer, API-mediated) |
| Access control enforcement | Trust-based (agent has filesystem access) | Container-enforced (API is the only path, access control is real) |
| Orchestration | None (fully autonomous) | Lightweight notification bus with optional tier ordering |
| Harness support | Multi-harness (Claude, Gemini, OpenCode, Codex) | Claude Code only (initially) |
| State management | Per-agent filesystem config | Centralized Flask server |

The key difference: SCION agents interact with real codebases via real tools and real filesystems. Our agents interact with a simulated workplace exclusively via API-backed MCP tools — the container boundary ensures agents can't bypass the API layer. Both use containers for isolation, but we use it to enforce the simulation boundary, not just for process separation.

## Security Considerations

Containerization is not an optimization or a nice-to-have — it is the minimum viable security boundary for this architecture. Without it, every other access control mechanism is theater.

### Why Containers Are Non-Negotiable

**The threat model is simple:** agents are LLM processes with tool access. LLMs are unpredictable. Even with well-crafted persona prompts, an agent may:

- Explore the filesystem out of curiosity (`ls /`, `cat /etc/passwd`, `find / -name "*.env"`)
- Read other agents' logs to gain information advantage (`cat var/logs/ceo.log`)
- Write directly to data directories to bypass access controls (`echo "..." > var/docs/leadership/secret.json`)
- Attempt to access cloud metadata endpoints (`curl http://169.254.169.254/...`)
- Read environment variables from other processes (`cat /proc/*/environ`)
- Install packages or download tools (`pip install`, `curl | sh`)

These aren't adversarial attacks — they're natural behaviors for an AI agent with Bash access trying to be helpful. The agent doesn't know it's supposed to stay in its lane unless the lane has walls.

Process-level isolation (subprocesses without containers) does not address these risks. A subprocess runs as the same user, with the same filesystem view, and the same network access as the parent. Containers provide:

1. **Filesystem isolation** — the agent literally cannot see host paths
2. **Network policy** — restrict egress to only the MCP server endpoint (and Vertex AI for LLM calls)
3. **Resource limits** — CPU/memory caps prevent a runaway agent from starving others
4. **Capability dropping** — no `CAP_SYS_ADMIN`, no `CAP_NET_RAW`, no privilege escalation
5. **Read-only root filesystem** — agent can only write to its ephemeral scratch space

### Lessons from OpenClaw

OpenClaw (referenced in `./references/openclaw`) demonstrates several useful architectural patterns — gateway-as-source-of-truth, event broadcasting, actor queues, session leasing — but its security posture is a cautionary tale. Key issues in OpenClaw that we explicitly avoid:

- **Process-based isolation without containers** — agents can access the host filesystem, read other agents' state, and escalate privileges
- **Trust-based access control** — permissions enforced in application logic, trivially bypassed by any agent with shell access
- **Shared credential exposure** — API keys and tokens accessible to all agents in the same process space
- **No network segmentation** — agents can reach arbitrary endpoints including cloud metadata services

We adopt OpenClaw's **architectural patterns** (listed below) while rejecting its **security model**:

| Pattern from OpenClaw | How we use it | Security difference |
|----------------------|---------------|---------------------|
| Gateway as single source of truth | Flask server owns all state | Same pattern |
| Event broadcasting (WebSocket/SSE) | MCP server pushes notifications to agents | Same pattern, over MCP transport |
| Actor queue (per-agent mutex) | Notification queue prevents concurrent turns | Same pattern |
| Session leasing (atomic turns) | `signal_done()` with timeout-based lease release | Same pattern |
| Permission modes on tool execution | MCP server enforces per-agent access control | **Container-enforced, not honor-based** |
| Process-based isolation | **Rejected** — containers required | **Fundamental difference** |

### Container Security Configuration

Recommended container runtime flags:

```bash
docker run \
  --read-only \                          # immutable root filesystem
  --tmpfs /tmp:size=512m \               # writable scratch space, size-limited
  --tmpfs /home/agent:size=1g \          # agent workspace, size-limited
  --cap-drop ALL \                       # drop all Linux capabilities
  --security-opt no-new-privileges \     # prevent privilege escalation
  --network=agent-net \                  # isolated bridge network
  --memory=2g \                          # memory cap
  --cpus=1.0 \                           # CPU cap
  --pids-limit=256 \                     # prevent fork bombs
  -e AGENT_PERSONA_KEY=director \
  -e MCP_SERVER_URL=http://mcp:8080 \
  agent-image:latest
```

Network policy (via Docker network or iptables):
- **Allow:** MCP server endpoint (e.g., `mcp:8080`)
- **Allow:** Vertex AI API (`us-east5-aiplatform.googleapis.com`)
- **Deny:** Host filesystem, other containers, cloud metadata (`169.254.169.254`), everything else

### MCP Server as the Trust Boundary

The MCP server is the **sole gatekeeper** between agents and simulation state. It must:

1. **Authenticate every request** — verify `AGENT_PERSONA_KEY` on each tool call
2. **Enforce access control** — check folder access, channel membership, repo permissions before executing
3. **Rate limit** — cap tool calls per notification cycle (prevents runaway agents)
4. **Audit log** — record every tool call with agent identity, parameters, and result
5. **Input validation** — sanitize all parameters (prevent injection via document content, commit messages, etc.)
6. **Reject unknown tools** — agents should not be able to call arbitrary endpoints

The MCP server is not just an API wrapper — it is the security perimeter.

## Open Questions

1. **Vertex AI credentials in containers** — Claude Code needs GCP application default credentials to call Vertex AI. Options: (a) mount the credentials file read-only into each container, (b) proxy all LLM calls through the MCP server so credentials stay on the host, (c) use workload identity if running on GCP. Option (a) is simplest; option (b) is cleanest for isolation but adds latency and complexity.

2. **Claude Code mode** — `claude -p` (print mode, exits after response) vs. interactive mode (stays alive, accepts follow-up input). Interactive mode is needed for stdin notification injection but may have different behavior characteristics inside a container.

3. **Container overhead** — Running 6-11 containers simultaneously adds memory and startup overhead. Need to measure. Lightweight base images (Alpine, distroless) and container reuse help. For local dev, rootless Podman may be lighter than Docker.

4. **Multi-harness support** — Should we support non-Claude harnesses (Gemini CLI, etc.) like SCION does? This would mean different container images per harness type, but the MCP tool surface stays the same since it's server-side.

5. **Background tasks** — Current `task_executor.py` spawns one-shot Claude SDK workers. In v3, should background tasks be additional containers with the same MCP tools? Or lighter-weight one-shot `claude -p` calls in ephemeral containers? The container model makes this natural — spin up a new container, let it run, tear it down.

6. **Token economics** — Long-running processes with tool calls accumulate context faster than the current turn-based model. Need to measure actual token consumption per notification cycle and compare with v2.

7. **Hook event payload** — Claude Code hooks pass event metadata as stdin JSON. Need to verify exactly what fields are available in the `model-end` event payload (token counts, model ID, thinking text, duration). This determines how rich our telemetry can be. If the payload is limited, we may need to supplement with `docker logs` parsing or `--output-format json`.

8. **Graceful degradation** — What happens when a container crashes mid-action (e.g., after posting a message but before creating the follow-up document)? Container restart is cheap (stateless), but the partial action is already committed to the Flask server. Need idempotency or at-least-once delivery semantics.

9. **Scratch workspace persistence** — Agent scratch workspaces are ephemeral by default (lost on container restart). If an agent is building a prototype over multiple notification cycles, it needs that workspace to survive restarts. Options: (a) named Docker volumes per agent (persist across restarts but not across sessions), (b) agents must commit all intermediate work to GitLab (nothing important stays local), (c) tmpfs with configurable persistence.

10. **Direct REST API bypass** — Even with MCP tools server-side, an agent with network access to port 5001 could potentially reverse-engineer the MCP protocol and craft raw HTTP requests. Mitigation: per-agent API tokens with short TTL, request signing, or mutual TLS between containers and MCP server. The goal isn't to be impenetrable — it's to raise the bar high enough that an LLM won't accidentally bypass it.
