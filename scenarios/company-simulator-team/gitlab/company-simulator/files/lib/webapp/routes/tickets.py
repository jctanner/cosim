"""Tickets API routes."""

import time

from flask import Blueprint, jsonify, request

from lib.tickets import generate_ticket_id, save_tickets_index
from lib.webapp.helpers import _broadcast_tickets_event
from lib.webapp.state import _tickets, _tickets_lock

bp = Blueprint("tickets", __name__)


@bp.route("/api/tickets", methods=["GET"])
def list_tickets():
    status_filter = request.args.get("status")
    assignee_filter = request.args.get("assignee")
    with _tickets_lock:
        tickets = list(_tickets.values())
    if status_filter:
        tickets = [t for t in tickets if t.get("status") == status_filter]
    if assignee_filter:
        tickets = [t for t in tickets if t.get("assignee") == assignee_filter]
    return jsonify(tickets)


@bp.route("/api/tickets", methods=["POST"])
def create_ticket():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    description = data.get("description", "")
    priority = data.get("priority", "medium").strip()
    assignee = data.get("assignee", "").strip()
    author = data.get("author", "unknown").strip()
    blocked_by = data.get("blocked_by", [])
    if not title:
        return jsonify({"error": "title required"}), 400
    if priority not in ("low", "medium", "high", "critical"):
        return jsonify({"error": "priority must be low/medium/high/critical"}), 400

    now = time.time()
    ticket_id = generate_ticket_id(title, now)

    with _tickets_lock:
        if ticket_id in _tickets:
            # Unlikely collision — append a char
            ticket_id = ticket_id + "X"

        ticket = {
            "id": ticket_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "assignee": assignee,
            "created_by": author,
            "created_at": now,
            "updated_at": now,
            "comments": [],
            "blocked_by": [],
            "blocks": [],
        }

        # Set up dependencies
        if blocked_by:
            if isinstance(blocked_by, str):
                blocked_by = [b.strip() for b in blocked_by.split(",") if b.strip()]
            for dep_id in blocked_by:
                if dep_id in _tickets:
                    ticket["blocked_by"].append(dep_id)
                    if ticket_id not in _tickets[dep_id].get("blocks", []):
                        _tickets[dep_id].setdefault("blocks", []).append(ticket_id)

        _tickets[ticket_id] = ticket
        save_tickets_index(dict(_tickets))

    _broadcast_tickets_event("created", ticket)
    return jsonify(ticket), 201


@bp.route("/api/tickets/<ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    with _tickets_lock:
        ticket = _tickets.get(ticket_id)
    if ticket is None:
        return jsonify({"error": "ticket not found"}), 404
    return jsonify(ticket)


@bp.route("/api/tickets/<ticket_id>", methods=["PUT"])
def update_ticket(ticket_id):
    data = request.get_json(force=True)
    with _tickets_lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            return jsonify({"error": "ticket not found"}), 404

        if "status" in data:
            status = data["status"].strip()
            if status not in ("open", "in_progress", "resolved", "closed"):
                return jsonify({"error": "invalid status"}), 400
            ticket["status"] = status
        if "assignee" in data:
            ticket["assignee"] = data["assignee"].strip()
        if "priority" in data:
            priority = data["priority"].strip()
            if priority in ("low", "medium", "high", "critical"):
                ticket["priority"] = priority

        ticket["updated_at"] = time.time()
        save_tickets_index(dict(_tickets))

    _broadcast_tickets_event("updated", ticket)
    return jsonify(ticket)


@bp.route("/api/tickets/<ticket_id>/comment", methods=["POST"])
def comment_ticket(ticket_id):
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    author = data.get("author", "unknown").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    with _tickets_lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            return jsonify({"error": "ticket not found"}), 404

        comment = {
            "author": author,
            "text": text,
            "timestamp": time.time(),
        }
        ticket.setdefault("comments", []).append(comment)
        ticket["updated_at"] = time.time()
        save_tickets_index(dict(_tickets))

    _broadcast_tickets_event("commented", {"ticket_id": ticket_id, "comment": comment})
    return jsonify(ticket), 201


@bp.route("/api/tickets/<ticket_id>/depends", methods=["POST"])
def ticket_depends(ticket_id):
    data = request.get_json(force=True)
    blocked_by = data.get("blocked_by", "").strip()
    if not blocked_by:
        return jsonify({"error": "blocked_by required"}), 400

    with _tickets_lock:
        ticket = _tickets.get(ticket_id)
        if ticket is None:
            return jsonify({"error": "ticket not found"}), 404

        dep_ids = [b.strip() for b in blocked_by.split(",") if b.strip()]
        for dep_id in dep_ids:
            if dep_id not in _tickets:
                continue
            if dep_id not in ticket.get("blocked_by", []):
                ticket.setdefault("blocked_by", []).append(dep_id)
            if ticket_id not in _tickets[dep_id].get("blocks", []):
                _tickets[dep_id].setdefault("blocks", []).append(ticket_id)

        ticket["updated_at"] = time.time()
        save_tickets_index(dict(_tickets))

    _broadcast_tickets_event("depends_updated", ticket)
    return jsonify(ticket)
