import re

from lib.gitlab import DEFAULT_REPO_ACCESS, generate_commit_id, get_accessible_repos, next_mr_id
from lib.webapp.routes.gitlab import _normalize_mr_id


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


class TestNormalizeMrId:
    def test_bare_number(self):
        assert _normalize_mr_id("1") == "!1"

    def test_with_bang(self):
        assert _normalize_mr_id("!1") == "!1"

    def test_url_encoded(self):
        assert _normalize_mr_id("%211") == "!1"

    def test_url_encoded_double_digit(self):
        assert _normalize_mr_id("%2142") == "!42"

    def test_with_whitespace(self):
        assert _normalize_mr_id("  !3  ") == "!3"

    def test_bare_number_double_digit(self):
        assert _normalize_mr_id("99") == "!99"

    def test_url_encoded_with_bang(self):
        assert _normalize_mr_id("%21!1") == "!1"


class TestNextMrId:
    def test_empty_list(self):
        assert next_mr_id([]) == "!1"

    def test_single_mr(self):
        assert next_mr_id([{"id": "!1"}]) == "!2"

    def test_multiple_mrs(self):
        assert next_mr_id([{"id": "!1"}, {"id": "!2"}, {"id": "!3"}]) == "!4"

    def test_non_sequential(self):
        assert next_mr_id([{"id": "!1"}, {"id": "!5"}]) == "!6"
