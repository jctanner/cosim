"""SummaryBufferMemory — FIFO window + LLM summary for evicted messages."""

from __future__ import annotations

import json
import os

from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.summary import call_summarize, format_messages_for_summary


class SummaryBufferMemory(FIFOMemory):
    """FIFO window for recent messages, LLM summary for evicted ones.

    Inherits pinning, tool-clearing, and atomic tool-group windowing from
    FIFOMemory. When the filtered message count exceeds max_messages, evicted
    messages are incrementally summarized via an LLM call.

    Output structure:
        [system, ...pinned, summary_msg, ...recent_window, user_prompt]

    Config keys (in addition to FIFOMemory keys):
        llm_client: OpenAI — client instance for summarization calls
        llm_model: str — model ID for summarization calls
        max_summary_tokens: int — max_tokens for summarization (default 500)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._llm_client = kwargs.get("llm_client")
        self._llm_model: str = kwargs.get("llm_model", "")
        self._max_summary_tokens: int = kwargs.get("max_summary_tokens", 500)
        self._summary: str = ""
        self._summary_file: str = self._derive_summary_file()
        self._summarized_count: int = 0

    def _derive_summary_file(self) -> str:
        if not self._session_file:
            return ""
        base, _ = os.path.splitext(self._session_file)
        return f"{base}_summary.json"

    def load(self) -> None:
        super().load()

        if self._summary_file and os.path.isfile(self._summary_file):
            try:
                with open(self._summary_file) as f:
                    data = json.load(f)
                self._summary = data.get("summary", "")
                self._summarized_count = data.get("summarized_count", 0)
            except (json.JSONDecodeError, OSError):
                pass

    def _get_filtered(self) -> list[dict]:
        """Return the filtered message list (tool-stripped or tool-cleared)."""
        if self._clear_tool_results:
            return self._clear_old_tool_pairs(self._history)
        return self._strip_tool_traffic(self._history)

    def get_messages(self, new_user_message: str) -> list[dict]:
        filtered = self._get_filtered()
        units = self._group_into_units(filtered)

        if self._compiled_pins:
            pinned_idxs: set[int] = set()
            pinned_units: list[list[dict]] = []
            for i, unit in enumerate(units):
                if any(self._is_pinned(msg) for msg in unit):
                    pinned_units.append(unit)
                    pinned_idxs.add(i)

            window_candidates = [u for i, u in enumerate(units) if i not in pinned_idxs]
            window_units = self._take_trailing_units(window_candidates, self._max_messages)

            window_msg_ids = {id(msg) for unit in window_units for msg in unit}
            unique_pinned = [u for u in pinned_units if not all(id(msg) in window_msg_ids for msg in u)]

            pinned_msgs = [msg for unit in unique_pinned for msg in unit]
            window_msgs = [msg for unit in window_units for msg in unit]
        else:
            pinned_msgs = []
            window_units = self._take_trailing_units(units, self._max_messages)
            window_msgs = [msg for unit in window_units for msg in unit]

        assembled: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            *pinned_msgs,
        ]

        if self._summary:
            assembled.append(
                {
                    "role": "assistant",
                    "content": f"[Summary of earlier conversation]\n{self._summary}",
                }
            )

        assembled.extend(window_msgs)
        assembled.append({"role": "user", "content": new_user_message})

        return self._normalize_for_api(assembled)

    def _compute_evicted_messages(self) -> tuple[list[dict], int]:
        """Determine which non-pinned messages are evicted from the window.

        Uses the same unit-based selection and pinning logic as get_messages()
        so the summary covers exactly the messages that are dropped from context.

        Returns (evicted_messages, total_evicted_count).
        """
        filtered = self._get_filtered()
        units = self._group_into_units(filtered)

        if self._compiled_pins:
            non_pinned_units = [u for u in units if not any(self._is_pinned(msg) for msg in u)]
        else:
            non_pinned_units = list(units)

        window_units = self._take_trailing_units(non_pinned_units, self._max_messages)
        n_window = len(window_units)

        if n_window >= len(non_pinned_units):
            return [], 0

        evicted_units = non_pinned_units[: len(non_pinned_units) - n_window]
        evicted_msgs = [msg for unit in evicted_units for msg in unit]
        return evicted_msgs, len(evicted_msgs)

    def add_messages(self, messages: list[dict]) -> None:
        super().add_messages(messages)

        if not self._llm_client:
            return

        evicted_msgs, evicted_count = self._compute_evicted_messages()

        if evicted_count <= self._summarized_count:
            return

        new_evictions = evicted_msgs[self._summarized_count :]
        text = format_messages_for_summary(new_evictions)
        result = call_summarize(
            self._llm_client,
            self._llm_model,
            text,
            self._summary,
            self._max_summary_tokens,
        )
        if result is not None:
            self._summary = result
            self._summarized_count = evicted_count

    def save(self) -> None:
        super().save()

        if self._summary_file and (self._summary or self._summarized_count):
            os.makedirs(os.path.dirname(self._summary_file), exist_ok=True)
            with open(self._summary_file, "w") as f:
                json.dump(
                    {
                        "summary": self._summary,
                        "summarized_count": self._summarized_count,
                    },
                    f,
                    ensure_ascii=False,
                )
