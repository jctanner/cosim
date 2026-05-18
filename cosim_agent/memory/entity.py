"""EntityMemory — LLM entity extraction and injection."""

from __future__ import annotations

import json
import os

from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.summary import format_messages_for_summary

_EXTRACT_SYSTEM = (
    "Extract entities (people, projects, systems, tools) mentioned in the conversation. "
    "Return a JSON object mapping entity names to one-sentence summaries. "
    "Only include entities with meaningful information. Return empty {} if none found."
)


class EntityMemory(FIFOMemory):
    """FIFO window with LLM-extracted entity summaries injected into context.

    After each turn, extracts entities via an LLM call and maintains a
    persistent entity store. Entity summaries are injected as a system
    message after the main system prompt.

    Config keys (in addition to FIFOMemory keys):
        llm_client: OpenAI — client instance for entity extraction
        llm_model: str — model ID for extraction calls
        max_entity_tokens: int — max_tokens for extraction (default 300)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._llm_client = kwargs.get("llm_client")
        self._llm_model: str = kwargs.get("llm_model", "")
        self._max_entity_tokens: int = kwargs.get("max_entity_tokens", 300)
        self._entities: dict[str, str] = {}
        self._entities_file: str = self._derive_entities_file()

    def _derive_entities_file(self) -> str:
        if not self._session_file:
            return ""
        base, _ = os.path.splitext(self._session_file)
        return f"{base}_entities.json"

    def load(self) -> None:
        super().load()
        if self._entities_file and os.path.isfile(self._entities_file):
            try:
                with open(self._entities_file) as f:
                    self._entities = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def _extract_entities(self, text: str) -> dict[str, str] | None:
        if not text.strip():
            return None
        try:
            response = self._llm_client.chat.completions.create(
                model=self._llm_model,
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM},
                    {"role": "user", "content": text},
                ],
                max_tokens=self._max_entity_tokens,
            )
            raw = response.choices[0].message.content
            return json.loads(raw)
        except Exception:
            return None

    def add_messages(self, messages: list[dict]) -> None:
        super().add_messages(messages)
        if not self._llm_client:
            return
        text = format_messages_for_summary(messages)
        extracted = self._extract_entities(text)
        if extracted:
            self._entities.update(extracted)

    def get_messages(self, new_user_message: str) -> list[dict]:
        result = super().get_messages(new_user_message)
        if not self._entities:
            return result
        lines = [f"{name}: {summary}" for name, summary in sorted(self._entities.items())]
        entity_msg = {"role": "system", "content": "[Known entities]\n" + "\n".join(lines)}
        result.insert(1, entity_msg)
        return result

    def save(self) -> None:
        super().save()
        if self._entities_file and self._entities:
            os.makedirs(os.path.dirname(self._entities_file), exist_ok=True)
            with open(self._entities_file, "w") as f:
                json.dump(self._entities, f, ensure_ascii=False)
