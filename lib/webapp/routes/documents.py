"""Document and folder API routes."""

import io
import tarfile
import time

from flask import Blueprint, jsonify, request, send_file

from lib.docs import DEFAULT_FOLDER_ACCESS, DEFAULT_FOLDERS, slugify
from lib.webapp.helpers import _broadcast_doc_event, _broadcast_folder_event, _extract_snippet, _save_index
from lib.webapp.state import (
    DOCS_DIR,
    _docs_index,
    _docs_lock,
    _folder_access,
    _folder_lock,
    _folders,
)

bp = Blueprint("documents", __name__)


@bp.route("/api/folders", methods=["GET"])
def list_folders():
    with _folder_lock:
        result = []
        for name in sorted(_folders.keys()):
            info = _folders[name]
            access = sorted(_folder_access.get(name, set()))
            result.append(
                {
                    "name": name,
                    "type": info["type"],
                    "description": info["description"],
                    "access": access,
                }
            )
    return jsonify(result)


@bp.route("/api/folders", methods=["POST"])
def create_folder():
    data = request.get_json(force=True)
    name = data.get("name", "").strip().strip("/")
    folder_type = data.get("type", "project")
    description = data.get("description", "")
    created_by = data.get("created_by", "unknown")
    access = data.get("access", [])

    if not name:
        return jsonify({"error": "name required"}), 400

    # Validate folder name: allow alphanumeric, hyphens, underscores, and /
    if not all(c.isalnum() or c in "-_/" for c in name):
        return jsonify({"error": "folder name may only contain alphanumeric, hyphens, underscores, and /"}), 400

    with _folder_lock:
        if name in _folders:
            return jsonify({"error": f"folder '{name}' already exists"}), 409

        _folders[name] = {"type": folder_type, "description": description}
        DEFAULT_FOLDERS[name] = {"type": folder_type, "description": description}
        _folder_access[name] = set(access)
        DEFAULT_FOLDER_ACCESS[name] = set(access)

    folder_dir = DOCS_DIR / name
    folder_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "name": name,
        "type": folder_type,
        "description": description,
        "access": sorted(access),
        "created_by": created_by,
    }
    _broadcast_folder_event("created", result)
    return jsonify(result), 201


@bp.route("/api/folders/<path:folder_path>/download", methods=["GET"])
def download_folder(folder_path):
    folder_path = folder_path.strip("/")
    folder_dir = DOCS_DIR / folder_path
    if not folder_dir.is_dir():
        return jsonify({"error": f"folder '{folder_path}' not found"}), 404

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for txt in sorted(folder_dir.rglob("*.txt")):
            arcname = str(txt.relative_to(DOCS_DIR))
            tar.add(str(txt), arcname=arcname)
    buf.seek(0)
    download_name = folder_path.replace("/", "-") + ".tar.gz"
    return send_file(buf, mimetype="application/gzip", as_attachment=True, download_name=download_name)


@bp.route("/api/folders/<path:folder_path>/access", methods=["PUT"])
def update_folder_access(folder_path):
    folder_path = folder_path.strip("/")
    data = request.get_json(force=True)
    access = data.get("access", [])

    with _folder_lock:
        if folder_path not in _folders:
            return jsonify({"error": f"folder '{folder_path}' not found"}), 404
        _folder_access[folder_path] = set(access)
        DEFAULT_FOLDER_ACCESS[folder_path] = set(access)

    result = {
        "name": folder_path,
        "access": sorted(access),
    }
    _broadcast_folder_event("access_updated", result)
    return jsonify(result)


@bp.route("/api/docs", methods=["GET"])
def list_docs():
    folder_filter = request.args.get("folder")
    with _docs_lock:
        docs = list(_docs_index.values())
    if folder_filter:
        docs = [d for d in docs if d.get("folder") == folder_filter]
    return jsonify(docs)


@bp.route("/api/docs", methods=["POST"])
def create_doc():
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    content = data.get("content", "")
    author = data.get("author", "unknown")
    folder = data.get("folder", "shared").strip()
    if not title:
        return jsonify({"error": "title required"}), 400

    with _folder_lock:
        if folder not in _folders:
            return jsonify({"error": f"folder '{folder}' not found"}), 400

    slug = slugify(title)
    folder_dir = DOCS_DIR / folder
    folder_dir.mkdir(parents=True, exist_ok=True)
    doc_path = folder_dir / f"{slug}.txt"

    with _docs_lock:
        if slug in _docs_index:
            return jsonify({"error": f"document '{slug}' already exists"}), 409

        doc_path.write_text(content, encoding="utf-8")
        now = time.time()
        meta = {
            "slug": slug,
            "title": title,
            "folder": folder,
            "created_at": now,
            "updated_at": now,
            "created_by": author,
            "size": len(content.encode("utf-8")),
            "preview": content[:100],
        }
        _docs_index[slug] = meta
        _save_index()

    _broadcast_doc_event("created", meta)
    return jsonify(meta), 201


@bp.route("/api/docs/search", methods=["GET"])
def search_docs():
    query = request.args.get("q", "").strip().lower()
    folders_param = request.args.get("folders", "")
    if not query:
        return jsonify([])

    folder_filter = None
    if folders_param:
        folder_filter = {f.strip() for f in folders_param.split(",") if f.strip()}

    results = []
    with _docs_lock:
        for slug, meta in _docs_index.items():
            if folder_filter and meta.get("folder") not in folder_filter:
                continue
            folder = meta.get("folder", "shared")
            doc_path = DOCS_DIR / folder / f"{slug}.txt"
            if not doc_path.exists():
                continue
            content = doc_path.read_text(encoding="utf-8", errors="replace")
            if query in meta.get("title", "").lower() or query in content.lower():
                results.append(
                    {
                        **meta,
                        "snippet": _extract_snippet(content, query),
                    }
                )
    return jsonify(results)


@bp.route("/api/docs/<path:folder>/<slug>", methods=["GET"])
def get_doc(folder, slug):
    with _docs_lock:
        meta = _docs_index.get(slug)
    if meta is None or meta.get("folder") != folder:
        return jsonify({"error": "not found"}), 404
    doc_path = DOCS_DIR / folder / f"{slug}.txt"
    if not doc_path.exists():
        return jsonify({"error": "not found"}), 404
    content = doc_path.read_text(encoding="utf-8", errors="replace")
    return jsonify({**meta, "content": content})


@bp.route("/api/docs/<path:folder>/<slug>", methods=["PUT"])
def update_doc(folder, slug):
    data = request.get_json(force=True)
    content = data.get("content", "")
    author = data.get("author", "unknown")
    new_title = data.get("title", "").strip()
    new_slug = data.get("new_slug", "").strip()

    # Normalize new_slug through slugify if provided
    if new_slug:
        new_slug = slugify(new_slug)
        if not new_slug:
            return jsonify({"error": "invalid new_slug (empty after normalization)"}), 400

    with _docs_lock:
        meta = _docs_index.get(slug)
        if meta is None or meta.get("folder") != folder:
            return jsonify({"error": "not found"}), 404

        # Check for slug collision before proceeding
        if new_slug and new_slug != slug and new_slug in _docs_index:
            return jsonify({"error": f"slug '{new_slug}' already exists"}), 409

        doc_path = DOCS_DIR / folder / f"{slug}.txt"

        # Save current content as a version before overwriting
        old_content = ""
        if doc_path.exists():
            old_content = doc_path.read_text(encoding="utf-8", errors="replace")
        if "history" not in meta:
            meta["history"] = []
        meta["history"].append(
            {
                "content": old_content,
                "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
                "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
            }
        )

        doc_path.write_text(content, encoding="utf-8")
        meta["updated_at"] = time.time()
        meta["updated_by"] = author
        meta["size"] = len(content.encode("utf-8"))
        meta["preview"] = content[:100]

        # Update title if provided
        if new_title:
            meta["title"] = new_title

        # Rename slug if provided and different
        if new_slug and new_slug != slug:
            new_path = DOCS_DIR / folder / f"{new_slug}.txt"
            doc_path.rename(new_path)
            meta["slug"] = new_slug
            del _docs_index[slug]
            _docs_index[new_slug] = meta

        _save_index()

    _broadcast_doc_event("updated", meta)
    return jsonify(meta)


@bp.route("/api/docs/<path:folder>/<slug>/history", methods=["GET"])
def get_doc_history(folder, slug):
    with _docs_lock:
        meta = _docs_index.get(slug)
    if meta is None or meta.get("folder") != folder:
        return jsonify({"error": "not found"}), 404
    history = meta.get("history", [])
    # Add current version as the first entry
    doc_path = DOCS_DIR / folder / f"{slug}.txt"
    current_content = ""
    if doc_path.exists():
        current_content = doc_path.read_text(encoding="utf-8", errors="replace")
    current = {
        "content": current_content,
        "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
        "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
        "is_current": True,
    }
    return jsonify([current] + list(reversed(history)))


@bp.route("/api/docs/<path:folder>/<slug>/append", methods=["POST"])
def append_doc(folder, slug):
    data = request.get_json(force=True)
    content = data.get("content", "")
    author = data.get("author", "unknown")

    with _docs_lock:
        meta = _docs_index.get(slug)
        if meta is None or meta.get("folder") != folder:
            return jsonify({"error": "not found"}), 404

        doc_path = DOCS_DIR / folder / f"{slug}.txt"
        existing = doc_path.read_text(encoding="utf-8", errors="replace")

        # Save current content as a version before appending
        if "history" not in meta:
            meta["history"] = []
        meta["history"].append(
            {
                "content": existing,
                "updated_by": meta.get("updated_by", meta.get("created_by", "unknown")),
                "updated_at": meta.get("updated_at", meta.get("created_at", 0)),
            }
        )

        new_content = existing + "\n" + content
        doc_path.write_text(new_content, encoding="utf-8")
        meta["updated_at"] = time.time()
        meta["updated_by"] = author
        meta["size"] = len(new_content.encode("utf-8"))
        meta["preview"] = new_content[:100]
        _save_index()

    _broadcast_doc_event("appended", meta)
    return jsonify(meta)


@bp.route("/api/docs/<path:folder>/<slug>", methods=["DELETE"])
def delete_doc(folder, slug):
    with _docs_lock:
        meta = _docs_index.get(slug)
        if meta is None or meta.get("folder") != folder:
            return jsonify({"error": "not found"}), 404
        _docs_index.pop(slug)

        doc_path = DOCS_DIR / folder / f"{slug}.txt"
        if doc_path.exists():
            doc_path.unlink()
        _save_index()

    _broadcast_doc_event("deleted", meta)
    return jsonify({"status": "deleted", "slug": slug, "folder": folder})


# -- Backward-compatible flat doc routes (redirect to shared) --


@bp.route("/api/docs/<slug>", methods=["GET"])
def get_doc_flat(slug):
    """Backward-compatible: look up doc by slug alone."""
    with _docs_lock:
        meta = _docs_index.get(slug)
    if meta is None:
        return jsonify({"error": "not found"}), 404
    folder = meta.get("folder", "shared")
    doc_path = DOCS_DIR / folder / f"{slug}.txt"
    if not doc_path.exists():
        return jsonify({"error": "not found"}), 404
    content = doc_path.read_text(encoding="utf-8", errors="replace")
    return jsonify({**meta, "content": content})
