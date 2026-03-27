"""Shared document utilities."""

import re
import unicodedata


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
