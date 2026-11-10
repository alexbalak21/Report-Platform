"""Domain events over Redis Streams (single stream, consumer groups).

Envelope: {"id", "type", "ts", "actor", "correlation_id", "data"}
Event catalog lives in docs/software_dev_plan.md section 7.7.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .logging import get_correlation_id, set_correlation_id

log = logging.getLogger(__name__)

STREAM = "rp.events"


def build_event(
    type_: str, data: dict[str, Any], actor: str | None = None, correlation_id: str | None = None
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": type_,
        "ts": datetime.now(UTC).isoformat(),
        "actor": actor,
        "correlation_id": correlation_id or get_correlation_id(),
        "data": data,
    }


def publish(redis_client, type_: str, data: dict[str, Any], actor: str | None = None) -> str:
    event = build_event(type_, data, actor)
    return redis_client.xadd(STREAM, {"event": json.dumps(event)}, maxlen=100_000, approximate=True)


def ensure_group(redis_client, group: str) -> None:
    try:
        redis_client.xgroup_create(STREAM, group, id="$", mkstream=True)
    except Exception as exc:  # BUSYGROUP when it already exists
        if "BUSYGROUP" not in str(exc):
            raise


def consume_once(
    redis_client,
    group: str,
    consumer: str,
    handlers: dict[str, Callable[[dict[str, Any]], None]],
    block_ms: int = 1000,
    count: int = 10,
) -> int:
    """Read one batch for this consumer group; ack after the handler succeeds.

    Events whose type has no handler are acked and skipped. A failing handler is
    logged and left pending (can be re-claimed later) instead of crashing the loop.
    """
    ensure_group(redis_client, group)
    resp = redis_client.xreadgroup(group, consumer, {STREAM: ">"}, count=count, block=block_ms)
    handled = 0
    for _stream, messages in resp or []:
        for msg_id, fields in messages:
            raw = fields.get("event") or fields.get(b"event")
            event = json.loads(raw)
            handler = handlers.get(event["type"])
            if handler is None:
                redis_client.xack(STREAM, group, msg_id)
                continue
            set_correlation_id(event.get("correlation_id"))
            try:
                handler(event)
            except Exception:
                log.exception("event handler failed: %s", event["type"])
                continue
            redis_client.xack(STREAM, group, msg_id)
            handled += 1
    return handled
