"""MCP server exposing 32 simulation tools via MCP-over-SSE.

Each agent gets its own FastMCP instance mounted at /agents/<key>/ on a
parent Starlette app.  Agent identity is baked into closures at construction
time — no auth tokens needed.

Run via:  python main.py mcp-server --scenario tech-startup --port 5001
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

logger = logging.getLogger("mcp_server")

# ---------------------------------------------------------------------------
# Audit ring buffer
# ---------------------------------------------------------------------------
_AUDIT_MAX = 10_000
_audit_log: deque[dict] = deque(maxlen=_AUDIT_MAX)
_AUDIT_FILE = Path(__file__).parent.parent / "var" / "logs" / "mcp_audit.log"

# ---------------------------------------------------------------------------
# Telemetry aggregation
# ---------------------------------------------------------------------------
_telemetry: dict[str, dict[str, Any]] = {}  # agent_key -> stats

# ---------------------------------------------------------------------------
# Done event queue (signal_done tracking for tier advancement)
# ---------------------------------------------------------------------------
_done_events: list[dict] = []
_done_event_counter: int = 0
_done_lock = threading.Lock()


def _record_audit(agent_key: str, tool_name: str, params: dict, result_summary: str, duration_ms: float):
    """Append an audit record."""
    entry = {
        "timestamp": time.time(),
        "agent_key": agent_key,
        "tool_name": tool_name,
        "params": params,
        "result_summary": result_summary[:200],
        "duration_ms": round(duration_ms, 2),
    }
    _audit_log.append(entry)
    # Best-effort append to disk
    try:
        _AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Scenario config loader (standalone — no imports from lib.scenario_loader)
# ---------------------------------------------------------------------------
SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


def _load_scenario_config(scenario_name: str) -> dict:
    """Load scenario YAML and resolve character paths. Returns raw config dict."""
    scenario_dir = SCENARIOS_DIR / scenario_name
    yaml_path = scenario_dir / "scenario.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"Scenario not found: {yaml_path}")
    with open(yaml_path) as f:
        config = yaml.safe_load(f)
    # Resolve character file paths to absolute
    for key, char in config.get("characters", {}).items():
        rel = char.get("character_file", "")
        if rel:
            char["character_file"] = str(scenario_dir / rel)
    return config


# ---------------------------------------------------------------------------
# Flask proxy helper
# ---------------------------------------------------------------------------

# The httpx.AsyncClient is stored here at runtime (set by lifespan)
_http_client: httpx.AsyncClient | None = None


async def _flask(method: str, path: str, flask_url: str, **kwargs) -> dict | list:
    """Make an async request to the Flask server."""
    assert _http_client is not None, "httpx client not initialized"
    url = flask_url.rstrip("/") + path
    resp = await _http_client.request(method, url, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Tool registration helpers
# ---------------------------------------------------------------------------


def _register_communication_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 7 communication tools on the server."""
    my_channels = set(config.get("memberships", {}).get(agent_key, []))
    # Auto-add director channel (created dynamically by Flask, not in scenario YAML)
    my_channels.add(f"#director-{agent_key}")

    @server.tool(
        name="list_channels",
        description="List all available chat channels with their descriptions and member counts.",
    )
    async def list_channels() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/channels", flask_url)
        _record_audit(agent_key, "list_channels", {}, f"{len(result)} channels", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="post_message",
        description="Post a message to a chat channel you belong to.",
    )
    async def post_message(channel: str, content: str) -> str:
        if channel not in my_channels:
            return f"Error: you are not a member of {channel}. Your channels: {sorted(my_channels)}"
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/messages",
            flask_url,
            json={
                "sender": display_name,
                "content": content,
                "channel": channel,
            },
        )
        _record_audit(
            agent_key, "post_message", {"channel": channel}, str(result.get("id", "")), (time.time() - t0) * 1000
        )
        return json.dumps(result)

    @server.tool(
        name="get_messages",
        description="Get recent messages from a channel you belong to. Use since_id to get only newer messages.",
    )
    async def get_messages(channel: str, since_id: int = 0, limit: int = 50) -> str:
        if channel not in my_channels:
            return f"Error: you are not a member of {channel}. Your channels: {sorted(my_channels)}"
        t0 = time.time()
        params: dict[str, Any] = {"channels": channel}
        if since_id:
            params["since"] = since_id
        result = await _flask("GET", "/api/messages", flask_url, params=params)
        if isinstance(result, list):
            result = result[-limit:]
        _record_audit(
            agent_key,
            "get_messages",
            {"channel": channel, "since_id": since_id},
            f"{len(result)} msgs",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="send_dm",
        description="Send a direct message to another team member via the #dms channel.",
    )
    async def send_dm(recipient_key: str, content: str) -> str:
        # Phase 1: use the existing #dms system channel with structured prefix
        t0 = time.time()
        dm_content = f"[DM to {recipient_key}] {content}"
        result = await _flask(
            "POST",
            "/api/messages",
            flask_url,
            json={
                "sender": display_name,
                "content": dm_content,
                "channel": "#dms",
            },
        )
        _record_audit(
            agent_key, "send_dm", {"recipient": recipient_key}, str(result.get("id", "")), (time.time() - t0) * 1000
        )
        return json.dumps({"sent": True, "channel": "#dms", "message_id": result.get("id")})

    @server.tool(
        name="get_my_dms",
        description="Get direct messages sent to you.",
    )
    async def get_my_dms(since_id: int = 0) -> str:
        t0 = time.time()
        params: dict[str, Any] = {"channels": "#dms"}
        if since_id:
            params["since"] = since_id
        all_msgs = await _flask("GET", "/api/messages", flask_url, params=params)
        # Filter to DMs addressed to this agent
        tag = f"[DM to {agent_key}]"
        dms = [m for m in all_msgs if tag in m.get("content", "")]
        _record_audit(agent_key, "get_my_dms", {"since_id": since_id}, f"{len(dms)} dms", (time.time() - t0) * 1000)
        return json.dumps(dms)

    @server.tool(
        name="join_channel",
        description="Join a chat channel. You will then be able to read and post messages there.",
    )
    async def join_channel(channel: str) -> str:
        t0 = time.time()
        encoded = channel.lstrip("#")
        result = await _flask(
            "POST",
            f"/api/channels/{encoded}/join",
            flask_url,
            json={
                "persona": agent_key,
            },
        )
        # Update local membership cache
        my_channels.add(channel)
        _record_audit(agent_key, "join_channel", {"channel": channel}, "joined", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="get_channel_members",
        description="Get the list of members in a channel.",
    )
    async def get_channel_members(channel: str) -> str:
        t0 = time.time()
        channels_list = await _flask("GET", "/api/channels", flask_url)
        for ch in channels_list:
            if ch.get("name") == channel:
                _record_audit(
                    agent_key,
                    "get_channel_members",
                    {"channel": channel},
                    f"{len(ch.get('members', []))} members",
                    (time.time() - t0) * 1000,
                )
                return json.dumps({"channel": channel, "members": ch.get("members", [])})
        _record_audit(agent_key, "get_channel_members", {"channel": channel}, "not found", (time.time() - t0) * 1000)
        return json.dumps({"error": f"Channel {channel} not found"})


def _register_document_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 9 document tools with folder access control."""
    my_folders = {folder for folder, members in config.get("folder_access", {}).items() if agent_key in members}

    async def _refresh_my_folders():
        """Refresh folder access from the server (picks up dynamically created folders)."""
        try:
            folders = await _flask("GET", "/api/folders", flask_url)
            my_folders.clear()
            for f in folders:
                if agent_key in f.get("access", []):
                    my_folders.add(f["name"])
        except Exception:
            pass

    async def _check_folder_access(folder: str) -> str | None:
        """Check folder access, refreshing from server on miss. Returns error string or None."""
        if folder in my_folders:
            return None
        await _refresh_my_folders()
        if folder in my_folders:
            return None
        return f"Error: you don't have access to folder '{folder}'. Your folders: {sorted(my_folders)}"

    @server.tool(
        name="create_doc",
        description="Create a new document in a folder you have access to.",
    )
    async def create_doc(title: str, folder: str, content: str) -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/docs",
            flask_url,
            json={
                "title": title,
                "content": content,
                "author": display_name,
                "folder": folder,
            },
        )
        _record_audit(
            agent_key,
            "create_doc",
            {"title": title, "folder": folder},
            result.get("slug", ""),
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="update_doc",
        description="Replace the content of an existing document. Optionally rename the title and/or slug.",
    )
    async def update_doc(folder: str, slug: str, content: str, title: str = "", new_slug: str = "") -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        payload: dict[str, str] = {"content": content, "author": display_name}
        if title:
            payload["title"] = title
        if new_slug:
            payload["new_slug"] = new_slug
        result = await _flask("PUT", f"/api/docs/{folder}/{slug}", flask_url, json=payload)
        _record_audit(agent_key, "update_doc", {"folder": folder, "slug": slug}, "updated", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="read_doc",
        description="Read a document's full content by folder and slug.",
    )
    async def read_doc(folder: str, slug: str) -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        result = await _flask("GET", f"/api/docs/{folder}/{slug}", flask_url)
        _record_audit(agent_key, "read_doc", {"folder": folder, "slug": slug}, "read", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="search_docs",
        description="Search documents by query string. Only searches folders you have access to.",
    )
    async def search_docs(query: str) -> str:
        await _refresh_my_folders()
        t0 = time.time()
        params: dict[str, str] = {"q": query}
        if my_folders:
            params["folders"] = ",".join(sorted(my_folders))
        result = await _flask("GET", "/api/docs/search", flask_url, params=params)
        _record_audit(agent_key, "search_docs", {"query": query}, f"{len(result)} results", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="list_docs",
        description="List documents, optionally filtered by folder.",
    )
    async def list_docs(folder: str | None = None) -> str:
        if folder:
            err = await _check_folder_access(folder)
            if err:
                return err
        else:
            await _refresh_my_folders()
        t0 = time.time()
        params: dict[str, str] = {}
        if folder:
            params["folder"] = folder
        result = await _flask("GET", "/api/docs", flask_url, params=params)
        if not folder:
            result = [d for d in result if d.get("folder", "") in my_folders]
        _record_audit(agent_key, "list_docs", {"folder": folder}, f"{len(result)} docs", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="delete_doc",
        description="Delete a document by folder and slug. Only works in folders you have access to.",
    )
    async def delete_doc(folder: str, slug: str) -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        result = await _flask("DELETE", f"/api/docs/{folder}/{slug}", flask_url)
        _record_audit(agent_key, "delete_doc", {"folder": folder, "slug": slug}, "deleted", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="append_doc",
        description="Append content to an existing document without replacing it. Only works in folders you have access to.",
    )
    async def append_doc(folder: str, slug: str, content: str) -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/docs/{folder}/{slug}/append",
            flask_url,
            json={
                "content": content,
                "author": display_name,
            },
        )
        _record_audit(agent_key, "append_doc", {"folder": folder, "slug": slug}, "appended", (time.time() - t0) * 1000)
        return json.dumps(result)

    all_persona_keys = sorted(config.get("characters", {}).keys())

    @server.tool(
        name="create_folder",
        description=(
            'Create a new folder (or nested folder like "projects/my-project"). '
            'You automatically get access. Pass access=["all"] to grant access to '
            "all team members, or a list of persona keys."
        ),
    )
    async def create_folder(name: str, description: str = "", access: list[str] | None = None) -> str:
        t0 = time.time()
        access_list = list(access) if access else []
        if "all" in access_list:
            access_list = list(all_persona_keys)
        if agent_key not in access_list:
            access_list.append(agent_key)
        result = await _flask(
            "POST",
            "/api/folders",
            flask_url,
            json={
                "name": name,
                "description": description,
                "type": "project",
                "created_by": display_name,
                "access": access_list,
            },
        )
        if "error" not in result:
            my_folders.add(name)
        _record_audit(agent_key, "create_folder", {"name": name}, result.get("name", ""), (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="update_folder_access",
        description=(
            'Update who can access a folder. Pass access=["all"] to grant access '
            "to all team members, or a list of persona keys. You must already have "
            "access to the folder."
        ),
    )
    async def update_folder_access(folder: str, access: list[str]) -> str:
        err = await _check_folder_access(folder)
        if err:
            return err
        t0 = time.time()
        access_list = list(access)
        if "all" in access_list:
            access_list = list(all_persona_keys)
        if agent_key not in access_list:
            access_list.append(agent_key)
        result = await _flask(
            "PUT",
            f"/api/folders/{folder}/access",
            flask_url,
            json={"access": access_list},
        )
        _record_audit(
            agent_key,
            "update_folder_access",
            {"folder": folder},
            f"{len(access_list)} members",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)


def _register_gitlab_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 6 GitLab tools with optional repo access control."""
    repo_access = config.get("repo_access", {})

    def _can_access_repo(repo_name: str) -> bool:
        if not repo_access:
            return True
        if repo_name not in repo_access:
            return True
        return agent_key in repo_access[repo_name]

    @server.tool(
        name="list_repos",
        description="List all GitLab repositories. Returns name, description, and commit count for each.",
    )
    async def list_repos() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/gitlab/repos", flask_url)
        _record_audit(agent_key, "list_repos", {}, f"{len(result)} repos", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="create_repo",
        description="Create a new GitLab repository.",
    )
    async def create_repo(name: str, description: str) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/gitlab/repos",
            flask_url,
            json={
                "name": name,
                "description": description,
                "author": display_name,
            },
        )
        _record_audit(agent_key, "create_repo", {"name": name}, result.get("name", ""), (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="commit_files",
        description="Commit one or more files to a repository. Files is a list of {path, content} objects.",
    )
    async def commit_files(project: str, message: str, files: list[dict]) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/gitlab/repos/{project}/commit",
            flask_url,
            json={
                "message": message,
                "files": files,
                "author": display_name,
            },
        )
        _record_audit(
            agent_key,
            "commit_files",
            {"project": project, "message": message},
            result.get("commit_id", ""),
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="read_file",
        description="Read a file from a GitLab repository.",
    )
    async def read_file(project: str, path: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask("GET", f"/api/gitlab/repos/{project}/file", flask_url, params={"path": path})
        _record_audit(agent_key, "read_file", {"project": project, "path": path}, "read", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="list_repo_tree",
        description="List files and directories in a GitLab repository.",
    )
    async def list_repo_tree(project: str, path: str | None = None) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        params: dict[str, str] = {}
        if path:
            params["path"] = path
        result = await _flask("GET", f"/api/gitlab/repos/{project}/tree", flask_url, params=params)
        _record_audit(
            agent_key,
            "list_repo_tree",
            {"project": project, "path": path},
            f"{len(result)} entries",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="get_repo_log",
        description="Get commit history for a GitLab repository.",
    )
    async def get_repo_log(project: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask("GET", f"/api/gitlab/repos/{project}/log", flask_url)
        _record_audit(
            agent_key, "get_repo_log", {"project": project}, f"{len(result)} commits", (time.time() - t0) * 1000
        )
        return json.dumps(result)

    # --- Merge Requests ---

    @server.tool(
        name="create_merge_request",
        description="Create a merge request with a unified diff for code review.",
    )
    async def create_merge_request(
        project: str, title: str, description: str, diff: str, reviewers: list[str] | None = None
    ) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        payload = {"title": title, "description": description, "diff": diff, "author": display_name}
        if reviewers:
            payload["reviewers"] = reviewers
        result = await _flask("POST", f"/api/gitlab/repos/{project}/merge-requests", flask_url, json=payload)
        _record_audit(
            agent_key,
            "create_merge_request",
            {"project": project, "title": title},
            f"created {result.get('id', '?')}",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="list_merge_requests",
        description="List merge requests for a GitLab repository. Optional status filter: open, merged, closed.",
    )
    async def list_merge_requests(project: str, status: str | None = None) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        params = {}
        if status:
            params["status"] = status
        result = await _flask("GET", f"/api/gitlab/repos/{project}/merge-requests", flask_url, params=params)
        _record_audit(
            agent_key, "list_merge_requests", {"project": project}, f"{len(result)} MRs", (time.time() - t0) * 1000
        )
        return json.dumps(result)

    @server.tool(
        name="get_merge_request",
        description="Get a merge request's details including diff and review comments.",
    )
    async def get_merge_request(project: str, mr_id: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask(
            "GET", f"/api/gitlab/repos/{project}/merge-requests/{urllib.parse.quote(mr_id, safe='')}", flask_url
        )
        _record_audit(
            agent_key,
            "get_merge_request",
            {"project": project, "mr_id": mr_id},
            result.get("title", "?"),
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="comment_on_merge_request",
        description="Add a review comment to a merge request.",
    )
    async def comment_on_merge_request(project: str, mr_id: str, text: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/gitlab/repos/{project}/merge-requests/{urllib.parse.quote(mr_id, safe='')}/comment",
            flask_url,
            json={"text": text, "author": display_name},
        )
        _record_audit(
            agent_key,
            "comment_on_merge_request",
            {"project": project, "mr_id": mr_id},
            "commented",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="approve_merge_request",
        description="Approve a merge request. You cannot approve your own MR. At least one approval is required before merging.",
    )
    async def approve_merge_request(project: str, mr_id: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/gitlab/repos/{project}/merge-requests/{urllib.parse.quote(mr_id, safe='')}/approve",
            flask_url,
            json={"author": display_name},
        )
        _record_audit(
            agent_key,
            "approve_merge_request",
            {"project": project, "mr_id": mr_id},
            "approved",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="merge_merge_request",
        description="Merge an open merge request. Requires at least one non-author approval.",
    )
    async def merge_merge_request(project: str, mr_id: str) -> str:
        if not _can_access_repo(project):
            return f"Error: you don't have access to repository '{project}'."
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/gitlab/repos/{project}/merge-requests/{urllib.parse.quote(mr_id, safe='')}/merge",
            flask_url,
            json={"author": display_name},
        )
        _record_audit(
            agent_key,
            "merge_merge_request",
            {"project": project, "mr_id": mr_id},
            "merged",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)


def _register_ticket_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 5 ticket tools."""

    @server.tool(
        name="get_ticket",
        description="Get a single ticket's full details including description, comments, and history by ticket ID.",
    )
    async def get_ticket(ticket_id: str) -> str:
        t0 = time.time()
        result = await _flask("GET", f"/api/tickets/{ticket_id}", flask_url)
        _record_audit(agent_key, "get_ticket", {"ticket_id": ticket_id}, "read", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="create_ticket",
        description="Create a new ticket with title, description, priority (low/medium/high/critical), and assignee persona key.",
    )
    async def create_ticket(
        title: str,
        description: str,
        priority: str,
        assignee: str,
        blocked_by: list[str] | None = None,
    ) -> str:
        t0 = time.time()
        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "priority": priority,
            "assignee": assignee,
            "author": display_name,
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by
        result = await _flask("POST", "/api/tickets", flask_url, json=payload)
        _record_audit(
            agent_key,
            "create_ticket",
            {"title": title, "priority": priority},
            result.get("id", ""),
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="update_ticket",
        description="Update a ticket's status or assignee. Status values: open, in-progress, resolved, closed.",
    )
    async def update_ticket(
        ticket_id: str,
        status: str | None = None,
        assignee: str | None = None,
    ) -> str:
        t0 = time.time()
        payload: dict[str, Any] = {"author": display_name}
        if status is not None:
            payload["status"] = status
        if assignee is not None:
            payload["assignee"] = assignee
        result = await _flask("PUT", f"/api/tickets/{ticket_id}", flask_url, json=payload)
        _record_audit(
            agent_key, "update_ticket", {"ticket_id": ticket_id, "status": status}, "updated", (time.time() - t0) * 1000
        )
        return json.dumps(result)

    @server.tool(
        name="comment_on_ticket",
        description="Add a comment to an existing ticket.",
    )
    async def comment_on_ticket(ticket_id: str, text: str) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/tickets/{ticket_id}/comment",
            flask_url,
            json={
                "text": text,
                "author": display_name,
            },
        )
        _record_audit(agent_key, "comment_on_ticket", {"ticket_id": ticket_id}, "commented", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="list_tickets",
        description="List tickets, optionally filtered by status or assignee.",
    )
    async def list_tickets(status: str | None = None, assignee: str | None = None) -> str:
        t0 = time.time()
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if assignee:
            params["assignee"] = assignee
        result = await _flask("GET", "/api/tickets", flask_url, params=params)
        _record_audit(
            agent_key,
            "list_tickets",
            {"status": status, "assignee": assignee},
            f"{len(result)} tickets",
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)


def _register_memo_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 5 memo tools."""

    @server.tool(
        name="list_memos",
        description="List all memo discussion threads. Returns thread ID, title, creator, and post count for each.",
    )
    async def list_memos() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/memos/threads", flask_url)
        _record_audit(agent_key, "list_memos", {}, f"{len(result)} threads", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="get_memo_thread",
        description="Get a memo thread's details and all its posts/replies.",
    )
    async def get_memo_thread(thread_id: str) -> str:
        t0 = time.time()
        thread = await _flask("GET", f"/api/memos/threads/{thread_id}", flask_url)
        posts = await _flask("GET", f"/api/memos/threads/{thread_id}/posts", flask_url)
        thread["posts"] = posts
        _record_audit(
            agent_key, "get_memo_thread", {"thread_id": thread_id}, f"{len(posts)} posts", (time.time() - t0) * 1000
        )
        return json.dumps(thread)

    @server.tool(
        name="create_memo",
        description="Create a new threaded discussion memo (like a mailing list thread).",
    )
    async def create_memo(title: str, description: str = "") -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/memos/threads",
            flask_url,
            json={
                "title": title,
                "creator": display_name,
                "description": description,
            },
        )
        _record_audit(agent_key, "create_memo", {"title": title}, result.get("id", ""), (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="reply_to_memo",
        description="Post a reply to an existing memo thread.",
    )
    async def reply_to_memo(thread_id: str, text: str) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/memos/threads/{thread_id}/posts",
            flask_url,
            json={
                "text": text,
                "author": display_name,
            },
        )
        _record_audit(agent_key, "reply_to_memo", {"thread_id": thread_id}, "replied", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="delete_memo",
        description="Delete a memo thread and all its posts.",
    )
    async def delete_memo(thread_id: str) -> str:
        t0 = time.time()
        result = await _flask("DELETE", f"/api/memos/threads/{thread_id}", flask_url)
        _record_audit(agent_key, "delete_memo", {"thread_id": thread_id}, "deleted", (time.time() - t0) * 1000)
        return json.dumps(result)


def _register_blog_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 7 blog tools."""

    @server.tool(
        name="list_blog_posts",
        description="List all blog posts. Returns title, slug, author, status, tags, and reply count for each post.",
    )
    async def list_blog_posts() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/blog/posts", flask_url)
        _record_audit(agent_key, "list_blog_posts", {}, f"{len(result)} posts", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="read_blog_post",
        description="Read a blog post's full content and all its replies by slug.",
    )
    async def read_blog_post(post_slug: str) -> str:
        t0 = time.time()
        post = await _flask("GET", f"/api/blog/posts/{post_slug}", flask_url)
        replies = await _flask("GET", f"/api/blog/posts/{post_slug}/replies", flask_url)
        post["replies"] = replies
        _record_audit(
            agent_key, "read_blog_post", {"post_slug": post_slug}, f"{len(replies)} replies", (time.time() - t0) * 1000
        )
        return json.dumps(post)

    @server.tool(
        name="create_blog_post",
        description="Create a new blog post. Set is_external=true for customer-facing posts.",
    )
    async def create_blog_post(
        title: str,
        body: str,
        is_external: bool = False,
        tags: list[str] | None = None,
    ) -> str:
        t0 = time.time()
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "author": display_name,
            "is_external": is_external,
        }
        if tags:
            payload["tags"] = tags
        result = await _flask("POST", "/api/blog/posts", flask_url, json=payload)
        _record_audit(
            agent_key, "create_blog_post", {"title": title}, result.get("slug", ""), (time.time() - t0) * 1000
        )
        return json.dumps(result)

    @server.tool(
        name="reply_to_blog",
        description="Post a reply/comment on a blog post.",
    )
    async def reply_to_blog(post_slug: str, text: str) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            f"/api/blog/posts/{post_slug}/replies",
            flask_url,
            json={
                "text": text,
                "author": display_name,
            },
        )
        _record_audit(agent_key, "reply_to_blog", {"post_slug": post_slug}, "replied", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="update_blog_post",
        description="Update an existing blog post. You can change title, body, status (draft/published/unpublished), is_external, or tags.",
    )
    async def update_blog_post(
        post_slug: str,
        title: str | None = None,
        body: str | None = None,
        status: str | None = None,
        is_external: bool | None = None,
        tags: list[str] | None = None,
    ) -> str:
        t0 = time.time()
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if status is not None:
            payload["status"] = status
        if is_external is not None:
            payload["is_external"] = is_external
        if tags is not None:
            payload["tags"] = tags
        if not payload:
            return "Error: no fields to update. Provide at least one of: title, body, status, is_external, tags."
        result = await _flask("PUT", f"/api/blog/posts/{post_slug}", flask_url, json=payload)
        _record_audit(agent_key, "update_blog_post", {"post_slug": post_slug}, "updated", (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="delete_blog_post",
        description="Delete a blog post and all its replies.",
    )
    async def delete_blog_post(post_slug: str) -> str:
        t0 = time.time()
        result = await _flask("DELETE", f"/api/blog/posts/{post_slug}", flask_url)
        _record_audit(agent_key, "delete_blog_post", {"post_slug": post_slug}, "deleted", (time.time() - t0) * 1000)
        return json.dumps(result)


def _register_email_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 2 email tools."""

    @server.tool(
        name="send_email",
        description="Send a company-wide email (visible to all team members).",
    )
    async def send_email(subject: str, body: str) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/emails",
            flask_url,
            json={
                "sender": display_name,
                "subject": subject,
                "body": body,
            },
        )
        _record_audit(
            agent_key, "send_email", {"subject": subject}, str(result.get("id", "")), (time.time() - t0) * 1000
        )
        return json.dumps(result)

    @server.tool(
        name="get_emails",
        description="Get all company-wide emails.",
    )
    async def get_emails() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/emails", flask_url)
        _record_audit(agent_key, "get_emails", {}, f"{len(result)} emails", (time.time() - t0) * 1000)
        return json.dumps(result)


def _register_jobs_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 3 jobs/runner tools."""

    @server.tool(
        name="run_from_repo",
        description="Execute a Python script from a GitLab repo. Commits the full repo to a sandboxed container. "
        "Returns immediately with a run_id — poll get_run() for results.",
    )
    async def run_from_repo(
        repo: str,
        path: str,
        ref: str = "main",
        language: str = "python",
        network: bool = False,
    ) -> str:
        t0 = time.time()
        result = await _flask(
            "POST",
            "/api/jobs/runs",
            flask_url,
            json={
                "repo": repo,
                "path": path,
                "ref": ref,
                "language": language,
                "network": network,
                "agent_id": agent_key,
            },
        )
        _record_audit(
            agent_key,
            "run_from_repo",
            {"repo": repo, "path": path, "ref": ref},
            result.get("run_id", ""),
            (time.time() - t0) * 1000,
        )
        return json.dumps(result)

    @server.tool(
        name="get_run",
        description="Get the status and results of a job run by its run_id.",
    )
    async def get_run(run_id: str) -> str:
        t0 = time.time()
        result = await _flask("GET", f"/api/jobs/runs/{run_id}", flask_url)
        _record_audit(agent_key, "get_run", {"run_id": run_id}, result.get("status", ""), (time.time() - t0) * 1000)
        return json.dumps(result)

    @server.tool(
        name="list_runs",
        description="List recent job runs.",
    )
    async def list_runs() -> str:
        t0 = time.time()
        result = await _flask("GET", "/api/jobs/runs", flask_url)
        _record_audit(agent_key, "list_runs", {}, f"{len(result)} runs", (time.time() - t0) * 1000)
        return json.dumps(result)


def _register_meta_tools(
    server: FastMCP,
    agent_key: str,
    display_name: str,
    flask_url: str,
    config: dict,
):
    """Register 6 meta/utility tools."""
    my_channels = set(config.get("memberships", {}).get(agent_key, []))
    my_channels.add(f"#director-{agent_key}")
    characters = config.get("characters", {})

    @server.tool(
        name="whoami",
        description="Returns your identity — persona key, display name, team role, and accessible resources.",
    )
    async def whoami() -> str:
        my_folders = sorted(
            folder for folder, members in config.get("folder_access", {}).items() if agent_key in members
        )
        char_info = characters.get(agent_key, {})
        result = {
            "persona_key": agent_key,
            "display_name": display_name,
            "team_description": char_info.get("team_description", ""),
            "channels": sorted(my_channels),
            "folders": my_folders,
        }
        _record_audit(agent_key, "whoami", {}, agent_key, 0)
        return json.dumps(result)

    @server.tool(
        name="who_is",
        description="Look up another team member by persona key. Returns their display name and role.",
    )
    async def who_is(persona_key: str) -> str:
        char_info = characters.get(persona_key)
        if not char_info:
            known = sorted(characters.keys())
            return json.dumps({"error": f"Unknown persona '{persona_key}'. Known: {known}"})
        result = {
            "persona_key": persona_key,
            "display_name": char_info.get("display_name", persona_key),
            "team_description": char_info.get("team_description", ""),
        }
        _record_audit(agent_key, "who_is", {"persona_key": persona_key}, persona_key, 0)
        return json.dumps(result)

    @server.tool(
        name="get_my_channels",
        description="List all channels you are currently a member of.",
    )
    async def get_my_channels() -> str:
        _record_audit(agent_key, "get_my_channels", {}, f"{len(my_channels)} channels", 0)
        return json.dumps({"channels": sorted(my_channels)})

    @server.tool(
        name="get_my_tickets",
        description="List tickets assigned to you.",
    )
    async def get_my_tickets() -> str:
        t0 = time.time()
        # Search by display name (how tickets store assignee) and also by key
        result_by_name = await _flask("GET", "/api/tickets", flask_url, params={"assignee": display_name})
        result_by_key = await _flask("GET", "/api/tickets", flask_url, params={"assignee": agent_key})
        # Merge, deduplicate by ticket ID
        seen: set[str] = set()
        merged: list[dict] = []
        for t in result_by_name + result_by_key:
            tid = t.get("id", "")
            if tid not in seen:
                seen.add(tid)
                merged.append(t)
        _record_audit(agent_key, "get_my_tickets", {}, f"{len(merged)} tickets", (time.time() - t0) * 1000)
        return json.dumps(merged)

    @server.tool(
        name="get_recent_activity",
        description="Get a summary of recent activity across channels, tickets, and documents.",
    )
    async def get_recent_activity(since_minutes: int = 30) -> str:
        t0 = time.time()
        cutoff = time.time() - (since_minutes * 60)

        # Fetch recent messages from my channels
        all_msgs = await _flask(
            "GET",
            "/api/messages",
            flask_url,
            params={
                "channels": ",".join(my_channels),
            },
        )
        recent_msgs = [m for m in all_msgs if m.get("timestamp", 0) > cutoff]

        # Fetch recent tickets
        all_tickets = await _flask("GET", "/api/tickets", flask_url)
        recent_tickets = [t for t in all_tickets if t.get("created", 0) > cutoff or t.get("updated", 0) > cutoff]

        # Fetch recent memos
        all_memos = await _flask("GET", "/api/memos/threads", flask_url, params={"include_posts": "1"})

        summary = {
            "recent_messages": len(recent_msgs),
            "messages_by_channel": {},
            "recent_tickets": len(recent_tickets),
            "ticket_ids": [t.get("id") for t in recent_tickets[:10]],
            "active_memo_threads": len(all_memos),
        }
        for m in recent_msgs:
            ch = m.get("channel", "?")
            summary["messages_by_channel"][ch] = summary["messages_by_channel"].get(ch, 0) + 1

        _record_audit(
            agent_key,
            "get_recent_activity",
            {"since_minutes": since_minutes},
            f"{len(recent_msgs)} msgs",
            (time.time() - t0) * 1000,
        )
        return json.dumps(summary)

    @server.tool(
        name="signal_done",
        description="Signal that you have finished processing your current turn. Call this with a brief summary when you are done.",
    )
    async def signal_done(summary: str = "") -> str:
        global _done_event_counter
        logger.info(f"Agent {agent_key} signaled done: {summary}")
        _record_audit(agent_key, "signal_done", {"summary": summary}, "ack", 0)
        # Append to done event queue for tier advancement tracking
        with _done_lock:
            _done_event_counter += 1
            _done_events.append(
                {
                    "id": _done_event_counter,
                    "agent_key": agent_key,
                    "summary": summary,
                    "timestamp": time.time(),
                }
            )
        # Forward summary to Flask thoughts API so it appears in UI
        if summary:
            try:
                await _flask(
                    "POST",
                    f"/api/npcs/{agent_key}/thoughts",
                    flask_url,
                    json={"thinking": "", "response": f"[Done] {summary}"},
                )
            except Exception:
                pass
        return json.dumps({"status": "acknowledged", "agent_key": agent_key})


# ---------------------------------------------------------------------------
# Per-agent MCP instance factory
# ---------------------------------------------------------------------------


def create_agent_mcp(
    agent_key: str,
    flask_url: str,
    config: dict,
) -> FastMCP:
    """Create a FastMCP instance for a single agent with all 32 tools."""
    display_name = config["characters"][agent_key]["display_name"]
    server = FastMCP(
        name=f"sim-agent-{agent_key}",
        instructions=f"You are {display_name}. Use the available tools to interact with the simulated workplace.",
        # Disable DNS rebinding protection — containers connect via gateway IP
        # or host.containers.internal, not 127.0.0.1
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    reg_args = (server, agent_key, display_name, flask_url, config)
    _register_communication_tools(*reg_args)
    _register_document_tools(*reg_args)
    _register_gitlab_tools(*reg_args)
    _register_ticket_tools(*reg_args)
    _register_memo_tools(*reg_args)
    _register_blog_tools(*reg_args)
    _register_email_tools(*reg_args)
    _register_jobs_tools(*reg_args)
    _register_meta_tools(*reg_args)

    return server


# ---------------------------------------------------------------------------
# Telemetry + health + audit HTTP endpoints
# ---------------------------------------------------------------------------


async def _health(request: Request) -> JSONResponse:
    scenario = getattr(request.app.state, "scenario_name", None)
    agent_keys = getattr(request.app.state, "agent_keys", [])
    return JSONResponse(
        {
            "status": "ok",
            "scenario": scenario,
            "agents": agent_keys,
        }
    )


async def _forward_activity(flask_url: str, agent_key: str, event_type: str, detail: str = "") -> None:
    """Forward agent activity to Flask for heartbeat tracking."""
    try:
        if _http_client is not None:
            await _http_client.post(
                f"{flask_url}/api/npcs/{agent_key}/activity",
                json={"event_type": event_type, "detail": detail},
                timeout=3,
            )
    except Exception:
        pass


async def _telemetry_model_end(request: Request) -> JSONResponse:
    """Receive model-end hook event from Claude Code container."""
    agent_key = request.headers.get("X-Agent-Key", "unknown")
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    stats = _telemetry.setdefault(
        agent_key,
        {
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost_usd": 0.0,
            "last_model": "",
        },
    )
    stats["api_calls"] += 1
    stats["last_model"] = data.get("model", "")

    # Extract token usage if present
    usage = data.get("usage", {})
    stats["input_tokens"] += usage.get("input_tokens", 0)
    stats["output_tokens"] += usage.get("output_tokens", 0)
    cost = data.get("total_cost_usd")
    if cost and cost != "None":
        try:
            stats["total_cost_usd"] += float(cost)
        except (ValueError, TypeError):
            pass

    # Forward thinking text to Flask for UI display
    thinking = data.get("thinking", "")
    if thinking:
        try:
            assert _http_client is not None
            await _http_client.post(
                f"{request.app.state.flask_url}/api/npcs/{agent_key}/thoughts",
                json={"thinking": thinking, "response": ""},
                timeout=5,
            )
        except Exception:
            pass

    # Forward activity ping to Flask for heartbeat tracking
    await _forward_activity(request.app.state.flask_url, agent_key, "model_end", data.get("model", ""))

    return JSONResponse({"ok": True})


async def _telemetry_tool_start(request: Request) -> JSONResponse:
    """Receive tool-start hook event from Claude Code container."""
    agent_key = request.headers.get("X-Agent-Key", "unknown")
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    tool_name = data.get("tool_name", "unknown")
    stats = _telemetry.setdefault(
        agent_key,
        {
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost_usd": 0.0,
            "last_model": "",
            "tool_calls": {},
        },
    )
    tool_calls = stats.setdefault("tool_calls", {})
    tool_calls[tool_name] = tool_calls.get(tool_name, 0) + 1

    # Forward activity ping to Flask for heartbeat tracking
    await _forward_activity(request.app.state.flask_url, agent_key, "tool_start", tool_name)

    return JSONResponse({"ok": True})


async def _get_telemetry(request: Request) -> JSONResponse:
    """Return aggregated telemetry data for all agents."""
    return JSONResponse(_telemetry)


async def _get_audit(request: Request) -> JSONResponse:
    """Return recent audit log entries."""
    limit = int(request.query_params.get("limit", "100"))
    entries = list(_audit_log)[-limit:]
    return JSONResponse(entries)


async def _done_events_endpoint(request: Request) -> JSONResponse:
    """GET: return done events with id > since_id. DELETE: clear all done events."""
    global _done_event_counter
    if request.method == "DELETE":
        with _done_lock:
            _done_events.clear()
            _done_event_counter = 0
        return JSONResponse({"cleared": True})
    # GET
    since_id = int(request.query_params.get("since_id", "0"))
    with _done_lock:
        events = [e for e in _done_events if e["id"] > since_id]
    return JSONResponse(events)


async def _done_events_cursor_endpoint(request: Request) -> JSONResponse:
    """GET: return the current done-event high-water mark without event data."""
    with _done_lock:
        return JSONResponse({"cursor": _done_event_counter})


async def _load_scenario_endpoint(request: Request) -> JSONResponse:
    """POST /api/load-scenario — dynamically load (or reload) a scenario.

    Accepts JSON: {"scenario": "tech-startup"}
    Mounts per-agent MCP sub-apps on the parent Starlette app.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    scenario_name = data.get("scenario")
    if not scenario_name:
        return JSONResponse({"error": "missing 'scenario' field"}, status_code=400)

    flask_url = request.app.state.flask_url

    try:
        config = _load_scenario_config(scenario_name)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)

    # Remove any previously mounted agent routes
    request.app.routes[:] = [
        r for r in request.app.routes if not (isinstance(r, Mount) and r.path.startswith("/agents/"))
    ]

    # Mount new per-agent MCP sub-apps
    agent_keys = []
    for agent_key in config.get("characters", {}):
        agent_mcp = create_agent_mcp(agent_key, flask_url, config)
        sse_app = agent_mcp.sse_app()
        request.app.routes.insert(0, Mount(f"/agents/{agent_key}", app=sse_app))
        agent_keys.append(agent_key)
        logger.info(f"Mounted MCP endpoint: /agents/{agent_key}/sse")

    request.app.state.scenario_name = scenario_name
    request.app.state.agent_keys = agent_keys

    logger.info(f"Loaded scenario '{scenario_name}' with {len(agent_keys)} agents: {agent_keys}")
    return JSONResponse(
        {
            "status": "ok",
            "scenario": scenario_name,
            "agents": agent_keys,
        }
    )


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


def build_app(scenario_name: str | None, flask_url: str) -> Starlette:
    """Build the parent Starlette app with per-agent MCP mounts.

    If scenario_name is None, the server starts with only management endpoints.
    Use POST /api/load-scenario to load a scenario later.

    Returns a Starlette app ready for uvicorn.
    """

    @asynccontextmanager
    async def lifespan(app: Starlette):
        global _http_client
        _http_client = httpx.AsyncClient(timeout=30)
        app.state.flask_url = flask_url
        app.state.scenario_name = scenario_name
        app.state.agent_keys = []
        logger.info(f"MCP server starting — scenario={scenario_name or '(none)'}, flask={flask_url}")
        yield
        await _http_client.aclose()
        _http_client = None
        logger.info("MCP server shutting down")

    # Build per-agent MCP sub-apps (if scenario provided at startup)
    routes: list[Mount | Route] = []
    agent_keys = []
    if scenario_name:
        config = _load_scenario_config(scenario_name)
        for agent_key in config.get("characters", {}):
            agent_mcp = create_agent_mcp(agent_key, flask_url, config)
            sse_app = agent_mcp.sse_app()
            routes.append(Mount(f"/agents/{agent_key}", app=sse_app))
            agent_keys.append(agent_key)
            logger.info(f"Mounted MCP endpoint: /agents/{agent_key}/sse")

    # Add management endpoints
    routes.extend(
        [
            Route("/health", _health, methods=["GET"]),
            Route("/api/load-scenario", _load_scenario_endpoint, methods=["POST"]),
            Route("/api/telemetry/model-end", _telemetry_model_end, methods=["POST"]),
            Route("/api/telemetry/tool-start", _telemetry_tool_start, methods=["POST"]),
            Route("/api/telemetry", _get_telemetry, methods=["GET"]),
            Route("/api/audit", _get_audit, methods=["GET"]),
            Route("/api/agents/done-events", _done_events_endpoint, methods=["GET", "DELETE"]),
            Route("/api/agents/done-events/cursor", _done_events_cursor_endpoint, methods=["GET"]),
        ]
    )

    app = Starlette(routes=routes, lifespan=lifespan)

    if scenario_name:
        logger.info(f"MCP server configured with {len(agent_keys)} agents: {agent_keys}")
    else:
        logger.info("MCP server started without scenario — POST /api/load-scenario to configure")
    return app
