"""Service-to-service HTTP: service token, correlation id, timeout, retry with backoff."""

from __future__ import annotations

import time
from typing import Any

import requests

from .config import Settings
from .errors import AppError
from .logging import CORRELATION_HEADER, get_correlation_id

SERVICE_TOKEN_HEADER = "X-Service-Token"


class UpstreamError(AppError):
    status = 502
    code = "UPSTREAM_ERROR"


class ServiceClient:
    def __init__(
        self,
        settings: Settings,
        target: str,
        timeout: float = 5.0,
        retries: int = 2,
        backoff: float = 0.2,
    ) -> None:
        try:
            self.base_url = settings.service_urls[target].rstrip("/")
        except KeyError as exc:
            raise UpstreamError(f"No URL configured for service '{target}'") from exc
        self.target = target
        self.token = settings.service_token
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", {}) or {}
        headers[SERVICE_TOKEN_HEADER] = self.token
        cid = get_correlation_id()
        if cid:
            headers[CORRELATION_HEADER] = cid
        url = f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = requests.request(
                    method, url, headers=headers, timeout=self.timeout, **kwargs
                )
                if resp.status_code >= 500 and attempt < self.retries:
                    time.sleep(self.backoff * (2**attempt))
                    continue
                return resp
            except requests.RequestException as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * (2**attempt))
        raise UpstreamError(f"{self.target} unreachable: {last}")

    def get(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", path, **kwargs)
