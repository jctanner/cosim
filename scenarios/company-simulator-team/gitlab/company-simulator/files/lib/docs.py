"""Shared document utilities."""

import re
import unicodedata

# Folder definitions and access controls.
# Populated at startup by lib.scenario_loader.load_scenario().
DEFAULT_FOLDERS: dict[str, dict] = {}
DEFAULT_FOLDER_ACCESS: dict[str, set[str]] = {}


def get_accessible_folders(persona_key: str) -> set[str]:
    """Return the set of folder names a persona can access."""
    return {folder for folder, members in DEFAULT_FOLDER_ACCESS.items() if persona_key in members}


def slugify(title: str, max_length: int = 80) -> str:
    """Convert a title to a filesystem-safe slug.

    Normalizes unicode, lowercases, replaces non-alphanumeric runs with
    hyphens, and truncates at a word boundary within *max_length* chars.
    """
    # Normalize unicode → ASCII approximation
    text = unicodedata.normalize("NFKD", title)
    text = text.encode("ascii", "ignore").decode("ascii")

    # Lowercase and replace non-alphanumeric chars with hyphens
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")

    if not text:
        return "untitled"

    # Truncate at word boundary
    if len(text) > max_length:
        text = text[:max_length]
        # Try to cut at the last hyphen to avoid partial words
        last_hyphen = text.rfind("-")
        if last_hyphen > 0:
            text = text[:last_hyphen]

    return text
