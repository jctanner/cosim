#!/usr/bin/env python3
"""Models.Corp agent harness — agentic tool-calling loop using OpenAI-compatible API.

Runs inside a podman container. Connects to an MCP server for tool discovery
and execution, calls a Models.Corp LLM endpoint via the OpenAI SDK, and loops
until the model stops requesting tools or max turns is reached.

Output: JSONL to stdout (parsed by ModelscorpBackend).
Logs: stderr.
"""

import argparse
import json
import re
import sys
import time
import uuid

import httpx
from openai import DefaultHttpxClient, OpenAI


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


# ── MCP client (raw JSON-RPC over Streamable HTTP) ─────────────────────


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

        headers: dict[str, str] = {"Content-Type": "application/json"}
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


# ── Tool format conversion ──────────────────────────────────────────────


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


# ── Text-based tool call parsing (for models like Granite) ──────────────

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
            calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": parsed.get("name", ""),
                    "arguments": json.dumps(parsed.get("arguments", {})),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    return calls


# ── Config loading ──────────────────────────────────────────────────────


def load_config(config_path: str) -> dict:
    with open(config_path) as f:
        return json.load(f)


def build_base_url(config: dict, model: str) -> str:
    """Build the OpenAI-compatible base URL for a given model."""
    endpoint_base = config["endpoint_base"]
    model_cfg = config.get("models", {}).get(model, {})
    endpoint_type = model_cfg.get("endpoint_type", config.get("endpoint_type", "staging"))
    base_path = model_cfg.get("base_path", "")
    return f"https://{model}--{endpoint_type}.{endpoint_base}:443{base_path}/v1"


def get_api_key(config: dict, model: str) -> str:
    model_cfg = config.get("models", {}).get(model, {})
    key = model_cfg.get("key", "")
    if not key:
        raise ValueError(f"No API key configured for model '{model}' in config")
    return key


def get_model_id(config: dict, model: str) -> str:
    """Get the model ID to send in API requests (may differ from the URL slug)."""
    model_cfg = config.get("models", {}).get(model, {})
    return model_cfg.get("model_id", model)


# ── Agent loop ──────────────────────────────────────────────────────────


def run_agent(
    prompt: str,
    system_prompt: str,
    mcp_url: str,
    model: str,
    max_turns: int,
    config: dict,
) -> None:
    base_url = build_base_url(config, model)
    api_key = get_api_key(config, model)
    model_id = get_model_id(config, model)

    log(f"Model slug: {model}")
    log(f"Model ID: {model_id}")
    log(f"Base URL: {base_url}")
    log(f"Max turns: {max_turns}")

    client = OpenAI(
        base_url=base_url,
        api_key=api_key,
        http_client=DefaultHttpxClient(verify=False),
    )

    mcp = MCPClient(mcp_url)
    try:
        log("Initializing MCP session ...")
        mcp.initialize()

        log("Fetching MCP tools ...")
        mcp_tools = mcp.list_tools()
        openai_tools = mcp_tools_to_openai(mcp_tools)
        log(f"Discovered {len(openai_tools)} tools")

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        total_input_tokens = 0
        total_output_tokens = 0
        last_text = ""
        turns = 0

        while turns < max_turns:
            turns += 1
            log(f"Turn {turns}/{max_turns} ...")

            kwargs: dict = {
                "model": model_id,
                "messages": messages,
            }
            if openai_tools:
                kwargs["tools"] = openai_tools

            try:
                response = client.chat.completions.create(**kwargs)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "rate" in error_str.lower():
                    wait = min(2**turns, 60)
                    log(f"Rate limited, waiting {wait}s ...")
                    time.sleep(wait)
                    turns -= 1
                    continue
                raise

            choice = response.choices[0]
            message = choice.message

            if response.usage:
                total_input_tokens += response.usage.prompt_tokens or 0
                total_output_tokens += response.usage.completion_tokens or 0

            assistant_msg: dict = {"role": "assistant"}
            if message.content:
                assistant_msg["content"] = message.content
                last_text = message.content

            if message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ]

            messages.append(assistant_msg)

            # Collect tool calls — native or parsed from text
            native_calls = message.tool_calls or []
            text_calls = []
            if not native_calls and message.content:
                text_calls = parse_text_tool_calls(message.content)
                if text_calls:
                    log(f"  Parsed {len(text_calls)} tool call(s) from text")
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["arguments"]},
                        }
                        for tc in text_calls
                    ]

            tool_call_names = []
            has_tool_calls = bool(native_calls or text_calls)

            if native_calls:
                for tc in native_calls:
                    name = tc.function.name
                    tool_call_names.append(name)
                    try:
                        args = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    log(f"  Tool: {name}({json.dumps(args, ensure_ascii=False)[:200]})")

                    try:
                        result = mcp.call_tool(name, args)
                    except Exception as e:
                        result = f"Error calling tool {name}: {e}"
                        log(f"  Tool error: {e}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )

            elif text_calls:
                for tc in text_calls:
                    name = tc["name"]
                    tool_call_names.append(name)
                    try:
                        args = json.loads(tc["arguments"])
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    log(f"  Tool: {name}({json.dumps(args, ensure_ascii=False)[:200]})")

                    try:
                        result = mcp.call_tool(name, args)
                    except Exception as e:
                        result = f"Error calling tool {name}: {e}"
                        log(f"  Tool error: {e}")

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        }
                    )

            emit(
                {
                    "type": "turn",
                    "turn": turns,
                    "content": message.content or "",
                    "tool_calls": tool_call_names,
                }
            )

            if not has_tool_calls:
                log("Model finished (no more tool calls)")
                break

        emit(
            {
                "type": "result",
                "response_text": last_text,
                "turns": turns,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
            }
        )

    finally:
        mcp.close()


# ── CLI ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Models.Corp agent harness")
    parser.add_argument("--prompt", required=True, help="Turn prompt")
    parser.add_argument("--system-prompt-file", required=True, help="Path to system prompt file")
    parser.add_argument("--mcp-url", required=True, help="MCP Streamable HTTP endpoint URL")
    parser.add_argument("--model", required=True, help="Model slug (e.g. granite-4-0-micro)")
    parser.add_argument("--max-turns", type=int, default=50, help="Max agentic turns")
    parser.add_argument("--config", required=True, help="Path to .modelscorp.json config")
    args = parser.parse_args()

    with open(args.system_prompt_file) as f:
        system_prompt = f.read()

    config = load_config(args.config)

    try:
        run_agent(
            prompt=args.prompt,
            system_prompt=system_prompt,
            mcp_url=args.mcp_url,
            model=args.model,
            max_turns=args.max_turns,
            config=config,
        )
    except Exception as e:
        log(f"FATAL: {e}")
        emit(
            {
                "type": "result",
                "response_text": "",
                "turns": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "error": str(e),
            }
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
