"""Orchestrator — event-driven multi-channel agent loop."""

import re
import asyncio
from pathlib import Path

from lib.chat_client import ChatClient
from lib.personas import (
    get_active_personas,
    build_initial_prompt,
    build_turn_prompt,
    DEFAULT_CHANNELS,
    PERSONAS,
    RESPONSE_TIERS,
    PERSONA_TIER,
)
from lib.agent_runner import AgentPool

LOG_DIR = Path(__file__).parent.parent / "logs"

# Senders that are agents (not human input)
_AGENT_DISPLAY_NAMES = {p["display_name"] for p in PERSONAS.values()}

# -- Document command regexes --

_DOC_BLOCK_RE = re.compile(
    r'<<<DOC:(CREATE|UPDATE|APPEND)\s+'
    r'(?:folder="([^"]+)"\s+)?'
    r'(?:title|slug)="([^"]+)"'
    r'\s*>>>'
    r'(.*?)'
    r'<<<END_DOC>>>',
    re.DOTALL,
)

_DOC_SEARCH_RE = re.compile(
    r'<<<DOC:SEARCH\s+query="([^"]+)"'
    r'(?:\s+folders="([^"]*)")?'
    r'\s*/>>>',
)

# -- Channel command regex --

_CHANNEL_JOIN_RE = re.compile(r'<<<CHANNEL:JOIN\s+(#[\w-]+)\s*>>>')

# -- GitLab command regexes --

_GITLAB_BLOCK_RE = re.compile(
    r'<<<GITLAB:COMMIT\s+project="([^"]+)"\s+message="([^"]+)"'
    r'\s*>>>'
    r'(.*?)'
    r'<<<END_GITLAB>>>',
    re.DOTALL,
)

_GITLAB_REPO_CREATE_RE = re.compile(
    r'<<<GITLAB:REPO_CREATE\s+name="([^"]+)"'
    r'(?:\s+description="([^"]*)")?'
    r'\s*/>>>',
)

_GITLAB_TREE_RE = re.compile(
    r'<<<GITLAB:TREE\s+project="([^"]+)"'
    r'(?:\s+path="([^"]*)")?'
    r'\s*/>>>',
)

_GITLAB_FILE_READ_RE = re.compile(
    r'<<<GITLAB:FILE_READ\s+project="([^"]+)"\s+path="([^"]+)"'
    r'\s*/>>>',
)

_GITLAB_LOG_RE = re.compile(
    r'<<<GITLAB:LOG\s+project="([^"]+)"'
    r'\s*/>>>',
)

# -- Multi-channel response marker --

_CHANNEL_MARKER_RE = re.compile(r'^\[#([\w-]+)\]\s*$', re.MULTILINE)


def _extract_doc_commands(text: str) -> tuple[str, list[dict]]:
    """Parse doc commands from agent response text.

    Returns (cleaned_text, commands_list) where cleaned_text has all
    command blocks stripped out, and commands_list is a list of dicts
    describing each command found.
    """
    commands = []

    for match in _DOC_BLOCK_RE.finditer(text):
        action = match.group(1).upper()
        folder = match.group(2) or "shared"
        identifier = match.group(3)
        content = match.group(4).strip()
        cmd = {"action": action, "content": content, "folder": folder}
        if action == "CREATE":
            cmd["title"] = identifier
        else:
            cmd["slug"] = identifier
        commands.append(cmd)

    for match in _DOC_SEARCH_RE.finditer(text):
        cmd = {"action": "SEARCH", "query": match.group(1)}
        if match.group(2):
            cmd["folders"] = [f.strip() for f in match.group(2).split(",") if f.strip()]
        commands.append(cmd)

    cleaned = _DOC_BLOCK_RE.sub("", text)
    cleaned = _DOC_SEARCH_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned, commands


def _execute_doc_commands(
    client: ChatClient,
    commands: list[dict],
    author: str,
) -> list[dict]:
    """Execute a list of parsed doc commands via ChatClient."""
    results = []
    for cmd in commands:
        action = cmd["action"]
        folder = cmd.get("folder", "shared")
        try:
            if action == "CREATE":
                result = client.create_doc(cmd["title"], cmd["content"], author, folder=folder)
                results.append({"action": "created", "ok": True, **result})
            elif action == "UPDATE":
                result = client.update_doc(folder, cmd["slug"], cmd["content"], author)
                results.append({"action": "updated", "ok": True, **result})
            elif action == "APPEND":
                result = client.append_doc(folder, cmd["slug"], cmd["content"], author)
                results.append({"action": "appended", "ok": True, **result})
            elif action == "SEARCH":
                folders = cmd.get("folders")
                result = client.search_docs(cmd["query"], folders=folders)
                results.append({"action": "search", "ok": True, "query": cmd["query"], "results": result})
        except Exception as e:
            results.append({"action": action.lower(), "ok": False, "error": str(e)})
    return results


def _format_search_results(search_data: dict) -> str:
    """Format search results as readable text for a system message."""
    query = search_data.get("query", "")
    hits = search_data.get("results", [])
    if not hits:
        return f'[Doc Search] No documents found matching "{query}".'

    lines = [f'[Doc Search] Results for "{query}":']
    for hit in hits:
        title = hit.get("title", hit.get("slug", "?"))
        slug = hit.get("slug", "?")
        snippet = hit.get("snippet", hit.get("preview", ""))
        lines.append(f"  - {title} (slug: {slug}): {snippet}")
    return "\n".join(lines)


def _parse_commit_files(body: str) -> list[dict]:
    """Parse COMMIT body into list of {path, content} dicts.

    Body is delimited by `FILE: <path>` lines. Each FILE: starts a new file;
    content until the next FILE: or end-of-block is that file's body.
    """
    files = []
    current_path = None
    current_lines: list[str] = []

    for line in body.split("\n"):
        if line.startswith("FILE: "):
            if current_path is not None:
                files.append({"path": current_path, "content": "\n".join(current_lines).strip()})
            current_path = line[6:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_path is not None:
        files.append({"path": current_path, "content": "\n".join(current_lines).strip()})

    return files


def _extract_gitlab_commands(text: str) -> tuple[str, list[dict]]:
    """Parse GitLab commands from agent response text.

    Returns (cleaned_text, commands_list).
    """
    commands = []

    for match in _GITLAB_BLOCK_RE.finditer(text):
        project = match.group(1)
        message = match.group(2)
        body = match.group(3)
        files = _parse_commit_files(body)
        commands.append({"action": "COMMIT", "project": project, "message": message, "files": files})

    for match in _GITLAB_REPO_CREATE_RE.finditer(text):
        cmd = {"action": "REPO_CREATE", "name": match.group(1)}
        if match.group(2):
            cmd["description"] = match.group(2)
        else:
            cmd["description"] = ""
        commands.append(cmd)

    for match in _GITLAB_TREE_RE.finditer(text):
        cmd = {"action": "TREE", "project": match.group(1)}
        if match.group(2):
            cmd["path"] = match.group(2)
        commands.append(cmd)

    for match in _GITLAB_FILE_READ_RE.finditer(text):
        commands.append({"action": "FILE_READ", "project": match.group(1), "path": match.group(2)})

    for match in _GITLAB_LOG_RE.finditer(text):
        commands.append({"action": "LOG", "project": match.group(1)})

    cleaned = _GITLAB_BLOCK_RE.sub("", text)
    cleaned = _GITLAB_REPO_CREATE_RE.sub("", cleaned)
    cleaned = _GITLAB_TREE_RE.sub("", cleaned)
    cleaned = _GITLAB_FILE_READ_RE.sub("", cleaned)
    cleaned = _GITLAB_LOG_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned, commands


def _execute_gitlab_commands(
    client: ChatClient,
    commands: list[dict],
    author: str,
) -> list[dict]:
    """Execute a list of parsed GitLab commands via ChatClient."""
    results = []
    for cmd in commands:
        action = cmd["action"]
        try:
            if action == "REPO_CREATE":
                result = client.create_repo(cmd["name"], cmd.get("description", ""), author)
                results.append({"action": "repo_created", "ok": True, **result})
            elif action == "COMMIT":
                result = client.commit_files(cmd["project"], cmd["message"], cmd["files"], author)
                results.append({"action": "committed", "ok": True, "project": cmd["project"], **result})
            elif action == "TREE":
                result = client.get_tree(cmd["project"], path=cmd.get("path"))
                results.append({"action": "tree", "ok": True, "project": cmd["project"],
                                "path": cmd.get("path", ""), "entries": result})
            elif action == "FILE_READ":
                result = client.get_file(cmd["project"], cmd["path"])
                results.append({"action": "file_read", "ok": True, "project": cmd["project"], **result})
            elif action == "LOG":
                result = client.get_log(cmd["project"])
                results.append({"action": "log", "ok": True, "project": cmd["project"], "commits": result})
        except Exception as e:
            results.append({"action": action.lower(), "ok": False, "error": str(e)})
    return results


def _format_gitlab_results(result: dict) -> str | None:
    """Format a read-only GitLab result as a system message string.

    Returns None for write operations (repo_created, committed).
    """
    action = result.get("action")

    if action == "tree":
        project = result.get("project", "?")
        path = result.get("path", "") or "/"
        entries = result.get("entries", [])
        if not entries:
            return f'[GitLab Tree] {project}:{path} — (empty)'
        lines = [f'[GitLab Tree] {project}:{path}']
        for e in entries:
            icon = "dir" if e.get("type") == "dir" else "file"
            lines.append(f"  [{icon}] {e.get('name', '?')}")
        return "\n".join(lines)

    if action == "file_read":
        project = result.get("project", "?")
        path = result.get("path", "?")
        content = result.get("content", "")
        return f'[GitLab File] {project}:{path}\n```\n{content}\n```'

    if action == "log":
        project = result.get("project", "?")
        commits = result.get("commits", [])
        if not commits:
            return f'[GitLab Log] {project} — no commits yet'
        lines = [f'[GitLab Log] {project} ({len(commits)} commits)']
        for c in commits[:10]:
            lines.append(f"  {c.get('id', '?')} {c.get('message', '')} — {c.get('author', '?')}")
        return "\n".join(lines)

    return None


def _extract_channel_commands(text: str) -> tuple[str, list[str]]:
    """Parse <<<CHANNEL:JOIN #name>>> commands from text.

    Returns (cleaned_text, list_of_channel_names_to_join).
    """
    channels_to_join = []
    for match in _CHANNEL_JOIN_RE.finditer(text):
        channels_to_join.append(match.group(1))

    cleaned = _CHANNEL_JOIN_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, channels_to_join


def _parse_multi_channel_response(text: str, default_channel: str) -> dict[str, str]:
    """Split text by [#channel-name] markers into channel -> content mapping.

    If no markers are found, the entire text maps to default_channel.
    """
    markers = list(_CHANNEL_MARKER_RE.finditer(text))
    if not markers:
        return {default_channel: text.strip()} if text.strip() else {}

    result = {}

    # Content before the first marker goes to default channel
    before_first = text[:markers[0].start()].strip()
    if before_first:
        result[default_channel] = before_first

    for i, marker in enumerate(markers):
        ch_name = "#" + marker.group(1)
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        content = text[start:end].strip()
        if content:
            if ch_name in result:
                result[ch_name] += "\n\n" + content
            else:
                result[ch_name] = content

    return result


async def _process_agent_response(
    client: ChatClient,
    response: str,
    persona: dict,
    default_channel: str,
) -> dict[str, str]:
    """Process a raw agent response: extract commands, parse multi-channel.

    Returns dict[channel, cleaned_text]. Empty dict if PASS.
    """
    # 1. Extract and execute doc commands
    cleaned, doc_commands = _extract_doc_commands(response)

    if doc_commands:
        author = persona["display_name"]
        results = _execute_doc_commands(client, doc_commands, author)
        for r in results:
            if r.get("action") == "search":
                msg_text = _format_search_results(r)
                client.post_message("System", msg_text, channel="#general")
                print(f"  {persona['display_name']}: doc search -> {len(r.get('results', []))} results")
            elif r.get("ok"):
                print(f"  {persona['display_name']}: doc {r['action']} -> {r.get('slug', r.get('title', '?'))}")
            else:
                print(f"  {persona['display_name']}: doc {r['action']} failed - {r.get('error', '?')}")

    # 2. Extract and execute GitLab commands
    cleaned, gitlab_commands = _extract_gitlab_commands(cleaned)

    if gitlab_commands:
        author = persona["display_name"]
        gl_results = _execute_gitlab_commands(client, gitlab_commands, author)
        for r in gl_results:
            formatted = _format_gitlab_results(r)
            if formatted:
                # Read-only results: post as System message
                client.post_message("System", formatted, channel="#general")
                print(f"  {persona['display_name']}: gitlab {r['action']} -> posted result")
            elif r.get("ok"):
                print(f"  {persona['display_name']}: gitlab {r['action']} -> {r.get('name', r.get('project', '?'))}")
            else:
                print(f"  {persona['display_name']}: gitlab {r['action']} failed - {r.get('error', '?')}")

    # 3. Extract channel join commands
    cleaned, channels_to_join = _extract_channel_commands(cleaned)

    for ch in channels_to_join:
        try:
            client.join_channel(ch, persona["name"])
            print(f"  {persona['display_name']}: joined {ch}")
        except Exception as e:
            print(f"  {persona['display_name']}: failed to join {ch} - {e}")

    # 4. Check for PASS
    if cleaned.upper() == "PASS" or not cleaned:
        return {}

    # 5. Parse multi-channel response
    return _parse_multi_channel_response(cleaned, default_channel)


def _is_agent_message(msg: dict) -> bool:
    """Return True if the message was posted by an agent."""
    return msg["sender"] in _AGENT_DISPLAY_NAMES


def _get_channel_memberships(client: ChatClient) -> dict[str, set[str]]:
    """Fetch current channel memberships from the server.

    Returns dict[channel_name, set_of_persona_keys].
    """
    channels = client.get_channels()
    return {ch["name"]: set(ch["members"]) for ch in channels}


async def _run_loop(
    client: ChatClient,
    pool: AgentPool,
    personas: list[dict],
    trigger_channels: set[str],
    max_waves: int,
) -> set[str]:
    """Run the event-driven response loop with tiered responses.

    Within each wave, agents respond in tiers (ICs first, then managers,
    then executives). Each tier sees the previous tier's responses before
    deciding whether to weigh in. Between waves, channels that received
    new messages become the next trigger set, up to max_waves.

    Returns the set of channels that received agent posts (empty if all PASS).
    """
    persona_map = {p["name"]: p for p in personas}
    wave = 0
    posted_channels: set[str] = set()

    while trigger_channels and wave < max_waves:
        wave += 1
        print(f"\n=== Wave {wave}/{max_waves} — triggered: {sorted(trigger_channels)} ===")

        # Get current memberships from server
        memberships = _get_channel_memberships(client)

        # Collect unique agents to trigger, tracking which channel triggered them
        agents_to_run: dict[str, set[str]] = {}  # persona_key -> set of trigger channels
        for ch in trigger_channels:
            members = memberships.get(ch, set())
            for persona_key in members:
                if persona_key in persona_map:
                    agents_to_run.setdefault(persona_key, set()).add(ch)

        if not agents_to_run:
            print("  No agents to trigger in these channels")
            break

        # Group agents by tier
        tiers: dict[int, dict[str, set[str]]] = {}  # tier -> {persona_key -> trigger channels}
        for pk, triggers in agents_to_run.items():
            tier = PERSONA_TIER.get(pk, 2)
            tiers.setdefault(tier, {})[pk] = triggers

        new_trigger_channels: set[str] = set()

        # Run tiers sequentially (1, 2, 3), agents within a tier sequentially
        # so each agent sees the previous agent's response before deciding
        for tier_num in sorted(tiers.keys()):
            tier_agents = tiers[tier_num]
            tier_names = ", ".join(
                persona_map[pk]["display_name"] for pk in sorted(tier_agents)
            )
            print(f"\n--- Tier {tier_num}: {tier_names} ---")

            for persona_key, triggered_by in tier_agents.items():
                persona = persona_map[persona_key]
                trigger_ch = sorted(triggered_by)[0]

                # Re-fetch history before each agent so it sees prior responses
                full_history = client.get_messages()
                docs = client.list_docs()
                repos = client.list_repos()

                # Collect all channels this agent is in
                all_agent_channels = set()
                for ch_name, ch_members in memberships.items():
                    if persona_key in ch_members:
                        all_agent_channels.add(ch_name)

                prompt = build_turn_prompt(
                    persona_key,
                    full_history,
                    trigger_channel=trigger_ch,
                    channels=all_agent_channels,
                    docs=docs,
                    repos=repos,
                )
                result = await pool.send(persona_key, prompt)

                if not result["success"]:
                    print(f"  {persona['display_name']}: failed, skipping")
                    continue

                response = result["response_text"].strip()
                channel_posts = await _process_agent_response(
                    client, response, persona, trigger_ch,
                )

                if not channel_posts:
                    print(f"  {persona['display_name']}: PASS")
                    continue

                for ch, content in channel_posts.items():
                    if ch not in memberships:
                        print(f"  {persona['display_name']}: skipping unknown channel {ch}")
                        continue
                    client.post_message(persona["display_name"], content, channel=ch)
                    print(f"  {persona['display_name']}: posted to {ch} ({len(content)} chars)")
                    posted_channels.add(ch)
                    if ch not in trigger_channels:
                        new_trigger_channels.add(ch)

        trigger_channels = new_trigger_channels

    if wave >= max_waves and trigger_channels:
        print(f"\n  Ripple limit reached ({max_waves} waves)")

    return posted_channels


async def run_orchestrator(args) -> None:
    """Main orchestrator loop: poll for messages, run agents, post responses."""
    client = ChatClient(base_url=args.server_url)
    personas = get_active_personas(getattr(args, "personas", None))
    model = getattr(args, "model", "sonnet")
    max_waves = getattr(args, "max_rounds", 3)
    poll_interval = getattr(args, "poll_interval", 5.0)

    max_auto_rounds = getattr(args, "max_auto_rounds", 3)

    if not personas:
        print("Error: no valid personas selected")
        return

    print(f"Orchestrator starting")
    print(f"  Server: {args.server_url}")
    print(f"  Model: {model}")
    print(f"  Personas: {', '.join(p['display_name'] for p in personas)}")
    print(f"  Max waves: {max_waves}")
    print(f"  Max autonomous rounds: {'unlimited' if max_auto_rounds == 0 else max_auto_rounds}")
    print(f"  Poll interval: {poll_interval}s")

    # Wait for server to be reachable
    while not client.health_check():
        print("Waiting for chat server...")
        await asyncio.sleep(2)
    print("Connected to chat server")

    # Open persistent agent sessions
    pool = AgentPool(personas, model, LOG_DIR)
    await pool.start(build_initial_prompt)

    # Skip any existing messages
    existing = client.get_messages()
    last_seen_id = existing[-1]["id"] if existing else 0
    print(f"Skipping {len(existing)} existing messages (last_seen_id={last_seen_id})")

    try:
        while True:
            new_messages = client.get_messages(since=last_seen_id)

            # Only trigger on non-agent messages (human input)
            human_messages = [m for m in new_messages if not _is_agent_message(m)]

            if not human_messages:
                await asyncio.sleep(poll_interval)
                continue

            # Update last_seen_id
            if new_messages:
                last_seen_id = new_messages[-1]["id"]

            # Determine which channels have new human messages
            trigger_channels = {m.get("channel", "#general") for m in human_messages}
            print(f"\nNew human message(s) in {sorted(trigger_channels)}")

            active_channels = await _run_loop(client, pool, personas, trigger_channels, max_waves)

            # Update last_seen_id to include any agent responses
            latest = client.get_messages()
            if latest:
                last_seen_id = latest[-1]["id"]

            # Autonomous continuation: let agents keep working
            # as long as they're producing output
            auto_round = 0
            while active_channels:
                if max_auto_rounds > 0 and auto_round >= max_auto_rounds:
                    print(f"\n  Autonomous round limit reached ({max_auto_rounds})")
                    break
                auto_round += 1

                # Brief pause — check for new human input first
                await asyncio.sleep(1)
                new_messages = client.get_messages(since=last_seen_id)
                human_messages = [m for m in new_messages if not _is_agent_message(m)]
                if human_messages:
                    print(f"\nHuman input detected — breaking autonomous continuation")
                    break

                limit_str = f"/{max_auto_rounds}" if max_auto_rounds > 0 else ""
                print(f"\n>>> Autonomous round {auto_round}{limit_str}"
                      f" — agents continuing in {sorted(active_channels)}")

                active_channels = await _run_loop(
                    client, pool, personas, active_channels, max_waves,
                )

                latest = client.get_messages()
                if latest:
                    last_seen_id = latest[-1]["id"]

            if auto_round > 0:
                print(f"\nAgents quiesced after {auto_round} autonomous round(s)")

            print(f"\nWaiting for new messages (last_seen_id={last_seen_id})...")
    finally:
        await pool.close()
