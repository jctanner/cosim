# To Build Later

---

## Scenarios vs. Instances

The fundamental data model for the simulator separates **templates** from **playthroughs**.

### Scenario (Template)

A scenario is an immutable, shareable definition of an organizational setup. It contains everything needed to start a simulation from scratch.

```
scenarios/
  tech-startup/
    scenario.yaml                  # channels, tiers, response rules, metadata
    characters/
      sarah-pm.CS.md               # character sheet (NRSP format)
      sarah-pm.NPC.md              # hidden directives / secret motives
      marcus-engmgr.CS.md
      ...
    docs/                          # pre-seeded documents for this scenario
```

A scenario defines:
- Who exists (characters + their sheets)
- How the org is structured (channels, tiers, folder access)
- What context exists at the start (pre-seeded docs)
- What tickets/tasks exist at the start (pre-seeded tickets, if any)
- What hidden dynamics are in play (NPC secret motives)

Scenarios are versioned, shared, and never modified by a running simulation.

### Instance (Playthrough)

An instance is a running or saved playthrough of a scenario. It contains all accumulated state from an active simulation.

```
instances/
  tech-startup--consulting-run-2026-03-27/
    metadata.json                  # name, scenario ref, timestamps, paused state
    chat_log.jsonl                 # all messages
    docs/                          # documents created during play
    gitlab/                        # repos created during play
    tickets/                       # tickets created during play (index + data)
    memberships.json               # channel membership overrides from defaults
    whispers.json                  # scenario director mode injections (if any)
    roster.json                    # active NPCs — who's been hired/fired (see below)

  tech-startup--new-hire-run-2026-03-28/
    metadata.json
    ...
```

An instance captures:
- Which scenario it came from
- All messages exchanged
- All documents and repos created
- All tickets created, updated, and commented on
- Channel membership changes (agents joining/leaving channels)
- NPC roster changes (hires and fires — see NPC Management below)
- Scenario Director mode whispers
- Current state (running, paused, completed)

**Resuming an instance:** Load the scenario template, overlay the instance state, spin up agents. They get full chat history via `build_turn_prompt` and pick up where you left off. The loss is internal reasoning from prior sessions — not facts.

**Multiple instances of one scenario:** You can have several saved playthroughs of the same scenario, each representing a different path through the same org setup. "What if I came in as a new hire instead of a consultant?"

### Open Questions

- **Instance naming:** Auto-generated from scenario + date, or user-provided?
- **Instance branching:** Should you be able to fork an instance at a save point and explore two paths? (Like NRSP's `TimelineType: Branch`)
- **Instance cleanup:** Auto-prune old instances? Size limits?
- **Scenario inheritance:** Could a scenario extend another? ("Same as tech-startup but add a QA team")

---

## NPC System & Scenario Architecture

This is the biggest architectural change on the table. It touches how agents are defined, configured, and managed — and sets up the foundation for everything else.

### Problem Statement

All agent configuration is currently hardcoded in Python (`personas.py`, `docs.py`, `.claude/skills/`). This makes it impossible to:
- Swap scenarios without editing code
- Share or version experiments independently
- Give the UI any control over agent behavior
- Use a structured format for character definitions

### Scenario Config (`scenario.yaml`)

Contains everything currently scattered across `personas.py` and `docs.py`:

```yaml
name: Tech Startup
description: A 10-person engineering org at a SaaS company

channels:
  "#general": { description: "Company-wide", external: false }
  "#engineering": { description: "Engineering team", external: false }
  "#support-external": { description: "Customer-facing support", external: true }
  ...

folders:
  shared: { type: shared, description: "Shared team documents" }
  engineering: { type: department, description: "Engineering department" }
  ...

tiers:
  1: [senior, support, sales, devops]      # ICs — respond first
  2: [engmgr, architect, pm, marketing]    # Managers — synthesize
  3: [ceo, cfo]                            # Executives — strategic only

characters:
  pm:
    display_name: "Sarah (PM)"
    sheet: characters/sarah-pm.CS.md
    npc_sheet: characters/sarah-pm.NPC.md
    channels: ["#general", "#engineering", "#sales", "#support", "#leadership", "#marketing", "#devops"]
    folders: [shared, public, engineering, sales, leadership, sarah, marketing, devops]
  ...
```

### Character Sheets (NRSP Format)

Adopt the [Narrative RPG Save Point Format](https://github.com/tyraziel/narrative-rpg-save-point-format/) for character definitions. Relevant file types:

- **`.CS.md` (Character Sheet)** — structured backstory, motivations, relationships, current state. Replaces the current `.claude/skills/` prompt files.
- **`.NPC.md` (NPC Extended Info)** — GM-only secret motives, hidden agendas, secret relationships. Enables "scenario director mode" scenarios.

**What maps well to corporate sim:**
- Backstory, Information, Motivations, Current State, Relationships

**What to skip or repurpose:**
- Stats → skills, expertise areas, years of experience
- Inventory → tools, system access, credentials
- Combat → skip entirely (or repurpose as "conflict style"?)

**Key question:** Should the agent's system prompt be *generated from* the CS file, or should the CS file *be* the prompt? Generating from it allows adding simulation instructions around the character data. Using it directly keeps things simpler but less flexible.

### What This Changes in the Code

`personas.py` becomes a **loader** instead of a **registry**:
- Reads `scenario.yaml` + character sheets at startup
- Builds the same data structures it currently hardcodes (PERSONAS, DEFAULT_CHANNELS, DEFAULT_MEMBERSHIPS, RESPONSE_TIERS, etc.)
- App takes a `--scenario tech-startup` flag (or env var)

The skill files in `.claude/skills/` are currently being misused — they're a Claude Code feature for giving Claude Code itself specialized capabilities. The simulator just reads them as raw text. Moving to CS.md files in the scenario folder fixes this.

### Migration Path

1. Extract current hardcoded values into `scenarios/tech-startup/scenario.yaml`
2. Move skill prompt files to `scenarios/tech-startup/characters/` as `.CS.md` files
3. Make `personas.py` a loader that reads the scenario config
4. Add `--scenario` flag to startup
5. Build NPC UI tab on top of the loaded data

### Open Questions

- **Prompt generation vs. direct use:** How do we turn a CS.md character sheet into an effective agent system prompt? Template? Direct injection? Hybrid?
- **Hot reload:** If you edit a character sheet mid-simulation, should the agent pick up changes on their next turn? Or require a restart?
- **Character sheet evolution:** Should the system update CS.md files as the simulation runs (e.g., updating "Current State" after major events)? This connects to session save/load.
- **Backward compatibility:** Keep supporting the current hardcoded setup as a fallback, or force migration?

---

## NPC Page (UI)

A new top-level tab for viewing and managing agents in the simulation.

### Two Contexts for NPC Configuration

There are two distinct moments when NPCs get configured, and the UI needs to support both:

**1. Scenario Setup (before play begins)**
- Defining who exists in the org from the start
- Setting backstories, motivations, relationships
- Configuring channels, tiers, folder access
- Writing NPC.md secret directives
- This is **authoring** — creating the template

**2. Active Instance (during play)**
- Hiring: spinning up a new agent mid-simulation
- Firing: removing an agent mid-simulation
- Reassigning: changing channels, tier, or role
- Scenario Director whispers: injecting hidden instructions
- This is **directing** — steering the running simulation

The NPC page needs to handle both, or they could be separate views (e.g., scenario editor vs. active roster).

### NPC Roster Management (Hire/Fire)

**Hiring (adding an NPC mid-instance):**
- User selects from a library of available character sheets, or creates a new one inline
- System spins up a new agent session (requires pause? or can we hot-add?)
- New agent gets injected into the chat context: "Please welcome [Name], who just joined as [Role]"
- Instance `roster.json` tracks the addition
- Channels, tier, and folder access must be assigned

**Firing (removing an NPC mid-instance):**
- User selects an active agent to remove
- System closes that agent's session
- Agent stops responding to future waves
- Instance `roster.json` tracks the removal
- Messages from the fired agent remain in history
- Tickets assigned to the fired agent need reassignment (prompt user or auto-assign)
- Optional: trigger an in-sim event ("Dana (CEO) has announced that [Name] is leaving the company")

**Reassigning (modifying an active NPC):**
- Change channel memberships (move someone to a different team)
- Change tier (promote/demote)
- Update their character sheet or NPC directives
- Question: does this require pause + session restart, or can it be applied on next turn?

### Roster State

The instance needs to track the delta between the scenario's default roster and the current state:

```json
{
  "removed": [
    { "key": "sales", "removed_at": 1711584000, "reason": "fired" }
  ],
  "added": [
    {
      "key": "qa",
      "display_name": "Jamie (QA Lead)",
      "sheet": "characters/jamie-qa.CS.md",
      "channels": ["#general", "#engineering"],
      "tier": 1,
      "added_at": 1711590000
    }
  ],
  "modified": [
    {
      "key": "senior",
      "changes": { "tier": 2, "channels_added": ["#leadership"] },
      "modified_at": 1711596000
    }
  ]
}
```

### Core Purpose — Revised

Given the hire/fire requirement, the NPC page needs to be a **full management panel** (option 4 from earlier), but built incrementally:

**Phase 1 — Visibility (read-only roster):**
- Grid or card layout of all active agents
- Each card shows: name, role, tier, channels, folder access
- Expandable to show character sheet content
- Status indicator: idle, responding, session error
- Color-coded by tier

**Phase 2 — Control (runtime management):**
- Hire button → opens character sheet editor or library picker
- Fire button → removes agent with confirmation
- Pause/mute individual agents
- Force an agent to respond to the current state

**Phase 3 — Configuration (deep editing):**
- Edit character sheet inline
- Modify channel/folder/tier assignments
- Scenario Director mode whisper input per agent
- NPC.md secret directive editor

### UI Layout

Should be a new top-level tab (alongside Chat, Docs, GitLab):

```
┌────────────────────────────────────────────────┐
│  Chat  │  Docs  │  GitLab  │  NPCs  │         │
├────────────────────────────────────────────────┤
│ [Hire NPC]                    Scenario: Tech.. │
├──────────┬─────────────────────────────────────┤
│ Tier 1   │  ┌──────────┐  ┌──────────┐        │
│ (ICs)    │  │ Alex     │  │ Jordan   │  ...   │
│          │  │ Senior   │  │ Support  │        │
│          │  │ #eng     │  │ #support │        │
│          │  │ ● idle   │  │ ● resp.. │        │
│          │  └──────────┘  └──────────┘        │
├──────────┤                                     │
│ Tier 2   │  ┌──────────┐  ┌──────────┐        │
│ (Mgrs)   │  │ Marcus   │  │ Priya    │  ...   │
│          │  │ Eng Mgr  │  │ Architect│        │
│          │  └──────────┘  └──────────┘        │
├──────────┤                                     │
│ Tier 3   │  ┌──────────┐  ┌──────────┐        │
│ (Execs)  │  │ Dana     │  │ Morgan   │        │
│          │  │ CEO      │  │ CFO      │        │
│          │  └──────────┘  └──────────┘        │
└──────────┴─────────────────────────────────────┘
```

Clicking a card expands to show the full character sheet, channel list, and action buttons (fire, whisper, reassign).

### Permissions Management

Each NPC's access to system resources should be viewable and editable from the NPC page:

**Channels:**
- View current channel memberships
- Add/remove from channels (drag-and-drop or checklist)
- Changes take effect on the agent's next turn
- Tracked in instance `roster.json` as modifications

**Document Folders:**
- View current folder access
- Grant/revoke access to specific folders
- Controls what the agent sees in the document index each turn

**GitLab Projects:**
- View which repos the agent can see/commit to
- Grant/revoke per-repo access
- Currently all agents see all repos — this would add scoping

**Tickets:**
- View tickets assigned to this agent
- Reassign tickets to/from this agent
- Scope which tickets an agent can see (e.g., only their team's)

All permission changes are instance-level overrides on top of the scenario defaults.

### Scenario Director Chat (Back-Channel)

A dedicated, private conversation area between the human operator and a specific agent. Different from whispers — this is a persistent back-channel where you can have an ongoing dialogue that other agents never see.

**Use cases:**
- Coaching an agent: "When the customer asks about pricing, anchor high"
- Extracting intel: "What do you really think about this deal?"
- Steering behavior: "Start pushing back on scope creep in the next meeting"
- Debugging: "Why did you say that in #engineering?"

**Implementation:**
- A chat panel on the expanded NPC card (or a modal)
- Messages stored per-agent in the instance (e.g., `scenario-director/sarah-pm.jsonl`)
- Injected into the agent's turn prompt as a `[SCENARIO_DIRECTOR]` context block that other agents don't receive
- Agent can respond to the scenario director in this channel and also act on instructions in public channels

### Agent Thoughts / Internal Monologue

Surface what agents are thinking but not saying. This data already exists in the SDK session logs (`logs/` directory) but isn't exposed in the UI.

**What to show:**
- The agent's internal reasoning for their last response
- What they considered saying but chose not to
- Their assessment of the current situation

**Implementation:**
- Parse the SDK session logs for reasoning content
- Display as a collapsible "Thoughts" section on the NPC card
- Read-only — this is observational, not interactive

### Scenario Director Mode (Whispers)

Distinct from scenario director chat — whispers are injected directives, not conversations:
- **Persistent** — applied to every future turn (added to NPC.md or equivalent)
- **One-shot** — injected into the next turn prompt only, then discarded
- **Conditional** — "if anyone asks about the budget, deflect"

These feed into the NPC.md secret motives or are injected as system-level context that other agents can't see. This is where the NRSP `.NPC.md` format fits perfectly.

### Why No DMs/PMs Between Agents

Decided against adding direct messages between agents. Reasons:

- The simulator's value is that **everything is observable** through channels
- DMs would create hidden state that's hard to track, debug, and replay
- If two agents need a private conversation, they can create a limited-membership internal channel (agents already have `<<<CHANNEL:JOIN>>>`)
- The human can always see all channels — DMs would break this panopticon model
- Scenario Director chat (human ↔ agent) is the only private channel, and that's intentional

### Open Questions

- **Character library:** Should there be a shared library of pre-built character sheets you can pull from when hiring? Or always author from scratch?
- **Hire mid-play without pause:** Can we hot-add an agent while others are running? Technically just opening a new session + adding to the orchestrator's persona list. Might be simpler than full pause.
- **Fire gracefully:** Should a fired agent get one last turn? ("I've been let go, here are my handoff notes...") Or just silently stop responding?
- **Agent mood/disposition:** Is there a concept of morale that changes based on what happens in the simulation? Could affect response tone.
- **Per-agent model:** Should different NPCs be able to use different Claude models? (Haiku for interns, Opus for CEO?)
- **NPC page vs. scenario editor:** Are these the same UI, or separate? Editing a scenario template is different from managing an active instance's roster.
- **Scenario Director chat persistence:** Should scenario director conversations carry over when resuming an instance? Probably yes — they're part of the agent's hidden context.
- **Thought capture granularity:** How much of the SDK reasoning log is useful to surface vs. noise?

---

## Session New/Save/Load

Create, snapshot, and restore simulation instances.

**New** — pick a scenario template, create a fresh instance (no messages, no docs, clean roster, spin up agents).

**Save** — snapshot the current running instance so it can be resumed later.

**Load** — restore a previously saved instance (spin down current agents, load state, spin up new agents).

**State to capture (on save):**
- Messages (`chat_log.jsonl`)
- Documents (`docs/` directory tree)
- GitLab repos (`gitlab/` directory tree)
- Tickets (`tickets/` directory + index)
- Channel memberships (dynamic joins beyond defaults)
- NPC roster changes (hires, fires, reassignments)
- Scenario Director mode whispers / NPC overrides
- Small metadata file (name, scenario ref, timestamps, state)

**State we can't capture (and why it's fine):**
- Agent Claude SDK sessions (internal reasoning/memory) — `build_turn_prompt` already injects full chat history + doc index each turn, so agents pick up context seamlessly on next response.

**Proposed implementation:**
- **"New"** button — presents scenario picker, creates a fresh `instances/<name>/` from the template, spins up agents
- **"Save"** button — prompts for a name (or auto-names), snapshots current state into the instance directory
- **"Load"** dropdown — lists saved instances, restores one (spins down current, loads state, spins up)
- Could also support export/import as a zip for sharing

**UI placement:** Header bar, next to the existing Chat/Docs/GitLab tabs or as a settings/menu dropdown.

---

## Pause/Resume & Scenario Switching

### Why Pause is Needed

Scenario switching without pause is destructive — agents mid-response would be killed, and you'd lose context with no way to come back. Pause is the prerequisite for clean scenario switching.

### Agent Lifecycle Costs

Based on `agent_runner.py`, here's what's involved:

**Spin-up (per agent):**
- Opens a `ClaudeSDKClient` session
- Sends the full initial role prompt (`build_initial_prompt`)
- Waits for the init response to be consumed
- Currently sequential — one agent at a time
- ~30-60+ seconds total for all agents

**Spin-down:**
- `AgentPool.close()` iterates and calls `__aexit__` on each client
- Must wait for any in-flight responses to complete
- Relatively fast once responses finish

**Cannot serialize:**
- Claude SDK sessions hold accumulated conversation context
- No way to freeze/thaw a session — closing it loses all internal reasoning history
- However: `build_turn_prompt` injects full chat history + doc index each turn, so agents recover context from the shared state. The loss is their internal "thinking" — not the facts.

### Proposed State Machine

```
[Running] ──Pause──→ [Pausing: agents finish current responses, new messages queue]
                              │
                              ▼
                        [Paused: all agents idle, no new waves triggered]
                              │
                    ┌─────────┼──────────┐
                    ▼         ▼          ▼
               Resume    Save State   Switch Scenario
                 │            │          │
                 ▼            ▼          ▼
            [Running]   [Paused]    Save current instance
                                    Spin down old agents
                                    Load new scenario/instance
                                    Spin up new agents
                                         │
                                         ▼
                                    [Running: new scenario/instance]
```

### UI Controls

- **Pause/Resume button** in header (toggles between states)
- Visual indicator: "Paused" banner or dimmed chat when paused
- **Scenario/instance switcher** — only enabled when paused
- Queued messages during pause are visible but grayed out, processed on resume

### Open Questions

- **Parallel spin-up:** Could agent sessions be opened concurrently instead of sequentially? Would cut startup time significantly.
- **Warm standby:** Could we keep multiple scenarios' agents alive simultaneously? Memory/API cost vs. switch speed tradeoff.
- **Partial pause:** Should you be able to pause specific agents (tiers, individuals) while others keep running?
- **Queue behavior:** When paused, should the human still be able to send messages (queued for when agents resume), or should input be blocked too?
- **Auto-save on pause:** Should pausing automatically snapshot the current state, so you always have a restore point?

---

## Completed

- ~~Scenario architecture~~ — config-driven YAML with per-character markdown files
- ~~Instance model~~ — instances/ directory with metadata, roster, thoughts
- ~~Session new/save/load~~ — full state persistence including roster changes
- ~~Pause/resume & scenario switching~~ — restart signaling, heartbeat commands
- ~~NPC page (visibility + control)~~ — tier-organized cards, online/offline, live status
- ~~NPC management (hire/fire)~~ — hot add/remove, two-phase firing, character templates
- ~~NPC configuration~~ — channels, folders, repos, tier, verbosity editing
- ~~Scenario Director chat~~ — per-agent private channels, two-way
- ~~Whisper system~~ — covered by director channels
- ~~Agent thoughts~~ — capture, history, search, detail modal
- ~~GitLab: create repos from UI~~ — sidebar button with inline form
- ~~GitLab: per-agent repo access~~ — scoping with checkboxes in config
- ~~Agent thoughts search~~ — real-time filtering in thoughts panel
- ~~Doc version restore~~ — restore button on history entries
- ~~Per-agent verbosity~~ — 6 levels from Concise to Dissertation
- ~~Doc editing with version history~~ — inline editor, author attribution, full history
- ~~Event system~~ — multi-action events with YAML editor, pool/log tabs, 22 sample events
- ~~Corporate email~~ — inbox tab, compose, #announcements channel, event action type
- ~~Recap system~~ — 18 styled recaps via one-shot agent, session persistence
- ~~Memo-list~~ — threaded async discussion board, Google Groups style, feature-flagged
- ~~Blog~~ — internal + external company blog with publish/draft/unpublish workflow, feature-flagged
- ~~Theme system~~ — 14 CSS variables, 5 themes (Default, Stadium, Field, Solarized Dark, Solarized Light)
- ~~NRSP character sheets~~ — 24 characters + 8 templates migrated to .CS.md format with ## Prompt extraction
- ~~Subsystem architecture doc~~ — docs/subsystem-architecture.md (updated for v3)
- ~~MUD dev team scenario~~ — "Realm of the Forgotten Crown" with seeded codebase, 6 characters, easter egg quest
- ~~v3 architecture~~ — container orchestrator, MCP server, webapp Blueprint refactor (T4NN3R)

## Priority / Sequencing

### Project Housekeeping (Do First)

1. **License** — choose and add a license file (MIT? Apache 2.0? AGPL?)
2. **README rewrite** — update for COSIM branding, v3 architecture (three-process model, MCP, containers), setup instructions, token spend warning
3. **Token spend warning** — prominent WARNING in README and startup output: "COSIM consumes a significant amount of LLM tokens. Expect hundreds of dollars in spend within minutes of running a full scenario. Monitor your usage."
4. **COSIM branding** — rename/rebrand to COSIM (Company Organization Simulator). Update README, docs, UI title, package metadata
5. **Logo** — design a COSIM logo for README, UI header, and docs
6. **Cleanup stale files** — remove `docs.agentic-sdlc/`, `gitlab.bak/`, old `.md` character files (replaced by `.CS.md`), any other committed runtime artifacts. Add to `.gitignore`
7. **Wrapper script** — `./cosim` or `./run.sh` that starts all three processes (Flask server, MCP server, container orchestrator) with sensible defaults
8. **Skills cleanup** — audit `.claude/skills/` directory, remove unused or outdated skill definitions

### CI / Quality

9. **CI and linting** — set up GitHub Actions with ruff (lint + format), pytest, and syntax checks. Gate PRs on passing checks
10. **Test suite (pytest)** — unit and integration tests for scenario loading, session save/load, API endpoints, state modules (memos, blog, email, events), personas prompt extraction
11. **Dependabot** — enable for Python dependencies (pyproject.toml) and GitHub Actions

### Features

12. **Local LLM support** — support Ollama and other local LLMs alongside Claude. The v3 MCP architecture enables this — agents just need a CLI that speaks MCP. Container image could swap `claude` for an Ollama-backed MCP client. Support `--model ollama:llama3` or similar
13. **Madden Mode (Telestrator)** — a drawing/annotation overlay for the Scenario Director. Canvas layer over the UI where you can circle messages, draw arrows showing cause and effect, sketch plans, annotate agent behavior in real-time
14. **Whiteboard** — shared visual collaboration subsystem for agents. Options: (a) sticky notes / card board where agents post ideas to columns (Miro/FigJam lite), (b) diagram-as-code with Mermaid/ASCII rendering, (c) collaborative scratchpad/markdown workspace
15. **Auto-firing events** — random/timed event triggering at intervals. Cascading follow-up events

### Enhancements

- **Notification audit** — (1) audit all CRUD operations for missing `showNotice()` coverage. (2) Add auto-dismiss cooldown timer (3-5 seconds). Stack multiple notifications
- **Theme audit** — audit all buttons, badges, and inline styles for CSS variable coverage. Semantic colors stay hardcoded
- **Character template metadata** — templates should auto-fill the entire hire form (role, tier, channels, folders), not just the prompt

### Architecture

- **Orchestrator rearchitecture** — v3 uses container-based agents (podman + MCP). The old v2 SDK-based orchestrator has been removed. Remaining work:
  1. **`--no-containers` fallback** — run `claude` CLI directly as subprocesses instead of inside podman containers, for environments without podman. Same MCP tools, no container build step
  2. **Per-agent model selection** — different agents use different models (Opus for CEO, Haiku for interns, Ollama for local testing)
  3. **MCP as the universal agent interface** — ensure any LLM CLI that speaks MCP can be used as an agent backend

---

## Road Map

### Phase 1: Feature Flags & Cleanup

**All subsystems should be feature-flagged** so they can be enabled/disabled per scenario. Some scenarios don't need email, blog, or memos — they should be toggleable in `scenario.yaml` settings without code changes.

Currently feature-flagged:
- ✅ `enable_memos` — memo-list subsystem
- ✅ `enable_blog` — blog subsystem
- ✅ `enable_background_tasks` — background task executor

Need feature flags:
- ❌ `enable_email` — corporate email / announcements
- ❌ `enable_tickets` — ticket tracker
- ❌ `enable_gitlab` — GitLab repos
- ❌ `enable_docs` — document system
- ❌ `enable_recaps` — recap generation

Each flag should gate: (1) MCP tool registration, (2) turn prompt section, (3) UI tab visibility. API endpoints stay active (for manual testing) but tools don't appear to agents when disabled.

### Phase 2: Model Flexibility

**Goal:** Run COSIM with any LLM, not just Claude.

**The interface is MCP.** The MCP server (32 simulation tools) is already model-agnostic — it doesn't care who calls the tools. The gap is on the **agent side**: the current architecture assumes the agent is Claude Code CLI, which speaks MCP natively. Other LLMs need an MCP-capable agent harness.

```
Current:   claude CLI ──(speaks MCP natively)──→ MCP Server ──→ Flask
Needed:    any LLM    ──→ MCP Agent Harness ──→ MCP Server ──→ Flask
```

**The MCP Agent Harness** is the key missing piece. It would:
- Take a system prompt + turn prompt (same as today)
- Send it to any LLM provider (Ollama, OpenAI, Bedrock, etc.)
- Parse the LLM's response for tool call intentions
- Execute MCP tool calls on the LLM's behalf
- Feed tool results back to the LLM for the next step
- Call `signal_done` when the agent is finished

This is essentially a lightweight agent loop that bridges any LLM to MCP. Claude Code has this built in; other providers need it built.

Steps:
1. **Claude model selection** — allow different Claude models per agent. `--model sonnet` is global today; should support per-agent: `{model: "opus"}` in character config for CEO, `{model: "haiku"}` for intern. Easiest win — still Claude, just different tiers
2. **MCP agent harness** — build or adopt a generic agent harness that connects any LLM to an MCP server. Could be a Python script that replaces `claude` in the container. Inputs: system prompt, MCP server URL. Outputs: tool calls + text responses
3. **Ollama / local models** — use the MCP agent harness with Ollama as the LLM backend. Key question: do local models (Llama, Mistral, Qwen) handle tool calling well enough for multi-step agent loops?
4. **Enterprise endpoints** — models.corp, Azure OpenAI, AWS Bedrock, internal model servers. Same harness, different LLM API backend
5. **Ambient (OpenShift)** — investigate integration with the internal Ambient project on OpenShift for model access and credential management

### Phase 3: Infrastructure Scaling

**Goal:** Run COSIM beyond a single dev machine.

1. **VM deployment** — run the three processes (Flask, MCP, orchestrator) on a VM with proper systemd services, log management, and monitoring
2. **K8s / cluster access** — containerize all three processes as pods. Agent containers become K8s Jobs or sidecar containers. Benefits: auto-scaling, health checks, log aggregation, secrets management
3. **Multi-tenant** — multiple scenarios running simultaneously on shared infrastructure. Each scenario gets its own namespace/session

### Phase 4: Ecosystem

1. **Scenario marketplace** — shareable scenario packages (YAML + characters + seeded content) that others can download and run
2. **NRSP integration** — MUD world data in NRSP format (rooms as `.LS.md`, NPCs as `.CS.md`), session logs as `.SLD.md`
3. **Plugin system** — custom subsystems as plugins that follow the architecture guide without modifying core code
