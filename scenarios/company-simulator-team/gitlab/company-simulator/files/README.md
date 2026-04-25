<img width="1254" height="1254" alt="image" src="https://github.com/user-attachments/assets/effccadf-db73-4adc-9fd5-f75bf699b761" />


# Multi-Agent Organization

A simulated software company where AI personas collaborate through a Slack-like chat system. A human operator drops a message into a channel — a feature request, a customer escalation, a pricing question — and an entire organization responds: engineers dig into feasibility, the PM scopes requirements, sales positions the value, finance models the deal, and leadership makes the call. All in real time, all visible in a web UI.

Each agent runs as an autonomous Claude Code instance inside a podman container, interacting with the simulation exclusively through MCP tools. Three processes coordinate the simulation: a Flask web server, an MCP tool server, and a container orchestrator.

> **Warning:** This project runs 10+ concurrent Claude instances per conversation round. A single human message can trigger dozens of API calls across multiple tiers. Token usage adds up fast — monitor your billing closely.

## Quick Start

**Requirements:**
- Python 3.13+
- podman
- Node.js/npm (for building the agent image)
- Google Cloud credentials for Vertex AI

### 1. Install dependencies

```bash
git clone <repo-url>
cd multi-agent-organization
pip install -e .
```

### 2. Create `.env`

```bash
cat > .env <<EOF
CLAUDE_CODE_USE_VERTEX=1
CLOUD_ML_REGION=us-east5
ANTHROPIC_VERTEX_PROJECT_ID=<your-project-id>
EOF
```

You also need GCP application default credentials:

```bash
gcloud auth application-default login
```

### 3. Build the agent container image

The agent image contains Claude Code CLI and telemetry hooks. GCP credentials are mounted at runtime.

```bash
./scripts/build-agent-image.sh
```

This runs `podman build -f container/Dockerfile.agent -t agent-image:latest container/` after verifying prerequisites.

**What goes into the image:**
- Python 3.13-slim base
- Claude Code CLI (`@anthropic-ai/claude-code` via npm)
- Telemetry hooks (`container/agent-hooks.json`) for forwarding model/tool events to the MCP server
- Runs as non-root `agent` user with `sleep infinity` as entrypoint

### 4. Start the three processes

You need three terminals (or use `tmux`/`screen`):

**Terminal 1 — Flask server:**
```bash
python main.py server --port 5000 --scenario tech-startup
```

**Terminal 2 — MCP server:**
```bash
python main.py mcp-server --port 5001 --scenario tech-startup
```

**Terminal 3 — Container orchestrator:**
```bash
python main.py chat --model sonnet --scenario tech-startup
```

### 5. Use it

Open `http://localhost:5000` in your browser. Click **New** to start a session. The orchestrator will launch one podman container per agent and report ready status.

Type a message in any channel and watch the team respond.

## Architecture

Three processes communicate via HTTP:

```
Flask Server (port 5000)          MCP Server (port 5001)
  REST API + SSE + Web UI           Per-agent MCP-over-SSE
  In-memory state store             32 tools with access control
  Session management                Audit logging + telemetry
        ▲                                  ▲
        │ HTTP/REST                        │ MCP-over-SSE
        │                                 │
        ▼                                 │
Container Orchestrator              Agent Containers (podman)
  Polls for new messages             One per persona
  Tiered wave execution              claude CLI + system prompt
  podman exec per turn               MCP config mounted
  signal_done detection              sleep infinity entrypoint
        │                                 ▲
        └── podman exec ──────────────────┘
```

- **Flask server** holds all simulation state and serves the web UI
- **MCP server** provides 32 tools (chat, docs, gitlab, tickets, memos, blog, email, meta) that agents use to interact with the simulation
- **Orchestrator** polls for human messages, launches agent turns via `podman exec claude` inside long-running containers, and manages tiered response ordering

## How It Works

### Tiered Response Ordering

When a human message arrives, agents respond in tiers:

| Tier | Agents | Purpose |
|------|--------|---------|
| 1 (ICs) | senior, support, sales, devops | Domain experts respond first |
| 2 (Managers) | engmgr, architect, pm, marketing, projmgr | See Tier 1 output, then coordinate |
| 3 (Executives) | ceo, cfo | See everything, make final calls |

Agents within a tier run concurrently (up to `--max-concurrent`). Tiers run sequentially. Each agent fetches its own context via MCP tools and calls `signal_done()` when finished.

### Agent Execution

Each agent turn:

1. Orchestrator builds a lightweight turn prompt (~300 bytes) telling the agent which channels have new activity
2. Orchestrator runs `podman exec agent-<key> claude -p <prompt> --system-prompt-file /home/agent/system-prompt.md --mcp-config /home/agent/.mcp-config.json ...`
3. Inside the container, Claude Code connects to the MCP server and uses tools: `get_messages()` to read context, `post_message()` to respond, `create_ticket()` / `commit_files()` / `create_doc()` etc. as needed
4. Agent calls `signal_done()` to signal completion
5. Orchestrator detects the done event and advances to the next tier

### Wave Propagation

If agents post to channels that weren't in the original trigger set, those channels become triggers for a new wave. This continues up to `--max-rounds` (default 5). After waves complete, autonomous continuation can run up to `--max-auto-rounds` additional cycles.

## The Team (tech-startup scenario)

| Persona | Role | Tier | Key Channels |
|---------|------|------|-------------|
| **Sarah** | Product Manager | 2 | #general, #engineering, #sales, #support, #leadership |
| **Marcus** | Engineering Manager | 2 | #general, #engineering, #support, #devops |
| **Priya** | Software Architect | 2 | #general, #engineering |
| **Alex** | Senior Engineer | 1 | #general, #engineering |
| **Jordan** | Support Engineer | 1 | #general, #engineering, #support, #support-external |
| **Taylor** | Sales Engineer | 1 | #general, #sales, #sales-external, #marketing |
| **Dana** | CEO | 3 | #general, #leadership, #sales, #marketing |
| **Morgan** | CFO | 3 | #general, #leadership, #sales |
| **Riley** | Marketing | 2 | #general, #marketing, #sales, #sales-external |
| **Casey** | DevOps Engineer | 1 | #general, #devops, #engineering, #support |
| **Nadia** | Project Manager | 2 | #general, #engineering, #support, #leadership, #devops |

## Features

- **Chat** — Multi-channel Slack-style messaging with internal and external (customer-facing) channels
- **Documents** — Folder-based document management with per-persona access control
- **GitLab** — Mock repository hosting with commits, file browsing, and history
- **Tickets** — Ticket tracker with priorities, statuses, assignees, comments, and dependency tracking
- **Memos** — Threaded discussion board for RFCs and proposals
- **Blog** — Internal and external company blog with comments
- **Email** — Company-wide broadcast announcements
- **Events** — Injectable scenario events (production outages, customer escalations, compliance notices)
- **Background Tasks** — Autonomous worker tasks spawned by agents with full tool access
- **NPC Management** — Hire/fire agents, modify configs, toggle online/offline from the UI
- **Session Management** — Save, load, and restore complete simulation state
- **Director Channels** — Private back-channels for sending instructions to individual agents

## CLI Reference

### Server

```bash
python main.py server [--port 5000] [--host 127.0.0.1] [--scenario tech-startup]
```

### MCP Server

```bash
python main.py mcp-server [--port 5001] [--host 0.0.0.0]
                          [--flask-url http://127.0.0.1:5000]
                          [--scenario tech-startup]
```

### Orchestrator

```bash
python main.py chat [--scenario tech-startup]
                    [--model sonnet|opus|haiku]
                    [--personas pm,senior,architect]
                    [--max-rounds 5]
                    [--max-auto-rounds 0]
                    [--poll-interval 5.0]
                    [--server-url http://127.0.0.1:5000]
                    [--mcp-port 5001]
                    [--mcp-host auto]
                    [--container-image agent-image:latest]
                    [--container-timeout 300]
                    [--max-turns 50]
                    [--max-concurrent 4]
                    [--done-timeout 120]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | `sonnet` | Claude model: `sonnet`, `opus`, or `haiku` |
| `--personas` | all | Comma-separated subset of personas to run |
| `--max-rounds` | 5 | Max ripple waves per response cycle |
| `--max-auto-rounds` | 0 | Max autonomous continuation rounds (0=disabled) |
| `--max-concurrent` | 4 | Max agents running concurrently within a tier |
| `--container-timeout` | 300 | Seconds before killing an agent turn |
| `--done-timeout` | 120 | Seconds to wait for `signal_done` before advancing tier |
| `--max-turns` | 50 | Max Claude tool-use turns per agent exec |

### Building the Agent Image

```bash
# Default image name: agent-image:latest
./scripts/build-agent-image.sh

# Custom image name
./scripts/build-agent-image.sh my-agent:v2
```

**Prerequisites checked by the script:**
- `podman` installed
- `container/agent-hooks.json` exists

## Scenarios

Scenarios define the entire simulation: personas, channels, events, and configuration. Stored under `scenarios/<name>/`.

| Scenario | Description |
|----------|-------------|
| `tech-startup` | Default: 11-person engineering org |
| `dotcom-2000` | Y2K startup scenario |
| `dnd-campaign` | Dungeons & Dragons campaign |
| `company-simulator-team` | Internal team scenario |
| `research-lab` | Research team scenario |

### Creating a New Scenario

1. Create a directory under `scenarios/` with a `scenario.yaml`
2. Add character files in `scenarios/<name>/characters/` (`.CS.md` format)
3. Define channels, memberships, response tiers, folders, folder access, and events in the YAML

See `scenarios/tech-startup/scenario.yaml` for a complete example.

## Project Structure

```
main.py                         # Entry point (server / mcp-server / chat)
pyproject.toml                  # Dependencies
container/
  Dockerfile.agent              # Agent container image
  agent-hooks.json              # Telemetry hooks for agent containers
scripts/build-agent-image.sh    # Image build script
.env                            # Vertex AI credentials (not committed)
lib/
  webapp/                       # Flask server, REST API, SSE, web UI
    __init__.py                 # App factory + blueprint registration
    state.py                    # Shared in-memory state management
    helpers.py                  # Initialization + SSE broadcast helpers
    template.py                 # Jinja2 web UI template
    routes/                     # REST API endpoint blueprints
      messages.py               # Messaging API + SSE stream
      channels.py               # Channel management
      documents.py              # Document CRUD + search
      gitlab.py                 # Repository operations
      tickets.py                # Ticket management
      memos.py                  # Memo threads
      blog.py                   # Blog posts + replies
      emails.py                 # Email broadcast
      npcs.py                   # Agent management (toggle, hire, fire)
      events.py                 # Event pool + triggers
      sessions.py               # Session save/load/new
      orchestrator.py           # Orchestrator status + commands
      recaps.py                 # Recap generation
      misc.py                   # Roles, templates, personas, usage
  container_orchestrator.py     # Container-based orchestrator + ContainerPool
  mcp_server.py                 # MCP tool server (Starlette + FastMCP)
  agent_runner.py               # Model utilities, one-shot agent runner
  chat_client.py                # HTTP client for Flask REST API
  personas.py                   # Persona registry, prompt builders (v2 + v3)
  scenario_loader.py            # YAML scenario loader
  session.py                    # Session save/load/new
  docs.py                       # Document storage + folder access control
  gitlab.py                     # Mock GitLab (repos, commits, files)
  tickets.py                    # Ticket tracking + ID generation
  events.py                     # Scenario event pool + event log
  memos.py                      # Threaded discussion board
  blog.py                       # Internal + external company blog
  email.py                      # Corporate email system
  task_executor.py              # Background task execution
  cli.py                        # CLI argument parser
scenarios/
  tech-startup/                 # Default scenario
    scenario.yaml               # Channels, tiers, folders, events, settings
    characters/                 # Per-character prompts (.CS.md files)
var/                            # Runtime state (gitignored)
  chat.log                      # Message persistence
  docs/                         # Document storage
  gitlab/                       # Repository storage
  tickets/                      # Ticket storage
  logs/                         # Agent logs + MCP audit trail
  tmp/                          # Agent MCP configs + system prompts
  instances/                    # Saved session snapshots
```

## Dependencies

- **claude-agent-sdk** — Claude Agent SDK for background tasks and one-shot agents
- **flask** — Web server and REST API
- **mcp** — MCP protocol library (FastMCP)
- **uvicorn** — ASGI server for the MCP server
- **httpx** — Async HTTP client (MCP → Flask proxy)
- **requests** — Sync HTTP client (orchestrator → Flask)
- **pyyaml** — Scenario YAML parsing
- **python-dotenv** — Environment variable management
- **podman** — Container runtime (not a Python dependency)
- **claude CLI** — Claude Code CLI, installed in the agent image via npm
