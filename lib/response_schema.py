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
    original = text.strip()
    if not original:
        return None

    text = original

    # Strategy 1: Strip markdown code fences (single block)
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

    # Strategy 4: Find ALL code-fenced blocks and try each one.
    # Handles agents that split a response into multiple fenced blocks.
    blocks = re.findall(r'```(?:json)?\s*\n(.*?)\n\s*```', original, re.DOTALL)
    if blocks:
        parsed_blocks = []
        for block in blocks:
            p = _try_parse_json(block.strip())
            if p:
                parsed_blocks.append(p)
        if len(parsed_blocks) == 1:
            return parsed_blocks[0]
        if len(parsed_blocks) > 1:
            return _merge_responses(parsed_blocks)

    return None


def _merge_responses(parsed_list: list[dict]) -> dict:
    """Merge multiple parsed JSON responses into one.

    Combines messages and commands arrays from all respond actions.
    """
    merged = {"action": "respond", "messages": [], "commands": []}
    for p in parsed_list:
        if p.get("action") == "pass":
            continue
        merged["messages"].extend(p.get("messages", []))
        merged["commands"].extend(p.get("commands", []))
    if not merged["messages"] and not merged["commands"]:
        return {"action": "pass"}
    return merged


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON with a valid action field."""
    if not text or not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Try to repair common LLM JSON errors (unescaped quotes in strings)
        repaired = _repair_json(text)
        if repaired is not None:
            try:
                parsed = json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                return None
        else:
            return None
    if not isinstance(parsed, dict):
        return None
    action = parsed.get("action")
    if action not in _VALID_ACTIONS:
        return None
    return parsed


def _repair_json(text: str) -> str | None:
    """Attempt to fix common JSON errors produced by LLMs.

    Handles unescaped double quotes inside string values by walking the
    string character-by-character and escaping quotes that appear inside
    already-open string literals.
    """
    # Quick check: does it look like a JSON object?
    if not text.strip().startswith("{"):
        return None

    result = []
    i = 0
    in_string = False
    n = len(text)

    while i < n:
        ch = text[i]

        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue

        # Inside a string
        if ch == '\\':
            # Escaped character — pass through both chars
            result.append(ch)
            if i + 1 < n:
                i += 1
                result.append(text[i])
            i += 1
            continue

        if ch == '"':
            # Is this the real end of the string, or an unescaped interior quote?
            # Look ahead: after the real closing quote we expect , : ] } or whitespace
            j = i + 1
            while j < n and text[j] in ' \t\r\n':
                j += 1
            if j >= n or text[j] in ',:]}\n':
                # Likely the real closing quote
                result.append(ch)
                in_string = False
            else:
                # Interior quote — escape it
                result.append('\\"')
            i += 1
            continue

        # Unescaped newlines inside strings are invalid JSON
        if ch == '\n':
            result.append('\\n')
            i += 1
            continue

        result.append(ch)
        i += 1

    repaired = ''.join(result)
    # Only return if we actually changed something
    return repaired if repaired != text else None


def normalize_commands(parsed: dict) -> tuple[list[dict], list[dict], list[dict], list[str], list[dict], list[dict]]:
    """Split the commands array by type into the flat dict format existing _execute_* functions expect.

    Returns (doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join, dm_cmds, task_cmds).

    Each command's 'action' + 'params' are merged into a flat dict, e.g.:
        {"type": "doc", "action": "CREATE", "params": {"folder": "shared", "title": "..."}}
    becomes:
        {"action": "CREATE", "folder": "shared", "title": "..."}
    """
    commands = parsed.get("commands", [])
    if not commands:
        return [], [], [], [], [], []

    doc_cmds = []
    gitlab_cmds = []
    tickets_cmds = []
    channels_to_join = []
    dm_cmds = []
    task_cmds = []

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
        elif cmd_type == "dm":
            # DMs use params directly: {to, text}
            dm_cmds.append({"to": params.get("to", ""), "text": params.get("text", "")})
        elif cmd_type == "task":
            task_cmds.append(flat)

    return doc_cmds, gitlab_cmds, tickets_cmds, channels_to_join, dm_cmds, task_cmds


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
