"""Jobs API routes."""

import hashlib
import secrets
import time

from flask import Blueprint, jsonify, request

from lib.gitlab import GITLAB_DIR
from lib.jobs import generate_run_id, save_runs_index
from lib.webapp.helpers import _broadcast_jobs_event
from lib.webapp.state import _gitlab_commits, _gitlab_lock, _runs, _runs_lock

bp = Blueprint("jobs", __name__)


@bp.route("/api/jobs/runs", methods=["GET"])
def list_runs():
    status_filter = request.args.get("status")
    agent_filter = request.args.get("agent")
    repo_filter = request.args.get("repo")
    with _runs_lock:
        runs = list(_runs.values())
    if status_filter:
        runs = [r for r in runs if r.get("status") == status_filter]
    if agent_filter:
        runs = [r for r in runs if r.get("agent_id") == agent_filter]
    if repo_filter:
        runs = [r for r in runs if r.get("repo") == repo_filter]
    runs.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return jsonify(runs)


@bp.route("/api/jobs/runs", methods=["POST"])
def create_run():
    data = request.get_json(force=True)
    repo = data.get("repo", "").strip()
    path = data.get("path", "").strip()
    ref = data.get("ref", "main").strip()
    language = data.get("language", "python").strip()
    network = bool(data.get("network", False))
    agent_id = data.get("agent_id", "unknown").strip()

    if not repo or not path:
        return jsonify({"error": "repo and path required"}), 400
    if language != "python":
        return jsonify({"error": "only python is supported in v1"}), 400

    with _gitlab_lock:
        commits = _gitlab_commits.get(repo)
        if commits is None:
            return jsonify({"error": f"repo '{repo}' not found"}), 404

        commit_id = None
        if ref == "main" or ref == "latest":
            if commits:
                commit_id = commits[-1]["id"]
        else:
            for c in commits:
                if c["id"] == ref:
                    commit_id = c["id"]
                    break
        if not commit_id and commits:
            commit_id = commits[-1]["id"]

    file_path = GITLAB_DIR / repo / "files" / path
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": f"file '{path}' not found in repo '{repo}'"}), 404

    content = file_path.read_text(encoding="utf-8", errors="replace")
    content_sha256 = hashlib.sha256(content.encode()).hexdigest()

    now = time.time()
    run_id = generate_run_id(repo, path, now)
    nonce = secrets.token_hex(32)

    # Collect all files in the repo for full-repo copy
    files_dir = GITLAB_DIR / repo / "files"
    repo_files = {}
    if files_dir.exists():
        for f in files_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(files_dir))
                repo_files[rel] = f.read_text(encoding="utf-8", errors="replace")

    run = {
        "run_id": run_id,
        "agent_id": agent_id,
        "status": "queued",
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "repo": repo,
        "path": path,
        "commit_id": commit_id,
        "language": language,
        "content_sha256": content_sha256,
        "command": f"python /work/{path}",
        "image": "python:3.13-slim",
        "timeout_seconds": 30,
        "exit_code": None,
        "network_enabled": network,
        "stdout": None,
        "stderr": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "nonce": nonce,
        "verifier_status": "not_run",
        "verifier_details": None,
        "receipt_sha256": None,
        "repo_files": repo_files,
    }

    with _runs_lock:
        if run_id in _runs:
            run_id = run_id + "X"
            run["run_id"] = run_id
        _runs[run_id] = run
        save_runs_index(dict(_runs))

    _broadcast_jobs_event("queued", {"run_id": run_id, "repo": repo, "path": path, "agent_id": agent_id})
    return jsonify({"run_id": run_id, "commit_id": commit_id, "status": "queued"}), 201


@bp.route("/api/jobs/runs/<run_id>", methods=["GET"])
def get_run(run_id):
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    safe = {k: v for k, v in run.items() if k != "repo_files"}
    return jsonify(safe)


@bp.route("/api/jobs/runs/<run_id>", methods=["PATCH"])
def update_run(run_id):
    data = request.get_json(force=True)
    with _runs_lock:
        run = _runs.get(run_id)
        if run is None:
            return jsonify({"error": "run not found"}), 404

        allowed_fields = {
            "status", "started_at", "finished_at", "exit_code",
            "stdout", "stderr", "stdout_sha256", "stderr_sha256",
            "verifier_status", "verifier_details", "receipt_sha256",
        }
        for key in allowed_fields:
            if key in data:
                run[key] = data[key]

        save_runs_index(dict(_runs))

    action = data.get("status", "updated")
    _broadcast_jobs_event(action, {"run_id": run_id, "status": run.get("status")})
    return jsonify({k: v for k, v in run.items() if k != "repo_files"})


@bp.route("/api/jobs/runs/<run_id>/files", methods=["GET"])
def get_run_files(run_id):
    """Return the repo_files snapshot for the job runner to write to workspace."""
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return jsonify({"error": "run not found"}), 404
    return jsonify(run.get("repo_files", {}))
