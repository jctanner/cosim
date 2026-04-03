"""Memo-list — threaded async discussion board (Google Groups / mailing list style)."""

import re
import time
import threading


_memo_threads: dict[str, dict] = {}   # thread_id -> thread metadata
_memo_posts: list[dict] = []          # all posts across all threads
_memos_lock = threading.Lock()


def _slugify(text: str) -> str:
    """Convert text to a URL-friendly slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] if slug else "thread"


def create_thread(title: str, creator: str, description: str = "") -> dict:
    """Create a new discussion thread. Returns the thread entry."""
    ts = time.time()
    slug = _slugify(title)
    thread_id = f"{slug}-{int(ts)}"

    thread = {
        "id": thread_id,
        "title": title,
        "description": description,
        "creator": creator,
        "created_at": ts,
        "post_count": 0,
        "last_post_at": ts,
        "last_post_text": "",
        "last_post_author": "",
    }
    with _memos_lock:
        _memo_threads[thread_id] = thread
    return dict(thread)


def post_memo(thread_id: str, text: str, author: str) -> dict:
    """Post a reply to a thread. Returns the post entry."""
    ts = time.time()
    with _memos_lock:
        thread = _memo_threads.get(thread_id)
        if not thread:
            raise ValueError(f"Thread not found: {thread_id}")

        post_id = len(_memo_posts) + 1
        post = {
            "id": post_id,
            "thread_id": thread_id,
            "author": author,
            "text": text,
            "timestamp": ts,
        }
        _memo_posts.append(post)

        # Update thread metadata
        thread["post_count"] += 1
        thread["last_post_at"] = ts
        thread["last_post_text"] = text[:100]
        thread["last_post_author"] = author

    return dict(post)


def get_threads(include_recent_posts: bool = False) -> list[dict]:
    """Return all threads sorted by last_post_at descending.

    If include_recent_posts=True, each thread dict includes a 'recent_posts'
    list with the last 2 posts (for turn prompt summaries).
    """
    with _memos_lock:
        threads = [dict(t) for t in _memo_threads.values()]
        if include_recent_posts:
            for t in threads:
                tid = t["id"]
                thread_posts = [dict(p) for p in _memo_posts if p["thread_id"] == tid]
                t["recent_posts"] = thread_posts[-2:] if thread_posts else []
    threads.sort(key=lambda t: t["last_post_at"], reverse=True)
    return threads


def get_thread(thread_id: str) -> dict | None:
    """Return a single thread's metadata, or None."""
    with _memos_lock:
        t = _memo_threads.get(thread_id)
        return dict(t) if t else None


def get_posts(thread_id: str) -> list[dict]:
    """Return all posts in a thread, oldest first."""
    with _memos_lock:
        return [dict(p) for p in _memo_posts if p["thread_id"] == thread_id]


def delete_thread(thread_id: str) -> bool:
    """Delete a thread and all its posts. Returns True if found."""
    with _memos_lock:
        if thread_id not in _memo_threads:
            return False
        del _memo_threads[thread_id]
        _memo_posts[:] = [p for p in _memo_posts if p["thread_id"] != thread_id]
        return True


def clear_memos():
    """Clear all threads and posts."""
    with _memos_lock:
        _memo_threads.clear()
        _memo_posts.clear()


def get_memo_snapshot() -> dict:
    """Return a copy for session save."""
    with _memos_lock:
        return {
            "threads": {k: dict(v) for k, v in _memo_threads.items()},
            "posts": [dict(p) for p in _memo_posts],
        }


def restore_memos(threads: dict, posts: list):
    """Restore from session data."""
    with _memos_lock:
        _memo_threads.clear()
        _memo_threads.update(threads)
        _memo_posts.clear()
        _memo_posts.extend(posts)
