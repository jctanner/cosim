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
import os
import sys
import time

from openai import DefaultHttpxClient, OpenAI

from cosim_agent.mcp_client import MCPClient, mcp_tools_to_openai, parse_text_tool_calls
from cosim_agent.memory import create_memory


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def emit(obj: dict) -> None:
    print(json.dumps(obj), flush=True)


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
    return f"https://{model}--{endpoint_type}.{endpoint_base}:443{base_path or '/v1'}"


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
    allowed_tools: list[str] | None = None,
    memory_config: dict | None = None,
    fallback_channel: str = "",
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
        if allowed_tools:
            mcp_tools = [t for t in mcp_tools if t["name"] in allowed_tools]
            log(f"Filtered to {len(mcp_tools)} allowed tools")
        openai_tools = mcp_tools_to_openai(mcp_tools)
        log(f"Discovered {len(openai_tools)} tools")

        mem_cfg = memory_config or {"strategy": "none"}
        memory = create_memory(mem_cfg, system_prompt, llm_client=client, llm_model=model_id)
        memory.load()
        log(f"Memory config: {mem_cfg}")
        messages = memory.get_messages(prompt)

        total_input_tokens = 0
        total_output_tokens = 0
        last_text = ""
        turns = 0
        turn_start_idx = len(messages) - 1  # include the user prompt in saved messages
        posted_message = False

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

            if "post_message" in tool_call_names:
                posted_message = True

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

            if "signal_done" in tool_call_names:
                log("Agent signaled done — stopping loop")
                break

        if fallback_channel and last_text and not posted_message:
            log(f"Fallback: model produced text but never called post_message — auto-posting to {fallback_channel}")
            try:
                mcp.call_tool("post_message", {"channel": fallback_channel, "content": last_text})
            except Exception as e:
                log(f"Fallback post_message failed: {e}")

        new_messages = messages[turn_start_idx:]
        if new_messages:
            memory.add_messages(new_messages)
            memory.save()

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
    parser.add_argument("--allowed-tools", default="", help="Comma-separated list of allowed tool names (empty=all)")
    parser.add_argument(
        "--memory-config",
        default="",
        help="JSON blob of memory config (strategy, session_file, max_messages, etc.)",
    )
    parser.add_argument("--memory-strategy", default="none", help="(legacy) Conversation memory strategy")
    parser.add_argument("--session-file", default="", help="(legacy) Path to JSONL session history file")
    parser.add_argument("--memory-max-messages", type=int, default=50, help="(legacy) Max messages in FIFO window")
    parser.add_argument(
        "--fallback-channel",
        default="",
        help="If the model produces text but never calls post_message, auto-post to this channel",
    )
    args = parser.parse_args()

    with open(args.system_prompt_file) as f:
        system_prompt = f.read()

    config = load_config(args.config)
    allowed_tools = [t.strip() for t in args.allowed_tools.split(",") if t.strip()] or None

    if args.memory_config:
        mem_cfg = json.loads(args.memory_config)
    else:
        mem_cfg = {
            "strategy": args.memory_strategy,
            "session_file": args.session_file,
            "max_messages": args.memory_max_messages,
        }

    session_file = mem_cfg.get("session_file", "")
    if session_file:
        os.makedirs(os.path.dirname(session_file), exist_ok=True)

    try:
        run_agent(
            prompt=args.prompt,
            system_prompt=system_prompt,
            mcp_url=args.mcp_url,
            model=args.model,
            max_turns=args.max_turns,
            config=config,
            allowed_tools=allowed_tools,
            memory_config=mem_cfg,
            fallback_channel=args.fallback_channel,
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
