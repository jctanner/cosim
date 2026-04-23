import pytest

from lib import blog


class TestCreatePost:
    def test_returns_post(self):
        p = blog.create_post("Hello World", "Body text", "alice")
        assert "hello-world" in p["slug"]
        assert p["title"] == "Hello World"
        assert p["author"] == "alice"
        assert p["status"] == "published"
        assert p["reply_count"] == 0

    def test_external(self):
        p = blog.create_post("Title", "Body", "alice", is_external=True)
        assert p["is_external"] is True

    def test_tags(self):
        p = blog.create_post("Title", "Body", "alice", tags=["eng", "rfc"])
        assert p["tags"] == ["eng", "rfc"]

    def test_default_tags_empty(self):
        p = blog.create_post("Title", "Body", "alice")
        assert p["tags"] == []

    def test_draft_status(self):
        p = blog.create_post("Title", "Body", "alice", status="draft")
        assert p["status"] == "draft"


class TestUpdatePost:
    def test_update_title(self):
        p = blog.create_post("Old Title", "Body", "alice")
        updated = blog.update_post(p["slug"], title="New Title")
        assert updated["title"] == "New Title"

    def test_update_status(self):
        p = blog.create_post("Title", "Body", "alice", status="draft")
        updated = blog.update_post(p["slug"], status="published")
        assert updated["status"] == "published"

    def test_ignores_unknown_fields(self):
        p = blog.create_post("Title", "Body", "alice")
        updated = blog.update_post(p["slug"], unknown_field="value")
        assert "unknown_field" not in updated

    def test_nonexistent_post(self):
        with pytest.raises(ValueError, match="Blog post not found"):
            blog.update_post("fake-slug", title="New")


class TestReplyToPost:
    def test_creates_reply(self):
        p = blog.create_post("Title", "Body", "alice")
        r = blog.reply_to_post(p["slug"], "Great post!", "bob")
        assert r["author"] == "bob"
        assert r["text"] == "Great post!"
        assert r["post_slug"] == p["slug"]

    def test_updates_post_metadata(self):
        p = blog.create_post("Title", "Body", "alice")
        blog.reply_to_post(p["slug"], "Reply", "bob")
        updated = blog.get_post(p["slug"])
        assert updated["reply_count"] == 1
        assert updated["last_reply_author"] == "bob"

    def test_nonexistent_post(self):
        with pytest.raises(ValueError, match="Blog post not found"):
            blog.reply_to_post("fake-slug", "text", "alice")


class TestGetPosts:
    def test_empty(self):
        assert blog.get_posts() == []

    def test_sorted_by_created_at_desc(self):
        blog.create_post("First", "Body", "alice")
        blog.create_post("Second", "Body", "bob")
        posts = blog.get_posts()
        assert posts[0]["title"] == "Second"

    def test_include_recent_replies(self):
        p = blog.create_post("Title", "Body", "alice")
        blog.reply_to_post(p["slug"], "Reply 1", "bob")
        blog.reply_to_post(p["slug"], "Reply 2", "carol")
        posts = blog.get_posts(include_recent_replies=True)
        assert len(posts[0]["recent_replies"]) == 1
        assert posts[0]["recent_replies"][0]["text"] == "Reply 2"


class TestDeletePost:
    def test_deletes(self):
        p = blog.create_post("Title", "Body", "alice")
        blog.reply_to_post(p["slug"], "Reply", "bob")
        assert blog.delete_post(p["slug"]) is True
        assert blog.get_post(p["slug"]) is None
        assert blog.get_replies(p["slug"]) == []

    def test_not_found(self):
        assert blog.delete_post("fake-slug") is False


class TestSnapshotRestore:
    def test_round_trip(self):
        p = blog.create_post("Title", "Body", "alice")
        blog.reply_to_post(p["slug"], "Reply", "bob")
        snap = blog.get_blog_snapshot()
        blog.clear_blog()
        assert blog.get_posts() == []
        blog.restore_blog(snap["posts"], snap["replies"])
        assert len(blog.get_posts()) == 1
        assert len(blog.get_replies(p["slug"])) == 1
