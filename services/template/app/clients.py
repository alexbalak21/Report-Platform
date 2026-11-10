"""Read access to the other services this one depends on.

Each client is created once per app (`app.extensions[...]`) so tests can swap in fakes.
Block versions are immutable, so a pinned snapshot is cached for the life of the process.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from flask import g
from rp_shared.auth import USER_ID_HEADER, USER_ROLE_HEADER
from rp_shared.config import Settings
from rp_shared.errors import NotFound
from rp_shared.service_client import ServiceClient, UpstreamError
from rp_shared.template_render import BlockSnapshot, pin_key

TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def caller_headers() -> dict[str, str]:
    """Ingestion's read endpoints want a user role: pass the caller's identity through."""
    headers = {}
    if g.get("user_id"):
        headers[USER_ID_HEADER] = g.user_id
    if g.get("user_role"):
        headers[USER_ROLE_HEADER] = g.user_role
    return headers


class BlockDirectory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: ServiceClient | None = None
        self._snapshots: dict[tuple[str, int], BlockSnapshot] = {}
        self._lock = threading.Lock()

    @property
    def client(self) -> ServiceClient:
        if self._client is None:
            self._client = ServiceClient(self._settings, "block")
        return self._client

    def snapshot(self, block_id: uuid.UUID | str, version: int) -> BlockSnapshot | None:
        key = pin_key(block_id, version)
        with self._lock:
            if key in self._snapshots:
                return self._snapshots[key]
        resp = self.client.get(f"/blocks/{block_id}/versions/{version}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise UpstreamError(f"block service answered {resp.status_code}")
        snap = BlockSnapshot.from_api(resp.json())
        with self._lock:
            self._snapshots[key] = snap
        return snap

    def summary(self, block_id: uuid.UUID | str) -> dict[str, Any] | None:
        """Latest state of a block: name, current_version, is_archived, ..."""
        resp = self.client.get(f"/blocks/{block_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise UpstreamError(f"block service answered {resp.status_code}")
        return resp.json()


@dataclass
class Catalog:
    """What the ingestion service says can be used as a keyword."""

    keys: set[str] = field(default_factory=set)
    tables: dict[str, set[str]] = field(default_factory=dict)
    builtins: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not self.keys


class CatalogClient:
    def __init__(self, settings: Settings, ttl: float = 30.0) -> None:
        self._settings = settings
        self._ttl = ttl
        self._cached: tuple[float, Catalog] | None = None
        self._lock = threading.Lock()

    def fetch(self) -> Catalog | None:
        """The catalog, or None when the ingestion service cannot be reached."""
        with self._lock:
            if self._cached and time.monotonic() - self._cached[0] < self._ttl:
                return self._cached[1]
        try:
            resp = ServiceClient(self._settings, "ingestion").get(
                "/ingestion/placeholders", headers=caller_headers()
            )
        except UpstreamError:
            return None
        if resp.status_code != 200:
            return None
        body = resp.json()
        catalog = Catalog(builtins=set(body.get("builtins", [])))
        for item in body.get("items", []):
            catalog.keys.add(item["key"])
            if item.get("qualified_key"):
                catalog.keys.add(item["qualified_key"])
            catalog.tables.setdefault(item["table"], set()).add(item["column"])
        with self._lock:
            self._cached = (time.monotonic(), catalog)
        return catalog


class DataSource:
    """Real rows for the builder's "preview with data"."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> ServiceClient:
        return ServiceClient(self._settings, "ingestion")

    @staticmethod
    def _check_table(table: str) -> str:
        if not TABLE_RE.match(table or ""):
            raise NotFound("Table not found")
        return table

    def record(self, table: str, row_id: str) -> dict[str, Any]:
        table = self._check_table(table)
        rid = uuid.UUID(str(row_id))  # ValueError -> handled by the caller as a bad request
        resp = self._client().get(f"/ingestion/tables/{table}/rows/{rid}", headers=caller_headers())
        if resp.status_code == 404:
            raise NotFound("Record not found")
        if resp.status_code != 200:
            raise UpstreamError(f"ingestion service answered {resp.status_code}")
        return resp.json()

    def rows(self, table: str, limit: int) -> list[dict[str, Any]]:
        table = self._check_table(table)
        resp = self._client().get(
            f"/ingestion/tables/{table}/rows",
            params={"page_size": limit},
            headers=caller_headers(),
        )
        if resp.status_code == 404:
            raise NotFound(f"Table '{table}' not found")
        if resp.status_code != 200:
            raise UpstreamError(f"ingestion service answered {resp.status_code}")
        return resp.json()["items"]
