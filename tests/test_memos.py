import pytest

from lib import memos


class TestCreateThread:
    def test_returns_thread(self):
        t = memos.create_thread("RFC: New API", "alice")
        assert "rfc-new-api" in t["id"]
        assert t["title"] == "RFC: New API"
        assert t["creator"] == "alice"
        assert t["post_count"] == 0

    def test_with_description(self):
        t = memos.create_thread("RFC", "alice", description="Details here")
        assert t["description"] == "Details here"


class TestPostMemo:
    def test_post_to_thread(self):
        t = memos.create_thread("RFC", "alice")
        p = memos.post_memo(t["id"], "I agree", "bob")
        assert p["thread_id"] == t["id"]
        assert p["author"] == "bob"
        assert p["text"] == "I agree"

    def test_updates_thread_metadata(self):
        t = memos.create_thread("RFC", "alice")
        memos.post_memo(t["id"], "Reply one", "bob")
        updated = memos.get_thread(t["id"])
        assert updated["post_count"] == 1
        assert updated["last_post_author"] == "bob"
        assert updated["last_post_text"] == "Reply one"

    def test_nonexistent_thread(self):
        with pytest.raises(ValueError, match="Thread not found"):
            memos.post_memo("fake-id", "text", "alice")

    def test_truncates_last_post_text(self):
        t = memos.create_thread("RFC", "alice")
        long_text = "x" * 200
        memos.post_memo(t["id"], long_text, "bob")
        updated = memos.get_thread(t["id"])
        assert len(updated["last_post_text"]) == 100


class TestGetThreads:
    def test_empty(self):
        assert memos.get_threads() == []

    def test_sorted_by_last_post(self):
        t1 = memos.create_thread("Old", "alice")
        t2 = memos.create_thread("New", "bob")
        threads = memos.get_threads()
        assert threads[0]["id"] == t2["id"]

    def test_include_recent_posts(self):
        t = memos.create_thread("RFC", "alice")
        memos.post_memo(t["id"], "Post 1", "bob")
        memos.post_memo(t["id"], "Post 2", "carol")
        memos.post_memo(t["id"], "Post 3", "dave")
        threads = memos.get_threads(include_recent_posts=True)
        assert len(threads[0]["recent_posts"]) == 2
        assert threads[0]["recent_posts"][-1]["text"] == "Post 3"


class TestGetPosts:
    def test_empty(self):
        t = memos.create_thread("RFC", "alice")
        assert memos.get_posts(t["id"]) == []

    def test_returns_ordered(self):
        t = memos.create_thread("RFC", "alice")
        memos.post_memo(t["id"], "First", "bob")
        memos.post_memo(t["id"], "Second", "carol")
        posts = memos.get_posts(t["id"])
        assert posts[0]["text"] == "First"
        assert posts[1]["text"] == "Second"


class TestDeleteThread:
    def test_deletes(self):
        t = memos.create_thread("RFC", "alice")
        memos.post_memo(t["id"], "Reply", "bob")
        assert memos.delete_thread(t["id"]) is True
        assert memos.get_thread(t["id"]) is None
        assert memos.get_posts(t["id"]) == []

    def test_not_found(self):
        assert memos.delete_thread("fake-id") is False


class TestSnapshotRestore:
    def test_round_trip(self):
        t = memos.create_thread("RFC", "alice")
        memos.post_memo(t["id"], "Reply", "bob")
        snap = memos.get_memo_snapshot()
        memos.clear_memos()
        assert memos.get_threads() == []
        memos.restore_memos(snap["threads"], snap["posts"])
        assert len(memos.get_threads()) == 1
        assert len(memos.get_posts(t["id"])) == 1
