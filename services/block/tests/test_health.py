from app import create_app


def test_healthz():
    client = create_app().test_client()
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json["service"] == "block"
