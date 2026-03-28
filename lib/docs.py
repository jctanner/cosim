"""Shared document utilities."""

import re
import unicodedata


# Folder definitions: name -> {type, description}
DEFAULT_FOLDERS = {
    "shared":      {"type": "shared",     "description": "Shared team documents"},
    "public":      {"type": "public",     "description": "Customer-visible documents"},
    "engineering": {"type": "department", "description": "Engineering department"},
    "sales":       {"type": "department", "description": "Sales department"},
    "support":     {"type": "department", "description": "Support department"},
    "leadership":  {"type": "department", "description": "Leadership team"},
    "sarah":       {"type": "personal",  "description": "Sarah's private folder"},
    "marcus":      {"type": "personal",  "description": "Marcus's private folder"},
    "priya":       {"type": "personal",  "description": "Priya's private folder"},
    "alex":        {"type": "personal",  "description": "Alex's private folder"},
    "jordan":      {"type": "personal",  "description": "Jordan's private folder"},
    "taylor":      {"type": "personal",  "description": "Taylor's private folder"},
    "dana":        {"type": "personal",  "description": "Dana's private folder"},
    "morgan":      {"type": "personal",  "description": "Morgan's private folder"},
    "marketing":   {"type": "department", "description": "Marketing department"},
    "devops":      {"type": "department", "description": "DevOps department"},
    "riley":       {"type": "personal",  "description": "Riley's private folder"},
    "casey":       {"type": "personal",  "description": "Casey's private folder"},
    "nadia":       {"type": "personal",  "description": "Nadia's private folder"},
}

# Folder access: folder_name -> set of persona keys allowed
DEFAULT_FOLDER_ACCESS = {
    "shared":      {"pm", "engmgr", "architect", "senior", "support", "sales", "ceo", "cfo", "marketing", "devops", "projmgr"},
    "public":      {"pm", "engmgr", "architect", "senior", "support", "sales", "ceo", "cfo", "marketing", "devops", "projmgr"},
    "engineering": {"pm", "engmgr", "architect", "senior", "devops", "projmgr"},
    "sales":       {"pm", "sales", "ceo", "cfo"},
    "support":     {"pm", "engmgr", "support", "devops", "projmgr"},
    "leadership":  {"pm", "ceo", "cfo", "projmgr"},
    "sarah":       {"pm"},
    "marcus":      {"engmgr"},
    "priya":       {"architect"},
    "alex":        {"senior"},
    "jordan":      {"support"},
    "taylor":      {"sales"},
    "dana":        {"ceo"},
    "morgan":      {"cfo"},
    "marketing":   {"pm", "marketing", "sales", "ceo", "projmgr"},
    "devops":      {"pm", "engmgr", "devops", "projmgr"},
    "riley":       {"marketing"},
    "casey":       {"devops"},
    "nadia":       {"projmgr"},
}


def get_accessible_folders(persona_key: str) -> set[str]:
    """Return the set of folder names a persona can access."""
    return {
        folder for folder, members in DEFAULT_FOLDER_ACCESS.items()
        if persona_key in members
    }


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
