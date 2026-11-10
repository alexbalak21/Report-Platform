from unittest.mock import patch

from app import create_app
from rp_shared.service_client import UpstreamError


def test_healthz():
    r = create_app().test_client().get("/healthz")
    assert r.status_code == 200 and r.json["service"] == "gateway"


def test_aggregate_health_reports_down_services():
    app = create_app()
    with patch("app.api.health.ServiceClient", side_effect=UpstreamError("no url")):
        r = app.test_client().get("/api/health")
    assert r.status_code == 200
    assert r.json["status"] == "degraded"
    assert {s["service"] for s in r.json["services"]} == {
        "auth",
        "ingestion",
        "block",
        "template",
        "report",
        "editor",
    }
    assert all(s["status"] == "down" for s in r.json["services"])
