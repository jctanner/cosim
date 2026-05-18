"""ReflexionMemory — per-turn LLM self-critique."""

from __future__ import annotations

import json
import os

from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.summary import format_messages_for_summary

_CRITIQUE_SYSTEM = (
    "You are a self-evaluation assistant. Critique the agent's last response. "
    "Was it helpful? Did it miss anything? Was the tool usage appropriate? "
    "Be specific and concise — 2-3 sentences."
)


class ReflexionMemory(FIFOMemory):
    """FIFO window with per-turn LLM self-critique.

    After each turn, generates a self-critique via an LLM call. The last
    ``max_reflections`` critiques are injected as a system message after
    the main system prompt.

    Config keys (in addition to FIFOMemory keys):
        llm_client: OpenAI — client instance for critique calls
        llm_model: str — model ID for critique calls
        max_reflections: int — how many past critiques to keep (default 3)
        max_critique_tokens: int — max_tokens for critique (default 200)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._llm_client = kwargs.get("llm_client")
        self._llm_model: str = kwargs.get("llm_model", "")
        self._max_reflections: int = kwargs.get("max_reflections", 3)
        self._max_critique_tokens: int = kwargs.get("max_critique_tokens", 200)
        self._critiques: list[dict] = []
        self._critiques_file: str = self._derive_critiques_file()

    def _derive_critiques_file(self) -> str:
        if not self._session_file:
            return ""
        base, _ = os.path.splitext(self._session_file)
        return f"{base}_critiques.json"

    def load(self) -> None:
        super().load()
        if self._critiques_file and os.path.isfile(self._critiques_file):
            try:
                with open(self._critiques_file) as f:
                    self._critiques = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def _generate_critique(self, messages: list[dict]) -> str | None:
        text = format_messages_for_summary(messages)
        if not text.strip():
            return None
        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {"role": "system", "content": _CRITIQUE_SYSTEM},
                    {"role": "user", "content": text},
                ],
                max_tokens=self._max_critique_tokens,
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def add_messages(self, messages: list[dict]) -> None:
        super().add_messages(messages)
        if not self._llm_client:
            return
        content = self._generate_critique(messages)
        if content:
            self._critiques.append({"turn": self._turn_counter, "content": content})
            self._critiques = self._critiques[-self._max_reflections :]

    def get_messages(self, new_user_message: str) -> list[dict]:
        result = super().get_messages(new_user_message)
        if not self._critiques:
            return result
        lines = [f"- Turn {c['turn']}: {c['content']}" for c in self._critiques]
        critique_msg = {"role": "system", "content": "[Self-critique from previous turns]\n" + "\n".join(lines)}
        result.insert(1, critique_msg)
        return result

    def save(self) -> None:
        super().save()
        if self._critiques_file and self._critiques:
            os.makedirs(os.path.dirname(self._critiques_file), exist_ok=True)
            with open(self._critiques_file, "w") as f:
                json.dump(self._critiques, f, ensure_ascii=False)
