import os
import uuid

import fakeredis
import pytest
from app import create_app
from rp_shared.config import Settings
from sqlalchemy import create_engine, text

TOKEN = "svc-token"
PG_URL = os.getenv("TEST_PG_URL")

SIMPLE = {"html": "<p>Hello {{props.name}}</p>", "props_schema": {"name": {"default": "world"}}}
TABLE = {
    "html": (
        "<table><thead><tr><th>Lot</th></tr></thead>"
        "<tbody>{{#each rows}}<tr><td>{{lot}}</td></tr>{{/each}}</tbody></table>"
    ),
    "css": "td { color: red } body { margin: 0 }",
    "props_schema": {"source": {"type": "datasource"}},
}


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="set TEST_PG_URL to run on Postgres"),
        ),
    ]
)
def app(request, tmp_path):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'blocks.db'}"
    else:
        admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("DROP SCHEMA IF EXISTS blocks CASCADE"))
            c.execute(text("CREATE SCHEMA blocks"))
        sep = "&" if "?" in PG_URL else "?"
        url = f"{PG_URL}{sep}options=-csearch_path%3Dblocks"
    settings = Settings(
        service_name="block", database_url=url, service_token=TOKEN, jwt_secret="x" * 32
    )
    app = create_app(settings, run_migrations=True)
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def h():
    """Headers as the gateway would send them: h('designer')."""

    def _h(role="designer", user=None, **extra):
        headers = {"X-Service-Token": TOKEN, "X-User-Id": user or str(uuid.uuid4()), **extra}
        if role:
            headers["X-User-Role"] = role
        return headers

    return _h


@pytest.fixture
def make_block(client, h):
    def _make(definition=None, name="My block", category="text", **extra):
        r = client.post(
            "/blocks",
            json={"name": name, "category": category, "definition": definition or SIMPLE, **extra},
            headers=h("designer"),
        )
        assert r.status_code == 201, r.json
        return r.json

    return _make
