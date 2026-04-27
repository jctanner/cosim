"""Blog API routes."""

from flask import Blueprint, jsonify, request

bp = Blueprint("blog", __name__)


@bp.route("/api/blog/posts", methods=["GET"])
def list_blog_posts():
    from lib.blog import get_posts

    include_replies = request.args.get("include_replies", "").lower() in ("1", "true")
    posts = get_posts(include_recent_replies=include_replies)
    filt = request.args.get("filter", "")
    if filt == "internal":
        posts = [p for p in posts if not p.get("is_external")]
    elif filt == "external":
        posts = [p for p in posts if p.get("is_external")]
    return jsonify(posts)


@bp.route("/api/blog/posts", methods=["POST"])
def create_blog_post_endpoint():
    from lib.blog import create_post

    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    body = data.get("body", "").strip()
    author = data.get("author", "System")
    is_external = data.get("is_external", False)
    tags = data.get("tags", [])
    entry = create_post(title, body, author, is_external=is_external, tags=tags)
    return jsonify(entry), 201


@bp.route("/api/blog/posts/<post_slug>", methods=["GET"])
def get_blog_post_detail(post_slug):
    from lib.blog import get_post, get_replies

    post = get_post(post_slug)
    if not post:
        return jsonify({"error": "not found"}), 404
    post["replies"] = get_replies(post_slug)
    return jsonify(post)


@bp.route("/api/blog/posts/<post_slug>", methods=["PUT"])
def update_blog_post_endpoint(post_slug):
    from lib.blog import update_post

    data = request.get_json(force=True)
    kwargs = {}
    for key in ("title", "body", "status", "is_external", "tags"):
        if key in data:
            kwargs[key] = data[key]
    if not kwargs:
        return jsonify({"error": "no fields to update"}), 400
    try:
        entry = update_post(post_slug, **kwargs)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(entry)


@bp.route("/api/blog/posts/<post_slug>/replies", methods=["GET"])
def list_blog_replies(post_slug):
    from lib.blog import get_replies

    return jsonify(get_replies(post_slug))


@bp.route("/api/blog/posts/<post_slug>/replies", methods=["POST"])
def reply_to_blog_post_endpoint(post_slug):
    from lib.blog import reply_to_post

    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    author = data.get("author", "System")
    try:
        entry = reply_to_post(post_slug, text, author)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify(entry), 201


@bp.route("/api/blog/posts/<post_slug>", methods=["DELETE"])
def delete_blog_post_endpoint(post_slug):
    from lib.blog import delete_post

    if delete_post(post_slug):
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404
