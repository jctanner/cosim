"""NoMemory — stateless pass-through (each turn starts fresh)."""

from cosim_agent.memory.base import ConversationMemory


class NoMemory(ConversationMemory):
    """Stateless pass-through — each turn starts fresh."""

    def load(self) -> None:
        pass

    def get_messages(self, new_user_message: str) -> list[dict]:
        return self._normalize_for_api(
            [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": new_user_message},
            ]
        )

    def add_messages(self, messages: list[dict]) -> None:
        pass

    def save(self) -> None:
        pass
