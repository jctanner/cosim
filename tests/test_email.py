from lib import email


class TestSendEmail:
    def test_creates_entry(self):
        e = email.send_email("alice", "Hello", "World")
        assert e["sender"] == "alice"
        assert e["subject"] == "Hello"
        assert e["body"] == "World"
        assert e["id"] == 1

    def test_auto_incrementing_id(self):
        e1 = email.send_email("alice", "First", "body")
        e2 = email.send_email("bob", "Second", "body")
        assert e2["id"] == e1["id"] + 1

    def test_has_timestamp(self):
        e = email.send_email("alice", "Hello", "World")
        assert "timestamp" in e
        assert isinstance(e["timestamp"], float)

    def test_empty_read_by(self):
        e = email.send_email("alice", "Hello", "World")
        assert e["read_by"] == []


class TestGetInbox:
    def test_empty(self):
        assert email.get_inbox() == []

    def test_returns_all(self):
        email.send_email("alice", "First", "body")
        email.send_email("bob", "Second", "body")
        assert len(email.get_inbox()) == 2


class TestGetEmail:
    def test_found(self):
        email.send_email("alice", "Hello", "World")
        e = email.get_email(1)
        assert e is not None
        assert e["sender"] == "alice"

    def test_not_found(self):
        assert email.get_email(999) is None

    def test_returns_copy(self):
        email.send_email("alice", "Hello", "World")
        e1 = email.get_email(1)
        e2 = email.get_email(1)
        assert e1 is not e2


class TestClearInbox:
    def test_clears(self):
        email.send_email("alice", "Hello", "World")
        email.clear_inbox()
        assert email.get_inbox() == []


class TestSnapshotRestore:
    def test_round_trip(self):
        email.send_email("alice", "Hello", "World")
        snapshot = email.get_inbox_snapshot()
        email.clear_inbox()
        assert email.get_inbox() == []
        email.restore_inbox(snapshot)
        assert len(email.get_inbox()) == 1
        assert email.get_inbox()[0]["sender"] == "alice"
