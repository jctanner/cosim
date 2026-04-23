import re

from lib.gitlab import generate_commit_id, get_accessible_repos, DEFAULT_REPO_ACCESS


class TestGenerateCommitId:
    def test_format(self):
        cid = generate_commit_id("initial commit", "alice", 1700000000.0)
        assert re.match(r"^[a-f0-9]{8}$", cid)

    def test_deterministic(self):
        a = generate_commit_id("fix bug", "alice", 1700000000.0)
        b = generate_commit_id("fix bug", "alice", 1700000000.0)
        assert a == b

    def test_different_messages(self):
        a = generate_commit_id("fix bug", "alice", 1700000000.0)
        b = generate_commit_id("add feature", "alice", 1700000000.0)
        assert a != b

    def test_different_authors(self):
        a = generate_commit_id("fix bug", "alice", 1700000000.0)
        b = generate_commit_id("fix bug", "bob", 1700000000.0)
        assert a != b


class TestGetAccessibleRepos:
    REPOS = [
        {"name": "frontend"},
        {"name": "backend"},
        {"name": "infra"},
    ]

    def test_no_restrictions(self):
        result = get_accessible_repos("alice", self.REPOS)
        assert len(result) == 3

    def test_restricted_access(self):
        DEFAULT_REPO_ACCESS["infra"] = {"devops"}
        result = get_accessible_repos("alice", self.REPOS)
        assert len(result) == 2
        assert all(r["name"] != "infra" for r in result)

    def test_has_access(self):
        DEFAULT_REPO_ACCESS["infra"] = {"devops"}
        result = get_accessible_repos("devops", self.REPOS)
        assert len(result) == 3

    def test_unrestricted_repos_always_accessible(self):
        DEFAULT_REPO_ACCESS["infra"] = {"devops"}
        result = get_accessible_repos("alice", self.REPOS)
        names = {r["name"] for r in result}
        assert "frontend" in names
        assert "backend" in names
