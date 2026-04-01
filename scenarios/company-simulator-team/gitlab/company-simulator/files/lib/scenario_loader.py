"""Scenario loader — reads scenario.yaml and populates module-level config."""

import yaml
from pathlib import Path


SCENARIOS_DIR = Path(__file__).parent.parent / "scenarios"


def load_scenario(scenario_name: str) -> None:
    """Load a scenario by name, populating module-level config dicts.

    Must be called before any code accesses PERSONAS, DEFAULT_CHANNELS, etc.
    Mutates dicts in place so all existing references stay valid.
    """
    import lib.personas as personas_mod
    import lib.docs as docs_mod

    scenario_dir = SCENARIOS_DIR / scenario_name
    config_path = scenario_dir / "scenario.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"Scenario not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # --- Populate personas.PERSONAS ---
    personas_mod.PERSONAS.clear()
    for key, char_info in config["characters"].items():
        char_file = char_info.get("character_file", f"characters/{key}.md")
        personas_mod.PERSONAS[key] = {
            "name": key,
            "display_name": char_info["display_name"],
            "team_description": char_info.get("team_description", key),
            "character_file": str(scenario_dir / char_file),
        }

    # --- Populate personas.DEFAULT_CHANNELS ---
    personas_mod.DEFAULT_CHANNELS.clear()
    personas_mod.DEFAULT_CHANNELS.update(config["channels"])

    # --- Populate personas.DEFAULT_MEMBERSHIPS ---
    personas_mod.DEFAULT_MEMBERSHIPS.clear()
    for key, ch_list in config["memberships"].items():
        personas_mod.DEFAULT_MEMBERSHIPS[key] = set(ch_list)

    # --- Populate personas.RESPONSE_TIERS and PERSONA_TIER ---
    personas_mod.RESPONSE_TIERS.clear()
    personas_mod.PERSONA_TIER.clear()
    for tier_str, keys in config["response_tiers"].items():
        tier_num = int(tier_str)
        personas_mod.RESPONSE_TIERS[tier_num] = keys
        for k in keys:
            personas_mod.PERSONA_TIER[k] = tier_num

    # --- Populate docs.DEFAULT_FOLDERS ---
    docs_mod.DEFAULT_FOLDERS.clear()
    docs_mod.DEFAULT_FOLDERS.update(config["folders"])

    # --- Populate docs.DEFAULT_FOLDER_ACCESS ---
    docs_mod.DEFAULT_FOLDER_ACCESS.clear()
    for folder_name, access_list in config["folder_access"].items():
        docs_mod.DEFAULT_FOLDER_ACCESS[folder_name] = set(access_list)

    print(f"Scenario loaded: {config.get('name', scenario_name)}")
    print(f"  Characters: {len(personas_mod.PERSONAS)}")
    print(f"  Channels: {len(personas_mod.DEFAULT_CHANNELS)}")
    print(f"  Folders: {len(docs_mod.DEFAULT_FOLDERS)}")


def list_scenarios() -> list[dict]:
    """List all available scenarios."""
    scenarios = []
    if not SCENARIOS_DIR.exists():
        return scenarios
    for d in sorted(SCENARIOS_DIR.iterdir()):
        config_path = d / "scenario.yaml"
        if d.is_dir() and config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                scenarios.append({
                    "key": d.name,
                    "name": config.get("name", d.name),
                    "description": config.get("description", ""),
                    "characters": len(config.get("characters", {})),
                })
            except Exception:
                pass
    return scenarios
