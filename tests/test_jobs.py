"""Tests for jobs module and jobs API routes."""

import re
import time

from lib.jobs import generate_run_id, get_runs_snapshot, restore_runs


class TestGenerateRunId:
    def test_format(self):
        rid = generate_run_id("my-repo", "main.py", 1700000000.0)
        assert re.match(r"^RUN-[A-F0-9]{6}$", rid)

    def test_deterministic(self):
        a = generate_run_id("repo", "script.py", 1700000000.0)
        b = generate_run_id("repo", "script.py", 1700000000.0)
        assert a == b

    def test_different_repos(self):
        a = generate_run_id("repo-a", "script.py", 1700000000.0)
        b = generate_run_id("repo-b", "script.py", 1700000000.0)
        assert a != b

    def test_different_paths(self):
        a = generate_run_id("repo", "main.py", 1700000000.0)
        b = generate_run_id("repo", "test.py", 1700000000.0)
        assert a != b

    def test_different_timestamps(self):
        a = generate_run_id("repo", "main.py", 1700000000.0)
        b = generate_run_id("repo", "main.py", 1700000001.0)
        assert a != b


class TestGetRunsSnapshot:
    def test_empty(self):
        assert get_runs_snapshot({}) == []

    def test_returns_list_of_values(self):
        runs = {
            "RUN-AAA": {"run_id": "RUN-AAA", "status": "completed"},
            "RUN-BBB": {"run_id": "RUN-BBB", "status": "queued"},
        }
        result = get_runs_snapshot(runs)
        assert len(result) == 2
        ids = {r["run_id"] for r in result}
        assert ids == {"RUN-AAA", "RUN-BBB"}


class TestRestoreRuns:
    def test_empty(self):
        assert restore_runs([]) == {}

    def test_preserves_completed(self):
        data = [{"run_id": "RUN-AAA", "status": "completed", "exit_code": 0}]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "completed"

    def test_abandons_queued(self):
        data = [{"run_id": "RUN-AAA", "status": "queued"}]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "abandoned"

    def test_abandons_running(self):
        data = [{"run_id": "RUN-AAA", "status": "running"}]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "abandoned"

    def test_preserves_failed(self):
        data = [{"run_id": "RUN-AAA", "status": "failed"}]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "failed"

    def test_preserves_timeout(self):
        data = [{"run_id": "RUN-AAA", "status": "timeout"}]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "timeout"

    def test_mixed_statuses(self):
        data = [
            {"run_id": "RUN-AAA", "status": "completed"},
            {"run_id": "RUN-BBB", "status": "running"},
            {"run_id": "RUN-CCC", "status": "queued"},
            {"run_id": "RUN-DDD", "status": "failed"},
        ]
        result = restore_runs(data)
        assert result["RUN-AAA"]["status"] == "completed"
        assert result["RUN-BBB"]["status"] == "abandoned"
        assert result["RUN-CCC"]["status"] == "abandoned"
        assert result["RUN-DDD"]["status"] == "failed"

    def test_skips_records_without_run_id(self):
        data = [{"status": "completed"}, {"run_id": "RUN-AAA", "status": "completed"}]
        result = restore_runs(data)
        assert len(result) == 1
        assert "RUN-AAA" in result
