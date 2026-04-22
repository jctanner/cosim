# CLAUDE.md

## Project Overview

Multi-Agent Organization is a simulation platform where AI personas (powered by Claude via Vertex AI) collaborate as a simulated company. Agents operate within a suite of integrated workplace tools — chat channels, a document system, a GitLab-style repository, a ticket tracker, a threaded memo board, company-wide email, and autonomous background tasks. A human interacts through a web UI and the entire organization responds with realistic department-specific perspectives in real-time.

## Quick Start

```bash
# Install dependencies
pip install -e .

# Create .env with Vertex AI credentials
cat > .env <<EOF
CLAUDE_CODE_USE_VERTEX=1
CLOUD_ML_REGION=us-east5
ANTHROPIC_VERTEX_PROJECT_ID=<your-project-id>
EOF

# Terminal 1: Start Flask server
python main.py server --port 5000 --host 127.0.0.1

# Terminal 2: Start MCP server
python main.py mcp-server --port 5001

# Terminal 3: Start agent orchestrator
python main.py chat --model sonnet --scenario tech-startup
```

Web UI at `http://localhost:5000`.

## Architecture

### Two-Process Model

- **Flask Server** (`lib/webapp.py`) — REST API, SSE broadcast, web UI, in-memory state for all subsystems
- **Orchestrator** (`lib/container_orchestrator.py`) — Polling loop that drives agent responses via podman containers with MCP tools

The two processes communicate exclusively via HTTP REST calls.

### Wave-Based Tiered Execution

When a human message arrives, agents respond in tiers:
1. **Tier 1** (ICs): senior, support, sales, devops — respond first
2. **Tier 2** (Managers): engmgr, architect, pm, marketing, projmgr — see Tier 1 output before responding
3. **Tier 3** (Executives): ceo, cfo — see all prior tiers before responding

Agents within each tier run concurrently (up to `--max-concurrent`). If agents post to new channels, the loop repeats (up to `max_rounds`).

### Container-Based Agent Execution

`ContainerPool` (`lib/container_orchestrator.py`) manages long-running podman containers — one per persona. Each agent turn runs `podman exec claude -p ...` inside the warm container. Agents interact with the simulation exclusively via MCP tools served by the MCP server (`lib/mcp_server.py`). Tier advancement uses `signal_done` events from the MCP server rather than waiting for process exit.

## Agent Tooling (MCP Tools)

Agents interact with the simulation via MCP tools registered on the MCP server. Tool categories:

| Tool Category | Module | Description |
|---------------|--------|-------------|
| Communication | `lib/mcp_server.py` | `post_message`, `get_messages`, `send_dm`, `get_my_dms`, `join_channel`, `get_channel_members` |
| Documents | `lib/docs.py` | `create_doc`, `update_doc`, `read_doc`, `search_docs`, `list_docs` — access-controlled folders |
| GitLab | `lib/gitlab.py` | `create_repo`, `commit_files`, `read_file`, `list_repo_tree`, `get_repo_log` |
| Tickets | `lib/tickets.py` | `create_ticket`, `update_ticket`, `comment_on_ticket`, `list_tickets` |
| Memos | `lib/memos.py` | `create_memo`, `reply_to_memo` — threaded async discussions |
| Blog | `lib/blog.py` | `create_blog_post`, `reply_to_blog` |
| Email | `lib/email.py` | `send_email`, `get_emails` |
| Meta | `lib/mcp_server.py` | `whoami`, `who_is`, `get_my_channels`, `get_my_tickets`, `get_recent_activity`, `signal_done` |

Additionally, the system provides:
- **Email** (`lib/email.py`) — Company-wide announcements visible to all agents. Injected by events or sent by agents.
- **Events** (`lib/events.py`) — Scenario-defined chaos injection (production outages, customer escalations, compliance notices, etc.). Each event fires multiple actions: messages, tickets, documents, emails.
- **Recaps** — Generated session summaries in configurable styles.
- **Session Management** (`lib/session.py`) — Full-state save/load/new with snapshots capturing chat, docs, repos, tickets, emails, memos, events, tasks, roster, and agent thoughts.

## Project Structure

```
main.py                     # Entry point (server / chat subcommands)
pyproject.toml              # Dependencies and metadata
container/
  Dockerfile.agent          # Agent container image definition
  agent-hooks.json          # Claude Code hooks for containerized agents
.env                        # Vertex AI credentials (not committed)
lib/
  webapp.py                 # Flask server, REST API, SSE, web UI
  container_orchestrator.py # Container-based agent loop (podman + MCP)
  mcp_server.py             # MCP tool server for agent containers
  agent_runner.py           # Claude SDK utilities + one-shot agent runner
  chat_client.py            # HTTP client for REST API
  personas.py               # Persona registry, prompt builders
  scenario_loader.py        # YAML scenario loader
  session.py                # Session save/load/new (full state snapshots)
  docs.py                   # Document storage + folder access control
  gitlab.py                 # Mock GitLab (repos, commits, files)
  tickets.py                # Ticket tracking + ID generation
  events.py                 # Scenario event pool + event log
  memos.py                  # Threaded discussion board (async memos)
  email.py                  # Corporate email system
  task_executor.py          # Background task execution (autonomous workers)
  cli.py                    # CLI argument parser
scenarios/
  tech-startup/             # Default: 11-person engineering org
  dotcom-2000/              # Y2K startup scenario
  dnd-campaign/             # Dungeons & Dragons campaign
  company-simulator-team/   # Internal team scenario
  character-templates/      # Reusable role templates
var/                        # Runtime state (gitignored)
  chat.log                  # Message persistence
  docs/                     # Generated documents
  gitlab/                   # Generated repos/files/commits
  tickets/                  # Generated tickets
  logs/                     # Agent session logs (one per persona)
  instances/                # Saved session snapshots
.claude/skills/             # Claude Code skill definitions (persona prompts)
```

## Tech Stack

- **Python 3.13+**
- **Flask >=3.0** — web server and REST API
- **claude-agent-sdk==0.1.48** — Claude Agent SDK (Vertex AI)
- **Server-Sent Events** — real-time UI updates
- **PyYAML** — scenario configuration
- **python-dotenv** — credential management
- **requests** — HTTP client (orchestrator to server)

## Scenarios

Each scenario is a `scenario.yaml` defining:
- **characters** — persona keys, display names, character file paths, team descriptions
- **channels** — chat channels with descriptions and external/internal flags
- **memberships** — which personas belong to which channels
- **response_tiers** — wave execution order (tier 1/2/3)
- **folders** — document folders (shared, department, personal) with types
- **folder_access** — per-folder persona access lists
- **events** — injectable scenario events with multi-action payloads (messages, tickets, documents, emails)
- **settings** — background tasks, memos, concurrency limits, timeouts, allowed tools

Character prompts live in per-scenario `characters/*.md` files.

To create a new scenario: add a directory under `scenarios/` with a `scenario.yaml` and `characters/` directory following the structure in `scenarios/tech-startup/`.

## Key Conventions

### Naming

- **Persona keys**: lowercase, no spaces (e.g., `pm`, `senior`, `devops`, `engmgr`, `projmgr`)
- **Channels**: lowercase with hyphens, `#`-prefixed (e.g., `#general`, `#engineering`, `#support-external`)
- **Document folders**: lowercase (e.g., `shared`, `engineering`, `leadership`)
- **Ticket IDs**: `TK-` prefix + 6-char hex hash
- **Background task IDs**: `BG-` prefix + 6-char hex hash
- **Commit IDs**: 8-char hex hash
- **Document slugs**: lowercase with hyphens, max 80 chars

### Code Style

- Type hints used throughout
- Docstrings on functions and classes
- Imports ordered: stdlib, third-party, local
- Module-level config dicts start empty, populated by `scenario_loader.load_scenario()`
- Thread-safe state uses `threading.Lock` (e.g., `_lock`, `_sub_lock`, `_memos_lock`)
- No automated test suite — testing is manual via the web UI and `var/logs/`

### Runtime State

All runtime state lives in `var/` (gitignored). The Flask server holds state in-memory and persists to `var/` on disk. Session snapshots under `var/instances/` capture the full state for save/load, including: chat log, documents, repos, tickets, logs, memberships, thoughts, roster, DM queue, events, background tasks, emails, memos, and recaps.

## CLI Reference

```bash
# Server
python main.py server [--port 5000] [--host 127.0.0.1] [--scenario tech-startup]

# Orchestrator (container-based)
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

# MCP server (required for orchestrator)
python main.py mcp-server [--port 5001] [--host 0.0.0.0]
                          [--flask-url http://127.0.0.1:5000]
                          [--scenario tech-startup]
```

## Environment Variables (.env)

Required:
```
CLAUDE_CODE_USE_VERTEX=1
CLOUD_ML_REGION=us-east5
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project>
```

Requires GCP application default credentials (`gcloud auth application-default login`).

## Common Development Tasks

- **Add a persona**: Add a character entry in `scenario.yaml`, create the character `.md` file, assign to channels/tiers/folders
- **Add a channel**: Add to `channels` and update `memberships` in `scenario.yaml`
- **Add an event**: Add to `events` list in `scenario.yaml` with `message`, `ticket`, `document`, or `email` actions
- **Add a new MCP tool**: Register in `lib/mcp_server.py`, add to `MCP_TOOL_NAMES` in `lib/container_orchestrator.py`
- **Modify prompt building**: Edit `lib/personas.py` (`build_v3_system_prompt`, `build_v3_turn_prompt`)
- **Add REST endpoints**: Edit `lib/webapp.py`
- **Add a new subsystem**: Create a module in `lib/`, wire it into `lib/webapp.py` (REST endpoints), `lib/mcp_server.py` (MCP tools), `lib/session.py` (save/load), and `lib/personas.py` (include state in prompts)
