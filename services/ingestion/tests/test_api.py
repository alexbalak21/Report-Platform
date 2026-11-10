"""End-to-end tests of the ingestion HTTP API (runs on SQLite, and on Postgres with TEST_PG_URL)."""

import copy
from datetime import date
from decimal import Decimal

from conftest import PROFILE, make_xlsx, sample_workbook


def _import_ok(upload, make_profile, run_import, data=None, profile=None):
    up = upload(data).json
    prof = make_profile(profile)
    run = run_import(up["id"], prof["id"]).json
    return up, prof, run


# --------------------------------------------------------------------------- access control


def test_requires_service_token(client):
    assert client.get("/ingestion/uploads").status_code == 401
    r = client.get("/ingestion/uploads", headers={"X-Service-Token": "wrong"})
    assert r.status_code == 401


def test_role_matrix(client, hdr, upload, make_profile):
    assert upload(role="viewer").status_code == 403  # viewers cannot upload
    assert upload(role="operator").status_code == 201
    r = client.post(
        "/ingestion/profiles", headers=hdr("operator"), json={"name": "x", "definition": PROFILE}
    )
    assert r.status_code == 403  # profiles are a designer job
    assert client.get("/ingestion/profiles", headers=hdr("viewer")).status_code == 200


# --------------------------------------------------------------------------- upload


def test_upload_rejects_bad_files(upload):
    assert upload(b"not a zip", name="x.xlsx").status_code in (400, 422)
    assert upload(sample_workbook(), name="x.csv").status_code in (400, 422)
    assert upload(sample_workbook(), name="../../etc/passwd").status_code in (400, 422)  # not .xlsx


def test_upload_lists_sheets_and_cleans_filename(upload):
    r = upload(name="../../etc/rapport.xlsx")
    assert r.status_code == 201
    assert r.json["filename"] == "rapport.xlsx"
    # backslashes are mangled by the multipart parser before we see them; never a separator
    r2 = upload(name="C:\\evil\\..\\rapport.xlsx")
    assert (
        r2.status_code == 201 and "/" not in r2.json["filename"] and "\\" not in r2.json["filename"]
    )
    assert r.json["sheets"] == ["Rapport", "especes"]
    assert r.json["status"] == "uploaded"


def test_preview_without_profiles(client, hdr, upload):
    up = upload().json
    r = client.get(f"/ingestion/uploads/{up['id']}/preview", headers=hdr("viewer"))
    assert r.status_code == 200
    body = r.json
    assert [s["name"] for s in body["sheets"]] == ["Rapport", "especes"]
    assert body["suggested_profiles"] == []
    assert body["preview"]["headers"][0] == "numero echantillon"


def test_preview_suggests_matching_profile_and_flags_unmapped(client, hdr, upload, make_profile):
    prof = make_profile()
    other = copy.deepcopy(PROFILE)
    other["match"] = {"sheet": "Rapport", "required_headers": ["colonne inexistante", "lot"]}
    make_profile(other, name="Other")
    up = upload(sample_workbook()).json
    r = client.get(f"/ingestion/uploads/{up['id']}/preview", headers=hdr("viewer")).json
    best = r["suggested_profiles"][0]
    assert best["id"] == prof["id"] and best["score"] == 1.0 and best["suggested"] is True
    # the other profile only matches half of its headers
    assert r["suggested_profiles"][1]["suggested"] is False


def test_preview_unknown_sheet_is_404(client, hdr, upload):
    up = upload().json
    r = client.get(f"/ingestion/uploads/{up['id']}/preview?sheet=nope", headers=hdr("viewer"))
    assert r.status_code == 404


# --------------------------------------------------------------------------- import


def test_import_happy_path(client, hdr, upload, make_profile, run_import):
    up, prof, run = _import_ok(upload, make_profile, run_import)
    assert run["status"] == "done", run
    assert run["rows_ok"] == 3 and run["rows_failed"] == 0
    assert run["tables"]["samples"]["rows"] == 3

    up_after = client.get(f"/ingestion/uploads/{up['id']}", headers=hdr("viewer")).json
    assert up_after["status"] == "done"

    rows = client.get(
        "/ingestion/tables/samples/rows?sort=sample_number", headers=hdr("viewer")
    ).json
    assert rows["total"] == 3
    first, _, third = rows["items"]
    assert first["customer_name"] == "ACME Corp"
    assert first["species_name"] == "Saumon"  # lookup resolved from the 'especes' sheet
    assert Decimal(first["imp_pct"]) == Decimal("12.6")  # 0.126 * 100, rounded to 1 decimal
    assert first["report_date"] == "2026-06-21"
    assert Decimal(third["imp_pct"]) == Decimal("20")  # "0,2": comma decimal accepted
    assert third["report_date"] == "2026-06-22"  # "22/06/2026" text date parsed


def test_reimport_is_idempotent(client, hdr, upload, make_profile, run_import):
    up, prof, _ = _import_ok(upload, make_profile, run_import)
    again = run_import(up["id"], prof["id"]).json
    assert again["status"] == "done"
    total = client.get("/ingestion/tables/samples/rows", headers=hdr("viewer")).json["total"]
    assert total == 3  # upserted on the key, not duplicated


def test_reupload_updates_changed_values(client, hdr, upload, make_profile, run_import):
    _, prof, _ = _import_ok(upload, make_profile, run_import)
    changed = sample_workbook()
    # same key (1001) but another customer, plus a new sample
    rows = [
        ["numero echantillon", "date rapport", "nom entreprise client", "lot", "imp", "espece"],
        [1001, date(2026, 6, 21), "Renamed Ltd", "L240501", 0.126, "SAL"],
        [1004, date(2026, 6, 23), "New Client", "L240504", 0.3, "COD"],
    ]
    changed = make_xlsx(
        {"Rapport": rows, "especes": [["code", "nom"], ["SAL", "Saumon"], ["COD", "Cabillaud"]]}
    )
    up2 = upload(changed).json
    assert run_import(up2["id"], prof["id"]).json["status"] == "done"
    out = client.get(
        "/ingestion/tables/samples/rows?sort=sample_number", headers=hdr("viewer")
    ).json
    assert out["total"] == 4
    assert out["items"][0]["customer_name"] == "Renamed Ltd"


def test_bad_rows_are_reported_not_fatal(client, hdr, upload, make_profile, run_import):
    bad = [
        [1005, date(2026, 6, 24), "=cmd|' /C calc'!A0", "", 0.1, "SAL"],  # missing required lot
        ["abc", date(2026, 6, 24), "Client", "L9", 0.1, "SAL"],  # key is not an integer
        [1006, date(2026, 6, 24), "Client", "L10", 0.1, "UNKNOWN"],  # lookup misses
    ]
    up, _, run = _import_ok(upload, make_profile, run_import, data=sample_workbook(extra_rows=bad))
    assert run["status"] == "done"
    assert run["rows_failed"] >= 2
    assert run["rows_ok"] >= 3
    errors = run["errors"]
    assert errors and all({"level", "row", "message"} <= e.keys() for e in errors)

    csv_resp = client.get(f"/ingestion/runs/{run['id']}/errors.csv", headers=hdr("viewer"))
    assert csv_resp.status_code == 200
    assert csv_resp.mimetype == "text/csv"
    assert csv_resp.data.startswith(b"\xef\xbb\xbf")  # BOM for Excel


def test_csv_neutralises_formula_injection(client, hdr, upload, make_profile, run_import):
    bad = [[1005, date(2026, 6, 24), "x", "", 0.1, "SAL"]]
    _, _, run = _import_ok(upload, make_profile, run_import, data=sample_workbook(extra_rows=bad))
    # force a hostile value into the stored errors and check the CSV escapes it
    from app.models import ImportRun

    app = client.application
    with app.extensions["db_sessionmaker"]() as s:
        r = s.get(ImportRun, __import__("uuid").UUID(run["id"]))
        r.errors = [
            {"level": "error", "row": 9, "column": "=A1", "message": "+SUM(1)", "value": "@x"}
        ]
        s.commit()
    body = client.get(f"/ingestion/runs/{run['id']}/errors.csv", headers=hdr("viewer")).data.decode(
        "utf-8-sig"
    )
    assert "'=A1" in body and "'+SUM(1)" in body and "'@x" in body


def test_dry_run_writes_nothing(client, hdr, upload, make_profile, run_import):
    up = upload().json
    prof = make_profile()
    run = run_import(up["id"], prof["id"], dry_run=True).json
    assert run["status"] == "done" and run["dry_run"] is True
    tables = client.get("/ingestion/tables", headers=hdr("viewer")).json["items"]
    assert tables == [] or all(t["row_count"] == 0 for t in tables)
    assert (
        client.get(f"/ingestion/uploads/{up['id']}", headers=hdr("viewer")).json["status"] != "done"
    )


def test_strict_profile_aborts_on_any_bad_row(client, hdr, upload, make_profile, run_import):
    strict = copy.deepcopy(PROFILE)
    strict["strict"] = True
    bad = [[1005, date(2026, 6, 24), "x", "", 0.1, "SAL"]]
    up, _, run = _import_ok(
        upload, make_profile, run_import, data=sample_workbook(extra_rows=bad), profile=strict
    )
    assert run["status"] == "failed"
    tables = client.get("/ingestion/tables", headers=hdr("viewer")).json["items"]
    assert tables == [] or all(t["row_count"] == 0 for t in tables)  # all-or-nothing


def test_profile_that_does_not_fit_the_workbook_fails_cleanly(
    client, hdr, upload, make_profile, run_import
):
    wrong = copy.deepcopy(PROFILE)
    wrong["tables"][0]["columns"][3]["source"] = "colonne inexistante"
    up, _, run = _import_ok(upload, make_profile, run_import, profile=wrong)
    assert run["status"] == "failed"
    assert run["error_message"]


def test_import_unknown_ids(client, hdr, upload, make_profile):
    import uuid

    up = upload().json
    r = client.post(
        f"/ingestion/uploads/{up['id']}/import",
        headers=hdr("operator"),
        json={"profile_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404
    r = client.post(f"/ingestion/uploads/{up['id']}/import", headers=hdr("operator"), json={})
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------------- profiles


def test_profile_crud_and_versioning(client, hdr, make_profile):
    prof = make_profile()
    assert prof["version"] == 1
    changed = copy.deepcopy(PROFILE)
    changed["header_row"] = 1
    changed["tables"][0]["columns"][2]["target"] = "client"
    r = client.put(
        f"/ingestion/profiles/{prof['id']}",
        headers={**hdr("designer"), "If-Match": str(prof["version"])},
        json={"name": "Lab results", "definition": changed},
    )
    assert r.status_code == 200, r.json
    assert r.json["version"] == 2
    stale = client.put(
        f"/ingestion/profiles/{prof['id']}",
        headers={**hdr("designer"), "If-Match": "1"},  # already moved on to version 2
        json={"name": "Lab results", "definition": changed},
    )
    assert stale.status_code == 409
    dup = client.post(
        "/ingestion/profiles",
        headers=hdr("designer"),
        json={"name": "Lab results", "definition": PROFILE},
    )
    assert dup.status_code == 409  # names are unique
    assert client.delete(
        f"/ingestion/profiles/{prof['id']}", headers=hdr("designer")
    ).status_code in (200, 204)
    assert (
        client.get(f"/ingestion/profiles/{prof['id']}", headers=hdr("designer")).status_code == 404
    )


def test_profile_rejects_invalid_definition(client, hdr):
    bad = copy.deepcopy(PROFILE)
    bad["tables"][0]["table"] = "Bad Name"
    r = client.post(
        "/ingestion/profiles", headers=hdr("designer"), json={"name": "x", "definition": bad}
    )
    assert r.status_code in (400, 422)
    assert r.json["error"]["code"]


def test_validate_draft_against_upload(client, hdr, upload):
    up = upload().json
    ok = client.post(
        "/ingestion/profiles/validate",
        headers=hdr("designer"),
        json={"definition": PROFILE, "upload_id": up["id"]},
    )
    assert ok.status_code == 200, ok.json
    assert ok.json.get("valid") is True
    assert ok.json["errors_total"] == 0


# --------------------------------------------------------------------------- browsing


def test_row_browsing(client, hdr, upload, make_profile, run_import):
    _import_ok(upload, make_profile, run_import)
    h = hdr("viewer")

    page = client.get(
        "/ingestion/tables/samples/rows?page=2&page_size=2&sort=sample_number", headers=h
    ).json
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2

    eq = client.get("/ingestion/tables/samples/rows?eq.customer_name=ACME%20Corp", headers=h).json
    assert eq["total"] == 2

    q = client.get("/ingestion/tables/samples/rows?q=nord", headers=h).json
    assert q["total"] == 1 and q["items"][0]["customer_name"] == "Poissons du Nord"

    assert client.get("/ingestion/tables/samples/rows?sort=nope", headers=h).status_code in (
        400,
        422,
    )
    assert client.get("/ingestion/tables/samples/rows?eq.nope=1", headers=h).status_code in (
        400,
        422,
    )
    assert client.get("/ingestion/tables/nope/rows", headers=h).status_code == 404
    assert client.get("/ingestion/tables/samples;drop/rows", headers=h).status_code == 404

    one = client.get(f"/ingestion/tables/samples/rows/{page['items'][0]['_id']}", headers=h)
    assert one.status_code == 200 and one.json["_id"] == page["items"][0]["_id"]


def test_like_wildcards_are_escaped(client, hdr, upload, make_profile, run_import):
    _import_ok(upload, make_profile, run_import)
    r = client.get("/ingestion/tables/samples/rows?q=%25", headers=hdr("viewer")).json
    assert r["total"] == 0  # a literal '%' matches nothing; it is not a wildcard


def test_table_registry_and_placeholder_catalog(client, hdr, upload, make_profile, run_import):
    _import_ok(upload, make_profile, run_import)
    tables = client.get("/ingestion/tables", headers=hdr("viewer")).json["items"]
    assert tables[0]["name"] == "samples" and tables[0]["row_count"] == 3
    cols = {c["name"]: c["type"] for c in tables[0]["columns"]}
    assert cols["imp_pct"] == "number" and cols["report_date"] == "date"

    cat = client.get("/ingestion/placeholders", headers=hdr("viewer")).json
    keys = {i["key"] for i in cat["items"]}
    assert {"sample_number", "customer_name", "lot", "imp_pct", "species_name"} <= keys
    assert any(i["qualified_key"] == "samples.lot" for i in cat["items"])
    assert cat["builtins"]  # today, report_number, page, ...


def test_report_number_write_back(client, hdr, upload, make_profile, run_import):
    _import_ok(upload, make_profile, run_import)
    row = client.get("/ingestion/tables/samples/rows", headers=hdr("viewer")).json["items"][0]
    url = f"/ingestion/tables/samples/rows/{row['_id']}"
    ok = client.patch(url, headers=hdr("operator"), json={"_report_number": "260621-1"})
    assert ok.status_code == 200
    assert client.get(url, headers=hdr("viewer")).json["_report_number"] == "260621-1"
    for body in ({"lot": "hacked"}, {"_report_number": "x" * 41}, {"_report_number": 5}, {}):
        assert client.patch(url, headers=hdr("operator"), json=body).status_code in (400, 422)
    assert (
        client.patch(
            url, headers={"X-Service-Token": "bad"}, json={"_report_number": "1"}
        ).status_code
        == 401
    )


def test_import_publishes_data_imported_event(client, app, upload, make_profile, run_import):
    _import_ok(upload, make_profile, run_import)
    redis = app.extensions["redis"]
    streams = redis.keys("*")
    assert streams, "expected an event stream in redis"
    entries = []
    for key in streams:
        if redis.type(key) == "stream":
            entries += redis.xrange(key)
    assert any("data.imported" in str(fields) for _, fields in entries)
