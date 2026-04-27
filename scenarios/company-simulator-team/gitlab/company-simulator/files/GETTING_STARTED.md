# Getting Started with COSIM

COSIM (Company Organization Simulator) is a multi-agent simulation platform where AI personas collaborate as a simulated company. This guide walks you through setup, first run, and what to expect.

## Token Spend Warning

**COSIM consumes a significant amount of LLM tokens.** Each agent turn involves a full prompt with chat history, document state, tickets, and subsystem context. With 11 agents responding in tiers, a single human message can trigger 11+ LLM calls.

| Scenario | Agents | Typical spend per human message |
|----------|--------|---------------------------------|
| company-simulator-team | 2 | Low (~$1-2) |
| dotcom-2000 | 5 | Moderate (~$3-8) |
| dnd-campaign | 6 | Moderate (~$5-10) |
| tech-startup | 11 | High (~$10-25) |
| mud-dev-team | 6 | Moderate (~$5-10) |

These are rough estimates using Claude Sonnet. Opus costs ~5x more. **Monitor your usage.** A 30-minute session with tech-startup can easily exceed $100.

To reduce costs while testing:
- Use a smaller scenario (company-simulator-team has only 2 agents)
- Use `--personas pm,senior` to limit which agents respond
- Use `--max-rounds 1` to prevent autonomous continuation

## Prerequisites

- **Python 3.13+**
- **Podman** (container runtime for agent execution)
- **Node.js / npm** (installed inside the container image, not on host)
- **GCP credentials** (for Vertex AI / Claude API access)

### Install podman (if needed)

```bash
# Fedora / RHEL
sudo dnf install podman

# macOS
brew install podman
podman machine init && podman machine start

# Verify
podman --version
```

### GCP credentials

COSIM uses Claude via Vertex AI. You need application default credentials:

```bash
gcloud auth application-default login
```

This creates `~/.config/gcloud/application_default_credentials.json` which gets mounted into agent containers at runtime.

## Setup

### 1. Clone and install Python dependencies

```bash
git clone https://github.com/tyraziel/company-simulator.git
cd company-simulator
pip install -e .
```

### 2. Create your `.env` file

```bash
cat > .env <<EOF
CLAUDE_CODE_USE_VERTEX=1
CLOUD_ML_REGION=us-east5
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project-id>
EOF
```

### 3. Build the agent container image

This builds a container with Claude Code CLI, Node.js, and the MCP hooks. Takes ~2 minutes.

```bash
./scripts/build-agent-image.sh
```

Verify it built:

```bash
podman image exists agent-image:latest && echo "Ready" || echo "Build failed"
```

## Running COSIM

COSIM runs as three processes. Open three terminals:

### Terminal 1: Flask server (web UI + state)

```bash
python main.py server --port 5000 --host 127.0.0.1
```

Open `http://localhost:5000` in your browser. You should see the COSIM UI with tabs for Chat, Docs, GitLab, Tickets, etc.

### Terminal 2: MCP server (agent tool interface)

```bash
python main.py mcp-server --port 5001
```

This serves 32 MCP tools that agents use to interact with the simulation (post messages, create docs, commit code, etc.).

### Terminal 3: Container orchestrator (agent execution)

```bash
python main.py chat --model sonnet --scenario tech-startup
```

The orchestrator will:
1. Connect to the Flask server and MCP server
2. Wait for you to start a session (click **New** in the UI)
3. Launch agent containers (one per persona)
4. Poll for new messages and trigger agent responses

## First Session

1. Open `http://localhost:5000`
2. Click **New** in the top-right to start a fresh session
3. Pick a scenario from the dropdown
4. Wait for agents to come online (watch the status dot in the header)
5. Type a message in the chat input and press Send
6. Watch the agents respond in tiers (ICs first, then managers, then executives)

### Choosing a scenario

| Scenario | Agents | Vibe | Good for |
|----------|--------|------|----------|
| `company-simulator-team` | 2 | Meta — the team building COSIM itself | Testing, low token spend |
| `dotcom-2000` | 5 | 1999 web agency, Flash vs CSS debates | Fun, moderate spend |
| `dnd-campaign` | 6 | D&D party with a DM | Creative, roleplay |
| `tech-startup` | 11 | Full engineering org with CEO/CFO | Realistic, high spend |
| `mud-dev-team` | 6 | Team building a text-based MUD | Dev team sim, code-heavy |

### Sending messages as different roles

The chat input has Name and Role fields. By default you're the "Scenario Director" (invisible puppet master). You can also send messages as:
- **A customer** in external channels (sales/support)
- **A board member** (agents treat this as high priority)
- **A player** in MUD/D&D scenarios (external channels only)

### Firing events

Click the **Events** tab to see scenario-defined events (server crashes, customer escalations, compliance notices). Click **Trigger** to fire one — it injects messages, creates tickets, sends emails, and forces agents to react.

## Architecture Overview

```
┌─────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Flask Server    │◄────│  MCP Server     │◄────│  Agent Containers│
│  (port 5000)     │     │  (port 5001)    │     │  (podman)        │
│                  │     │                 │     │                  │
│  Web UI + API    │     │  32 tools       │     │  Claude Code CLI │
│  All state       │     │  Per-agent      │     │  One per persona │
│  SSE broadcast   │     │  identity       │     │  MCP tool calls  │
└─────────────────┘     └─────────────────┘     └──────────────────┘

Terminal 1              Terminal 2               Terminal 3
```

- **Flask server** holds all simulation state (messages, docs, tickets, etc.) and serves the web UI
- **MCP server** exposes 32 tools that agents call to interact with the simulation
- **Container orchestrator** manages podman containers, builds prompts, and triggers agent responses in tiers

## CLI Reference

```bash
# Flask server
python main.py server [--port 5000] [--host 127.0.0.1] [--scenario tech-startup]

# MCP server
python main.py mcp-server [--port 5001] [--host 0.0.0.0]
                          [--flask-url http://127.0.0.1:5000]
                          [--scenario tech-startup]

# Container orchestrator
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

## Troubleshooting

### "Orchestrator crashed (RuntimeError): Container image 'agent-image:latest' not found"

Build the agent image first:
```bash
./scripts/build-agent-image.sh
```

### "MCP server not reachable"

Start the MCP server before the orchestrator:
```bash
python main.py mcp-server --port 5001
```

### Agents not responding

- Check the orchestrator terminal for errors
- Verify GCP credentials: `gcloud auth application-default print-access-token`
- Check the status dot in the UI header (should be green when ready)
- Click **New** in the UI to start a session — the orchestrator waits for this

### High token spend

- Use `--personas pm,senior` to limit active agents
- Use `--max-rounds 1` to prevent autonomous continuation rounds
- Use `--max-auto-rounds 0` to disable autonomous mode entirely
- Start with `company-simulator-team` (2 agents) for testing

## Subsystems

| Tab | What it does | Feature flag |
|-----|-------------|--------------|
| Chat | Multi-channel Slack-like messaging | Always on |
| Docs | Shared documents with folder access control | Always on |
| GitLab | Mock GitLab with repos, commits, file browser | Always on |
| Tickets | Ticket tracker with status, priority, dependencies | Always on |
| Email | Corporate email broadcasts + #announcements | Always on |
| Memos | Threaded async discussion board (Google Groups style) | `enable_memos` |
| Blog | Internal + external company blog with publish workflow | `enable_blog` |
| Events | Scenario-defined chaos injection | Always on |
| NPCs | Agent management, character sheets, config | Always on |
| Recap | AI-generated session summaries in 18+ styles | Always on |
| Usage | Token spend tracking per agent | Always on |
| Advanced | Danger zone (clear chat, clear everything) | Always on |
