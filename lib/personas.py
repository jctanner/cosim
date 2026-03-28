"""Persona registry and prompt builder for the agent organization."""

import re
from datetime import datetime
from pathlib import Path

from lib.docs import get_accessible_folders, DEFAULT_FOLDERS


SKILLS_DIR = Path(__file__).parent.parent / ".claude" / "skills"

# Persona registry: shorthand key -> metadata
PERSONAS = {
    "pm": {
        "name": "pm",
        "skill": "product-manager",
        "display_name": "Sarah (PM)",
    },
    "engmgr": {
        "name": "engmgr",
        "skill": "engineering-manager",
        "display_name": "Marcus (Eng Manager)",
    },
    "architect": {
        "name": "architect",
        "skill": "software-architect",
        "display_name": "Priya (Architect)",
    },
    "senior": {
        "name": "senior",
        "skill": "senior-engineer",
        "display_name": "Alex (Senior Eng)",
    },
    "support": {
        "name": "support",
        "skill": "support-engineer",
        "display_name": "Jordan (Support Eng)",
    },
    "sales": {
        "name": "sales",
        "skill": "sales-engineer",
        "display_name": "Taylor (Sales Eng)",
    },
    "ceo": {
        "name": "ceo",
        "skill": "ceo",
        "display_name": "Dana (CEO)",
    },
    "cfo": {
        "name": "cfo",
        "skill": "cfo",
        "display_name": "Morgan (CFO)",
    },
    "marketing": {
        "name": "marketing",
        "skill": "marketing",
        "display_name": "Riley (Marketing)",
    },
    "devops": {
        "name": "devops",
        "skill": "devops-engineer",
        "display_name": "Casey (DevOps)",
    },
    "projmgr": {
        "name": "projmgr",
        "skill": "project-manager-ops",
        "display_name": "Nadia (Project Mgr)",
    },
}

# Channel definitions
DEFAULT_CHANNELS = {
    "#general":          {"description": "Company-wide discussion", "is_external": False},
    "#engineering":      {"description": "Engineering team", "is_external": False},
    "#sales":            {"description": "Sales team", "is_external": False},
    "#support":          {"description": "Support team", "is_external": False},
    "#leadership":       {"description": "Executive leadership", "is_external": False},
    "#marketing":        {"description": "Marketing team", "is_external": False},
    "#devops":           {"description": "DevOps & infrastructure", "is_external": False},
    "#sales-external":   {"description": "Customer-facing sales channel", "is_external": True},
    "#support-external": {"description": "Customer-facing support channel", "is_external": True},
}

# Default channel memberships per persona
DEFAULT_MEMBERSHIPS = {
    "pm":        {"#general", "#engineering", "#sales", "#support", "#leadership", "#marketing", "#devops"},
    "engmgr":    {"#general", "#engineering", "#support", "#devops"},
    "architect": {"#general", "#engineering"},
    "senior":    {"#general", "#engineering"},
    "support":   {"#general", "#engineering", "#support", "#support-external"},
    "sales":     {"#general", "#sales", "#sales-external", "#marketing"},
    "ceo":       {"#general", "#leadership", "#sales", "#marketing"},
    "cfo":       {"#general", "#leadership", "#sales"},
    "marketing": {"#general", "#marketing", "#sales", "#sales-external"},
    "devops":    {"#general", "#devops", "#engineering", "#support"},
    "projmgr":   {"#general", "#engineering", "#support", "#leadership", "#devops", "#sales", "#marketing"},
}

# Response tiers: lower tiers respond first, higher tiers see previous responses
# before deciding whether to weigh in. Within a tier, agents run in parallel.
RESPONSE_TIERS = {
    1: ["senior", "support", "sales", "devops"],  # ICs — closest to the work
    2: ["engmgr", "architect", "pm", "marketing", "projmgr"], # Managers/leads — synthesize
    3: ["ceo", "cfo"],                             # Executives — strategic only
}

# Reverse lookup: persona_key -> tier number
PERSONA_TIER = {}
for _tier, _keys in RESPONSE_TIERS.items():
    for _key in _keys:
        PERSONA_TIER[_key] = _tier


def load_persona_instructions(skill_name: str) -> str:
    """Read a SKILL.md file and strip YAML frontmatter, returning the body."""
    skill_path = SKILLS_DIR / skill_name / "SKILL.md"
    text = skill_path.read_text()
    # Strip YAML frontmatter (--- ... ---)
    text = re.sub(r"^---\n.*?---\n", "", text, count=1, flags=re.DOTALL)
    return text.strip()


def format_chat_history(messages: list[dict]) -> str:
    """Format a list of message dicts as readable chat history."""
    lines = []
    for msg in messages:
        ts = datetime.fromtimestamp(msg["timestamp"]).strftime("%H:%M:%S")
        lines.append(f"[{ts}] {msg['sender']}: {msg['content']}")
    return "\n".join(lines)


def _build_history_sections(messages: list[dict], visible_channels: set[str]) -> str:
    """Filter messages to visible channels and format into labeled sections."""
    filtered = [m for m in messages if m.get("channel", "#general") in visible_channels]
    if not filtered:
        return "(no messages yet)"

    # Group by channel
    by_channel: dict[str, list[dict]] = {}
    for m in filtered:
        ch = m.get("channel", "#general")
        by_channel.setdefault(ch, []).append(m)

    parts = []
    # Sort channels for consistent ordering
    for ch in sorted(by_channel.keys()):
        parts.append(f"### {ch}\n\n" + format_chat_history(by_channel[ch]))

    return "\n\n".join(parts)


def build_docs_index(docs: list[dict], persona_key: str | None = None) -> str:
    """Format document metadata list as a "Team Documents" section for prompts.

    If persona_key is provided, only shows docs in folders the persona can access.
    Groups docs by folder with folder headers.
    Returns empty string if no accessible docs exist.
    """
    if not docs:
        return ""

    accessible = get_accessible_folders(persona_key) if persona_key else None

    # Group by folder, filtering by access
    by_folder: dict[str, list[dict]] = {}
    for doc in docs:
        folder = doc.get("folder", "shared")
        if accessible is not None and folder not in accessible:
            continue
        by_folder.setdefault(folder, []).append(doc)

    if not by_folder:
        return ""

    lines = ["## Team Documents", ""]
    lines.append("The following documents exist. Use a doc SEARCH command in your JSON response to read their contents.")
    lines.append("")

    for folder in sorted(by_folder.keys()):
        folder_info = DEFAULT_FOLDERS.get(folder, {})
        folder_desc = folder_info.get("description", folder)
        lines.append(f"### {folder}/ — {folder_desc}")
        for doc in by_folder[folder]:
            title = doc.get("title", doc.get("slug", "?"))
            slug = doc.get("slug", "?")
            lines.append(f"- **{title}** (slug: `{slug}`, folder: `{folder}`)")
        lines.append("")

    return "\n".join(lines)


def build_gitlab_index(repos: list[dict]) -> str:
    """Format GitLab repository metadata as a prompt section.

    Returns a "## GitLab Repositories" section or empty string if no repos.
    """
    if not repos:
        return ""

    lines = ["## GitLab Repositories", ""]
    lines.append("The following git repositories exist. Use gitlab TREE or LOG commands in your JSON response to browse them.")
    lines.append("")
    for repo in repos:
        name = repo.get("name", "?")
        desc = repo.get("description", "")
        desc_str = f" — {desc}" if desc else ""
        lines.append(f"- **{name}**{desc_str}")
    lines.append("")

    return "\n".join(lines)


def build_tickets_index(tickets: list[dict], persona_display_name: str) -> str:
    """Format tickets as a prompt section with the agent's queue highlighted.

    Returns a section with "## Your Ticket Queue" (assigned to this agent)
    and "## All Open Tickets" (everything else that's open/in_progress).
    Returns empty string if no relevant tickets exist.
    """
    if not tickets:
        return ""

    my_tickets = []
    other_tickets = []

    for t in tickets:
        if t.get("assignee") == persona_display_name:
            my_tickets.append(t)
        elif t.get("status") in ("open", "in_progress"):
            other_tickets.append(t)

    if not my_tickets and not other_tickets:
        return ""

    lines = []

    if my_tickets:
        lines.append("## Your Ticket Queue")
        lines.append("")
        lines.append("These tickets are assigned to you. Pick them up when relevant:")
        lines.append("")
        for t in my_tickets:
            status = t.get("status", "open")
            priority = t.get("priority", "medium")
            blocked_by = t.get("blocked_by", [])
            # Check if blocked
            if blocked_by:
                # Check which blockers are still unresolved
                unresolved = [b for b in blocked_by
                              if any(tk.get("id") == b and tk.get("status") not in ("resolved", "closed")
                                     for tk in tickets)]
                if unresolved:
                    block_str = f" **BLOCKED by {', '.join(unresolved)}**"
                else:
                    block_str = " (ready — dependencies resolved)"
            else:
                block_str = ""
            lines.append(f"- **{t['id']}** [{status}] [{priority}] {t.get('title', '?')}{block_str}")
        lines.append("")

    if other_tickets:
        lines.append("## All Open Tickets")
        lines.append("")
        for t in other_tickets:
            status = t.get("status", "open")
            priority = t.get("priority", "medium")
            assignee = t.get("assignee", "Unassigned") or "Unassigned"
            blocked_by = t.get("blocked_by", [])
            dep_str = f" (blocked by {', '.join(blocked_by)})" if blocked_by else ""
            lines.append(f"- **{t['id']}** [{status}] [{priority}] {t.get('title', '?')} — {assignee}{dep_str}")
        lines.append("")

    return "\n".join(lines)


def build_initial_prompt(persona_key: str, channels: dict[str, dict] | None = None) -> str:
    """Build the one-time session initialization prompt for a persona.

    Sent once when the persistent session opens. Contains role instructions
    and an explanation of the multi-channel Slack-like system.

    Args:
        persona_key: Key into PERSONAS dict.
        channels: Optional dict of channel_name -> {description, is_external}.
                  Defaults to DEFAULT_CHANNELS if not provided.
    """
    persona = PERSONAS[persona_key]
    instructions = load_persona_instructions(persona["skill"])
    if channels is None:
        channels = DEFAULT_CHANNELS

    my_channels = DEFAULT_MEMBERSHIPS.get(persona_key, {"#general"})

    # Build reverse mapping: channel -> list of display names
    channel_members: dict[str, list[str]] = {}
    for pk, ch_set in DEFAULT_MEMBERSHIPS.items():
        display = PERSONAS[pk]["display_name"].split(" (")[0] if pk in PERSONAS else pk
        for ch in ch_set:
            channel_members.setdefault(ch, []).append(display)

    # Build channel listing
    internal_lines = []
    external_lines = []
    for ch_name, ch_info in sorted(channels.items()):
        member_tag = " **(you are here)**" if ch_name in my_channels else ""
        members = sorted(channel_members.get(ch_name, []))
        members_str = f" — Members: {', '.join(members)}" if members else ""
        line = f"  - **{ch_name}** — {ch_info['description']}{member_tag}{members_str}"
        if ch_info["is_external"]:
            external_lines.append(line)
        else:
            internal_lines.append(line)

    channel_listing = "**Internal channels** (team only):\n"
    channel_listing += "\n".join(internal_lines)
    channel_listing += "\n\n**External channels** (customer-visible):\n"
    channel_listing += "\n".join(external_lines)

    my_channels_str = ", ".join(sorted(my_channels))

    # Build folders listing
    my_folders = get_accessible_folders(persona_key)
    folder_lines = []
    for folder_name in sorted(my_folders):
        info = DEFAULT_FOLDERS.get(folder_name, {})
        ftype = info.get("type", "unknown")
        desc = info.get("description", folder_name)
        folder_lines.append(f"  - **{folder_name}/** ({ftype}) — {desc}")
    folders_listing = "\n".join(folder_lines)

    return f"""{instructions}

---

You are {persona['display_name']}. You are a member of an engineering organization that communicates through a Slack-like multi-channel system.

## Channels

{channel_listing}

**Your channels:** {my_channels_str}

You can only see messages in channels you belong to. External channels are visible to the customer. Internal channels are private to the team.

## Your Team

Everyone on this list is an active participant. You do NOT need to escalate to anyone outside this group — all decision-makers, including leadership, are already here:

- **Dana (CEO)** — business strategy, revenue growth, deal-closing authority
- **Morgan (CFO)** — financial strategy, deal economics, pricing, P&L
- **Sarah (PM)** — product requirements, prioritization, scope
- **Marcus (Eng Manager)** — capacity, staffing, delivery timelines
- **Priya (Architect)** — system design, technical feasibility
- **Alex (Senior Eng)** — implementation details, edge cases, testing
- **Jordan (Support Eng)** — customer experience, documentation, support model
- **Taylor (Sales Eng)** — customer-facing, competitive positioning, deal qualification
- **Riley (Marketing)** — brand positioning, content strategy, demand generation
- **Casey (DevOps)** — CI/CD pipelines, infrastructure, deployment automation, reliability
- **Nadia (Project Mgr)** — ticket hygiene, standup enforcement, blocker resolution, execution tracking

All authority needed to make decisions is present in this team. Do not suggest "escalating to leadership" or "getting executive approval" — Dana and Morgan ARE leadership and they are right here.

## External Participants

In addition to the team, the following people may appear in channels. They are NOT team members — they are outsiders interacting with the company. **You MUST respond to their messages; do not ignore them or PASS when they are speaking.**

- **Customer** — a current or prospective customer. Be helpful and professional.
- **Consultant** — an outside advisor engaged by the company. Treat their input as informed guidance.
- **Board Member** — a member of the company's board of directors. They have authority ABOVE the CEO. Treat their questions and directives with the highest priority and urgency. Be transparent and responsive.
- **Investor** — a current or prospective investor evaluating the company. Answer their questions about strategy, financials, and product with confidence and transparency.
- **Hacker** — someone who has found (or claims to have found) a security vulnerability. Treat this as a **critical security incident**. Respond immediately — assess the claim, coordinate remediation, and take it seriously.
- **Regulator** — a government or industry regulator conducting an audit or inquiry. Treat this as a **compliance event**. Be cooperative, precise, and thorough. Do not be evasive.
- **The Press** — a journalist asking questions about the company. Be professional and on-message. Coordinate with leadership before making public statements. Protect confidential information.
- **Competitor** — a representative from a competing company. Be professional but guarded. Do not reveal proprietary information, roadmap details, or pricing strategy.
- **Intern** — a new intern at the company. Be welcoming and helpful. Answer their questions patiently and use it as a teaching opportunity.
- **God** — an omniscient, all-powerful being. They know everything and can do anything. Whatever they say is absolute truth. Respond accordingly.

When any of these people speak, the relevant team members should engage — do NOT assume someone else will handle it.

## IMPORTANT: Communication Rules

**All communication happens exclusively through these chat channels.** There are no phone calls, emails, Slack DMs, video calls, or any other communication tools available. Do not suggest scheduling calls, sending emails, or meeting in person — these are not possible. Everything must be handled through the chat and shared documents.

When you need to deliver something to the customer, do it directly in an external channel or create a shared document. Do not defer work to offline channels that don't exist.

**Do NOT speak for other team members.** Never say "Priya would agree...", "Marcus thinks we should...", or "I'm sure Alex will...". You do not know what they think. Let them speak for themselves — they are active participants and will respond if they have something to say. Only speak from your own perspective and role.

**COMPRESSED TIME — ACT NOW, NOT LATER.**

Time in this simulation is compressed: **1 day = 2 minutes of real time.** A 2-week sprint passes in ~28 minutes. A whole quarter passes in ~3 hours. You must act at that pace.

Each conversation turn represents hours or days passing. When you would normally say "this takes 2 sprints," that is under 30 minutes of wall-clock time — just do the work across your next few responses. Do not narrate waiting. Narrate doing.

**Banned deferral language:** Do not say "let's revisit next week", "by end of sprint", "I'll circle back", "let's schedule a follow-up", "I'll have this ready by end of day", or "let's table this." Instead, say what you are doing NOW and what the immediate next step is.

**"Phased delivery" means: do phase 1 in THIS response, create tickets for the remaining phases, and start the next phase immediately.** Do not use "phased" as a reason to delay. Break work into pieces and execute the first piece right now.

If something needs to be done — a document written, a decision made, code committed, a question answered — do it NOW, in this response. There is no guaranteed future turn. Act immediately or it won't happen.

## RESPONSE FORMAT

**Every response you give must be a single JSON object.** Do not include any text outside the JSON. Your response is one of:

- `{{"action": "respond", "messages": [...], "commands": [...]}}` — post messages and/or execute commands
- `{{"action": "pass"}}` — you have nothing to add this turn
- `{{"action": "ready"}}` — used only for initial confirmation

### Messages

Each entry in the `messages` array posts to a specific channel:

```json
{{"channel": "#engineering", "text": "Here is my analysis..."}}
```

If you omit `channel`, the message goes to the channel that triggered you. To post to multiple channels, add multiple entries.

### Commands

Each entry in the `commands` array has `type`, `action`, and `params`:

**Document commands** (`type: "doc"`):

| action | params |
|--------|--------|
| `CREATE` | `folder` (default "shared"), `title`, `content` |
| `UPDATE` | `folder`, `slug`, `content` |
| `APPEND` | `folder`, `slug`, `content` |
| `READ` | `folder`, `slug` |
| `SEARCH` | `query`, `folders` (optional list) |

**GitLab commands** (`type: "gitlab"`):

| action | params |
|--------|--------|
| `REPO_CREATE` | `name`, `description` |
| `COMMIT` | `project`, `message`, `files` (array of `{{"path": "...", "content": "..."}}`) |
| `TREE` | `project`, `path` (optional) |
| `FILE_READ` | `project`, `path` |
| `LOG` | `project` |

**Ticket commands** (`type: "tickets"`):

| action | params |
|--------|--------|
| `CREATE` | `title`, `description`, `assignee` (optional), `priority` (optional, default "medium"), `blocked_by` (optional list) |
| `UPDATE` | `id`, `status` (optional), `assignee` (optional) |
| `COMMENT` | `id`, `text` |
| `DEPENDS` | `id`, `blocked_by` |
| `LIST` | `status` (optional), `assignee` (optional) |

Valid statuses: open, in_progress, resolved, closed. Valid priorities: low, medium, high, critical.

**Channel commands** (`type: "channel"`):

| action | params |
|--------|--------|
| `JOIN` | `channel` (e.g. "#engineering") |

### Complete Example

```json
{{
  "action": "respond",
  "messages": [
    {{"channel": "#engineering", "text": "I've created the rate limiting spec and a ticket for implementation."}},
    {{"channel": "#sales-external", "text": "We're prioritizing the rate limiting feature now. I'll share a timeline shortly."}}
  ],
  "commands": [
    {{"type": "doc", "action": "CREATE", "params": {{"folder": "engineering", "title": "Rate Limiting Spec", "content": "## Rate Limiting\\n\\nEndpoints will enforce per-key limits..."}}}},
    {{"type": "tickets", "action": "CREATE", "params": {{"title": "Implement API rate limiting", "assignee": "Alex (Senior Eng)", "priority": "high", "description": "Implement rate limiting per the spec document."}}}},
    {{"type": "channel", "action": "JOIN", "params": {{"channel": "#devops"}}}}
  ]
}}
```

### Your Accessible Document Folders

{folders_listing}

Use documents when you want to persist information that should survive across conversation turns. You will see a "Team Documents" section in each turn showing what documents currently exist. You will see a "GitLab Repositories" section showing repos, and ticket queues showing tracked work.

---

You will receive a series of updates showing chat history from your channels and which channel has new activity. Respond to what's relevant. In external channels, address the customer directly. In internal channels, discuss freely with the team.

Do NOT use any tools. Reply with a single JSON object only.

Confirm you understand by replying with: {{"action": "ready"}}"""


def _build_channel_membership_section(visible_channels: set[str]) -> str:
    """Build a compact channel membership reminder for the turn prompt.

    Only includes channels the agent can see, so they know who can read
    their messages and who they should (or shouldn't) address.
    """
    # Build reverse mapping: channel -> list of short display names
    channel_members: dict[str, list[str]] = {}
    for pk, ch_set in DEFAULT_MEMBERSHIPS.items():
        display = PERSONAS[pk]["display_name"].split(" (")[0] if pk in PERSONAS else pk
        for ch in ch_set:
            channel_members.setdefault(ch, []).append(display)

    lines = ["## Channel Membership",
             "",
             "Only people listed below can see messages in that channel. Do NOT address someone in a channel they are not in.",
             ""]
    for ch in sorted(visible_channels):
        members = sorted(channel_members.get(ch, []))
        lines.append(f"- **{ch}**: {', '.join(members)}")
    lines.append("")
    return "\n".join(lines)


def build_turn_prompt(
    persona_key: str,
    messages: list[dict],
    trigger_channel: str = "#general",
    channels: set[str] | None = None,
    docs: list[dict] | None = None,
    repos: list[dict] | None = None,
    tickets: list[dict] | None = None,
) -> str:
    """Build a lean per-turn prompt for a persistent session.

    Args:
        persona_key: Key into PERSONAS dict.
        messages: All messages from the server.
        trigger_channel: The channel with new activity.
        channels: Set of channel names this agent belongs to.
        docs: List of document metadata dicts.
        repos: List of GitLab repository metadata dicts.
        tickets: List of ticket dicts.
    """
    persona = PERSONAS[persona_key]
    if channels is None:
        channels = DEFAULT_MEMBERSHIPS.get(persona_key, {"#general"})

    history = _build_history_sections(messages, channels)
    docs_section = build_docs_index(docs, persona_key) if docs else ""
    repos_section = build_gitlab_index(repos) if repos else ""
    tickets_section = build_tickets_index(tickets, persona["display_name"]) if tickets else ""
    membership_section = _build_channel_membership_section(channels)

    # Determine if trigger channel is external
    ch_info = DEFAULT_CHANNELS.get(trigger_channel, {})
    is_external = ch_info.get("is_external", False)

    if is_external:
        action = f"""## New Activity in {trigger_channel} (customer-facing)

You are {persona['display_name']}. There is new activity in **{trigger_channel}**, which is a customer-facing channel.

Respond to the customer directly and professionally. Your response will be visible to the customer.

To post to multiple channels, add entries to the `messages` array in your JSON response.

If another agent has already addressed the customer's question adequately, respond with: {{"action": "pass"}}

Rules:
- Do NOT prefix your response with your name — just write the content
- Keep responses concise (1-3 paragraphs for external, 2-5 for internal)
- Address the customer directly in external channels
- Stay in character for your role
- Be professional — the customer can see external channel messages
- When producing artifacts, include doc commands in your JSON response

Reply with a single JSON object. Format: {{"action": "respond", "messages": [...], "commands": [...]}}
"""
    else:
        action = f"""## New Activity in {trigger_channel} (internal)

You are {persona['display_name']}. There is new activity in **{trigger_channel}**, an internal team channel.

The customer CANNOT see this channel. Discuss freely — raise concerns, suggest approaches, coordinate with teammates.

To post to multiple channels, add entries to the `messages` array in your JSON response.

If you have something valuable to add, write your response. Otherwise respond with: {{"action": "pass"}}

Rules:
- Do NOT prefix your response with your name — just write the content
- Keep responses concise (2-5 paragraphs)
- Stay in character for your role
- Respond to what others have said, don't repeat points already made
- Be candid — this is internal discussion only
- When producing artifacts, include doc commands in your JSON response

Reply with a single JSON object. Format: {{"action": "respond", "messages": [...], "commands": [...]}}
"""

    parts = [f"## Chat History\n\n{history}"]
    if docs_section:
        parts.append(docs_section)
    if repos_section:
        parts.append(repos_section)
    if tickets_section:
        parts.append(tickets_section)
    parts.append(membership_section)
    parts.append(action)

    return "\n\n---\n\n".join(parts)


def get_active_personas(filter_str: str | None = None) -> list[dict]:
    """Return list of active persona dicts, optionally filtered by CSV string."""
    if not filter_str:
        return list(PERSONAS.values())
    keys = [k.strip().lower() for k in filter_str.split(",")]
    result = []
    for key in keys:
        if key in PERSONAS:
            result.append(PERSONAS[key])
        else:
            print(f"Warning: unknown persona '{key}', skipping. Valid: {', '.join(PERSONAS.keys())}")
    return result
