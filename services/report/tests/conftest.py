import os
import uuid

import fakeredis
import pytest
from app import create_app
from app.config import ReportConfig
from rp_shared.config import Settings
from rp_shared.errors import Conflict, NotFound
from rp_shared.schemas.template_document import empty_document
from rp_shared.storage import LocalStorage
from rp_shared.template_render import BlockSnapshot, pin_key
from sqlalchemy import create_engine, text

TOKEN = "svc-token"
PG_URL = os.getenv("TEST_PG_URL")


class FakeBlocks:
    """Stand-in for the block service (same `snapshot` method as the real directory)."""

    def __init__(self):
        self.snaps = {}

    def add(self, name, html, version=1, **definition):
        block_id = str(uuid.uuid4())
        definition.setdefault("props_schema", {})
        self.snaps[pin_key(block_id, version)] = BlockSnapshot(
            block_id=block_id,
            version=version,
            name=name,
            html=html,
            css=f".blk-{block_id[:6]} p{{margin:0}}",
            scope_class=f"blk-{block_id[:6]}",
            definition=definition,
        )
        return block_id

    def snapshot(self, block_id, version):
        return self.snaps.get(pin_key(block_id, version))


class FakeTemplates:
    """Published template versions, as the template service serves them."""

    def __init__(self):
        self.versions = {}  # (template_id, n) -> body
        self.latest = {}  # template_id -> n or None
        self.calls = 0

    def publish(self, document, name="Rapport d'essai", template_id=None):
        tid = template_id or str(uuid.uuid4())
        n = (self.latest.get(tid) or 0) + 1
        self.versions[(tid, n)] = {
            "template_id": tid,
            "name": name,
            "version": n,
            "document": document,
        }
        self.latest[tid] = n
        return tid

    def draft_only(self):
        tid = str(uuid.uuid4())
        self.latest[tid] = None
        return tid

    def version(self, template_id, number, actor):
        self.calls += 1
        tid = str(template_id)
        if tid not in self.latest:
            raise NotFound("Template not found")
        if number is None:
            number = self.latest[tid]
            if number is None:
                raise Conflict("no published version", code="TEMPLATE_NOT_PUBLISHED")
        try:
            return number, self.versions[(tid, number)]
        except KeyError:
            raise NotFound("Template version not found") from None


class FakeData:
    def __init__(self):
        self.records = {}  # (table, id) -> row
        self.tables = {}  # table -> list of rows
        self.writebacks = []  # (table, id, number)
        self.writeback_ok = True

    def add(self, table="samples", **row):
        rid = uuid.uuid4()
        self.records[(table, str(rid))] = {"_id": str(rid), **row}
        return rid

    def record(self, table, row_id, actor):
        try:
            return dict(self.records[(table, str(row_id))])
        except KeyError:
            raise NotFound("Record not found") from None

    def rows(self, table, limit, actor):
        if table not in self.tables:
            raise NotFound(f"Table '{table}' not found")
        return self.tables[table][:limit]

    def write_back(self, table, row_id, number):
        self.writebacks.append((table, str(row_id), number))
        return self.writeback_ok


class FakePdf:
    """Records what it was asked to print; returns a recognisable fake PDF."""

    def __init__(self):
        self.printed = []
        self.fail = False

    def render(self, html):
        if self.fail:
            raise RuntimeError("chromium crashed")
        self.printed.append(html)
        return b"%PDF-fake-" + str(len(self.printed)).encode()


def doc(body=(), header=(), footer=(), **settings):
    d = empty_document()
    d["regions"]["body"]["items"] = list(body)
    d["regions"]["header"]["items"] = list(header)
    d["regions"]["footer"]["items"] = list(footer)
    d["settings"].update(settings)
    return d


def inst(block_id, instance_id, version=1, **props):
    return {
        "instance_id": instance_id,
        "block_id": block_id,
        "block_version": version,
        "locked": False,
        "props": props,
    }


@pytest.fixture
def blocks():
    b = FakeBlocks()
    b.header = b.add("Header", "<p>Rapport N° {{report_number}}</p>", placeholders=[])
    b.kv = b.add(
        "Key/value",
        "<p>Client: {{customer_name}} Lot: {{lot}}</p>",
        placeholders=["customer_name", "lot"],
    )
    b.table = b.add(
        "Results table",
        "<table>{{#each rows}}<tr><td>{{lot}}</td></tr>{{/each}}</table>",
        props_schema={"source": {"type": "datasource"}},
        loops=["rows"],
        loop_columns=["lot"],
    )
    b.footer = b.add("Footer", "<p>Page {{page}} / {{pages}}</p>")
    return b


@pytest.fixture
def templates():
    return FakeTemplates()


@pytest.fixture
def data():
    return FakeData()


@pytest.fixture
def pdf():
    return FakePdf()


@pytest.fixture
def tpl(templates, blocks):
    """A published template: header + key/value + footer."""
    return templates.publish(
        doc(
            body=[inst(blocks.kv, "kv")],
            header=[inst(blocks.header, "hd")],
            footer=[inst(blocks.footer, "ft")],
        )
    )


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="set TEST_PG_URL to run on Postgres"),
        ),
    ]
)
def app(request, tmp_path, blocks, templates, data, pdf):
    if request.param == "sqlite":
        url = f"sqlite:///{tmp_path / 'reports.db'}"
    else:
        admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.execute(text("DROP SCHEMA IF EXISTS reports CASCADE"))
            c.execute(text("CREATE SCHEMA reports"))
        sep = "&" if "?" in PG_URL else "?"
        url = f"{PG_URL}{sep}options=-csearch_path%3Dreports"
    settings = Settings(
        service_name="report", database_url=url, service_token=TOKEN, jwt_secret="x" * 32
    )
    config = ReportConfig(sync_jobs=True, local_storage_dir=str(tmp_path / "files"))
    app = create_app(
        settings,
        config=config,
        run_migrations=True,
        storage=LocalStorage(str(tmp_path / "files")),
        pdf=pdf,
        templates=templates,
        blocks=blocks,
        data=data,
    )
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    app.dialect = request.param
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def h():
    """Headers as the gateway sends them: h('operator'). role=None -> internal call."""

    def _h(role="operator", user=None, **extra):
        headers = {"X-Service-Token": TOKEN, "X-User-Id": user or str(uuid.uuid4()), **extra}
        if role:
            headers["X-User-Role"] = role
        return headers

    return _h


@pytest.fixture
def record(data):
    """A complete sample record."""
    return data.add(customer_name="ACME Corp", lot="L240501")


@pytest.fixture
def generate(client, h, tpl):
    def _generate(record_id, template_id=None, table="samples", expect=201, **extra):
        r = client.post(
            "/reports",
            json={
                "template_id": template_id or tpl,
                "table": table,
                "record_id": str(record_id),
                **extra,
            },
            headers=h(),
        )
        assert r.status_code == expect, r.json
        return r.json

    return _generate


@pytest.fixture
def events(app):
    """Event types published so far, in order."""
    import json

    def _events():
        entries = app.extensions["redis"].xrange("rp.events")
        return [json.loads(f["event"]) for _, f in entries]

    return _events
