import uuid

import fakeredis
import pytest
from app import create_app
from rp_shared.auth import make_access_token
from rp_shared.config import Settings

SECRET = "test-secret-" + "x" * 32


@pytest.fixture
def app():
    settings = Settings(
        service_name="gateway",
        service_urls={
            "block": "http://block:8000",
            "report": "http://report:8000",
            "auth": "http://auth:8000",
        },
        jwt_secret=SECRET,
        service_token="svc-token",
    )
    app = create_app(settings)
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth():
    """Header factory: auth('designer') -> a bearer token the way the auth service issues it."""

    def _auth(role="admin"):
        token = make_access_token(SECRET, str(uuid.uuid4()), role, minutes=5)
        return {"Authorization": f"Bearer {token}"}

    return _auth
