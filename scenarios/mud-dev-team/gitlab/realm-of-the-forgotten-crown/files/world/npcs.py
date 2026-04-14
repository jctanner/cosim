"""Realm of the Forgotten Crown — NPC definitions."""

NPCS = {
    "innkeeper_bram": {
        "name": "Bram the Innkeeper",
        "description": (
            "A stocky man with a red nose and a laugh that fills the room. He dries "
            "mugs with a rag that hasn't been clean since the tavern opened."
        ),
        "level": 5,
        "hp": 80,
        "hostile": False,
        "dialogue": {
            "greet": "Welcome to the Shattered Tankard! Rest your bones, have an ale.",
            "quest": (
                "If you're looking for work, there's a bounty on kobolds at the Northern "
                "Pass. Talk to the guard captain at the south gate. And if you're feeling "
                "brave — or foolish — ask about the Crown."
            ),
            "crown": (
                "The Forgotten Crown? Aye, I've heard the stories. An artifact of the old "
                "kingdom, shattered into pieces and scattered across the realm. They say "
                "whoever reassembles it gains power over the land itself. But every "
                "adventurer who's gone looking has come back empty-handed. Or hasn't come "
                "back at all."
            ),
            "ale": "That'll be 2 gold. Best ale in Thornwick — well, only ale in Thornwick.",
        },
        "inventory": ["healing_potion"],
        "merchant": False,
    },

    "merchant_vesra": {
        "name": "Vesra the Merchant",
        "description": (
            "A sharp-eyed woman with an abacus and a smile that calculates profit margins. "
            "Her stall is piled with goods from every corner of the realm."
        ),
        "level": 3,
        "hp": 40,
        "hostile": False,
        "dialogue": {
            "greet": "Looking to buy? Looking to sell? Either way, you've come to the right place.",
            "buy": "Let me see what you've got. I pay fair prices — mostly.",
            "sell": "Browse my wares. Everything's priced to move.",
        },
        "inventory": [
            "rusty_sword", "leather_armor", "healing_potion", "mana_potion",
            "enchanted_copper_ring",  # BUG: infinite stock, buy/sell price mismatch
        ],
        "merchant": True,
        "buy_modifier": 0.5,   # pays 50% of item value
        "sell_modifier": 1.0,  # sells at 100% of item value
        # TODO: Track merchant inventory — currently infinite stock on all items
        # This is the root cause of the gold exploit with enchanted_copper_ring
    },

    "blacksmith_grimholt": {
        "name": "Grimholt the Blacksmith",
        "description": (
            "A dwarf of few words and many hammers. His arms are scarred from decades "
            "at the forge. He sizes you up the way he sizes up metal — looking for flaws."
        ),
        "level": 10,
        "hp": 150,
        "hostile": False,
        "dialogue": {
            "greet": "Hmph. Need something forged? Or are you just here to watch?",
            "forge": (
                "I can repair your gear or upgrade it — if you bring me the materials. "
                "Iron ore from the Ironridge mines, and I'll make you something worth swinging."
            ),
            "quality": "I don't do cheap work. You want cheap, go to Vesra.",
        },
        "inventory": ["iron_longsword", "chainmail", "wooden_shield"],
        "merchant": True,
        "buy_modifier": 0.3,   # pays less — he's a craftsman, not a trader
        "sell_modifier": 1.2,  # sells higher — premium for quality
    },

    "priestess_lyana": {
        "name": "Priestess Lyana",
        "description": (
            "A woman of middle years with steady hands and steadier eyes. Her robes are "
            "simple white linen, but she carries herself with the quiet authority of "
            "someone who has faced darkness and chosen to light a candle."
        ),
        "level": 12,
        "hp": 60,
        "hostile": False,
        "dialogue": {
            "greet": "The Dawn welcomes you, traveler. How may I serve?",
            "heal": "Be still. Let the light find what is broken.",
            "bless": (
                "May your path be lit and your purpose true. The Dawn does not promise "
                "safety — only clarity."
            ),
            "crown": (
                "The Forgotten Crown... yes, I know of it. The temple has records that "
                "speak of its creation. It was forged to unite, not to rule. But power "
                "always invites corruption, and so it was broken. Perhaps that was mercy."
            ),
        },
        "inventory": ["healing_potion", "mana_potion"],
        "merchant": False,
        "services": ["heal", "bless"],  # can heal players and grant buffs
    },

    "guard_captain": {
        "name": "Captain Hendricks",
        "description": (
            "A tired soldier with a permanent squint and armor that's been repaired "
            "more times than replaced. He looks like a man who's counting the days "
            "until retirement."
        ),
        "level": 8,
        "hp": 120,
        "hostile": False,
        "dialogue": {
            "greet": "Another adventurer. Good. We could use the help.",
            "bounty": (
                "Kobolds in the Northern Pass. They've been hitting caravans — three "
                "merchants robbed, one guard wounded. 50 gold for clearing the camp. "
                "20 more if you bring the leader back alive. And watch for traps. "
                "The little bastards are clever."
            ),
            "threat": (
                "The kobolds are a nuisance, but they're not the real problem. Something "
                "stirred in the mountains last month. We've seen lights. The miners won't "
                "go near the deep shafts anymore. If you're heading up that way... "
                "be careful."
            ),
        },
        "inventory": [],
        "merchant": False,
    },

    # Easter egg: The Advisor — holds the Signed Tome of MUD Programming
    "the_advisor": {
        "name": "The Advisor",
        "description": (
            "An ancient figure surrounded by scrolls and leather-bound tomes. His eyes "
            "hold the depth of someone who has built a thousand worlds and watched them "
            "thrive and fall. He speaks with the quiet authority of one who literally "
            "wrote the book on this craft. On his desk, prominently displayed, sits a "
            "single volume bound in finest leather: 'The Art of MUD Programming.'"
        ),
        "level": 99,
        "hp": 9999,
        "hostile": False,
        "dialogue": {
            "greet": (
                "Ah, a visitor. Few find this place. Fewer still understand what they "
                "find. Tell me — do you seek knowledge, or merely answers? They are "
                "not the same thing."
            ),
            "tome": (
                "This? This is 'The Art of MUD Programming.' I wrote it long ago, when "
                "the world was younger and the craft was new. It contains everything I "
                "know about building worlds that persist — game loops, state management, "
                "the architecture of shared dreams. I have been keeping this signed copy "
                "for someone worthy. Perhaps... for Tyraziel the Head Wizard? He builds "
                "worlds with the same fire I once had. Would you deliver it to him?"
            ),
            "wisdom": (
                "A MUD lives or dies by three things: the quality of its world, the "
                "fairness of its systems, and the strength of its community. You can "
                "have the best code in existence, but if your players don't feel at "
                "home, they will leave. Remember that."
            ),
            "farewell": (
                "Go well, adventurer. And remember — the best games are the ones where "
                "the players write the story. We just build the stage."
            ),
        },
        "inventory": ["signed_tome"],
        "merchant": False,
        "quest_giver": True,
        "quest": {
            "name": "The Tome of Knowledge",
            "description": "Deliver the Signed Tome of MUD Programming to Tyraziel the Head Wizard.",
            "reward_xp": 500,
            "reward_gold": 0,  # some things are beyond gold
            "reward_item": "crown_shard",  # the real reward
        },
    },
}
