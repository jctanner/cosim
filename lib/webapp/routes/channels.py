"""Channel API routes."""

from flask import Blueprint, jsonify, request

from lib.webapp.state import _channels, _channel_members, _channel_lock
from lib.webapp.helpers import _broadcast_channel_update

bp = Blueprint("channels", __name__)


@bp.route("/api/channels", methods=["GET"])
def list_channels():
    with _channel_lock:
        result = []
        for name, info in sorted(_channels.items()):
            members = sorted(_channel_members.get(name, set()))
            entry = {
                "name": name,
                "description": info["description"],
                "is_external": info["is_external"],
                "members": members,
            }
            if info.get("is_system"):
                entry["is_system"] = True
            if info.get("is_director"):
                entry["is_director"] = True
                entry["director_persona"] = info.get("director_persona", "")
            result.append(entry)
    return jsonify(result)


@bp.route("/api/channels/<path:name>/join", methods=["POST"])
def join_channel(name):
    # Handle URL-encoded '#'
    if not name.startswith("#"):
        name = "#" + name
    data = request.get_json(force=True)
    persona = data.get("persona", "").strip()
    if not persona:
        return jsonify({"error": "persona required"}), 400

    with _channel_lock:
        if name not in _channels:
            return jsonify({"error": f"channel '{name}' not found"}), 404
        _channel_members.setdefault(name, set()).add(persona)
        members = sorted(_channel_members[name])

    _broadcast_channel_update(name, members)
    print(f"Channel join: {persona} -> {name}")
    return jsonify({"channel": name, "members": members})


@bp.route("/api/channels/<path:name>/leave", methods=["POST"])
def leave_channel(name):
    if not name.startswith("#"):
        name = "#" + name
    data = request.get_json(force=True)
    persona = data.get("persona", "").strip()
    if not persona:
        return jsonify({"error": "persona required"}), 400

    with _channel_lock:
        if name not in _channels:
            return jsonify({"error": f"channel '{name}' not found"}), 404
        _channel_members.get(name, set()).discard(persona)
        members = sorted(_channel_members.get(name, set()))

    _broadcast_channel_update(name, members)
    print(f"Channel leave: {persona} <- {name}")
    return jsonify({"channel": name, "members": members})
