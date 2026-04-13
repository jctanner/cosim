"""Recap API routes."""

import time

from flask import Blueprint, jsonify, request

from lib.webapp.state import (
    _recaps, _messages, _lock,
    _docs_index, _tickets,
)

bp = Blueprint("recaps", __name__)


@bp.route("/api/recaps", methods=["GET"])
def list_recaps():
    return jsonify(_recaps)


@bp.route("/api/recap", methods=["POST"])
def generate_recap():
    import asyncio
    import threading
    from lib.agent_runner import run_agent_for_response
    from pathlib import Path

    data = request.get_json(force=True)
    style = data.get("style", "normal")
    print(f"[recap] Generating recap in style: {style}")

    STYLE_PROMPTS = {
        "normal": "Write a clear, professional summary of what happened.",
        "ye-olde-english": "Write the recap in Ye Olde English, with 'thee', 'thou', 'hath', 'forsooth', and medieval phrasing throughout.",
        "tolkien": "Write the recap as if it were a passage from The Lord of the Rings — epic, sweeping, with references to quests, fellowships, and dark forces.",
        "star-wars": "Write the recap as a Star Wars opening crawl. Start with 'A long time ago, in a codebase far, far away...' and use space opera drama.",
        "star-trek": "Write the recap as a Captain's Log entry. 'Captain's Log, Stardate...' Include references to the crew, away missions, and the prime directive.",
        "dr-who": "Write the recap as if The Doctor is explaining what happened to a confused companion. Wibbly-wobbly, timey-wimey.",
        "morse-code": "Write the recap normally but add STOP after each sentence, like a telegraph message. Keep it terse.",
        "dr-seuss": "Write the recap in the style of Dr. Seuss — rhyming couplets, whimsical language, 'I do not like green bugs in prod, I do not like them, oh my cod.'",
        "shakespeare": "Write the recap as a Shakespearean monologue. Iambic pentameter where possible. Include asides and dramatic declarations.",
        "80s-rock-ballad": "Write the recap as lyrics to an 80s power ballad. Include a key change, a guitar solo section [GUITAR SOLO], and dramatic crescendo.",
        "90s-alternative": "Write the recap in the style of 90s alternative rock lyrics — angsty, introspective, ironic detachment about the state of the codebase.",
        "heavy-metal": "Write the recap as HEAVY METAL lyrics. ALL CAPS for emphasis. References to DESTRUCTION, CHAOS, DEPLOYING TO PRODUCTION, and THE ETERNAL VOID OF TECHNICAL DEBT.",
        "dystopian": "Write the recap as a dystopian narrative. The company is a megacorp. The codebase is a surveillance system. Compliance training is re-education. The open office is a panopticon. Hope is a deprecated feature.",
        "matrix": "Write the recap as if Morpheus is explaining what happened to Neo. 'What if I told you...' References to the Matrix, agents (the bad kind), red pills, blue pills, the desert of the real. The codebase IS the Matrix.",
        "pharaoh": "Write the recap as a royal decree from a Pharaoh. 'So let it be written, so let it be done.' Grand proclamations about what was commanded and what was achieved. References to building monuments (features), the Nile (the data pipeline), golden treasures (shipped code), and scribes (developers). End each major point with 'So let it be written, so let it be done.'",
        "tombstone": "Write the recap in the style of a Western, Tombstone specifically. Narrate like Doc Holliday and Wyatt Earp are reviewing the sprint. 'I'm your huckleberry.' References to showdowns (code reviews), outlaws (bugs), the OK Corral (production), and riding into the sunset. Dry wit, whiskey references, and dramatic standoffs over merge conflicts.",
        "survivor": "Write the recap as a Survivor tribal council. Jeff Probst is hosting. The team members are contestants. Alliances formed over architecture decisions. Blindsides during code review. 'The tribe has spoken' when a feature gets cut. Confessional-style asides where team members reveal their true feelings. Someone plays a hidden immunity idol (a revert commit). End with 'Grab your torch' and snuff it.",
        "hackernews": "Write the recap as a Hacker News-worthy blog post. Technical but accessible. Start with a hook that makes people click. Include architecture decisions, tradeoffs considered, and lessons learned. Sprinkle in references to scaling, first principles thinking, and 'we considered X but chose Y because Z.' End with a thoughtful takeaway. The tone should make HN commenters say 'this is actually good' instead of their usual complaints.",
    }

    style_instruction = STYLE_PROMPTS.get(style, STYLE_PROMPTS["normal"])

    # Collect all state
    with _lock:
        msgs = list(_messages)

    msg_summary = []
    for m in msgs[-100:]:
        msg_summary.append(f"[{m.get('channel', '#general')}] {m['sender']}: {m['content'][:200]}")

    from lib.events import get_event_log
    event_log = get_event_log()
    event_summary = []
    for e in event_log:
        event_summary.append(f"[{e.get('severity', 'medium')}] {e.get('name', 'Event')} - {len(e.get('actions', []))} actions")

    nl = chr(10)
    prompt = f"""You are a recap writer. Summarize what happened in this simulation session.

## Style
{style_instruction}

## Chat Messages (most recent {len(msg_summary)})
{nl.join(msg_summary) if msg_summary else "No messages yet."}

## Events Fired ({len(event_log)})
{nl.join(event_summary) if event_summary else "No events fired."}

## Documents Created
{len(_docs_index)} documents

## Tickets
{len(_tickets)} tickets

## Stats
- Total messages: {len(msgs)}
- Channels active: {len(set(m.get('channel', '#general') for m in msgs))}

Write a compelling recap of this simulation session in the requested style. Keep it to no more than 15 paragraphs. Make it entertaining and capture the key moments, decisions, and drama."""

    result_holder = [None]
    error_holder = [None]

    async def _run():
        try:
            result = await run_agent_for_response(
                name="Recap Writer",
                prompt=prompt,
                log_dir=Path(__file__).resolve().parent.parent.parent / "var" / "logs",
                model="sonnet",
            )
            result_holder[0] = result
        except Exception as e:
            error_holder[0] = str(e)

    def _thread_target():
        asyncio.run(_run())

    t = threading.Thread(target=_thread_target)
    t.start()
    t.join(timeout=120)

    if error_holder[0]:
        return jsonify({"error": error_holder[0]}), 500
    if result_holder[0] and result_holder[0].get("success"):
        recap_entry = {"recap": result_holder[0]["response_text"], "style": style, "timestamp": time.time()}
        _recaps.append(recap_entry)
        print(f"[recap] Generated {style} recap ({len(result_holder[0]['response_text'])} chars)")
        return jsonify(recap_entry)
    return jsonify({"error": "Recap generation failed or timed out"}), 500
