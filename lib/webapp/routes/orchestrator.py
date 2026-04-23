"""Orchestrator status, heartbeat, command, and typing routes."""

import time

from flask import Blueprint, jsonify, request

from lib.webapp.helpers import _broadcast
from lib.webapp.state import (
    _command_lock,
    _orchestrator_commands,
    _orchestrator_lock,
    _orchestrator_status,
)

bp = Blueprint("orchestrator", __name__)


@bp.route("/api/status", methods=["GET"])
def get_status():
    from lib.session import get_current_session
    from lib.webapp.state import (
        _channels,
        _docs_index,
        _gitlab_repos,
        _messages,
        _tickets,
    )

    with _orchestrator_lock:
        orch = dict(_orchestrator_status)
        # Mark as disconnected if no heartbeat in 30 seconds
        if orch["last_heartbeat"] == 0 or time.time() - orch["last_heartbeat"] > 30:
            orch["state"] = "disconnected"
            orch["message"] = ""
    return jsonify(
        {
            "server": "running",
            "scenario": get_current_session().get("scenario"),
            "messages": len(_messages),
            "documents": len(_docs_index),
            "repos": len(_gitlab_repos),
            "tickets": len(_tickets),
            "channels": len(_channels),
            "orchestrator": orch,
        }
    )


@bp.route("/api/orchestrator/heartbeat", methods=["POST"])
def orchestrator_heartbeat():
    data = request.get_json(force=True)
    with _orchestrator_lock:
        _orchestrator_status["state"] = data.get("state", "ready")
        _orchestrator_status["scenario"] = data.get("scenario")
        _orchestrator_status["agents"] = data.get("agents", {})
        _orchestrator_status["last_heartbeat"] = time.time()
        _orchestrator_status["message"] = data.get("message", "")
    # Return any pending command (only if caller wants to check)
    if data.get("check_commands", True):
        with _command_lock:
            if _orchestrator_commands:
                cmd = _orchestrator_commands.pop(0)
                print(
                    f"[cmd queue] consumed: {cmd.get('action')} key={cmd.get('key', '')} (remaining: {len(_orchestrator_commands)})"
                )
            else:
                cmd = {"action": None}
            return jsonify(cmd)
    return jsonify({"action": None})


@bp.route("/api/orchestrator/command", methods=["POST"])
def orchestrator_command():
    data = request.get_json(force=True)
    action = data.get("action")
    if action not in ("restart", "shutdown", "add_agent", "remove_agent", None):
        return jsonify({"error": "invalid action"}), 400
    with _command_lock:
        _orchestrator_commands.append(data)
        print(f"[cmd queue] added: {action} (queue size: {len(_orchestrator_commands)})")
    return jsonify({"queued": action})


@bp.route("/api/typing", methods=["POST"])
def typing_indicator():
    data = request.get_json(force=True)
    event = {
        "type": "typing",
        "sender": data.get("sender", ""),
        "channel": data.get("channel", "#general"),
        "active": data.get("active", True),
    }
    _broadcast(event)
    return jsonify({"ok": True})
