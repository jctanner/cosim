"""Realm of the Forgotten Crown — Room definitions for Thornwick Village."""

ROOMS = {
    "thornwick_tavern": {
        "name": "The Shattered Tankard",
        "description": (
            "The hearth crackles with a warmth that seeps into tired bones. Rough-hewn "
            "tables fill the common room, scarred by a thousand mugs and the occasional "
            "dagger. The air is thick with woodsmoke, roasting meat, and the low murmur "
            "of travelers trading stories they'll deny in the morning. A faded banner "
            "above the bar reads 'ALL WELCOME — NO SPELLCASTING INDOORS.'"
        ),
        "exits": {"east": "thornwick_market", "south": "thornwick_gate", "up": "tavern_rooms"},
        "items": [],
        "npcs": ["innkeeper_bram"],
    },
    "tavern_rooms": {
        "name": "Upstairs Rooms — The Shattered Tankard",
        "description": (
            "A narrow hallway of doors, each one leading to a small room with a straw "
            "mattress and a candle. The floorboards creak with the memory of every "
            "adventurer who's slept here between quests. A window at the end of the hall "
            "offers a view of Thornwick's torchlit streets below."
        ),
        "exits": {"down": "thornwick_tavern"},
        "items": [],
        "npcs": [],
    },
    "thornwick_market": {
        "name": "Thornwick Market Square",
        "description": (
            "The market square bustles with merchants hawking wares from canvas-covered "
            "stalls. A blacksmith's hammer rings out a steady rhythm from the forge to "
            "the north. To the south, a weathered signpost points toward the gates and "
            "the wilderness beyond. The cobblestones are worn smooth by generations of "
            "boot heels and cart wheels."
        ),
        "exits": {"west": "thornwick_tavern", "north": "thornwick_smithy",
                  "east": "thornwick_temple", "south": "thornwick_gate"},
        "items": [],
        "npcs": ["merchant_vesra"],
    },
    "thornwick_smithy": {
        "name": "Grimholt's Forge",
        "description": (
            "Heat rolls off the open forge in waves. Grimholt, a barrel-chested dwarf, "
            "works a glowing blade with precise, unhurried strokes. Weapons hang from "
            "every wall — swords, axes, maces, and a few pieces whose purpose you can't "
            "quite determine. The anvil is older than the town itself, or so Grimholt claims."
        ),
        "exits": {"south": "thornwick_market"},
        "items": [],
        "npcs": ["blacksmith_grimholt"],
    },
    "thornwick_temple": {
        "name": "Temple of the Dawn",
        "description": (
            "Pale light filters through stained glass, casting colored shadows across "
            "stone pews. The temple is quiet — the kind of quiet that feels intentional, "
            "as if silence is part of the architecture. An altar of white marble stands "
            "at the far end, perpetually lit by a flame that never gutters."
        ),
        "exits": {"west": "thornwick_market"},
        "items": [],
        "npcs": ["priestess_lyana"],
    },
    "thornwick_gate": {
        "name": "Thornwick South Gate",
        "description": (
            "The south gate of Thornwick stands open but guarded. Two soldiers in "
            "mismatched armor lean on their spears with the practiced boredom of men "
            "who've never seen a real threat. Beyond the gate, a dirt road winds south "
            "through farmland toward the dark treeline of the Whispering Woods."
        ),
        "exits": {"north": "thornwick_market", "south": "crossroads"},
        "items": [],
        "npcs": ["guard_captain"],
    },
    "crossroads": {
        "name": "The Crossroads",
        "description": (
            "Two roads meet at a weathered stone marker carved with directions so old "
            "they've become suggestions. North leads back to Thornwick. East disappears "
            "into the Whispering Woods. West climbs toward the Ironridge Mountains. "
            "South stretches into open plains dotted with ancient ruins. A tattered "
            "bounty notice is nailed to the marker."
        ),
        "exits": {"north": "thornwick_gate", "east": "whispering_woods",
                  "west": "mountain_path", "south": "open_plains"},
        "items": ["bounty_notice"],
        "npcs": [],
    },
    "whispering_woods": {
        "name": "Edge of the Whispering Woods",
        "description": (
            "The trees close in like curtains. Dappled light filters through a canopy "
            "so thick it turns noon into twilight. The forest earned its name — the "
            "wind through the branches sounds like whispered words in a language you "
            "almost recognize. Paths branch deeper into the woods, and not all of them "
            "lead somewhere safe."
        ),
        "exits": {"west": "crossroads"},
        "items": [],
        "npcs": [],
        # TODO: Add deeper forest rooms, mob spawns, quest hooks
    },
    "mountain_path": {
        "name": "Ironridge Mountain Path",
        "description": (
            "The road narrows to a rocky trail that switchbacks up the mountainside. "
            "Loose scree crunches underfoot. Far above, you can see the dark mouth of "
            "what might be a mine — or a cave. The air is thinner here, and colder."
        ),
        "exits": {"east": "crossroads"},
        "items": [],
        "npcs": [],
        # TODO: Add mine entrance, mountain zones
    },
    "open_plains": {
        "name": "The Open Plains",
        "description": (
            "Tall grass stretches to the horizon in every direction, broken only by "
            "crumbling stone walls that mark the borders of farms long abandoned. "
            "Somewhere to the south, barely visible, the ruins of an old watchtower "
            "rise against the sky like a broken tooth."
        ),
        "exits": {"north": "crossroads"},
        "items": [],
        "npcs": [],
        # TODO: Add ruins zone, wandering mobs
    },
    # Easter egg: The Advisor's hidden room
    "advisors_sanctum": {
        "name": "The Advisor's Sanctum",
        "description": (
            "You've found a hidden chamber behind a false wall in the deepest part of "
            "the old library. Bookshelves line every surface, floor to ceiling, filled "
            "with volumes on arcane programming arts: socket incantations, game loop "
            "rituals, state persistence spells. In the center sits an ancient figure "
            "at a desk covered in scrolls. He looks up and smiles, as if he's been "
            "expecting you."
        ),
        "exits": {"out": "thornwick_temple"},
        "items": [],
        "npcs": ["the_advisor"],
    },
}
