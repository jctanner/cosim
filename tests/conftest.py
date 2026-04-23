import pytest

from lib import email, events, memos, blog, docs, gitlab


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset in-memory module state before each test."""
    email.clear_inbox()
    events.clear_events()
    memos.clear_memos()
    blog.clear_blog()
    docs.DEFAULT_FOLDERS.clear()
    docs.DEFAULT_FOLDER_ACCESS.clear()
    gitlab.DEFAULT_REPO_ACCESS.clear()
    yield
