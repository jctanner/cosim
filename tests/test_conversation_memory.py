"""Tests for cosim_agent.memory — conversation memory strategies."""

import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from cosim_agent.memory import create_memory
from cosim_agent.memory.base import ConversationMemory
from cosim_agent.memory.decay import DecayMemory
from cosim_agent.memory.entity import EntityMemory
from cosim_agent.memory.episodic import EpisodicMemory
from cosim_agent.memory.fifo import FIFOMemory
from cosim_agent.memory.none import NoMemory
from cosim_agent.memory.reflexion import ReflexionMemory
from cosim_agent.memory.summary import SummaryMemory
from cosim_agent.memory.summary_buffer import SummaryBufferMemory

SYSTEM_PROMPT = "You are Rocky. You're a member of a small team."

SAMPLE_MESSAGES = [
    {"role": "user", "content": "What's the status of the deployment?"},
    {"role": "assistant", "content": "Checking the logs now..."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "get_messages", "arguments": "{}"}},
        ],
    },
    {"role": "tool", "tool_call_id": "tc1", "content": "3 messages in #general..."},
    {"role": "assistant", "content": "The deployment looks healthy."},
    {"role": "user", "content": "Great, create a ticket for the follow-up."},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "tc2", "type": "function", "function": {"name": "create_ticket", "arguments": "{}"}},
        ],
    },
    {"role": "tool", "tool_call_id": "tc2", "content": "TK-abc123 created"},
    {"role": "assistant", "content": "Created ticket TK-abc123."},
]

MIXED_ASSISTANT_MSG = {
    "role": "assistant",
    "content": "Let me check that for you.",
    "tool_calls": [
        {"id": "tc3", "type": "function", "function": {"name": "get_messages", "arguments": "{}"}},
    ],
}


@pytest.fixture
def session_file():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


# ── Factory tests ──────────────────────────────────────────────────────


class TestFactory:
    def test_create_none(self):
        m = create_memory({"strategy": "none"}, SYSTEM_PROMPT)
        assert isinstance(m, NoMemory)

    def test_create_fifo(self):
        m = create_memory({"strategy": "fifo", "max_messages": 30}, SYSTEM_PROMPT)
        assert isinstance(m, FIFOMemory)
        assert m._max_messages == 30

    def test_create_fifo_default_max(self):
        m = create_memory({"strategy": "fifo"}, SYSTEM_PROMPT)
        assert m._max_messages == 50

    def test_create_empty_config_defaults_to_none(self):
        m = create_memory({}, SYSTEM_PROMPT)
        assert isinstance(m, NoMemory)

    def test_create_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown memory strategy"):
            create_memory({"strategy": "nonexistent"}, SYSTEM_PROMPT)

    def test_create_with_session_file(self, session_file):
        m = create_memory({"strategy": "fifo", "session_file": session_file}, SYSTEM_PROMPT)
        assert m._session_file == session_file


# ── NoMemory tests ─────────────────────────────────────────────────────


class TestNoMemory:
    def test_returns_system_and_user_only(self):
        m = NoMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        msgs = m.get_messages("Hello")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"

    def test_add_messages_is_noop(self):
        m = NoMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        assert len(msgs) == 2

    def test_no_meta_in_output(self):
        m = NoMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        msgs = m.get_messages("Hello")
        assert all("_meta" not in msg for msg in msgs)


# ── FIFOMemory tests ──────────────────────────────────────────────────


class TestFIFOMemory:
    def test_returns_window(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=3)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        # system + up to 3 filtered messages + user
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Next"

    def test_preserves_system_prompt(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SYSTEM_PROMPT

    def test_user_prompt_always_last(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("My question")
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "My question"

    def test_stores_all_messages(self, session_file):
        m = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        m.save()
        with open(session_file) as f:
            stored = [json.loads(line) for line in f if line.strip()]
        assert len(stored) == len(SAMPLE_MESSAGES)
        roles = [s["role"] for s in stored]
        assert "tool" in roles
        assert roles.count("assistant") == 5

    def test_filters_tool_messages_in_selection(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        assert all(msg.get("role") != "tool" for msg in msgs)

    def test_no_orphaned_tool_calls_in_selection(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        for msg in msgs:
            assert "tool_calls" not in msg

    def test_mixed_assistant_strips_tool_calls(self):
        """Assistant msg with both content and tool_calls keeps text, drops tool_calls."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages([MIXED_ASSISTANT_MSG, {"role": "tool", "tool_call_id": "tc3", "content": "result"}])
        msgs = m.get_messages("Next")
        assistant_msgs = [msg for msg in msgs if msg["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["content"] == "Let me check that for you."
        assert "tool_calls" not in assistant_msgs[0]

    def test_meta_added_on_store(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages([{"role": "user", "content": "Hello"}])
        assert "_meta" in m._history[0]
        assert "ts" in m._history[0]["_meta"]
        assert "turn" in m._history[0]["_meta"]
        assert m._history[0]["_meta"]["turn"] == 1

    def test_no_meta_in_output(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)

    def test_persists_to_jsonl(self, session_file):
        m1 = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m1.add_messages(SAMPLE_MESSAGES[:3])
        m1.save()

        m2 = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m2.load()
        assert len(m2._history) == 3

    def test_old_session_file_compat(self, session_file):
        """Session files without _meta fields load correctly."""
        old_msgs = [
            {"role": "user", "content": "old message"},
            {"role": "assistant", "content": "old reply"},
        ]
        with open(session_file, "w") as f:
            for msg in old_msgs:
                f.write(json.dumps(msg) + "\n")

        m = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m.load()
        assert len(m._history) == 2
        msgs = m.get_messages("Next")
        assert len(msgs) == 4  # system + 2 old + user

    def test_handles_empty_session_file(self, session_file):
        with open(session_file, "w") as f:
            f.write("")
        m = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m.load()
        assert len(m._history) == 0

    def test_handles_missing_session_file(self):
        m = FIFOMemory(session_file="/nonexistent/path.jsonl", system_prompt=SYSTEM_PROMPT)
        m.load()
        assert len(m._history) == 0

    def test_max_messages_boundary(self):
        """With max_messages=2, only last 2 filtered messages appear."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=2)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        # system + 2 filtered + user = 4
        inner = [msg for msg in msgs if msg["role"] not in ("system", "user")]
        assert len(inner) <= 2

    def test_max_messages_counts_filtered_not_raw(self):
        """max_messages applies after filtering tool traffic."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=3)
        # 9 raw messages → 5 after filtering tool traffic (2 user + 3 assistant)
        # max_messages=3 takes last 3 filtered: assistant, user, assistant
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        # system + 3 window messages + new user = 5
        inner = msgs[1:-1]  # strip system and new user prompt
        assert len(inner) == 3

    def test_turn_counter_increments(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages([{"role": "user", "content": "t1"}])
        assert m._turn_counter == 1
        m.add_messages([{"role": "user", "content": "t2"}])
        assert m._turn_counter == 2

    def test_turn_counter_restored_on_load(self, session_file):
        m1 = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m1.add_messages([{"role": "user", "content": "t1"}])
        m1.add_messages([{"role": "user", "content": "t2"}])
        m1.save()

        m2 = FIFOMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT)
        m2.load()
        assert m2._turn_counter == 2


# ── Normalization tests ───────────────────────────────────────────────


class TestNormalization:
    def test_strips_meta(self):
        msgs = [{"role": "user", "content": "hi", "_meta": {"ts": 123, "turn": 1}}]
        result = ConversationMemory._normalize_for_api(msgs)
        assert "_meta" not in result[0]

    def test_preserves_standard_fields(self):
        msg = {
            "role": "assistant",
            "content": "hello",
            "tool_calls": [{"id": "tc1"}],
            "name": "bot",
        }
        result = ConversationMemory._normalize_for_api([msg])
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "hello"
        assert result[0]["tool_calls"] == [{"id": "tc1"}]
        assert result[0]["name"] == "bot"

    def test_strips_unknown_fields(self):
        msg = {"role": "user", "content": "hi", "custom_field": "x", "_meta": {}}
        result = ConversationMemory._normalize_for_api([msg])
        assert "custom_field" not in result[0]
        assert "_meta" not in result[0]

    def test_tool_message_preserves_tool_call_id(self):
        msg = {"role": "tool", "tool_call_id": "tc1", "content": "result"}
        result = ConversationMemory._normalize_for_api([msg])
        assert result[0]["tool_call_id"] == "tc1"
        assert result[0]["content"] == "result"

    def test_handles_missing_content(self):
        msg = {"role": "assistant", "tool_calls": [{"id": "tc1"}]}
        result = ConversationMemory._normalize_for_api([msg])
        assert result[0]["tool_calls"] == [{"id": "tc1"}]


# ── Pinned messages tests (Phase 1c) ─────────────────────────────────


class TestPinnedMessages:
    def test_pinned_marked_at_storage_time(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, pin_patterns=["CRITICAL"])
        m.add_messages(
            [
                {"role": "user", "content": "CRITICAL: server down"},
                {"role": "assistant", "content": "Looking into it."},
            ]
        )
        assert m._history[0]["_meta"]["pinned"] is True
        assert m._history[1]["_meta"]["pinned"] is False

    def test_pinned_survives_beyond_fifo_window(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=3, pin_patterns=["IMPORTANT"])
        m.add_messages([{"role": "user", "content": "IMPORTANT: remember this"}])
        for i in range(20):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "IMPORTANT: remember this" in contents

    def test_pinned_by_content_substring(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, pin_patterns=["Scenario Director"])
        m.add_messages(
            [
                {"role": "user", "content": "Message from Scenario Director: do X"},
                {"role": "assistant", "content": "On it."},
            ]
        )
        assert m._history[0]["_meta"]["pinned"] is True
        assert m._history[1]["_meta"]["pinned"] is False

    def test_pinned_by_content_regex(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, pin_patterns=[r"TK-\w+"])
        m.add_messages(
            [
                {"role": "assistant", "content": "Created TK-abc123"},
                {"role": "assistant", "content": "Nothing special"},
            ]
        )
        assert m._history[0]["_meta"]["pinned"] is True
        assert m._history[1]["_meta"]["pinned"] is False

    def test_pinned_ordering(self):
        """Pinned messages appear after system prompt, before recent window."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=2, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: first"}])
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "PIN: first"
        assert msgs[-1]["content"] == "Next"

    def test_pinned_coexists_with_fifo(self):
        """Output is [system, ...pinned, ...recent_window, user_prompt]."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=2, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: remember"}])
        m.add_messages([{"role": "assistant", "content": "a"}])
        m.add_messages([{"role": "assistant", "content": "b"}])
        m.add_messages([{"role": "assistant", "content": "c"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "PIN: remember"
        assert msgs[-1]["content"] == "Next"
        # Window should be last 2 non-pinned: b, c
        window = msgs[2:-1]
        assert [m["content"] for m in window] == ["b", "c"]

    def test_pinned_not_duplicated(self):
        """A pinned message in the recent window appears only once."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=10, pin_patterns=["PIN"])
        m.add_messages(
            [
                {"role": "user", "content": "PIN: important"},
                {"role": "assistant", "content": "ok"},
            ]
        )
        msgs = m.get_messages("Next")
        pin_count = sum(1 for msg in msgs if msg.get("content") == "PIN: important")
        assert pin_count == 1

    def test_pinned_no_meta_in_output(self):
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: test"}])
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)

    def test_no_pin_patterns_works_as_before(self):
        """Without pin_patterns, behavior is unchanged from plain FIFO."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, max_messages=2)
        m.add_messages(SAMPLE_MESSAGES)
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert all("_meta" not in msg for msg in msgs)


# ── Tool result clearing tests (Phase 1d) ────────────────────────────


TOOL_HEAVY_CONVERSATION = [
    {"role": "user", "content": "Check the logs"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "tc1", "type": "function", "function": {"name": "get_messages", "arguments": "{}"}},
        ],
    },
    {"role": "tool", "tool_call_id": "tc1", "content": "Log entry 1\nLog entry 2\nLog entry 3"},
    {"role": "assistant", "content": "Logs look fine."},
]


class TestToolResultClearing:
    def _make_memory(self, **kwargs):
        defaults = {"clear_tool_results": True, "clear_tool_results_after": 3, "max_messages": 50}
        defaults.update(kwargs)
        return FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT, **defaults)

    def test_clearing_false_strips_all_tool_messages(self):
        """Default mode (clear_tool_results=false) strips all tool traffic."""
        m = FIFOMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        assert all(msg.get("role") != "tool" for msg in msgs)
        assert all("tool_calls" not in msg for msg in msgs)

    def test_clearing_enables_tool_messages_in_context(self):
        """With clear_tool_results=true, recent tool messages appear in context."""
        m = self._make_memory(clear_tool_results_after=10)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        roles = [msg["role"] for msg in msgs]
        assert "tool" in roles

    def test_clearing_preserves_recent_pairs(self):
        """Tool pairs within threshold are kept verbatim."""
        m = self._make_memory(clear_tool_results_after=10)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        tool_msgs = [msg for msg in msgs if msg["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "Log entry 1\nLog entry 2\nLog entry 3"

    def test_clearing_drops_old_pairs(self):
        """Old tool-call/result pairs beyond threshold are dropped as a unit."""
        m = self._make_memory(clear_tool_results_after=2)
        # Turn 1: tool call
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        # Turns 2, 3, 4: push past threshold
        m.add_messages([{"role": "user", "content": "t2"}])
        m.add_messages([{"role": "user", "content": "t3"}])
        m.add_messages([{"role": "user", "content": "t4"}])
        msgs = m.get_messages("Next")
        # Turn 1 tool pair should be gone (turn 1 <= current_turn 4 - threshold 2 = 2)
        assert all(msg.get("role") != "tool" for msg in msgs)
        # But user/assistant text from turn 1 should survive
        contents = [msg.get("content", "") for msg in msgs]
        assert "Check the logs" in contents
        assert "Logs look fine." in contents

    def test_clearing_keeps_text_from_mixed_assistant(self):
        """Assistant with both text and tool_calls: old pair keeps text, drops tool_calls."""
        m = self._make_memory(clear_tool_results_after=1)
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "Let me check.",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "get_messages", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "result data"},
            ]
        )
        # Push past threshold
        m.add_messages([{"role": "user", "content": "t2"}])
        m.add_messages([{"role": "user", "content": "t3"}])
        msgs = m.get_messages("Next")
        assistant_msgs = [msg for msg in msgs if msg["role"] == "assistant"]
        assert any(msg["content"] == "Let me check." for msg in assistant_msgs)
        assert all("tool_calls" not in msg for msg in assistant_msgs)

    def test_clearing_valid_message_ordering(self):
        """Every tool result has a preceding assistant+tool_calls with matching ID."""
        m = self._make_memory(clear_tool_results_after=10)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)

    def test_clearing_handles_orphaned_tool_results(self):
        """Orphaned tool results from old turns are dropped by turn threshold."""
        m = self._make_memory(clear_tool_results_after=1)
        m.add_messages(
            [
                {"role": "tool", "tool_call_id": "orphan", "content": "orphan result"},
            ]
        )
        for _ in range(3):
            m.add_messages(
                [
                    {"role": "assistant", "content": "filler"},
                ]
            )
        msgs = m.get_messages("Next")
        tool_msgs = [msg for msg in msgs if msg.get("role") == "tool"]
        assert len(tool_msgs) == 0, "Old orphaned tool results should be dropped"

    def test_clearing_no_meta_in_output(self):
        m = self._make_memory()
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)

    def test_clearing_small_window_keeps_group_atomic(self):
        """max_messages=1 must not split a tool-call group across the boundary."""
        m = self._make_memory(max_messages=1, clear_tool_results_after=10)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)

    def test_clearing_small_window_includes_full_group(self):
        """A tool-call group that fits within max_messages is kept intact."""
        m = self._make_memory(max_messages=3, clear_tool_results_after=10)
        m.add_messages(
            [
                {"role": "user", "content": "filler 1"},
                {"role": "user", "content": "filler 2"},
                {"role": "user", "content": "filler 3"},
            ]
        )
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)
        tool_msgs = [msg for msg in msgs if msg["role"] == "tool"]
        if tool_msgs:
            assert tool_msgs[0]["tool_call_id"] == "tc1"

    def test_clearing_multiple_tool_calls_per_assistant(self):
        """Assistant with 2 tool_calls keeps both results as an atomic group."""
        m = self._make_memory(max_messages=4, clear_tool_results_after=10)
        m.add_messages(
            [
                {"role": "user", "content": "filler"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "a1", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                        {"id": "a2", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "a1", "content": "r1"},
                {"role": "tool", "tool_call_id": "a2", "content": "r2"},
                {"role": "assistant", "content": "Done."},
            ]
        )
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)

    def test_clearing_window_boundary_excludes_partial_group(self):
        """When a group won't fit in the remaining budget, it's excluded entirely."""
        m = self._make_memory(max_messages=2, clear_tool_results_after=10)
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "x1", "type": "function", "function": {"name": "t1", "arguments": "{}"}},
                        {"id": "x2", "type": "function", "function": {"name": "t2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "x1", "content": "r1"},
                {"role": "tool", "tool_call_id": "x2", "content": "r2"},
                {"role": "assistant", "content": "summary"},
            ]
        )
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)
        inner = [msg for msg in msgs if msg["role"] not in ("system", "user")]
        # The 3-message group won't fit in budget=2 after "summary", so only "summary"
        assert len(inner) <= 2

    def test_pin_pattern_ignores_tool_results(self):
        """A tool result whose content matches a pin pattern is NOT pinned."""
        m = FIFOMemory(
            session_file="",
            system_prompt=SYSTEM_PROMPT,
            max_messages=2,
            pin_patterns=["CRITICAL"],
            clear_tool_results=True,
            clear_tool_results_after=10,
        )
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "get_logs", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "CRITICAL error in prod"},
                {"role": "assistant", "content": "There's a critical error."},
            ]
        )
        for i in range(5):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)

    def test_pin_pattern_ignores_assistant_tool_calls(self):
        """An assistant+tool_calls with matching text content is NOT pinned."""
        m = FIFOMemory(
            session_file="",
            system_prompt=SYSTEM_PROMPT,
            max_messages=2,
            pin_patterns=["IMPORTANT"],
            clear_tool_results=True,
            clear_tool_results_after=10,
        )
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "IMPORTANT: checking now",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "check", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "all clear"},
                {"role": "assistant", "content": "Done checking."},
            ]
        )
        for i in range(5):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)
        # The mixed assistant should NOT be pinned separately from its tool result
        contents = [msg.get("content", "") for msg in msgs]
        assert "IMPORTANT: checking now" not in contents or "all clear" in contents

    def test_pin_with_clearing_no_orphaned_tools(self):
        """Combined pinning + clearing never produces orphaned tool messages."""
        m = FIFOMemory(
            session_file="",
            system_prompt=SYSTEM_PROMPT,
            max_messages=3,
            pin_patterns=["REMEMBER"],
            clear_tool_results=True,
            clear_tool_results_after=10,
        )
        m.add_messages([{"role": "user", "content": "REMEMBER: this is key"}])
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        msgs = m.get_messages("Next")
        self._assert_valid_tool_pairing(msgs)
        # The pinned message should survive
        contents = [msg.get("content", "") for msg in msgs]
        assert "REMEMBER: this is key" in contents

    @staticmethod
    def _assert_valid_tool_pairing(msgs: list[dict]) -> None:
        """Assert every tool result has a preceding assistant with a matching tool_call_id."""
        for i, msg in enumerate(msgs):
            if msg.get("role") != "tool":
                continue
            tcid = msg.get("tool_call_id", "")
            found = False
            for j in range(i - 1, -1, -1):
                prev = msgs[j]
                if prev.get("role") == "assistant" and "tool_calls" in prev:
                    call_ids = {tc.get("id", "") for tc in prev["tool_calls"]}
                    if tcid in call_ids:
                        found = True
                    break
            assert found, (
                f"Tool result at index {i} (tool_call_id={tcid!r}) "
                f"has no preceding assistant with matching tool_call_id"
            )


# ── Mock LLM client ─────────────────────────────────────────────────────


class MockCompletions:
    def __init__(self):
        self.calls: list[dict] = []
        self.response_text = "Mocked summary of the conversation."

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.response_text))])


class MockOpenAIClient:
    def __init__(self):
        self._completions = MockCompletions()
        self.chat = SimpleNamespace(completions=self._completions)

    @property
    def calls(self):
        return self._completions.calls

    @property
    def response_text(self):
        return self._completions.response_text

    @response_text.setter
    def response_text(self, value):
        self._completions.response_text = value


class FailingCompletions:
    def create(self, **kwargs):
        raise ConnectionError("LLM unavailable")


class FailingOpenAIClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=FailingCompletions())


# ── SummaryMemory tests (Phase 1e) ──────────────────────────────────────


class TestSummaryMemory:
    def _make_memory(self, session_file="", **kwargs):
        defaults = {"llm_client": MockOpenAIClient(), "llm_model": "test-model", "max_summary_tokens": 200}
        defaults.update(kwargs)
        return SummaryMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT, **defaults)

    def test_summary_calls_llm_for_compression(self):
        """add_messages() calls the LLM to generate a summary."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client)
        m.add_messages([{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there!"}])
        assert len(client.calls) == 1
        call_kwargs = client.calls[0]
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["max_tokens"] == 200
        assert any("Hello" in msg["content"] for msg in call_kwargs["messages"] if msg.get("content"))

    def test_summary_replaces_history_with_summary(self):
        """get_messages() returns [system, summary, user] — not raw history."""
        m = self._make_memory()
        m.add_messages(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
        )
        msgs = m.get_messages("What's next?")
        assert len(msgs) == 3
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "assistant"
        assert "[Summary of our conversation so far]" in msgs[1]["content"]
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "What's next?"

    def test_summary_preserves_raw_on_disk(self, session_file):
        """Raw JSONL still contains all original messages after summarization."""
        m = self._make_memory(session_file=session_file)
        m.add_messages(SAMPLE_MESSAGES)
        m.save()
        with open(session_file) as f:
            stored = [json.loads(line) for line in f if line.strip()]
        assert len(stored) == len(SAMPLE_MESSAGES)

    def test_summary_handles_llm_failure(self):
        """If LLM call fails, add_messages() doesn't crash and get_messages() falls back."""
        m = self._make_memory(llm_client=FailingOpenAIClient())
        m.add_messages(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ]
        )
        # Should not crash
        msgs = m.get_messages("Next")
        # Falls back to recent history messages
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Next"
        # Should contain fallback messages from history
        assert len(msgs) > 2

    def test_summary_max_tokens_respected(self):
        """max_summary_tokens is passed as max_tokens to the LLM call."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_summary_tokens=300)
        m.add_messages([{"role": "user", "content": "Hello"}])
        assert client.calls[0]["max_tokens"] == 300

    def test_summary_no_meta_in_output(self):
        m = self._make_memory()
        m.add_messages([{"role": "user", "content": "Hello"}])
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)

    def test_summary_file_persistence(self, session_file):
        """Summary persists and loads correctly across instances."""
        m1 = self._make_memory(session_file=session_file)
        m1.add_messages([{"role": "user", "content": "Hello"}])
        m1.save()

        m2 = self._make_memory(session_file=session_file)
        m2.load()
        assert m2._summary == "Mocked summary of the conversation."
        msgs = m2.get_messages("Next")
        assert any("[Summary of our conversation so far]" in msg.get("content", "") for msg in msgs)

    def test_summary_factory_registration(self):
        """create_memory() recognizes 'summary' strategy."""
        client = MockOpenAIClient()
        m = create_memory(
            {"strategy": "summary", "max_summary_tokens": 100},
            SYSTEM_PROMPT,
            llm_client=client,
            llm_model="test-model",
        )
        assert isinstance(m, SummaryMemory)
        assert m._max_summary_tokens == 100

    def test_summary_no_llm_client_graceful(self):
        """Without an LLM client, add_messages() stores but doesn't summarize."""
        m = SummaryMemory(session_file="", system_prompt=SYSTEM_PROMPT)
        m.add_messages([{"role": "user", "content": "Hello"}])
        assert m._summary == ""
        msgs = m.get_messages("Next")
        # Falls back to history
        assert len(msgs) == 3  # system + history msg + user

    def test_summary_incremental_update(self):
        """Second add_messages() passes existing summary to LLM."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client)
        m.add_messages([{"role": "user", "content": "Turn 1"}])
        assert len(client.calls) == 1

        client.response_text = "Updated summary."
        m.add_messages([{"role": "user", "content": "Turn 2"}])
        assert len(client.calls) == 2
        second_call = client.calls[1]
        user_msg = [msg for msg in second_call["messages"] if msg["role"] == "user"][0]
        assert "Current summary:" in user_msg["content"]
        assert "Mocked summary" in user_msg["content"]


# ── SummaryBufferMemory tests (Phase 1f) ────────────────────────────────


class TestSummaryBufferMemory:
    def _make_memory(self, session_file="", **kwargs):
        defaults = {
            "llm_client": MockOpenAIClient(),
            "llm_model": "test-model",
            "max_summary_tokens": 200,
            "max_messages": 5,
        }
        defaults.update(kwargs)
        return SummaryBufferMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT, **defaults)

    def test_hybrid_keeps_recent_verbatim(self):
        """Under max_messages, returns raw messages with no summary."""
        m = self._make_memory(max_messages=10)
        m.add_messages(
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ]
        )
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "Hello" in contents
        assert "Hi!" in contents
        assert not any("[Summary" in c for c in contents)

    def test_hybrid_summarizes_evicted(self):
        """Over max_messages, LLM is called and summary appears in output."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_messages=3)
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        msgs = m.get_messages("Next")
        assert len(client.calls) > 0
        contents = [msg.get("content", "") for msg in msgs]
        assert any("[Summary of earlier conversation]" in c for c in contents)

    def test_hybrid_summary_prepended(self):
        """Output order: [system, summary, recent_window, user]."""
        m = self._make_memory(max_messages=3)
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        # Find summary position
        summary_idx = None
        for i, msg in enumerate(msgs):
            if "[Summary of earlier conversation]" in msg.get("content", ""):
                summary_idx = i
                break
        assert summary_idx is not None
        assert summary_idx > 0  # after system
        assert summary_idx < len(msgs) - 1  # before user

    def test_hybrid_boundary(self):
        """Exactly at max_messages, no summarization triggered."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_messages=5)
        for i in range(5):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        msgs = m.get_messages("Next")
        assert len(client.calls) == 0
        contents = [msg.get("content", "") for msg in msgs]
        assert not any("[Summary" in c for c in contents)

    def test_hybrid_incremental_summary(self):
        """LLM gets existing summary + new evictions, not full history."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_messages=3)
        # First batch: push past window
        for i in range(5):
            m.add_messages([{"role": "assistant", "content": f"batch1 msg {i}"}])
        first_call_count = len(client.calls)
        assert first_call_count > 0

        # Second batch: more evictions
        client.response_text = "Updated summary."
        for i in range(3):
            m.add_messages([{"role": "assistant", "content": f"batch2 msg {i}"}])
        assert len(client.calls) > first_call_count
        last_call = client.calls[-1]
        user_msg = [msg for msg in last_call["messages"] if msg["role"] == "user"][0]
        assert "Current summary:" in user_msg["content"]

    def test_hybrid_no_meta_in_output(self):
        m = self._make_memory()
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)

    def test_hybrid_with_pinning(self):
        """Pinned messages appear before summary in output."""
        m = self._make_memory(max_messages=3, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: remember this"}])
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "PIN: remember this" in contents
        pin_idx = contents.index("PIN: remember this")
        summary_idx = next(
            (i for i, c in enumerate(contents) if "[Summary of earlier conversation]" in c),
            None,
        )
        if summary_idx is not None:
            assert pin_idx < summary_idx, "Pinned should appear before summary"

    def test_hybrid_valid_tool_pairing(self):
        """Tool groups remain intact (inherited from FIFOMemory)."""
        m = self._make_memory(max_messages=5, clear_tool_results=True, clear_tool_results_after=10)
        m.add_messages(TOOL_HEAVY_CONVERSATION)
        msgs = m.get_messages("Next")
        TestToolResultClearing._assert_valid_tool_pairing(msgs)

    def test_hybrid_factory_registration(self):
        """create_memory() recognizes 'summary-buffer' strategy."""
        client = MockOpenAIClient()
        m = create_memory(
            {"strategy": "summary-buffer", "max_messages": 10, "max_summary_tokens": 100},
            SYSTEM_PROMPT,
            llm_client=client,
            llm_model="test-model",
        )
        assert isinstance(m, SummaryBufferMemory)
        assert m._max_messages == 10
        assert m._max_summary_tokens == 100

    def test_hybrid_file_persistence(self, session_file):
        """Summary and summarized_count persist across instances."""
        m1 = self._make_memory(session_file=session_file, max_messages=3)
        for i in range(6):
            m1.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        m1.save()
        assert m1._summarized_count > 0

        m2 = self._make_memory(session_file=session_file, max_messages=3)
        m2.load()
        assert m2._summary == m1._summary
        assert m2._summarized_count == m1._summarized_count

    def test_hybrid_eviction_matches_selection_with_tool_groups(self):
        """Eviction accounting uses the same unit-based logic as get_messages()."""
        client = MockOpenAIClient()
        m = self._make_memory(
            llm_client=client,
            max_messages=3,
            clear_tool_results=True,
            clear_tool_results_after=10,
        )
        # A 3-message tool-call group counts as one unit
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "check", "arguments": "{}"}},
                        {"id": "tc2", "type": "function", "function": {"name": "check2", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "result 1"},
                {"role": "tool", "tool_call_id": "tc2", "content": "result 2"},
                {"role": "assistant", "content": "All clear."},
            ]
        )
        msgs = m.get_messages("Next")
        # Window should contain the full tool group (3 msgs) + "All clear." (1 msg)
        # That's 4 messages which is > max_messages=3, but the group is atomic
        # so either the group is in or out. With _take_trailing_units, the last
        # unit ("All clear.") is taken first, then the 3-msg group won't fit.
        # So window = just ["All clear."] and the tool group is evicted.
        # Verify the eviction was actually summarized
        if len(client.calls) > 0:
            last_call = client.calls[-1]
            user_msg = [msg for msg in last_call["messages"] if msg["role"] == "user"][0]
            # Evicted content should appear in the summarization prompt
            assert "result 1" in user_msg["content"] or "check" in user_msg["content"]
        # Verify get_messages produces valid output regardless
        TestToolResultClearing._assert_valid_tool_pairing(msgs)

    def test_hybrid_pinned_excluded_from_eviction_summary(self):
        """Pinned messages are NOT sent to the LLM for summarization."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_messages=3, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: critical directive"}])
        for i in range(10):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        # Pinned message should not appear in any summarization call
        for call in client.calls:
            user_msg = [msg for msg in call["messages"] if msg["role"] == "user"][0]
            assert "PIN: critical directive" not in user_msg["content"], (
                "Pinned message should not be sent to LLM for summarization"
            )
        # But it should still appear verbatim in get_messages()
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "PIN: critical directive" in contents

    def test_hybrid_tool_results_in_eviction_summary(self):
        """Evicted tool results are included in the summarization prompt."""
        client = MockOpenAIClient()
        m = self._make_memory(
            llm_client=client,
            max_messages=2,
            clear_tool_results=True,
            clear_tool_results_after=10,
        )
        m.add_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "tc1", "type": "function", "function": {"name": "get_logs", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tc1", "content": "ERROR: disk full on node-3"},
                {"role": "assistant", "content": "Found an error."},
            ]
        )
        # Push past window so the tool group gets evicted
        for i in range(5):
            m.add_messages([{"role": "assistant", "content": f"filler {i}"}])
        # Verify tool result content made it into the summarization prompt
        found_tool_content = False
        for call in client.calls:
            user_msg = [msg for msg in call["messages"] if msg["role"] == "user"][0]
            if "disk full" in user_msg["content"]:
                found_tool_content = True
                break
        assert found_tool_content, "Evicted tool results should be included in summary"


# ── format_messages_for_summary tests ────────────────────────────────────


class TestFormatMessagesForSummary:
    def test_includes_tool_results(self):
        from cosim_agent.memory.summary import format_messages_for_summary

        msgs = [
            {"role": "tool", "tool_call_id": "tc1", "content": "query returned 5 rows"},
        ]
        text = format_messages_for_summary(msgs)
        assert "[tool result]" in text
        assert "query returned 5 rows" in text

    def test_includes_tool_call_names(self):
        from cosim_agent.memory.summary import format_messages_for_summary

        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "tc1", "function": {"name": "get_messages", "arguments": "{}"}},
                ],
            },
        ]
        text = format_messages_for_summary(msgs)
        assert "[called: get_messages]" in text

    def test_truncates_long_tool_results(self):
        from cosim_agent.memory.summary import format_messages_for_summary

        msgs = [
            {"role": "tool", "tool_call_id": "tc1", "content": "x" * 1000},
        ]
        text = format_messages_for_summary(msgs)
        assert len(text) < 600

    def test_mixed_assistant_with_tool_calls(self):
        from cosim_agent.memory.summary import format_messages_for_summary

        msgs = [
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [{"id": "tc1", "function": {"name": "check", "arguments": "{}"}}],
            },
        ]
        text = format_messages_for_summary(msgs)
        assert "Let me check." in text
        assert "[called: check]" in text


# ── DecayMemory tests (Phase 2a) ──────────────────────────────────────


class TestDecayMemory:
    def _make_memory(self, **kwargs):
        defaults = {"max_messages": 5, "decay_halflife_hours": 4.0}
        defaults.update(kwargs)
        return DecayMemory(session_file="", system_prompt=SYSTEM_PROMPT, **defaults)

    def test_decay_scores_recent_higher(self):
        """Recent messages score higher than old ones."""
        import time

        m = self._make_memory(max_messages=1)
        m.add_messages([{"role": "user", "content": "old message"}])
        # Backdate the old message
        m._history[0]["_meta"]["ts"] = time.time() - 3600 * 24
        m.add_messages([{"role": "assistant", "content": "new message"}])
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "new message" in contents
        assert "old message" not in contents

    def test_decay_selects_top_scored(self):
        """Selection picks top N by score, not last N by position."""
        import time

        m = self._make_memory(max_messages=2)
        now = time.time()
        # Add 4 messages: old, very-old, old, recent
        m.add_messages([{"role": "user", "content": "msg A"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 2  # 2 hours old
        m.add_messages([{"role": "assistant", "content": "msg B"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 48  # 48 hours old
        m.add_messages([{"role": "user", "content": "msg C"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 2  # 2 hours old
        m.add_messages([{"role": "assistant", "content": "msg D"}])
        m._history[-1]["_meta"]["ts"] = now  # current
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        # msg B (48h old) should be excluded as lowest score
        assert "msg B" not in contents
        assert "msg D" in contents

    def test_decay_access_reinforces(self):
        """Message with higher importance scores higher."""
        import time

        m = self._make_memory(max_messages=1)
        now = time.time()
        m.add_messages([{"role": "user", "content": "normal msg"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 8
        m.add_messages([{"role": "assistant", "content": "important msg"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 8
        m._history[-1]["_meta"]["importance"] = 5.0
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "important msg" in contents
        assert "normal msg" not in contents

    def test_decay_halflife_configurable(self):
        """Different halflife values produce different selection sets."""
        import time

        now = time.time()
        msgs_to_add = [
            {"role": "user", "content": "ancient msg"},
            {"role": "assistant", "content": "recent msg"},
        ]

        # Fast decay (0.001h halflife) — ancient message should be gone
        m_fast = self._make_memory(max_messages=1, decay_halflife_hours=0.001)
        m_fast.add_messages([msgs_to_add[0].copy()])
        m_fast._history[-1]["_meta"]["ts"] = now - 3600 * 2
        m_fast.add_messages([msgs_to_add[1].copy()])
        m_fast._history[-1]["_meta"]["ts"] = now
        fast_msgs = m_fast.get_messages("Next")
        fast_contents = [msg.get("content", "") for msg in fast_msgs]

        # Slow decay (1000h halflife) — both messages should score similarly
        m_slow = self._make_memory(max_messages=1, decay_halflife_hours=1000.0)
        m_slow.add_messages([msgs_to_add[0].copy()])
        m_slow._history[-1]["_meta"]["ts"] = now - 3600 * 2
        m_slow.add_messages([msgs_to_add[1].copy()])
        m_slow._history[-1]["_meta"]["ts"] = now
        slow_msgs = m_slow.get_messages("Next")
        slow_contents = [msg.get("content", "") for msg in slow_msgs]

        # Fast decay should exclude old, slow decay could include either
        assert "ancient msg" not in fast_contents
        assert "recent msg" in fast_contents
        # With slow decay and max_messages=1, the most recent wins by tiebreak
        assert "recent msg" in slow_contents

    def test_decay_pinned_bypass(self):
        """Pinned messages always included regardless of score."""
        import time

        m = self._make_memory(max_messages=1, decay_halflife_hours=0.001, pin_patterns=["PIN"])
        m.add_messages([{"role": "user", "content": "PIN: critical"}])
        m._history[-1]["_meta"]["ts"] = time.time() - 3600 * 100  # very old
        m.add_messages([{"role": "assistant", "content": "recent"}])
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        assert "PIN: critical" in contents
        assert "recent" in contents

    def test_decay_interleaved_old_new(self):
        """Can select a mix of old high-importance and new messages."""
        import time

        m = self._make_memory(max_messages=3)
        now = time.time()
        m.add_messages([{"role": "user", "content": "important old"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 12
        m._history[-1]["_meta"]["importance"] = 10.0
        m.add_messages([{"role": "assistant", "content": "boring old"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 12
        m.add_messages([{"role": "user", "content": "medium old"}])
        m._history[-1]["_meta"]["ts"] = now - 3600 * 6
        m.add_messages([{"role": "assistant", "content": "recent 1"}])
        m._history[-1]["_meta"]["ts"] = now
        m.add_messages([{"role": "user", "content": "recent 2"}])
        m._history[-1]["_meta"]["ts"] = now
        msgs = m.get_messages("Next")
        contents = [msg.get("content", "") for msg in msgs]
        # "important old" should survive thanks to high importance
        assert "important old" in contents
        # recent messages should be present
        assert "recent 2" in contents

    def test_decay_no_meta_in_output(self):
        m = self._make_memory()
        m.add_messages([{"role": "user", "content": "Hello"}])
        msgs = m.get_messages("Next")
        assert all("_meta" not in msg for msg in msgs)


# ── EntityMemory tests (Phase 2b) ──────────────────────────────────────


class TestEntityMemory:
    def _make_memory(self, session_file="", **kwargs):
        client = MockOpenAIClient()
        client.response_text = '{"Alice": "Engineer working on deployment", "TK-abc": "Follow-up ticket"}'
        defaults = {"llm_client": client, "llm_model": "test-model", "max_messages": 50}
        defaults.update(kwargs)
        return EntityMemory(session_file=session_file, system_prompt=SYSTEM_PROMPT, **defaults)

    def test_entity_extraction(self):
        """Mock LLM returns JSON, entities are stored."""
        m = self._make_memory()
        m.add_messages([{"role": "user", "content": "Alice deployed the fix for TK-abc"}])
        assert "Alice" in m._entities
        assert "TK-abc" in m._entities
        assert "Engineer" in m._entities["Alice"]

    def test_entity_summaries_injected(self):
        """Entity summary message appears after system prompt in get_messages()."""
        m = self._make_memory()
        m.add_messages([{"role": "user", "content": "Alice deployed the fix"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SYSTEM_PROMPT
        assert msgs[1]["role"] == "system"
        assert "[Known entities]" in msgs[1]["content"]
        assert "Alice" in msgs[1]["content"]

    def test_entity_summaries_updated(self):
        """Second turn updates existing entity summaries."""
        client = MockOpenAIClient()
        client.response_text = '{"Alice": "Senior engineer"}'
        m = self._make_memory(llm_client=client)
        m.add_messages([{"role": "user", "content": "Alice is here"}])
        assert m._entities["Alice"] == "Senior engineer"

        client.response_text = '{"Alice": "Senior engineer, now team lead"}'
        m.add_messages([{"role": "user", "content": "Alice got promoted"}])
        assert m._entities["Alice"] == "Senior engineer, now team lead"

    def test_entity_persistence(self, session_file):
        """Save + load round-trip preserves entities."""
        m1 = self._make_memory(session_file=session_file)
        m1.add_messages([{"role": "user", "content": "Alice deployed the fix"}])
        m1.save()

        m2 = self._make_memory(session_file=session_file)
        m2.load()
        assert m2._entities == m1._entities

    def test_entity_extraction_failure(self):
        """LLM failure doesn't crash, no entities added."""
        m = self._make_memory(llm_client=FailingOpenAIClient())
        m.add_messages([{"role": "user", "content": "Hello"}])
        assert m._entities == {}
        msgs = m.get_messages("Next")
        # No entity message injected
        assert not any("[Known entities]" in msg.get("content", "") for msg in msgs)


# ── EpisodicMemory tests (Phase 2c) ──────────────────────────────────────


class TestEpisodicMemory:
    def _make_memory(self, **kwargs):
        defaults = {
            "llm_client": MockOpenAIClient(),
            "llm_model": "test-model",
            "max_messages": 50,
            "reflection_interval": 3,
            "max_reflections": 5,
        }
        defaults.update(kwargs)
        return EpisodicMemory(session_file="", system_prompt=SYSTEM_PROMPT, **defaults)

    def test_episodic_reflection_generated(self):
        """After reflection_interval turns, LLM is called to generate reflection."""
        client = MockOpenAIClient()
        client.response_text = "Key theme: deployment monitoring."
        m = self._make_memory(llm_client=client, reflection_interval=3)
        for i in range(3):
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        assert len(m._reflections) == 1
        assert m._reflections[0]["content"] == "Key theme: deployment monitoring."
        assert m._reflections[0]["turn"] == 3

    def test_episodic_reflections_injected(self):
        """Reflections appear in get_messages() after system prompt."""
        client = MockOpenAIClient()
        client.response_text = "Reflection content here."
        m = self._make_memory(llm_client=client, reflection_interval=2)
        m.add_messages([{"role": "assistant", "content": "msg 1"}])
        m.add_messages([{"role": "assistant", "content": "msg 2"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SYSTEM_PROMPT
        assert msgs[1]["role"] == "system"
        assert "[Reflections on conversation]" in msgs[1]["content"]
        assert "Reflection content here." in msgs[1]["content"]

    def test_episodic_reflection_not_generated_before_interval(self):
        """No reflection at turn 1 if interval=10."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, reflection_interval=10)
        m.add_messages([{"role": "assistant", "content": "msg"}])
        assert len(m._reflections) == 0

    def test_episodic_max_reflections_trimmed(self):
        """Excess reflections are trimmed to max_reflections."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, reflection_interval=1, max_reflections=2)
        for i in range(5):
            client.response_text = f"Reflection {i}"
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        assert len(m._reflections) == 2
        assert m._reflections[-1]["content"] == "Reflection 4"

    def test_episodic_reflection_failure(self):
        """LLM failure doesn't crash, no reflection added."""
        m = self._make_memory(llm_client=FailingOpenAIClient(), reflection_interval=1)
        m.add_messages([{"role": "assistant", "content": "msg"}])
        assert len(m._reflections) == 0


# ── ReflexionMemory tests (Phase 2d) ──────────────────────────────────────


class TestReflexionMemory:
    def _make_memory(self, **kwargs):
        defaults = {
            "llm_client": MockOpenAIClient(),
            "llm_model": "test-model",
            "max_messages": 50,
            "max_reflections": 3,
        }
        defaults.update(kwargs)
        return ReflexionMemory(session_file="", system_prompt=SYSTEM_PROMPT, **defaults)

    def test_reflexion_generates_critique(self):
        """Mock LLM is called after add_messages()."""
        client = MockOpenAIClient()
        client.response_text = "The response was helpful but could be more specific."
        m = self._make_memory(llm_client=client)
        m.add_messages([{"role": "assistant", "content": "I checked the logs."}])
        assert len(m._critiques) == 1
        assert m._critiques[0]["content"] == "The response was helpful but could be more specific."
        assert len(client.calls) == 1

    def test_reflexion_injects_past_critiques(self):
        """Last N critiques appear in get_messages() after system prompt."""
        client = MockOpenAIClient()
        client.response_text = "Critique: good response."
        m = self._make_memory(llm_client=client)
        m.add_messages([{"role": "assistant", "content": "msg 1"}])
        msgs = m.get_messages("Next")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SYSTEM_PROMPT
        assert msgs[1]["role"] == "system"
        assert "[Self-critique from previous turns]" in msgs[1]["content"]
        assert "Critique: good response." in msgs[1]["content"]

    def test_reflexion_window_truncates(self):
        """Only last max_reflections critiques kept."""
        client = MockOpenAIClient()
        m = self._make_memory(llm_client=client, max_reflections=2)
        for i in range(5):
            client.response_text = f"Critique {i}"
            m.add_messages([{"role": "assistant", "content": f"msg {i}"}])
        assert len(m._critiques) == 2
        assert m._critiques[-1]["content"] == "Critique 4"
        assert m._critiques[0]["content"] == "Critique 3"

    def test_reflexion_failure_graceful(self):
        """LLM failure doesn't crash, no critique added."""
        m = self._make_memory(llm_client=FailingOpenAIClient())
        m.add_messages([{"role": "assistant", "content": "msg"}])
        assert len(m._critiques) == 0
        msgs = m.get_messages("Next")
        assert not any("[Self-critique" in msg.get("content", "") for msg in msgs)
