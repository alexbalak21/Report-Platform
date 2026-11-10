import uuid

from conftest import doc


def test_regenerate_keeps_id_and_number_and_discards_edits(client, h, data, record, generate, app):
    r = generate(record)
    storage = app.extensions["report_deps"].storage
    key = f"reports/{r['id']}/report.html"
    storage.put(key, b"<p>hand edited</p>")

    data.records[("samples", str(record))]["customer_name"] = "NEW NAME"
    again = client.post(f"/reports/{r['id']}/regenerate", headers=h())
    assert again.status_code == 200
    assert again.json["id"] == r["id"] and again.json["report_number"] == r["report_number"]
    body = client.get(f"/reports/{r['id']}/html", headers=h()).get_data(as_text=True)
    assert "NEW NAME" in body and "hand edited" not in body


def test_regenerate_can_move_to_the_latest_template(client, h, templates, tpl, record, generate):
    r = generate(record)
    templates.publish(doc(body=[]), name="Renamed", template_id=tpl)
    same = client.post(f"/reports/{r['id']}/regenerate", headers=h()).json
    assert same["template"]["version"] == 1
    moved = client.post(
        f"/reports/{r['id']}/regenerate", json={"use_latest": True}, headers=h()
    ).json
    assert moved["template"] == {"id": tpl, "version": 2, "name": "Renamed"}


def test_finalize_stores_the_pdf_and_locks_the_report(client, h, record, generate, pdf, events):
    r = generate(record)
    done = client.post(f"/reports/{r['id']}/finalize", headers=h())
    assert done.status_code == 200
    assert done.json["status"] == "final" and done.json["has_pdf"] is True
    assert done.json["unchanged"] is False and done.json["finalized_at"]
    assert len(pdf.printed) == 1 and "ACME Corp" in pdf.printed[0]

    got = client.get(f"/reports/{r['id']}/pdf", headers=h("viewer"))
    assert got.status_code == 200 and got.mimetype == "application/pdf"
    assert got.data == b"%PDF-fake-1" and got.headers["X-Report-Status"] == "final"
    assert f"{r['report_number']}.pdf" in got.headers["Content-Disposition"]
    assert len(pdf.printed) == 1  # served from storage, not printed again

    again = client.post(f"/reports/{r['id']}/finalize", headers=h())
    assert again.json["unchanged"] is True and len(pdf.printed) == 1
    assert client.post(f"/reports/{r['id']}/regenerate", headers=h()).status_code == 409
    assert client.delete(f"/reports/{r['id']}", headers=h()).status_code == 409
    assert [e["type"] for e in events()] == ["report.generated", "report.finalized"]


def test_finalize_is_refused_while_a_keyword_is_unresolved(client, h, data, generate, app, pdf):
    r = generate(data.add(customer_name="ACME"))  # no lot -> [[lot?]]
    refused = client.post(f"/reports/{r['id']}/finalize", headers=h())
    assert refused.status_code == 422 and refused.json["error"]["code"] == "REPORT_UNRESOLVED_KEYS"
    assert refused.json["error"]["details"] == [{"key": "lot"}]
    assert pdf.printed == []

    # the user fixes it by hand in the editor: finalize looks at the *current* HTML
    storage = app.extensions["report_deps"].storage
    key = f"reports/{r['id']}/report.html"
    storage.put(key, storage.get(key).replace(b"[[lot?]]", b"L-FIXED"))
    assert client.post(f"/reports/{r['id']}/finalize", headers=h()).status_code == 200


def test_pdf_failure_leaves_the_report_a_draft(client, h, record, generate, pdf):
    r = generate(record)
    pdf.fail = True
    bad = client.post(f"/reports/{r['id']}/finalize", headers=h())
    assert bad.status_code == 502 and bad.json["error"]["code"] == "PDF_FAILED"
    assert client.get(f"/reports/{r['id']}", headers=h()).json["status"] == "draft"
    assert client.get(f"/reports/{r['id']}/pdf", headers=h()).status_code == 502
    pdf.fail = False
    assert client.post(f"/reports/{r['id']}/finalize", headers=h()).status_code == 200


def test_draft_pdf_is_printed_on_the_fly_and_not_stored(client, h, record, generate, pdf):
    r = generate(record)
    got = client.get(f"/reports/{r['id']}/pdf", headers=h("viewer"))
    assert got.status_code == 200 and got.headers["X-Report-Status"] == "draft"
    assert client.get(f"/reports/{r['id']}", headers=h()).json["has_pdf"] is False


def test_finalize_retries_the_write_back(client, h, data, record, generate):
    data.writeback_ok = False
    r = generate(record)
    assert r["record_synced"] is False
    data.writeback_ok = True
    done = client.post(f"/reports/{r['id']}/finalize", headers=h()).json
    assert done["record_synced"] is True


def test_delete_a_draft_frees_the_record_but_not_the_number(client, h, data, record, generate):
    r = generate(record)
    assert client.delete(f"/reports/{r['id']}", headers=h()).status_code == 204
    assert client.get(f"/reports/{r['id']}", headers=h()).status_code == 404
    assert data.writebacks[-1] == ("samples", str(record), None)  # the row is clear again
    assert client.get(f"/reports/{r['id']}/html", headers=h()).status_code == 404

    again = generate(record)
    assert again["report_number"].endswith("-2")  # the gap is honest


def test_unknown_ids_are_404(client, h):
    nope = uuid.uuid4()
    for method, path in [
        ("get", f"/reports/{nope}"),
        ("get", f"/reports/{nope}/html"),
        ("get", f"/reports/{nope}/pdf"),
        ("get", f"/reports/jobs/{nope}"),
        ("post", f"/reports/{nope}/finalize"),
        ("post", f"/reports/{nope}/regenerate"),
        ("delete", f"/reports/{nope}"),
    ]:
        assert getattr(client, method)(path, headers=h()).status_code == 404, path


def test_batch_generates_each_record_and_isolates_failures(client, h, tpl, data, events):
    good = [data.add(customer_name=f"C{i}", lot="L") for i in range(3)]
    ghost = uuid.uuid4()
    r = client.post(
        "/reports/batch",
        json={"template_id": tpl, "table": "samples", "record_ids": [*map(str, good), str(ghost)]},
        headers=h(),
    )
    assert r.status_code == 202 and r.json["total"] == 4
    job = client.get(f"/reports/jobs/{r.json['id']}", headers=h("viewer")).json
    assert job["status"] == "done" and (job["done"], job["failed"]) == (3, 1)
    assert job["template_version"] == 1 and job["finished_at"]
    by_status = {i["record_id"]: i for i in job["items"]}
    assert by_status[str(ghost)]["status"] == "failed"
    assert by_status[str(ghost)]["error"]["code"] == "NOT_FOUND"
    ok = [i for i in job["items"] if i["status"] == "ok"]
    assert len(ok) == 3 and all(i["report_id"] for i in ok)
    assert client.get("/reports", headers=h()).json["total"] == 3

    progress = [e["data"] for e in events() if e["type"] == "job.progress"]
    assert progress[0]["status"] == "running" and progress[-1]["status"] == "done"
    assert progress[-1]["done"] == 3 and progress[-1]["failed"] == 1


def test_batch_that_cannot_start_is_failed_not_stuck(client, h, templates, data, record):
    r = client.post(
        "/reports/batch",
        json={
            "template_id": templates.draft_only(),
            "table": "samples",
            "record_ids": [str(record)],
        },
        headers=h(),
    )
    job = client.get(f"/reports/jobs/{r.json['id']}", headers=h()).json
    assert job["status"] == "failed" and "no published version" in job["error"]


def test_batch_input_is_validated(client, h, tpl, record, app):
    def post(**body):
        base = {"template_id": tpl, "table": "samples", "record_ids": [str(record)]}
        return client.post("/reports/batch", json={**base, **body}, headers=h())

    assert post(record_ids=[]).status_code == 400
    assert post(record_ids="x").status_code == 400
    assert post(record_ids=["nope"]).status_code == 400
    too_many = [str(uuid.uuid4()) for _ in range(app.config["REPORT"].max_batch + 1)]
    assert post(record_ids=too_many).status_code == 400
    dup = post(record_ids=[str(record), str(record)])
    assert dup.status_code == 202 and dup.json["total"] == 1  # duplicates collapse
