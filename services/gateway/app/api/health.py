"""GET /api/health: status of every backend service, for the frontend status page."""

from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, current_app, jsonify
from rp_shared.config import SERVICE_NAMES
from rp_shared.service_client import ServiceClient, UpstreamError

bp = Blueprint("health", __name__, url_prefix="/api")


def _probe(settings, name: str) -> dict:
    try:
        client = ServiceClient(settings, name, timeout=2.0, retries=0)
        resp = client.get("/readyz")
        body = resp.json() if resp.content else {}
        return {
            "service": name,
            "status": body.get("status", "unknown"),
            "checks": body.get("checks", {}),
        }
    except UpstreamError as exc:
        return {"service": name, "status": "down", "error": exc.message}
    except (ValueError, AttributeError):  # body was not a JSON object
        return {"service": name, "status": "down", "error": "invalid health response"}


@bp.get("/health")
def health():
    settings = current_app.config["SETTINGS"]
    names = [n for n in SERVICE_NAMES if n != "gateway"]
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        results = list(pool.map(lambda n: _probe(settings, n), names))
    ok = all(r["status"] == "ready" for r in results)
    return jsonify(status="ok" if ok else "degraded", services=results)
