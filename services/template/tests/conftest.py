import os
import uuid

import fakeredis
import pytest
from app import create_app
from app.clients import Catalog
from rp_shared.config import Settings
from rp_shared.errors import NotFound
from rp_shared.service_client import UpstreamError
from rp_shared.template_render import BlockSnapshot, pin_key
from sqlalchemy import create_engine, text

TOKEN = "svc-token"
PG_URL = os.getenv("TEST_PG_URL")


class FakeBlocks:
    """In-memory stand-in for the block service (same two methods the real client has)."""

    def __init__(self):
        self.snaps: dict[tuple[str, int], BlockSnapshot] = {}
        self.info: dict[str, dict] = {}
        self.down = False

    def add(self, name, html, *, region="body", archived=False, version=1, **definition):
        block_id = str(uuid.uuid4())
        definition.setdefault("props_schema", {})
        definition["repeatable_region"] = region
        self.info[block_id] = {
            "id": block_id,
            "name": name,
            "current_version": 0,
            "is_archived": archived,
        }
        for v in range(1, version + 1):
            self._store(block_id, name, html, v, definition)
        return block_id

    def _store(self, block_id, name, html, version, definition):
        self.snaps[pin_key(block_id, version)] = BlockSnapshot(
            block_id=block_id,
            version=version,
            name=name,
            html=html,
            css=f".blk-{block_id[:6]}-{version} p{{margin:0}}",
            scope_class=f"blk-{block_id[:6]}-{version}",
            definition=definition,
        )
        self.info[block_id]["current_version"] = version

    def bump(self, block_id, html=None):
        """Publish a new version of a block (what the block service does on edit)."""
        latest = self.info[block_id]["current_version"]
        old = self.snaps[pin_key(block_id, latest)]
        self._store(block_id, old.name, html or old.html, latest + 1, old.definition)
        return latest + 1

    def snapshot(self, block_id, version):
        if self.down:
            raise UpstreamError("block unreachable")
        return self.snaps.get(pin_key(block_id, version))

    def summary(self, block_id):
        if self.down:
            raise UpstreamError("block unreachable")
        return self.info.get(str(block_id))


class FakeCatalog:
    def __init__(self):
        cols = {"customer_name", "lot", "imp_pct", "sample_number"}
        self.catalog: Catalog | None = Catalog(
            keys=set(cols), tables={"samples": set(cols)}, builtins={"today", "now"}
        )

    def fetch(self):
        return self.catalog


class FakeData:
    def __init__(self):
        self.records = {}
        self.table_rows = {}

    def record(self, table, row_id):
        uuid.UUID(str(row_id))
        try:
            return self.records[(table, str(row_id))]
        except KeyError:
            raise NotFound("Record not found") from None

    def rows(self, table, limit):
        if table not in self.table_rows:
            raise NotFound(f"Table '{table}' not found")
        return self.table_rows[table][:limit]


@pytest.fixture
def fb():
    """Fake block service pre-loaded with a few blocks (ids are attributes)."""
    b = FakeBlocks()
    b.title = b.add(
        "Title",
        '<h1 class="t">{{props.text}}</h1>',
        props_schema={"text": {"type": "string", "default": "Report"}},
    )
    b.header = b.add(
        "Header",
        "<p>{{props.organisation}} N° {{report_number}}</p>",
        region="header",
        props_schema={"organisation": {"type": "string", "default": "Org"}},
    )
    b.footer = b.add(
        "Footer",
        "<p>Page {{page}} / {{pages}}</p>",
        region="footer",
    )
    b.kv = b.add(
        "Key/value",
        "<p>{{customer_name}} {{lot}}</p>",
        placeholders=["customer_name", "lot"],
    )
    b.table = b.add(
        "Results table",
        "<table>{{#each rows}}<tr><td>{{lot}}</td><td>{{imp_pct}}</td></tr>{{/each}}</table>",
        props_schema={"source": {"type": "datasource", "label": "Data source"}},
        placeholders=["rows"],
        loops=["rows"],
        loop_columns=["lot", "imp_pct"],
    )
    b.logo = b.add(
        "Image",
        '<img src="{{props.src}}" alt="">',
        region="any",
        props_schema={
            "src": {"type": "string", "label": "Image URL", "required": True},
            "size": {"type": "number"},
            "mode": {"type": "select", "options": ["fit", "fill"]},
            "border": {"type": "boolean"},
        },
    )
    return b


@pytest.fixture
def catalog():
    return FakeCatalog()


@pytest.fixture
def data():
    return FakeData()


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="set TEST_PG_URL to run on Postgres"),
        ),
    ]
)
def app(request, tmp_path, fb, catalog, data):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'templates.db'}"
    else:
        admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("DROP SCHEMA IF EXISTS templates CASCADE"))
            c.execute(text("CREATE SCHEMA templates"))
        sep = "&" if "?" in PG_URL else "?"
        url = f"{PG_URL}{sep}options=-csearch_path%3Dtemplates"
    settings = Settings(
        service_name="template", database_url=url, service_token=TOKEN, jwt_secret="x" * 32
    )
    app = create_app(settings, run_migrations=True, blocks=fb, catalog=catalog, data=data)
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def h():
    """Headers as the gateway would send them: h('designer'). role=None -> internal call."""

    def _h(role="designer", user=None, **extra):
        headers = {"X-Service-Token": TOKEN, "X-User-Id": user or str(uuid.uuid4()), **extra}
        if role:
            headers["X-User-Role"] = role
        return headers

    return _h


def inst(block_id, iid, version=1, **props):
    return {"instance_id": iid, "block_id": block_id, "block_version": version, "props": props}


def make_doc(body=(), header=(), footer=(), overlays=(), **page):
    from rp_shared.schemas.template_document import empty_document

    d = empty_document()
    d["regions"]["body"]["items"] = list(body)
    d["regions"]["header"]["items"] = list(header)
    d["regions"]["footer"]["items"] = list(footer)
    d["overlays"] = list(overlays)
    for k, v in page.items():
        d["page"][k] = v
    return d


@pytest.fixture
def make_template(client, h):
    def _make(document=None, name="Lab report", **extra):
        body = {"name": name, **extra}
        if document is not None:
            body["document"] = document
        r = client.post("/templates", json=body, headers=h("designer"))
        assert r.status_code == 201, r.json
        return r.json

    return _make


@pytest.fixture
def save(client, h):
    def _save(template, document, **extra):
        r = client.put(
            f"/templates/{template['id']}/draft",
            json={"document": document, **extra},
            headers=h("designer", **{"If-Match": str(template["draft_revision"])}),
        )
        assert r.status_code == 200, r.json
        return r.json

    return _save
