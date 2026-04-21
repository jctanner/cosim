"""GitLab API routes."""

import io
import json
import tarfile
import time

from flask import Blueprint, jsonify, request, send_file

from lib.gitlab import GITLAB_DIR, save_repos_index, generate_commit_id
from lib.webapp.state import _gitlab_repos, _gitlab_commits, _gitlab_lock
from lib.webapp.helpers import _broadcast_gitlab_event

bp = Blueprint("gitlab", __name__)


@bp.route("/api/gitlab/repos", methods=["GET"])
def list_gitlab_repos():
    with _gitlab_lock:
        repos = list(_gitlab_repos.values())
    return jsonify(repos)


@bp.route("/api/gitlab/repos", methods=["POST"])
def create_gitlab_repo():
    data = request.get_json(force=True)
    name = data.get("name", "").strip()
    description = data.get("description", "")
    author = data.get("author", "unknown")
    if not name:
        return jsonify({"error": "name required"}), 400

    with _gitlab_lock:
        if name in _gitlab_repos:
            return jsonify({"error": f"repo '{name}' already exists"}), 409

        now = time.time()
        meta = {
            "name": name,
            "description": description,
            "created_by": author,
            "created_at": now,
        }
        _gitlab_repos[name] = meta
        _gitlab_commits[name] = []

        # Persist to disk
        repo_dir = GITLAB_DIR / name / "files"
        repo_dir.mkdir(parents=True, exist_ok=True)
        commits_path = GITLAB_DIR / name / "_commits.json"
        commits_path.write_text("[]")
        save_repos_index(dict(_gitlab_repos))

    _broadcast_gitlab_event("repo_created", meta)
    return jsonify(meta), 201


@bp.route("/api/gitlab/repos/<project>/tree", methods=["GET"])
def gitlab_tree(project):
    path = request.args.get("path", "").strip().strip("/")
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404

    files_dir = GITLAB_DIR / project / "files"
    if path:
        target = files_dir / path
    else:
        target = files_dir

    if not target.exists() or not target.is_dir():
        return jsonify([])

    entries = []
    for item in sorted(target.iterdir()):
        rel = str(item.relative_to(files_dir))
        entry = {"name": item.name, "path": rel}
        if item.is_dir():
            entry["type"] = "dir"
        else:
            entry["type"] = "file"
        entries.append(entry)

    # Sort: dirs first, then files
    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"]))
    return jsonify(entries)


@bp.route("/api/gitlab/repos/<project>/file", methods=["GET"])
def gitlab_file(project):
    path = request.args.get("path", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400

    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404

    file_path = GITLAB_DIR / project / "files" / path
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "file not found"}), 404

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return jsonify({"path": path, "content": content})


@bp.route("/api/gitlab/repos/<project>/commit", methods=["POST"])
def gitlab_commit(project):
    data = request.get_json(force=True)
    message = data.get("message", "").strip()
    files = data.get("files", [])
    author = data.get("author", "unknown")
    if not message:
        return jsonify({"error": "message required"}), 400
    if not files:
        return jsonify({"error": "files required"}), 400

    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404

        now = time.time()
        commit_id = generate_commit_id(message, author, now)

        # Write files to disk
        files_dir = GITLAB_DIR / project / "files"
        paths = []
        for f in files:
            fp = files_dir / f["path"]
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(f["content"], encoding="utf-8")
            paths.append(f["path"])

        commit = {
            "id": commit_id,
            "message": message,
            "author": author,
            "timestamp": now,
            "files": paths,
        }
        _gitlab_commits.setdefault(project, []).append(commit)

        # Persist commits
        commits_path = GITLAB_DIR / project / "_commits.json"
        commits_path.write_text(json.dumps(_gitlab_commits[project], indent=2))

    _broadcast_gitlab_event("committed", {"project": project, "commit": commit})
    return jsonify(commit), 201


@bp.route("/api/gitlab/repos/<project>/download", methods=["GET"])
def gitlab_download(project):
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404

    files_dir = GITLAB_DIR / project / "files"
    if not files_dir.exists():
        return jsonify({"error": "repo has no files"}), 404

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for fpath in sorted(files_dir.rglob("*")):
            if fpath.is_file():
                arcname = f"{project}/{fpath.relative_to(files_dir)}"
                tar.add(str(fpath), arcname=arcname)
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/gzip",
        as_attachment=True,
        download_name=f"{project}.tar.gz",
    )


@bp.route("/api/gitlab/repos/<project>/log", methods=["GET"])
def gitlab_log(project):
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        commits = list(_gitlab_commits.get(project, []))
    # Return newest first
    commits.reverse()
    return jsonify(commits)
