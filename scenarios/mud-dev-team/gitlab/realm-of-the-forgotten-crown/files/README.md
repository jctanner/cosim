# Realm of the Forgotten Crown

A text-based MUD (Multi-User Dungeon) built in Python.

## Overview

Realm of the Forgotten Crown is a classic text-based multiplayer RPG. Players connect via telnet, explore a persistent fantasy world, fight monsters, complete quests, and interact with other players in real-time.

**Status:** Live — 200+ active players, 14 zones, 847 rooms

## Tech Stack

- **Python 3.11+** with asyncio for concurrent connections
- Telnet protocol (port 4000)
- JSON flat-file persistence (rooms, items, NPCs, player saves)
- Custom game loop with configurable tick rate (default: 4 ticks/second)

## Running

```bash
python server.py --port 4000 --tick-rate 4
```

Connect with any telnet client: `telnet localhost 4000`

## Project Structure

```
server.py              # Main server loop, connection handler, command parser
world/
  rooms.py             # Room definitions and zone loading
  items.py             # Item definitions and inventory system
  npcs.py              # NPC definitions, dialogue, and AI
combat/
  engine.py            # Combat system — turn-based with initiative
  spells.py            # Spell definitions and casting system
```

## Team

- **Tyraziel** — Head Wizard / Implementor (architecture, server, final authority)
- **Codex** — Advisor (architecture guidance, game design philosophy)
- **Pixel** — Builder (rooms, items, NPCs, quests, lore)
- **Hex** — Coder (combat, spells, economy, balance)
- **Sage** — Moderator (community, player support, rules)
- **Glitch** — QA / Mortal Tester (bugs, balance testing, new player experience)

## Known Issues

- Warrior DPS is roughly half of Mage DPS at equivalent levels (balance pass needed)
- Merchant buy/sell cycling allows gold generation (pricing exploit)
- Quest log shows completed quests as active (persistence bug)
- Zone transition lag under high player count

## Future: NRSP Migration

World data (rooms, NPCs, items) is currently stored as Python dicts. We plan to migrate to the [Narrative RPG Save Point Format (NRSP)](https://github.com/tyraziel/narrative-rpg-save-point-format/) for all game content:

- **Rooms → `.LS.md` (Location Sheets)** — room descriptions, exits, and state as structured markdown with YAML frontmatter
- **NPCs → `.CS.md` (Character Sheets)** — NPC identity, dialogue, motivations, and inventory in NRSP character sheet format
- **NPC secrets → `.NPC.md` (NPC Sheets)** — hidden motives, secret inventory, and quest triggers for GM/builder use
- **Items → structured YAML** — item definitions with NRSP-compatible metadata

Benefits: human-readable world files, version-controlled content, semantic structure for tooling, and compatibility with the broader NRSP ecosystem for narrative state management. This aligns with the project's open-source philosophy — game content should be as accessible and transparent as the code.
