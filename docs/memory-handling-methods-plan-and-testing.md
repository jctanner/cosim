# Memory Handling Methods — Implementation Plan & Testing

## Current State

Memory lives in `container/conversation_memory.py` with two strategies:
- `none` (NoMemory) — stateless, each turn starts fresh
- `fifo` (FIFOMemory) — sliding window of last N messages, persisted as JSONL

The strategy is selected via `--memory-strategy` CLI arg in `modelscorp_agent.py`.

**Current default behavior:**
- `modelscorp_agent.py` CLI default: `--memory-strategy none` (line 425)
- `ModelscorpBackend.build_exec_command()` overrides to `fifo` only when `use_sessions=True` AND a `session_id` is provided (line 343-351 in `agent_backends.py`)
- Claude and Codex backends handle sessions natively (Claude `--resume`, Codex thread resume) — the `conversation_memory.py` module only applies to Models.Corp agents

No per-agent memory config exists in `scenario.yaml` yet.

Key files:
- `container/conversation_memory.py` — strategy implementations (runs inside container)
- `container/modelscorp_agent.py` — agent loop that calls `create_memory()`
- `lib/agent_backends.py` — passes `--memory-strategy` to the container
- `lib/scenario_loader.py` — loads persona config from scenario.yaml
- `lib/session.py` — session save/load (must preserve memory config)
- `lib/container_orchestrator.py` — hire/fire agent paths (must preserve memory config)

---

## Architecture: Composable Base + Mixins

Rather than 15 independent strategies, implement as composable layers:

```
┌─────────────────────────────────────┐
│         Token Budget Guard          │  ← outer wrapper, triggers compaction
├─────────────────────────────────────┤
│     Enrichment Mixins               │
│  ┌───────────┐                      │
│  │  Entity   │                      │  ← inject extra context (entity summaries,
│  │  Tracking │                      │    reflections) into the selected set
│  └───────────┘                      │
├─────────────────────────────────────┤
│   Outbound Normalization            │  ← strip _meta, enforce API schema
├─────────────────────────────────────┤
│   Base Strategy (one of) — owns     │
│   the full history and selects      │
│   which messages go to the LLM.     │
│   Integrates:                       │
│    • pinning (pre-selection)        │
│    • tool clearing (post-selection) │
│    • decay scoring (selection)      │
│  fifo | summary | summary-buffer   │
│  episodic | rag | reflexion         │
├─────────────────────────────────────┤
│         Persistence Layer           │
│  JSONL file (current) | SQLite      │
│  (always stores ALL messages —      │
│   including tool calls/results,     │
│   with _meta for timestamps etc.)   │
└─────────────────────────────────────┘
```

**Important architectural constraints:**

1. **Pinning, tool clearing, and decay scoring are NOT separate filters.** They are
   integrated into the base strategy's selection logic, because they affect *which*
   messages survive selection. A filter running after FIFO slicing would never see
   messages that were already evicted. Pinning and decay affect how the window is
   chosen; tool clearing transforms the chosen messages before output. See the
   Phase 1 sections below for specifics.

2. **The persistence layer always stores the full, unfiltered history** — including
   tool calls and tool results. Current `FIFOMemory.add_messages()` strips tool
   messages before storing; this must change so that strategies like tool result
   clearing and episodic memory have access to the raw record. The *selection* step
   (what goes to the LLM) is where filtering happens, not the *storage* step.

3. **`_meta` must never reach the LLM API.** Stored messages include a `_meta` field
   for timestamps, pin flags, importance scores, etc. The outbound normalization step
   strips `_meta` (and any other non-standard fields) from every message before
   `get_messages()` returns them. This is mandatory — OpenAI-compatible APIs reject
   unknown fields in chat messages. The normalization is a method on the base class
   (`ConversationMemory._normalize_for_api(messages)`) so all strategies inherit it.

4. **Tool clearing must preserve valid message ordering.** OpenAI-compatible chat APIs
   require tool result messages to immediately follow their paired assistant tool_calls
   message. Clearing must always drop or keep tool-call/result pairs together as a unit
   — never drop one and keep the other. See Phase 1d for the exact rules.

### Config surface (scenario.yaml)

```yaml
characters:
  rocky:
    # ... existing fields ...
    memory:
      strategy: "summary-buffer"       # base strategy
      max_messages: 50                  # FIFO/buffer window size
      max_summary_tokens: 500          # summary budget
      pin_patterns: ["Scenario Director"]  # never evict messages matching these senders
      clear_tool_results: true         # strip old tool call/result pairs
      decay_halflife_hours: 4.0        # forgetting curve halflife
      entity_tracking: false           # extract and track entities
```

### Default behavior rules

There are two distinct defaults:

1. **Factory default** (no config at all): `none` (NoMemory). A Models.Corp agent
   invoked without `--memory-config` or `--memory-strategy` is stateless. This
   preserves backward compatibility and is the correct behavior for one-shot or
   test invocations.

2. **Orchestrator default** (sessions enabled, no per-agent memory config in
   scenario.yaml): `{"strategy": "fifo", "max_messages": 50}`. When `use_sessions=True`
   and a `session_id` is assigned, `ModelscorpBackend` injects this default config
   so that agents get conversational continuity without requiring explicit config.

3. **Explicit per-agent config** (scenario.yaml `memory:` block): overrides both
   defaults. The orchestrator passes the config verbatim; the factory constructs
   whatever is specified.

The rule: **explicit config > orchestrator session default > factory stateless default.**

### Wiring changes

1. `scenario_loader.py` — load `memory` dict from character config, store in persona dict
2. `lib/session.py` — include `memory` in roster save (`_get_roster()` line 108-119)
   and restore (`load_session()` line 326-334). Both currently enumerate a fixed set
   of persona fields; `memory`, `allowed_tools`, and `fallback_channel` must be added.
3. `lib/container_orchestrator.py` — include `memory` in the persona dict constructed
   during `add_agent` (hire) at line 852-859. Currently copies `name`, `display_name`,
   `team_description`, `character_file`, `agent_type`, `model` — must also copy
   `memory`, `allowed_tools`, `fallback_channel`.
4. `agent_backends.py` — the `ModelscorpBackend` needs access to per-agent memory
   config, but `build_exec_command()` currently has no `memory_config` parameter
   (and neither does the `AgentBackend` protocol). Two options:
   - **Option A: `set_memory_config(persona_key, config)` method** — mirrors the
     existing `set_allowed_tools()` pattern. Called during pool setup and hire flows.
     `build_exec_command()` reads from `self._memory_configs[persona_key]`.
   - **Option B: add `memory_config: dict` param to `build_exec_command()`** — passed
     directly from the orchestrator, which reads it from the persona dict.
   Option A is preferred because it matches the existing `set_allowed_tools()` pattern
   and avoids changing the protocol signature (Claude/Codex backends ignore it).
   The backend serializes the config as `--memory-config '{...}'` JSON on the command
   line. When `use_sessions=True` and no explicit config is set, it injects the
   orchestrator default (`fifo`/50). The orchestrator call site
   (`container_orchestrator.py` line 632) needs to call `set_memory_config()` during
   pool setup alongside the existing `set_allowed_tools()` call.
5. `modelscorp_agent.py` — accept `--memory-config` JSON, pass to `create_memory()`.
   Keep `--memory-strategy` and `--memory-max-messages` as deprecated fallbacks that
   convert to config dict internally.
6. `conversation_memory.py` — factory `create_memory()` accepts a config dict. New
   signature: `create_memory(config: dict, session_file: str, system_prompt: str,
   llm_client=None, llm_model: str = "") -> ConversationMemory`. The `llm_client`
   and `llm_model` params are only required for strategies that need LLM calls
   (summary, entity, episodic, reflexion). Passed from `modelscorp_agent.py`'s
   existing `OpenAI` client instance and model ID — no new client construction needed.

---

## Implementation Phases

### Phase 0: Extract agent harness into an installable package

Currently `modelscorp_agent.py` and `conversation_memory.py` live in `container/`
and are COPYed as loose files into the agent container image. This is fragile:
- No way to import them from host-side code (tests, utilities)
- No dependency management — `openai` and `httpx` are pip-installed separately
  in the Dockerfile
- Adding new modules (memory strategies, entity extractors) means adding more
  COPY lines and ensuring the import graph works inside the container
- Can't run tests against them without path hacks

**Extract into `cosim_agent/` package at the project root:**

```
cosim_agent/
  __init__.py
  agent.py              ← current modelscorp_agent.py (the agent loop + CLI)
  memory/
    __init__.py          ← create_memory() factory, ConversationMemory base class
    fifo.py              ← FIFOMemory
    none.py              ← NoMemory
    summary.py           ← (Phase 1e) SummaryMemory
    summary_buffer.py    ← (Phase 1f) SummaryBufferMemory
    ...                  ← future strategies get their own modules
  mcp_client.py          ← MCPClient class (extracted from agent.py)
```

**Package configuration** — add a second `[project]` section or make it a
sub-package of the existing project. Simplest approach: add `cosim_agent` to
`pyproject.toml`'s `[tool.setuptools.packages.find]`:

```toml
[tool.setuptools.packages.find]
include = ["lib*", "cosim_agent*"]
```

The package's dependencies (`openai`, `httpx`) are already in the top-level
`pyproject.toml` deps. No separate `pyproject.toml` needed.

**Dockerfile changes:**

```dockerfile
# Before (loose file copies):
COPY modelscorp_agent.py /home/agent/modelscorp_agent.py
COPY conversation_memory.py /home/agent/conversation_memory.py

# After (pip install from project root):
COPY cosim_agent/ /tmp/cosim_agent/
COPY pyproject.toml /tmp/pyproject.toml
RUN pip install --no-cache-dir /tmp/ && rm -rf /tmp/cosim_agent /tmp/pyproject.toml
```

Alternatively, to avoid copying the full project into the build context, create a
minimal `cosim_agent/pyproject.toml` that only declares the agent package:

```toml
[project]
name = "cosim-agent"
version = "0.1.0"
dependencies = ["openai", "httpx"]

[tool.setuptools.packages.find]
include = ["cosim_agent*"]
```

Then the Dockerfile becomes:
```dockerfile
COPY cosim_agent/ /tmp/cosim_agent/cosim_agent/
COPY cosim_agent/pyproject.toml /tmp/cosim_agent/pyproject.toml
RUN pip install --no-cache-dir /tmp/cosim_agent && rm -rf /tmp/cosim_agent
```

**Entry point** — add a console script so the agent can be invoked as
`cosim-agent` instead of `python /home/agent/modelscorp_agent.py`:

```toml
[project.scripts]
cosim-agent = "cosim_agent.agent:main"
```

**Backend command change** — `ModelscorpBackend.build_exec_command()` changes from:
```python
cmd = ["podman", "exec", container_name, "python", "/home/agent/modelscorp_agent.py", ...]
```
to:
```python
cmd = ["podman", "exec", container_name, "cosim-agent", ...]
```

**Import path changes:**
- `from conversation_memory import create_memory` → `from cosim_agent.memory import create_memory`
- Internal imports within the package use relative imports

**Makefile `build-agent` target** — the build context must include `cosim_agent/`
and its `pyproject.toml`. Either change the build context to the project root:
```makefile
build-agent:
	podman build -f container/Dockerfile.agent -t agent-image:latest .
```
Or use a multi-context build / copy the package into `container/` as a build step.

**Testing benefit** — once `cosim_agent` is a proper package, host-side tests can
simply `import cosim_agent.memory` and test strategies directly without container
builds. The `tests/test_conversation_memory.py` tests all run on the host.

**Lint** — add `cosim_agent/` to the ruff targets in the Makefile:
```makefile
lint:
	ruff check lib/ cosim_agent/ tests/ main.py
	ruff format --check lib/ cosim_agent/ tests/ main.py
```

### Phase 1: Foundation (composable architecture + 3 base strategies)

**1a. Refactor config plumbing**
- Add `memory` dict to scenario.yaml character schema
- Load through `scenario_loader.py` into persona dict
- Pass as `--memory-config '{...}'` JSON blob instead of individual flags
- Backward compat: if `--memory-strategy` is passed (old style), convert to config dict

**1b. Persistence layer: store everything + outbound normalization**
- Change `FIFOMemory.add_messages()` to store ALL messages including tool calls and
  tool results. Currently it strips them (lines 96-103 of `conversation_memory.py`).
  The raw JSONL becomes the source of truth for the full conversation.
- **Persist the user turn prompt.** Currently `modelscorp_agent.py` sets
  `turn_start_idx = len(messages)` after `memory.get_messages(prompt)` at line 246,
  then only saves `messages[turn_start_idx:]` at line 395. This excludes the user
  prompt message that `get_messages()` appended. Fix: either save
  `messages[turn_start_idx - 1:]` (to include the user message), or better, add a
  `memory.add_user_message(prompt)` call before the agent loop that explicitly
  persists the user turn. This ensures the JSONL contains the complete conversation
  (user prompts + assistant responses + tool interactions), not just the model's
  output.
- The `get_messages()` method (selection step) is where filtering happens — each
  strategy decides what to send to the LLM. Current FIFO behavior (stripping tool
  messages from API context) moves from `add_messages()` to `get_messages()`.
- Add a `_meta` field to stored messages for timestamps, importance scores, pin flags,
  and turn numbers. Stored in JSONL alongside the message:
  `{"role": "...", "content": "...", "_meta": {"turn": 3, "ts": 1716000000, "pinned": false}}`
- Old session files without `_meta` load fine — missing meta treated as defaults.
- **Outbound normalization (mandatory).** Add `_normalize_for_api(messages)` to the
  `ConversationMemory` base class. Called as the final step of every `get_messages()`
  implementation. Strips `_meta` and any other non-standard fields from each message
  dict, returning only fields valid for the OpenAI chat completions API (`role`,
  `content`, `tool_calls`, `tool_call_id`, `name`). This prevents API rejections
  from backends that validate message schema strictly.
- `modelscorp_agent.py` currently passes `messages` directly to
  `client.chat.completions.create()` at line 257. After this change, the messages
  returned by `get_messages()` are already normalized — no change needed in the
  agent loop.

**1c. Pinned messages**
- NOT a post-selection filter. Pinning is part of the base strategy's selection logic.
- When `pin_patterns` is configured, `add_messages()` marks matching messages with
  `_meta.pinned = true` at storage time.
- **Match criteria — content-based only.** Current stored messages have only `role`
  and `content` fields (see `conversation_memory.py` line 86). There is no `sender`
  or `source` field — sender names only appear embedded in message content (e.g.,
  the turn prompt includes "- Human (Scenario Director): ..."). Therefore pinning
  matches against `content` via substring or regex, not against a structured sender
  field. If a structured `_meta.source` field is added later (Phase 1b could include
  this), pinning can additionally match on it, but content matching is the v1
  mechanism.
- Config example: `pin_patterns: ["Scenario Director", "CRITICAL"]` — any message
  whose content contains either substring is pinned.
- In `get_messages()`, the strategy collects pinned messages FIRST (from the full
  history), then fills the remaining budget with the normal selection (FIFO tail,
  summary, etc.). Pinned messages are inserted in chronological order after the
  system prompt and before the recent window.
- Output: `[system, ...pinned, ...recent_window, user_prompt]`
- Pinned messages count against the token budget (if a budget guard is active).

**1d. Tool result clearing**
- Applied during `get_messages()` as a post-selection transformation — it modifies
  the selected messages, not the stored history.
- Requires the persistence layer to store tool messages (Phase 1b).
- **Interaction with FIFO tool stripping:** By default, FIFO's `get_messages()` strips
  all tool messages from the API context (the behavior moved from `add_messages()` in
  Phase 1b). When `clear_tool_results: true` is configured, this changes: FIFO
  *includes* tool messages in the selected window instead of stripping them, and the
  clearing step then scrubs old ones. This means `clear_tool_results` is a mode switch
  that changes FIFO's selection behavior:
  - `clear_tool_results: false` (default) → strip all tool messages from context
    (current behavior, backward compatible)
  - `clear_tool_results: true` → include tool messages but scrub old pairs
- **Pair-wise clearing rule.** OpenAI-compatible APIs require tool result messages
  to immediately follow their paired assistant `tool_calls` message. Clearing always
  operates on complete pairs (assistant-with-tool_calls + all its tool results):
  - **Old pairs** (older than `clear_tool_results_after` turns): drop the entire pair
    — both the assistant tool_calls message and all its tool result messages. If the
    assistant message also contains text `content`, keep only the text content and
    remove the `tool_calls` field.
  - **Recent pairs** (within threshold): keep verbatim.
  - Never drop a tool result while keeping its paired assistant call, or vice versa.
- Config: `clear_tool_results: true`, `clear_tool_results_after: 3` (turns)

**1e. Summary strategy**
- `SummaryMemory` — after each turn, call the LLM to update a running summary
- Receives the `OpenAI` client and model ID from the harness via the factory
  (`create_memory(config, ..., llm_client=client, llm_model=model_id)`). No new
  client construction — reuses the same client and endpoint the agent uses for
  its main completions.
- Summary stored alongside raw JSONL as a separate file (`sessions/{id}_summary.txt`).
  Raw JSONL is the source of truth; summary is derived and can be regenerated.
- Config: `max_summary_tokens` controls summary length
- If the LLM summarization call fails (network error, rate limit), falls back to
  truncated FIFO behavior for that turn and retries summarization next turn.

**1f. Summary + Buffer Hybrid**
- `SummaryBufferMemory` — FIFO window for recent messages, LLM summary for evicted ones
- When buffer exceeds `max_messages`, evicted messages are summarized and prepended
- Uses the same LLM client plumbing as SummaryMemory (Phase 1e)
- Returns: `[system, ...pinned, summary_msg, ...recent_buffer, user_prompt]`
- Pinned messages (if configured) appear before the summary, since they represent
  high-priority context that should not be summarized away

### Phase 2: Advanced strategies

**2a. Forgetting Curves**
- Integrated into the base strategy's selection logic (NOT a post-selection mixin).
  Like pinning, decay affects *which* messages are selected from the full history,
  so it must run before the window is chosen — not after.
- Uses `_meta.ts` (timestamp) and `_meta.access_count` from stored messages.
- Replaces strict FIFO ordering with score-based selection:
  `score = importance * exp(-lambda * age_hours)` where
  `lambda = ln(2) / decay_halflife_hours`.
- Selection: from the full history, score all messages, select the top N by score
  (instead of the last N by position). Pinned messages bypass scoring.
- This is implemented as a `DecayScoredMemory` base strategy (or as a config option
  on FIFO: `decay_halflife_hours` switches FIFO from positional to scored selection).
- Config: `decay_halflife_hours: 4.0`

**2b. Entity Memory**
- `EntityTracker` mixin — after each turn, extract entities via LLM call
- Store entity summaries in a separate JSON file (`sessions/{id}_entities.json`)
- Inject relevant entity summaries into context alongside conversation history
- Config: `entity_tracking: true`

**2c. Episodic + Semantic**
- `EpisodicMemory` — stores all messages with timestamps and importance scores
- Retrieval: weighted combination of recency, importance, and relevance (cosine sim on text)
- Periodic reflection: every N turns, LLM generates high-level "reflections" stored separately
- Config: `reflection_interval: 10` (turns between reflections)

**2d. Reflexion**
- `ReflexionMemory` — after each turn, LLM generates self-critique
- Store last N reflections, inject into next turn's prompt
- Config: `max_reflections: 3`

### Phase 3: Infrastructure-dependent strategies

**3a. RAG / Embedding Retrieval**
- Requires an embedding endpoint (check if Models.Corp serves one)
- Simple file-based vector store using numpy (no external DB)
- `RAGMemory` — embed all messages, retrieve top-K by cosine similarity at query time
- Fallback: if no embedding endpoint, use BM25 (keyword matching) as a pure-Python alternative
- Config: `rag_top_k: 10`

**3b. Token Budget Guard**
- Wraps any base strategy
- Counts tokens (tiktoken for OpenAI-compatible, or char-based heuristic for others)
- When context exceeds threshold, triggers the base strategy's compaction
- Config: `max_context_tokens: 4000, compaction_threshold: 0.85`

**3c. Hierarchical Memory**
- Multi-tier storage: working (current turn), short-term (last few turns), long-term (summarized)
- Automatic promotion/demotion between tiers based on age and access patterns
- Config: `tiers: {short_term: 10, long_term: 50}`

---

## Testing Strategy

### Unit tests (`tests/test_conversation_memory.py`)

Each strategy and mixin gets isolated unit tests. No LLM calls — mock the OpenAI client for strategies that need summarization.

#### Test fixtures

```python
SAMPLE_MESSAGES = [
    {"role": "user", "content": "What's the status of the deployment?"},
    {"role": "assistant", "content": "Checking the logs now..."},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "get_messages", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "1", "content": "3 messages in #general..."},
    {"role": "assistant", "content": "The deployment looks healthy."},
    {"role": "user", "content": "Great, create a ticket for the follow-up."},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "2", "type": "function", "function": {"name": "create_ticket", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "2", "content": "TK-abc123 created"},
    {"role": "assistant", "content": "Created ticket TK-abc123."},
]

SYSTEM_PROMPT = "You are Rocky. You're a member of a small team."
```

#### NoMemory tests
- `test_no_memory_returns_system_and_user_only` — verify only [system, user] returned
- `test_no_memory_add_messages_is_noop` — verify messages are discarded
- `test_no_memory_save_load_is_noop` — verify no file I/O

#### FIFOMemory tests
- `test_fifo_returns_window` — add 20 messages, verify only last N returned by `get_messages()`
- `test_fifo_preserves_system_prompt` — system prompt always at position 0
- `test_fifo_user_prompt_always_last` — new user message always at end
- `test_fifo_persists_to_jsonl` — save, create new instance, load, verify state
- `test_fifo_stores_all_messages` — verify `add_messages()` stores tool calls and tool results in JSONL (Phase 1b change)
- `test_fifo_filters_tool_messages_in_selection` — verify `get_messages()` strips tool messages from API context (filtering moved from storage to selection)
- `test_fifo_handles_empty_session_file` — graceful handling of missing/empty file
- `test_fifo_max_messages_boundary` — exactly N messages, N+1 messages, 0 messages
- `test_fifo_old_session_file_compat` — session files without `_meta` fields load correctly
- `test_fifo_persists_user_message` — verify the user turn prompt is stored in JSONL (Phase 1b fix)

#### SummaryMemory tests
- `test_summary_calls_llm_for_compression` — mock OpenAI client, verify summarization prompt sent
- `test_summary_replaces_history_with_summary` — after summarization, only summary + recent messages in context
- `test_summary_preserves_raw_on_disk` — raw JSONL still contains all messages
- `test_summary_handles_llm_failure` — falls back to FIFO if summarization call fails
- `test_summary_max_tokens_respected` — summary stays within configured token budget

#### SummaryBufferMemory tests
- `test_hybrid_keeps_recent_verbatim` — messages within window are returned as-is
- `test_hybrid_summarizes_evicted` — messages outside window are summarized
- `test_hybrid_summary_prepended` — summary message appears after system prompt, before buffer
- `test_hybrid_boundary` — exactly at max_messages, no summarization triggered
- `test_hybrid_incremental_summary` — new evictions update existing summary, don't regenerate from scratch

#### Pinned messages tests
- `test_pinned_marked_at_storage_time` — `add_messages()` sets `_meta.pinned=true` on matching messages
- `test_pinned_survives_beyond_fifo_window` — add 30 messages with window=10, pinned message from turn 1 still in `get_messages()` output
- `test_pinned_by_content_substring` — pin by content substring (e.g., "Scenario Director" appearing in message content)
- `test_pinned_by_content_regex` — pin by content regex pattern match
- `test_pinned_ordering` — pinned messages appear after system prompt, before recent window, in chronological order
- `test_pinned_coexists_with_fifo` — output is `[system, ...pinned, ...recent_window, user_prompt]`
- `test_pinned_token_budget_interaction` — pinned messages count against token budget
- `test_pinned_not_duplicated` — a pinned message that is also in the recent FIFO window appears only once
- `test_pinned_no_meta_in_output` — pinned messages returned by `get_messages()` have `_meta` stripped

#### Tool result clearing tests
- `test_clearing_requires_full_persistence` — verify raw JSONL contains tool messages (Phase 1b prerequisite)
- `test_clearing_enables_tool_messages_in_context` — with `clear_tool_results: true`, FIFO includes tool messages (unlike default stripping behavior)
- `test_clearing_drops_old_pairs` — old tool-call/result pairs (beyond threshold) are dropped as a unit
- `test_clearing_keeps_text_from_mixed_assistant` — assistant message with both text and `tool_calls` keeps text, drops `tool_calls` field
- `test_clearing_preserves_recent_pairs` — tool pairs within `clear_tool_results_after` threshold kept verbatim
- `test_clearing_valid_message_ordering` — output never has a tool result without its preceding assistant tool_calls (validates API compatibility)
- `test_clearing_handles_orphaned_tool_results` — graceful with tool results whose paired call is missing (drop the orphan)
- `test_clearing_token_reduction` — verify measurable token reduction (>20%) on a tool-heavy conversation
- `test_clearing_false_strips_all_tool_messages` — with `clear_tool_results: false` (default), all tool messages are stripped (backward compat)
- `test_clearing_no_meta_in_output` — cleared messages returned by `get_messages()` have `_meta` stripped

#### Forgetting curves tests
- `test_decay_scores_recent_higher` — recent messages get higher scores
- `test_decay_selects_top_scored` — selection picks top N by score from full history, not last N by position
- `test_decay_access_reinforces` — accessed messages get score boost via `_meta.access_count`
- `test_decay_halflife_configurable` — different halflife values produce different selection sets
- `test_decay_pinned_bypass` — pinned messages always included regardless of score
- `test_decay_interleaved_old_new` — can select a mix of old high-scored and new messages (unlike FIFO which only selects contiguous tail)
- `test_decay_no_meta_in_output` — selected messages have `_meta` stripped

#### EntityMemory tests
- `test_entity_extraction` — mock LLM extracts entities from conversation
- `test_entity_summaries_injected` — relevant entity summaries appear in context
- `test_entity_summaries_updated` — new information about an entity updates its summary
- `test_entity_persistence` — entity store persists across turns
- `test_entity_extraction_failure` — graceful fallback if LLM extraction fails

#### EpisodicMemory tests
- `test_episodic_stores_with_timestamps` — each message gets a timestamp
- `test_episodic_retrieval_weighted` — retrieval uses recency + importance + relevance
- `test_episodic_reflection_generated` — after N turns, reflection is generated
- `test_episodic_reflections_injected` — reflections appear in context
- `test_episodic_importance_scoring` — important messages scored higher

#### ReflexionMemory tests
- `test_reflexion_generates_critique` — mock LLM generates self-critique after turn
- `test_reflexion_injects_past_reflections` — last N reflections in context
- `test_reflexion_window_truncates` — only last max_reflections kept
- `test_reflexion_failure_graceful` — if critique generation fails, turn proceeds normally

#### RAGMemory tests
- `test_rag_embeds_messages` — messages are embedded and stored
- `test_rag_retrieves_relevant` — top-K most similar messages returned
- `test_rag_bm25_fallback` — if no embedding endpoint, falls back to keyword matching
- `test_rag_persistence` — vector store persists to disk
- `test_rag_deduplication` — same message not stored twice

#### TokenBudgetGuard tests
- `test_budget_triggers_compaction` — when context exceeds threshold, compaction called
- `test_budget_no_compaction_under_threshold` — under threshold, no compaction
- `test_budget_char_heuristic` — character-based token estimation works
- `test_budget_tiktoken` — tiktoken counting works when available

#### Outbound normalization tests
- `test_normalize_strips_meta` — `_normalize_for_api()` removes `_meta` from all messages
- `test_normalize_preserves_standard_fields` — `role`, `content`, `tool_calls`, `tool_call_id`, `name` survive normalization
- `test_normalize_strips_unknown_fields` — any field not in the OpenAI schema is removed
- `test_normalize_handles_missing_fields` — messages without optional fields (no `content`, no `tool_calls`) normalize cleanly
- `test_normalize_called_by_all_strategies` — verify `get_messages()` output from every strategy contains no `_meta` fields (parametrized across all strategy types)

#### Factory tests
- `test_create_memory_empty_config` — `{}` returns NoMemory (factory default)
- `test_create_memory_fifo` — `{"strategy": "fifo"}` returns FIFOMemory
- `test_create_memory_with_pin_patterns` — config with `pin_patterns` returns strategy with pinning enabled
- `test_create_memory_unknown_strategy_raises` — unknown strategy name raises ValueError
- `test_create_memory_from_legacy_args` — old `--memory-strategy fifo` still works
- `test_create_memory_llm_client_required_for_summary` — `{"strategy": "summary"}` without `llm_client` raises ValueError
- `test_create_memory_llm_client_optional_for_fifo` — `{"strategy": "fifo"}` works without `llm_client`
- `test_orchestrator_default_when_sessions_enabled` — verify `ModelscorpBackend` injects `{"strategy": "fifo", "max_messages": 50}` when `use_sessions=True` and persona has no explicit memory config
- `test_explicit_config_overrides_orchestrator_default` — persona with `memory: {"strategy": "summary-buffer"}` is not overridden by the orchestrator session default

### Integration tests (live model, controlled scenario)

These run against a real (or mocked) MCP server and LLM endpoint. Slower, run separately.

#### Test harness setup

Create a minimal test scenario with 2 agents:
- Agent A: posts messages to a channel
- Agent B: uses the memory strategy under test

Script drives N turns of conversation, then verifies Agent B's behavior.

#### Integration test cases

**Memory persistence across turns:**
1. Run agent for 5 turns with `fifo` strategy
2. Kill and restart agent process
3. Verify agent recalls messages from turns 1-5

**Summary quality:**
1. Run agent for 20 turns with `summary-buffer` strategy (window=5)
2. Ask agent "what was discussed in the first few messages?"
3. Verify response references early content (via summary, not raw messages)

**Tool result clearing effectiveness:**
1. Run agent for 10 turns with heavy tool use
2. Measure token count of context with and without tool result clearing
3. Verify >30% token reduction with clearing enabled

**Forgetting curve behavior:**
1. Run agent for 20 turns
2. Ask about a message from turn 1 (should be faded)
3. Ask about a message from turn 18 (should be vivid)
4. Verify responses reflect recency weighting

**Entity tracking accuracy:**
1. Discuss 3 distinct entities across 10 turns
2. Verify entity store contains all 3 with accurate summaries
3. Ask agent about entity from turn 2 — verify it recalls via entity memory

**Pinned message survival:**
1. Configure pin for "Scenario Director" messages
2. Run 30 turns with a Scenario Director message at turn 1
3. Verify the pinned message is still in context at turn 30

### Comparative benchmarking

Run the same 20-turn conversation script against all strategies and measure:

| Metric | How to measure |
|--------|---------------|
| Token usage per turn | Sum `input_tokens` from harness output |
| Total cost | Sum across all turns |
| Recall accuracy | Ask 5 factual questions about earlier turns, score 0-5 |
| Latency per turn | Wall clock time from harness output |
| Context size growth | Track message array length per turn |
| Persistence size | Measure session file size on disk |

Output as a comparison table per strategy. Run on at least 2 models (one strong like Qwen 3-14B, one weak like Granite Micro) to see how model capability interacts with strategy.

### Regression tests

After any memory strategy change, verify:
- [ ] `NoMemory` still works (stateless agents unaffected)
- [ ] `FIFOMemory` API-context behavior unchanged for agents with no explicit memory config (same messages sent to LLM; persistence size/content will change due to storing tool messages)
- [ ] Session files from old format (no `_meta`, no tool messages) load correctly
- [ ] Agent loop completes without error for each strategy
- [ ] `signal_done` still detected correctly (memory doesn't interfere with loop control)
- [ ] Fallback channel still fires when needed
- [ ] Memory config absent in scenario.yaml defaults to current behavior
- [ ] Session save/load preserves `memory` config in roster (round-trip test)
- [ ] Hire/fire (add_agent) preserves `memory` config on the new persona
- [ ] `--memory-strategy fifo` (legacy CLI) still works and behaves identically
- [ ] Factory default (no config) returns NoMemory, orchestrator default (sessions enabled) returns FIFO
- [ ] No `_meta` fields in any messages returned by `get_messages()` (outbound normalization)
- [ ] Tool clearing never produces orphaned tool results (valid message ordering)

---

## Implementation Order (recommended)

```
Phase 0:  Extract cosim_agent/ package            (prerequisite — enables testing + clean imports)
Phase 1a: Config plumbing refactor                (prerequisite for everything)
Phase 1b: Persistence: store all messages         (prerequisite for 1c, 1d, and Phase 2+)
Phase 1c: Pinned messages                         (high value, enables eval scenarios)
Phase 1d: Tool result clearing                    (high value, reduces token waste)
Phase 1e: SummaryMemory + LLM client plumbing     (unlocks long conversations)
Phase 1f: SummaryBufferMemory                     (best general-purpose strategy)
Phase 2a: ForgettingCurves                        (improves FIFO eviction quality)
Phase 2b: EntityMemory                            (useful for multi-agent tracking)
Phase 2c: EpisodicMemory + reflections            (Generative Agents-style)
Phase 2d: ReflexionMemory                         (self-improvement loop)
Phase 3a: RAGMemory                               (needs embedding endpoint)
Phase 3b: TokenBudgetGuard                        (wraps any strategy)
Phase 3c: HierarchicalMemory                      (most complex, do last)
```

Each phase should be independently deployable — a new strategy or mixin can be added, tested, and configured per-agent without affecting agents using other strategies.

---

## Risk & Constraints

- **LLM calls for memory management add cost.** Summary, entity, episodic, and reflexion strategies all require extra LLM calls per turn. For Models.Corp models behind a rate limiter, this could be a bottleneck. Mitigation: summarize every N turns instead of every turn, use a cheap/fast model for summarization.
- **LLM client plumbing for memory strategies.** `create_memory()` currently takes only `(strategy, session_file, system_prompt, **kwargs)`. Strategies that need LLM calls (summary, entity, episodic, reflexion) require access to the OpenAI client and model ID. The factory signature must be extended to accept `llm_client` and `llm_model` as optional params. These come from the same client instance that `modelscorp_agent.py` already constructs for the main completion calls — no new client construction or credential handling needed. Strategies that don't need LLM calls (fifo, none) ignore these params.
- **Weak models can't self-manage memory.** MemGPT-style paging is excluded because Granite Micro can't reliably call memory management functions. All memory logic must live in the harness, not in the model's reasoning.
- **Session save/load and hire/fire must preserve memory config.** Three code paths construct persona dicts with fixed field lists:
  - `lib/session.py:_get_roster()` (lines 108-119) — serializes persona to JSON for save
  - `lib/session.py:load_session()` (lines 326-334) — reconstructs persona dict from saved JSON
  - `lib/container_orchestrator.py` add_agent handler (lines 852-859) — constructs persona dict for hired agents
  All three must include `memory`, `allowed_tools`, and `fallback_channel`. A future-proof approach: instead of enumerating fields, copy the full persona dict and only override/validate specific keys. But at minimum, the three new fields must be added to all three locations in Phase 1a.
- **`_meta` leaking to API calls will cause hard failures.** If `_normalize_for_api()` is not called (or is bypassed), `_meta` fields will reach `client.chat.completions.create()` and some backends will reject the request with a schema validation error. This is the highest-risk failure mode of the `_meta` approach. Mitigation: the normalization method lives on the base class and is called as the final step of `get_messages()` in the base class implementation (template method pattern), so subclasses cannot accidentally skip it. Unit tests parametrized across all strategies verify no `_meta` in output.
- **Tool message ordering violations cause silent model confusion or hard API errors.** If clearing drops an assistant tool_calls message but keeps its tool results, or vice versa, the API may reject the request or the model may hallucinate. The pair-wise clearing rule (Phase 1d) prevents this, but any future code that manipulates tool messages must respect it. A validation assert in `_normalize_for_api()` can catch violations in development.
- **Persistence layer change is a breaking behavioral change.** Phase 1b changes `FIFOMemory.add_messages()` to store tool messages that were previously stripped. This increases session file size but is required for tool result clearing and other strategies. Old session files (without tool messages) remain loadable — they just have less data available for clearing/replay.
- **Session file format changes.** New strategies may need richer metadata than raw JSONL. The `_meta` field on stored messages provides forward-compatible extensibility — old files without `_meta` load fine with defaults. Use a versioned header in session files so old files can be migrated or read by new code.
- **Container image size.** Adding numpy (for RAG vector math) or tiktoken increases the image. Keep dependencies optional — import with try/except and fall back gracefully.
- **Testing LLM-dependent strategies.** Summary quality is nondeterministic. Integration tests should use fuzzy matching (keyword presence, not exact string comparison) and run multiple times to check stability.
