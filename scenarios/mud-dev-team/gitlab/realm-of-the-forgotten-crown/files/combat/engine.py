"""Realm of the Forgotten Crown — Combat engine."""

import random

# Combat constants
BASE_TICK_DAMAGE = 10
CRIT_MULTIPLIER = 2.0
FLEE_CHANCE = 0.4
LEVEL_DAMAGE_SCALE = 1.5  # damage multiplier per level difference


class CombatInstance:
    """Manages a single combat encounter between a player and a mob."""

    def __init__(self, player, mob):
        self.player = player
        self.mob = mob
        self.mob_hp = mob.get("hp", 50)
        self.mob_max_hp = mob.get("hp", 50)
        self.round = 0
        self.active = True

    def player_attack(self):
        """Player attacks the mob. Returns (damage, is_crit, message)."""
        base_damage = self._get_player_damage()
        is_crit = random.random() < self._get_crit_chance()

        if is_crit:
            damage = int(base_damage * CRIT_MULTIPLIER)
            msg = f"CRITICAL HIT! You strike {self.mob['name']} for {damage} damage!"
        else:
            damage = base_damage
            msg = f"You hit {self.mob['name']} for {damage} damage."

        self.mob_hp -= damage
        if self.mob_hp <= 0:
            self.mob_hp = 0
            self.active = False
            msg += f"\n{self.mob['name']} has been slain!"
        return damage, is_crit, msg

    def mob_attack(self):
        """Mob attacks the player. Returns (damage, message)."""
        if not self.active:
            return 0, ""

        mob_level = self.mob.get("level", 1)
        base_damage = int(mob_level * 3 + random.randint(1, 6))

        # Apply player defense
        defense = self._get_player_defense()
        damage = max(1, base_damage - defense)

        self.player.hp -= damage
        msg = f"{self.mob['name']} hits you for {damage} damage."

        if self.player.hp <= 0:
            self.player.hp = 0
            self.active = False
            msg += "\nYou have been slain! You respawn at the tavern."
        return damage, msg

    def try_flee(self):
        """Attempt to flee combat. Returns (success, message)."""
        if random.random() < FLEE_CHANCE:
            self.active = False
            return True, "You flee from combat!"
        return False, f"You try to flee but {self.mob['name']} blocks your escape!"

    def _get_player_damage(self):
        """Calculate player damage per hit."""
        # Base damage from level
        level_bonus = self.player.level * 2

        # Weapon damage
        weapon = self.player.equipped.get("mainhand")
        weapon_damage = weapon.get("damage", 0) if weapon else 0

        # Class modifier
        # BUG: Warrior base damage is too low compared to Mage spell damage
        # Warrior: level*2 + weapon = ~23 DPS at level 20 with common gear
        # Mage: can cast fireball for 35 + spell_bonus every round = ~47 DPS
        # TODO: Add Warrior damage scaling or reduce spell damage
        class_mod = 1.0
        if self.player.class_name == "Warrior":
            class_mod = 1.0  # TODO: Should be 1.5+ to compete with Mage spells
        elif self.player.class_name == "Thief":
            class_mod = 1.2  # backstab bonus

        return int((level_bonus + weapon_damage) * class_mod)

    def _get_player_defense(self):
        """Calculate player defense rating."""
        defense = 0
        for slot in ["body", "offhand"]:
            item = self.player.equipped.get(slot)
            if item:
                defense += item.get("defense", 0)
        return defense

    def _get_crit_chance(self):
        """Calculate player critical hit chance."""
        base_crit = 0.05  # 5% base
        weapon = self.player.equipped.get("mainhand")
        if weapon:
            base_crit += weapon.get("crit_bonus", 0) / 100.0
        return min(base_crit, 0.5)  # cap at 50%

    def get_status(self):
        """Return combat status string."""
        return (
            f"--- Combat: Round {self.round} ---\n"
            f"You: {self.player.hp}/{self.player.max_hp} HP | "
            f"{self.mob['name']}: {self.mob_hp}/{self.mob_max_hp} HP"
        )


def calculate_xp_reward(player_level, mob_level):
    """Calculate XP reward for killing a mob."""
    base_xp = mob_level * 10
    level_diff = mob_level - player_level
    if level_diff > 0:
        # Bonus XP for killing higher-level mobs
        return int(base_xp * (1 + level_diff * 0.2))
    elif level_diff < -5:
        # Reduced XP for trivial mobs
        return max(1, int(base_xp * 0.1))
    return base_xp


def calculate_gold_reward(mob_level):
    """Calculate gold dropped by a mob."""
    return random.randint(mob_level, mob_level * 5)


def xp_to_level(level):
    """Calculate XP required to reach a given level."""
    # Quadratic scaling: level 2 = 100, level 10 = 4500, level 20 = 19000
    return int(level * level * 50 - 50)
