"""Orchestrator — event-driven multi-channel agent loop."""

import re
import asyncio
import threading
import time as _time
from pathlib import Path

from lib.chat_client import ChatClient
from lib.response_schema import parse_json_response, normalize_commands, extract_messages
from lib.personas import (
    get_active_personas,
    build_initial_prompt,
    build_turn_prompt,
    DEFAULT_CHANNELS,
    DEFAULT_MEMBERSHIPS,
    PERSONAS,
    RESPONSE_TIERS,
    PERSONA_TIER,
)
from lib.agent_runner import AgentPool

LOG_DIR = Path(__file__).parent.parent / "var" / "logs"

# -- DM (Direct Message) queue --
# recipient_key -> [{from_key, from_name, text, timestamp}]
_dm_queue: dict[str, list[dict]] = {}
_dm_queue_lock = threading.Lock()
MAX_DMS_PER_TURN = 2


def get_dm_queue() -> dict:
    """Return a snapshot of the DM queue (for session persistence)."""
    with _dm_queue_lock:
        return {k: list(v) for k, v in _dm_queue.items()}


def set_dm_queue(data: dict) -> None:
    """Replace the DM queue with saved data (for session restore)."""
    with _dm_queue_lock:
        _dm_queue.clear()
        _dm_queue.update(data)


def _queue_dms(client, persona, dm_cmds):
    """Queue DMs from an agent, enforcing the per-turn cap."""
    sender_key = persona["name"]
    sender_name = persona["display_name"]
    queued = 0
    with _dm_queue_lock:
        for dm in dm_cmds[:MAX_DMS_PER_TURN]:
            recipient = dm.get("to", "")
            text = dm.get("text", "").strip()
            if not recipient or not text:
                continue
            if recipient not in PERSONAS:
                print(f"  {sender_name}: dm to unknown agent {recipient}, skipped")
                continue
            _dm_queue.setdefault(recipient, []).append({
                "from_key": sender_key,
                "from_name": sender_name,
                "text": text,
                "timestamp": _time.time(),
            })
            queued += 1
            recip_name = PERSONAS[recipient]["display_name"]
            # Log to #dms for operator visibility
            client.post_message("System",
                f"[DM] {sender_name} \u2192 {recip_name}: {text}",
                channel="#dms")
            print(f"  {sender_name}: dm -> {recip_name}")
    if len(dm_cmds) > MAX_DMS_PER_TURN:
        print(f"  {sender_name}: {len(dm_cmds) - MAX_DMS_PER_TURN} DMs dropped (cap={MAX_DMS_PER_TURN})")


def _pop_pending_dms(persona_key: str) -> list[dict]:
    """Pop and return pending DMs for an agent, clearing the queue."""
    with _dm_queue_lock:
        return _dm_queue.pop(persona_key, [])


def _get_agent_display_names() -> set[str]:
    """Get agent display names dynamically (PERSONAS populated after scenario load)."""
    return {p["display_name"] for p in PERSONAS.values()}

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

_DOC_READ_RE = re.compile(
    r'<<<DOC:READ\s+'
    r'folder="([^"]+)"\s+'
    r'slug="([^"]+)"'
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

# -- Tickets command regexes --

_TICKETS_CREATE_RE = re.compile(
    r'<<<TICKETS:CREATE\s+'
    r'title="([^"]+)"'
    r'(?:\s+assignee="([^"]*)")?'
    r'(?:\s+priority="([^"]*)")?'
    r'(?:\s+blocked_by="([^"]*)")?'
    r'\s*>>>'
    r'(.*?)'
    r'<<<END_TICKETS>>>',
    re.DOTALL,
)

_TICKETS_UPDATE_RE = re.compile(
    r'<<<TICKETS:UPDATE\s+'
    r'id="([^"]+)"'
    r'(?:\s+status="([^"]*)")?'
    r'(?:\s+assignee="([^"]*)")?'
    r'\s*/>>>',
)

_TICKETS_COMMENT_RE = re.compile(
    r'<<<TICKETS:COMMENT\s+'
    r'id="([^"]+)"'
    r'\s*>>>'
    r'(.*?)'
    r'<<<END_TICKETS>>>',
    re.DOTALL,
)

_TICKETS_DEPENDS_RE = re.compile(
    r'<<<TICKETS:DEPENDS\s+'
    r'id="([^"]+)"\s+'
    r'blocked_by="([^"]+)"'
    r'\s*/>>>',
)

_TICKETS_LIST_RE = re.compile(
    r'<<<TICKETS:LIST'
    r'(?:\s+status="([^"]*)")?'
    r'(?:\s+assignee="([^"]*)")?'
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

    for match in _DOC_READ_RE.finditer(text):
        commands.append({"action": "READ", "folder": match.group(1), "slug": match.group(2)})

    cleaned = _DOC_BLOCK_RE.sub("", text)
    cleaned = _DOC_SEARCH_RE.sub("", cleaned)
    cleaned = _DOC_READ_RE.sub("", cleaned)
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
            elif action == "READ":
                result = client.get_doc(cmd["folder"], cmd["slug"])
                results.append({"action": "read", "ok": True, **result})
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


def _format_doc_read_result(result: dict) -> str | None:
    """Format a DOC:READ result as a system message with full content."""
    if result.get("action") != "read":
        return None
    title = result.get("title", result.get("slug", "?"))
    slug = result.get("slug", "?")
    folder = result.get("folder", "shared")
    content = result.get("content", "")
    return f'[Document] {title} (folder: {folder}, slug: {slug}):\n\n{content}'


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


def _extract_tickets_commands(text: str) -> tuple[str, list[dict]]:
    """Parse TICKETS commands from agent response text.

    Returns (cleaned_text, commands_list).
    """
    commands = []

    for match in _TICKETS_CREATE_RE.finditer(text):
        cmd = {
            "action": "CREATE",
            "title": match.group(1),
            "assignee": match.group(2) or "",
            "priority": match.group(3) or "medium",
            "description": match.group(5).strip(),
        }
        if match.group(4):
            cmd["blocked_by"] = [b.strip() for b in match.group(4).split(",") if b.strip()]
        commands.append(cmd)

    for match in _TICKETS_UPDATE_RE.finditer(text):
        cmd = {"action": "UPDATE", "id": match.group(1)}
        if match.group(2):
            cmd["status"] = match.group(2)
        if match.group(3):
            cmd["assignee"] = match.group(3)
        commands.append(cmd)

    for match in _TICKETS_COMMENT_RE.finditer(text):
        commands.append({
            "action": "COMMENT",
            "id": match.group(1),
            "text": match.group(2).strip(),
        })

    for match in _TICKETS_DEPENDS_RE.finditer(text):
        commands.append({
            "action": "DEPENDS",
            "id": match.group(1),
            "blocked_by": match.group(2),
        })

    for match in _TICKETS_LIST_RE.finditer(text):
        cmd = {"action": "LIST"}
        if match.group(1):
            cmd["status"] = match.group(1)
        if match.group(2):
            cmd["assignee"] = match.group(2)
        commands.append(cmd)

    cleaned = _TICKETS_CREATE_RE.sub("", text)
    cleaned = _TICKETS_UPDATE_RE.sub("", cleaned)
    cleaned = _TICKETS_COMMENT_RE.sub("", cleaned)
    cleaned = _TICKETS_DEPENDS_RE.sub("", cleaned)
    cleaned = _TICKETS_LIST_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned, commands


def _execute_tickets_commands(
    client: ChatClient,
    commands: list[dict],
    author: str,
) -> list[dict]:
    """Execute a list of parsed tickets commands via ChatClient."""
    results = []
    for cmd in commands:
        action = cmd["action"]
        try:
            if action == "CREATE":
                result = client.create_ticket(
                    title=cmd["title"],
                    description=cmd.get("description", ""),
                    priority=cmd.get("priority", "medium"),
                    assignee=cmd.get("assignee", ""),
                    author=author,
                    blocked_by=cmd.get("blocked_by"),
                )
                results.append({"action": "created", "ok": True, **result})
            elif action == "UPDATE":
                result = client.update_ticket(
                    ticket_id=cmd["id"],
                    author=author,
                    status=cmd.get("status"),
                    assignee=cmd.get("assignee"),
                )
                results.append({"action": "updated", "ok": True, **result})
            elif action == "COMMENT":
                result = client.comment_ticket(cmd["id"], cmd["text"], author)
                results.append({"action": "commented", "ok": True, "id": cmd["id"]})
            elif action == "DEPENDS":
                result = client.add_dependency(cmd["id"], cmd["blocked_by"])
                results.append({"action": "depends", "ok": True, **result})
            elif action == "LIST":
                result = client.list_tickets(
                    status=cmd.get("status"),
                    assignee=cmd.get("assignee"),
                )
                results.append({"action": "list", "ok": True, "tickets": result})
        except Exception as e:
            results.append({"action": action.lower(), "ok": False, "error": str(e)})
    return results


def _format_tickets_results(result: dict) -> str | None:
    """Format a tickets LIST result as a system message string.

    Returns None for write operations (created, updated, commented, depends).
    """
    if result.get("action") != "list":
        return None

    tickets = result.get("tickets", [])
    if not tickets:
        return "[Tickets] No tickets found."

    lines = [f"[Tickets] {len(tickets)} ticket(s):"]
    for t in tickets:
        assignee = t.get("assignee") or "Unassigned"
        blocked = f" (blocked by {', '.join(t.get('blocked_by', []))})" if t.get("blocked_by") else ""
        lines.append(
            f"  {t.get('id', '?')} [{t.get('status', '?')}] [{t.get('priority', '?')}] "
            f"{t.get('title', '?')} — {assignee}{blocked}"
        )
    return "\n".join(lines)


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


def _post_system(client: ChatClient, text: str) -> None:
    """Post a system message to #system (operator-visible only)."""
    client.post_message("System", text, channel="#system")


def _log_doc_results(client: ChatClient, persona: dict, results: list[dict]) -> None:
    """Log and post doc command results."""
    for r in results:
        if r.get("action") == "search":
            msg_text = _format_search_results(r)
            _post_system(client, msg_text)
            print(f"  {persona['display_name']}: doc search -> {len(r.get('results', []))} results")
        elif r.get("action") == "read":
            formatted = _format_doc_read_result(r)
            if formatted:
                _post_system(client, formatted)
                print(f"  {persona['display_name']}: doc read -> {r.get('slug', '?')}")
        elif r.get("ok"):
            print(f"  {persona['display_name']}: doc {r['action']} -> {r.get('slug', r.get('title', '?'))}")
        else:
            print(f"  {persona['display_name']}: doc {r['action']} failed - {r.get('error', '?')}")


def _log_gitlab_results(client: ChatClient, persona: dict, results: list[dict]) -> None:
    """Log and post GitLab command results."""
    for r in results:
        formatted = _format_gitlab_results(r)
        if formatted:
            _post_system(client, formatted)
            print(f"  {persona['display_name']}: gitlab {r['action']} -> posted result")
        elif r.get("ok"):
            print(f"  {persona['display_name']}: gitlab {r['action']} -> {r.get('name', r.get('project', '?'))}")
        else:
            print(f"  {persona['display_name']}: gitlab {r['action']} failed - {r.get('error', '?')}")


def _log_tickets_results(client: ChatClient, persona: dict, results: list[dict]) -> None:
    """Log and post tickets command results."""
    for r in results:
        formatted = _format_tickets_results(r)
        if formatted:
            _post_system(client, formatted)
            print(f"  {persona['display_name']}: tickets list -> {len(r.get('tickets', []))} tickets")
        elif r.get("ok"):
            print(f"  {persona['display_name']}: ticket {r['action']} -> {r.get('id', r.get('title', '?'))}")
        else:
            print(f"  {persona['display_name']}: ticket {r['action']} failed - {r.get('error', '?')}")


async def _process_json_response(
    client: ChatClient,
    parsed: dict,
    persona: dict,
    default_channel: str,
    on_activity=None,
) -> dict[str, str]:
    """Process a parsed JSON agent response: execute commands, extract messages.

    Returns dict[channel, text]. Empty dict if action is 'pass' or 'ready'.
    """
    author = persona["display_name"]

    # 1. Normalize and execute commands
    doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join, dm_cmds = normalize_commands(parsed)

    if doc_cmds:
        if on_activity:
            on_activity("writing docs")
        results = _execute_doc_commands(client, doc_cmds, author)
        _log_doc_results(client, persona, results)

    if gitlab_cmds:
        if on_activity:
            on_activity("committing code")
        gl_results = _execute_gitlab_commands(client, gitlab_cmds, author)
        _log_gitlab_results(client, persona, gl_results)

    if tickets_cmds:
        if on_activity:
            on_activity("managing tickets")
        tk_results = _execute_tickets_commands(client, tickets_cmds, author)
        _log_tickets_results(client, persona, tk_results)

    for ch in channels_to_join:
        try:
            client.join_channel(ch, persona["name"])
            print(f"  {persona['display_name']}: joined {ch}")
        except Exception as e:
            print(f"  {persona['display_name']}: failed to join {ch} - {e}")

    if dm_cmds:
        _queue_dms(client, persona, dm_cmds)

    # 2. Extract channel-routed messages
    return extract_messages(parsed, default_channel)


async def _process_regex_response(
    client: ChatClient,
    response: str,
    persona: dict,
    default_channel: str,
) -> dict[str, str]:
    """Process a raw agent response using regex parsing (legacy fallback).

    Returns dict[channel, cleaned_text]. Empty dict if PASS.
    """
    # 1. Extract and execute doc commands
    cleaned, doc_commands = _extract_doc_commands(response)

    if doc_commands:
        author = persona["display_name"]
        results = _execute_doc_commands(client, doc_commands, author)
        _log_doc_results(client, persona, results)

    # 2. Extract and execute GitLab commands
    cleaned, gitlab_commands = _extract_gitlab_commands(cleaned)

    if gitlab_commands:
        author = persona["display_name"]
        gl_results = _execute_gitlab_commands(client, gitlab_commands, author)
        _log_gitlab_results(client, persona, gl_results)

    # 3. Extract and execute tickets commands
    cleaned, tickets_commands = _extract_tickets_commands(cleaned)

    if tickets_commands:
        author = persona["display_name"]
        tk_results = _execute_tickets_commands(client, tickets_commands, author)
        _log_tickets_results(client, persona, tk_results)

    # 4. Extract channel join commands
    cleaned, channels_to_join = _extract_channel_commands(cleaned)

    for ch in channels_to_join:
        try:
            client.join_channel(ch, persona["name"])
            print(f"  {persona['display_name']}: joined {ch}")
        except Exception as e:
            print(f"  {persona['display_name']}: failed to join {ch} - {e}")

    # 5. Check for PASS
    if cleaned.upper() == "PASS" or not cleaned:
        return {}

    # 5.5. Last-ditch JSON parse attempt — if regex couldn't strip commands
    # but the text is still JSON, try one more time
    stripped = cleaned.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            import json as _json
            parsed = _json.loads(stripped)
            if isinstance(parsed, dict) and parsed.get("action") in ("respond", "pass", "ready"):
                print(f"  {persona['display_name']}: recovered JSON in regex fallback")
                return await _process_json_response(client, parsed, persona, default_channel)
        except (ValueError, _json.JSONDecodeError):
            pass

    # 6. Parse multi-channel response
    return _parse_multi_channel_response(cleaned, default_channel)


async def _process_agent_response(
    client: ChatClient,
    response: str,
    persona: dict,
    default_channel: str,
    on_activity=None,
) -> dict[str, str]:
    """Process an agent response: try JSON first, fall back to regex parsing.

    Returns dict[channel, cleaned_text]. Empty dict if PASS.
    """
    parsed = parse_json_response(response)
    if parsed is not None:
        print(f"  {persona['display_name']}: parsed as JSON (action={parsed.get('action')})")
        return await _process_json_response(client, parsed, persona, default_channel, on_activity)
    else:
        return await _process_regex_response(client, response, persona, default_channel)


def _is_agent_message(msg: dict) -> bool:
    """Return True if the message was posted by an agent."""
    return msg["sender"] in _get_agent_display_names()


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
    scenario_name: str = "",
) -> set[str]:
    """Run the event-driven response loop with tiered responses.

    Within each wave, agents respond in tiers (ICs first, then managers,
    then executives). Agents within a tier run in parallel — they all get
    the same state snapshot and their LLM calls execute concurrently via
    asyncio.gather(). After all sends complete, responses are processed
    and posted sequentially to preserve message ordering.

    Tier-to-tier ordering is preserved: higher tiers see all lower tier
    output before running. Between waves, channels that received new
    messages become the next trigger set, up to max_waves.

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

        # Get online/offline status
        npcs = client.get_npcs()
        offline_keys = {n["key"] for n in npcs if not n.get("online", True)}
        verbosity_map = {n["key"]: n.get("verbosity", "normal") for n in npcs}

        # Collect unique agents to trigger, tracking which channel triggered them
        agents_to_run: dict[str, set[str]] = {}  # persona_key -> set of trigger channels
        for ch in trigger_channels:
            # Director channels trigger the specific agent they're for
            if ch.startswith("#director-"):
                pk = ch.replace("#director-", "")
                if pk in persona_map and pk not in offline_keys:
                    agents_to_run.setdefault(pk, set()).add(ch)
                elif pk in offline_keys:
                    print(f"  Skipping {persona_map.get(pk, {}).get('display_name', pk)}: out of office")
                continue
            members = memberships.get(ch, set())
            for persona_key in members:
                if persona_key in persona_map:
                    if persona_key in offline_keys:
                        continue
                    agents_to_run.setdefault(persona_key, set()).add(ch)

        if offline_keys:
            offline_names = [persona_map[k]["display_name"] for k in offline_keys if k in persona_map]
            if offline_names:
                print(f"  Out of office: {', '.join(offline_names)}")

        if not agents_to_run:
            print("  No agents to trigger in these channels")
            break

        # Group agents by tier
        tiers: dict[int, dict[str, set[str]]] = {}  # tier -> {persona_key -> trigger channels}
        for pk, triggers in agents_to_run.items():
            tier = PERSONA_TIER.get(pk, 2)
            tiers.setdefault(tier, {})[pk] = triggers

        new_trigger_channels: set[str] = set()

        # Run tiers sequentially (1, 2, 3), agents within a tier in parallel.
        # All agents in a tier get the same state snapshot and run concurrently.
        # Tier-to-tier ordering is preserved so higher tiers see lower tier output.
        for tier_num in sorted(tiers.keys()):
            tier_agents = tiers[tier_num]
            tier_names = ", ".join(
                persona_map[pk]["display_name"] for pk in sorted(tier_agents)
            )
            print(f"\nWave {wave}, Tier {tier_num}: running {len(tier_agents)} agent(s) "
                  f"in parallel ({tier_names})")

            # 1. Fetch state ONCE per tier (shared snapshot)
            full_history = client.get_messages()
            docs = client.list_docs()
            repos = client.list_repos()
            tickets = client.list_tickets()

            # 2. Build prompts and launch all sends in parallel
            async def _run_agent(pk, trigger_ch):
                persona = persona_map[pk]
                all_agent_channels = {
                    ch_name for ch_name, ch_members in memberships.items()
                    if pk in ch_members
                }
                pending_dms = _pop_pending_dms(pk)
                prompt = build_turn_prompt(
                    pk,
                    full_history,
                    trigger_channel=trigger_ch,
                    channels=all_agent_channels,
                    docs=docs,
                    repos=repos,
                    tickets=tickets,
                    offline_agents=offline_keys,
                    pending_dms=pending_dms,
                    verbosity=verbosity_map.get(pk, "normal"),
                )
                # Show typing indicator and update agent status
                display_name = persona["display_name"]
                for ch in all_agent_channels:
                    client.set_typing(display_name, ch, active=True)

                # Update heartbeat to show this agent is responding
                agents_status = _build_agent_status(personas, pool)
                agents_status[pk]["state"] = "responding"
                client.send_heartbeat("responding", scenario_name, agents_status,
                                      f"{display_name} is thinking...", check_commands=False)

                result = await pool.send(pk, prompt)

                # Clear typing indicators
                for ch in all_agent_channels:
                    client.set_typing(display_name, ch, active=False)

                return pk, trigger_ch, result, all_agent_channels

            tasks = [
                _run_agent(pk, sorted(triggered_by)[0])
                for pk, triggered_by in tier_agents.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 3. Process responses sequentially (preserves posting order)
            for entry in results:
                if isinstance(entry, Exception):
                    print(f"  [Tier {tier_num}] agent task failed: {entry}")
                    continue

                persona_key, trigger_ch, result, all_agent_channels = entry
                persona = persona_map[persona_key]
                display_name = persona["display_name"]

                # Reset agent status after response
                agents_status = _build_agent_status(personas, pool)
                client.send_heartbeat("responding", scenario_name, agents_status,
                                      "Processing messages...", check_commands=False)

                if not result["success"]:
                    agents_status = _build_agent_status(personas, pool)
                    client.send_heartbeat("responding", scenario_name, agents_status,
                                          "Processing messages...", check_commands=False)
                    print(f"  {display_name}: failed, skipping")
                    continue

                response = result["response_text"].strip()
                thinking = result.get("thinking_text", "")

                # Post agent thoughts to the server
                if thinking or response:
                    client.post_thoughts(persona_key, thinking, response)

                # Update status during command processing
                def _update_agent_activity(activity, _pk=persona_key, _dn=display_name):
                    s = _build_agent_status(personas, pool)
                    s[_pk]["state"] = activity
                    client.send_heartbeat("responding", scenario_name, s,
                                          f"{_dn}: {activity}...", check_commands=False)

                _update_agent_activity("processing commands")
                channel_posts = await _process_agent_response(
                    client, response, persona, trigger_ch, _update_agent_activity,
                )

                # Reset status
                agents_status = _build_agent_status(personas, pool)
                client.send_heartbeat("responding", scenario_name, agents_status,
                                      "Processing messages...", check_commands=False)

                print(f"  {display_name}: response={len(response)} chars, channels={list(channel_posts.keys())}")

                if not channel_posts:
                    print(f"  {display_name}: PASS")
                    continue

                for ch, content in channel_posts.items():
                    # Allow agents to post to their own director channel
                    own_director = ch == f"#director-{persona_key}"
                    if not own_director and ch not in memberships:
                        print(f"  {display_name}: skipping unknown channel {ch}")
                        continue
                    if not own_director and persona_key not in memberships.get(ch, set()):
                        print(f"  {display_name}: skipping {ch} — not a member")
                        continue
                    client.post_message(display_name, content, channel=ch)
                    print(f"  {display_name}: posted to {ch} ({len(content)} chars)")
                    posted_channels.add(ch)
                    if ch not in trigger_channels:
                        new_trigger_channels.add(ch)

        # Check for pending DMs — trigger recipients in the next wave
        with _dm_queue_lock:
            dm_recipients = {k for k in _dm_queue if k in persona_map and _dm_queue[k]}
        if dm_recipients:
            for pk in dm_recipients:
                for ch_name, ch_members in memberships.items():
                    if pk in ch_members:
                        new_trigger_channels.add(ch_name)
                        break
            dm_names = ", ".join(persona_map[pk]["display_name"] for pk in dm_recipients)
            print(f"  Pending DMs for: {dm_names} — triggering next wave")

        trigger_channels = new_trigger_channels

    if wave >= max_waves and trigger_channels:
        print(f"\n  Ripple limit reached ({max_waves} waves)")

    return posted_channels


def _requeue_restart(base_url: str, scenario_name: str) -> None:
    """Re-queue a restart command to the server using a sync HTTP request.

    Called when the orchestrator is about to crash from a CancelledError
    so the command survives the restart.
    """
    import requests as sync_requests
    try:
        resp = sync_requests.post(
            f"{base_url}/api/orchestrator/command",
            json={"action": "restart", "scenario": scenario_name},
            timeout=5,
        )
        print(f"  Re-queue response: {resp.status_code}")
    except Exception as e:
        print(f"  Re-queue failed: {e}")


async def _process_single_command(client, pool, personas, scenario_name, cmd):
    """Process a single add/remove agent command.

    Returns (updated personas list, restart_requested bool).
    """
    action = cmd.get("action")

    if action == "add_agent":
        agent_key = cmd.get("key", "")
        if agent_key and agent_key not in pool._clients:
            npcs = client.get_npcs()
            npc_data = next((n for n in npcs if n["key"] == agent_key), None)
            if npc_data:
                persona = {
                    "name": agent_key,
                    "display_name": npc_data["display_name"],
                    "team_description": npc_data.get("team_description", ""),
                    "character_file": npc_data.get("character_file", ""),
                }
                PERSONAS[agent_key] = persona
                DEFAULT_MEMBERSHIPS[agent_key] = set(npc_data.get("channels", ["#general"]))
                tier = npc_data.get("tier", 1)
                PERSONA_TIER[agent_key] = tier
                RESPONSE_TIERS.setdefault(tier, [])
                if agent_key not in RESPONSE_TIERS[tier]:
                    RESPONSE_TIERS[tier].append(agent_key)
                pool._personas[agent_key] = persona
                print(f"\n*** Adding agent: {persona['display_name']} ***")
                try:
                    await pool._open_session(agent_key, build_initial_prompt)
                    personas = list(pool._personas.values())
                    client.post_message("System", f"{persona['display_name']} has joined the team!", channel="#system")
                    client.post_message("System", f"Welcome {persona['display_name']} to the team!", channel="#general")
                    print(f"  Agent added: {persona['display_name']}")
                except (Exception, BaseException) as e:
                    print(f"  Failed to add agent: {e}")
                    # Re-queue for retry (likely CancelledError from prior remove)
                    print(f"  Re-queuing add_agent for {agent_key}")
                    try:
                        import requests as _req
                        _req.post(f"{client.base_url}/api/orchestrator/command",
                                  json={"action": "add_agent", "key": agent_key}, timeout=5)
                    except Exception:
                        pass
                    # Clean up partial state
                    pool._personas.pop(agent_key, None)
            else:
                print(f"  Agent {agent_key} not found on server")

    elif action == "remove_agent":
        agent_key = cmd.get("key", "")
        if agent_key:
            display_name = pool._personas.get(agent_key, {}).get("display_name", agent_key)
            print(f"\n*** Removing agent: {display_name} ***")
            if agent_key in pool._clients:
                try:
                    await pool._close_session(agent_key)
                except (Exception, BaseException):
                    pass
            pool._personas.pop(agent_key, None)
            PERSONAS.pop(agent_key, None)
            DEFAULT_MEMBERSHIPS.pop(agent_key, None)
            old_tier = PERSONA_TIER.pop(agent_key, None)
            if old_tier and old_tier in RESPONSE_TIERS:
                if agent_key in RESPONSE_TIERS[old_tier]:
                    RESPONSE_TIERS[old_tier].remove(agent_key)
            personas = list(pool._personas.values())
            # Finalize on server (remove from server's PERSONAS)
            try:
                import requests as _req
                _req.post(f"{client.base_url}/api/npcs/{agent_key}/finalize-fire", timeout=5)
            except Exception:
                pass
            client.post_message("System", f"{display_name} has left the company.", channel="#system")
            client.post_message("System", f"{display_name} has left the company.", channel="#general")
            print(f"  Agent removed: {display_name}")

    elif action == "restart":
        return personas, True

    return personas, False


async def _process_pending_commands(client, pool, personas, scenario_name):
    """Drain the command queue via heartbeat, processing all pending commands.

    Returns (updated personas list, restart_requested bool).
    Catches CancelledError from SDK cancel scope leaks after remove_agent.
    """
    try:
        return await _process_pending_commands_inner(client, pool, personas, scenario_name)
    except (asyncio.CancelledError, BaseException) as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        print(f"  Command processing interrupted ({type(e).__name__}), continuing...")
        try:
            await asyncio.sleep(0)
        except (Exception, BaseException):
            pass
        return list(pool._personas.values()), False


async def _process_pending_commands_inner(client, pool, personas, scenario_name):
    """Inner implementation of command drain loop."""
    while True:
        agents = _build_agent_status(personas, pool)
        cmd = client.send_heartbeat("ready", scenario_name, agents)
        action = cmd.get("action")
        if not action:
            break
        print(f"\n  Pending command: {cmd}")
        personas, restart = await _process_single_command(client, pool, personas, scenario_name, cmd)
        if restart:
            return personas, True
    return personas, False


def _build_agent_status(personas: list[dict], pool: AgentPool | None = None) -> dict:
    """Build agent status dict for heartbeat."""
    result = {}
    for p in personas:
        key = p["name"]
        has_session = pool is not None and key in pool._clients
        result[key] = {
            "display_name": p["display_name"],
            "state": "ready" if has_session else "offline",
        }
    return result


async def _start_agents(client, personas, model, scenario_name):
    """Spin up agent pool and announce agents online.

    Returns (pool, interrupted). If interrupted is True, a restart command
    was received during startup.
    """
    pool = AgentPool(personas, model, LOG_DIR)
    total = len(personas)
    pending_cmd = [None]  # saves the command that caused the interrupt

    online_names = []

    def on_progress(i, tot, key, display_name, state):
        agents = _build_agent_status(personas, pool)
        agents[key]["state"] = state
        if state == "starting":
            msg = f"Starting agent {i}/{tot}: {display_name}..."
        else:
            online_names.append(display_name)
            msg = f"Agent ready {i}/{tot}: {display_name}"
            online_list = ", ".join(online_names)
            client.post_message("System",
                f"{display_name} is online ({i}/{tot})\n\nAgents online: {online_list}",
                channel="#system")
        # Update status only — don't consume commands during agent init
        client.send_heartbeat("starting", scenario_name, agents, msg, check_commands=False)
        return False

    await pool.start(build_initial_prompt, on_progress=on_progress)

    if pending_cmd[0]:
        print("Startup interrupted by pending command")
        # Announce the agents that came online are going offline
        for i, name in enumerate(reversed(online_names), 1):
            remaining = [n for n in online_names if n != name]
            online_names.remove(name)
            if remaining:
                client.post_message("System",
                    f"{name} is offline (startup cancelled)\n\nAgents still online: {', '.join(remaining)}",
                    channel="#system")
            else:
                client.post_message("System",
                    f"{name} is offline (startup cancelled)\n\nAll agents offline.",
                    channel="#system")
        return pool, pending_cmd[0]

    # Send ready heartbeat
    agents = _build_agent_status(personas, pool)
    client.send_heartbeat("ready", scenario_name, agents, "All agents ready", check_commands=False)

    return pool, None


async def _stop_agents(client, pool, personas, scenario_name=""):
    """Shut down agent pool and announce agents offline."""
    if not pool:
        return
    names = [p["display_name"] for p in personas]
    remaining = list(names)
    total = len(names)
    for i, name in enumerate(names, 1):
        remaining.remove(name)
        if remaining:
            online_list = ", ".join(remaining)
            client.post_message("System",
                f"{name} is offline ({i}/{total})\n\nAgents still online: {online_list}",
                channel="#system")
        else:
            client.post_message("System",
                f"{name} is offline ({i}/{total})\n\nAll agents offline.",
                channel="#system")
        agents = _build_agent_status(personas, pool)
        agents[personas[i-1]["name"]]["state"] = "offline"
        client.send_heartbeat("stopping", scenario_name, agents,
                              f"Stopping agent {i}/{total}: {name}", check_commands=False)
    await pool.close()


async def run_orchestrator(args) -> None:
    """Main orchestrator loop: poll for messages, run agents, post responses."""
    client = ChatClient(base_url=args.server_url)
    model = getattr(args, "model", "sonnet")
    scenario_name = getattr(args, "scenario", None) or "unknown"
    max_waves = getattr(args, "max_rounds", 3)
    poll_interval = getattr(args, "poll_interval", 5.0)
    max_auto_rounds = getattr(args, "max_auto_rounds", 3)
    personas = []  # populated when scenario loads

    print(f"Orchestrator starting")
    print(f"  Server: {args.server_url}")
    print(f"  Model: {model}")
    print(f"  Max waves: {max_waves}")
    print(f"  Max autonomous rounds: {'unlimited' if max_auto_rounds == 0 else max_auto_rounds}")
    print(f"  Poll interval: {poll_interval}s")

    # Wait for server to be reachable
    while not client.health_check():
        client.send_heartbeat("connecting", scenario_name, {}, "Waiting for server...", check_commands=False)
        print("Waiting for chat server...")
        await asyncio.sleep(2)
    print("Connected to chat server")

    # Wait for a session to be started (New or Load) before spinning up agents
    pool = None
    last_seen_id = 0

    # Send initial status — don't consume commands yet, the waiting loop handles that
    client.send_heartbeat("waiting", scenario_name, {},
                          "Checking for pending commands...", check_commands=False)
    next_cmd = None

    print("Waiting for session start (click New or Load in the UI)...")
    while pool is None:
        if next_cmd:
            cmd = next_cmd
            next_cmd = None
        else:
            cmd = client.send_heartbeat("waiting", scenario_name, {},
                                        "Waiting for session — click New or Load")
        action = cmd.get("action")
        # Handle remove commands even in waiting state (finalize on server)
        if action == "remove_agent":
            agent_key = cmd.get("key", "")
            if agent_key:
                try:
                    import requests as _req
                    _req.post(f"{client.base_url}/api/npcs/{agent_key}/finalize-fire", timeout=5)
                    print(f"  Finalized fire for {agent_key} (no active session)")
                except Exception:
                    pass
            continue
        if action == "add_agent":
            print(f"  Ignoring add_agent for {cmd.get('key','')} (no active session)")
            continue
        if action == "restart":
            new_scenario = cmd.get("scenario", scenario_name)
            if new_scenario != scenario_name:
                from lib.scenario_loader import load_scenario
                load_scenario(new_scenario)
                scenario_name = new_scenario
            personas = get_active_personas(getattr(args, "personas", None))
            print(f"\nStarting session: {scenario_name}")
            print(f"  Personas: {', '.join(p['display_name'] for p in personas)}")
            try:
                pool, pending = await _start_agents(client, personas, model, scenario_name)
            except (Exception, BaseException):
                # SDK cancel scope crash — re-queue the command via a sync request
                # so we pick it up after main.py restarts us
                print(f"Agent startup crashed, re-queuing restart for {scenario_name}")
                _requeue_restart(client.base_url, scenario_name)
                raise
            if pending:
                # Another command came in during startup — close partial pool and retry
                print(f"Interrupted — new command: {pending.get('action')} ({pending.get('scenario')})")
                try:
                    await pool.close()
                except (Exception, BaseException):
                    pass
                try:
                    await asyncio.sleep(2)
                except (Exception, BaseException):
                    pass
                pool = None
                next_cmd = pending  # re-queue the interrupting command
                continue
            existing = client.get_messages()
            last_seen_id = existing[-1]["id"] if existing else 0
            print(f"Skipping {len(existing)} existing messages (last_seen_id={last_seen_id})")
        else:
            await asyncio.sleep(poll_interval)

    try:
        while True:
            # Check for commands from the server
            agents = _build_agent_status(personas, pool)
            cmd = client.send_heartbeat("ready", scenario_name, agents)
            if cmd.get("action"):
                print(f"\n  Command received: {cmd}")
            if cmd.get("action") == "restart":
                new_scenario = cmd.get("scenario", scenario_name)
                print(f"\n*** Restart command received (scenario: {new_scenario}) ***")

                # Shut down current agents
                try:
                    await _stop_agents(client, pool, personas, scenario_name)
                except (Exception, BaseException):
                    print("Warning: error during agent shutdown, continuing restart")

                # Brief pause to let async resources clean up
                try:
                    await asyncio.sleep(2)
                except (Exception, BaseException):
                    pass

                # Reload scenario if changed
                if new_scenario != scenario_name:
                    from lib.scenario_loader import load_scenario
                    load_scenario(new_scenario)
                    scenario_name = new_scenario

                # Reload personas and start new agents
                personas = get_active_personas(getattr(args, "personas", None))
                try:
                    pool, pending = await _start_agents(client, personas, model, scenario_name)
                except (Exception, BaseException):
                    print(f"Agent startup crashed, re-queuing restart for {scenario_name}")
                    _requeue_restart(client.base_url, scenario_name)
                    raise

                if pending:
                    # Interrupted again — close and handle the new command next loop
                    print(f"Restart interrupted by another command")
                    try:
                        await pool.close()
                    except (Exception, BaseException):
                        pass
                    try:
                        await asyncio.sleep(2)
                    except (Exception, BaseException):
                        pass
                    # The pending command will be picked up on the next heartbeat
                    # Re-queue it by posting it back to the server
                    try:
                        import requests
                        requests.post(
                            f"{client.base_url}/api/orchestrator/command",
                            json=pending, timeout=5,
                        )
                    except Exception:
                        pass
                    continue

                # Reset message tracking
                existing = client.get_messages()
                last_seen_id = existing[-1]["id"] if existing else 0
                print(f"Restart complete. Skipping {len(existing)} existing messages.")
                continue

            if cmd.get("action") == "shutdown":
                print("\n*** Shutdown command received ***")
                break

            if cmd.get("action") in ("add_agent", "remove_agent"):
                # Process this command and drain any others in the queue
                personas, _ = await _process_single_command(client, pool, personas, scenario_name, cmd)
                personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
                if restart:
                    continue
                continue

            new_messages = client.get_messages(since=last_seen_id)

            # Only trigger on non-agent messages (human input)
            human_messages = [m for m in new_messages if not _is_agent_message(m)]

            if not human_messages:
                await asyncio.sleep(poll_interval)
                continue

            # Update heartbeat to show responding
            agents = _build_agent_status(personas, pool)
            client.send_heartbeat("responding", scenario_name, agents, "Processing messages...", check_commands=False)

            # Update last_seen_id
            if new_messages:
                last_seen_id = new_messages[-1]["id"]

            # Determine which channels have new human messages
            trigger_channels = {m.get("channel", "#general") for m in human_messages}
            print(f"\nNew human message(s) in {sorted(trigger_channels)}")

            active_channels = await _run_loop(client, pool, personas, trigger_channels, max_waves, scenario_name)

            # Reset all agents to ready and process any pending commands
            personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
            if restart:
                continue

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

                # Brief pause — process pending commands and check for new human input
                await asyncio.sleep(1)

                # Process add commands between rounds (removes deferred until quiesce)
                while True:
                    agents = _build_agent_status(personas, pool)
                    cmd = client.send_heartbeat("responding", scenario_name, agents, "Autonomous continuation...")
                    action = cmd.get("action")
                    if not action:
                        break
                    if action == "add_agent":
                        print(f"  Pending command: {cmd}")
                        personas, _ = await _process_single_command(client, pool, personas, scenario_name, cmd)
                    elif action == "remove_agent":
                        # Defer removal until autonomous rounds finish
                        print(f"  Deferring remove until quiesce: {cmd.get('key')}")
                        import requests as _req
                        try:
                            _req.post(f"{client.base_url}/api/orchestrator/command", json=cmd, timeout=5)
                        except Exception:
                            pass
                        break
                    elif action == "restart":
                        break
                    else:
                        break

                new_messages = client.get_messages(since=last_seen_id)
                human_messages = [m for m in new_messages if not _is_agent_message(m)]
                if human_messages:
                    print(f"\nHuman input detected — breaking autonomous continuation")
                    break

                limit_str = f"/{max_auto_rounds}" if max_auto_rounds > 0 else ""
                print(f"\n>>> Autonomous round {auto_round}{limit_str}"
                      f" — agents continuing in {sorted(active_channels)}")

                active_channels = await _run_loop(
                    client, pool, personas, active_channels, max_waves, scenario_name,
                )

                latest = client.get_messages()
                if latest:
                    last_seen_id = latest[-1]["id"]

            if auto_round > 0:
                print(f"\nAgents quiesced after {auto_round} autonomous round(s)")

            # Process any pending commands after quiesce
            personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
            if restart:
                continue

            # Auto-save session after each response cycle
            try:
                from lib.session import save_session
                save_session("autosave")
                print("Auto-saved session")
            except Exception as e:
                print(f"Auto-save failed: {e}")

            print(f"\nWaiting for new messages (last_seen_id={last_seen_id})...")
    finally:
        try:
            await _stop_agents(client, pool, personas, scenario_name)
        except (Exception, BaseException):
            pass
