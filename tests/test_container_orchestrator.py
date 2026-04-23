"""Tests for container orchestrator helper functions."""

from lib.container_orchestrator import (
    _filter_trigger_messages_for_agent,
    _resolve_agent_trigger_channels,
)


class TestFilterTriggerMessagesForAgent:
    """Tests for _filter_trigger_messages_for_agent."""

    def test_returns_none_for_empty_input(self):
        assert _filter_trigger_messages_for_agent([], {"#general"}, 0) is None
        assert _filter_trigger_messages_for_agent(None, {"#general"}, 0) is None

    def test_filters_by_channel_membership(self):
        msgs = [
            {"id": 1, "sender": "Human", "channel": "#general", "content": "hello"},
            {"id": 2, "sender": "Human", "channel": "#secret", "content": "hidden"},
        ]
        result = _filter_trigger_messages_for_agent(msgs, {"#general"}, last_seen_id=0)
        assert len(result) == 1
        assert result[0]["channel"] == "#general"

    def test_excludes_already_seen_messages(self):
        """Messages the agent has already seen should not appear as headlines.

        This is the core bug: when autonomous rounds re-trigger agents on
        channels they already posted in, the original human message (which
        the agent already responded to) was leaking through as a headline,
        causing agents to respond to it again with near-identical messages.
        """
        msgs = [
            {"id": 7, "sender": "Violet", "channel": "#open-forum", "content": "Hi everyone, I'm violet"},
        ]
        agent_channels = {"#open-forum", "#thinking-room"}

        # Agent already saw through message 8 (responded in a previous round)
        result = _filter_trigger_messages_for_agent(msgs, agent_channels, last_seen_id=8)

        # The old message should NOT be included
        assert result is None or len(result) == 0

    def test_includes_unseen_messages(self):
        msgs = [
            {"id": 7, "sender": "Violet", "channel": "#open-forum", "content": "Old message"},
            {"id": 15, "sender": "Violet", "channel": "#open-forum", "content": "New message"},
        ]
        agent_channels = {"#open-forum"}

        result = _filter_trigger_messages_for_agent(msgs, agent_channels, last_seen_id=10)

        assert len(result) == 1
        assert result[0]["id"] == 15
        assert result[0]["content"] == "New message"

    def test_mixed_seen_and_unseen_across_channels(self):
        msgs = [
            {"id": 3, "sender": "Alice", "channel": "#general", "content": "Old general msg"},
            {"id": 12, "sender": "Bob", "channel": "#general", "content": "New general msg"},
            {"id": 5, "sender": "Carol", "channel": "#secret", "content": "Not my channel"},
            {"id": 14, "sender": "Dave", "channel": "#open-forum", "content": "New forum msg"},
        ]
        agent_channels = {"#general", "#open-forum"}

        result = _filter_trigger_messages_for_agent(msgs, agent_channels, last_seen_id=10)

        assert len(result) == 2
        ids = {m["id"] for m in result}
        assert ids == {12, 14}

    def test_last_seen_zero_includes_all_in_channel(self):
        """With last_seen_id=0 (first round), all messages in agent channels are included."""
        msgs = [
            {"id": 1, "sender": "Human", "channel": "#general", "content": "First"},
            {"id": 2, "sender": "Human", "channel": "#general", "content": "Second"},
        ]
        result = _filter_trigger_messages_for_agent(msgs, {"#general"}, last_seen_id=0)
        assert len(result) == 2


class TestResolveAgentTriggerChannels:
    """Tests for _resolve_agent_trigger_channels."""

    def test_normal_channel_intersection(self):
        result = _resolve_agent_trigger_channels(
            trigger_ch_set={"#general", "#engineering"},
            agent_channels={"#general", "#support"},
        )
        assert result == {"#general"}

    def test_director_channel_preserved_even_without_membership(self):
        """Director channels are dynamically created and won't appear in
        the agent's membership list. They must not be dropped by the
        intersection, or the agent's turn prompt will have an empty
        channel set (the 'activity in .' bug).
        """
        result = _resolve_agent_trigger_channels(
            trigger_ch_set={"#director-director"},
            agent_channels={"#briefing", "#research", "#synthesis"},
        )
        assert "#director-director" in result

    def test_director_channel_with_regular_channels(self):
        result = _resolve_agent_trigger_channels(
            trigger_ch_set={"#director-director", "#briefing"},
            agent_channels={"#briefing", "#research"},
        )
        assert result == {"#director-director", "#briefing"}

    def test_empty_trigger_set(self):
        result = _resolve_agent_trigger_channels(
            trigger_ch_set=set(),
            agent_channels={"#general"},
        )
        assert result == set()

    def test_no_overlap_no_director(self):
        result = _resolve_agent_trigger_channels(
            trigger_ch_set={"#sales"},
            agent_channels={"#engineering"},
        )
        assert result == set()
