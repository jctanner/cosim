"""SummaryMemory — LLM-generated running summary of conversation history."""

from __future__ import annotations

import json
import os

from cosim_agent.memory.base import ConversationMemory

_SUMMARIZE_SYSTEM = (
    "Progressively summarize the lines of conversation provided, "
    "adding onto the previous summary returning a new summary."
)

_FALLBACK_WINDOW = 10


def format_messages_for_summary(messages: list[dict]) -> str:
    """Convert message dicts to readable text for summarization.

    Includes tool results (truncated) and tool-call names so that
    tool-heavy conversations retain recall after eviction.
    """
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "tool":
            if not content:
                continue
            truncated = content[:500] if len(content) > 500 else content
            lines.append(f"[tool result] {truncated}")
            continue

        if role == "assistant" and "tool_calls" in msg:
            tool_names = []
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                if isinstance(fn, dict):
                    tool_names.append(fn.get("name", "?"))
            call_info = f" [called: {', '.join(tool_names)}]" if tool_names else ""
            if content:
                lines.append(f"assistant: {content}{call_info}")
            elif tool_names:
                lines.append(f"assistant:{call_info}")
            continue

        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def call_summarize(
    client,
    model: str,
    messages_text: str,
    existing_summary: str,
    max_tokens: int,
) -> str | None:
    """Call the LLM to generate or update a summary.

    Returns the summary string on success, None on any failure.
    """
    if not messages_text.strip():
        return existing_summary or None

    if existing_summary:
        user_content = (
            f"Current summary:\n{existing_summary}\n\nNew lines of conversation:\n{messages_text}\n\nNew summary:"
        )
    else:
        user_content = f"Summarize:\n\n{messages_text}"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SUMMARIZE_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
    except Exception:
        return None


class SummaryMemory(ConversationMemory):
    """Full-history summarization via LLM.

    After each turn, calls the LLM to update a running summary. The API
    context contains only [system, summary, user] — no raw history.

    Raw JSONL history is preserved on disk as the source of truth.
    The summary is stored alongside it as a separate JSON file.

    Config keys (via kwargs):
        llm_client: OpenAI — client instance for summarization calls
        llm_model: str — model ID for summarization calls
        max_summary_tokens: int — max_tokens for summarization (default 500)
    """

    def __init__(self, session_file: str, system_prompt: str, **kwargs):
        super().__init__(session_file, system_prompt, **kwargs)
        self._llm_client = kwargs.get("llm_client")
        self._llm_model: str = kwargs.get("llm_model", "")
        self._max_summary_tokens: int = kwargs.get("max_summary_tokens", 500)
        self._history: list[dict] = []
        self._summary: str = ""
        self._summary_file: str = self._derive_summary_file()

    def _derive_summary_file(self) -> str:
        if not self._session_file:
            return ""
        base, _ = os.path.splitext(self._session_file)
        return f"{base}_summary.json"

    def load(self) -> None:
        if self._session_file and os.path.isfile(self._session_file):
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
                self._turn_counter = max(m.get("_meta", {}).get("turn", 0) for m in self._history)

        if self._summary_file and os.path.isfile(self._summary_file):
            try:
                with open(self._summary_file) as f:
                    data = json.load(f)
                self._summary = data.get("summary", "")
            except (json.JSONDecodeError, OSError):
                pass

    def get_messages(self, new_user_message: str) -> list[dict]:
        assembled: list[dict] = [{"role": "system", "content": self._system_prompt}]

        if self._summary:
            assembled.append(
                {
                    "role": "assistant",
                    "content": f"[Summary of our conversation so far]\n{self._summary}",
                }
            )
        elif self._history:
            text_msgs = [
                m
                for m in self._history
                if m.get("role") in ("user", "assistant") and m.get("content") and "tool_calls" not in m
            ]
            for msg in text_msgs[-_FALLBACK_WINDOW:]:
                assembled.append({"role": msg["role"], "content": msg["content"]})

        assembled.append({"role": "user", "content": new_user_message})
        return self._normalize_for_api(assembled)

    def add_messages(self, messages: list[dict]) -> None:
        self._turn_counter += 1
        for msg in messages:
            self._add_meta(msg)
            self._history.append(msg)

        if self._llm_client:
            text = format_messages_for_summary(messages)
            result = call_summarize(
                self._llm_client,
                self._llm_model,
                text,
                self._summary,
                self._max_summary_tokens,
            )
            if result is not None:
                self._summary = result

    def save(self) -> None:
        if not self._session_file:
            return
        os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
        with open(self._session_file, "w") as f:
            for msg in self._history:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        if self._summary_file:
            with open(self._summary_file, "w") as f:
                json.dump({"summary": self._summary}, f, ensure_ascii=False)
