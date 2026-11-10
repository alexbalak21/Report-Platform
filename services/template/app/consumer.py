"""Reacts to `block.updated`: flag the templates whose *draft* pins an older version.

Published versions are immutable and never flagged; the badge is about what the designer can
act on ("upgrade blocks"). Flags are cleared when the draft pins the latest version again
(see service.prune_outdated).
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any

from flask import Flask
from rp_shared.events import consume_once
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from .models import Template, TemplateBlockUsage

log = logging.getLogger(__name__)
GROUP = "template"


def handle_block_updated(factory: sessionmaker, event: dict[str, Any]) -> int:
    """Returns how many templates were flagged."""
    data = event["data"]
    block_id = uuid.UUID(str(data["block_id"]))
    latest = int(data["version"])
    name = str(data.get("name") or "")
    with factory() as s:
        rows = s.execute(
            select(TemplateBlockUsage.template_id, func.min(TemplateBlockUsage.block_version))
            .where(
                TemplateBlockUsage.source == "draft",
                TemplateBlockUsage.block_id == block_id,
                TemplateBlockUsage.block_version < latest,
            )
            .group_by(TemplateBlockUsage.template_id)
        ).all()
        for template_id, pinned in rows:
            template = s.get(Template, template_id)
            if template is None:
                continue
            entries = [
                e for e in (template.outdated_blocks or []) if e["block_id"] != str(block_id)
            ]
            entries.append(
                {"block_id": str(block_id), "name": name, "pinned": pinned, "latest": latest}
            )
            template.outdated_blocks = entries
        s.commit()
        return len(rows)


def start_consumer(app: Flask, block_ms: int = 5000) -> threading.Event | None:
    """Run the consumer loop in a daemon thread. Returns the Event that stops it."""
    redis = app.extensions.get("redis")
    factory = app.extensions.get("db_sessionmaker")
    if redis is None or factory is None:
        return None
    handlers = {"block.updated": lambda event: handle_block_updated(factory, event)}
    consumer = f"{GROUP}-{os.getpid()}"
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                consume_once(redis, GROUP, consumer, handlers, block_ms=block_ms)
            except Exception:
                log.exception("event consumer failed; retrying")
                stop.wait(2)

    threading.Thread(target=loop, daemon=True, name="template-events").start()
    return stop
