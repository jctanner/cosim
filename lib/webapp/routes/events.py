"""Events API routes."""

import time

from flask import Blueprint, jsonify, request

from lib.tickets import generate_ticket_id
from lib.webapp.helpers import (
    _broadcast,
    _broadcast_doc_event,
    _broadcast_tickets_event,
    _persist_message,
    _save_index,
)
from lib.webapp.state import (
    DOCS_DIR,
    _docs_index,
    _docs_lock,
    _lock,
    _messages,
    _tickets,
    _tickets_lock,
)

bp = Blueprint("events", __name__)


@bp.route("/api/events/pool", methods=["GET"])
def get_event_pool():
    from lib.events import get_event_pool as _get_pool

    return jsonify(_get_pool())


@bp.route("/api/events/pool", methods=["POST"])
def add_event_to_pool():
    from lib.events import add_event

    data = request.get_json(force=True)
    idx = add_event(data)
    return jsonify({"ok": True, "index": idx}), 201


@bp.route("/api/events/pool/<int:index>", methods=["PUT"])
def update_event_in_pool(index):
    from lib.events import update_event

    data = request.get_json(force=True)
    update_event(index, data)
    return jsonify({"ok": True})


@bp.route("/api/events/pool/<int:index>", methods=["DELETE"])
def delete_event_from_pool(index):
    from lib.events import delete_event

    delete_event(index)
    return jsonify({"ok": True})


@bp.route("/api/events/trigger", methods=["POST"])
def trigger_event():
    from lib.events import fire_event

    data = request.get_json(force=True)
    results = []
    # Execute each action
    for action in data.get("actions", []):
        action_type = action.get("type", "")
        if action_type == "message":
            sender = action.get("sender", "System")
            content = action.get("content", "")
            channel = action.get("channel", "#general")
            with _lock:
                msg = {
                    "id": len(_messages) + 1,
                    "sender": sender,
                    "content": content,
                    "channel": channel,
                    "timestamp": time.time(),
                    "is_event": True,
                }
                _messages.append(msg)
            _persist_message(msg)
            _broadcast(msg)
            results.append({"type": "message", "channel": channel, "sender": sender})
        elif action_type == "ticket":
            title = action.get("title", "")
            if title:
                author = action.get("author", "System")
                ticket_id = generate_ticket_id(title, time.time())
                now = time.time()
                ticket = {
                    "id": ticket_id,
                    "title": title,
                    "description": action.get("description", ""),
                    "status": "open",
                    "priority": action.get("priority", "medium"),
                    "assignee": action.get("assignee", ""),
                    "created_by": author,
                    "created_at": now,
                    "updated_at": now,
                    "comments": [],
                    "blocked_by": [],
                    "blocks": [],
                }
                with _tickets_lock:
                    _tickets[ticket_id] = ticket
                    from lib.tickets import save_tickets_index

                    save_tickets_index(dict(_tickets))
                _broadcast_tickets_event("created", ticket)
                results.append({"type": "ticket", "id": ticket_id, "title": title})
        elif action_type == "document":
            title = action.get("title", "")
            if title:
                from lib.docs import slugify

                author = action.get("author", "System")
                folder = action.get("folder", "shared")
                content = action.get("content", "")
                slug = slugify(title)
                folder_dir = DOCS_DIR / folder
                folder_dir.mkdir(parents=True, exist_ok=True)
                doc_path = folder_dir / f"{slug}.txt"
                with _docs_lock:
                    if slug not in _docs_index:
                        doc_path.write_text(content, encoding="utf-8")
                        now = time.time()
                        meta = {
                            "slug": slug,
                            "title": title,
                            "folder": folder,
                            "created_at": now,
                            "updated_at": now,
                            "created_by": author,
                            "size": len(content.encode("utf-8")),
                            "preview": content[:100],
                        }
                        _docs_index[slug] = meta
                        _save_index()
                        _broadcast_doc_event("created", meta)
                        results.append({"type": "document", "title": title, "folder": folder, "slug": slug})
        elif action_type == "email":
            from lib.email import send_email

            sender = action.get("sender", action.get("from", "System"))
            subject = action.get("subject", "")
            body = action.get("body", action.get("content", ""))
            if subject:
                entry = send_email(sender, subject, body)
                results.append({"type": "email", "id": entry["id"], "subject": subject})
                # Also post to #announcements so agents see it in chat
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
        elif action_type == "memo":
            from lib.memos import create_thread, post_memo

            title = action.get("title", action.get("thread_title", ""))
            creator = action.get("sender", action.get("creator", "System"))
            description = action.get("description", "")
            text = action.get("text", action.get("content", ""))
            thread_id = action.get("thread_id", "")
            if thread_id and text:
                # Post to existing thread
                try:
                    post = post_memo(thread_id, text, creator)
                    results.append({"type": "memo", "action": "posted", "thread_id": thread_id, "post_id": post["id"]})
                except ValueError:
                    results.append({"type": "memo", "action": "post_failed", "error": "thread not found"})
            elif title:
                # Create new thread, optionally with an initial post
                thread = create_thread(title, creator, description)
                if text:
                    post_memo(thread["id"], text, creator)
                results.append({"type": "memo", "action": "created", "thread_id": thread["id"], "title": title})
        elif action_type == "blog":
            from lib.blog import create_post as create_blog
            from lib.blog import reply_to_post as reply_blog

            title = action.get("title", "")
            body = action.get("body", action.get("content", ""))
            creator = action.get("sender", action.get("author", "System"))
            is_external = action.get("is_external", False)
            tags = action.get("tags", [])
            post_slug = action.get("post_slug", "")
            text = action.get("text", "")
            if post_slug and text:
                # Reply to existing post
                try:
                    reply = reply_blog(post_slug, text, creator)
                    results.append(
                        {"type": "blog", "action": "replied", "post_slug": post_slug, "reply_id": reply["id"]}
                    )
                except ValueError:
                    results.append({"type": "blog", "action": "reply_failed", "error": "post not found"})
            elif title:
                # Create new blog post
                post = create_blog(title, body, creator, is_external=is_external, tags=tags)
                results.append({"type": "blog", "action": "created", "slug": post["slug"], "title": title})
        elif action_type == "merge_request":
            from lib.gitlab import next_mr_id, save_merge_requests
            from lib.webapp.state import _gitlab_lock, _gitlab_merge_requests, _gitlab_repos

            project = action.get("project", "")
            title = action.get("title", "")
            diff = action.get("diff", "")
            sender = action.get("sender", action.get("author", "System"))
            description = action.get("description", "")
            reviewers = action.get("reviewers", [])
            if project and title and diff:
                with _gitlab_lock:
                    if project in _gitlab_repos:
                        mrs = _gitlab_merge_requests.setdefault(project, [])
                        mr_id = next_mr_id(mrs)
                        now = time.time()
                        additions = sum(1 for ln in diff.split("\n") if ln.startswith("+") and not ln.startswith("+++"))
                        deletions = sum(1 for ln in diff.split("\n") if ln.startswith("-") and not ln.startswith("---"))
                        mr = {
                            "id": mr_id,
                            "title": title,
                            "description": description,
                            "author": sender,
                            "project": project,
                            "diff": diff,
                            "additions": additions,
                            "deletions": deletions,
                            "status": "open",
                            "reviewers": reviewers,
                            "approvals": [],
                            "comments": [],
                            "created_at": now,
                            "updated_at": now,
                        }
                        mrs.append(mr)
                        save_merge_requests(project, mrs)
                        results.append({"type": "merge_request", "action": "created", "id": mr_id, "title": title})
    # Log the event with results
    data["results"] = results
    entry = fire_event(data)
    return jsonify(entry)


@bp.route("/api/events/log", methods=["GET"])
def get_events_log():
    from lib.events import get_event_log

    return jsonify(get_event_log())
