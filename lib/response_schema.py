"""Structured JSON response schema for agent outputs.

Replaces regex-based <<<>>> command parsing with a clean JSON protocol.
Agents return one of:
    {"action": "respond", "messages": [...], "commands": [...]}
    {"action": "pass"}
    {"action": "ready"}
"""

import json
import re

_VALID_ACTIONS = {"respond", "pass", "ready"}

# Strip markdown code fences (```json ... ``` or ``` ... ```)
_CODE_FENCE_RE = re.compile(
    r'^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$',
    re.DOTALL,
)


def parse_json_response(text: str) -> dict | None:
    """Attempt to parse agent text as a JSON response.

    Strips markdown code fences if present. Tries multiple strategies
    to extract valid JSON. Returns None if no valid JSON with a valid
    'action' field can be found (triggers regex fallback in the orchestrator).
    """
    text = text.strip()
    if not text:
        return None

    # Strategy 1: Strip markdown code fences
    fence_match = _CODE_FENCE_RE.match(text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Strategy 2: Try direct parse
    parsed = _try_parse_json(text)
    if parsed:
        return parsed

    # Strategy 3: Find first { and last } — extract the JSON object
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace >= 0 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1]
        parsed = _try_parse_json(candidate)
        if parsed:
            return parsed

    # Strategy 4: Look for code fences anywhere in the text (not just wrapping it)
    fence_search = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if fence_search:
        parsed = _try_parse_json(fence_search.group(1).strip())
        if parsed:
            return parsed

    return None


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON with a valid action field."""
    if not text or not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    action = parsed.get("action")
    if action not in _VALID_ACTIONS:
        return None
    return parsed


def normalize_commands(parsed: dict) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Split the commands array by type into the flat dict format existing _execute_* functions expect.

    Returns (doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join).

    Each command's 'action' + 'params' are merged into a flat dict, e.g.:
        {"type": "doc", "action": "CREATE", "params": {"folder": "shared", "title": "..."}}
    becomes:
        {"action": "CREATE", "folder": "shared", "title": "..."}
    """
    commands = parsed.get("commands", [])
    if not commands:
        return [], [], [], []

    doc_cmds = []
    gitlab_cmds = []
    tickets_cmds = []
    channels_to_join = []

    for cmd in commands:
        cmd_type = cmd.get("type", "").lower()
        action = cmd.get("action", "")
        params = cmd.get("params", {})

        # Merge action + params into flat dict
        flat = {"action": action, **params}

        if cmd_type == "doc":
            doc_cmds.append(flat)
        elif cmd_type == "gitlab":
            gitlab_cmds.append(flat)
        elif cmd_type == "tickets":
            tickets_cmds.append(flat)
        elif cmd_type == "channel":
            if action.upper() == "JOIN":
                channel = params.get("channel", "")
                if channel:
                    channels_to_join.append(channel)

    return doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join


def extract_messages(parsed: dict, default_channel: str) -> dict[str, str]:
    """Convert the messages array into a {channel: text} mapping.

    Returns empty dict for 'pass' or 'ready' actions, or if no messages.
    """
    action = parsed.get("action", "")
    if action in ("pass", "ready"):
        return {}

    messages = parsed.get("messages", [])
    if not messages:
        return {}

    result: dict[str, str] = {}
    for msg in messages:
        channel = msg.get("channel", default_channel)
        text = msg.get("text", "").strip()
        if not text:
            continue
        if channel in result:
            result[channel] += "\n\n" + text
        else:
            result[channel] = text

    return result
