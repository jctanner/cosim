"""GitLab API routes."""

import io
import json
import tarfile
import time

from flask import Blueprint, jsonify, request, send_file

from lib.gitlab import GITLAB_DIR, generate_commit_id, next_mr_id, save_merge_requests, save_repos_index


def _normalize_mr_id(mr_id: str) -> str:
    """Normalize MR ID to canonical !N format. Accepts '1', '!1', '%211'."""
    from urllib.parse import unquote
    mr_id = unquote(mr_id).strip().lstrip("!")
    return f"!{mr_id}"
from lib.webapp.helpers import _broadcast_gitlab_event
from lib.webapp.state import _gitlab_commits, _gitlab_lock, _gitlab_merge_requests, _gitlab_repos

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


# --- Merge Requests ---


@bp.route("/api/gitlab/repos/<project>/merge-requests", methods=["GET"])
def list_merge_requests(project):
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        mrs = list(_gitlab_merge_requests.get(project, []))
    status_filter = request.args.get("status")
    if status_filter:
        mrs = [mr for mr in mrs if mr.get("status") == status_filter]
    mrs.reverse()
    return jsonify(mrs)


@bp.route("/api/gitlab/repos/<project>/merge-requests", methods=["POST"])
def create_merge_request(project):
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    diff = data.get("diff", "").strip()
    if not diff:
        return jsonify({"error": "diff required"}), 400

    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        mrs = _gitlab_merge_requests.setdefault(project, [])
        mr_id = next_mr_id(mrs)
        now = time.time()
        # Count additions/deletions from diff
        additions = 0
        deletions = 0
        for line in diff.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                additions += 1
            elif line.startswith("-") and not line.startswith("---"):
                deletions += 1

        mr = {
            "id": mr_id,
            "title": title,
            "description": data.get("description", "").strip(),
            "author": data.get("author", "System"),
            "project": project,
            "diff": diff,
            "additions": additions,
            "deletions": deletions,
            "status": "open",
            "reviewers": data.get("reviewers", []),
            "approvals": [],
            "comments": [],
            "created_at": now,
            "updated_at": now,
        }
        mrs.append(mr)
        save_merge_requests(project, mrs)
    _broadcast_gitlab_event("mr_created", {"project": project, **mr})
    return jsonify(mr), 201


@bp.route("/api/gitlab/repos/<project>/merge-requests/<mr_id>", methods=["GET"])
def get_merge_request(project, mr_id):
    mr_id = _normalize_mr_id(mr_id)
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        for mr in _gitlab_merge_requests.get(project, []):
            if mr["id"] == mr_id:
                return jsonify(dict(mr))
    return jsonify({"error": "merge request not found"}), 404


@bp.route("/api/gitlab/repos/<project>/merge-requests/<mr_id>/comment", methods=["POST"])
def comment_on_mr(project, mr_id):
    mr_id = _normalize_mr_id(mr_id)
    data = request.get_json(force=True)
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        for mr in _gitlab_merge_requests.get(project, []):
            if mr["id"] == mr_id:
                comment = {
                    "author": data.get("author", "System"),
                    "text": text,
                    "timestamp": time.time(),
                }
                mr["comments"].append(comment)
                mr["updated_at"] = time.time()
                save_merge_requests(project, _gitlab_merge_requests[project])
                return jsonify(comment), 201
    return jsonify({"error": "merge request not found"}), 404


@bp.route("/api/gitlab/repos/<project>/merge-requests/<mr_id>", methods=["PUT"])
def update_mr(project, mr_id):
    mr_id = _normalize_mr_id(mr_id)
    data = request.get_json(force=True)
    new_status = data.get("status")
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        for mr in _gitlab_merge_requests.get(project, []):
            if mr["id"] == mr_id:
                if new_status:
                    current = mr["status"]
                    # State machine: open -> closed|merged, closed -> open, merged -> (none)
                    valid_transitions = {
                        "open": {"closed"},
                        "closed": {"open"},
                        "merged": set(),
                    }
                    if new_status not in valid_transitions.get(current, set()):
                        return jsonify({"error": f"cannot transition from {current} to {new_status}"}), 400
                    mr["status"] = new_status
                mr["updated_at"] = time.time()
                save_merge_requests(project, _gitlab_merge_requests[project])
                return jsonify(mr)
    return jsonify({"error": "merge request not found"}), 404


@bp.route("/api/gitlab/repos/<project>/merge-requests/<mr_id>/approve", methods=["POST"])
def approve_mr(project, mr_id):
    mr_id = _normalize_mr_id(mr_id)
    data = request.get_json(force=True)
    author = data.get("author", "System")
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        for mr in _gitlab_merge_requests.get(project, []):
            if mr["id"] == mr_id:
                if mr["status"] != "open":
                    return jsonify({"error": f"cannot approve: status is {mr['status']}"}), 400
                if author == mr["author"]:
                    return jsonify({"error": "cannot approve your own merge request"}), 400
                approvals = mr.setdefault("approvals", [])
                if author not in approvals:
                    approvals.append(author)
                    mr["updated_at"] = time.time()
                    save_merge_requests(project, _gitlab_merge_requests[project])
                return jsonify(mr)
    return jsonify({"error": "merge request not found"}), 404


@bp.route("/api/gitlab/repos/<project>/merge-requests/<mr_id>/merge", methods=["POST"])
def merge_mr(project, mr_id):
    mr_id = _normalize_mr_id(mr_id)
    data = request.get_json(force=True) if request.data else {}
    with _gitlab_lock:
        if project not in _gitlab_repos:
            return jsonify({"error": "repo not found"}), 404
        for mr in _gitlab_merge_requests.get(project, []):
            if mr["id"] == mr_id:
                if mr["status"] != "open":
                    return jsonify({"error": f"cannot merge: status is {mr['status']}"}), 400
                approvals = mr.get("approvals", [])
                if not approvals:
                    return jsonify({"error": "cannot merge: no approvals. At least one non-author approval required"}), 400
                mr["status"] = "merged"
                mr["merged_by"] = data.get("author", "System")
                mr["merged_at"] = time.time()
                mr["updated_at"] = time.time()
                save_merge_requests(project, _gitlab_merge_requests[project])
                _broadcast_gitlab_event("mr_merged", {"project": project, "id": mr_id, "title": mr["title"]})
                return jsonify(mr)
    return jsonify({"error": "merge request not found"}), 404
