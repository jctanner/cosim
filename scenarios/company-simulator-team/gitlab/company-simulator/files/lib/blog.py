"""Blog — internal + external company blog with authored posts and replies."""

import re
import threading
import time

_blog_posts: dict[str, dict] = {}  # slug -> post metadata
_blog_replies: list[dict] = []  # all replies across all posts
_blog_lock = threading.Lock()


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] if slug else "post"


def create_post(
    title: str,
    body: str,
    author: str,
    is_external: bool = False,
    tags: list[str] | None = None,
    status: str = "published",
) -> dict:
    """Create a new blog post. Status: draft, published, unpublished."""
    ts = time.time()
    slug = f"{_slugify(title)}-{int(ts)}"

    post = {
        "slug": slug,
        "title": title,
        "body": body,
        "author": author,
        "is_external": is_external,
        "tags": tags or [],
        "status": status,
        "created_at": ts,
        "updated_at": ts,
        "reply_count": 0,
        "last_reply_at": ts,
        "last_reply_text": "",
        "last_reply_author": "",
    }
    with _blog_lock:
        _blog_posts[slug] = post
    return dict(post)


def update_post(post_slug: str, **kwargs) -> dict:
    """Update a blog post's fields. Supported: title, body, status, is_external, tags.

    Returns the updated post dict.
    """
    with _blog_lock:
        post = _blog_posts.get(post_slug)
        if not post:
            raise ValueError(f"Blog post not found: {post_slug}")
        for key in ("title", "body", "status", "is_external", "tags"):
            if key in kwargs:
                post[key] = kwargs[key]
        post["updated_at"] = time.time()
    return dict(post)


def reply_to_post(post_slug: str, text: str, author: str) -> dict:
    """Post a reply to a blog post. Returns the reply entry."""
    ts = time.time()
    with _blog_lock:
        post = _blog_posts.get(post_slug)
        if not post:
            raise ValueError(f"Blog post not found: {post_slug}")

        reply_id = len(_blog_replies) + 1
        reply = {
            "id": reply_id,
            "post_slug": post_slug,
            "author": author,
            "text": text,
            "timestamp": ts,
        }
        _blog_replies.append(reply)

        # Update post metadata
        post["reply_count"] += 1
        post["last_reply_at"] = ts
        post["last_reply_text"] = text[:100]
        post["last_reply_author"] = author

    return dict(reply)


def get_posts(include_recent_replies: bool = False) -> list[dict]:
    """Return all posts sorted by created_at descending.

    If include_recent_replies=True, each post includes a 'recent_replies'
    list with the last reply (for turn prompt summaries).
    """
    with _blog_lock:
        posts = [dict(p) for p in _blog_posts.values()]
        if include_recent_replies:
            for p in posts:
                slug = p["slug"]
                post_replies = [dict(r) for r in _blog_replies if r["post_slug"] == slug]
                p["recent_replies"] = post_replies[-1:] if post_replies else []
    posts.sort(key=lambda p: p["created_at"], reverse=True)
    return posts


def get_post(post_slug: str) -> dict | None:
    """Return a single post's metadata, or None."""
    with _blog_lock:
        p = _blog_posts.get(post_slug)
        return dict(p) if p else None


def get_replies(post_slug: str) -> list[dict]:
    """Return all replies to a post, oldest first."""
    with _blog_lock:
        return [dict(r) for r in _blog_replies if r["post_slug"] == post_slug]


def delete_post(post_slug: str) -> bool:
    """Delete a post and all its replies. Returns True if found."""
    with _blog_lock:
        if post_slug not in _blog_posts:
            return False
        del _blog_posts[post_slug]
        _blog_replies[:] = [r for r in _blog_replies if r["post_slug"] != post_slug]
        return True


def clear_blog():
    """Clear all posts and replies."""
    with _blog_lock:
        _blog_posts.clear()
        _blog_replies.clear()


def get_blog_snapshot() -> dict:
    """Return a copy for session save."""
    with _blog_lock:
        return {
            "posts": {k: dict(v) for k, v in _blog_posts.items()},
            "replies": [dict(r) for r in _blog_replies],
        }


def restore_blog(posts: dict, replies: list):
    """Restore from session data."""
    with _blog_lock:
        _blog_posts.clear()
        _blog_posts.update(posts)
        _blog_replies.clear()
        _blog_replies.extend(replies)
