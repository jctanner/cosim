"""Miscellaneous API routes — roles, personas, templates, avatars, usage."""

from flask import Blueprint, jsonify, send_from_directory

from lib.session import get_current_session
from lib.webapp.helpers import _parse_usage_from_logs

bp = Blueprint("misc", __name__)

DEFAULT_HUMAN_ROLES = [
    "Scenario Director",
    "Consultant",
    "Customer",
    "New Hire",
    "Board Member",
    "Intern",
    "Vendor",
    "Investor",
    "Auditor",
    "Competitor",
    "Regulator",
    "The Press",
    "Hacker",
    "God",
]

DEFAULT_JOB_TITLES = [
    "PM",
    "Eng Manager",
    "Architect",
    "Senior Eng",
    "Junior Eng",
    "Support Eng",
    "Sales Eng",
    "QA Lead",
    "DevOps",
    "Designer",
    "Marketing",
    "Security Specialist",
    "CEO",
    "CFO",
    "CTO",
    "COO",
    "Project Mgr",
    "Intern",
    "Contractor",
]


@bp.route("/api/roles", methods=["GET"])
def get_roles():
    from lib.scenario_loader import SCENARIO_SETTINGS

    human_roles = SCENARIO_SETTINGS.get("human_roles", DEFAULT_HUMAN_ROLES)
    job_titles = SCENARIO_SETTINGS.get("job_titles", DEFAULT_JOB_TITLES)
    return jsonify({"human_roles": human_roles, "job_titles": job_titles})


@bp.route("/api/templates", methods=["GET"])
def list_templates():
    from lib.scenario_loader import SCENARIOS_DIR

    templates_dir = SCENARIOS_DIR / "character-templates"
    result = []
    if templates_dir.exists():
        for f in sorted(templates_dir.glob("*.CS.md")):
            key_name = f.name.replace(".CS.md", "")
            name = key_name.replace("-", " ").title()
            result.append({"key": key_name, "name": name})
        if not result:
            # Fallback to old .md format
            for f in sorted(templates_dir.glob("*.md")):
                if f.name.endswith(".CS.md"):
                    continue
                name = f.stem.replace("-", " ").title()
                result.append({"key": f.stem, "name": name})
    return jsonify(result)


@bp.route("/api/templates/<key>", methods=["GET"])
def get_template(key):
    from lib.scenario_loader import SCENARIOS_DIR

    path = SCENARIOS_DIR / "character-templates" / f"{key}.CS.md"
    if not path.exists():
        path = SCENARIOS_DIR / "character-templates" / f"{key}.md"
    if not path.exists():
        return jsonify({"error": "template not found"}), 404
    content = path.read_text()
    return jsonify({"key": key, "content": content})


@bp.route("/api/personas", methods=["GET"])
def get_personas():
    from lib.personas import PERSONAS

    result = {}
    for key, p in PERSONAS.items():
        result[key] = {
            "key": key,
            "display_name": p["display_name"],
            "team_description": p.get("team_description", ""),
            "avatar": p.get("avatar"),
        }
    return jsonify(result)


@bp.route("/avatars/<path:filename>")
def serve_avatar(filename):
    """Serve avatar images from the current scenario's avatars/ directory."""
    from lib.scenario_loader import SCENARIOS_DIR

    scenario = get_current_session().get("scenario")
    if not scenario:
        return "No scenario loaded", 404
    avatars_dir = SCENARIOS_DIR / scenario / "avatars"
    if not avatars_dir.is_dir():
        return "Not found", 404
    return send_from_directory(str(avatars_dir), filename)


@bp.route("/api/usage", methods=["GET"])
def get_usage():
    return jsonify(_parse_usage_from_logs())
