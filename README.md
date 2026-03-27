# Multi-Agent Organization

A simulated software company where 10 AI personas collaborate through a Slack-like chat system. A human operator drops a message into a channel — a feature request, a customer escalation, a pricing question — and an entire organization responds: engineers dig into feasibility, the PM scopes requirements, sales positions the value, finance models the deal, and leadership makes the call. All in real time, all visible in a web UI.

Built on the Claude Agent SDK with persistent sessions per persona, a Flask chat server with SSE, a shared document workspace, and a mock GitLab for code hosting.

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

**Tiers** control response ordering. Tier 1 (ICs) responds first, closest to the work. Tier 2 (managers/leads) sees Tier 1's responses before deciding whether to weigh in. Tier 3 (executives) sees everything before making strategic calls. Within a tier, agents run sequentially so each sees what the previous agent said.

## Architecture

```
main.py
  |
  |-- server -----> Flask webapp (webapp.py)
  |                   REST API + SSE + Web UI
  |                   In-memory: messages, channels, docs, gitlab repos
  |
  |-- chat -------> Orchestrator (orchestrator.py)
                      Poll for new messages
                      Trigger agents by channel membership
                      Parse commands from agent responses
                      Post responses back to channels
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
                             Prompt builder: initial + per-turn
                             Channel membership, doc index, repo index
```

The server and orchestrator run as separate processes. The orchestrator polls for new human messages, determines which channels were triggered, and runs agents in tiered waves. Each agent gets a prompt containing the full chat history (filtered to their channels), a list of existing documents and repos, channel membership info, and an instruction to respond or PASS.

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
python main.py server [--port 5000] [--host 127.0.0.1]

python main.py chat [--model sonnet|opus|haiku]
                     [--personas pm,senior,architect]
                     [--max-rounds 5]
                     [--poll-interval 5.0]
                     [--server-url http://127.0.0.1:5000]
```

Use `--personas` to run a subset of the team for faster iteration or lower cost.

## How It Works

### Message Flow

1. Human types a message in the web UI (any channel)
2. Orchestrator detects the new message via polling
3. Agents in that channel are grouped by tier
4. For each tier, agents run sequentially:
   - Full chat history is re-fetched (so each agent sees prior responses)
   - A turn prompt is built with history, docs, repos, and channel membership
   - The agent responds or says PASS
   - Commands embedded in the response are extracted and executed
   - The cleaned response is posted to the appropriate channel(s)
5. If agents posted to new channels, those become triggers for the next wave
6. Waves repeat up to `--max-rounds`

### Agent Commands

Agents can embed structured commands in their responses. The orchestrator extracts and executes them before posting the cleaned text.

**Documents** (Google Docs-style shared workspace):
```
<<<DOC:CREATE folder="engineering" title="API Design">>>
## Endpoints
- GET /users
- POST /users
<<<END_DOC>>>

<<<DOC:SEARCH query="rate limiting" folders="engineering,shared"/>>>
```

**GitLab** (simplified code hosting):
```
<<<GITLAB:REPO_CREATE name="api-service" description="Main API"/>>>

<<<GITLAB:COMMIT project="api-service" message="Add rate limiting">>>
FILE: config/limits.yaml
default: 100/min
FILE: src/middleware.py
def rate_limit(request):
    pass
<<<END_GITLAB>>>

<<<GITLAB:TREE project="api-service"/>>>
<<<GITLAB:FILE_READ project="api-service" path="src/middleware.py"/>>>
<<<GITLAB:LOG project="api-service"/>>>
```

**Channel management**:
```
<<<CHANNEL:JOIN #devops>>>
```

**Multi-channel responses** (post different content to different channels):
```
[#engineering]
The implementation will need a new middleware layer.

[#sales-external]
Yes, we support custom rate limits on Enterprise plans.
```

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
├── lib/
│   ├── cli.py                       # Argument parser
│   ├── webapp.py                    # Flask server, REST API, SSE, web UI
│   ├── orchestrator.py              # Event loop, command parsing, tiered execution
│   ├── agent_runner.py              # Persistent Claude SDK sessions (AgentPool)
│   ├── chat_client.py               # HTTP client for the webapp API
│   ├── personas.py                  # Persona registry, prompt builder
│   ├── docs.py                      # Document storage utilities, folder access
│   └── gitlab.py                    # GitLab mock storage utilities
├── .claude/skills/
│   ├── product-manager/SKILL.md
│   ├── engineering-manager/SKILL.md
│   ├── software-architect/SKILL.md
│   ├── senior-engineer/SKILL.md
│   ├── support-engineer/SKILL.md
│   ├── sales-engineer/SKILL.md
│   ├── ceo/SKILL.md
│   ├── cfo/SKILL.md
│   ├── marketing/SKILL.md
│   └── devops-engineer/SKILL.md
├── docs/                            # Runtime — document storage
├── gitlab/                          # Runtime — git repo storage
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
5. Optionally add a personal folder in `lib/docs.py`

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
