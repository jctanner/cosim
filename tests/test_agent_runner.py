from lib.agent_runner import format_duration, get_model_display_name, get_model_id


class TestGetModelDisplayName:
    def test_sonnet(self):
        assert "Sonnet" in get_model_display_name("sonnet")

    def test_opus(self):
        assert "Opus" in get_model_display_name("opus")

    def test_haiku(self):
        assert "Haiku" in get_model_display_name("haiku")

    def test_unknown_returns_input(self):
        assert get_model_display_name("unknown") == "unknown"


class TestGetModelId:
    def test_sonnet(self):
        assert "sonnet" in get_model_id("sonnet")

    def test_opus(self):
        assert "opus" in get_model_id("opus")

    def test_haiku(self):
        assert "haiku" in get_model_id("haiku")

    def test_unknown_returns_input(self):
        assert get_model_id("unknown") == "unknown"


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert format_duration(125) == "2m 5s"

    def test_hours_minutes_seconds(self):
        assert format_duration(3661) == "1h 1m 1s"

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_exact_minute(self):
        assert format_duration(60) == "1m 0s"

    def test_exact_hour(self):
        assert format_duration(3600) == "1h 0s"
