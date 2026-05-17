"""EpisodicMemory — FIFO window with periodic LLM reflections."""

from __future__ import annotations

import json
import os

from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.summary import format_messages_for_summary

_REFLECT_SYSTEM = (
    "Generate a high-level reflection about the conversation so far. "
    "Identify key themes, patterns, unresolved questions, and important decisions. "
    "Be concise — one paragraph."
)


class EpisodicMemory(FIFOMemory):
    """FIFO window with periodic LLM-generated reflections.

    Every ``reflection_interval`` turns, generates a high-level reflection
    via an LLM call. Reflections are injected as a system message after the
    main system prompt.

    Config keys (in addition to FIFOMemory keys):
        llm_client: OpenAI — client instance for reflection calls
        llm_model: str — model ID for reflection calls
        reflection_interval: int — turns between reflections (default 10)
        max_reflection_tokens: int — max_tokens for reflection (default 300)
        max_reflections: int — max stored reflections (default 5)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._llm_client = kwargs.get("llm_client")
        self._llm_model: str = kwargs.get("llm_model", "")
        self._reflection_interval: int = kwargs.get("reflection_interval", 10)
        self._max_reflection_tokens: int = kwargs.get("max_reflection_tokens", 300)
        self._max_reflections: int = kwargs.get("max_reflections", 5)
        self._reflections: list[dict] = []
        self._reflections_file: str = self._derive_reflections_file()

    def _derive_reflections_file(self) -> str:
        if not self._session_file:
            return ""
        base, _ = os.path.splitext(self._session_file)
        return f"{base}_reflections.json"

    def load(self) -> None:
        super().load()
        if self._reflections_file and os.path.isfile(self._reflections_file):
            try:
                with open(self._reflections_file) as f:
                    self._reflections = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def _generate_reflection(self) -> str | None:
        text = format_messages_for_summary(self._history[-20:])
        if not text.strip():
            return None

        prior = ""
        if self._reflections:
            prior = "\n".join(f"- {r['content']}" for r in self._reflections)

        user_content = f"Conversation summary so far:\n{text}"
        if prior:
            user_content += f"\n\nExisting reflections:\n{prior}"
        user_content += "\n\nGenerate a new reflection:"

        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {"role": "system", "content": _REFLECT_SYSTEM},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=self._max_reflection_tokens,
            )
            return response.choices[0].message.content
        except Exception:
            return None

    def add_messages(self, messages: list[dict]) -> None:
        super().add_messages(messages)
        if not self._llm_client:
            return
        if self._turn_counter > 0 and self._turn_counter % self._reflection_interval == 0:
            content = self._generate_reflection()
            if content:
                self._reflections.append({"turn": self._turn_counter, "content": content})
                self._reflections = self._reflections[-self._max_reflections :]

    def get_messages(self, new_user_message: str) -> list[dict]:
        result = super().get_messages(new_user_message)
        if not self._reflections:
            return result
        lines = [f"- {r['content']}" for r in self._reflections]
        reflect_msg = {"role": "system", "content": "[Reflections on conversation]\n" + "\n".join(lines)}
        result.insert(1, reflect_msg)
        return result

    def save(self) -> None:
        super().save()
        if self._reflections_file and self._reflections:
            os.makedirs(os.path.dirname(self._reflections_file), exist_ok=True)
            with open(self._reflections_file, "w") as f:
                json.dump(self._reflections, f, ensure_ascii=False)
