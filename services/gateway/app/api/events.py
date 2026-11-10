"""GET /api/events/stream: Server-Sent Events fed by the `rp.events` Redis stream.

Clients authenticate with the Authorization header (use fetch streaming, not EventSource).
Each open stream holds one worker thread, hence GUNICORN_THREADS for the gateway.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from flask import Blueprint, Response, current_app, request, stream_with_context
from rp_shared.events import STREAM

from ..security import require_auth

log = logging.getLogger(__name__)
bp = Blueprint("events", __name__, url_prefix="/api/events")

# Only events the UI cares about are relayed to browsers.
UI_EVENTS = {
    "job.progress",
    "data.imported",
    "template.published",
    "report.generated",
    "report.revised",
    "report.finalized",
}


def event_stream(redis, start_id: str | None, block_ms: int = 15000) -> Iterator[str]:
    """Yield SSE frames forever: relayed events, plus a comment heartbeat when idle."""
    yield "retry: 3000\n\n"
    last_id = start_id
    if last_id is None:
        latest = redis.xrevrange(STREAM, count=1) if redis is not None else []
        last_id = latest[0][0] if latest else "0-0"
    while True:
        if redis is None:
            yield ": no-redis\n\n"
            return
        batches = redis.xread({STREAM: last_id}, count=50, block=block_ms)
        if not batches:
            yield ": ping\n\n"
            continue
        for _stream, entries in batches:
            for entry_id, fields in entries:
                last_id = entry_id
                try:
                    event = json.loads(fields["event"])
                except (KeyError, ValueError):
                    continue
                if event.get("type") in UI_EVENTS:
                    yield f"id: {entry_id}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"


@bp.get("/stream")
@require_auth()
def stream():
    redis = current_app.extensions.get("redis")
    gen = event_stream(redis, request.headers.get("Last-Event-ID"))
    return Response(
        stream_with_context(gen),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
