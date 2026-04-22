"""Memos API routes."""

from flask import Blueprint, jsonify, request

bp = Blueprint("memos", __name__)


@bp.route("/api/memos/threads", methods=["GET"])
def list_memo_threads():
    from lib.memos import get_threads
    include_posts = request.args.get("include_posts", "").lower() in ("1", "true")
    return jsonify(get_threads(include_recent_posts=include_posts))


@bp.route("/api/memos/threads", methods=["POST"])
def create_memo_thread_endpoint():
    from lib.memos import create_thread
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    creator = data.get("creator", "System")
    description = data.get("description", "").strip()
    entry = create_thread(title, creator, description)
    return jsonify(entry), 201


@bp.route("/api/memos/threads/<thread_id>", methods=["GET"])
def get_memo_thread_detail(thread_id):
    from lib.memos import get_thread, get_posts
    thread = get_thread(thread_id)
    if not thread:
        return jsonify({"error": "not found"}), 404
    thread["posts"] = get_posts(thread_id)
    return jsonify(thread)


@bp.route("/api/memos/threads/<thread_id>/posts", methods=["GET"])
def list_memo_posts(thread_id):
    from lib.memos import get_posts
    return jsonify(get_posts(thread_id))


@bp.route("/api/memos/threads/<thread_id>/posts", methods=["POST"])
def post_memo_endpoint(thread_id):
    from lib.memos import post_memo
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    author = data.get("author", "System")
    try:
        entry = post_memo(thread_id, text, author)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(entry), 201


@bp.route("/api/memos/threads/<thread_id>", methods=["DELETE"])
def delete_memo_thread_endpoint(thread_id):
    from lib.memos import delete_thread
    if delete_thread(thread_id):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404
