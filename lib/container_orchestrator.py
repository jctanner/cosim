"""Container orchestrator — runs agents as long-running podman containers with MCP tools.

Phase 3: Containers start once at session init and persist across turns. Each agent
turn runs via `podman exec` inside the warm container. Tier advancement uses
signal_done events from the MCP server rather than waiting for process exit.
"""

import asyncio
import json
import logging
import os
import platform as _platform
import shutil
import subprocess
import threading
import time as _time
from pathlib import Path

import requests as sync_requests

from lib.chat_client import ChatClient
from lib.agent_runner import get_model_id, format_duration
from lib.personas import (
    get_active_personas,
    build_v3_system_prompt,
    build_v3_turn_prompt,
    DEFAULT_CHANNELS,
    DEFAULT_MEMBERSHIPS,
    PERSONAS,
    RESPONSE_TIERS,
    PERSONA_TIER,
)

logger = logging.getLogger("container_orchestrator")

LOG_DIR = Path(__file__).parent.parent / "var" / "logs"
TMP_DIR = Path(__file__).parent.parent / "var" / "tmp"

# Env vars to forward from the host process to agent containers.
# These are loaded from .env by main.py's load_dotenv() call.
_FORWARD_ENV_VARS = [
    "CLAUDE_CODE_USE_VERTEX",
    "CLOUD_ML_REGION",
    "ANTHROPIC_VERTEX_PROJECT_ID",
    "ANTHROPIC_API_KEY",        # non-Vertex usage
]


def _collect_env_vars() -> list[str]:
    """Collect env vars from os.environ to forward to containers as -e flags.

    Returns a list like ["-e", "KEY=VAL", "-e", "KEY2=VAL2", ...].
    """
    flags: list[str] = []
    for key in _FORWARD_ENV_VARS:
        val = os.environ.get(key)
        if val:
            flags.extend(["-e", f"{key}={val}"])
    return flags


def _find_and_stage_gcp_credentials() -> str | None:
    """Find GCP credentials and copy to var/tmp/ for SELinux-safe mounting.

    Checks: GOOGLE_APPLICATION_CREDENTIALS env var, project-local copy,
    then standard gcloud location. Copies to var/tmp/ so the mount uses
    the same SELinux context as other already-working volume mounts.
    """
    candidates = [
        os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        str(Path(__file__).parent.parent / "application_default_credentials.json"),
        str(Path.home() / ".config" / "gcloud" / "application_default_credentials.json"),
    ]
    src = None
    for path in candidates:
        if path and Path(path).is_file():
            src = path
            break
    if not src:
        return None

    staged = TMP_DIR / "gcp-credentials.json"
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, staged)
    return str(staged)


# -- DM (Direct Message) queue --
# recipient_key -> [{from_key, from_name, text, timestamp}]
_dm_queue: dict[str, list[dict]] = {}
_dm_queue_lock = threading.Lock()


def get_dm_queue() -> dict:
    """Return a snapshot of the DM queue (for session persistence)."""
    with _dm_queue_lock:
        return {k: list(v) for k, v in _dm_queue.items()}


def set_dm_queue(data: dict) -> None:
    """Replace the DM queue with saved data (for session restore)."""
    with _dm_queue_lock:
        _dm_queue.clear()
        _dm_queue.update(data)


def _get_agent_display_names() -> set[str]:
    """Get agent display names dynamically (PERSONAS populated after scenario load)."""
    return {p["display_name"] for p in PERSONAS.values()}


def _is_agent_message(msg: dict) -> bool:
    """Return True if the message was posted by an agent (not an event or human)."""
    if msg.get("is_event"):
        return False  # Event-triggered messages should be treated as external input
    return msg["sender"] in _get_agent_display_names()


def _get_channel_memberships(client: ChatClient) -> dict[str, set[str]]:
    """Fetch current channel memberships from the server.

    Returns dict[channel_name, set_of_persona_keys].
    """
    channels = client.get_channels()
    return {ch["name"]: set(ch["members"]) for ch in channels}


def _requeue_restart(base_url: str, scenario_name: str) -> None:
    """Re-queue a restart command to the server using a sync HTTP request.

    Called when the orchestrator is about to crash so the command
    survives the restart.
    """
    try:
        resp = sync_requests.post(
            f"{base_url}/api/orchestrator/command",
            json={"action": "restart", "scenario": scenario_name},
            timeout=5,
        )
        print(f"  Re-queue response: {resp.status_code}")
    except Exception as e:
        print(f"  Re-queue failed: {e}")


# All 32 MCP tool names (must match lib/mcp_server.py registrations)
MCP_TOOL_NAMES = [
    # Communication (7)
    "list_channels", "post_message", "get_messages", "send_dm", "get_my_dms",
    "join_channel", "get_channel_members",
    # Documents (7)
    "create_doc", "update_doc", "read_doc", "search_docs", "list_docs",
    "delete_doc", "append_doc",
    # GitLab (6)
    "list_repos", "create_repo", "commit_files", "read_file",
    "list_repo_tree", "get_repo_log",
    # Tickets (5)
    "get_ticket", "create_ticket", "update_ticket", "comment_on_ticket", "list_tickets",
    # Memos (5)
    "list_memos", "get_memo_thread", "create_memo", "reply_to_memo", "delete_memo",
    # Blog (7)
    "list_blog_posts", "read_blog_post", "create_blog_post", "reply_to_blog",
    "update_blog_post", "delete_blog_post",
    # Email (2)
    "send_email", "get_emails",
    # Meta (6)
    "whoami", "who_is", "get_my_channels", "get_my_tickets",
    "get_recent_activity", "signal_done",
]


def _detect_mcp_host() -> str:
    """Detect the hostname containers use to reach services on the host.

    Prefers host.containers.internal (works on macOS and modern Linux
    podman with pasta/slirp4netns). Falls back to the podman network
    gateway IP if the DNS name isn't resolvable from inside a container.
    """
    if _platform.system() == "Darwin":
        return "host.containers.internal"

    # Linux: test host.containers.internal from inside a throwaway container
    try:
        proc = subprocess.run(
            ["podman", "run", "--rm", "agent-image:latest",
             "getent", "hosts", "host.containers.internal"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return "host.containers.internal"
    except Exception:
        pass

    # Fall back: get the host gateway IP from podman's default network
    try:
        proc = subprocess.run(
            ["podman", "network", "inspect", "podman",
             "--format", "{{range .Subnets}}{{.Gateway}}{{end}}"],
            capture_output=True, text=True, timeout=5,
        )
        gateway = proc.stdout.strip()
        if gateway:
            return gateway
    except Exception:
        pass

    return "host.containers.internal"


async def _preflight_checks(container_image: str) -> None:
    """Validate podman and container image before session startup."""
    # Check podman is available
    proc = await asyncio.create_subprocess_exec(
        "podman", "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"podman not found or not working: {stderr.decode()}")
    print(f"  podman: {stdout.decode().strip()}")

    # Check container image exists
    proc = await asyncio.create_subprocess_exec(
        "podman", "image", "exists", container_image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Container image '{container_image}' not found.\n"
            f"  Build it first:  ./scripts/build-agent-image.sh\n"
            f"  Or manually:     podman build -t {container_image} -f container/Dockerfile.agent container/"
        )
    print(f"  image: {container_image}")


class ContainerPool:
    """Manages long-running podman containers for agent personas.

    Containers start once at session init and persist across turns. Each agent
    turn runs `podman exec claude -p ...` inside the warm container. Per-agent
    asyncio.Locks prevent concurrent execution within a single container.
    """

    def __init__(
        self,
        personas: list[dict],
        model: str,
        log_dir: Path,
        mcp_host: str = "host.containers.internal",
        mcp_port: int = 5001,
        container_image: str = "agent-image:latest",
        container_timeout: int = 300,
        max_turns: int = 50,
        done_timeout: int = 120,
    ):
        self._personas: dict[str, dict] = {p["name"]: p for p in personas}
        self._model = model
        self._model_id = get_model_id(model)
        self._log_dir = log_dir
        self._mcp_host = mcp_host
        self._mcp_port = mcp_port
        self._container_image = container_image
        self._container_timeout = container_timeout
        self._max_turns = max_turns
        self._done_timeout = done_timeout
        self._active_containers: dict[str, str] = {}  # persona_key -> container_name
        self._agent_locks: dict[str, asyncio.Lock] = {}  # per-agent mutex
        self._mcp_base_url: str = ""  # set in start()
        mcp_tools = [f"mcp__sim__{t}" for t in MCP_TOOL_NAMES]
        from lib.scenario_loader import get_settings
        agent_builtin = get_settings().get("agent_builtin_tools", ["WebSearch", "WebFetch"])
        self._allowed_tools_str = ",".join(mcp_tools + agent_builtin)
        self._config_files: dict[str, Path] = {}  # persona_key -> mcp config path
        self._prompt_files: dict[str, Path] = {}  # persona_key -> system prompt path
        self._env_flags = _collect_env_vars()
        self._gcp_creds_path = _find_and_stage_gcp_credentials()

    async def start(self, build_system_prompt_fn, on_progress=None) -> None:
        """Validate prerequisites, generate configs, and launch long-running containers.

        Args:
            build_system_prompt_fn: callable(persona_key) -> str that returns
                the system prompt for a persona.
            on_progress: optional callable(i, total, persona_key, display_name, state)
                called for each persona during setup.
        """
        self._log_dir.mkdir(parents=True, exist_ok=True)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        self._mcp_base_url = f"http://127.0.0.1:{self._mcp_port}"

        # Clear done events on MCP server
        self._clear_done_events()

        total = len(self._personas)
        env_names = [f.split("=")[0] for f in self._env_flags if "=" in f and not f.startswith("-")]
        print(f"Launching {total} long-running agent containers...")
        print(f"  Env vars forwarded: {', '.join(env_names) if env_names else '(none)'}")
        if self._gcp_creds_path:
            print(f"  GCP credentials: {self._gcp_creds_path}")
        else:
            print(f"  GCP credentials: not found (containers may fail auth)")

        for i, (key, persona) in enumerate(self._personas.items(), 1):
            display_name = persona["display_name"]
            print(f"  Launching ({i}/{total}): {display_name}...", end="", flush=True)

            if on_progress:
                on_progress(i, total, key, display_name, "starting")

            # Generate MCP config file
            mcp_config = {
                "mcpServers": {
                    "sim": {
                        "type": "sse",
                        "url": f"http://{self._mcp_host}:{self._mcp_port}/agents/{key}/sse",
                    }
                }
            }
            config_path = TMP_DIR / f"mcp-config-{key}.json"
            config_path.write_text(json.dumps(mcp_config, indent=2))
            self._config_files[key] = config_path

            # Generate system prompt file
            prompt_path = TMP_DIR / f"system-prompt-{key}.md"
            prompt_content = build_system_prompt_fn(key)
            prompt_path.write_text(prompt_content)
            self._prompt_files[key] = prompt_path

            # Initialize log file
            log_file = self._log_dir / f"{display_name.replace('/', '_').replace(' ', '_')}.log"
            with open(log_file, "w") as f:
                f.write(f"Agent: {display_name}\n")
                f.write(f"Model: {self._model} ({self._model_id})\n")
                f.write(f"Mode: container (v3 — long-running)\n")
                f.write(f"Image: {self._container_image}\n")
                f.write(f"{'=' * 60}\n\n")

            # Launch long-running container
            await self._launch_container(key)

            # Create per-agent lock
            self._agent_locks[key] = asyncio.Lock()

            print(f" ready ({i}/{total})")

            if on_progress:
                on_progress(i, total, key, display_name, "ready")

        print(f"All {total} agent containers running")

    async def _launch_container(self, persona_key: str) -> None:
        """Launch a long-running container for an agent.

        Removes any leftover container from a previous session first.
        """
        container_name = f"agent-{persona_key}"
        config_path = self._config_files[persona_key]
        prompt_path = self._prompt_files[persona_key]

        # Cleanup leftover container from previous session
        rm_proc = await asyncio.create_subprocess_exec(
            "podman", "rm", "-f", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await rm_proc.communicate()

        # Launch long-running container (CMD from Dockerfile: sleep infinity)
        cmd = [
            "podman", "run", "-d",
            "--name", container_name,
            "-e", f"AGENT_PERSONA_KEY={persona_key}",
            "-e", f"MCP_SERVER_URL={self._mcp_host}:{self._mcp_port}",
            *self._env_flags,
            "-v", f"{config_path.resolve()}:/home/agent/.mcp-config.json:ro,Z",
            "-v", f"{prompt_path.resolve()}:/home/agent/system-prompt.md:ro,Z",
            self._container_image,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to launch container {container_name}: {stderr.decode().strip()}"
            )

        # Verify container is running
        check_proc = await asyncio.create_subprocess_exec(
            "podman", "container", "inspect", container_name,
            "--format", "{{.State.Status}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        check_stdout, _ = await check_proc.communicate()
        status = check_stdout.decode().strip()
        if status != "running":
            raise RuntimeError(
                f"Container {container_name} is not running (status: {status}). "
                f"Check: podman logs {container_name}"
            )

        # Copy GCP credentials into container (avoids volume mount permission/SELinux issues)
        if self._gcp_creds_path:
            creds_dest = f"{container_name}:/home/agent/.config/gcloud/application_default_credentials.json"
            cp_proc = await asyncio.create_subprocess_exec(
                "podman", "cp", self._gcp_creds_path, creds_dest,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await cp_proc.communicate()
            # Fix ownership so the agent user can read it
            chown_proc = await asyncio.create_subprocess_exec(
                "podman", "exec", "--user", "root", container_name,
                "chown", "agent:agent",
                "/home/agent/.config/gcloud/application_default_credentials.json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await chown_proc.communicate()

        self._active_containers[persona_key] = container_name

    async def run_agent(self, persona_key: str, turn_prompt: str) -> dict:
        """Run a single agent turn via `podman exec` inside the long-running container.

        The per-agent lock prevents concurrent execution within a single container.
        Timeout kills the exec'd process, not the container itself.

        Returns:
            dict with 'name', 'success', 'response_text', 'duration_seconds', 'exit_code'
        """
        async with self._agent_locks[persona_key]:
            return await self._run_agent_inner(persona_key, turn_prompt)

    async def _run_agent_inner(self, persona_key: str, turn_prompt: str) -> dict:
        """Inner implementation of run_agent (called under lock)."""
        persona = self._personas[persona_key]
        display_name = persona["display_name"]
        container_name = self._active_containers.get(persona_key)

        if not container_name:
            return {
                "name": display_name,
                "success": False,
                "response_text": "",
                "duration_seconds": 0,
                "exit_code": -1,
                "error": f"No active container for {persona_key}",
            }

        log_file = self._log_dir / f"{display_name.replace('/', '_').replace(' ', '_')}.log"

        # Log the turn prompt
        with open(log_file, "a") as f:
            f.write(f"TURN PROMPT:\n{turn_prompt}\n\n{'-' * 40}\n\n")

        cmd = [
            "podman", "exec", container_name,
            "claude",
            "-p", turn_prompt,
            "--system-prompt-file", "/home/agent/system-prompt.md",
            "--mcp-config", "/home/agent/.mcp-config.json",
            "--allowedTools", self._allowed_tools_str,
            "--output-format", "json",
            "--model", self._model_id,
            "--max-turns", str(self._max_turns),
            "--permission-mode", "dontAsk",
        ]

        start_time = _time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._container_timeout,
                )
            except asyncio.TimeoutError:
                print(f"  {display_name}: exec timeout ({self._container_timeout}s), killing process...")
                # Kill the exec'd process, not the container
                try:
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass
                elapsed = _time.monotonic() - start_time

                with open(log_file, "a") as f:
                    f.write(f"\nTIMEOUT after {format_duration(elapsed)}\n{'=' * 60}\n\n")

                return {
                    "name": display_name,
                    "success": False,
                    "response_text": "",
                    "duration_seconds": elapsed,
                    "exit_code": -1,
                    "error": "timeout",
                }

            elapsed = _time.monotonic() - start_time
            exit_code = proc.returncode
            stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

            # Log stdout/stderr
            with open(log_file, "a") as f:
                if stdout_text:
                    f.write(f"STDOUT:\n{stdout_text}\n\n")
                if stderr_text:
                    f.write(f"STDERR:\n{stderr_text}\n\n")
                f.write(f"EXIT CODE: {exit_code}\n")
                f.write(f"DURATION: {format_duration(elapsed)}\n")
                f.write(f"{'=' * 60}\n\n")

            success = exit_code == 0

            # Try to extract response from JSON output
            response_text = ""
            if stdout_text.strip():
                try:
                    output = json.loads(stdout_text.strip())
                    response_text = output.get("result", stdout_text.strip())
                except (json.JSONDecodeError, ValueError):
                    response_text = stdout_text.strip()

            if not success:
                print(f"  {display_name}: exec exited with code {exit_code} "
                      f"({format_duration(elapsed)})")
                if stderr_text:
                    for line in stderr_text.strip().split("\n")[:3]:
                        print(f"    stderr: {line}")

            return {
                "name": display_name,
                "success": success,
                "response_text": response_text,
                "duration_seconds": elapsed,
                "exit_code": exit_code,
            }

        except Exception as e:
            elapsed = _time.monotonic() - start_time
            print(f"  {display_name}: exec failed — {e}")

            with open(log_file, "a") as f:
                f.write(f"\nERROR: {e}\n{'=' * 60}\n\n")

            return {
                "name": display_name,
                "success": False,
                "response_text": "",
                "duration_seconds": elapsed,
                "exit_code": -1,
                "error": str(e),
            }

    def _poll_done_events(self, since_id: int = 0) -> list[dict]:
        """Poll the MCP server for signal_done events (sync HTTP)."""
        try:
            resp = sync_requests.get(
                f"{self._mcp_base_url}/api/agents/done-events",
                params={"since_id": since_id},
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug(f"Failed to poll done events: {e}")
        return []

    def _get_done_event_cursor(self) -> int:
        """Get the current done-event high-water mark without fetching all events."""
        try:
            resp = sync_requests.get(
                f"{self._mcp_base_url}/api/agents/done-events/cursor",
                timeout=5,
            )
            if resp.status_code == 200:
                return resp.json().get("cursor", 0)
        except Exception:
            pass
        return 0

    def _clear_done_events(self) -> None:
        """Clear all done events on the MCP server."""
        try:
            sync_requests.delete(
                f"{self._mcp_base_url}/api/agents/done-events",
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"Failed to clear done events: {e}")

    async def close(self) -> None:
        """Stop and remove all long-running containers."""
        for persona_key, container_name in list(self._active_containers.items()):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "podman", "stop", "-t", "5", container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                # Remove the container (not auto-removed since no --rm)
                rm_proc = await asyncio.create_subprocess_exec(
                    "podman", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await rm_proc.communicate()
                print(f"  Stopped and removed container: {container_name}")
            except Exception as e:
                print(f"  Failed to stop/remove {container_name}: {e}")
        self._active_containers.clear()
        self._agent_locks.clear()


# ---------------------------------------------------------------------------
# Agent status helpers
# ---------------------------------------------------------------------------

def _build_agent_status(personas: list[dict], pool: ContainerPool | None = None) -> dict:
    """Build agent status dict for heartbeat."""
    result = {}
    for p in personas:
        key = p["name"]
        # With long-running containers, check if the per-agent lock is held
        # to determine if the agent is actively processing a turn
        is_active = (
            pool is not None
            and key in pool._agent_locks
            and pool._agent_locks[key].locked()
        )
        result[key] = {
            "display_name": p["display_name"],
            "state": "responding" if is_active else "ready",
        }
    return result


# ---------------------------------------------------------------------------
# Command processing (adapted for container pool — no SDK sessions)
# ---------------------------------------------------------------------------

async def _process_single_command(client, pool, personas, scenario_name, cmd):
    """Process a single add/remove agent command.

    Returns (updated personas list, restart_requested bool).
    """
    action = cmd.get("action")

    if action == "add_agent":
        agent_key = cmd.get("key", "")
        if agent_key and agent_key not in pool._personas:
            npcs = client.get_npcs()
            npc_data = next((n for n in npcs if n["key"] == agent_key), None)
            if npc_data:
                persona = {
                    "name": agent_key,
                    "display_name": npc_data["display_name"],
                    "team_description": npc_data.get("team_description", ""),
                    "character_file": npc_data.get("character_file", ""),
                }
                PERSONAS[agent_key] = persona
                DEFAULT_MEMBERSHIPS[agent_key] = set(npc_data.get("channels", ["#general"]))
                tier = npc_data.get("tier", 1)
                PERSONA_TIER[agent_key] = tier
                RESPONSE_TIERS.setdefault(tier, [])
                if agent_key not in RESPONSE_TIERS[tier]:
                    RESPONSE_TIERS[tier].append(agent_key)
                pool._personas[agent_key] = persona

                # Generate config files for the new agent
                mcp_config = {
                    "mcpServers": {
                        "sim": {
                            "type": "sse",
                            "url": f"http://{pool._mcp_host}:{pool._mcp_port}/agents/{agent_key}/sse",
                        }
                    }
                }
                config_path = TMP_DIR / f"mcp-config-{agent_key}.json"
                config_path.write_text(json.dumps(mcp_config, indent=2))
                pool._config_files[agent_key] = config_path

                prompt_path = TMP_DIR / f"system-prompt-{agent_key}.md"
                prompt_path.write_text(build_v3_system_prompt(agent_key))
                pool._prompt_files[agent_key] = prompt_path

                # Launch long-running container and create lock
                await pool._launch_container(agent_key)
                pool._agent_locks[agent_key] = asyncio.Lock()

                personas = list(pool._personas.values())
                client.post_message("System", f"{persona['display_name']} has joined the team!", channel="#system")
                print(f"  Agent added: {persona['display_name']}")
            else:
                print(f"  Agent {agent_key} not found on server")

    elif action == "remove_agent":
        agent_key = cmd.get("key", "")
        if agent_key:
            display_name = pool._personas.get(agent_key, {}).get("display_name", agent_key)
            print(f"\n*** Removing agent: {display_name} ***")

            # Stop and remove long-running container
            if agent_key in pool._active_containers:
                container_name = pool._active_containers[agent_key]
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "podman", "stop", "-t", "5", container_name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await proc.communicate()
                    rm_proc = await asyncio.create_subprocess_exec(
                        "podman", "rm", "-f", container_name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await rm_proc.communicate()
                except Exception:
                    pass
                pool._active_containers.pop(agent_key, None)

            pool._agent_locks.pop(agent_key, None)
            pool._personas.pop(agent_key, None)
            pool._config_files.pop(agent_key, None)
            pool._prompt_files.pop(agent_key, None)
            PERSONAS.pop(agent_key, None)
            DEFAULT_MEMBERSHIPS.pop(agent_key, None)
            old_tier = PERSONA_TIER.pop(agent_key, None)
            if old_tier and old_tier in RESPONSE_TIERS:
                if agent_key in RESPONSE_TIERS[old_tier]:
                    RESPONSE_TIERS[old_tier].remove(agent_key)
            personas = list(pool._personas.values())
            # Finalize on server
            try:
                sync_requests.post(f"{client.base_url}/api/npcs/{agent_key}/finalize-fire", timeout=5)
            except Exception:
                pass
            client.post_message("System", f"{display_name} has left the company.", channel="#system")
            print(f"  Agent removed: {display_name}")

    elif action == "restart":
        return personas, True

    return personas, False


async def _process_pending_commands(client, pool, personas, scenario_name):
    """Drain the command queue via heartbeat, processing all pending commands.

    Returns (updated personas list, restart_requested bool).
    """
    while True:
        agents = _build_agent_status(personas, pool)
        cmd = client.send_heartbeat("ready", scenario_name, agents)
        action = cmd.get("action")
        if not action:
            break
        print(f"\n  Pending command: {cmd}")
        personas, restart = await _process_single_command(client, pool, personas, scenario_name, cmd)
        if restart:
            return personas, True
    return personas, False


# ---------------------------------------------------------------------------
# Tiered wave loop
# ---------------------------------------------------------------------------

async def _run_loop(
    client: ChatClient,
    pool: ContainerPool,
    personas: list[dict],
    trigger_channels: set[str],
    max_waves: int,
    scenario_name: str = "",
    max_concurrent: int = 4,
    agent_last_seen: dict[str, int] | None = None,
) -> set[str]:
    """Run the event-driven response loop with tiered agent responses.

    Mirrors v2 _run_loop() from lib/orchestrator.py but with key differences:
    - No state snapshot fetching per tier (agents fetch via MCP tools)
    - Turn prompt is build_v3_turn_prompt() (~300 bytes) not build_turn_prompt() (~10K+ bytes)
    - Agent launch: pool.run_agent(pk, prompt) not pool.send(pk, prompt)
    - No response parsing — agents already did everything via MCP tools
    - Activity detection via Flask message polling after each tier

    Returns the set of channels that received new agent posts (empty if all quiet).
    """
    if agent_last_seen is None:
        agent_last_seen = {}
    persona_map = {p["name"]: p for p in personas}
    wave = 0
    posted_channels: set[str] = set()
    semaphore = asyncio.Semaphore(max_concurrent)

    while trigger_channels and wave < max_waves:
        wave += 1
        print(f"\n=== Wave {wave}/{max_waves} — triggered: {sorted(trigger_channels)} ===")

        # Get current memberships from server
        memberships = _get_channel_memberships(client)

        # Get online/offline status
        npcs = client.get_npcs()
        offline_keys = {n["key"] for n in npcs if not n.get("online", True)}

        # Collect unique agents to trigger, tracking which channel triggered them
        agents_to_run: dict[str, set[str]] = {}
        for ch in trigger_channels:
            # Director channels trigger the specific agent they're for
            if ch.startswith("#director-"):
                pk = ch.replace("#director-", "")
                if pk in persona_map and pk not in offline_keys:
                    agents_to_run.setdefault(pk, set()).add(ch)
                elif pk in offline_keys:
                    print(f"  Skipping {persona_map.get(pk, {}).get('display_name', pk)}: out of office")
                continue
            members = memberships.get(ch, set())
            for persona_key in members:
                if persona_key in persona_map:
                    if persona_key in offline_keys:
                        continue
                    agents_to_run.setdefault(persona_key, set()).add(ch)

        if offline_keys:
            offline_names = [persona_map[k]["display_name"] for k in offline_keys if k in persona_map]
            if offline_names:
                print(f"  Out of office: {', '.join(offline_names)}")

        if not agents_to_run:
            print("  No agents to trigger in these channels")
            break

        # Group agents by tier
        tiers: dict[int, dict[str, set[str]]] = {}
        for pk, triggers in agents_to_run.items():
            tier = PERSONA_TIER.get(pk, 2)
            tiers.setdefault(tier, {})[pk] = triggers

        new_trigger_channels: set[str] = set()

        # Run tiers sequentially (1, 2, 3), agents within a tier concurrently.
        for tier_num in sorted(tiers.keys()):
            tier_agents = tiers[tier_num]
            tier_names = ", ".join(
                persona_map[pk]["display_name"] for pk in sorted(tier_agents)
            )
            print(f"\nWave {wave}, Tier {tier_num}: running {len(tier_agents)} agent(s) "
                  f"concurrently ({tier_names})")

            # Snapshot message state BEFORE this tier runs
            pre_tier_messages = client.get_messages()
            pre_tier_last_id = pre_tier_messages[-1]["id"] if pre_tier_messages else 0

            # Build prompts and get trigger messages for headlines
            trigger_msgs = [
                m for m in pre_tier_messages[-20:]
                if not _is_agent_message(m)
                and m.get("channel") in trigger_channels
            ]

            async def _run_agent(pk, trigger_ch_set):
                async with semaphore:
                    persona = persona_map[pk]
                    display_name = persona["display_name"]
                    all_agent_channels = {
                        ch_name for ch_name, ch_members in memberships.items()
                        if pk in ch_members
                    }

                    # Filter trigger messages to only channels this agent belongs to
                    agent_trigger_msgs = [
                        m for m in trigger_msgs
                        if m.get("channel") in all_agent_channels
                    ] if trigger_msgs else None

                    prompt = build_v3_turn_prompt(
                        pk,
                        trigger_ch_set & all_agent_channels,
                        trigger_messages=agent_trigger_msgs[-3:] if agent_trigger_msgs else None,
                        last_seen_id=agent_last_seen.get(pk, 0),
                    )

                    # Show typing indicators
                    for ch in all_agent_channels:
                        client.set_typing(display_name, ch, active=True)

                    # Update heartbeat to show this agent is responding
                    agents_status = _build_agent_status(personas, pool)
                    agents_status[pk]["state"] = "responding"
                    client.send_heartbeat("responding", scenario_name, agents_status,
                                          f"{display_name} is thinking...", check_commands=False)

                    result = await pool.run_agent(pk, prompt)

                    # Clear typing indicators
                    for ch in all_agent_channels:
                        client.set_typing(display_name, ch, active=False)

                    return pk, result

            # --- signal_done-aware tier advancement ---
            # Snapshot done event cursor before launching agents.
            # Use the MCP server's event count endpoint to get the current
            # high-water mark without fetching all historical events.
            done_since_id = pool._get_done_event_cursor()

            # Launch all agents as background tasks
            tier_agent_keys = set(tier_agents.keys())
            agent_tasks: dict[str, asyncio.Task] = {
                pk: asyncio.create_task(_run_agent(pk, triggered_by))
                for pk, triggered_by in tier_agents.items()
            }

            # Poll for completion: signal_done events OR process exits
            done_keys: set[str] = set()
            deadline = _time.monotonic() + pool._done_timeout

            while len(done_keys) < len(tier_agent_keys) and _time.monotonic() < deadline:
                # Check signal_done events from MCP server
                events = pool._poll_done_events(since_id=done_since_id)
                for event in events:
                    ek = event["agent_key"]
                    if ek in tier_agent_keys and ek not in done_keys:
                        done_keys.add(ek)
                        display = persona_map[ek]["display_name"]
                        summary = event.get("summary", "")
                        print(f"  {display}: signal_done — {summary}" if summary
                              else f"  {display}: signal_done")
                    done_since_id = max(done_since_id, event["id"])

                # Check process exits (fallback)
                for pk, task in agent_tasks.items():
                    if task.done() and pk not in done_keys:
                        done_keys.add(pk)

                if len(done_keys) < len(tier_agent_keys):
                    await asyncio.sleep(1)

            if len(done_keys) < len(tier_agent_keys):
                remaining = tier_agent_keys - done_keys
                remaining_names = ", ".join(persona_map[pk]["display_name"] for pk in remaining)
                print(f"  Tier {tier_num} timeout — advancing without: {remaining_names}")

            # Wait briefly for remaining processes to finish (per-agent mutex
            # prevents next turn from starting until process actually finishes)
            for pk, task in agent_tasks.items():
                if not task.done():
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=10)
                    except (asyncio.TimeoutError, Exception):
                        pass

            # Log results from completed tasks
            for pk, task in agent_tasks.items():
                if not task.done():
                    print(f"  {persona_map[pk]['display_name']}: still running (will finish in background)")
                    continue
                try:
                    entry = task.result()
                except Exception as exc:
                    print(f"  [Tier {tier_num}] agent task failed: {exc}")
                    continue

                _, result = entry
                persona = persona_map[pk]
                display_name = persona["display_name"]

                if result["success"]:
                    print(f"  {display_name}: completed ({format_duration(result['duration_seconds'])})")
                else:
                    error = result.get("error", f"exit code {result.get('exit_code', '?')}")
                    print(f"  {display_name}: failed — {error} ({format_duration(result['duration_seconds'])})")

            # Reset agent statuses after tier
            agents_status = _build_agent_status(personas, pool)
            client.send_heartbeat("responding", scenario_name, agents_status,
                                  "Processing messages...", check_commands=False)

            # Update per-agent last_seen to current high-water mark
            all_latest = client.get_messages()
            if all_latest:
                hw = all_latest[-1]["id"]
                for pk in tier_agents:
                    agent_last_seen[pk] = hw

            # Activity detection: query Flask for new messages since pre-tier snapshot
            latest = client.get_messages(since=pre_tier_last_id)
            agent_msgs = [m for m in latest if _is_agent_message(m)]
            new_channels = {m.get("channel", "#general") for m in agent_msgs} - trigger_channels
            if new_channels:
                new_trigger_channels.update(new_channels)
                print(f"  New agent activity in: {sorted(new_channels)}")

            posted_channels.update(m.get("channel", "#general") for m in agent_msgs)

        trigger_channels = new_trigger_channels

    if wave >= max_waves and trigger_channels:
        print(f"\n  Ripple limit reached ({max_waves} waves)")

    return posted_channels


# ---------------------------------------------------------------------------
# Main orchestrator loop
# ---------------------------------------------------------------------------

async def run_container_orchestrator(args) -> None:
    """Main container orchestrator loop: poll for messages, launch containers, detect activity.

    Mirrors run_orchestrator() from lib/orchestrator.py but uses ContainerPool
    instead of AgentPool, and v3 prompts instead of v2 prompts.
    """
    client = ChatClient(base_url=args.server_url)
    model = getattr(args, "model", "sonnet")
    scenario_name = getattr(args, "scenario", None)
    max_waves = getattr(args, "max_rounds", 3)
    poll_interval = getattr(args, "poll_interval", 5.0)
    max_auto_rounds = getattr(args, "max_auto_rounds", 3)
    mcp_port = getattr(args, "mcp_port", 5001)
    container_image = getattr(args, "container_image", "agent-image:latest")
    container_timeout = getattr(args, "container_timeout", 300)
    max_turns = getattr(args, "max_turns", 50)
    max_concurrent = getattr(args, "max_concurrent", 4)
    done_timeout = getattr(args, "done_timeout", 120)
    ticket_reminders = getattr(args, "ticket_reminders", False)
    personas = []

    mcp_host = getattr(args, "mcp_host", None) or _detect_mcp_host()

    print(f"Container orchestrator starting")
    print(f"  Server: {args.server_url}")
    print(f"  Model: {model}")
    print(f"  Max waves: {max_waves}")
    print(f"  Max autonomous rounds: {'unlimited' if max_auto_rounds == 0 else max_auto_rounds}")
    print(f"  Poll interval: {poll_interval}s")
    print(f"  MCP port: {mcp_port}")
    print(f"  MCP host (container→host): {mcp_host}")
    print(f"  Container image: {container_image}")
    print(f"  Container timeout: {container_timeout}s")
    print(f"  Max turns per container: {max_turns}")
    print(f"  Max concurrent agents: {max_concurrent}")
    print(f"  Done timeout: {done_timeout}s")
    print(f"  Ticket reminders: {'enabled' if ticket_reminders else 'disabled'}")

    # Wait for Flask server to be reachable
    while not client.health_check():
        client.send_heartbeat("connecting", scenario_name, {}, "Waiting for server...", check_commands=False)
        print("Waiting for chat server...")
        await asyncio.sleep(2)
    print("Connected to chat server")

    # Wait for MCP server to be reachable
    mcp_base_url = f"http://127.0.0.1:{mcp_port}"
    print(f"Checking MCP server at {mcp_base_url}...")
    mcp_retries = 0
    mcp_max_retries = 30  # ~60 seconds
    while True:
        try:
            resp = sync_requests.get(f"{mcp_base_url}/health", timeout=5)
            if resp.status_code == 200:
                print(f"MCP server is healthy: {resp.json()}")
                break
        except Exception:
            pass
        mcp_retries += 1
        if mcp_retries == 1:
            print(f"Waiting for MCP server at {mcp_base_url}...")
            print(f"  Start it with: python main.py mcp-server --port {mcp_port}")
        elif mcp_retries >= mcp_max_retries:
            raise RuntimeError(
                f"MCP server at {mcp_base_url} not reachable after {mcp_max_retries} attempts.\n"
                f"  Start it: python main.py mcp-server --port {mcp_port}"
            )
        client.send_heartbeat("connecting", scenario_name, {},
                              "Waiting for MCP server...", check_commands=False)
        await asyncio.sleep(2)

    # Pre-flight: validate podman and container image early
    await _preflight_checks(container_image)

    # Wait for a session to be started (New or Load)
    pool = None
    last_seen_id = 0

    client.send_heartbeat("waiting", scenario_name, {},
                          "Checking for pending commands...", check_commands=False)
    next_cmd = None

    print("Waiting for session start (click New or Load in the UI)...")
    while pool is None:
        if next_cmd:
            cmd = next_cmd
            next_cmd = None
        else:
            cmd = client.send_heartbeat("waiting", scenario_name, {},
                                        "Waiting for session — click New or Load")
        action = cmd.get("action")

        # Handle remove commands even in waiting state
        if action == "remove_agent":
            agent_key = cmd.get("key", "")
            if agent_key:
                try:
                    sync_requests.post(f"{client.base_url}/api/npcs/{agent_key}/finalize-fire", timeout=5)
                    print(f"  Finalized fire for {agent_key} (no active session)")
                except Exception:
                    pass
            continue
        if action == "add_agent":
            print(f"  Ignoring add_agent for {cmd.get('key', '')} (no active session)")
            continue

        if action == "restart":
            new_scenario = cmd.get("scenario", scenario_name)
            if new_scenario != scenario_name:
                from lib.scenario_loader import load_scenario
                load_scenario(new_scenario)
                scenario_name = new_scenario
            personas = get_active_personas(getattr(args, "personas", None))
            print(f"\nStarting session: {scenario_name}")
            print(f"  Personas: {', '.join(p['display_name'] for p in personas)}")

            # Load scenario on MCP server (in case it started without one)
            try:
                resp = sync_requests.post(
                    f"{mcp_base_url}/api/load-scenario",
                    json={"scenario": scenario_name},
                    timeout=10,
                )
                if resp.status_code == 200:
                    mcp_agents = resp.json().get("agents", [])
                    print(f"  MCP server loaded scenario '{scenario_name}' ({len(mcp_agents)} agents)")
                else:
                    print(f"  Warning: MCP load-scenario returned {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"  Warning: Failed to load scenario on MCP server: {e}")

            pool = ContainerPool(
                personas, model, LOG_DIR,
                mcp_host=mcp_host,
                mcp_port=mcp_port,
                container_image=container_image,
                container_timeout=container_timeout,
                max_turns=max_turns,
                done_timeout=done_timeout,
            )

            # Capture message baseline before startup so messages sent
            # while agents come online aren't silently skipped.
            existing = client.get_messages()
            last_seen_id = existing[-1]["id"] if existing else 0
            print(f"Skipping {len(existing)} existing messages (last_seen_id={last_seen_id})")

            online_names = []

            def on_progress(i, tot, key, display_name, state):
                agents = _build_agent_status(personas, pool)
                agents[key]["state"] = state
                if state == "starting":
                    msg = f"Launching agent {i}/{tot}: {display_name}..."
                else:
                    online_names.append(display_name)
                    msg = f"Agent ready {i}/{tot}: {display_name}"
                    online_list = ", ".join(online_names)
                    client.post_message("System",
                        f"{display_name} is online ({i}/{tot})\n\nAgents online: {online_list}",
                        channel="#system")
                client.send_heartbeat("starting", scenario_name, agents, msg, check_commands=False)

            try:
                await pool.start(build_v3_system_prompt, on_progress=on_progress)
            except Exception as e:
                print(f"Agent setup failed: {e}")
                try:
                    await pool.close()
                except Exception:
                    pass
                pool = None
                client.send_heartbeat("waiting", scenario_name, {},
                                      f"Agent startup failed: {e}", check_commands=False)
                _requeue_restart(client.base_url, scenario_name)
                continue

            # Send ready heartbeat
            agents = _build_agent_status(personas, pool)
            client.send_heartbeat("ready", scenario_name, agents, "All agents ready", check_commands=False)
            agent_last_seen: dict[str, int] = {}
        else:
            # Poll frequently while waiting for session start, regardless
            # of the configured poll_interval (which may be very long).
            await asyncio.sleep(min(poll_interval, 5.0))

    try:
        while True:
            # Check for commands from the server
            agents = _build_agent_status(personas, pool)
            cmd = client.send_heartbeat("ready", scenario_name, agents)
            if cmd.get("action"):
                print(f"\n  Command received: {cmd}")

            if cmd.get("action") == "restart":
                new_scenario = cmd.get("scenario", scenario_name)
                print(f"\n*** Restart command received (scenario: {new_scenario}) ***")

                # Stop any active containers
                if pool is not None:
                    await pool.close()

                # Brief pause
                await asyncio.sleep(2)

                # Reload scenario if changed
                if new_scenario != scenario_name:
                    from lib.scenario_loader import load_scenario
                    load_scenario(new_scenario)
                    scenario_name = new_scenario

                # Reload personas and create new pool
                personas = get_active_personas(getattr(args, "personas", None))

                # Reload scenario on MCP server
                try:
                    resp = sync_requests.post(
                        f"{mcp_base_url}/api/load-scenario",
                        json={"scenario": scenario_name},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        mcp_agents = resp.json().get("agents", [])
                        print(f"  MCP server reloaded scenario '{scenario_name}' ({len(mcp_agents)} agents)")
                    else:
                        print(f"  Warning: MCP load-scenario returned {resp.status_code}: {resp.text}")
                except Exception as e:
                    print(f"  Warning: Failed to reload scenario on MCP server: {e}")

                pool = ContainerPool(
                    personas, model, LOG_DIR,
                    mcp_host=mcp_host,
                    mcp_port=mcp_port,
                    container_image=container_image,
                    container_timeout=container_timeout,
                    max_turns=max_turns,
                    done_timeout=done_timeout,
                )

                # Capture message baseline before restart so messages sent
                # while agents come online aren't silently skipped.
                existing = client.get_messages()
                last_seen_id = existing[-1]["id"] if existing else 0
                print(f"Restart: baseline set at message {last_seen_id}")

                online_names = []

                def on_progress_restart(i, tot, key, display_name, state):
                    agents = _build_agent_status(personas, pool)
                    agents[key]["state"] = state
                    if state == "starting":
                        msg = f"Launching agent {i}/{tot}: {display_name}..."
                    else:
                        online_names.append(display_name)
                        msg = f"Agent ready {i}/{tot}: {display_name}"
                        online_list = ", ".join(online_names)
                        client.post_message("System",
                            f"{display_name} is online ({i}/{tot})\n\nAgents online: {online_list}",
                            channel="#system")
                    client.send_heartbeat("starting", scenario_name, agents, msg, check_commands=False)

                try:
                    await pool.start(build_v3_system_prompt, on_progress=on_progress_restart)
                except Exception as e:
                    print(f"Agent setup failed during restart: {e}")
                    # Clean up any containers that were launched before the failure
                    await pool.close()
                    pool = None
                    _requeue_restart(client.base_url, scenario_name)
                    continue

                print(f"Restart complete. {len(online_names)} agents online.")
                agent_last_seen = {}
                continue

            if cmd.get("action") == "shutdown":
                print("\n*** Shutdown command received ***")
                break

            if cmd.get("action") in ("add_agent", "remove_agent"):
                personas, _ = await _process_single_command(client, pool, personas, scenario_name, cmd)
                personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
                if restart:
                    continue
                continue

            new_messages = client.get_messages(since=last_seen_id)

            # Only trigger on non-agent messages (human input)
            human_messages = [m for m in new_messages if not _is_agent_message(m)]

            if not human_messages:
                if ticket_reminders:
                    try:
                        tickets = client.list_tickets()
                        open_tickets = [t for t in tickets if t.get("status", "").lower() not in ("done", "closed", "resolved")]
                        if open_tickets:
                            lines = [f"**[Automated Reminder]** There are {len(open_tickets)} open ticket(s) that need attention:\n"]
                            for t in open_tickets:
                                assignee = t.get("assignee", "unassigned")
                                lines.append(f"- **{t['id']}**: {t['title']} (priority: {t.get('priority', 'n/a')}, assigned: {assignee}, status: {t.get('status', 'open')})")
                            lines.append("\nPlease review and continue working on your assigned tickets.")
                            channels = client.get_channels()
                            ch_names = [c["name"] for c in channels if not c.get("is_external") and not c["name"].startswith("#director-") and c["name"] not in ("#system", "#dms")]
                            reminder_channel = "#general" if "#general" in ch_names else ch_names[0] if ch_names else "#general"
                            client.post_message("System", "\n".join(lines), channel=reminder_channel)
                            print(f"\nPosted ticket reminder ({len(open_tickets)} open tickets) to {reminder_channel}")
                            continue
                    except Exception as e:
                        print(f"Ticket reminder check failed: {e}")
                await asyncio.sleep(poll_interval)
                continue

            # Update heartbeat to show responding
            agents = _build_agent_status(personas, pool)
            client.send_heartbeat("responding", scenario_name, agents, "Processing messages...", check_commands=False)

            # Update last_seen_id
            if new_messages:
                last_seen_id = new_messages[-1]["id"]

            # Determine which channels have new human messages
            trigger_channels = {m.get("channel", "#general") for m in human_messages}
            print(f"\nNew human message(s) in {sorted(trigger_channels)}")

            active_channels = await _run_loop(
                client, pool, personas, trigger_channels, max_waves,
                scenario_name, max_concurrent, agent_last_seen,
            )

            # Reset all agents to ready and process any pending commands
            personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
            if restart:
                continue

            # Update last_seen_id to include any agent responses
            latest = client.get_messages()
            if latest:
                last_seen_id = latest[-1]["id"]

            # Autonomous continuation: let agents keep working
            auto_round = 0
            while active_channels:
                if max_auto_rounds > 0 and auto_round >= max_auto_rounds:
                    print(f"\n  Autonomous round limit reached ({max_auto_rounds})")
                    break
                auto_round += 1

                # Brief pause — process pending commands and check for new human input
                await asyncio.sleep(1)

                # Process add commands between rounds
                while True:
                    agents = _build_agent_status(personas, pool)
                    cmd = client.send_heartbeat("responding", scenario_name, agents, "Autonomous continuation...")
                    action = cmd.get("action")
                    if not action:
                        break
                    if action == "add_agent":
                        print(f"  Pending command: {cmd}")
                        personas, _ = await _process_single_command(client, pool, personas, scenario_name, cmd)
                    elif action == "remove_agent":
                        # Defer removal until autonomous rounds finish
                        print(f"  Deferring remove until quiesce: {cmd.get('key')}")
                        try:
                            sync_requests.post(f"{client.base_url}/api/orchestrator/command", json=cmd, timeout=5)
                        except Exception:
                            pass
                        break
                    elif action == "restart":
                        break
                    else:
                        break

                new_messages = client.get_messages(since=last_seen_id)
                human_messages = [m for m in new_messages if not _is_agent_message(m)]
                if human_messages:
                    print(f"\nHuman input detected — breaking autonomous continuation")
                    break

                limit_str = f"/{max_auto_rounds}" if max_auto_rounds > 0 else ""
                print(f"\n>>> Autonomous round {auto_round}{limit_str}"
                      f" — agents continuing in {sorted(active_channels)}")

                active_channels = await _run_loop(
                    client, pool, personas, active_channels, max_waves,
                    scenario_name, max_concurrent, agent_last_seen,
                )

                latest = client.get_messages()
                if latest:
                    last_seen_id = latest[-1]["id"]

            if auto_round > 0:
                print(f"\nAgents quiesced after {auto_round} autonomous round(s)")

            # Process any pending commands after quiesce
            personas, restart = await _process_pending_commands(client, pool, personas, scenario_name)
            if restart:
                continue

            # Auto-save session after each response cycle
            try:
                from lib.session import save_session
                save_session("autosave")
                print("Auto-saved session")
            except Exception as e:
                print(f"Auto-save failed: {e}")

            print(f"\nWaiting for new messages (last_seen_id={last_seen_id})...")
    finally:
        try:
            if pool is not None:
                await pool.close()
        except Exception:
            pass
