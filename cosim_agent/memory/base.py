"""Base class for conversation memory strategies."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

_API_FIELDS = {"role", "content", "tool_calls", "tool_call_id", "name"}


class ConversationMemory(ABC):
    """Base class for conversation memory strategies."""

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        self._session_file = session_file
        self._system_prompt = system_prompt
        self._turn_counter = 0

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

    def _add_meta(self, msg: dict, **extra) -> dict:
        """Attach _meta to a message for storage. Returns the same dict, mutated."""
        meta = msg.get("_meta", {})
        meta.setdefault("ts", time.time())
        meta.setdefault("turn", self._turn_counter)
        meta.setdefault("pinned", False)
        meta.update(extra)
        msg["_meta"] = meta
        return msg

    @staticmethod
    def _normalize_for_api(messages: list[dict]) -> list[dict]:
        """Strip _meta and non-standard fields before sending to the LLM API."""
        normalized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in _API_FIELDS and v is not None}
            if "content" not in clean and "tool_calls" not in clean:
                clean["content"] = ""
            normalized.append(clean)
        return normalized
