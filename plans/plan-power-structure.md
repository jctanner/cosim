# V1 Design: Power, Incentives, and DMs

Four mechanisms total. Each one changes behavior through system enforcement, not prompt wishes.

---

## 1. DMs (biggest behavioral change, ~200 lines)

### How it works

DMs are a new command type in the agent JSON response:

```json
{
  "commands": [
    {"type": "dm", "to": "sarah_pm", "text": "Can we align on scope before the exec meeting?"}
  ]
}
```

DMs are **not real-time conversations**. They're one-shot messages that get queued and delivered at the start of the recipient's next turn.

### System-enforced constraints

- **Max 2 DMs per agent per turn.** Orchestrator silently drops extras. No negotiation.
- **DMs never trigger a new response cycle.** They're passive — delivered when the recipient is already being triggered by a regular channel message.
- **DMs flow up within a cycle, down across cycles.** During a response cycle:
  - Tier 1 runs → can DM tier 2/3 agents
  - Tier 2 runs → receives tier 1 DMs, can DM tier 3
  - Tier 3 runs → receives tier 1+2 DMs
  - Tier 3 DMs to tier 1 → delivered in the **next** response cycle

This matches real organizations: ICs escalate up fast, executive direction propagates down on the next beat.

### Implementation

**A. State (orchestrator-level):**
```python
_dm_queue: dict[str, list[dict]]  # recipient_key -> [{from, text, timestamp}]
```

Persisted as `dm_queue.json` in session save/load.

**B. Response schema:** Add `"dm"` to the recognized command types in `normalize_commands()`. Extract DMs, enforce the 2-per-turn cap, queue them.

**C. Turn prompt injection:** In `build_turn_prompt()`, if the agent has pending DMs, inject them as:

```
## Private Messages (visible only to you)
- From Sarah (PM): "Can we align on scope before the exec meeting?"
- From Alex (Senior Eng): "The API design won't scale. Flagging before it goes to Dana."
```

Then clear the queue for that recipient.

**D. Auditability:** Log all DMs to `#system` channel so the operator can see them. Agents can't.

### Prompt budget: ~200 tokens max (2 DMs × ~100 tokens)

### What this actually changes

Without DMs, all coordination is public. Every negotiation, disagreement, and alignment happens in front of everyone. That's why agents over-cooperate — public dissent feels risky to an LLM.

DMs give agents a private channel to disagree, warn, escalate, and pre-align. The CEO can privately tell the CFO "push back on this in #general" without the engineer seeing the coordination. That's a structural behavior change, not a prompt instruction.

---

## 2. Executive Directives (pinned decisions, ~100 lines)

### The problem with chat-based authority

Right now, when Dana (CEO) says "We're building the AI Operations Platform," that statement scrolls away in chat history. Two turns later, an agent might propose something contradictory because the directive is buried in context.

Executive authority is meaningless if executive decisions are ephemeral.

### How it works

Add a `"decision"` command type, restricted to tier-3 agents:

```json
{
  "commands": [
    {"type": "decision", "text": "We are building AI Ops Control Plane. All engineering work aligns to this."}
  ]
}
```

**System-enforced:** The orchestrator rejects `decision` commands from tier 1 or tier 2 agents. They literally cannot issue binding directives — the command fails silently and a system message tells them "Decision commands require executive authority."

### What happens with decisions

- Stored in an `_active_decisions` list (max 5, oldest dropped when full)
- Injected into **every agent's turn prompt** as a persistent section:

```
## Active Executive Directives
1. [Dana (CEO), 2 turns ago]: "We are building AI Ops Control Plane. All engineering work aligns to this."
2. [Dana (CEO), 5 turns ago]: "SOC2 compliance is approved. $15K immediate budget."
```

- Decisions persist across turns until pushed out by newer decisions
- Saved/restored with sessions

### Prompt budget: ~250 tokens max (5 directives × ~50 tokens)

### What this actually changes

This is the simplest mechanism that creates real hierarchy. Exec statements don't fade — they're structurally pinned. Every agent sees them every turn. An IC can't "forget" the CEO's direction because the system re-injects it.

Combined with DMs: the CEO can DM a manager "enforce this harder" while the public directive stays neutral. That's real organizational dynamics.

---

## 3. Role Pressure Injection (computed state, ~150 lines)

### Why incentive labels don't work

Telling an agent "you prioritize revenue" produces theatrical compliance. The agent writes "From a revenue perspective..." before doing whatever it was going to do anyway.

### What works instead: different data, not different labels

Each agent gets a **role pressure** section in their turn prompt, computed from actual system state. The data is real, not fictional.

**For the PM:**
```
## Your Current Situation
- 4 open tickets assigned to you (1 marked critical)
- 2 docs you authored were edited by others in the last cycle
- No PRD exists for the AI Ops Control Plane yet
```

**For the CEO:**
```
## Your Current Situation
- 11 agents active, 8 responded last cycle
- 3 executive directives active (oldest is 6 turns ago)
- Board pitch deadline: scenario says 2 days
- Engineering has 12 open tickets, 4 critical
```

**For a Senior Engineer:**
```
## Your Current Situation
- 3 tickets assigned to you (1 overdue)
- You have 0 open PRs
- Last commit: 2 turns ago to ai-ops-control-plane
- 2 DMs pending from you (awaiting response next cycle)
```

### Implementation

Add a `build_role_pressure()` function that:
1. Takes the persona key and current system state (tickets, docs, repos)
2. Computes 3-5 concrete facts relevant to that role
3. Returns a short text block

Call it from `build_turn_prompt()` and inject the result.

### What this actually changes

Agents don't act on abstract incentives. They act on concrete situations. When an engineer sees "3 tickets assigned, 1 overdue," they feel pressure to address tickets rather than writing another architectural proposal. When the CEO sees "no PRD exists," they pressure the PM.

The incentive tension emerges from **different agents seeing different problems**, not from labels.

### Prompt budget: ~100 tokens (3-5 short lines)

---

## 4. Action Gating (the "theatrical compliance" killer, ~100 lines)

### The principle

Don't tell agents what they can't do. Make it not work when they try.

### Concrete gates in the orchestrator

Add validation to `normalize_commands()` and the command execution path:

**a) Decision authority:** Only tier-3 agents can issue `decision` commands. Tier 1-2 attempts → rejected, system message posted to `#system`: `"[Agent] attempted to issue an executive directive (denied — requires tier 3 authority)"`

**b) Ticket reassignment:** Only tier-2+ agents can reassign tickets to other agents. Tier-1 agents can only assign tickets to themselves. Tier-1 attempt to reassign → rejected with system message.

**c) Doc approval tagging:** When a tier-1 agent creates a doc, the orchestrator adds `[DRAFT - awaiting review]` to the metadata. Tier-2+ agents can issue a `{"type": "doc", "action": "APPROVE", "slug": "..."}` command to remove the draft tag. This is lightweight but meaningful — it means IC work requires managerial sign-off to be "official."

**d) Override:** Tier-2+ agents can issue `{"type": "override", "target_slug": "...", "reason": "..."}` to flag a doc or ticket as overridden. The override reason is injected into the original author's next turn prompt.

### What this actually changes

Authority becomes structural, not performative. A tier-1 agent literally cannot issue a binding decision. A manager can literally override IC work and the IC sees "Your doc 'api-design' was overridden by Marcus (Eng Manager): 'Doesn't address scale requirements.'" That creates real consequences without a scoring system.

---

## State Design Under Context Constraints

### What's persisted (outside prompt)

| State | Storage | Size |
|-------|---------|------|
| DM queue | In-memory dict, saved to `dm_queue.json` in session | Small — max 2 per agent, cleared on delivery |
| Active decisions | In-memory list, saved to `decisions.json` in session | Max 5 items |
| Override log | In-memory list, saved to `overrides.json` | Last 10 overrides |

### What's injected per turn

| Section | Tokens | Source |
|---------|--------|--------|
| Private Messages | ~200 max | DM queue |
| Executive Directives | ~250 max | Active decisions list |
| Your Current Situation | ~100 max | Computed from tickets/docs/repos |
| Recent Overrides (if applicable) | ~50 | Override log, filtered to this agent |

**Total added per turn: ~600 tokens worst case.** That's negligible against the current prompt size.

### What is NOT injected

- No trust scores
- No performance history
- No incentive labels
- No relationship graphs
- No personality modifiers

All of those produce theatrical compliance. The injected state is **facts and constraints**, not instructions.

---

## Measurement / Validation

Three specific signals that prove behavior actually changed:

### 1. Pass rate divergence

**Current state:** Agents rarely pass. Everyone responds to everything.

**Expected change:** With role pressure ("you have 3 overdue tickets") and information asymmetry (DMs), agents should pass more often on topics outside their responsibility. Measure pass rate per agent per turn.

**How to measure:** Count `"action": "pass"` responses per agent. Track the ratio before/after. If an agent with 3 overdue tickets starts passing on strategic discussions to focus on execution, that's real behavior change.

### 2. DM-to-public ratio on decisions

**Current state:** All coordination is public. Decisions emerge from group consensus.

**Expected change:** Decisions should sometimes be pre-aligned via DM before being announced publicly. Measure how often a public decision was preceded by DMs between the participating agents.

**How to measure:** For each executive directive issued, check if the CEO sent/received DMs to/from relevant agents in the same or prior cycle. A high DM-before-decision rate means agents are using private coordination, not just performing in public.

### 3. Override and rejection rate

**Current state:** Agent proposals are never challenged or overridden.

**Expected change:** Higher-tier agents should occasionally override or reject lower-tier work. If overrides never happen, the authority gates aren't being used. If they happen constantly, the system is too adversarial.

**How to measure:** Count override commands per cycle. Target: 5-15% of tier-1 proposals get overridden or modified by tier 2+. Zero means the feature isn't working. Over 30% means it's too aggressive.

Expose all three metrics on a `/api/metrics` endpoint and add them to the Usage tab.

---

## Implementation Order

1. **DMs** — highest ROI, most structural change, unlocks everything else
2. **Executive directives** — creates real hierarchy with minimal code
3. **Role pressure injection** — makes agents responsive to their actual situation
4. **Action gating** — enforces authority at the system level

Each step is independently useful. Don't build them all at once — ship DMs, observe behavior, then add the next layer.

---

## What was deliberately cut

- **Trust/influence scoring** — produces theatrical compliance, adds complexity, hard to calibrate. Cut it.
- **Performance review cycles** — too much infrastructure for v1. The role pressure section already surfaces "you have overdue tickets" which creates the same effect.
- **Complex approval workflows** — the draft-tagging mechanism is enough for v1. Multi-step approval chains are over-engineering.
- **Agent-initiated DM conversations** — v1 DMs are one-shot. No back-and-forth threads. That prevents DM explosion while still enabling coordination.
- **Dynamic incentive adjustment** — role pressures are computed from real state, not from a tunable incentive system. The scenario itself provides the pressure. No knobs needed.

---

## Persona Instruction Updates

Each persona's character file needs 2-3 additional lines. Not incentive labels — concrete accountability statements.

**Example for Sarah (PM):**
```
You are measured on: whether PRDs exist for committed features, whether tickets have clear acceptance criteria, and whether scope is controlled. If the team builds something without a PRD, that's your failure. If scope creeps without your sign-off, that's your failure.
```

**Example for Dana (CEO):**
```
You are measured on: whether the company has a clear strategic direction that the team is executing against, whether executive decisions are timely, and whether the board has what it needs. If the team is building without direction, that's your failure. You have the authority to make binding decisions — use it.
```

**Example for Alex (Senior Eng):**
```
You are measured on: whether committed code works, whether your tickets close on time, and whether your technical proposals are implementable (not just architecturally elegant). If you propose something that can't ship in the timeframe, that's your failure.
```

These are short, concrete, and focused on accountability rather than personality.
