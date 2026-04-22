"""Email/Announcements API routes."""

import time

from flask import Blueprint, jsonify, request

from lib.webapp.state import _messages, _lock
from lib.webapp.helpers import _persist_message, _broadcast

bp = Blueprint("emails", __name__)


@bp.route("/api/emails", methods=["GET"])
def list_emails():
    from lib.email import get_inbox
    return jsonify(get_inbox())


@bp.route("/api/emails", methods=["POST"])
def create_email():
    from lib.email import send_email
    data = request.get_json(force=True)
    sender = data.get("sender", "System")
    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()
    if not subject:
        return jsonify({"error": "subject required"}), 400
    entry = send_email(sender, subject, body)
    # Also post to #announcements
    with _lock:
        msg = {
            "id": len(_messages) + 1,
            "sender": sender,
            "content": f"**[EMAIL] {subject}**\n\n{body}",
            "channel": "#announcements",
            "timestamp": time.time(),
        }
        _messages.append(msg)
    _persist_message(msg)
    _broadcast(msg)
    return jsonify(entry), 201


@bp.route("/api/emails/<int:email_id>", methods=["GET"])
def get_email_detail(email_id):
    from lib.email import get_email
    entry = get_email(email_id)
    if not entry:
        return jsonify({"error": "not found"}), 404
    return jsonify(entry)
