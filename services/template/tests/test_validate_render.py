import uuid

from app.clients import Catalog
from conftest import inst, make_doc
from rp_shared.template_render import PAGE_TOKEN


def validate(client, h, t, document=None):
    body = {"document": document} if document is not None else {}
    r = client.post(f"/templates/{t['id']}/validate", json=body, headers=h("operator"))
    assert r.status_code == 200, r.json
    return r.json


def codes(result, severity=None):
    return [i["code"] for i in result["issues"] if severity is None or i["severity"] == severity]


# ------------------------------------------------------------------ validation
def test_a_clean_template_validates(client, h, make_template, fb):
    t = make_template(
        make_doc(
            header=[inst(fb.header, "h")],
            body=[inst(fb.title, "t"), inst(fb.kv, "kv"), inst(fb.table, "tb", source="samples")],
            footer=[inst(fb.footer, "f")],
        )
    )
    r = validate(client, h, t)
    assert r == {"ok": True, "errors": 0, "warnings": 0, "issues": []}


def test_region_rules(client, h, make_template, fb):
    t = make_template(
        make_doc(
            header=[inst(fb.footer, "wrong-h"), inst(fb.logo, "ok-h", src="x")],
            body=[inst(fb.header, "wrong-b")],
            footer=[inst(fb.title, "wrong-f")],
            overlays=[
                {**inst(fb.footer, "wrong-o"), "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10},
                {**inst(fb.title, "ok-o"), "x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10},
            ],
        )
    )
    r = validate(client, h, t)
    bad = sorted(i["instance_id"] for i in r["issues"] if i["code"] == "REGION_NOT_ALLOWED")
    assert bad == ["wrong-b", "wrong-f", "wrong-h", "wrong-o"] and not r["ok"]


def test_required_invalid_and_unknown_props(client, h, make_template, fb):
    t = make_template(
        make_doc(
            body=[
                inst(fb.logo, "a"),  # src missing
                inst(fb.logo, "b", src="", size="big"),  # blank required + wrong type
                inst(fb.logo, "c", src="u", size=3, mode="stretch", border="yes"),
                inst(fb.logo, "d", src="u", mode="fit", border=True, bogus=1),
            ]
        )
    )
    r = validate(client, h, t)
    by = {(i["instance_id"], i["code"], i.get("prop")) for i in r["issues"]}
    assert ("a", "PROP_REQUIRED", "src") in by
    assert ("b", "PROP_REQUIRED", "src") in by and ("b", "PROP_INVALID", "size") in by
    assert ("c", "PROP_INVALID", "mode") in by and ("c", "PROP_INVALID", "border") in by
    assert ("d", "PROP_UNKNOWN", "bogus") in by
    assert not any(i[0] == "d" and i[1] != "PROP_UNKNOWN" for i in by)
    msg = next(i["message"] for i in r["issues"] if i.get("prop") == "mode")
    assert "fit, fill" in msg


def test_required_prop_satisfied_by_default(client, h, make_template, fb):
    block = fb.add(
        "Needs title",
        "<p>{{props.title}}</p>",
        props_schema={"title": {"type": "string", "required": True, "default": "Hello"}},
    )
    r = validate(client, h, make_template(make_doc(body=[inst(block, "a")])))
    assert r["ok"]


def test_datasource_checks(client, h, make_template, fb):
    t = make_template(
        make_doc(
            body=[
                inst(fb.table, "none"),
                inst(fb.table, "ghost", source="nope"),
                inst(fb.table, "good", source="samples"),
            ]
        )
    )
    r = validate(client, h, t)
    by = {(i["instance_id"], i["code"]) for i in r["issues"]}
    assert ("none", "DATASOURCE_REQUIRED") in by and ("ghost", "DATASOURCE_UNKNOWN") in by
    assert not any(i[0] == "good" for i in by)


def test_loop_columns_are_checked_against_the_chosen_table(client, h, make_template, fb, catalog):
    catalog.catalog = Catalog(
        keys={"lot", "other_col"},
        tables={"samples": {"lot"}, "other": {"lot", "imp_pct", "other_col"}},
    )
    t = make_template(
        make_doc(body=[inst(fb.table, "a", source="samples"), inst(fb.table, "b", source="other")])
    )
    r = validate(client, h, t)
    unknown = [(i["instance_id"], i["key"]) for i in r["issues"] if i["code"] == "UNKNOWN_KEY"]
    assert unknown == [("a", "imp_pct")]


def test_unknown_keywords_are_errors_but_builtins_and_loop_names_are_fine(
    client, h, make_template, fb, catalog
):
    odd = fb.add("Odd", "<p>{{nope}} {{today}} {{report_number}}</p>", placeholders=["nope"])
    t = make_template(make_doc(body=[inst(odd, "o"), inst(fb.kv, "kv")]))
    r = validate(client, h, t)
    assert [(i["code"], i["key"]) for i in r["issues"]] == [("UNKNOWN_KEY", "nope")]
    # the loop name 'rows' is never looked up in the catalog (the data source decides)
    t2 = make_template(make_doc(body=[inst(fb.table, "tb", source="samples")]), name="t2")
    assert validate(client, h, t2)["ok"]


def test_qualified_keywords_are_known(client, h, make_template, fb, catalog):
    q = fb.add("Q", "<p>{{samples.lot}}</p>", placeholders=["samples.lot"])
    catalog.catalog = Catalog(keys={"lot", "samples.lot"}, tables={"samples": {"lot"}})
    assert validate(client, h, make_template(make_doc(body=[inst(q, "q")])))["ok"]


def test_catalog_unavailable_and_empty_only_warn(client, h, make_template, fb, catalog):
    t = make_template(make_doc(body=[inst(fb.kv, "kv"), inst(fb.table, "tb")]))
    catalog.catalog = None
    r = validate(client, h, t)
    assert "CATALOG_UNAVAILABLE" in codes(r, "warning")
    assert codes(r, "error") == ["DATASOURCE_REQUIRED"]  # still checked without the catalog
    catalog.catalog = Catalog()
    r = validate(client, h, t)
    assert "CATALOG_EMPTY" in codes(r, "warning")
    assert "UNKNOWN_KEY" not in codes(r)


def test_missing_block_version_is_an_error(client, h, make_template, fb):
    t = make_template(
        make_doc(body=[inst(str(uuid.uuid4()), "ghost"), inst(fb.title, "future", version=7)])
    )
    r = validate(client, h, t)
    assert codes(r, "error") == ["BLOCK_NOT_FOUND", "BLOCK_NOT_FOUND"]


def test_outdated_and_archived_blocks_warn(client, h, make_template, fb):
    fb.bump(fb.title)
    fb.info[fb.kv]["is_archived"] = True
    t = make_template(make_doc(body=[inst(fb.title, "old", version=1), inst(fb.kv, "kv")]))
    r = validate(client, h, t)
    assert r["ok"]
    outdated = next(i for i in r["issues"] if i["code"] == "BLOCK_OUTDATED")
    assert (outdated["pinned"], outdated["latest"]) == (1, 2)
    assert "BLOCK_ARCHIVED" in codes(r, "warning")


def test_page_geometry_rules(client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "t")]))
    doc = make_doc(body=[inst(fb.title, "t")], header=[inst(fb.header, "h")])
    doc["regions"]["header"]["height_mm"] = 100
    doc["regions"]["footer"]["items"] = [inst(fb.footer, "f")]
    doc["regions"]["footer"]["height_mm"] = 100
    # Regions are capped at 100 mm each, so leave under MIN_BODY_HEIGHT_MM (30) with big margins:
    # 297 - 40 - 40 - 100 - 100 = 17 mm.
    doc["page"]["margins_mm"] = {"t": 40, "r": 15, "b": 40, "l": 15}
    doc["overlays"] = [
        {**inst(fb.title, "far"), "x_mm": 200, "y_mm": 0, "w_mm": 30, "h_mm": 10},
        {**inst(fb.title, "near"), "x_mm": 100, "y_mm": 10, "w_mm": 30, "h_mm": 10},
    ]
    r = validate(client, h, t, document=doc)
    assert "NO_BODY_ROOM" in codes(r, "error")
    assert [i["instance_id"] for i in r["issues"] if i["code"] == "OVERLAY_OUTSIDE_PAGE"] == ["far"]


def test_empty_header_region_takes_no_room(client, h, make_template):
    t = make_template()
    r = validate(client, h, t)
    assert r["ok"] and codes(r) == ["EMPTY_BODY"]


def test_validate_an_unsaved_document_without_touching_the_draft(client, h, make_template, fb):
    t = make_template()
    r = validate(client, h, t, document=make_doc(body=[inst(fb.logo, "x")]))
    assert not r["ok"]
    assert validate(client, h, t)["ok"]  # the stored draft is unchanged
    bad = client.post(
        f"/templates/{t['id']}/validate", json={"document": {"x": 1}}, headers=h("operator")
    )
    assert bad.status_code == 400


def test_block_service_down_is_a_502_not_a_crash(client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "t")]))
    fb.down = True
    r = client.post(f"/templates/{t['id']}/validate", headers=h("operator"))
    assert r.status_code == 502 and r.json["error"]["code"] == "UPSTREAM_ERROR"


# ------------------------------------------------------------------ render
def render(client, h, t, **body):
    r = client.post(f"/templates/{t['id']}/render", json=body, headers=h("operator"))
    assert r.status_code == 200, r.json
    return r.json


def test_render_sample_mode_shows_keywords_and_layout(client, h, make_template, fb):
    t = make_template(
        make_doc(
            header=[inst(fb.header, "h", organisation="ACME")],
            body=[inst(fb.kv, "kv"), inst(fb.table, "tb", source="samples")],
            footer=[inst(fb.footer, "f")],
        )
    )
    out = render(client, h, t)
    assert out["ok"] and out["with_data"] is False and out["mode"] == "preview"
    html = out["html"]
    assert "ACME N° {{report_number}}" in html
    assert "{{customer_name}} {{lot}}" in html and "<td>{{lot}}</td><td>{{imp_pct}}</td>" in html
    assert "Page 1 / 1" in html


def test_render_with_a_real_record(client, h, make_template, fb, data):
    rid = str(uuid.uuid4())
    data.records[("samples", rid)] = {"_id": rid, "customer_name": "ACME", "lot": "L9"}
    data.table_rows["samples"] = [
        {"lot": "L1", "imp_pct": "1%"},
        {"lot": "L2", "imp_pct": "2%"},
        {"lot": "L3", "imp_pct": "3%"},
    ]
    t = make_template(make_doc(body=[inst(fb.kv, "kv"), inst(fb.table, "tb", source="samples")]))
    out = render(client, h, t, record={"table": "samples", "id": rid}, max_rows=2)
    assert out["with_data"] is True
    assert "ACME L9" in out["html"]
    assert "<td>L1</td><td>1%</td></tr><tr><td>L2</td>" in out["html"]
    assert "L3" not in out["html"]  # max_rows
    assert out["unresolved"] == [] and out["ok"]


def test_render_with_data_reports_unresolved_keywords(client, h, make_template, fb, data):
    rid = str(uuid.uuid4())
    data.records[("samples", rid)] = {"customer_name": "ACME"}  # no 'lot'
    t = make_template(make_doc(body=[inst(fb.kv, "kv")]))
    out = render(client, h, t, record={"table": "samples", "id": rid})
    assert out["unresolved"] == ["lot"] and out["ok"] is False
    assert "[[lot?]]" in out["html"]
    out = render(client, h, t, record={"table": "samples", "id": rid}, missing="blank")
    assert "[[" not in out["html"] and out["unresolved"] == ["lot"]


def test_render_report_number_comes_from_the_request_or_the_record(
    client, h, make_template, fb, data
):
    rid = str(uuid.uuid4())
    data.records[("samples", rid)] = {
        "customer_name": "A",
        "lot": "L",
        "_report_number": "261109-3",
    }
    t = make_template(make_doc(header=[inst(fb.header, "h")]))
    rec = {"table": "samples", "id": rid}
    assert "N° 261109-3" in render(client, h, t, record=rec)["html"]
    assert "N° 777" in render(client, h, t, record=rec, report_number="777")["html"]
    data.records[("samples", rid)].pop("_report_number")
    assert "N° PREVIEW" in render(client, h, t, record=rec)["html"]


def test_render_print_mode_uses_page_tokens(client, h, make_template, fb):
    t = make_template(make_doc(footer=[inst(fb.footer, "f")]))
    out = render(client, h, t, mode="print")
    assert out["mode"] == "print" and f"Page {PAGE_TOKEN} /" in out["html"]
    assert "box-shadow" not in out["html"]


def test_render_a_published_version_or_an_unsaved_document(client, h, make_template, save, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a", text="First")]))
    client.post(f"/templates/{t['id']}/publish", headers=h())
    t = client.get(f"/templates/{t['id']}", headers=h("viewer")).json
    save(t, make_doc(body=[inst(fb.title, "a", text="Second")]))
    assert "First" in render(client, h, t, version=1)["html"]
    assert "Second" in render(client, h, t)["html"]
    unsaved = make_doc(body=[inst(fb.title, "a", text="Third")])
    assert "Third" in render(client, h, t, document=unsaved)["html"]


def test_render_pins_the_block_version(client, h, make_template, fb):
    v2 = fb.bump(fb.title, "<h1>NEW {{props.text}}</h1>")
    t = make_template(
        make_doc(body=[inst(fb.title, "old", version=1), inst(fb.title, "new", version=v2)])
    )
    html = render(client, h, t)["html"]
    assert html.count("NEW") == 1 and html.index("Report") < html.index("NEW Report")


def test_render_reports_missing_blocks(client, h, make_template, fb):
    gone = str(uuid.uuid4())
    t = make_template(make_doc(body=[inst(gone, "ghost")]))
    out = render(client, h, t)
    assert out["ok"] is False and out["missing_blocks"][0]["instance_id"] == "ghost"
    assert "Block unavailable" in out["html"]


def test_render_input_validation(client, h, make_template, data):
    t = make_template()
    url = f"/templates/{t['id']}/render"
    post = lambda body: client.post(url, json=body, headers=h("operator"))  # noqa: E731
    assert post({"mode": "pdf"}).status_code == 400
    assert post({"missing": "error"}).status_code == 400
    assert post({"record": "x"}).status_code == 400
    assert post({"record": {"table": "samples"}}).status_code == 400
    assert post({"record": {"table": "samples", "id": "nope"}}).status_code == 400
    assert post({"record": {"table": "samples", "id": str(uuid.uuid4())}}).status_code == 404
    assert post({"record": {"table": "Bad-Name", "id": str(uuid.uuid4())}}).status_code == 404
    rid = str(uuid.uuid4())
    data.records[("samples", rid)] = {}
    assert post({"record": {"table": "samples", "id": rid}, "max_rows": "x"}).status_code == 400
    assert (
        client.post(
            url, data="[1]", content_type="application/json", headers=h("operator")
        ).status_code
        == 400
    )


def test_render_escapes_data_and_blocks_script_injection(client, h, make_template, fb, data):
    rid = str(uuid.uuid4())
    data.records[("samples", rid)] = {"customer_name": "<script>x</script>", "lot": "<b>L</b>"}
    t = make_template(make_doc(body=[inst(fb.kv, "kv")]))
    html = render(client, h, t, record={"table": "samples", "id": rid})["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>L</b>" not in html
