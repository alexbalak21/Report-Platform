import re
import threading
import uuid
from datetime import date

import pytest
from conftest import doc, inst


def today_prefix():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return f"{datetime.now(ZoneInfo('Europe/Paris')):%y%m%d}"


def test_generate_creates_a_numbered_draft(client, h, tpl, record, data, generate, events):
    r = generate(record)
    assert re.fullmatch(rf"{today_prefix()}-1", r["report_number"])
    assert r["status"] == "draft" and r["has_pdf"] is False
    assert r["template"]["version"] == 1 and r["template"]["name"] == "Rapport d'essai"
    assert r["record"] == {"table": "samples", "id": str(record)}
    assert r["unresolved_keys"] == [] and r["record_synced"] is True

    html = client.get(f"/reports/{r['id']}/html", headers=h("viewer"))
    assert html.status_code == 200 and html.mimetype == "text/html"
    body = html.get_data(as_text=True)
    assert "Client: ACME Corp Lot: L240501" in body
    assert f"Rapport N° {r['report_number']}" in body
    assert "@page" in body  # print layout, not the grey on-screen desk

    assert data.writebacks == [("samples", str(record), r["report_number"])]
    assert [e["type"] for e in events()] == ["report.generated"]
    assert events()[0]["data"]["report_number"] == r["report_number"]


def test_numbers_count_up_per_day_and_follow_the_report_date(generate, data):
    a, b, c = (data.add(customer_name=f"C{i}", lot="L") for i in range(3))
    assert generate(a)["report_number"].endswith("-1")
    assert generate(b)["report_number"].endswith("-2")
    other_day = generate(c, report_date="2026-06-21")
    assert other_day["report_number"] == "260621-1"
    assert other_day["report_date"] == "2026-06-21"


def test_second_report_for_same_record_is_a_conflict(client, h, tpl, record, generate):
    first = generate(record)
    r = client.post(
        "/reports",
        json={"template_id": tpl, "table": "samples", "record_id": str(record)},
        headers=h(),
    )
    assert r.status_code == 409 and r.json["error"]["code"] == "REPORT_EXISTS"
    assert r.json["error"]["details"][0]["report_id"] == first["id"]


def test_failures_are_clean_errors(client, h, tpl, templates, record, generate):
    def post(**body):
        base = {"template_id": tpl, "table": "samples", "record_id": str(record)}
        return client.post("/reports", json={**base, **body}, headers=h())

    unpublished = post(template_id=templates.draft_only())
    assert unpublished.status_code == 409
    assert unpublished.json["error"]["code"] == "TEMPLATE_NOT_PUBLISHED"
    assert post(template_id=str(uuid.uuid4())).status_code == 404
    assert post(template_version=9).status_code == 404
    assert post(record_id=str(uuid.uuid4())).status_code == 404
    assert post(table="DROP TABLE x").status_code == 404  # never reaches the data service
    assert post(record_id="nope").status_code == 400
    assert post(template_version="x").status_code == 400
    assert post(report_date="21/06/2026").status_code == 400
    assert client.post("/reports", data="[]", headers=h()).status_code == 400


def test_roles(client, h, tpl, record):
    body = {"template_id": tpl, "table": "samples", "record_id": str(record)}
    assert client.post("/reports", json=body, headers=h("viewer")).status_code == 403
    assert client.post("/reports", json=body, headers=h(None)).status_code == 401
    assert client.get("/reports", headers=h("viewer")).status_code == 200
    assert client.get("/reports", headers={}).status_code == 401


def test_missing_value_is_marked_and_reported(client, h, tpl, data, generate):
    incomplete = data.add(customer_name="ACME Corp")  # no lot
    r = generate(incomplete)
    assert r["unresolved_keys"] == ["lot"] and r["unresolved_count"] == 1
    body = client.get(f"/reports/{r['id']}/html", headers=h()).get_data(as_text=True)
    assert "[[lot?]]" in body


def test_error_policy_refuses_and_gives_the_number_back(
    client, h, templates, blocks, data, generate
):
    strict = templates.publish(doc(body=[inst(blocks.kv, "kv")], missing_policy="error"))
    bad = data.add(customer_name="ACME")
    r = client.post(
        "/reports",
        json={"template_id": strict, "table": "samples", "record_id": str(bad)},
        headers=h(),
    )
    assert r.status_code == 422 and r.json["error"]["code"] == "REPORT_UNRESOLVED_KEYS"
    assert r.json["error"]["details"] == [{"key": "lot"}]
    assert client.get("/reports", headers=h()).json["total"] == 0  # nothing was stored

    good = data.add(customer_name="ACME", lot="L1")
    ok = generate(good, template_id=strict)
    assert ok["report_number"].endswith("-1")  # the failed attempt did not burn a number


def test_blank_policy_leaves_empty_text(client, h, templates, blocks, data, generate):
    lenient = templates.publish(doc(body=[inst(blocks.kv, "kv")], missing_policy="blank"))
    r = generate(data.add(customer_name="ACME"), template_id=lenient)
    assert r["unresolved_keys"] == ["lot"]
    body = client.get(f"/reports/{r['id']}/html", headers=h()).get_data(as_text=True)
    assert "Client: ACME Lot: " in body and "[[" not in body


def test_loop_blocks_read_their_rows(client, h, templates, blocks, data, generate):
    tid = templates.publish(doc(body=[inst(blocks.table, "t", source="samples")]))
    data.tables["samples"] = [{"lot": "A1"}, {"lot": "B2"}]
    r = generate(data.add(customer_name="X", lot="Z"), template_id=tid)
    body = client.get(f"/reports/{r['id']}/html", headers=h()).get_data(as_text=True)
    assert "<td>A1</td>" in body and "<td>B2</td>" in body


def test_a_template_version_that_lost_a_block_is_unprocessable(client, h, templates, record):
    tid = templates.publish(doc(body=[inst(str(uuid.uuid4()), "ghost")]))
    r = client.post(
        "/reports",
        json={"template_id": tid, "table": "samples", "record_id": str(record)},
        headers=h(),
    )
    assert r.status_code == 422 and r.json["error"]["code"] == "BLOCK_MISSING"


def test_pinned_version_is_used_even_after_a_newer_one(
    client, h, templates, blocks, tpl, data, generate
):
    templates.publish(doc(body=[]), name="v2", template_id=tpl)  # v2 is empty
    r1 = generate(data.add(customer_name="A", lot="1"), template_version=1)
    r2 = generate(data.add(customer_name="B", lot="2"))  # latest = v2
    assert (r1["template"]["version"], r2["template"]["version"]) == (1, 2)
    assert "ACME" not in client.get(f"/reports/{r2['id']}/html", headers=h()).get_data(as_text=True)


def test_write_back_failure_is_recorded_not_fatal(data, generate, record):
    data.writeback_ok = False
    r = generate(record)
    assert r["record_synced"] is False


def test_numbers_never_collide_under_concurrency(app, tpl, data, h):
    if app.dialect != "postgres":
        pytest.skip("needs real row locks; SQLite serialises writers anyway")
    ids = [data.add(customer_name=f"C{i}", lot="L") for i in range(12)]
    numbers, errors = [], []

    def work(rid):
        c = app.test_client()
        r = c.post(
            "/reports",
            json={"template_id": tpl, "table": "samples", "record_id": str(rid)},
            headers=h(),
        )
        (numbers if r.status_code == 201 else errors).append(r.json)

    threads = [threading.Thread(target=work, args=(i,)) for i in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    got = sorted(int(n["report_number"].split("-")[1]) for n in numbers)
    assert got == list(range(1, 13))  # unique and gap-free


def test_list_filters_and_pages(client, h, tpl, templates, blocks, data, generate):
    other = templates.publish(doc(body=[inst(blocks.kv, "kv")]), name="Certificat")
    records = [data.add(customer_name=f"C{i}", lot="L") for i in range(3)]
    for rid in records:
        generate(rid)
    generate(records[0], template_id=other)

    def get(qs):
        return client.get(f"/reports?{qs}", headers=h("viewer")).json

    assert get("")["total"] == 4
    assert get(f"template_id={other}")["total"] == 1
    assert get(f"record_id={records[0]}")["total"] == 2
    assert get("q=Certificat")["items"][0]["template"]["name"] == "Certificat"
    assert get("status=final")["total"] == 0
    page = get("page_size=3&page=2")
    assert page["total"] == 4 and len(page["items"]) == 1
    assert client.get("/reports?template_id=zzz", headers=h()).status_code == 400
    assert re.fullmatch(rf"{today_prefix()}-\d+", get("")["items"][0]["report_number"])
    assert date.fromisoformat(get("")["items"][0]["report_date"])
