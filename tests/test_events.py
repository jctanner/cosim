from lib import events


class TestEventPool:
    def test_add_returns_index(self):
        idx = events.add_event({"name": "Outage", "severity": "high"})
        assert idx == 0

    def test_add_multiple(self):
        events.add_event({"name": "A"})
        idx = events.add_event({"name": "B"})
        assert idx == 1
        assert len(events.get_event_pool()) == 2

    def test_update_event(self):
        events.add_event({"name": "A"})
        events.update_event(0, {"name": "A-updated"})
        pool = events.get_event_pool()
        assert pool[0]["name"] == "A-updated"

    def test_update_out_of_bounds(self):
        events.update_event(99, {"name": "nope"})
        assert events.get_event_pool() == []

    def test_delete_event(self):
        events.add_event({"name": "A"})
        events.add_event({"name": "B"})
        events.delete_event(0)
        pool = events.get_event_pool()
        assert len(pool) == 1
        assert pool[0]["name"] == "B"

    def test_delete_out_of_bounds(self):
        events.add_event({"name": "A"})
        events.delete_event(99)
        assert len(events.get_event_pool()) == 1


class TestFireEvent:
    def test_logs_event(self):
        entry = events.fire_event({"name": "Outage", "severity": "high", "actions": []})
        assert entry["name"] == "Outage"
        assert entry["severity"] == "high"
        assert "timestamp" in entry

    def test_defaults(self):
        entry = events.fire_event({})
        assert entry["name"] == "Custom Event"
        assert entry["severity"] == "medium"
        assert entry["actions"] == []

    def test_log_accumulates(self):
        events.fire_event({"name": "A"})
        events.fire_event({"name": "B"})
        assert len(events.get_event_log()) == 2


class TestInitEventPool:
    def test_copies_from_scenario(self):
        events.SCENARIO_EVENTS.clear()
        events.SCENARIO_EVENTS.append({"name": "Scenario Event"})
        events.init_event_pool()
        pool = events.get_event_pool()
        assert len(pool) == 1
        assert pool[0]["name"] == "Scenario Event"
        events.SCENARIO_EVENTS.clear()

    def test_clears_log(self):
        events.fire_event({"name": "A"})
        events.init_event_pool()
        assert events.get_event_log() == []


class TestSnapshotRestore:
    def test_round_trip(self):
        events.add_event({"name": "A"})
        events.fire_event({"name": "B"})
        pool_snap = events.get_pool_snapshot()
        log_snap = events.get_log_snapshot()
        events.clear_events()
        assert events.get_event_pool() == []
        assert events.get_event_log() == []
        events.restore_events(pool_snap, log_snap)
        assert len(events.get_event_pool()) == 1
        assert len(events.get_event_log()) == 1
