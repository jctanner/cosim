"""Messages API routes including SSE stream."""

import queue
import time

from flask import Blueprint, Response, jsonify, request

from lib.webapp.helpers import _broadcast, _persist_message
from lib.webapp.state import (
    CHAT_LOG,
    _channel_lock,
    _channels,
    _lock,
    _messages,
    _sub_lock,
    _subscribers,
)

bp = Blueprint("messages", __name__)


@bp.route("/api/messages", methods=["GET"])
def get_messages():
    since = request.args.get("since", type=int)
    channels_param = request.args.get("channels", type=str)
    with _lock:
        result = list(_messages)
        if since is not None:
            result = [m for m in result if m["id"] > since]
        if channels_param is not None:
            ch_set = set()
            for c in channels_param.split(","):
                c = c.strip()
                if not c.startswith("#"):
                    c = "#" + c
                ch_set.add(c)
            result = [m for m in result if m.get("channel", "#general") in ch_set]
    return jsonify(result)


@bp.route("/api/messages", methods=["POST"])
def post_message():
    data = request.get_json(force=True)
    sender = data.get("sender", "").strip()
    content = data.get("content", "").strip()
    channel = data.get("channel", "#general").strip()
    if not sender or not content:
        return jsonify({"error": "sender and content required"}), 400
    with _channel_lock:
        if channel not in _channels:
            return jsonify({"error": f"unknown channel: {channel}"}), 400
    with _lock:
        msg = {
            "id": len(_messages) + 1,
            "sender": sender,
            "content": content,
            "channel": channel,
            "timestamp": time.time(),
        }
        _messages.append(msg)
    _persist_message(msg)
    _broadcast(msg)
    return jsonify(msg), 201


@bp.route("/api/messages/clear", methods=["POST"])
def clear_messages():
    with _lock:
        _messages.clear()
    if CHAT_LOG.exists():
        CHAT_LOG.unlink()
    return jsonify({"status": "cleared"})


@bp.route("/api/messages/stream")
def stream():
    def generate():
        q = queue.Queue(maxsize=256)
        with _sub_lock:
            _subscribers.append(q)
        try:
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"event: message\ndata: {data}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            with _sub_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    return Response(
        generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )
