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

    # -- Document API --

    def list_docs(self) -> list[dict]:
        """Return metadata for all shared documents."""
        resp = requests.get(f"{self.base_url}/api/docs", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def create_doc(self, title: str, content: str, author: str = "unknown") -> dict:
        """Create a new shared document. Returns metadata dict."""
        resp = requests.post(
            f"{self.base_url}/api/docs",
            json={"title": title, "content": content, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_doc(self, slug: str) -> dict:
        """Get a document's metadata and full content."""
        resp = requests.get(f"{self.base_url}/api/docs/{slug}", timeout=10)
        resp.raise_for_status()
        return resp.json()

    def update_doc(self, slug: str, content: str, author: str = "unknown") -> dict:
        """Replace a document's content entirely."""
        resp = requests.put(
            f"{self.base_url}/api/docs/{slug}",
            json={"content": content, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def append_doc(self, slug: str, content: str, author: str = "unknown") -> dict:
        """Append content to an existing document."""
        resp = requests.post(
            f"{self.base_url}/api/docs/{slug}/append",
            json={"content": content, "author": author},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def search_docs(self, query: str) -> list[dict]:
        """Search document titles and contents. Returns list of matching metadata."""
        resp = requests.get(
            f"{self.base_url}/api/docs/search",
            params={"q": query},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def delete_doc(self, slug: str) -> dict:
        """Delete a shared document."""
        resp = requests.delete(f"{self.base_url}/api/docs/{slug}", timeout=10)
        resp.raise_for_status()
        return resp.json()
