import fakeredis
import pytest
from app import create_app
from app.auth_service import create_user
from app.config import AuthConfig
from app.models import Base
from rp_shared.config import Settings
from sqlalchemy.orm import Session

PASSWORD = "correct horse battery"


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        service_name="auth",
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        service_urls={"block": "http://block:8000", "report": "http://report:8000"},
        jwt_secret="test-secret-" + "x" * 32,
        service_token="svc-token",
    )
    app = create_app(
        settings, AuthConfig(login_limit_per_account=3, login_limit_per_ip=100, cookie_path="/auth")
    )
    Base.metadata.create_all(app.extensions["db_engine"])
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def make_user(app):
    def _make(email="admin@example.com", role="admin", password=PASSWORD, active=True):
        with Session(app.extensions["db_engine"]) as s:
            u = create_user(s, email, password, email.split("@")[0], role)
            u.is_active = active
            s.commit()
            return str(u.id)

    return _make


@pytest.fixture
def login(client):
    def _login(email="admin@example.com", password=PASSWORD):
        return client.post("/auth/login", json={"email": email, "password": password})

    return _login


@pytest.fixture
def auth(make_user, login):
    """Header factory: auth('designer') creates a user with that role and logs in."""

    def _auth(role="admin"):
        email = f"{role}@example.com"
        make_user(email, role)
        token = login(email).json["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _auth
