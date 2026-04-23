import re

from lib.tickets import generate_ticket_id


class TestGenerateTicketId:
    def test_format(self):
        tid = generate_ticket_id("Fix login bug", 1700000000.0)
        assert re.match(r"^TK-[A-F0-9]{6}$", tid)

    def test_deterministic(self):
        a = generate_ticket_id("Fix login bug", 1700000000.0)
        b = generate_ticket_id("Fix login bug", 1700000000.0)
        assert a == b

    def test_different_titles(self):
        a = generate_ticket_id("Fix login bug", 1700000000.0)
        b = generate_ticket_id("Add search feature", 1700000000.0)
        assert a != b

    def test_different_timestamps(self):
        a = generate_ticket_id("Fix login bug", 1700000000.0)
        b = generate_ticket_id("Fix login bug", 1700000001.0)
        assert a != b
