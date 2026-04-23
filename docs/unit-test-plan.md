# Unit Test Plan

## Setup

```bash
make test           # create venv, install deps, run tests
make test-verbose   # same but with per-test output
make clean          # remove venv and caches
```

Uses `uv` for venv creation and dependency installation. Test dependencies
(`pytest`, `pytest-asyncio`) are declared in `pyproject.toml` under
`[project.optional-dependencies] test`.

## Current Coverage (Phase 1)

| Test File | Module | Tests | What's Covered |
|-----------|--------|-------|----------------|
| `test_docs.py` | `lib/docs.py` | 13 | `slugify` (unicode, truncation, edge cases), `get_accessible_folders` |
| `test_tickets.py` | `lib/tickets.py` | 4 | `generate_ticket_id` format, determinism, uniqueness |
| `test_gitlab.py` | `lib/gitlab.py` | 8 | `generate_commit_id`, `get_accessible_repos` filtering |
| `test_email.py` | `lib/email.py` | 11 | send, inbox, lookup, clear, snapshot/restore round-trip |
| `test_events.py` | `lib/events.py` | 14 | event pool CRUD, fire/log, init from scenario, snapshot/restore |
| `test_memos.py` | `lib/memos.py` | 14 | threads, posts, sorting, recent posts, delete, snapshot/restore |
| `test_blog.py` | `lib/blog.py` | 16 | posts, replies, update, delete, sorting, snapshot/restore |
| `test_agent_runner.py` | `lib/agent_runner.py` | 14 | model name/ID mapping, `format_duration` |

## Phased Expansion Plan

### Phase 2 — CLI & Scenario Loading
- `test_cli.py` — argument parsing for `server`, `chat`, `mcp-server` subcommands
- `test_scenario_loader.py` — YAML parsing, `_parse_frontmatter`, `load_scenario` (with tmp fixtures)

### Phase 3 — Prompt Building (pure text formatting)
- `test_personas.py` — `format_chat_history`, `_build_history_sections`, `build_docs_index`, `build_tickets_index`, `build_gitlab_index`

### Phase 4 — HTTP Client (mock requests)
- `test_chat_client.py` — all REST wrapper methods with mocked `requests.get`/`post`

### Phase 5 — File I/O (tmp_path fixtures)
- `test_tickets_io.py` — `init_tickets_storage`, `load_tickets_index`, `save_tickets_index`
- `test_gitlab_io.py` — `init_gitlab_storage`, `load_repos_index`, `save_repos_index`
- `test_session.py` — `save_session`, `load_session`, `list_sessions`

### Phase 6 — Async & Executors (pytest-asyncio + mocking)
- `test_task_executor.py` — task submission, ID generation, worker lifecycle
- `test_agent_runner_async.py` — `run_agent_for_response` with mocked SDK client

### Phase 7 — Infrastructure Utilities
- `test_container_orchestrator.py` — `_collect_env_vars`, DM queue functions
- `test_mcp_server.py` — audit recording, telemetry, individual tool logic

## Module Testability Summary

| Module | Pure Logic | Needs Mocking | Priority |
|--------|-----------|---------------|----------|
| `docs.py` | High | None | Done |
| `tickets.py` | ID gen only | Filesystem for I/O | Done (pure) |
| `gitlab.py` | ID + filtering | Filesystem for I/O | Done (pure) |
| `email.py` | All in-memory | None | Done |
| `events.py` | All in-memory | None | Done |
| `memos.py` | All in-memory | None | Done |
| `blog.py` | All in-memory | None | Done |
| `agent_runner.py` | Mappings + formatting | SDK for async | Done (pure) |
| `cli.py` | All pure | None | Phase 2 |
| `scenario_loader.py` | Frontmatter parsing | Filesystem | Phase 2 |
| `personas.py` | Text formatting | Filesystem for instructions | Phase 3 |
| `chat_client.py` | None (all HTTP) | `requests` | Phase 4 |
| `session.py` | Some | Filesystem | Phase 5 |
| `task_executor.py` | ID gen | SDK + threads | Phase 6 |
| `container_orchestrator.py` | Minimal | podman + subprocess | Phase 7 |
| `mcp_server.py` | Audit/telemetry | FastMCP + httpx | Phase 7 |
| `webapp/` | None | Flask test client | Future |
