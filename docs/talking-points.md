# CoSim Talking Points

## Why CoSim?

- If you're going to put AI agents into real workflows, you need to understand how they behave in organizational contexts — CoSim is a safe sandbox for that
- Every failure mode discovered in simulation (information leaks, speaking for others, deferring work, plausible-wrong output) is one you don't discover in production
- Everyone's asking "where do agents fit in our development process?" — CoSim provides a concrete testbed instead of speculation
- The 36-activity SDLC framework came out of watching agents actually try to do the work
- You can't A/B test your org chart with real humans — you can with simulated ones
- What happens with flat teams vs hierarchy? Different team compositions? Different communication structures? CoSim lets you run those experiments in hours instead of quarters
- Multi-agent orchestration, MCP tooling, container isolation, prompt engineering at scale — these are skills the organization will need regardless of the specific application
- CoSim is a forcing function for learning how to build and operate agentic systems

## What CoSim Is

- A simulation platform where 10+ Claude-powered AI personas operate as a realistic organization — departments, hierarchy, communication channels, workplace tools
- Human drops a message into a channel; the entire org responds: engineers assess feasibility, PM scopes requirements, sales positions value, finance models the deal, leadership decides
- Three-process architecture: Flask server (state), MCP server (32 tools), container orchestrator (podman + Claude Code)

## How It Works

- Each agent runs autonomously in its own container with Claude Code + MCP tools
- Tiered wave execution: ICs respond first → managers see IC output and coordinate → execs see everything and make final calls
- Agents autonomously file tickets, create documents, commit code, write memos, send emails — no explicit programming for these behaviors

## Why It Matters

- Hierarchy improves quality — flat parallel responses produced repetitive noise; tiered dispatch created genuine collaboration and realistic org dynamics
- Demonstrates what "agentic" actually means: autonomy, adaptability, and goal-direction across lifecycle phases — not just AI-assisted workflows with human checkpoints
- Agents can reliably handle ~14 of the 36 SDLC activity types today; CoSim is a testbed for understanding which ones and how

## Research Angle

- Potential IO psychology research platform — lets you study org culture formation, team composition effects, crisis adaptation in ways impossible with human subjects
- Honest caveat: zero published validation studies exist yet comparing LLM org simulations to real human behavior
- The research-lab scenario already demonstrated agents self-organizing a research effort: filing tickets, dividing work, debating methodology, delivering a synthesized report

## Scenarios Already Built

- Tech startup (11-person engineering org), research lab, D&D campaign, Y2K dotcom, MUD dev team — shows the platform is general-purpose

## The Sobering Part

- An overloaded agent produces plausible-looking output that is quietly wrong — failure modes differ from humans
- State management and container networking were harder problems than agent coordination itself
- Token usage adds up fast with 10+ concurrent Claude instances per round

## Development Velocity & Iteration

- ~120 commits from first commit to current state — built iteratively, not designed upfront
- Architecture evolved through 4 distinct phases: simple chat → JSON responses → podman containers → MCP-driven containerized agents with signal_done tier advancement
- Multiple contributors (PRs from tyraziel for scenarios, events, and features)

## Key Architectural Pivots

- Started with sequential agents, moved to parallel-within-tier execution via asyncio
- Replaced regex command parsing with structured JSON, then replaced that entirely with MCP tools
- Deprecated the v2 orchestrator completely in favor of container-based v3
- Refactored monolithic webapp.py into a blueprinted Flask package
- Hardcoded credentials removed from Dockerfile — moved to runtime mount (security hardening)

## Emergent Complexity / Real Engineering Problems Solved

- Cross-channel information leaks in agent prompts (had to enforce lane discipline)
- Agents speaking on behalf of other team members (had to explicitly prohibit it)
- Messages sent during startup being silently skipped
- Stale container DNS, SELinux mount issues, false disconnect detection
- Session save/load losing memos, events, emails, recaps (state management really is the hard part)
- Agents spamming #general — had to teach them channel discipline

## Platform Breadth

- 7 scenarios built (tech-startup, research-lab, D&D campaign, Y2K dotcom, MUD dev team, company-simulator-team, plus character templates)
- 32 MCP tools across 8 categories (chat, docs, gitlab, tickets, memos, blog, email, meta)
- Features added organically as agents needed them: DMs, background tasks, hire/fire, director channels, ticket reminders, blog system, memo threads
- 5 UI themes, agent thought inspection, token usage tracking, session management

## The "Agents Are Weird" Moments

- Had to tell agents "there is no 'later' — act now or it won't happen"
- Agents tried to schedule work for the future instead of doing it immediately
- Autonomous continuation needed explicit quiescence detection
- Per-agent verbosity controls added because some agents wouldn't stop talking
