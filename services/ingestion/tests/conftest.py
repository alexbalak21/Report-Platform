import io
import os
import uuid
from datetime import date

import fakeredis
import pytest
from app import create_app
from app.config import IngestionConfig
from app.storage import LocalStorage
from openpyxl import Workbook
from rp_shared.config import Settings
from sqlalchemy import create_engine, text

TOKEN = "svc-token"
PG_URL = os.getenv("TEST_PG_URL")


def make_xlsx(sheets: dict[str, list[list]], formulas: dict | None = None) -> bytes:
    """Build a workbook. `formulas` = {(sheet, 'B2'): '=A2*2'} written without a cached value."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    for (sheet, ref), formula in (formulas or {}).items():
        wb[sheet][ref] = formula
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HEADERS = ["numero echantillon", "date rapport", "nom entreprise client", "lot", "imp", "espece"]


def sample_workbook(extra_rows: list[list] | None = None) -> bytes:
    rows = [
        HEADERS,
        [1001, date(2026, 6, 21), "ACME Corp", "L240501", 0.126, "SAL"],
        [1002, date(2026, 6, 21), "Poissons du Nord", "L240502", 0.05, "COD"],
        [1003, "22/06/2026", "ACME Corp", "L240503", "0,2", "SAL"],
        *(extra_rows or []),
    ]
    especes = [["code", "nom"], ["SAL", "Saumon"], ["COD", "Cabillaud"]]
    return make_xlsx({"Rapport": rows, "especes": especes})


PROFILE = {
    "match": {"sheet": "Rapport", "required_headers": ["numero echantillon", "lot"]},
    "tables": [
        {
            "table": "samples",
            "key": ["sample_number"],
            "columns": [
                {"source": "numero echantillon", "target": "sample_number", "type": "integer"},
                {"source": "date rapport", "target": "report_date", "type": "date"},
                {"source": "nom entreprise client", "target": "customer_name"},
                {"source": "lot", "target": "lot", "required": True},
                {
                    "source": "imp",
                    "target": "imp_pct",
                    "type": "number",
                    "operations": [
                        {"type": "multiply", "value": 100},
                        {"type": "round", "decimals": 1},
                    ],
                },
                {
                    "source": "espece",
                    "target": "species_name",
                    "lookup": {"sheet": "especes", "key_column": "code", "value_column": "nom"},
                },
            ],
        }
    ],
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
        url = f"sqlite:///{tmp_path / 'ing.db'}"
    else:
        admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            for schema in ("ingestion", "data"):
                c.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                c.execute(text(f"CREATE SCHEMA {schema}"))
        sep = "&" if "?" in PG_URL else "?"
        url = f"{PG_URL}{sep}options=-csearch_path%3Dingestion%2Cdata"
    settings = Settings(
        service_name="ingestion", database_url=url, service_token=TOKEN, jwt_secret="x" * 32
    )
    cfg = IngestionConfig(sync_jobs=True, local_storage_dir=str(tmp_path / "files"))
    app = create_app(
        settings, cfg, storage=LocalStorage(str(tmp_path / "files")), run_migrations=True
    )
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)
    yield app
    app.extensions["db_engine"].dispose()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def hdr():
    def _hdr(role="designer"):
        return {
            "X-Service-Token": TOKEN,
            "X-User-Id": str(uuid.UUID(int=7)),
            "X-User-Role": role,
        }

    return _hdr


@pytest.fixture
def upload(client, hdr):
    def _upload(data: bytes | None = None, name="rapport.xlsx", role="operator"):
        r = client.post(
            "/ingestion/uploads",
            headers=hdr(role),
            data={"file": (io.BytesIO(data or sample_workbook()), name)},
            content_type="multipart/form-data",
        )
        return r

    return _upload


@pytest.fixture
def make_profile(client, hdr):
    def _make(definition=None, name="Lab results"):
        r = client.post(
            "/ingestion/profiles",
            headers=hdr("designer"),
            json={"name": name, "definition": definition or PROFILE},
        )
        assert r.status_code == 201, r.json
        return r.json

    return _make


@pytest.fixture
def run_import(client, hdr):
    def _run(upload_id, profile_id, **extra):
        return client.post(
            f"/ingestion/uploads/{upload_id}/import",
            headers=hdr("operator"),
            json={"profile_id": profile_id, **extra},
        )

    return _run
