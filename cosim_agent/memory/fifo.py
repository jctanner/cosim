"""FIFOMemory — sliding window of the last N messages."""

from __future__ import annotations

import json
import os
import re

from cosim_agent.memory.base import ConversationMemory


class FIFOMemory(ConversationMemory):
    """Pinned system prompt + sliding window of the last N messages.

    Full history (including tool calls/results and _meta) is saved to disk
    as JSONL. Only a filtered subset of the most recent messages is sent
    to the API.

    Config keys (via kwargs):
        max_messages: int — window size (default 50)
        pin_patterns: list[str] — substring/regex patterns; matching messages
            are pinned and survive beyond the FIFO window.
            Only applies to standalone text messages — tool traffic
            (tool results and assistant tool-call messages) is never pinned.
        clear_tool_results: bool — if true, include tool messages in context
            but scrub old pairs (default false: strip all tool messages)
        clear_tool_results_after: int — turn threshold for clearing old pairs
            (default 3)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._max_messages: int = kwargs.get("max_messages", 50)
        self._pin_patterns: list[str] = kwargs.get("pin_patterns") or []
        self._clear_tool_results: bool = kwargs.get("clear_tool_results", False)
        self._clear_tool_results_after: int = kwargs.get("clear_tool_results_after", 3)
        self._history: list[dict] = []
        self._compiled_pins: list[re.Pattern] = [re.compile(p) for p in self._pin_patterns]

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
        if self._history:
            last_turn = max(m.get("_meta", {}).get("turn", 0) for m in self._history)
            self._turn_counter = last_turn

    def _is_pinned(self, msg: dict) -> bool:
        """Check if a message should be pinned.

        Tool traffic (tool results and assistant+tool_calls) is never pinned
        to avoid splitting tool-call groups.
        """
        role = msg.get("role", "")
        if role == "tool":
            return False
        if role == "assistant" and "tool_calls" in msg:
            return False
        if msg.get("_meta", {}).get("pinned"):
            return True
        content = msg.get("content", "")
        if not content or not self._compiled_pins:
            return False
        return any(p.search(content) for p in self._compiled_pins)

    def _strip_tool_traffic(self, messages: list[dict]) -> list[dict]:
        """Remove all tool messages and orphaned tool_calls (default mode)."""
        filtered = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "tool":
                continue
            if role == "assistant" and "tool_calls" in msg:
                if not msg.get("content"):
                    continue
                msg = {k: v for k, v in msg.items() if k != "tool_calls"}
            filtered.append(msg)
        return filtered

    def _clear_old_tool_pairs(self, messages: list[dict]) -> list[dict]:
        """Keep all messages but scrub tool-call/result pairs older than threshold."""
        current_turn = self._turn_counter
        threshold = current_turn - self._clear_tool_results_after
        result = []
        for msg in messages:
            role = msg.get("role", "")
            turn = msg.get("_meta", {}).get("turn", 0)

            if role == "tool":
                if turn <= threshold:
                    continue
                result.append(msg)
            elif role == "assistant" and "tool_calls" in msg:
                if turn <= threshold:
                    if msg.get("content"):
                        result.append({k: v for k, v in msg.items() if k != "tool_calls"})
                    continue
                result.append(msg)
            else:
                result.append(msg)
        return result

    @staticmethod
    def _group_into_units(messages: list[dict]) -> list[list[dict]]:
        """Group messages into atomic units for window slicing.

        An assistant message with tool_calls plus its consecutive tool results
        forms one unit. Everything else is a single-message unit. This ensures
        tool-call groups are never split by the FIFO window boundary.
        """
        units: list[list[dict]] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                group = [msg]
                call_ids = {tc.get("id", "") for tc in msg.get("tool_calls", []) if tc.get("id")}
                j = i + 1
                while j < len(messages):
                    next_msg = messages[j]
                    if next_msg.get("role") == "tool" and next_msg.get("tool_call_id", "") in call_ids:
                        group.append(next_msg)
                        j += 1
                    else:
                        break
                units.append(group)
                i = j
            else:
                units.append([msg])
                i += 1
        return units

    @staticmethod
    def _take_trailing_units(units: list[list[dict]], max_messages: int) -> list[list[dict]]:
        """Take trailing units totaling at most max_messages individual messages.

        Always includes at least the last unit even if it exceeds max_messages
        on its own.
        """
        if not units:
            return []
        result: list[list[dict]] = []
        count = 0
        for unit in reversed(units):
            unit_size = len(unit)
            if result and count + unit_size > max_messages:
                break
            result.append(unit)
            count += unit_size
        result.reverse()
        return result

    def get_messages(self, new_user_message: str) -> list[dict]:
        if self._clear_tool_results:
            eligible = self._clear_old_tool_pairs(self._history)
        else:
            eligible = self._strip_tool_traffic(self._history)

        units = self._group_into_units(eligible)

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

            assembled = [
                {"role": "system", "content": self._system_prompt},
                *pinned_msgs,
                *window_msgs,
                {"role": "user", "content": new_user_message},
            ]
        else:
            window_units = self._take_trailing_units(units, self._max_messages)
            window_msgs = [msg for unit in window_units for msg in unit]

            assembled = [
                {"role": "system", "content": self._system_prompt},
                *window_msgs,
                {"role": "user", "content": new_user_message},
            ]

        return self._normalize_for_api(assembled)

    def add_messages(self, messages: list[dict]) -> None:
        self._turn_counter += 1
        for msg in messages:
            self._add_meta(msg)
            if self._is_pinned(msg):
                msg["_meta"]["pinned"] = True
            self._history.append(msg)

    def save(self) -> None:
        if not self._session_file:
            return
        os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
        with open(self._session_file, "w") as f:
            for msg in self._history:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
