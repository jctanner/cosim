"""Realm of the Forgotten Crown — Main game server."""

import asyncio
import json
import time
from pathlib import Path

# Configuration
DEFAULT_PORT = 4000
TICK_RATE = 4  # ticks per second
SAVE_INTERVAL = 300  # auto-save every 5 minutes

# Global state
players = {}  # {writer: Player}
rooms = {}    # loaded from world/rooms.py
items = {}    # loaded from world/items.py
npcs = {}     # loaded from world/npcs.py


class Player:
    """Represents a connected player session."""

    def __init__(self, reader, writer, name="Unknown"):
        self.reader = reader
        self.writer = writer
        self.name = name
        self.room = "thornwick_tavern"  # starting room
        self.hp = 100
        self.max_hp = 100
        self.mana = 50
        self.max_mana = 50
        self.level = 1
        self.xp = 0
        self.gold = 100
        self.inventory = []
        self.equipped = {}
        self.class_name = "Adventurer"
        self.in_combat = False

    def send(self, text):
        """Send text to the player's telnet connection."""
        try:
            self.writer.write((text + "\r\n").encode())
        except Exception:
            pass

    def prompt(self):
        """Send the command prompt."""
        self.send(f"\r\n[HP:{self.hp}/{self.max_hp} MP:{self.mana}/{self.max_mana}] > ")


# --- Command handlers ---

def cmd_look(player, args):
    """Look at the current room."""
    room = rooms.get(player.room)
    if not room:
        player.send("You are in a void. This shouldn't happen.")
        return
    player.send(f"\r\n{room['name']}")
    player.send(f"{room['description']}")
    # Show exits
    exits = ", ".join(room.get("exits", {}).keys())
    player.send(f"[Exits: {exits}]")
    # Show items on ground
    for item_id in room.get("items", []):
        item = items.get(item_id)
        if item:
            player.send(f"  {item['name']} is here.")
    # Show NPCs
    for npc_id in room.get("npcs", []):
        npc = npcs.get(npc_id)
        if npc:
            player.send(f"  {npc['name']} is here.")
    # Show other players
    for other in players.values():
        if other != player and other.room == player.room:
            player.send(f"  {other.name} is here.")


def cmd_move(player, direction):
    """Move to an adjacent room."""
    room = rooms.get(player.room)
    if not room:
        return
    exits = room.get("exits", {})
    if direction not in exits:
        player.send(f"You can't go {direction}.")
        return
    # Notify others in old room
    for other in players.values():
        if other != player and other.room == player.room:
            other.send(f"{player.name} leaves {direction}.")
    # Move
    player.room = exits[direction]
    # Notify others in new room
    for other in players.values():
        if other != player and other.room == player.room:
            other.send(f"{player.name} arrives.")
    cmd_look(player, "")


def cmd_say(player, message):
    """Say something to everyone in the room."""
    if not message:
        player.send("Say what?")
        return
    player.send(f'You say, "{message}"')
    for other in players.values():
        if other != player and other.room == player.room:
            other.send(f'{player.name} says, "{message}"')


def cmd_who(player, args):
    """List all connected players."""
    player.send(f"\r\n--- Players Online: {len(players)} ---")
    for p in players.values():
        room = rooms.get(p.room, {})
        player.send(f"  {p.name} (Lvl {p.level} {p.class_name}) - {room.get('name', 'Unknown')}")


def cmd_help(player, args):
    """Show available commands."""
    player.send("\r\n--- Commands ---")
    player.send("  look          - Look around")
    player.send("  north/south/east/west/up/down - Move")
    player.send("  say <message> - Talk to the room")
    player.send("  who           - List online players")
    player.send("  inventory     - Check your inventory")
    player.send("  quit          - Disconnect")
    player.send("  help          - This help text")


COMMANDS = {
    "look": cmd_look, "l": cmd_look,
    "say": cmd_say, "'": cmd_say,
    "who": cmd_who,
    "help": cmd_help, "?": cmd_help,
}

DIRECTIONS = {"north", "south", "east", "west", "up", "down",
              "n", "s", "e", "w", "u", "d"}

DIRECTION_ALIASES = {"n": "north", "s": "south", "e": "east",
                     "w": "west", "u": "up", "d": "down"}


def process_command(player, raw_input):
    """Parse and execute a player command."""
    parts = raw_input.strip().split(None, 1)
    if not parts:
        return
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd in DIRECTION_ALIASES:
        cmd = DIRECTION_ALIASES[cmd]
    if cmd in DIRECTIONS:
        cmd_move(player, cmd)
    elif cmd in COMMANDS:
        COMMANDS[cmd](player, args)
    elif cmd == "quit":
        player.send("Farewell, adventurer.")
        raise ConnectionError("Player quit")
    else:
        player.send(f"Unknown command: {cmd}. Type 'help' for a list.")


# --- Connection handler ---

async def handle_connection(reader, writer):
    """Handle a new telnet connection."""
    addr = writer.get_extra_info("peername")
    print(f"New connection from {addr}")

    writer.write(b"\r\nWelcome to the Realm of the Forgotten Crown!\r\n")
    writer.write(b"What is your name, adventurer? ")
    await writer.drain()

    try:
        name_data = await asyncio.wait_for(reader.readline(), timeout=60)
        name = name_data.decode().strip()
        if not name:
            name = "Stranger"
    except (asyncio.TimeoutError, ConnectionError):
        writer.close()
        return

    player = Player(reader, writer, name)
    players[writer] = player

    player.send(f"\r\nWelcome, {name}! Type 'help' for commands.\r\n")
    cmd_look(player, "")
    player.prompt()

    try:
        while True:
            data = await asyncio.wait_for(reader.readline(), timeout=600)
            if not data:
                break
            raw = data.decode().strip()
            if raw:
                process_command(player, raw)
            player.prompt()
    except (asyncio.TimeoutError, ConnectionError, ConnectionResetError):
        pass
    finally:
        del players[writer]
        writer.close()
        print(f"{name} disconnected")


# --- Game loop ---

async def game_tick():
    """Main game loop — runs every tick."""
    # TODO: Process NPC AI, respawns, combat rounds, regen
    # TODO: Process spell effects and buffs/debuffs
    pass


async def tick_loop():
    """Run the game tick at the configured rate."""
    interval = 1.0 / TICK_RATE
    while True:
        await game_tick()
        await asyncio.sleep(interval)


# --- Main ---

async def main():
    """Start the game server."""
    # TODO: Load rooms, items, NPCs from data files
    from world.rooms import ROOMS
    from world.items import ITEMS
    from world.npcs import NPCS
    rooms.update(ROOMS)
    items.update(ITEMS)
    npcs.update(NPCS)

    print(f"Loaded {len(rooms)} rooms, {len(items)} items, {len(npcs)} NPCs")

    server = await asyncio.start_server(handle_connection, "0.0.0.0", DEFAULT_PORT)
    print(f"Realm of the Forgotten Crown running on port {DEFAULT_PORT}")
    print(f"Tick rate: {TICK_RATE}/sec")

    asyncio.create_task(tick_loop())

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
