# Plan: Models.Corp Backend via Custom Python Agent Harness

## Context

The cosim platform currently supports two agent backends — Claude Code CLI and Codex CLI — both running inside podman containers. We want to add a third backend that uses Red Hat's internal **Models.Corp** platform, which exposes OpenAI-compatible `/v1/chat/completions` endpoints for models like Granite 4.0, Qwen3-14B, Gemini, and Claude (proxied via Vertex AI).

Since Models.Corp is a REST API (no CLI tool), we'll write a lightweight Python agent harness that implements the agentic tool-calling loop. This runs inside the same podman containers, using the OpenAI Python SDK for LLM calls and raw JSON-RPC HTTP for MCP tool interaction.

Models.Corp uses **per-model API keys**, so the credential config maps each model to its key and endpoint.

## Files to Create

### 1. `container/modelscorp_agent.py` — Agent loop script (~250 lines)

Runs inside the container. Implements:

- **CLI args**: `--prompt`, `--system-prompt-file`, `--mcp-url`, `--model`, `--max-turns`, `--config`
- **MCP client** (raw JSON-RPC over HTTP POST to Streamable HTTP endpoint):
  - `initialize` → get session ID from `mcp-session-id` header
  - `tools/list` → discover available tools, convert to OpenAI function format
  - `tools/call` → execute tool calls from LLM responses
- **Agent loop**:
  1. Read system prompt + config
  2. Initialize MCP session, fetch tools
  3. Convert MCP tools → OpenAI `tools` parameter (JSON Schema mapping is 1:1)
  4. Call `client.chat.completions.create()` with messages + tools
  5. If response has `tool_calls`: execute each via MCP, append assistant message + tool results to messages, loop back to step 4
  6. If no tool_calls or max turns reached: output final result
  7. Handle `signal_done` tool calls normally (execute via MCP so orchestrator detects it)
- **JSONL output** (one JSON object per line to stdout):
  - `{"type": "turn", "turn": N, "content": "...", "tool_calls": [...]}` — per-turn trace
  - `{"type": "result", "response_text": "...", "turns": N, "input_tokens": N, "output_tokens": N}` — final summary
- **Error handling**: Retry on 429 (rate limit) with exponential backoff, fail on auth errors, log to stderr

### 2. `.modelscorp.json.example` — Example credential config

```json
{
  "endpoint_base": "YOUR_ENDPOINT_HOST",
  "endpoint_type": "staging",
  "models": {
    "granite-4-0-micro": {"key": "YOUR_KEY_HERE"},
    "granite-4-0-h-tiny": {"key": "YOUR_KEY_HERE"},
    "qwen3-14b": {"key": "YOUR_KEY_HERE"},
    "gemini": {"key": "YOUR_KEY_HERE", "endpoint_type": "production", "base_path": "/v1beta/openai"},
    "claude": {"key": "YOUR_KEY_HERE"}
  }
}
```

URL construction: `https://{model}--{endpoint_type}.{endpoint_base}:443{base_path}/v1`
(default `base_path` is empty, so path becomes `/v1`; some models may override the path)

## Files to Modify

### 3. `lib/agent_backends.py` — Add `ModelscorpBackend` class

Add a new class following the same `AgentBackend` protocol as `ClaudeBackend` and `CodexBackend`:

- **`build_exec_command()`**: `podman exec <container> python /home/agent/modelscorp_agent.py --prompt <turn_prompt> --system-prompt-file /home/agent/system-prompt.md --mcp-url http://{mcp_host}:{mcp_port}/agents-http/{key}/mcp --model <model_id> --max-turns <N> --config /home/agent/.modelscorp.json`
- **`parse_output()`**: Parse JSONL looking for `type: "result"` line → extract `response_text`, `turns`, token counts
- **`generate_config_files()`**: Create system-prompt.md (reuse Claude's approach). Read `.modelscorp.json` from project root and stage it as a config file.
- **`get_volume_mounts()`**: Mount system-prompt.md and modelscorp config as read-only
- **`get_credential_sources()`**: Return empty list (credentials are in the config file, mounted as a volume)

Update `get_backend()` factory to handle `agent_type == "modelscorp"`.

### 4. `lib/agent_runner.py` — Add model mappings

Add `_MODELSCORP_MODELS` dict mapping shorthands to Models.Corp model slugs:
```python
_MODELSCORP_MODELS = {
    "granite-micro": "granite-4-0-micro",
    "granite-tiny": "granite-4-0-h-tiny",
    "granite-small": "granite-4-0-h-small",
    "qwen3": "qwen3-14b",
    "gemini": "gemini",
    "claude": "claude",
}
```

Update `get_model_id()` to handle `agent_type == "modelscorp"`.

### 5. `lib/container_orchestrator.py` — No env var changes needed

Credentials are passed via the config file (volume mount), not env vars. No changes to `_FORWARD_ENV_VARS`.

### 6. `container/Dockerfile.agent` — Install Python dependencies

Add `pip install openai` (httpx comes as a transitive dependency). Copy the agent script:
```dockerfile
RUN pip install --no-cache-dir openai
COPY modelscorp_agent.py /home/agent/modelscorp_agent.py
```

### 7. `lib/cli.py` — Add "modelscorp" to agent type choices

Update `--default-agent-type` choices from `["claude", "codex"]` to `["claude", "codex", "modelscorp"]`.

### 8. `.gitignore` — Add `.modelscorp.json`

Ensure the credentials file is never committed.

## Architecture Notes

- **MCP transport**: Uses the same Streamable HTTP endpoint as Codex (`/agents-http/{key}/mcp`), speaking JSON-RPC 2.0 over HTTP POST. No new MCP server changes needed.
- **Tool calling**: Relies on model support for OpenAI-style function calling. Granite 4.0, Qwen3, Claude, and Gemini all support this. Models without tool calling support will get a clear error from the harness.
- **No sessions for MVP**: Skip session resume support initially. The orchestrator handles this gracefully (falls back to fresh prompts).
- **Container reuse**: Same long-running container pattern — `sleep infinity` with `podman exec` per turn.

## Verification

1. **Unit test the harness standalone**: Run `modelscorp_agent.py` outside a container against a Models.Corp endpoint with a simple prompt and no MCP tools to verify LLM communication works
2. **Test MCP integration**: Run with `--mcp-url` pointed at the running MCP server, verify tool discovery and execution
3. **End-to-end**: Configure a scenario with `agent_type: "modelscorp"` and `model: "granite-micro"` for one character, start the full stack (Flask + MCP + orchestrator), send a message, verify the agent responds through the simulation
4. **Mixed backends**: Run a scenario with claude, codex, and modelscorp agents in different tiers to verify they coexist
