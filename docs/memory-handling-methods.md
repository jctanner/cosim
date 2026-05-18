# Memory Handling Methods for LLM Agent Systems

A survey of memory strategies used in LLM agent systems, with notes on relevance to multi-agent chat architectures where agents communicate via tool calls (MCP).

---

## 1. Sliding Window / FIFO

Retains only the most recent K messages in a first-in-first-out queue. Oldest messages are dropped when the window is full. No summarization or compression — data is simply discarded.

**Mechanics:** Fixed-size buffer. New message arrives, oldest evicted if at capacity.

**Pros:**
- Trivially simple, predictable token usage
- No LLM calls for memory management
- Zero latency overhead

**Cons:**
- Complete loss of early context
- No way to reference anything outside the window
- Performance degrades on tasks requiring long-range recall

**Used by:** LangChain `ConversationBufferWindowMemory`, MemGPT (innermost message buffer), most chat UIs implicitly.

**Multi-agent relevance:** Simple baseline for per-agent message history, but agents lose awareness of early coordination decisions. Insufficient alone for systems where agents must track agreements made many turns ago.

**Status in cosim:** Implemented as `FIFOMemory` in `container/conversation_memory.py`.

---

## 2. Summary / Compression

Uses an LLM to compress the full conversation history into a running summary. The summary replaces raw messages in the context window.

**Mechanics:** After each turn (or batch of turns), the LLM is prompted to update a running summary incorporating new information. Only the summary is injected into the prompt, not the raw history. The summary grows sublinearly compared to raw history.

**Pros:**
- Dramatically reduces token usage for long conversations
- Retains gist of entire history
- Bounded context growth regardless of conversation length

**Cons:**
- Lossy — details, exact quotes, and nuance are lost
- Summaries can drift or misrepresent over time (semantic drift)
- Requires additional LLM calls, adding latency and cost per turn

**Used by:** LangChain `ConversationSummaryMemory`, Claude Code's compaction system (triggers at ~95% of 200K window), Anthropic's compaction API.

**Multi-agent relevance:** Each agent could maintain its own summary, but summaries diverge across agents, leading to inconsistent shared understanding. Useful for reducing per-agent context cost in systems with many concurrent agents.

---

## 3. Summary + Buffer Hybrid

Combines a running summary of older messages with a raw buffer of the most recent K messages (or tokens up to a `max_token_limit`). Preserves full fidelity for recent context while retaining the gist of older exchanges.

**Mechanics:** Monitors the token count of the raw buffer. When it exceeds the limit, the oldest messages are summarized by an LLM and the summary replaces them. Recent messages remain verbatim.

**Pros:**
- Best of both worlds — recent detail plus long-range gist
- Configurable boundary between summarized and raw regions
- Most practical balance of cost and recall quality

**Cons:**
- Still lossy for older messages
- Boundary between "summarized" and "raw" is arbitrary
- Summary quality depends on the LLM used for compression

**Used by:** LangChain `ConversationSummaryBufferMemory`, Claude Code (preserves 5 most recently accessed files alongside compressed summary), Hermes Agent framework (structured summary template).

**Multi-agent relevance:** Strong candidate for per-agent memory. Each agent keeps full fidelity on recent messages (including messages from other agents via tool calls) while maintaining a summary of earlier coordination.

---

## 4. RAG / Embedding-Based Retrieval

Stores past interactions (or extracted facts) as vector embeddings in a database. At inference time, the current query is embedded and the most semantically similar memories are retrieved and injected into the prompt.

**Mechanics:** Each message or extracted fact is embedded and stored in a vector database (ChromaDB, Pinecone, pgvector). At query time, the current context is embedded and cosine similarity identifies the top-K relevant memories. Retrieved memories are inserted into the prompt.

**Pros:**
- Scales to very large memory stores
- Only contextually relevant memories consume tokens
- Works across sessions
- No information is ever truly "lost"

**Cons:**
- Retrieval quality is a bottleneck — conversational turns are interdependent, so cosine similarity on isolated chunks often misses contextually important episodes
- Temporal queries ("what happened last Monday") retrieve poorly
- Requires embedding infrastructure
- Does not learn from interactions (each query is independent)

**Used by:** CrewAI short-term memory (ChromaDB), Mem0 (multi-signal retrieval: semantic similarity + BM25 + entity matching), MongoDB + LangGraph for cross-session memory.

**Multi-agent relevance:** Enables shared memory stores where all agents can query a common vector database. However, agents may retrieve different subsets for the same event, leading to inconsistent recall. Works well as a "collective memory" when paired with access controls.

---

## 5. Entity Memory

Extracts and tracks named entities (people, places, organizations, concepts) mentioned in conversation, maintaining an evolving summary for each entity.

**Mechanics:** An LLM with a dedicated prompt extracts proper nouns from each turn. For each extracted entity, a separate LLM call generates or updates a summary stored in an entity store (in-memory, Redis, or SQLite). At inference, entities relevant to the current query are retrieved and their summaries injected into the prompt.

**Pros:**
- Tracks specific entities across long conversations
- Provides structured, queryable knowledge about key participants and topics
- Natural fit for systems with many named objects (tickets, repos, people)

**Cons:**
- Relies heavily on LLM extraction quality (may miss implicit entities or hallucinate)
- Per-turn LLM calls for extraction and summarization add cost
- Entity disambiguation is fragile (e.g., "the project" vs. "Project Alpha")

**Used by:** LangChain `ConversationEntityMemory`, Mem0's entity linking.

**Multi-agent relevance:** Highly relevant. In multi-agent chat, agents need to track who said what, who is responsible for what, and which entities (tickets, documents, repos) are being discussed. Entity memory could serve as a shared registry of known entities across agents.

---

## 6. Knowledge Graph Memory

Stores memory as a graph of entities (nodes) and their relationships (edges), enabling multi-hop reasoning and structured relationship queries.

**Mechanics:** An LLM extracts (subject, predicate, object) triples from conversation and writes them to a graph store. Retrieval traverses the graph from query-relevant entities, following edges to find related facts. Systems like Graphiti (by Zep AI) add temporal awareness for incremental real-time updates.

**Pros:**
- Explicitly models relationships between entities
- Supports multi-hop reasoning ("who reports to the person who created this ticket?")
- Temporal-aware graphs can track how relationships change over time

**Cons:**
- Schema design is challenging
- Graph construction requires reliable triple extraction (LLM-dependent, error-prone)
- Benchmarks show accuracy differences between graph-based and vector-based approaches are often not statistically significant, while vector-based is more efficient
- Storage overhead and "memory bloat" are concerns

**Used by:** LangChain `ConversationKGMemory`, Graphiti (Neo4j-backed, by Zep AI), MAGMA, G-Memory.

**Multi-agent relevance:** The "shared blackboard" pattern — a knowledge graph serves as collective memory accessible to all agents. Each agent reads/writes to the graph. Strong for organizations where relationship structure matters (org charts, dependency tracking, ticket assignment chains).

---

## 7. Episodic vs. Semantic Memory

Inspired by cognitive science. Episodic memory stores specific experiences as ordered sequences (what happened, when, where). Semantic memory stores generalized facts and knowledge abstracted from episodes.

**Mechanics:** In Generative Agents (Park et al., 2023), every observation enters an episodic "memory stream" with a timestamp and importance score. Retrieval uses a weighted combination:

```
score = alpha_recency * recency + alpha_importance * importance + alpha_relevance * relevance
```

Recency uses exponential decay (0.995/hour). Periodically, the agent synthesizes episodes into higher-level "reflections" (semantic memory). The CoALA framework formalizes this taxonomy, adding procedural memory (skills stored as code, as in Voyager's skill library).

**Pros:**
- Mirrors human cognition — intuitive and well-studied
- Episodic memory preserves narrative context and temporal flow (useful for audit trails)
- Semantic memory provides compact, generalized knowledge
- Ablation studies showed removing reflection eliminated emergent coordination behaviors

**Cons:**
- The consolidation step (episodes → semantic knowledge) requires periodic LLM-driven summarization, which is fragile
- Maintaining consistent scoring weights is challenging
- Procedural memory is domain-specific and hard to generalize

**Used by:** Generative Agents (Park et al., UIST 2023), Voyager (procedural/skill memory), CoALA framework, HiMem, Letta/MemGPT.

**Multi-agent relevance:** Directly applicable. Each agent in a multi-agent simulation should have its own episodic stream, with shared semantic reflections surfaced to the group. The Generative Agents architecture was specifically designed for multi-agent simulation.

---

## 8. MemGPT / Virtual Context Management (Paging)

Treats the LLM context window as RAM and external storage as disk, using OS-inspired virtual memory paging. The LLM itself manages what data is paged in and out via function calls.

**Mechanics:** Two-tier architecture:
- **Main context** (in-window): system instructions, working context "scratchpad," FIFO message queue
- **External context**: Recall Storage (searchable database of all past messages) and Archival Storage (vector-indexed long-term store)

The LLM autonomously decides what to keep, evict, or retrieve by calling memory management functions (`archival_memory_search`, `core_memory_append`, etc.). When token usage approaches a threshold, the system inserts an internal alert and the LLM decides what to compress or page out.

**Pros:**
- Provides the illusion of unbounded memory within a fixed context window
- Self-directed — the LLM manages its own memory
- Model and provider agnostic

**Cons:**
- Relies on the LLM making good memory management decisions (unreliable for smaller models)
- Each paging operation requires inference, adding latency and cost
- Complex to implement correctly

**Used by:** MemGPT (Packer et al., 2023), Letta framework (production MemGPT).

**Multi-agent relevance:** Each agent could run its own MemGPT-style memory manager. The archival storage could be shared across agents. The self-directed approach reduces the need for centralized memory management but makes coordination harder (each agent pages independently). Requires models capable of reliable function calling — not suitable for smaller models that struggle with tool use.

---

## 9. Reflexion / Self-Reflection Memory

Agents verbally reflect on task failures and store reflective text in an episodic memory buffer to improve performance on subsequent attempts. A form of "verbal reinforcement learning."

**Mechanics:** Three components:
- **Actor:** generates actions
- **Evaluator:** scores outcomes
- **Self-Reflection Model:** generates natural language feedback on failures

After each trial, the reflection is stored in a memory buffer (truncated to last 3 reflections). On the next attempt, past reflections are injected into the prompt, providing a "semantic gradient" for improvement. No weight updates — learning is purely through prompt augmentation.

**Pros:**
- Enables rapid improvement without fine-tuning
- Achieves 91% pass@1 on HumanEval (vs. 80% for GPT-4 baseline)
- Simple to implement — just store and inject text

**Cons:**
- Relies on accurate self-evaluation (hard for complex tasks)
- Limited to a sliding window of reflections (typically 3)
- Does not generalize across tasks — reflections are task-specific
- The frozen base model never actually learns

**Used by:** Reflexion (Shinn et al., NeurIPS 2023).

**Multi-agent relevance:** Agents could reflect on coordination failures ("I should have checked the #engineering channel before committing"). Shared reflections could serve as organizational learning. However, the current formulation is single-agent and task-specific.

---

## 10. Scratchpad / Working Memory

A designated region of the context window where the agent maintains structured notes, intermediate reasoning state, or task progress — distinct from conversation history.

**Mechanics:** A mutable section of the prompt that the agent reads and writes to via tool calls or structured output. MemGPT's "working context" partition is the canonical example. HiAgent chunks working memory by subgoals, summarizing action-observation pairs once subgoals complete (reducing context usage from 100% to 65% while doubling success rates).

**Pros:**
- Provides structured, persistent state within or alongside the context window
- Reduces redundancy in conversation history
- Agent controls what is retained vs. discarded

**Cons:**
- Requires the agent to reliably maintain the scratchpad (LLMs can forget to update it)
- Scratchpad content competes for tokens with other context
- Quality depends on the model's ability to self-organize

**Used by:** MemGPT/Letta (working context partition), Claude Code (structured note-taking, to-do lists), HiAgent, common in code-generation agents (plan files).

**Multi-agent relevance:** Each agent can maintain its own scratchpad tracking its current task, assigned tickets, and coordination state. A shared scratchpad (e.g., a shared document or wiki page) could serve as a lightweight coordination mechanism.

---

## 11. Hierarchical Memory (Short/Mid/Long-Term Tiers)

Organizes memory into multiple tiers of increasing abstraction and decreasing access frequency, mirroring cognitive models (Atkinson-Shiffrin) and OS memory hierarchies.

**Mechanics:** H-MEM (2025) uses four layers:
1. **Domain Layer** — broadest abstractions
2. **Category Layer** — topic groupings
3. **Memory Trace Layer** — individual memories
4. **Episode Layer** — finest-grained raw experiences

Each memory vector includes positional indices pointing to related sub-memories in adjacent layers. HiMem uses topic-aware segmentation to construct Episode Memory, then consolidates stable facts into dense Note Memory.

**Pros:**
- Different types of information stored at appropriate granularities
- Efficient retrieval (traverse from abstract to specific)
- Reduces context pollution

**Cons:**
- Complex to implement and maintain
- Schema/tier design requires upfront decisions
- Recent work shows simple retrieval can outperform complex hierarchies on some benchmarks

**Used by:** H-MEM (2025), HiMem (2026), CrewAI unified Memory (hierarchical scope tree), MemGPT's two-tier system.

**Multi-agent relevance:** Natural fit for organizations. Individual agents get personal short-term memory; teams share mid-term departmental memory; the organization shares long-term institutional knowledge.

---

## 12. Forgetting Curves / Decay

Applies time-based decay functions to memory importance, inspired by the Ebbinghaus forgetting curve. Memories that are not accessed or reinforced gradually lose priority and may be pruned.

**Mechanics:** Memory strength decreases based on time elapsed and is reinforced by access. Generative Agents use exponential decay (factor 0.995 per hour) for the recency component of retrieval scoring. "Active forgetting" (deliberately pruning irrelevant or stale memories) outperforms naive "add-all" approaches, which suffer from "catastrophic interference" where accumulated stale/incorrect memories degrade performance.

**Pros:**
- Prevents memory bloat
- Naturally prioritizes recent and frequently-accessed information
- More human-like memory behavior

**Cons:**
- Risk of forgetting critically important but infrequently accessed information
- Decay parameters require tuning
- Hard to recover from premature forgetting

**Used by:** MemoryBank (Ebbinghaus-inspired), Generative Agents (recency decay), Nemori (Free-Energy Principle-based calibration loops).

**Multi-agent relevance:** Different agents might need different decay rates — a support agent needs to remember recent customer issues; a strategic agent needs to remember decisions from weeks ago. Shared memories should decay more slowly than individual working notes.

---

## 13. Pinned / Priority Messages

Certain high-priority information is persistently kept in the active context window and never evicted, regardless of memory pressure. Analogous to "pinning" pages in OS virtual memory.

**Mechanics:** In MemGPT/Letta, the system prompt and "core memory" partitions are static — never paged out. Claude Code's compaction protects "head messages" (system prompt + first exchange) and preserves the 5 most recently accessed files. Priority can be assigned by importance scoring or by explicit user/system designation.

**Pros:**
- Guarantees critical information (identity, goals, key constraints) is always available
- Prevents catastrophic forgetting of mission-critical context

**Cons:**
- Pinned content permanently consumes token budget
- Over-pinning leads to context starvation for dynamic content
- No formal framework for deciding what to pin

**Used by:** MemGPT/Letta (core memory partition), Claude Code (protected head messages), most agent frameworks implicitly pin system prompts.

**Multi-agent relevance:** Each agent's persona prompt and role definition should be pinned. Shared organizational rules, current sprint goals, or active incident details could be pinned across all agents' contexts.

---

## 14. Token-Budget-Aware Management

Explicitly allocates the available token budget across competing concerns (system prompt, retrieved memories, conversation history, tool results) and triggers compression/eviction based on usage thresholds.

**Mechanics:** Claude Code triggers auto-compaction at ~95% of 200K tokens. LangChain's `ConversationSummaryBufferMemory` monitors token counts and triggers summarization when `max_token_limit` is exceeded. Research shows 23% performance degradation when context utilization exceeds 85% of maximum. The "lost-in-the-middle" problem (Liu et al., 2024) shows 30%+ accuracy drops for information placed in the middle of the context window.

**Pros:**
- Prevents context overflow
- Optimizes cost/performance tradeoff
- Addresses the structural "lost-in-the-middle" problem

**Cons:**
- Token budgeting is a zero-sum game (more retrieved documents = less conversation history)
- LLMs often fail to follow given token budgets, especially small ones ("Token Elasticity" phenomenon)
- Requires empirical tuning of thresholds per model

**Used by:** Claude Code (95% threshold), Anthropic compaction API, TALE framework.

**Multi-agent relevance:** In systems with many concurrent agents, token budget management directly affects infrastructure cost. Agents that use tools heavily (generating large tool outputs) need more aggressive compression than agents that primarily read.

---

## 15. Novel / Emerging Approaches (2024–2026)

### A-MEM (Agentic Memory)
Zettelkasten-inspired self-organizing memory. Each memory unit (note) is enriched with LLM-generated keywords, tags, contextual descriptions, and dynamically constructed links to related memories. New experiences retroactively refine existing notes. Scales to 1M memories with retrieval time increasing only from 0.31μs to 3.70μs. Doubles performance on complex multi-hop reasoning vs. baselines.

### Subagent Isolation / Multi-Agent Context Splitting
Instead of compressing one agent's context, split work across multiple agents with isolated context windows. Anthropic reports 90% improvement in task completion with multi-agent vs. single-agent architectures. Each subagent gets a fresh context window, eliminating the compression problem entirely at the cost of coordination overhead.

### Tool Result Clearing
Lightweight compaction that clears raw tool call results from deep in the message history, since the agent rarely needs to re-read them. No LLM calls needed — cheapest form of compaction.

### MemMachine (2026)
Ground-truth-preserving memory that stores raw conversational episodes rather than LLM-extracted facts. Achieves ~80% reduction in token usage vs. competing systems by reserving LLM calls only for summarization/abstraction.

### Reflective Memory Management (RMM)
Constructs memory at adaptive granularities (utterance, turn, session, or topic level) and refines retrieval using feedback from response citations, applying online reinforcement learning to rerank memory relevance.

### Portable Agent Memory (2025)
A protocol for provenance-verified memory transfer across heterogeneous LLM agents, enabling agents to share memories with verifiable lineage. Addresses the "memory silo" problem in multi-agent systems.

### Nemori
Autonomously segments conversational streams into semantically aligned episodes and continually updates semantic knowledge via active prediction-calibration loops based on the Free-Energy Principle from neuroscience.

### SSGM (Stability and Safety Governed Memory)
Addresses risks of evolving memory including silent cross-user contamination of shared stores, memory-induced sycophancy, and self-reinforcing error loops (confirmation bias).

---

## Applicability to cosim

Currently implemented: `none` (stateless) and `fifo` (sliding window). Both in `container/conversation_memory.py` with a pluggable `_STRATEGIES` registry.

The most promising strategies for cosim's multi-agent architecture, in rough priority order:

| Strategy | Complexity | Value | Notes |
|----------|-----------|-------|-------|
| Summary + Buffer Hybrid | Medium | High | Best next step — extend FIFO with summary of evicted messages |
| Pinned Messages | Low | High | Pin persona prompt + Scenario Director instructions in FIFO window |
| Tool Result Clearing | Low | Medium | Strip tool results from old turns before they enter the FIFO window |
| Entity Memory | Medium | Medium | Track agents, channels, tickets, docs mentioned across turns |
| Forgetting Curves | Low | Medium | Weight recent messages higher during FIFO eviction |
| Episodic + Semantic | High | High | Full Generative Agents-style architecture — high value but complex |
| MemGPT-style Paging | High | Medium | Requires models capable of reliable self-directed memory management — not suitable for smaller Models.Corp models |

Key constraint: cosim uses multiple LLM backends (Claude, Codex, Granite, Qwen, Llama, Gemini) with varying capabilities. Any memory strategy must work without relying on the model itself to manage memory (rules out MemGPT-style self-directed paging for weaker models). The memory management logic should live in the harness (`modelscorp_agent.py`), not in the model's own reasoning.

---

## References

- Park et al., "Generative Agents: Interactive Simulacra of Human Behavior" (UIST 2023)
- Packer et al., "MemGPT: Towards LLMs as Operating Systems" (2023)
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning" (NeurIPS 2023)
- Liu et al., "Lost in the Middle: How Language Models Use Long Contexts" (2024)
- LangChain memory module documentation
- CrewAI memory documentation
- Anthropic, "Effective Context Engineering for AI Agents" (2025)
- A-MEM: "Agentic Memory for LLM Agents" (NeurIPS 2025)
- H-MEM: Hierarchical memory architecture (2025)
- HiMem: Hierarchical episodic + note memory (2026)
- MemMachine: Ground-truth-preserving memory (2026)
- Portable Agent Memory protocol (2025)
