# Multi-Agent Organization

A simulated software company where 11 AI personas collaborate through a Slack-like chat system. A human operator drops a message into a channel — a feature request, a customer escalation, a pricing question — and an entire organization responds: engineers dig into feasibility, the PM scopes requirements, sales positions the value, finance models the deal, and leadership makes the call. All in real time, all visible in a web UI.

Built on the Claude Agent SDK with persistent sessions per persona, a Flask chat server with SSE, a shared document workspace, a mock GitLab for code hosting, and a ticket tracking system.

## What It Looks Like

```
Terminal 1 (server):
$ python main.py server
Chat log cleared on startup
Channels initialized: ['#devops', '#engineering', '#general', ...]
GitLab storage ready: gitlab/  (0 existing repos)

Terminal 2 (agents):
$ python main.py chat
Orchestrator starting
  Personas: Sarah (PM), Marcus (Eng Manager), Priya (Architect), ...
  Model: sonnet
Connected to chat server

=== Wave 1/5 — triggered: ['#general'] ===
--- Tier 1: Alex (Senior Eng), Casey (DevOps), Jordan (Support Eng), Taylor (Sales Eng) ---
  Alex (Senior Eng): posted to #general (847 chars)
  Casey (DevOps): posted to #general (612 chars)
  Jordan (Support Eng): PASS
  Taylor (Sales Eng): posted to #sales (1204 chars)
--- Tier 2: Marcus (Eng Manager), Priya (Architect), Sarah (PM) ---
  Marcus (Eng Manager): posted to #general (523 chars)
  ...
```

The web UI at `http://localhost:5000` shows a tabbed interface with Chat (multi-channel Slack), Docs (Google Docs-style workspace), and GitLab (repository browser).

## The Team

| Persona | Role | Tier | Default Channels |
|---------|------|------|-----------------|
| **Sarah** | Product Manager | 2 | #general, #engineering, #sales, #support, #leadership, #marketing, #devops |
| **Marcus** | Engineering Manager | 2 | #general, #engineering, #support, #devops |
| **Priya** | Software Architect | 2 | #general, #engineering |
| **Alex** | Senior Engineer | 1 | #general, #engineering |
| **Jordan** | Support Engineer | 1 | #general, #engineering, #support, #support-external |
| **Taylor** | Sales Engineer | 1 | #general, #sales, #sales-external, #marketing |
| **Dana** | CEO | 3 | #general, #leadership, #sales, #marketing |
| **Morgan** | CFO | 3 | #general, #leadership, #sales |
| **Riley** | Marketing | 2 | #general, #marketing, #sales, #sales-external |
| **Casey** | DevOps Engineer | 1 | #general, #devops, #engineering, #support |
| **Nadia** | Project Manager | 2 | #general, #engineering, #support, #leadership, #devops, #sales, #marketing |

**Tiers** control response ordering. Tier 1 (ICs) responds first, closest to the work. Tier 2 (managers/leads) sees Tier 1's responses before deciding whether to weigh in. Tier 3 (executives) sees everything before making strategic calls. Within a tier, agents run sequentially so each sees what the previous agent said.

## Architecture

```
main.py
  |
  |-- server -----> Flask webapp (webapp.py)
  |                   REST API + SSE + Web UI
  |                   In-memory: messages, channels, docs, repos, tickets
  |
  |-- chat -------> Orchestrator (orchestrator.py)
                      Poll for new messages
                      Trigger agents by channel membership
                      Parse JSON responses (regex fallback)
                      Execute commands, post messages to channels
                      |
                      +--> AgentPool (agent_runner.py)
                      |      One persistent ClaudeSDKClient per persona
                      |      Sessions stay open across turns
                      |      Model: sonnet | opus | haiku (Vertex AI)
                      |
                      +--> ChatClient (chat_client.py)
                      |      HTTP client for the webapp REST API
                      |
                      +--> Personas (personas.py)
                      |      Prompt builder: initial + per-turn
                      |      Channel membership, doc/repo/ticket index
                      |
                      +--> ResponseSchema (response_schema.py)
                             JSON parser + command normalizer
```

The server and orchestrator run as separate processes. The orchestrator polls for new human messages, determines which channels were triggered, and runs agents in tiered waves. Each agent gets a prompt containing the full chat history (filtered to their channels), a list of existing documents, repos, and tickets, channel membership info, and an instruction to respond in structured JSON (or PASS).

## Quick Start

**Requirements:** Python 3.13+, Google Cloud credentials for Vertex AI

1. **Clone and install:**
   ```bash
   git clone <repo-url>
   cd multi-agent-organization
   pip install -e .
   ```

2. **Create `.env`:**
   ```
   CLAUDE_CODE_USE_VERTEX=1
   CLOUD_ML_REGION=us-east5
   ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
   ```

3. **Start the server** (terminal 1):
   ```bash
   python main.py server
   ```

4. **Start the agents** (terminal 2):
   ```bash
   python main.py chat
   ```

5. **Open the UI** at `http://localhost:5000`, type a message, and watch the team respond.

### CLI Options

```
python main.py server [--scenario tech-startup]
                      [--port 5000] [--host 127.0.0.1]

python main.py chat [--scenario tech-startup]
                     [--model sonnet|opus|haiku]
                     [--personas pm,senior,architect]
                     [--max-rounds 5]
                     [--max-auto-rounds 0]
                     [--poll-interval 5.0]
                     [--server-url http://127.0.0.1:5000]
```

Use `--scenario` to load a different scenario (default: `tech-startup`). Scenarios are defined in `scenarios/<name>/scenario.yaml` with per-character markdown files in `scenarios/<name>/characters/`.

Use `--personas` to run a subset of the team for faster iteration or lower cost. `--max-auto-rounds` limits how many autonomous continuation rounds agents can run after the initial trigger (0 = unlimited).

## How It Works

### Message Flow

1. Human types a message in the web UI (any channel)
2. Orchestrator detects the new message via polling
3. Agents in that channel are grouped by tier
4. For each tier, agents run sequentially:
   - Full chat history is re-fetched (so each agent sees prior responses)
   - A turn prompt is built with history, docs, repos, tickets, and channel membership
   - The agent responds with a JSON object or passes
   - The orchestrator parses the JSON, executes any commands, and posts messages to channels
   - If JSON parsing fails, a regex-based fallback parser handles the response
5. If agents posted to new channels, those become triggers for the next wave
6. Waves repeat up to `--max-rounds`
7. After waves complete, autonomous continuation runs until agents quiesce or `--max-auto-rounds` is reached

### Agent Response Format

Agents respond with a single JSON object per turn. The orchestrator parses the JSON to extract commands and channel-routed messages.

```json
{"action": "respond", "messages": [...], "commands": [...]}
{"action": "pass"}
{"action": "ready"}
```

**Multi-channel messages** (post different content to different channels):
```json
{
  "action": "respond",
  "messages": [
    {"channel": "#engineering", "text": "The implementation will need a new middleware layer."},
    {"channel": "#sales-external", "text": "Yes, we support custom rate limits on Enterprise plans."}
  ]
}
```

**Commands** (structured operations the orchestrator executes):
```json
{
  "action": "respond",
  "messages": [{"channel": "#general", "text": "I've set up the repo and created a ticket."}],
  "commands": [
    {"type": "doc", "action": "CREATE", "params": {"folder": "engineering", "title": "API Design", "content": "## Endpoints\n- GET /users\n- POST /users"}},
    {"type": "gitlab", "action": "REPO_CREATE", "params": {"name": "api-service", "description": "Main API"}},
    {"type": "gitlab", "action": "COMMIT", "params": {"project": "api-service", "message": "Add rate limiting", "files": [{"path": "config/limits.yaml", "content": "default: 100/min"}, {"path": "src/middleware.py", "content": "def rate_limit(request):\n    pass"}]}},
    {"type": "tickets", "action": "CREATE", "params": {"title": "Implement rate limiting", "assignee": "Alex (Senior Eng)", "priority": "high", "description": "Add per-endpoint rate limits."}},
    {"type": "channel", "action": "JOIN", "params": {"channel": "#devops"}}
  ]
}
```

**Command types:**

| Type | Actions |
|------|---------|
| `doc` | `CREATE`, `UPDATE`, `APPEND`, `READ`, `SEARCH` |
| `gitlab` | `REPO_CREATE`, `COMMIT`, `TREE`, `FILE_READ`, `LOG` |
| `tickets` | `CREATE`, `UPDATE`, `COMMENT`, `DEPENDS`, `LIST` |
| `channel` | `JOIN` |

A regex-based fallback parser handles responses from agents that produce the legacy `<<<>>>` command format instead of JSON.

### Channels

Nine channels with two visibility levels:

| Channel | Type | Purpose |
|---------|------|---------|
| #general | Internal | Company-wide discussion |
| #engineering | Internal | Engineering team |
| #sales | Internal | Sales team |
| #support | Internal | Support team |
| #leadership | Internal | Executive leadership |
| #marketing | Internal | Marketing team |
| #devops | Internal | DevOps & infrastructure |
| #sales-external | **External** | Customer-facing sales |
| #support-external | **External** | Customer-facing support |

External channels are visible to the customer. Agents adjust their tone accordingly — candid internally, professional externally.

### Document Folders

Documents are organized into folders with persona-based access control:

- **shared/** and **public/** — accessible to everyone
- **engineering/**, **sales/**, **support/**, **marketing/**, **devops/** — department folders
- **leadership/** — CEO, CFO, PM only
- **sarah/**, **marcus/**, etc. — personal folders, one persona each

### Persona Instructions

Each persona has a skill file at `.claude/skills/<skill-name>/SKILL.md` defining:

- **Behavioral guidelines** — what to focus on, how to think
- **Communication style** — tone, phrases, structure
- **When to PASS** — when to stay silent instead of adding noise

Example (Senior Engineer):
```markdown
- Think about implementation specifics: data structures, algorithms, error handling
- Identify edge cases and failure modes others might miss
- Suggest testing strategies: unit tests, integration tests, load tests
- Keep responses to 2-4 paragraphs maximum

Respond PASS if:
- The discussion is about business strategy or high-level prioritization
- The implementation details have already been covered adequately
```

## Project Structure

```
.
├── main.py                          # Entry point (server / chat)
├── pyproject.toml                   # Dependencies and metadata
├── .env                             # Vertex AI credentials (not committed)
├── AGENT_LOOP.md                    # Detailed architecture documentation
├── lib/
│   ├── cli.py                       # Argument parser
│   ├── webapp.py                    # Flask server, REST API, SSE, web UI
│   ├── orchestrator.py              # Event loop, command execution, tiered dispatch
│   ├── agent_runner.py              # Persistent Claude SDK sessions (AgentPool)
│   ├── chat_client.py               # HTTP client for the webapp API
│   ├── personas.py                  # Persona registry, prompt builder (config loaded from scenario)
│   ├── scenario_loader.py           # Reads scenario.yaml, populates module-level config
│   ├── response_schema.py           # JSON response parser + command normalizer
│   ├── docs.py                      # Document storage utilities, folder access
│   ├── gitlab.py                    # GitLab mock storage utilities
│   └── tickets.py                   # Ticket storage + ID generation
├── scenarios/
│   └── tech-startup/                # Default scenario
│       ├── scenario.yaml            # Channels, tiers, folders, character refs
│       └── characters/              # Per-character role prompts (.md)
├── .claude/skills/                  # Claude Code skill definitions (also used as legacy prompts)
├── docs/                            # Runtime — document storage (folders + index)
├── gitlab/                          # Runtime — git repo storage
├── tickets/                         # Runtime — ticket storage
├── logs/                            # Runtime — agent session logs
└── chat.log                         # Runtime — message persistence
```

## REST API

### Messages
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/messages` | List messages (`?since=ID`, `?channels=...`) |
| POST | `/api/messages` | Post a message (`{sender, content, channel}`) |
| POST | `/api/messages/clear` | Clear all messages |
| GET | `/api/messages/stream` | SSE stream for real-time updates |

### Channels
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/channels` | List channels with members |
| POST | `/api/channels/<name>/join` | Join a channel (`{persona}`) |
| POST | `/api/channels/<name>/leave` | Leave a channel (`{persona}`) |

### Documents
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/docs` | List documents (`?folder=...`) |
| POST | `/api/docs` | Create document (`{title, content, author, folder}`) |
| GET | `/api/docs/search` | Search documents (`?q=...&folders=...`) |
| GET | `/api/docs/<folder>/<slug>` | Read a document |
| PUT | `/api/docs/<folder>/<slug>` | Replace document content |
| POST | `/api/docs/<folder>/<slug>/append` | Append to a document |
| DELETE | `/api/docs/<folder>/<slug>` | Delete a document |

### GitLab
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/gitlab/repos` | List repositories |
| POST | `/api/gitlab/repos` | Create repository (`{name, description, author}`) |
| GET | `/api/gitlab/repos/<project>/tree` | File tree (`?path=subdir`) |
| GET | `/api/gitlab/repos/<project>/file` | Read file (`?path=...`) |
| POST | `/api/gitlab/repos/<project>/commit` | Commit files (`{message, files, author}`) |
| GET | `/api/gitlab/repos/<project>/log` | Commit history (newest first) |

### Tickets
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tickets` | List tickets (`?status=...`, `?assignee=...`) |
| POST | `/api/tickets` | Create ticket (`{title, description, priority, assignee, author, blocked_by}`) |
| GET | `/api/tickets/<id>` | Get a ticket |
| PUT | `/api/tickets/<id>` | Update ticket status/assignee |
| POST | `/api/tickets/<id>/comment` | Add a comment |
| POST | `/api/tickets/<id>/depends` | Add a dependency |

### Folders
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/folders` | List folders with access info |

## Customization

### Adding a Persona

1. Add an entry to `PERSONAS` in `lib/personas.py`
2. Add channel memberships in `DEFAULT_MEMBERSHIPS`
3. Assign a response tier in `RESPONSE_TIERS`
4. Create a skill file at `.claude/skills/<skill-name>/SKILL.md`
5. Add folder access rules in `DEFAULT_FOLDER_ACCESS` in `lib/docs.py`
6. Optionally add a personal folder in `DEFAULT_FOLDERS` in `lib/docs.py`

### Adding a Channel

1. Add to `DEFAULT_CHANNELS` in `lib/personas.py`
2. Add members to `DEFAULT_MEMBERSHIPS`
3. The webapp and orchestrator pick it up automatically

### Changing Models

Use `--model` to switch between models for all agents:
```bash
python main.py chat --model haiku    # Fast and cheap, good for testing
python main.py chat --model sonnet   # Default, balanced
python main.py chat --model opus     # Most capable
```

## Dependencies

- **claude-agent-sdk** — persistent Claude sessions via Vertex AI
- **flask** — web server and REST API
- **python-dotenv** — environment variable management
- **requests** — HTTP client
