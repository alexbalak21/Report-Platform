"""Calls to the other services. Every call carries an explicit `Actor` (captured when the request
arrived) because batch jobs run in a background thread with no Flask request context.

Published template versions and block versions are immutable, so they are cached for the life
of the process.
"""

from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from rp_shared.auth import USER_ID_HEADER, USER_ROLE_HEADER
from rp_shared.config import Settings
from rp_shared.errors import Conflict, NotFound
from rp_shared.service_client import ServiceClient, UpstreamError
from rp_shared.template_render import BlockSnapshot, pin_key

TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


@dataclass(frozen=True)
class Actor:
    user_id: str | None
    role: str | None

    @property
    def headers(self) -> dict[str, str]:
        h = {}
        if self.user_id:
            h[USER_ID_HEADER] = self.user_id
        if self.role:
            h[USER_ROLE_HEADER] = self.role
        return h


def check_table(table: Any) -> str:
    if not isinstance(table, str) or not TABLE_RE.match(table):
        raise NotFound("Table not found")
    return table


class TemplateDirectory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._versions: dict[tuple[str, int], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _client(self) -> ServiceClient:
        return ServiceClient(self._settings, "template")

    def version(self, template_id: uuid.UUID | str, number: int | None, actor: Actor):
        """(version number, snapshot) of a published version; the latest when `number` is None."""
        if number is None:
            resp = self._client().get(f"/templates/{template_id}", headers=actor.headers)
            if resp.status_code == 404:
                raise NotFound("Template not found")
            if resp.status_code != 200:
                raise UpstreamError(f"template service answered {resp.status_code}")
            number = resp.json().get("published_version")
            if number is None:
                raise Conflict(
                    "This template has no published version yet", code="TEMPLATE_NOT_PUBLISHED"
                )
        key = (str(template_id), int(number))
        with self._lock:
            if key in self._versions:
                return int(number), self._versions[key]
        resp = self._client().get(
            f"/templates/{template_id}/versions/{number}", headers=actor.headers
        )
        if resp.status_code == 404:
            raise NotFound("Template version not found")
        if resp.status_code != 200:
            raise UpstreamError(f"template service answered {resp.status_code}")
        body = resp.json()
        with self._lock:
            self._versions[key] = body
        return int(number), body


class BlockDirectory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._snapshots: dict[tuple[str, int], BlockSnapshot] = {}
        self._lock = threading.Lock()

    def snapshot(self, block_id: uuid.UUID | str, version: int) -> BlockSnapshot | None:
        key = pin_key(block_id, version)
        with self._lock:
            if key in self._snapshots:
                return self._snapshots[key]
        resp = ServiceClient(self._settings, "block").get(f"/blocks/{block_id}/versions/{version}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise UpstreamError(f"block service answered {resp.status_code}")
        snap = BlockSnapshot.from_api(resp.json())
        with self._lock:
            self._snapshots[key] = snap
        return snap


class DataSource:
    """Records and loop rows from the ingestion service, and the report-number write-back."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _client(self) -> ServiceClient:
        return ServiceClient(self._settings, "ingestion")

    def record(self, table: str, row_id: uuid.UUID, actor: Actor) -> dict[str, Any]:
        resp = self._client().get(
            f"/ingestion/tables/{check_table(table)}/rows/{row_id}", headers=actor.headers
        )
        if resp.status_code == 404:
            raise NotFound("Record not found")
        if resp.status_code != 200:
            raise UpstreamError(f"ingestion service answered {resp.status_code}")
        return resp.json()

    def rows(self, table: str, limit: int, actor: Actor) -> list[dict[str, Any]]:
        resp = self._client().get(
            f"/ingestion/tables/{check_table(table)}/rows",
            params={"page_size": limit},
            headers=actor.headers,
        )
        if resp.status_code == 404:
            raise NotFound(f"Table '{table}' not found")
        if resp.status_code != 200:
            raise UpstreamError(f"ingestion service answered {resp.status_code}")
        return resp.json()["items"]

    def write_back(self, table: str, row_id: uuid.UUID, number: str) -> bool:
        """Store the report number on the source row. False if it could not be done (the
        report exists anyway; `record_synced` stays False and finalize tries again)."""
        try:
            resp = self._client().request(
                "PATCH",
                f"/ingestion/tables/{check_table(table)}/rows/{row_id}",
                json={"_report_number": number},
            )
        except (UpstreamError, NotFound):
            return False
        return resp.status_code == 200
