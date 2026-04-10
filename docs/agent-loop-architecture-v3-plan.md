# v3 Agent Loop Architecture — Phase 1 Implementation Plan

## Overview

Phase 1 delivers two foundational components for the v3 architecture:

1. **MCP Server** (`lib/mcp_server.py`) — Async process exposing 32 simulation tools via MCP-over-SSE, proxying to the Flask server
2. **Container Image** (`Dockerfile.agent`) — Minimal Docker image with Claude Code + MCP config + telemetry hooks

All existing v2 functionality continues working unchanged. The MCP server is additive.

## Architecture

### Per-Agent MCP Mount

Each agent gets its own FastMCP server instance mounted at `/agents/<key>/` on a parent Starlette app. Agent identity is baked into closures at construction time — no auth tokens or headers needed.

```
GET  /agents/pm/sse           → PM's SSE connection
POST /agents/pm/messages/     → PM's message endpoint
GET  /agents/senior/sse       → Senior's SSE connection
POST /agents/senior/messages/ → Senior's message endpoint
GET  /health                  → Health check
POST /api/telemetry/model-end → Hook telemetry
POST /api/telemetry/tool-start → Hook telemetry
GET  /api/telemetry           → Aggregated usage data
GET  /api/audit               → Audit log
```

### 32 MCP Tools

| Category | Tools |
|----------|-------|
| Communication (6) | `post_message`, `get_messages`, `send_dm`, `get_my_dms`, `join_channel`, `get_channel_members` |
| Documents (5) | `create_doc`, `update_doc`, `read_doc`, `search_docs`, `list_docs` |
| GitLab (5) | `create_repo`, `commit_files`, `read_file`, `list_repo_tree`, `get_repo_log` |
| Tickets (4) | `create_ticket`, `update_ticket`, `comment_on_ticket`, `list_tickets` |
| Memos (2) | `create_memo`, `reply_to_memo` |
| Blog (2) | `create_blog_post`, `reply_to_blog` |
| Email (2) | `send_email`, `get_emails` |
| Meta (6) | `get_my_channels`, `get_my_tickets`, `get_recent_activity`, `whoami`, `who_is`, `signal_done` |

### Access Control

- **Channels**: membership check from scenario config
- **Documents**: folder access check from scenario config
- **GitLab**: repo access check (optional, from scenario config)
- **Identity**: author/sender auto-set from display name (unforgeable)

### Proxy Pattern

All tools use `httpx.AsyncClient` to call Flask REST endpoints. Client created/closed via Starlette lifespan context manager.

### Telemetry & Audit

- Audit: in-memory ring buffer (10K entries), written to `var/logs/mcp_audit.log`
- Telemetry: aggregated per-agent token/cost data from Claude Code hooks

## Files

| File | Purpose |
|------|---------|
| `lib/mcp_server.py` | MCP server implementation |
| `Dockerfile.agent` | Container image for agent processes |
| `agent-hooks.json` | Claude Code telemetry hooks |
| `lib/cli.py` | Modified: `mcp-server` subcommand |
| `main.py` | Modified: `mcp-server` handler |
| `pyproject.toml` | Modified: new dependencies |

## Phase 1 Limitations

- `signal_done()` is a no-op stub (Phase 3 notification bus will consume it)
- DMs use hidden channels `#dm-{sender}-{recipient}` as a workaround
- No container orchestration yet (Phase 2)
