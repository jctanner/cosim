"""Agent CLI backends — strategy pattern for Claude Code vs Codex invocation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class AgentBackend(Protocol):
    """Protocol for agent CLI backends."""

    def build_exec_command(
        self,
        container_name: str,
        turn_prompt: str,
        *,
        resuming: bool = False,
        session_id: str | None = None,
        model_id: str,
        max_turns: int,
        allowed_tools_str: str,
        use_sessions: bool = False,
    ) -> list[str]: ...

    def parse_output(self, stdout: str) -> tuple[str, str, dict]:
        """Parse JSONL output. Returns (response_text, thinking_text, metadata)."""
        ...

    def generate_config_files(
        self,
        persona_key: str,
        system_prompt: str,
        mcp_host: str,
        mcp_port: int,
        tmp_dir: Path,
    ) -> dict[str, Path]:
        """Generate config files for this backend. Returns {purpose: host_path}."""
        ...

    def get_volume_mounts(self, config_files: dict[str, Path]) -> list[str]:
        """Return podman -v flags for mounting config into the container."""
        ...

    def get_credential_sources(self) -> list[tuple[str, str]]:
        """Return list of (host_path, container_path) for credentials to copy."""
        ...


class ClaudeBackend:
    """Claude Code CLI backend."""

    def build_exec_command(
        self,
        container_name: str,
        turn_prompt: str,
        *,
        resuming: bool = False,
        session_id: str | None = None,
        model_id: str,
        max_turns: int,
        allowed_tools_str: str,
        use_sessions: bool = False,
    ) -> list[str]:
        cmd = ["podman", "exec", container_name, "claude"]

        if resuming and session_id:
            cmd += ["--resume", session_id]

        cmd += ["-p", turn_prompt]

        if not resuming:
            cmd += ["--system-prompt-file", "/home/agent/system-prompt.md"]
            if session_id:
                cmd += ["--session-id", session_id]

        cmd += [
            "--mcp-config",
            "/home/agent/.mcp-config.json",
            "--allowedTools",
            allowed_tools_str,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model_id,
            "--max-turns",
            str(max_turns),
            "--permission-mode",
            "dontAsk",
        ]
        return cmd

    def parse_output(self, stdout: str) -> tuple[str, str, dict]:
        response_text = ""
        thinking_parts: list[str] = []
        metadata: dict = {}

        if not stdout.strip():
            return response_text, "", metadata

        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            obj_type = obj.get("type", "")
            if obj_type == "result":
                response_text = obj.get("result", "")
                metadata["num_turns"] = obj.get("num_turns", 0)
                metadata["total_cost_usd"] = obj.get("total_cost_usd", 0)
                metadata["usage"] = obj.get("usage", {})
            elif obj_type == "assistant":
                msg = obj.get("message", {})
                for block in msg.get("content", []):
                    if isinstance(block, dict):
                        if block.get("type") == "thinking" and block.get("thinking"):
                            thinking_parts.append(block["thinking"])

        thinking_text = "\n\n".join(thinking_parts)

        summary_parts = []
        if metadata.get("num_turns"):
            summary_parts.append(f"{metadata['num_turns']} tool call(s)")
        out_tokens = metadata.get("usage", {}).get("output_tokens", 0)
        if out_tokens:
            summary_parts.append(f"{out_tokens} output tokens")
        cost = metadata.get("total_cost_usd", 0)
        if cost:
            summary_parts.append(f"${cost:.4f}")
        if summary_parts:
            thinking_text = (
                (thinking_text + "\n\n---\n" + ", ".join(summary_parts)) if thinking_text else ", ".join(summary_parts)
            )

        return response_text, thinking_text, metadata

    def generate_config_files(
        self,
        persona_key: str,
        system_prompt: str,
        mcp_host: str,
        mcp_port: int,
        tmp_dir: Path,
    ) -> dict[str, Path]:
        mcp_config = {
            "mcpServers": {
                "sim": {
                    "type": "sse",
                    "url": f"http://{mcp_host}:{mcp_port}/agents/{persona_key}/sse",
                }
            }
        }
        config_path = tmp_dir / f"mcp-config-{persona_key}.json"
        config_path.write_text(json.dumps(mcp_config, indent=2))

        prompt_path = tmp_dir / f"system-prompt-{persona_key}.md"
        prompt_path.write_text(system_prompt)

        return {"mcp_config": config_path, "system_prompt": prompt_path}

    def get_volume_mounts(self, config_files: dict[str, Path]) -> list[str]:
        mounts: list[str] = []
        if "mcp_config" in config_files:
            mounts += ["-v", f"{config_files['mcp_config'].resolve()}:/home/agent/.mcp-config.json:ro,Z"]
        if "system_prompt" in config_files:
            mounts += ["-v", f"{config_files['system_prompt'].resolve()}:/home/agent/system-prompt.md:ro,Z"]
        return mounts

    def get_credential_sources(self) -> list[tuple[str, str]]:
        gcp_paths = [
            Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
        ]
        for p in gcp_paths:
            if p.is_file():
                return [(str(p), "/home/agent/.config/gcloud/application_default_credentials.json")]
        return []


class CodexBackend:
    """OpenAI Codex CLI backend."""

    def build_exec_command(
        self,
        container_name: str,
        turn_prompt: str,
        *,
        resuming: bool = False,
        session_id: str | None = None,
        model_id: str,
        max_turns: int,
        allowed_tools_str: str,
        use_sessions: bool = False,
    ) -> list[str]:
        cmd = ["podman", "exec", container_name, "codex", "exec"]

        if resuming and session_id:
            cmd += ["resume", session_id]

        cmd += [
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            model_id,
        ]

        if not use_sessions:
            cmd.append("--ephemeral")

        if not resuming:
            cmd += ["-s", "danger-full-access", "--color", "never"]

        cmd.append(turn_prompt)
        return cmd

    def parse_output(self, stdout: str) -> tuple[str, str, dict]:
        response_parts: list[str] = []
        metadata: dict = {}

        if not stdout.strip():
            return "", "", metadata

        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            obj_type = obj.get("type", "")
            if obj_type == "thread.started":
                metadata["thread_id"] = obj.get("thread_id", "")
            elif obj_type == "item.completed":
                item = obj.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        response_parts.append(text)
            elif obj_type == "turn.completed":
                metadata["usage"] = obj.get("usage", {})

        response_text = "\n".join(response_parts)

        summary_parts = []
        usage = metadata.get("usage", {})
        out_tokens = usage.get("output_tokens", 0)
        if out_tokens:
            summary_parts.append(f"{out_tokens} output tokens")
        reasoning = usage.get("reasoning_output_tokens", 0)
        if reasoning:
            summary_parts.append(f"{reasoning} reasoning tokens")
        thinking_text = ", ".join(summary_parts) if summary_parts else ""

        return response_text, thinking_text, metadata

    def generate_config_files(
        self,
        persona_key: str,
        system_prompt: str,
        mcp_host: str,
        mcp_port: int,
        tmp_dir: Path,
    ) -> dict[str, Path]:
        escaped = system_prompt.replace("\\", "\\\\").replace('"', '\\"')
        lines = [
            f'instructions = """\n{escaped}\n"""',
            "",
            "[mcp_servers.sim]",
            f'url = "http://{mcp_host}:{mcp_port}/agents-http/{persona_key}/mcp"',
        ]
        config_path = tmp_dir / f"codex-config-{persona_key}.toml"
        config_path.write_text("\n".join(lines))
        return {"codex_config": config_path}

    def get_volume_mounts(self, config_files: dict[str, Path]) -> list[str]:
        mounts: list[str] = []
        if "codex_config" in config_files:
            mounts += ["-v", f"{config_files['codex_config'].resolve()}:/home/agent/.codex/config.toml:ro,Z"]
        return mounts

    def get_credential_sources(self) -> list[tuple[str, str]]:
        codex_auth = Path.home() / ".codex" / "auth.json"
        if codex_auth.is_file():
            return [(str(codex_auth), "/home/agent/.codex/auth.json")]
        return []


class ModelscorpBackend:
    """Models.Corp backend — custom Python agent harness with OpenAI-compatible API."""

    def __init__(self) -> None:
        self._mcp_urls: dict[str, str] = {}
        self._allowed_tools: dict[str, list[str]] = {}

    def set_allowed_tools(self, persona_key: str, tools: list[str]) -> None:
        self._allowed_tools[persona_key] = tools

    def build_exec_command(
        self,
        container_name: str,
        turn_prompt: str,
        *,
        resuming: bool = False,
        session_id: str | None = None,
        model_id: str,
        max_turns: int,
        allowed_tools_str: str,
        use_sessions: bool = False,
    ) -> list[str]:
        persona_key = container_name.removeprefix("agent-")
        mcp_url = self._mcp_urls.get(persona_key, "")
        cmd = [
            "podman",
            "exec",
            container_name,
            "python",
            "/home/agent/modelscorp_agent.py",
            "--prompt",
            turn_prompt,
            "--system-prompt-file",
            "/home/agent/system-prompt.md",
            "--mcp-url",
            mcp_url,
            "--model",
            model_id,
            "--max-turns",
            str(max_turns),
            "--config",
            "/home/agent/.modelscorp.json",
        ]
        agent_tools = self._allowed_tools.get(persona_key)
        if agent_tools:
            cmd += ["--allowed-tools", ",".join(agent_tools)]
        if use_sessions and session_id:
            cmd += [
                "--memory-strategy",
                "fifo",
                "--session-file",
                f"/home/agent/sessions/{session_id}.jsonl",
                "--memory-max-messages",
                "50",
            ]
        return cmd

    def parse_output(self, stdout: str) -> tuple[str, str, dict]:
        response_text = ""
        metadata: dict = {}

        if not stdout.strip():
            return response_text, "", metadata

        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") == "result":
                response_text = obj.get("response_text", "")
                metadata["num_turns"] = obj.get("turns", 0)
                metadata["usage"] = {
                    "input_tokens": obj.get("input_tokens", 0),
                    "output_tokens": obj.get("output_tokens", 0),
                }
                if obj.get("error"):
                    metadata["error"] = obj["error"]

        summary_parts = []
        if metadata.get("num_turns"):
            summary_parts.append(f"{metadata['num_turns']} turn(s)")
        out_tokens = metadata.get("usage", {}).get("output_tokens", 0)
        if out_tokens:
            summary_parts.append(f"{out_tokens} output tokens")
        thinking_text = ", ".join(summary_parts) if summary_parts else ""

        return response_text, thinking_text, metadata

    def generate_config_files(
        self,
        persona_key: str,
        system_prompt: str,
        mcp_host: str,
        mcp_port: int,
        tmp_dir: Path,
    ) -> dict[str, Path]:
        prompt_path = tmp_dir / f"system-prompt-{persona_key}.md"
        prompt_path.write_text(system_prompt)

        project_config = Path(__file__).parent.parent / ".modelscorp.json"
        if project_config.is_file():
            staged = tmp_dir / f"modelscorp-config-{persona_key}.json"
            staged.write_text(project_config.read_text())
        else:
            staged = tmp_dir / f"modelscorp-config-{persona_key}.json"
            staged.write_text("{}")

        mcp_url = f"http://{mcp_host}:{mcp_port}/agents-http/{persona_key}/mcp"
        self._mcp_urls[persona_key] = mcp_url

        return {
            "system_prompt": prompt_path,
            "modelscorp_config": staged,
        }

    def get_volume_mounts(self, config_files: dict[str, Path]) -> list[str]:
        mounts: list[str] = []
        if "system_prompt" in config_files:
            mounts += ["-v", f"{config_files['system_prompt'].resolve()}:/home/agent/system-prompt.md:ro,Z"]
        if "modelscorp_config" in config_files:
            mounts += ["-v", f"{config_files['modelscorp_config'].resolve()}:/home/agent/.modelscorp.json:ro,Z"]
        return mounts

    def get_credential_sources(self) -> list[tuple[str, str]]:
        return []


def get_backend(agent_type: str) -> AgentBackend:
    """Factory for agent backends."""
    if agent_type == "codex":
        return CodexBackend()
    if agent_type == "modelscorp":
        return ModelscorpBackend()
    return ClaudeBackend()
