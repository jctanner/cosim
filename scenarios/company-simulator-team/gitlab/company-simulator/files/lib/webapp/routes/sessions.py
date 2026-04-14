"""Session management API routes."""

from flask import Blueprint, jsonify, request

from lib.session import (
    save_session, load_session, new_session, list_sessions,
    get_current_session, set_scenario, get_memberships_from_instance,
    delete_session, rename_session,
)
from lib.scenario_loader import list_scenarios
from lib.webapp.state import (
    _channel_members, _channel_lock,
    _orchestrator_commands, _command_lock,
)
from lib.webapp.helpers import _reinitialize, _restore_session_extras

bp = Blueprint("sessions", __name__)


@bp.route("/api/session/current", methods=["GET"])
def session_current():
    return jsonify(get_current_session())


@bp.route("/api/session/list", methods=["GET"])
def session_list():
    return jsonify(list_sessions())


@bp.route("/api/session/scenarios", methods=["GET"])
def session_scenarios():
    return jsonify(list_scenarios())


@bp.route("/api/session/save", methods=["POST"])
def session_save():
    data = request.get_json(force=True) if request.data else {}
    name = data.get("name")
    try:
        meta = save_session(name)
        return jsonify(meta), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/session/load", methods=["POST"])
def session_load():
    data = request.get_json(force=True)
    instance_name = data.get("instance")
    if not instance_name:
        return jsonify({"error": "instance required"}), 400
    try:
        meta = load_session(instance_name)
        # Load the scenario config so channels/personas/folders are populated
        scenario = meta.get("scenario")
        if scenario:
            from lib.scenario_loader import load_scenario as _load_scenario
            _load_scenario(scenario)
            set_scenario(scenario)
        _reinitialize()
        # Re-restore memos, events, emails, and recaps that were
        # cleared by _load_scenario / _reinitialize
        _restore_session_extras(instance_name)
        # Apply saved memberships on top of defaults
        memberships = get_memberships_from_instance(instance_name)
        if memberships:
            with _channel_lock:
                for ch, members in memberships.items():
                    if ch in _channel_members:
                        _channel_members[ch] = set(members)
        # Signal orchestrator to restart with this session's scenario
        with _command_lock:
            _orchestrator_commands.append({"action": "restart", "scenario": scenario})
        return jsonify(meta)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/session/new", methods=["POST"])
def session_new():
    data = request.get_json(force=True) if request.data else {}
    scenario = data.get("scenario")
    try:
        meta = new_session(scenario)
        _reinitialize()
        # Signal orchestrator to restart with the new scenario
        with _command_lock:
            _orchestrator_commands.append({"action": "restart", "scenario": scenario or get_current_session().get("scenario")})
        meta["restarting_agents"] = True
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/session/<instance>", methods=["DELETE"])
def session_delete(instance):
    try:
        delete_session(instance)
        return jsonify({"ok": True})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/session/<instance>", methods=["PUT"])
def session_rename(instance):
    data = request.get_json(force=True)
    new_name = data.get("name")
    if not new_name:
        return jsonify({"error": "name required"}), 400
    try:
        meta = rename_session(instance, new_name)
        return jsonify(meta)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
