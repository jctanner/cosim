"""Realm of the Forgotten Crown — Item definitions."""

ITEMS = {
    # --- Weapons ---
    "rusty_sword": {
        "name": "Rusty Sword",
        "description": "A blade that's seen better centuries. Still sharp enough to hurt.",
        "type": "weapon",
        "slot": "mainhand",
        "damage": 5,
        "level_req": 1,
        "value": 10,
    },
    "iron_longsword": {
        "name": "Iron Longsword",
        "description": "A solid blade forged by competent hands. Nothing fancy, everything functional.",
        "type": "weapon",
        "slot": "mainhand",
        "damage": 12,
        "level_req": 5,
        "value": 75,
    },
    "staff_of_sparks": {
        "name": "Staff of Sparks",
        "description": "A gnarled wooden staff capped with a crystal that crackles with latent energy.",
        "type": "weapon",
        "slot": "mainhand",
        "damage": 8,
        "spell_bonus": 15,  # Mages get this bonus on spell damage
        "level_req": 3,
        "value": 120,
    },
    "dagger_of_shadows": {
        "name": "Dagger of Shadows",
        "description": "The blade seems to drink the light. Thieves swear it makes them quieter.",
        "type": "weapon",
        "slot": "mainhand",
        "damage": 7,
        "crit_bonus": 10,  # percentage
        "level_req": 5,
        "value": 90,
    },

    # --- Armor ---
    "leather_armor": {
        "name": "Leather Armor",
        "description": "Supple leather hardened with oil. Provides modest protection without restricting movement.",
        "type": "armor",
        "slot": "body",
        "defense": 5,
        "level_req": 1,
        "value": 30,
    },
    "chainmail": {
        "name": "Chainmail Hauberk",
        "description": "Interlocking iron rings that turn glancing blows. Heavy but reliable.",
        "type": "armor",
        "slot": "body",
        "defense": 15,
        "level_req": 8,
        "value": 200,
    },
    "wooden_shield": {
        "name": "Wooden Shield",
        "description": "A round shield of oak banded with iron. It'll stop one good hit.",
        "type": "armor",
        "slot": "offhand",
        "defense": 8,
        "level_req": 3,
        "value": 45,
    },

    # --- Consumables ---
    "healing_potion": {
        "name": "Healing Potion",
        "description": "A stoppered vial of red liquid. Tastes like copper and optimism.",
        "type": "consumable",
        "effect": "heal",
        "amount": 30,
        "value": 25,
    },
    "mana_potion": {
        "name": "Mana Potion",
        "description": "Blue liquid that swirls on its own. Restores magical energy.",
        "type": "consumable",
        "effect": "restore_mana",
        "amount": 25,
        "value": 30,
    },

    # --- The exploit item ---
    "enchanted_copper_ring": {
        "name": "Enchanted Copper Ring",
        "description": "A simple copper band with a faint magical shimmer. Worth more than it should be.",
        "type": "accessory",
        "slot": "ring",
        "defense": 1,
        "value": 15,  # BUG: buy price is 10, sell price is 15 — infinite gold exploit
        "buy_price": 10,  # TODO: Fix merchant pricing — buy should >= sell
        "level_req": 1,
    },

    # --- Quest items ---
    "bounty_notice": {
        "name": "Tattered Bounty Notice",
        "description": (
            "A weather-worn notice nailed to the crossroads marker: 'BOUNTY: Kobolds "
            "in the Northern Pass — 50 Gold. See Captain Hendricks at the Thornwick "
            "guardhouse. WARNING: They set traps. Bring a rogue.'"
        ),
        "type": "quest",
        "value": 0,
    },
    "crown_shard": {
        "name": "Shard of the Forgotten Crown",
        "description": (
            "A fragment of obsidian-dark metal that hums with ancient power. One piece "
            "of something that was once whole. The edges are warm to the touch."
        ),
        "type": "quest",
        "value": 0,  # priceless
        "legendary": True,
    },

    # --- Easter egg: The Signed Tome ---
    "signed_tome": {
        "name": "Signed Tome of MUD Programming",
        "description": (
            "A leather-bound volume of extraordinary weight and importance. The cover "
            "reads 'The Art of MUD Programming' in gold-embossed letters. Inside the "
            "front cover, a handwritten inscription: 'To those who build worlds with "
            "words — may your game loops never block and your state always persist.' "
            "It is signed by The Advisor himself. Tyraziel the Head Wizard has been "
            "searching for this book."
        ),
        "type": "quest",
        "value": 0,  # priceless
        "legendary": True,
        "quest": "deliver_to_tyraziel",
    },
}
