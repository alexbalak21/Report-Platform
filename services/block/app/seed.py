"""Create or update the default blocks (idempotent; safe when several workers start at once)."""

from __future__ import annotations

import logging

from rp_shared.schemas.block import parse_block_definition
from sqlalchemy import select, text

from .defaults import DEFAULTS, default_id
from .models import Block
from .service import add_version

log = logging.getLogger(__name__)
SEED_LOCK_KEY = 7_201_005


def seed_defaults(session_factory) -> int:
    """Returns how many versions were added."""
    added = 0
    with session_factory() as session:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SEED_LOCK_KEY})
        for spec in DEFAULTS:
            block = session.scalar(select(Block).where(Block.slug == spec["slug"]))
            if block is None:
                block = Block(
                    id=default_id(spec["slug"]),
                    kind="default",
                    slug=spec["slug"],
                    name=spec["name"],
                    category=spec["category"],
                    description=spec["description"],
                    current_version=0,
                )
                session.add(block)
            else:
                block.name, block.category = spec["name"], spec["category"]
                block.description = spec["description"]
            row, _ = add_version(session, block, parse_block_definition(spec["definition"]), None)
            added += row is not None
        session.commit()
    if added:
        log.info("default blocks: %d new version(s)", added)
    return added
