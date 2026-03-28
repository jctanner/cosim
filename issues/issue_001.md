# Issue 001: Agent messages posted to non-default channels not visible in UI

## Symptom

Orchestrator log shows an agent (e.g. Jordan) successfully posted to `#engineering`, but the message does not appear in the web UI for that channel.

Example log output:
```
  Jordan (Support Eng): posted to #engineering (150 chars)
```

## Investigation Notes

### What works

- `client.post_message()` calls the server and gets a 201 response (`raise_for_status()` would throw otherwise) — `lib/chat_client.py:72-80`
- The server's `post_message` endpoint stores the message in `_messages`, persists to `chat.log`, and calls `_broadcast()` — `lib/webapp.py:1651-1672`
- The web UI's `addMessage()` correctly routes messages into `messagesByChannel[ch]` — `lib/webapp.py:939-952`
- The web UI's `loadMessages()` fetches ALL messages (no channel filter) on initial load — `lib/webapp.py:972-976`
- Jordan (`"support"`) is a member of `#engineering` in `DEFAULT_MEMBERSHIPS` — `lib/personas.py:90`

### Possible causes to investigate

1. **SSE queue overflow dropping messages** — `_broadcast()` in `lib/webapp.py:78-89` uses `put_nowait()` on a `Queue(maxsize=256)`. If the queue is full, the message is silently dropped AND the subscriber is removed. On reconnect (2s delay), the client creates a new SSE connection but does NOT re-fetch missed messages via `loadMessages()`. Any messages broadcast during the gap are lost.

2. **No membership validation on posting** — `lib/orchestrator.py:734` checks `if ch not in memberships` (channel exists) but does not check `if persona_key not in memberships[ch]` (agent is a member). The server endpoint also does not validate sender membership. This may not be the cause for Jordan/#engineering specifically (Jordan is a member), but could cause phantom posts for other agents.

3. **Multi-channel response parsing edge cases** — `_parse_multi_channel_response()` in `lib/orchestrator.py:511-538` uses `_CHANNEL_MARKER_RE = r'^\[#([\w-]+)\]\s*$'`. If the agent formats the marker with extra text on the same line (e.g. `[#engineering] Here's my update`), the regex won't match and the content goes to the default channel instead.

## Files involved

| File | Lines | Role |
|------|-------|------|
| `lib/orchestrator.py` | 733-741 | Posting loop with membership check |
| `lib/orchestrator.py` | 511-538 | `_parse_multi_channel_response()` |
| `lib/orchestrator.py` | 132 | `_CHANNEL_MARKER_RE` regex |
| `lib/webapp.py` | 78-89 | `_broadcast()` — SSE with queue overflow |
| `lib/webapp.py` | 1682-1701 | SSE stream endpoint, `Queue(maxsize=256)` |
| `lib/webapp.py` | 939-952 | `addMessage()` JS — client-side routing |
| `lib/webapp.py` | 1651-1672 | `post_message()` server endpoint |
| `lib/chat_client.py` | 72-80 | `ChatClient.post_message()` |

## Suggested fixes

- **SSE resilience**: On SSE reconnect, re-fetch messages since last seen ID to fill the gap
- **Membership check**: Add `persona_key not in memberships[ch]` guard at `orchestrator.py:734`
- **Debug logging**: Log the raw agent response and parsed `channel_posts` dict to make this easier to reproduce
