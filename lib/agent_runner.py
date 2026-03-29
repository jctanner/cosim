"""Claude SDK agent launcher — captures and returns full text responses."""

import json
import time
import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions


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


def _extract_response_text(msg, response_parts: list[str]) -> None:
    """Extract text from an SDK message into response_parts list."""
    type_name = type(msg).__name__
    if type_name == "AssistantMessage":
        for block in msg.content:
            if type(block).__name__ == "TextBlock":
                response_parts.append(block.text)
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


class AgentPool:
    """Manages persistent Claude SDK sessions for agent personas.

    Opens one ClaudeSDKClient per persona at startup. Role instructions are
    sent once during initialization. Subsequent calls to send() reuse the
    existing session, avoiding cold-start overhead.
    """

    def __init__(self, personas: list[dict], model: str, log_dir: Path):
        self._personas = {p["name"]: p for p in personas}
        self._model = model
        self._model_id = get_model_id(model)
        self._log_dir = log_dir
        self._cwd = str(Path(__file__).parent.parent)
        self._clients: dict[str, ClaudeSDKClient] = {}
        self._log_files: dict[str, Path] = {}

    async def start(self, build_initial_prompt, on_progress=None) -> None:
        """Open and initialize sessions for all personas.

        Args:
            build_initial_prompt: callable(persona_key) -> str that returns the
                one-time role prompt for a persona.
            on_progress: optional callable(i, total, persona_key, display_name, state)
                called before ("starting") and after ("ready") each agent.
                Return True from the callback to abort startup early.
        """
        self._log_dir.mkdir(parents=True, exist_ok=True)

        total = len(self._personas)
        print(f"Opening {total} agent sessions...")
        for i, (key, persona) in enumerate(self._personas.items(), 1):
            print(f"  Starting ({i}/{total}): {persona['display_name']}...", end="", flush=True)
            if on_progress:
                if on_progress(i, total, key, persona["display_name"], "starting"):
                    print(f" ABORTED")
                    print(f"Startup interrupted at {i}/{total}")
                    return
            await self._open_session(key, build_initial_prompt)
            if on_progress:
                if on_progress(i, total, key, persona["display_name"], "ready"):
                    print(f" ABORTED after ready")
                    print(f"Startup interrupted at {i}/{total}")
                    return
        print(f"All {len(self._clients)}/{total} sessions ready")

    async def _open_session(self, persona_key: str, build_initial_prompt) -> None:
        """Open a single persistent session and send role instructions."""
        persona = self._personas[persona_key]
        name = persona["display_name"]

        options = ClaudeAgentOptions(
            cwd=self._cwd,
            allowed_tools=["Read"],
            permission_mode="bypassPermissions",
            model=self._model_id,
        )

        # Set up log file (append mode for persistent sessions)
        log_file = self._log_dir / f"{name.replace('/', '_').replace(' ', '_')}.log"
        self._log_files[persona_key] = log_file
        with open(log_file, "w") as f:
            f.write(f"Agent: {name}\n")
            f.write(f"Model: {self._model} ({self._model_id})\n")
            f.write(f"Session type: persistent\n")
            f.write(f"{'=' * 60}\n\n")

        start_time = time.monotonic()
        client = ClaudeSDKClient(options=options)
        await client.__aenter__()
        self._clients[persona_key] = client

        # Send role instructions as the initial prompt
        init_prompt = build_initial_prompt(persona_key)
        with open(log_file, "a") as f:
            f.write(f"INIT PROMPT:\n{init_prompt}\n\n{'=' * 60}\n\n")

        await client.query(init_prompt)
        async for msg in client.receive_response():
            pass  # Consume init response (expect "READY" or similar)

        elapsed = time.monotonic() - start_time
        ready_count = len(self._clients)
        total_count = len(self._personas)
        print(f" ready ({ready_count}/{total_count}) ({format_duration(elapsed)})")

    async def send(self, persona_key: str, prompt: str) -> dict:
        """Send a turn prompt to a persona's persistent session.

        Returns:
            dict with 'name', 'success', 'response_text', 'duration_seconds'
        """
        persona = self._personas[persona_key]
        name = persona["display_name"]
        log_file = self._log_files.get(persona_key)

        client = self._clients.get(persona_key)
        if client is None:
            return {
                "name": name,
                "success": False,
                "response_text": "",
                "error": "no session",
                "duration_seconds": 0,
            }

        # Log the prompt
        if log_file:
            with open(log_file, "a") as f:
                f.write(f"TURN PROMPT:\n{prompt}\n\n{'-' * 40}\n\n")

        start_time = time.monotonic()
        response_parts = []

        try:
            await client.query(prompt)
            async for msg in client.receive_response():
                if log_file:
                    with open(log_file, "a") as f:
                        f.write(f"{msg}\n")
                        f.flush()
                _extract_response_text(msg, response_parts)

            elapsed = time.monotonic() - start_time
            response_text = "\n".join(response_parts).strip()

            if log_file:
                with open(log_file, "a") as f:
                    f.write(f"\nRESPONSE: {response_text}\n")
                    f.write(f"DURATION: {format_duration(elapsed)}\n")
                    f.write(f"{'=' * 60}\n\n")

            return {
                "name": name,
                "success": True,
                "response_text": response_text,
                "duration_seconds": elapsed,
            }

        except Exception as e:
            elapsed = time.monotonic() - start_time
            print(f"  {name}: session error — {e}")

            if log_file:
                with open(log_file, "a") as f:
                    f.write(f"\nERROR: {e}\n{'=' * 60}\n\n")

            # Remove dead session so orchestrator knows it's gone
            await self._close_session(persona_key)

            return {
                "name": name,
                "success": False,
                "response_text": "",
                "error": str(e),
                "duration_seconds": elapsed,
            }

    async def _close_session(self, persona_key: str) -> None:
        """Close a single session. Handles SDK errors gracefully."""
        client = self._clients.pop(persona_key, None)
        if client:
            try:
                await client.__aexit__(None, None, None)
            except (Exception, BaseException):
                pass  # SDK may raise CancelledError or other async errors

    async def close(self) -> None:
        """Close all sessions. Safe to call multiple times."""
        keys = list(self._clients.keys())
        if not keys:
            return
        for key in keys:
            await self._close_session(key)
        print("All agent sessions closed")


# ── Legacy one-shot function (kept for backward compat) ──────────────

async def run_agent_for_response(
    name: str,
    prompt: str,
    log_dir: Path,
    model: str = "sonnet",
) -> dict:
    """
    Launch a Claude agent session and capture the full text response.

    This is the original one-shot function. Prefer AgentPool for
    persistent sessions with lower latency.
    """
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
