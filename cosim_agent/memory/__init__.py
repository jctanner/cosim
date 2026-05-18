"""Pluggable conversation memory for the Models.Corp agent harness.

Strategies control how prior conversation history is managed across turns.
Full history is always preserved on disk; the strategy only controls what
subset is sent to the LLM API.
"""

from __future__ import annotations

from cosim_agent.memory.base import ConversationMemory
from cosim_agent.memory.decay import DecayMemory
from cosim_agent.memory.entity import EntityMemory
from cosim_agent.memory.episodic import EpisodicMemory
from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.none import NoMemory
from cosim_agent.memory.reflexion import ReflexionMemory
from cosim_agent.memory.summary import SummaryMemory
from cosim_agent.memory.summary_buffer import SummaryBufferMemory

__all__ = [
    "ConversationMemory",
    "NoMemory",
    "FIFOMemory",
    "SummaryMemory",
    "SummaryBufferMemory",
    "DecayMemory",
    "EntityMemory",
    "EpisodicMemory",
    "ReflexionMemory",
    "create_memory",
]

_STRATEGIES: dict[str, type[ConversationMemory]] = {
    "none": NoMemory,
    "fifo": FIFOMemory,
    "summary": SummaryMemory,
    "summary-buffer": SummaryBufferMemory,
    "decay": DecayMemory,
    "entity": EntityMemory,
    "episodic": EpisodicMemory,
    "reflexion": ReflexionMemory,
}


def create_memory(
    config: dict,
    system_prompt: str,
    llm_client=None,
    llm_model: str = "",
) -> ConversationMemory:
    """Factory — create a memory instance from a config dict.

    Config keys:
        strategy: str — strategy name ("none", "fifo", "summary", "summary-buffer")
        session_file: str — path to JSONL session file
        max_messages: int — FIFO window size (default 50)
        ... future keys passed through as kwargs to the strategy

    The llm_client and llm_model parameters are passed through to strategies
    that need LLM access for summarization (summary, summary-buffer).
    """
    strategy = config.get("strategy", "none")
    session_file = config.get("session_file", "")
    cls = _STRATEGIES.get(strategy)
    if cls is None:
        raise ValueError(f"Unknown memory strategy '{strategy}'. Available: {list(_STRATEGIES)}")
    kwargs = {k: v for k, v in config.items() if k not in ("strategy", "session_file")}
    if llm_client is not None:
        kwargs["llm_client"] = llm_client
    if llm_model:
        kwargs["llm_model"] = llm_model
    return cls(session_file=session_file, system_prompt=system_prompt, **kwargs)
