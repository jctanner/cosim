"""Pluggable conversation memory for the Models.Corp agent harness.

Strategies control how prior conversation history is managed across turns.
Full history is always preserved on disk; the strategy only controls what
subset is sent to the LLM API.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod


class ConversationMemory(ABC):
    """Base class for conversation memory strategies."""

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        self._session_file = session_file
        self._system_prompt = system_prompt

    @abstractmethod
    def load(self) -> None:
        """Load prior history from disk."""

    @abstractmethod
    def get_messages(self, new_user_message: str) -> list[dict]:
        """Return the messages array for the API call."""

    @abstractmethod
    def add_messages(self, messages: list[dict]) -> None:
        """Append this turn's messages (assistant + tool results) to history."""

    @abstractmethod
    def save(self) -> None:
        """Persist history to disk."""


class NoMemory(ConversationMemory):
    """Stateless pass-through — each turn starts fresh (current default behavior)."""

    def load(self) -> None:
        pass

    def get_messages(self, new_user_message: str) -> list[dict]:
        return [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": new_user_message},
        ]

    def add_messages(self, messages: list[dict]) -> None:
        pass

    def save(self) -> None:
        pass


class FIFOMemory(ConversationMemory):
    """Pinned system prompt + sliding window of the last N messages.

    Full history is saved to disk as JSONL. Only the most recent
    ``max_messages`` are sent to the API (plus the system prompt,
    which is always pinned at position 0).
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._max_messages: int = kwargs.get("max_messages", 50)
        self._history: list[dict] = []

    def load(self) -> None:
        if not self._session_file or not os.path.isfile(self._session_file):
            return
        with open(self._session_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._history.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def get_messages(self, new_user_message: str) -> list[dict]:
        window = self._history[-self._max_messages:] if self._history else []
        return [
            {"role": "system", "content": self._system_prompt},
            *window,
            {"role": "user", "content": new_user_message},
        ]

    def add_messages(self, messages: list[dict]) -> None:
        self._history.extend(messages)

    def save(self) -> None:
        if not self._session_file:
            return
        os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
        with open(self._session_file, "w") as f:
            for msg in self._history:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")


_STRATEGIES: dict[str, type[ConversationMemory]] = {
    "none": NoMemory,
    "fifo": FIFOMemory,
}


def create_memory(
    strategy: str,
    session_file: str,
    system_prompt: str,
    **kwargs,
) -> ConversationMemory:
    """Factory — create a memory instance by strategy name."""
    cls = _STRATEGIES.get(strategy)
    if cls is None:
        raise ValueError(f"Unknown memory strategy '{strategy}'. Available: {list(_STRATEGIES)}")
    return cls(session_file=session_file, system_prompt=system_prompt, **kwargs)
