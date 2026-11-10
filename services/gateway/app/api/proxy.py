"""Authenticated reverse proxy: /api/<blocks|templates|reports|ingestion|editor>/...

Flow: verify JWT -> check role against policy -> forward with the service token and the
caller's identity (X-User-Id / X-User-Role) -> stream the answer back. Client-supplied
X-User-* / X-Service-Token headers are never forwarded (headers are rebuilt from an allow-list).
"""

from __future__ import annotations

import logging

import requests
from flask import Blueprint, Response, current_app, g, jsonify, request
from rp_shared.auth import SERVICE_TOKEN_HEADER, USER_ID_HEADER, USER_ROLE_HEADER
from rp_shared.errors import Forbidden, NotFound, error_body
from rp_shared.logging import CORRELATION_HEADER, get_correlation_id

from .. import auth_service as svc
from ..db import get_session
from ..policy import SEGMENT_SERVICE, allowed, min_role
from ..security import authenticate_request

log = logging.getLogger(__name__)
bp = Blueprint("proxy", __name__, url_prefix="/api")

FORWARD_REQUEST_HEADERS = (
    "Content-Type",
    "Accept",
    "If-Match",
    "If-None-Match",
    "Range",
)
DROP_RESPONSE_HEADERS = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "content-encoding",  # requests already decoded the body
    "content-length",
    "set-cookie",  # services never set browser cookies
}
AUDITED_METHODS = {
    "POST",
    "DELETE",
}  # PUT/PATCH are autosave-heavy and stay out of the audit log


@bp.route(
    "/<segment>",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    defaults={"rest": ""},
)
@bp.route("/<segment>/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def forward(segment: str, rest: str):
    if segment not in SEGMENT_SERVICE:
        raise NotFound("Unknown route")
    rel = f"{segment}/{rest}".rstrip("/")

    authenticate_request()
    needed = min_role(rel, request.method)
    if needed is None:
        raise NotFound("Unknown route")
    if not allowed(g.user_role, needed):
        raise Forbidden(f"Role '{g.user_role}' cannot perform this action")

    settings = current_app.config["SETTINGS"]
    service = SEGMENT_SERVICE[segment]
    base = settings.service_urls.get(service)
    if not base:
        return _error(502, "UPSTREAM_ERROR", f"No URL configured for service '{service}'")

    url = f"{base.rstrip('/')}/{rel}"
    if request.query_string:
        url += "?" + request.query_string.decode()

    headers = {h: request.headers[h] for h in FORWARD_REQUEST_HEADERS if h in request.headers}
    headers[SERVICE_TOKEN_HEADER] = settings.service_token
    headers[USER_ID_HEADER] = g.user_id
    headers[USER_ROLE_HEADER] = g.user_role
    if cid := get_correlation_id():
        headers[CORRELATION_HEADER] = cid

    try:
        upstream = requests.request(
            request.method,
            url,
            headers=headers,
            data=request.get_data() if request.method in {"POST", "PUT", "PATCH"} else None,
            timeout=(3.05, 60),
            stream=True,
        )
    except requests.Timeout:
        return _error(504, "UPSTREAM_TIMEOUT", f"{service} did not answer in time")
    except requests.RequestException:
        log.warning("upstream %s unreachable", service, exc_info=True)
        return _error(502, "UPSTREAM_ERROR", f"{service} is unreachable")

    if request.method in AUDITED_METHODS and upstream.status_code < 400:
        session = get_session()
        svc.audit(
            session,
            f"http.{request.method.lower()}",
            g.user_id,
            service,
            None,
            {"path": f"/{rel}", "status": upstream.status_code},
        )
        session.commit()

    out_headers = [
        (k, v) for k, v in upstream.headers.items() if k.lower() not in DROP_RESPONSE_HEADERS
    ]
    return Response(
        upstream.iter_content(chunk_size=64 * 1024),
        status=upstream.status_code,
        headers=out_headers,
        direct_passthrough=True,
    )


def _error(status: int, code: str, message: str):

    resp = jsonify(error_body(code, message))
    resp.status_code = status
    return resp
