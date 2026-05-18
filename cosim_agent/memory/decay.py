"""DecayMemory — score-based selection using exponential decay."""

from __future__ import annotations

import math
import time

from cosim_agent.memory.fifo import FIFOMemory


class DecayMemory(FIFOMemory):
    """Replaces strict FIFO positional selection with score-based selection.

    Scoring formula: score = importance * exp(-lambda * age_hours)
    where lambda = ln(2) / decay_halflife_hours.

    Pinned messages bypass scoring and are always included. Selected
    messages are re-sorted chronologically for coherent context.

    Config keys (in addition to FIFOMemory keys):
        decay_halflife_hours: float — half-life for decay (default 4.0)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._decay_halflife: float = kwargs.get("decay_halflife_hours", 4.0)
        self._decay_lambda: float = math.log(2) / self._decay_halflife if self._decay_halflife > 0 else 0.0

    def _score_unit(self, unit: list[dict], now: float) -> float:
        ts = max(msg.get("_meta", {}).get("ts", now) for msg in unit)
        age_hours = (now - ts) / 3600.0
        importance = max(msg.get("_meta", {}).get("importance", 1.0) for msg in unit)
        return importance * math.exp(-self._decay_lambda * age_hours)

    def get_messages(self, new_user_message: str) -> list[dict]:
        if self._clear_tool_results:
            eligible = self._clear_old_tool_pairs(self._history)
        else:
            eligible = self._strip_tool_traffic(self._history)

        units = self._group_into_units(eligible)
        now = time.time()

        if self._compiled_pins:
            pinned_units: list[list[dict]] = []
            scorable: list[tuple[int, list[dict]]] = []
            for i, unit in enumerate(units):
                if any(self._is_pinned(msg) for msg in unit):
                    pinned_units.append(unit)
                else:
                    scorable.append((i, unit))
        else:
            pinned_units = []
            scorable = list(enumerate(units))

        scored = [(idx, unit, self._score_unit(unit, now)) for idx, unit in scorable]
        scored.sort(key=lambda x: x[2], reverse=True)

        selected: list[tuple[int, list[dict]]] = []
        count = 0
        for idx, unit, _score in scored:
            unit_size = len(unit)
            if selected and count + unit_size > self._max_messages:
                break
            selected.append((idx, unit))
            count += unit_size

        selected.sort(key=lambda x: x[0])

        pinned_msgs = [msg for unit in pinned_units for msg in unit]
        selected_msgs = [msg for _, unit in selected for msg in unit]

        assembled = [
            {"role": "system", "content": self._system_prompt},
            *pinned_msgs,
            *selected_msgs,
            {"role": "user", "content": new_user_message},
        ]

        return self._normalize_for_api(assembled)
