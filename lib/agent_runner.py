"""Claude SDK agent launcher — utilities and one-shot agent runner."""

import json
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


def get_model_display_name(model_shorthand: str) -> str:
    """Convert model shorthand to human-readable display name."""
    display_names = {
        "sonnet": "Claude Sonnet 4.5",
        "opus": "Claude Opus 4.6",
        "haiku": "Claude Haiku 3.5",
    }
    return display_names.get(model_shorthand, model_shorthand)


def get_model_id(model_shorthand: str) -> str:
    """Convert model shorthand to full model ID."""
    model_mapping = {
        "sonnet": "claude-sonnet-4-5",
        "opus": "claude-opus-4-6",
        "haiku": "claude-haiku-3-5",
    }
    return model_mapping.get(model_shorthand, model_shorthand)


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration string."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def _extract_response_text(msg, response_parts: list[str], thinking_parts: list[str] | None = None) -> None:
    """Extract text from an SDK message into response_parts list."""
    type_name = type(msg).__name__
    if type_name == "AssistantMessage":
        for block in msg.content:
            block_type = type(block).__name__
            if block_type == "TextBlock":
                response_parts.append(block.text)
            elif block_type == "ThinkingBlock" and thinking_parts is not None:
                if hasattr(block, "thinking") and block.thinking:
                    thinking_parts.append(block.thinking)
    elif type_name == "ResultMessage":
        # Handle structured_output (JSON mode) if present
        if hasattr(msg, "structured_output") and msg.structured_output:
            try:
                response_parts.append(json.dumps(msg.structured_output))
            except (TypeError, ValueError):
                pass
        if hasattr(msg, "result") and msg.result:
            if not response_parts:
                response_parts.append(msg.result)


async def run_agent_for_response(
    name: str,
    prompt: str,
    log_dir: Path,
    model: str = "sonnet",
) -> dict:
    """Launch a one-shot Claude agent session and capture the full text response."""
    log_file = log_dir / f"{name.replace('/', '_').replace(' ', '_')}.log"
    model_id = get_model_id(model)

    options = ClaudeAgentOptions(
        cwd=str(Path(__file__).parent.parent),
        allowed_tools=["Read"],
        permission_mode="bypassPermissions",
        model=model_id,
    )

    log_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"  Running agent: {name} (model: {model})")

    with open(log_file, "w") as log:
        log.write(f"Agent: {name}\n")
        log.write(f"Model: {model}\n")
        log.write(f"{'=' * 60}\n\n")
        log.write("PROMPT:\n")
        log.write(prompt)
        log.write(f"\n\n{'=' * 60}\n")
        log.write("AGENT OUTPUT:\n\n")

    start_time = time.monotonic()
    response_parts = []

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)

            with open(log_file, "a") as log:
                async for msg in client.receive_response():
                    log.write(f"{msg}\n")
                    log.flush()
                    _extract_response_text(msg, response_parts)

        elapsed = time.monotonic() - start_time
        response_text = "\n".join(response_parts).strip()

        print(f"  Completed: {name} ({format_duration(elapsed)})")

        return {
            "name": name,
            "success": True,
            "response_text": response_text,
            "log_file": str(log_file),
            "duration_seconds": elapsed,
        }

    except Exception as e:
        elapsed = time.monotonic() - start_time

        print(f"  Failed: {name} ({format_duration(elapsed)}) — {e}")

        with open(log_file, "a") as log:
            log.write(f"\n\nERROR: {e}\n")

        return {
            "name": name,
            "success": False,
            "response_text": "",
            "error": str(e),
            "log_file": str(log_file),
            "duration_seconds": elapsed,
        }
