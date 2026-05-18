"""MCP client and tool-format helpers for the Models.Corp agent harness."""

from __future__ import annotations

import json
import re
import uuid

import httpx


class MCPClient:
    """Minimal MCP client using JSON-RPC over HTTP POST."""

    def __init__(self, url: str, timeout: float = 30.0):
        self._url = url
        self._timeout = timeout
        self._session_id: str | None = None
        self._req_id = 0
        self._client = httpx.Client(timeout=timeout, verify=False)

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _post(self, method: str, params: dict | None = None) -> dict:
        body: dict = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
        }
        if params:
            body["params"] = params

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        resp = self._client.post(self._url, json=body, headers=headers)
        resp.raise_for_status()

        if not self._session_id:
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session_id = sid

        return resp.json()

    def initialize(self) -> dict:
        result = self._post(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "modelscorp-agent", "version": "1.0"},
            },
        )
        self._post("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        result = self._post("tools/list")
        return result.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._post("tools/call", {"name": name, "arguments": arguments})
        tool_result = result.get("result", {})
        content = tool_result.get("content", [])
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts) if parts else json.dumps(tool_result)

    def close(self):
        self._client.close()


def mcp_tools_to_openai(mcp_tools: list[dict]) -> list[dict]:
    """Convert MCP tool definitions to OpenAI function-calling format."""
    openai_tools = []
    for tool in mcp_tools:
        schema = tool.get("inputSchema", {"type": "object", "properties": {}})
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return openai_tools


_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def parse_text_tool_calls(text: str) -> list[dict]:
    """Extract tool calls from <tool_call> tags in model text output."""
    calls = []
    for match in _TOOL_CALL_RE.finditer(text):
        try:
            parsed = json.loads(match.group(1))
            raw_args = parsed.get("arguments", {})
            if isinstance(raw_args, str):
                raw_args = json.loads(raw_args)
            calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": parsed.get("name", ""),
                    "arguments": json.dumps(raw_args),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return calls
