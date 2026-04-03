"""HTTP client for the chat server API."""

import requests


class ChatClient:
    """Thin wrapper around the chat server REST API."""

    def __init__(self, base_url: str = "http://127.0.0.1:5000"):
        self.base_url = base_url.rstrip("/")

    def health_check(self) -> bool:
        """Return True if the server is reachable."""
        try:
            resp = requests.get(f"{self.base_url}/api/messages", timeout=5)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    # -- Channel API --

    def get_channels(self) -> list[dict]:
        """Return list of channel dicts with name, description, is_external, members."""
        resp = requests.get(f"{self.base_url}/api/channels", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def join_channel(self, channel: str, persona: str) -> dict:
        """Add a persona to a channel. Returns {channel, members}."""
        # URL-encode the '#'
        encoded = channel.lstrip("#")
        resp = requests.post(
            f"{self.base_url}/api/channels/{encoded}/join",
            json={"persona": persona},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def leave_channel(self, channel: str, persona: str) -> dict:
        """Remove a persona from a channel. Returns {channel, members}."""
        encoded = channel.lstrip("#")
        resp = requests.post(
            f"{self.base_url}/api/channels/{encoded}/leave",
            json={"persona": persona},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Messages --

    def get_messages(
        self,
        since: int = 0,
        channels: list[str] | None = None,
    ) -> list[dict]:
        """Fetch messages, optionally only those with id > since.

        If channels is specified, only messages in those channels are returned.
        """
        params = {}
        if since:
            params["since"] = since
        if channels:
            # Strip '#' for the query param and join with commas
            params["channels"] = ",".join(channels)
        resp = requests.get(f"{self.base_url}/api/messages", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def post_message(self, sender: str, content: str, channel: str = "#general") -> dict:
        """Post a new message and return the created message dict."""
        resp = requests.post(
            f"{self.base_url}/api/messages",
            json={"sender": sender, "content": content, "channel": channel},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Folder API --

    def get_folders(self) -> list[dict]:
        """Return list of folder dicts with name, type, description, access."""
        resp = requests.get(f"{self.base_url}/api/folders", timeout=10)
        resp.raise_for_status()
        return resp.json()

    # -- Document API --

    def list_docs(self, folder: str | None = None) -> list[dict]:
        """Return metadata for documents, optionally filtered by folder."""
        params = {}
        if folder:
            params["folder"] = folder
        resp = requests.get(f"{self.base_url}/api/docs", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_doc(self, title: str, content: str, author: str = "unknown", folder: str = "shared") -> dict:
        """Create a new document in the specified folder. Returns metadata dict."""
        resp = requests.post(
            f"{self.base_url}/api/docs",
            json={"title": title, "content": content, "author": author, "folder": folder},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_doc(self, folder: str, slug: str) -> dict:
        """Get a document's metadata and full content."""
        resp = requests.get(f"{self.base_url}/api/docs/{folder}/{slug}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def update_doc(self, folder: str, slug: str, content: str, author: str = "unknown") -> dict:
        """Replace a document's content entirely."""
        resp = requests.put(
            f"{self.base_url}/api/docs/{folder}/{slug}",
            json={"content": content, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def append_doc(self, folder: str, slug: str, content: str, author: str = "unknown") -> dict:
        """Append content to an existing document."""
        resp = requests.post(
            f"{self.base_url}/api/docs/{folder}/{slug}/append",
            json={"content": content, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def search_docs(self, query: str, folders: list[str] | None = None) -> list[dict]:
        """Search document titles and contents. Returns list of matching metadata."""
        params = {"q": query}
        if folders:
            params["folders"] = ",".join(folders)
        resp = requests.get(
            f"{self.base_url}/api/docs/search",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_doc(self, folder: str, slug: str) -> dict:
        """Delete a document from the specified folder."""
        resp = requests.delete(f"{self.base_url}/api/docs/{folder}/{slug}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    # -- GitLab API --

    def list_repos(self) -> list[dict]:
        """Return list of GitLab repository metadata dicts."""
        resp = requests.get(f"{self.base_url}/api/gitlab/repos", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_repo(self, name: str, description: str, author: str) -> dict:
        """Create a new GitLab repository."""
        resp = requests.post(
            f"{self.base_url}/api/gitlab/repos",
            json={"name": name, "description": description, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_tree(self, project: str, path: str | None = None) -> list[dict]:
        """Get file tree for a repository, optionally scoped to a subdirectory."""
        params = {}
        if path:
            params["path"] = path
        resp = requests.get(
            f"{self.base_url}/api/gitlab/repos/{project}/tree",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_file(self, project: str, path: str) -> dict:
        """Read a file from a repository."""
        resp = requests.get(
            f"{self.base_url}/api/gitlab/repos/{project}/file",
            params={"path": path},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def commit_files(self, project: str, message: str, files: list[dict], author: str) -> dict:
        """Commit files to a repository."""
        resp = requests.post(
            f"{self.base_url}/api/gitlab/repos/{project}/commit",
            json={"message": message, "files": files, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_log(self, project: str) -> list[dict]:
        """Get commit history for a repository (newest first)."""
        resp = requests.get(
            f"{self.base_url}/api/gitlab/repos/{project}/log",
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Tickets API --

    def list_tickets(
        self,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[dict]:
        """Return list of tickets, optionally filtered by status or assignee."""
        params = {}
        if status:
            params["status"] = status
        if assignee:
            params["assignee"] = assignee
        resp = requests.get(f"{self.base_url}/api/tickets", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_ticket(
        self,
        title: str,
        description: str,
        priority: str,
        assignee: str,
        author: str,
        blocked_by: list[str] | None = None,
    ) -> dict:
        """Create a new ticket. Returns ticket dict."""
        payload = {
            "title": title,
            "description": description,
            "priority": priority,
            "assignee": assignee,
            "author": author,
        }
        if blocked_by:
            payload["blocked_by"] = blocked_by
        resp = requests.post(
            f"{self.base_url}/api/tickets",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_ticket(self, ticket_id: str) -> dict:
        """Get a single ticket with comments."""
        resp = requests.get(f"{self.base_url}/api/tickets/{ticket_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def update_ticket(
        self,
        ticket_id: str,
        author: str,
        status: str | None = None,
        assignee: str | None = None,
    ) -> dict:
        """Update ticket fields (status, assignee)."""
        payload = {"author": author}
        if status is not None:
            payload["status"] = status
        if assignee is not None:
            payload["assignee"] = assignee
        resp = requests.put(
            f"{self.base_url}/api/tickets/{ticket_id}",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def comment_ticket(self, ticket_id: str, text: str, author: str) -> dict:
        """Add a comment to a ticket."""
        resp = requests.post(
            f"{self.base_url}/api/tickets/{ticket_id}/comment",
            json={"text": text, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def add_dependency(self, ticket_id: str, blocked_by: str) -> dict:
        """Add a dependency to a ticket."""
        resp = requests.post(
            f"{self.base_url}/api/tickets/{ticket_id}/depends",
            json={"blocked_by": blocked_by},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Memo-list API --

    def get_memo_threads(self, include_posts: bool = False) -> list[dict]:
        """Return list of memo-list discussion threads.

        If include_posts=True, each thread includes 'recent_posts' with the last 2 posts.
        """
        try:
            params = {}
            if include_posts:
                params["include_posts"] = "1"
            resp = requests.get(f"{self.base_url}/api/memos/threads", params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    def get_memo_thread(self, thread_id: str) -> dict | None:
        """Get a single thread with its posts."""
        resp = requests.get(f"{self.base_url}/api/memos/threads/{thread_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_memo_thread(self, title: str, creator: str, description: str = "") -> dict:
        """Create a new discussion thread."""
        resp = requests.post(
            f"{self.base_url}/api/memos/threads",
            json={"title": title, "creator": creator, "description": description},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def post_memo(self, thread_id: str, text: str, author: str) -> dict:
        """Post a reply to a discussion thread."""
        resp = requests.post(
            f"{self.base_url}/api/memos/threads/{thread_id}/posts",
            json={"text": text, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Agent thoughts --

    def post_thoughts(self, persona_key: str, thinking: str, response: str):
        """Post agent's internal thoughts to the server."""
        try:
            requests.post(
                f"{self.base_url}/api/npcs/{persona_key}/thoughts",
                json={"thinking": thinking, "response": response},
                timeout=5,
            )
        except Exception:
            pass

    # -- NPC status --

    def get_npcs(self) -> list[dict]:
        """Return list of NPC dicts with online/offline status."""
        try:
            resp = requests.get(f"{self.base_url}/api/npcs", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return []

    # -- Typing indicators --

    def set_typing(self, sender: str, channel: str, active: bool = True):
        """Send typing indicator for an agent in a channel."""
        try:
            requests.post(
                f"{self.base_url}/api/typing",
                json={"sender": sender, "channel": channel, "active": active},
                timeout=5,
            )
        except Exception:
            pass

    # -- Orchestrator control --

    def send_heartbeat(self, state: str, scenario: str, agents: dict,
                       message: str = "", check_commands: bool = True) -> dict:
        """Send orchestrator heartbeat. Returns any pending command if check_commands=True."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/orchestrator/heartbeat",
                json={"state": state, "scenario": scenario,
                      "agents": agents, "message": message,
                      "check_commands": check_commands},
                timeout=5,
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("action"):
                print(f"  [heartbeat] consumed command: {result}")
            return result
        except Exception:
            return {"action": None}
