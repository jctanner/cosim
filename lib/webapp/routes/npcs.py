"""NPC (agent) management API routes."""

import re
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from lib.webapp.helpers import _broadcast, _persist_message
from lib.webapp.state import (
    _agent_firing,
    _agent_last_activity,
    _agent_online,
    _agent_online_lock,
    _agent_thoughts,
    _agent_thoughts_lock,
    _agent_verbosity,
    _channel_lock,
    _channel_members,
    _channels,
    _command_lock,
    _gitlab_lock,
    _gitlab_repos,
    _lock,
    _messages,
    _orchestrator_commands,
    _orchestrator_lock,
    _orchestrator_status,
)

bp = Blueprint("npcs", __name__)


@bp.route("/api/npcs", methods=["GET"])
def list_npcs():
    from lib.docs import get_accessible_folders
    from lib.gitlab import DEFAULT_REPO_ACCESS
    from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS

    all_repo_names = sorted(_gitlab_repos.keys())
    result = []
    with _orchestrator_lock:
        agent_states = _orchestrator_status.get("agents", {})
        last_hb = _orchestrator_status.get("last_heartbeat", 0)
    orch_connected = last_hb > 0 and (time.time() - last_hb < 30)
    with _agent_online_lock:
        for key, p in PERSONAS.items():
            channels = sorted(DEFAULT_MEMBERSHIPS.get(key, set()))
            folders = sorted(get_accessible_folders(key))
            # Repos: if no access control, all repos; otherwise filter
            if DEFAULT_REPO_ACCESS:
                repos = sorted(
                    r
                    for r in all_repo_names
                    if r not in DEFAULT_REPO_ACCESS or key in DEFAULT_REPO_ACCESS.get(r, set())
                )
            else:
                repos = all_repo_names
            toggled_online = _agent_online.get(key, True)
            # Determine live state from orchestrator heartbeat
            agent_info = agent_states.get(key, {})
            is_firing = key in _agent_firing
            if not orch_connected:
                if is_firing:
                    live_state = "firing"
                else:
                    # Check per-agent activity from MCP hooks
                    activity = _agent_last_activity.get(key, {})
                    last_active = activity.get("timestamp", 0)
                    if time.time() - last_active < 60:
                        live_state = "responding"
                    else:
                        live_state = "disconnected"
            elif is_firing:
                live_state = "firing"
            elif not toggled_online:
                live_state = "offline"
            else:
                live_state = agent_info.get("state", "unknown")
            result.append(
                {
                    "key": key,
                    "display_name": p["display_name"],
                    "team_description": p.get("team_description", ""),
                    "character_file": p.get("character_file", ""),
                    "avatar": p.get("avatar"),
                    "tier": PERSONA_TIER.get(key, 0),
                    "channels": channels,
                    "folders": folders,
                    "repos": repos,
                    "online": toggled_online,
                    "verbosity": _agent_verbosity.get(key, "normal"),
                    "live_state": live_state,
                }
            )
    return jsonify(result)


@bp.route("/api/npcs/<key>/toggle", methods=["POST"])
def toggle_npc(key):
    from lib.personas import PERSONAS

    if key not in PERSONAS:
        return jsonify({"error": f"unknown agent: {key}"}), 404
    display_name = PERSONAS[key]["display_name"]
    with _agent_online_lock:
        current = _agent_online.get(key, True)
        _agent_online[key] = not current
        new_state = _agent_online[key]
    # Post system message
    if new_state:
        msg = f"{display_name} is back online"
    else:
        msg = f"{display_name} is now out of office"
    with _lock:
        sys_msg = {
            "id": len(_messages) + 1,
            "sender": "System",
            "content": msg,
            "channel": "#system",
            "timestamp": time.time(),
        }
        _messages.append(sys_msg)
    _persist_message(sys_msg)
    _broadcast(sys_msg)
    return jsonify({"key": key, "online": new_state, "display_name": display_name})


@bp.route("/api/npcs/<key>/activity", methods=["POST"])
def npc_activity(key):
    """Record agent activity from MCP hook events (implicit heartbeat)."""
    data = request.get_json(force=True)
    with _agent_online_lock:
        _agent_last_activity[key] = {
            "timestamp": time.time(),
            "event_type": data.get("event_type", "unknown"),
            "detail": data.get("detail", ""),
        }
    return jsonify({"ok": True})


@bp.route("/api/npcs/<key>/fire", methods=["POST"])
def fire_npc(key):
    from lib.personas import PERSONAS

    if key not in PERSONAS:
        return jsonify({"error": f"unknown agent: {key}"}), 404

    display_name = PERSONAS[key]["display_name"]

    # Mark as firing — agent stays in PERSONAS but is skipped in responses
    with _agent_online_lock:
        _agent_online[key] = False  # skip in response waves
        _agent_firing.add(key)

    # Signal orchestrator to remove this agent's session
    # Orchestrator will call back to /api/npcs/<key>/finalize-fire after session closes
    with _command_lock:
        _orchestrator_commands.append({"action": "remove_agent", "key": key})
        print(f"[cmd queue] fire: remove_agent key={key} (queue size: {len(_orchestrator_commands)})")

    # Post system message
    with _lock:
        sys_msg = {
            "id": len(_messages) + 1,
            "sender": "System",
            "content": f"{display_name} has left the company.",
            "channel": "#system",
            "timestamp": time.time(),
        }
        _messages.append(sys_msg)
    _persist_message(sys_msg)
    _broadcast(sys_msg)

    return jsonify({"ok": True, "key": key, "display_name": display_name, "fired": True})


@bp.route("/api/npcs/<key>/finalize-fire", methods=["POST"])
def finalize_fire(key):
    """Called by orchestrator after closing the agent's session."""
    from lib.docs import DEFAULT_FOLDER_ACCESS
    from lib.gitlab import DEFAULT_REPO_ACCESS
    from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS, RESPONSE_TIERS

    if key not in PERSONAS:
        return jsonify({"ok": True})  # already removed

    display_name = PERSONAS[key]["display_name"]
    del PERSONAS[key]
    DEFAULT_MEMBERSHIPS.pop(key, None)
    with _channel_lock:
        for members in _channel_members.values():
            members.discard(key)
    old_tier = PERSONA_TIER.pop(key, None)
    if old_tier and old_tier in RESPONSE_TIERS:
        if key in RESPONSE_TIERS[old_tier]:
            RESPONSE_TIERS[old_tier].remove(key)
    for access_set in DEFAULT_FOLDER_ACCESS.values():
        access_set.discard(key)
    for access_set in DEFAULT_REPO_ACCESS.values():
        access_set.discard(key)
    with _agent_online_lock:
        _agent_online.pop(key, None)
    with _agent_online_lock:
        _agent_firing.discard(key)
    print(f"[fire] finalized: {display_name} removed from PERSONAS")
    return jsonify({"ok": True, "key": key, "finalized": True})


@bp.route("/api/npcs/hire", methods=["POST"])
def hire_npc():
    from lib.docs import DEFAULT_FOLDER_ACCESS, DEFAULT_FOLDERS
    from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS, RESPONSE_TIERS

    data = request.get_json(force=True)
    display_name = data.get("display_name", "").strip()
    key = data.get("key", "").strip().lower().replace(" ", "")
    team_description = data.get("team_description", "").strip()
    prompt_content = data.get("prompt", "").strip()
    tier = int(data.get("tier", 1))
    channels = data.get("channels", ["#general"])
    folders = data.get("folders", ["shared", "public"])

    if not display_name or not key:
        return jsonify({"error": "display_name and key required"}), 400
    if key in PERSONAS:
        return jsonify({"error": f"agent key '{key}' already exists"}), 409

    # Save character file to instance runtime directory (not the scenario template)
    from lib.session import VAR_DIR

    char_dir = VAR_DIR / "characters"
    char_dir.mkdir(parents=True, exist_ok=True)
    char_file = char_dir / f"{key}.md"
    char_file.write_text(prompt_content or f"# {display_name}\\n\\nYou are {display_name}.")

    # Add to PERSONAS
    PERSONAS[key] = {
        "name": key,
        "display_name": display_name,
        "team_description": team_description,
        "character_file": str(char_file),
    }

    # Add to memberships
    DEFAULT_MEMBERSHIPS[key] = set(channels)
    with _channel_lock:
        for ch in channels:
            if ch in _channel_members:
                _channel_members[ch].add(key)

    # Add to tier
    RESPONSE_TIERS.setdefault(tier, [])
    if key not in RESPONSE_TIERS[tier]:
        RESPONSE_TIERS[tier].append(key)
    PERSONA_TIER[key] = tier

    # Add folder access
    for folder_name in folders:
        DEFAULT_FOLDER_ACCESS.setdefault(folder_name, set()).add(key)

    # Create personal folder
    personal_name = display_name.split("(")[0].strip().lower().replace(" ", "")
    if personal_name not in DEFAULT_FOLDERS:
        DEFAULT_FOLDERS[personal_name] = {
            "type": "personal",
            "description": f"{display_name}'s private folder",
        }
        DEFAULT_FOLDER_ACCESS[personal_name] = {key}

    # Set online and verbosity
    verbosity = data.get("verbosity", "normal")
    with _agent_online_lock:
        _agent_online[key] = True
        if verbosity != "normal":
            _agent_verbosity[key] = verbosity

    # Create director channel
    with _channel_lock:
        ch_name = f"#director-{key}"
        _channels[ch_name] = {
            "description": f"Private channel with {display_name}",
            "is_external": False,
            "is_director": True,
            "director_persona": key,
            "created_at": time.time(),
        }
        _channel_members[ch_name] = {key}

    # Signal orchestrator to add this agent's session
    with _command_lock:
        _orchestrator_commands.append({"action": "add_agent", "key": key})
        print(f"[cmd queue] hire: add_agent key={key} (queue size: {len(_orchestrator_commands)})")

    # Post system message
    with _lock:
        sys_msg = {
            "id": len(_messages) + 1,
            "sender": "System",
            "content": f"Welcome {display_name} to the team!",
            "channel": "#system",
            "timestamp": time.time(),
        }
        _messages.append(sys_msg)
    _persist_message(sys_msg)
    _broadcast(sys_msg)

    return jsonify({"ok": True, "key": key, "display_name": display_name, "hired": True}), 201


@bp.route("/api/npcs/<key>/config", methods=["PUT"])
def update_npc_config(key):
    from lib.docs import DEFAULT_FOLDER_ACCESS
    from lib.gitlab import DEFAULT_REPO_ACCESS
    from lib.personas import DEFAULT_MEMBERSHIPS, PERSONA_TIER, PERSONAS, RESPONSE_TIERS

    if key not in PERSONAS:
        return jsonify({"error": f"unknown agent: {key}"}), 404

    data = request.get_json(force=True)
    display_name = PERSONAS[key]["display_name"]

    # Update channel memberships
    if "channels" in data:
        new_channels = set(data["channels"])
        DEFAULT_MEMBERSHIPS[key] = new_channels
        # Update live channel members
        with _channel_lock:
            for ch_name in _channels:
                members = _channel_members.get(ch_name, set())
                if ch_name in new_channels:
                    members.add(key)
                else:
                    members.discard(key)

    # Update folder access
    if "folders" in data:
        new_folders = set(data["folders"])
        for folder_name in list(DEFAULT_FOLDER_ACCESS.keys()):
            if folder_name in new_folders:
                DEFAULT_FOLDER_ACCESS[folder_name].add(key)
            else:
                DEFAULT_FOLDER_ACCESS[folder_name].discard(key)

    # Update tier
    if "tier" in data:
        new_tier = int(data["tier"])
        old_tier = PERSONA_TIER.get(key)
        if old_tier != new_tier:
            # Remove from old tier
            if old_tier in RESPONSE_TIERS:
                if key in RESPONSE_TIERS[old_tier]:
                    RESPONSE_TIERS[old_tier].remove(key)
            # Add to new tier
            RESPONSE_TIERS.setdefault(new_tier, [])
            if key not in RESPONSE_TIERS[new_tier]:
                RESPONSE_TIERS[new_tier].append(key)
            PERSONA_TIER[key] = new_tier

    # Update verbosity
    if "verbosity" in data:
        with _agent_online_lock:
            _agent_verbosity[key] = data["verbosity"]

    # Update repo access
    if "repos" in data:
        new_repos = set(data["repos"])
        with _gitlab_lock:
            for repo_name in _gitlab_repos:
                # If repo has no access control yet, initialize with all agents
                if repo_name not in DEFAULT_REPO_ACCESS:
                    DEFAULT_REPO_ACCESS[repo_name] = set(PERSONAS.keys())
                if repo_name in new_repos:
                    DEFAULT_REPO_ACCESS[repo_name].add(key)
                else:
                    DEFAULT_REPO_ACCESS[repo_name].discard(key)

    return jsonify({"ok": True, "key": key, "display_name": display_name})


@bp.route("/api/npcs/<key>/thoughts", methods=["GET"])
def get_agent_thoughts(key):
    with _agent_thoughts_lock:
        thoughts = list(_agent_thoughts.get(key, []))
    return jsonify(thoughts)


@bp.route("/api/npcs/<key>/thoughts", methods=["POST"])
def post_agent_thoughts(key):
    data = request.get_json(force=True)
    entry = {
        "thinking": data.get("thinking", ""),
        "response": data.get("response", ""),
        "timestamp": time.time(),
    }
    with _agent_thoughts_lock:
        _agent_thoughts.setdefault(key, []).append(entry)
    return jsonify({"ok": True})


@bp.route("/api/npcs/<key>/character-sheet", methods=["GET"])
def get_agent_character_sheet(key):
    """Return the parsed NRSP character sheet for this agent."""
    from lib.personas import PERSONAS
    from lib.scenario_loader import _parse_frontmatter

    if key not in PERSONAS:
        return jsonify({"error": "unknown agent"}), 404
    try:
        char_path = PERSONAS[key].get("character_file", "")
        if not char_path or not Path(char_path).exists():
            return jsonify({"error": "character file not found"}), 404
        text = Path(char_path).read_text()
        frontmatter = _parse_frontmatter(text)
        # Strip frontmatter from body
        body = re.sub(r"^---\n.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
        # Parse sections (## headers)
        sections = []
        current_title = None
        current_lines = []
        for line in body.split("\n"):
            if line.startswith("## ") and not line.startswith("### "):
                if current_title is not None:
                    sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
                current_title = line[3:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_title is not None:
            sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
        return jsonify({"key": key, "frontmatter": frontmatter, "sections": sections})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/npcs/<key>/prompt", methods=["GET"])
def get_agent_prompt(key):
    """Return the character file content for this agent, split into context and prompt."""
    from lib.personas import PERSONAS

    if key not in PERSONAS:
        return jsonify({"error": "unknown agent"}), 404
    try:
        char_path = PERSONAS[key].get("character_file", "")
        if not char_path or not Path(char_path).exists():
            return jsonify({"error": "character file not found"}), 404
        text = Path(char_path).read_text()
        text = re.sub(r"^---\n.*?---\n", "", text, count=1, flags=re.DOTALL).strip()
        # Split on ## Prompt
        prompt_match = re.search(r"^## Prompt\s*\n(.*?)(?=\n## (?!#)|\Z)", text, re.DOTALL | re.MULTILINE)
        if prompt_match:
            context = text[: prompt_match.start()].strip()
            prompt = prompt_match.group(1).strip()
        else:
            context = ""
            prompt = text
        return jsonify({"key": key, "context": context, "prompt": prompt})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
