"""Realm of the Forgotten Crown — Spell definitions."""

SPELLS = {
    # --- Damage spells (Mage) ---
    "fireball": {
        "name": "Fireball",
        "description": "A ball of fire that explodes on impact.",
        "class": "Mage",
        "type": "damage",
        "base_damage": 35,
        "mana_cost": 15,
        "level_req": 1,
        "scaling": 2.5,  # damage per caster level
        # NOTE: At level 20, fireball does 35 + (20 * 2.5) = 85 damage per cast
        # Compare to Warrior melee at level 20: ~23 damage per round
        # The DPS gap is intentional for burst vs sustained, but it's too wide
        # TODO: Reduce scaling to 1.5 or add Warrior sustained damage bonus
    },
    "lightning_bolt": {
        "name": "Lightning Bolt",
        "description": "A crackling bolt of electricity arcs toward your target.",
        "class": "Mage",
        "type": "damage",
        "base_damage": 25,
        "mana_cost": 12,
        "level_req": 3,
        "scaling": 2.0,
    },
    "ice_shard": {
        "name": "Ice Shard",
        "description": "Razor-sharp ice pierces your enemy and slows their movement.",
        "class": "Mage",
        "type": "damage",
        "base_damage": 20,
        "mana_cost": 10,
        "level_req": 1,
        "scaling": 1.5,
        "effect": "slow",
        "effect_duration": 3,  # rounds
    },
    "arcane_blast": {
        "name": "Arcane Blast",
        "description": "Pure arcane energy tears through defenses.",
        "class": "Mage",
        "type": "damage",
        "base_damage": 50,
        "mana_cost": 25,
        "level_req": 10,
        "scaling": 3.0,
        "ignores_armor": True,
    },

    # --- Healing spells (Cleric) ---
    "heal": {
        "name": "Heal",
        "description": "Mend wounds with divine light.",
        "class": "Cleric",
        "type": "heal",
        "base_heal": 30,
        "mana_cost": 12,
        "level_req": 1,
        "scaling": 2.0,
    },
    "greater_heal": {
        "name": "Greater Heal",
        "description": "A powerful surge of healing energy.",
        "class": "Cleric",
        "type": "heal",
        "base_heal": 60,
        "mana_cost": 25,
        "level_req": 8,
        "scaling": 3.0,
    },

    # --- Buff spells ---
    "shield_of_faith": {
        "name": "Shield of Faith",
        "description": "A shimmering barrier absorbs incoming damage.",
        "class": "Cleric",
        "type": "buff",
        "effect": "defense_up",
        "amount": 10,
        "mana_cost": 15,
        "level_req": 5,
        "duration": 10,  # rounds
    },
    "battle_cry": {
        "name": "Battle Cry",
        "description": "A thunderous shout that strengthens nearby allies.",
        "class": "Warrior",
        "type": "buff",
        "effect": "damage_up",
        "amount": 5,
        "mana_cost": 0,  # Warriors don't use mana — this uses a cooldown instead
        "level_req": 5,
        "duration": 5,
        "cooldown": 10,  # rounds between uses
        # NOTE: This is the Warrior's only "spell" — it's really an ability
        # Compared to Mage's 4 damage spells, Warriors are underequipped
        # TODO: Add more Warrior abilities (Shield Bash, Cleave, Intimidate)
    },

    # --- Utility ---
    "detect_traps": {
        "name": "Detect Traps",
        "description": "Your senses sharpen, revealing hidden dangers.",
        "class": "Thief",
        "type": "utility",
        "effect": "detect",
        "mana_cost": 5,
        "level_req": 3,
        "duration": 20,
    },
    "bard_song": {
        "name": "Inspiring Ballad",
        "description": "Your song lifts the spirits of all nearby allies.",
        "class": "Bard",
        "type": "buff",
        "effect": "xp_bonus",
        "amount": 10,  # percentage bonus
        "mana_cost": 10,
        "level_req": 1,
        "duration": 30,
        "area": True,  # affects all party members
    },
}


def calculate_spell_damage(spell, caster_level, spell_bonus=0):
    """Calculate spell damage with level scaling and gear bonus."""
    base = spell["base_damage"]
    scaling = spell.get("scaling", 0)
    total = base + int(caster_level * scaling) + spell_bonus
    return total


def calculate_spell_heal(spell, caster_level):
    """Calculate healing amount with level scaling."""
    base = spell["base_heal"]
    scaling = spell.get("scaling", 0)
    return base + int(caster_level * scaling)


def can_cast(player, spell):
    """Check if a player can cast a spell."""
    if player.level < spell.get("level_req", 1):
        return False, f"Requires level {spell['level_req']}"
    if player.mana < spell.get("mana_cost", 0):
        return False, "Not enough mana"
    if player.class_name != spell.get("class", ""):
        return False, f"Only {spell['class']} can cast this"
    return True, "OK"
