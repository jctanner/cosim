from lib.docs import slugify, get_accessible_folders, DEFAULT_FOLDER_ACCESS


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        assert slugify("Hello, World! #2024") == "hello-world-2024"

    def test_unicode_normalization(self):
        assert slugify("café résumé") == "cafe-resume"

    def test_empty_string(self):
        assert slugify("") == "untitled"

    def test_all_special_chars(self):
        assert slugify("!!!@@@###") == "untitled"

    def test_truncation(self):
        long_title = "a-" * 50
        result = slugify(long_title, max_length=20)
        assert len(result) <= 20

    def test_truncation_at_word_boundary(self):
        result = slugify("hello world foo bar baz qux", max_length=15)
        assert "-" not in result or not result.endswith("-")

    def test_strips_leading_trailing_hyphens(self):
        assert slugify("--hello--") == "hello"

    def test_collapses_multiple_hyphens(self):
        assert slugify("hello    world") == "hello-world"


class TestGetAccessibleFolders:
    def test_empty_access(self):
        assert get_accessible_folders("alice") == set()

    def test_single_folder(self):
        DEFAULT_FOLDER_ACCESS["shared"] = {"alice", "bob"}
        assert get_accessible_folders("alice") == {"shared"}

    def test_multiple_folders(self):
        DEFAULT_FOLDER_ACCESS["shared"] = {"alice"}
        DEFAULT_FOLDER_ACCESS["engineering"] = {"alice"}
        DEFAULT_FOLDER_ACCESS["leadership"] = {"bob"}
        assert get_accessible_folders("alice") == {"shared", "engineering"}

    def test_no_access(self):
        DEFAULT_FOLDER_ACCESS["leadership"] = {"bob"}
        assert get_accessible_folders("alice") == set()
