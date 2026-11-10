import json
import uuid

import pytest
from conftest import SIMPLE, TABLE


def ids(resp):
    return [i["id"] for i in resp.json["items"]]


# --- create ----------------------------------------------------------------
def test_create_stores_version_one_sanitized_and_scoped(client, h):
    r = client.post(
        "/blocks",
        json={
            "name": "  Evil  ",
            "category": "text",
            "definition": {
                "html": '<p onclick="x()">Hi</p><script>alert(1)</script>',
                "css": "p { color: red } @import url(https://evil/x.css);",
            },
        },
        headers=h("designer"),
    )
    assert r.status_code == 201
    b = r.json
    assert b["kind"] == "custom" and b["name"] == "Evil" and b["current_version"] == 1
    cur = b["current"]
    assert cur["html_sanitized"] == "<p>Hi</p>"
    assert cur["scope_class"] == f"blk-{b['id'].replace('-', '')}-v1"
    assert f".{cur['scope_class']} p" in cur["css_sanitized"]
    assert "@import" not in cur["css_sanitized"]
    assert any("@import" in w for w in b["warnings"])


def test_create_computes_placeholders_and_loops_itself(client, h):
    r = client.post(
        "/blocks",
        json={"name": "T", "category": "table", "definition": {**TABLE, "placeholders": ["fake"]}},
        headers=h("designer"),
    )
    d = r.json["current"]["definition"]
    assert d["loops"] == ["rows"] and d["placeholders"] == ["rows"]
    assert d["loop_columns"] == ["lot"]
    assert "fake" not in d["placeholders"]


def test_loop_markers_survive_inside_the_stored_table(client, h):
    html = client.post(
        "/blocks", json={"name": "T", "category": "table", "definition": TABLE}, headers=h()
    ).json["current"]["html_sanitized"]
    assert "<tbody>{{#each rows}}<tr><td>{{lot}}</td></tr>{{/each}}</tbody>" in html


@pytest.mark.parametrize(
    "body, field",
    [
        ({"name": "", "category": "text", "definition": SIMPLE}, "name"),
        ({"name": "x", "category": "nope", "definition": SIMPLE}, "category"),
        ({"name": "x", "category": "text", "definition": {"html": ""}}, None),
        ({"name": "x", "category": "text"}, None),
        ({"name": "x" * 121, "category": "text", "definition": SIMPLE}, "name"),
    ],
)
def test_create_rejects_invalid_input(client, h, body, field):
    r = client.post("/blocks", json=body, headers=h())
    assert r.status_code == 400
    assert r.json["error"]["code"] == "VALIDATION_ERROR"
    if field:
        assert field in r.json["error"]["message"]


def test_create_rejects_template_syntax_errors_with_position(client, h):
    r = client.post(
        "/blocks",
        json={"name": "x", "category": "text", "definition": {"html": "<p>{{#each rows}}</p>"}},
        headers=h(),
    )
    assert r.status_code == 400
    detail = r.json["error"]["details"][0]
    assert detail["path"] == "html" and detail["line"] == 1


def test_create_warns_about_placeholder_in_style_attribute_unknown_prop_and_loop_without_source(
    client, h
):
    r = client.post(
        "/blocks",
        json={
            "name": "x",
            "category": "text",
            "definition": {
                "html": (
                    '<div style="height: {{props.h}}mm">{{props.ghost}}'
                    "{{#each rows}}a{{/each}}</div>"
                )
            },
        },
        headers=h(),
    )
    assert r.status_code == 201
    text = " | ".join(r.json["warnings"])
    assert "style attribute" in text
    assert "props.ghost" in text
    assert "datasource" in text


# --- read / list -----------------------------------------------------------
def test_get_block_and_unknown_id(client, h, make_block):
    b = make_block()
    r = client.get(f"/blocks/{b['id']}", headers=h("viewer"))
    assert r.status_code == 200 and r.json["current"]["version"] == 1
    r = client.get(f"/blocks/{uuid.uuid4()}", headers=h("viewer"))
    assert r.status_code == 404 and r.json["error"]["code"] == "NOT_FOUND"


def test_list_filters_search_and_pagination(client, h, make_block):
    a = make_block(name="Alpha table", category="table")
    b = make_block(name="Beta text", category="text", description="mentions gamma")
    assert a["id"] in ids(client.get("/blocks?kind=custom", headers=h("viewer")))
    r = client.get("/blocks?kind=custom&category=table", headers=h("viewer"))
    assert ids(r) == [a["id"]]
    assert ids(client.get("/blocks?q=GAMMA&kind=custom", headers=h("viewer"))) == [b["id"]]
    r = client.get("/blocks?page_size=3", headers=h("viewer"))
    assert len(r.json["items"]) == 3 and r.json["total"] >= 12
    assert client.get("/blocks?page=abc", headers=h("viewer")).status_code == 400


def test_list_items_are_light_summaries(client, h, make_block):
    make_block(TABLE, name="T", category="table")
    item = client.get("/blocks?kind=custom", headers=h("viewer")).json["items"][0]
    assert item["has_loop"] is True and item["default_size_mm"] == {"w": 180.0, "h": 20.0}
    assert "html_sanitized" not in item and "current" not in item


# --- update / versioning ---------------------------------------------------
def test_update_requires_if_match(client, h, make_block):
    b = make_block()
    r = client.put(f"/blocks/{b['id']}", json={"name": "x"}, headers=h())
    assert r.status_code == 428 and r.json["error"]["code"] == "PRECONDITION_REQUIRED"
    r = client.put(f"/blocks/{b['id']}", json={}, headers=h(**{"If-Match": "abc"}))
    assert r.status_code == 400


def test_new_content_makes_a_new_version_and_publishes_an_event(app, client, h, make_block):
    b = make_block()
    redis = app.extensions["redis"]
    redis.delete("rp.events")
    r = client.put(
        f"/blocks/{b['id']}",
        json={"definition": {**SIMPLE, "html": "<p>Changed {{props.name}}</p>"}},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 200 and r.json["current_version"] == 2
    assert r.json["current"]["html_sanitized"] == "<p>Changed {{props.name}}</p>"
    # version 1 is untouched, and has its own scope class
    v1 = client.get(f"/blocks/{b['id']}/versions/1", headers=h("viewer")).json
    assert v1["definition"]["html"] == SIMPLE["html"]
    assert v1["scope_class"].endswith("-v1")
    assert r.json["current"]["scope_class"].endswith("-v2")
    events = [json.loads(f["event"]) for _id, f in redis.xrange("rp.events")]
    assert [e["type"] for e in events] == ["block.updated"]
    assert events[0]["data"]["block_id"] == b["id"] and events[0]["data"]["version"] == 2


def test_saving_identical_content_does_not_bump_the_version(app, client, h, make_block):
    b = make_block()
    r = client.put(
        f"/blocks/{b['id']}", json={"definition": SIMPLE}, headers=h(**{"If-Match": "1"})
    )
    assert r.status_code == 200 and r.json["current_version"] == 1
    versions = client.get(f"/blocks/{b['id']}/versions", headers=h("viewer")).json["items"]
    assert len(versions) == 1


def test_metadata_only_changes_do_not_bump_the_version(client, h, make_block):
    b = make_block()
    r = client.put(
        f"/blocks/{b['id']}",
        json={"name": "Renamed", "category": "title" if False else "other", "description": "d"},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 200
    assert (r.json["name"], r.json["category"], r.json["current_version"]) == (
        "Renamed",
        "other",
        1,
    )


def test_stale_if_match_conflicts_and_returns_the_latest(client, h, make_block):
    b = make_block()
    client.put(
        f"/blocks/{b['id']}",
        json={"definition": {**SIMPLE, "html": "<p>v2</p>"}},
        headers=h(**{"If-Match": "1"}),
    )
    r = client.put(
        f"/blocks/{b['id']}",
        json={"definition": {**SIMPLE, "html": "<p>mine</p>"}},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 409 and r.json["error"]["code"] == "CONFLICT"
    assert r.json["error"]["details"][0]["current_version"] == 2


def test_update_with_invalid_definition_changes_nothing(client, h, make_block):
    b = make_block()
    r = client.put(
        f"/blocks/{b['id']}",
        json={"name": "Nope", "definition": {"html": "<p>{{#if x}}</p>"}},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 400
    again = client.get(f"/blocks/{b['id']}", headers=h("viewer")).json
    assert again["name"] == "My block" and again["current_version"] == 1


def test_version_history_lists_newest_first(client, h, make_block):
    b = make_block()
    for n in (1, 2):
        client.put(
            f"/blocks/{b['id']}",
            json={"definition": {**SIMPLE, "html": f"<p>v{n + 1}</p>"}},
            headers=h(**{"If-Match": str(n)}),
        )
    items = client.get(f"/blocks/{b['id']}/versions", headers=h("viewer")).json["items"]
    assert [i["version"] for i in items] == [3, 2, 1]
    assert [i["is_current"] for i in items] == [True, False, False]
    assert client.get(f"/blocks/{b['id']}/versions/9", headers=h("viewer")).status_code == 404


# --- default blocks --------------------------------------------------------
def test_default_blocks_are_read_only(client, h):
    default = client.get("/blocks?kind=default&page_size=1", headers=h("viewer")).json["items"][0]
    for call in (
        lambda: client.put(
            f"/blocks/{default['id']}", json={"name": "x"}, headers=h(**{"If-Match": "1"})
        ),
        lambda: client.post(f"/blocks/{default['id']}/archive", headers=h()),
    ):
        r = call()
        assert r.status_code == 403 and r.json["error"]["code"] == "DEFAULT_BLOCK_READ_ONLY"


def test_duplicate_a_default_block_into_an_editable_copy(client, h):
    default = next(
        b
        for b in client.get("/blocks?kind=default&page_size=50", headers=h("viewer")).json["items"]
        if b["slug"] == "results_table"
    )
    r = client.post(f"/blocks/{default['id']}/duplicate", json={}, headers=h())
    assert r.status_code == 201
    copy = r.json
    assert copy["kind"] == "custom" and copy["slug"] is None and copy["id"] != default["id"]
    assert copy["name"] == "Results table (copy)" and copy["current_version"] == 1
    assert copy["current"]["definition"]["loops"] == ["rows"]
    # ... and it can be edited
    r = client.put(
        f"/blocks/{copy['id']}",
        json={"definition": {**copy["current"]["definition"], "css": ".res td { color: blue }"}},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 200 and r.json["current_version"] == 2


def test_duplicate_with_a_custom_name(client, h, make_block):
    b = make_block()
    r = client.post(f"/blocks/{b['id']}/duplicate", json={"name": "Mine"}, headers=h())
    assert r.status_code == 201 and r.json["name"] == "Mine"


# --- archive ---------------------------------------------------------------
def test_archived_blocks_leave_the_palette_but_stay_readable(client, h, make_block):
    b = make_block()
    assert client.post(f"/blocks/{b['id']}/archive", headers=h()).status_code == 200
    assert b["id"] not in ids(client.get("/blocks?kind=custom", headers=h("viewer")))
    assert b["id"] in ids(client.get("/blocks?kind=custom&include_archived=1", headers=h("viewer")))
    assert client.get(f"/blocks/{b['id']}/versions/1", headers=h("viewer")).status_code == 200
    assert client.post(f"/blocks/{b['id']}/restore", headers=h()).status_code == 200
    assert b["id"] in ids(client.get("/blocks?kind=custom", headers=h("viewer")))


# --- preview ---------------------------------------------------------------
def test_preview_uses_defaults_props_and_context(client, h):
    r = client.post(
        "/blocks/preview",
        json={
            "definition": {
                "html": "<p>{{props.title}} for {{customer}}</p>",
                "props_schema": {"title": {"default": "Report"}},
            },
            "context": {"customer": "ACME"},
        },
        headers=h("operator"),
    )
    assert r.status_code == 200 and r.json["ok"] is True
    assert r.json["html"] == "<p>Report for ACME</p>" and r.json["unresolved"] == []
    r = client.post(
        "/blocks/preview",
        json={
            "definition": {"html": "<p>{{props.title}}</p>", "props_schema": {"title": {}}},
            "props": {"title": "Override"},
        },
        headers=h("operator"),
    )
    assert r.json["html"] == "<p>Override</p>"


def test_preview_expands_table_loops_with_sample_rows(client, h):
    r = client.post(
        "/blocks/preview",
        json={"definition": TABLE, "context": {"rows": [{"lot": "L1"}, {"lot": "L2"}]}},
        headers=h(),
    )
    assert r.json["html"].count("<tr><td>") == 2
    assert "<td>L1</td>" in r.json["html"] and "<td>L2</td>" in r.json["html"]
    assert r.json["loops"] == ["rows"]


def test_preview_reports_unresolved_keys_and_escapes_values(client, h):
    r = client.post(
        "/blocks/preview",
        json={
            "definition": {"html": "<p>{{missing}} {{name}}</p>"},
            "context": {"name": "<script>alert(1)</script>"},
        },
        headers=h(),
    )
    assert r.json["unresolved"] == ["missing"]
    assert "[[missing?]]" in r.json["html"]
    assert "<script>" not in r.json["html"] and "&lt;script&gt;" in r.json["html"]


def test_preview_document_is_sandboxable(client, h):
    r = client.post(
        "/blocks/preview", json={"definition": TABLE, "context": {"rows": []}}, headers=h()
    )
    doc = r.json["document"]
    assert "Content-Security-Policy" in doc and "default-src &#x27;none&#x27;" in doc
    assert 'class="blk blk-preview"' in doc
    assert ".blk-preview td" in r.json["css"] and "body" not in r.json["css"]


def test_preview_returns_errors_with_200_while_the_author_is_typing(client, h):
    for definition in ({"html": "<p>{{#each rows}}"}, {"html": ""}, {"nothing": 1}):
        r = client.post("/blocks/preview", json={"definition": definition}, headers=h())
        assert r.status_code == 200 and r.json["ok"] is False and r.json["errors"]


def test_preview_stores_nothing(client, h):
    before = client.get("/blocks?include_archived=1", headers=h("viewer")).json["total"]
    client.post("/blocks/preview", json={"definition": SIMPLE}, headers=h())
    assert client.get("/blocks?include_archived=1", headers=h("viewer")).json["total"] == before


# --- import / export -------------------------------------------------------
def test_export_then_import_round_trip(client, h, make_block):
    b = make_block(TABLE, name="Results / v2", category="table", description="d")
    r = client.get(f"/blocks/{b['id']}/export", headers=h("viewer"))
    assert r.status_code == 200
    assert 'filename="Results-v2.block.json"' in r.headers["Content-Disposition"]
    doc = r.json
    assert doc["format"] == "rp-block" and doc["format_version"] == 1
    assert not {"placeholders", "loops", "loop_columns"} & set(doc["definition"])
    r = client.post("/blocks/import", json=doc, headers=h())
    assert r.status_code == 201
    copy = r.json
    assert copy["id"] != b["id"] and copy["kind"] == "custom" and copy["name"] == "Results / v2"
    assert copy["current"]["html_sanitized"] == b["current"]["html_sanitized"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: {**d, "format": "other"},
        lambda d: {**d, "format_version": 99},
        lambda d: {k: v for k, v in d.items() if k != "definition"},
    ],
)
def test_import_rejects_bad_files(client, h, make_block, mutate):
    b = make_block()
    doc = client.get(f"/blocks/{b['id']}/export", headers=h("viewer")).json
    assert client.post("/blocks/import", json=mutate(doc), headers=h()).status_code == 400


def test_import_sanitizes_hostile_content(client, h):
    doc = {
        "format": "rp-block",
        "format_version": 1,
        "name": "Hostile",
        "category": "other",
        "definition": {"html": '<img src=x onerror="alert(1)"><script>1</script>', "css": "a{}"},
    }
    r = client.post("/blocks/import", json=doc, headers=h())
    assert r.status_code == 201
    assert "onerror" not in r.json["current"]["html_sanitized"]
    assert "script" not in r.json["current"]["html_sanitized"]


# --- access control --------------------------------------------------------
def test_service_token_is_required(client):
    assert client.get("/blocks").status_code == 401
    r = client.get("/blocks", headers={"X-Service-Token": "wrong", "X-User-Role": "admin"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "role, allowed", [("viewer", False), ("operator", False), ("designer", True)]
)
def test_only_designers_can_write(client, h, role, allowed):
    r = client.post(
        "/blocks", json={"name": "x", "category": "text", "definition": SIMPLE}, headers=h(role)
    )
    assert (r.status_code == 201) is allowed
    if not allowed:
        assert r.status_code == 403


def test_viewers_can_read_but_not_preview(client, h):
    assert client.get("/blocks", headers=h("viewer")).status_code == 200
    r = client.post("/blocks/preview", json={"definition": SIMPLE}, headers=h("viewer"))
    assert r.status_code == 403


def test_internal_service_calls_can_read_a_pinned_version(client, h, make_block):
    b = make_block()
    internal = h(role=None)
    r = client.get(f"/blocks/{b['id']}/versions/1", headers=internal)
    assert r.status_code == 200
    assert r.json["block_id"] == b["id"] and r.json["scope_class"].endswith("-v1")
    assert client.get("/blocks", headers=internal).status_code == 200
    # ... but cannot write without a user
    r = client.post(
        "/blocks", json={"name": "x", "category": "text", "definition": SIMPLE}, headers=internal
    )
    assert r.status_code == 401


def test_error_envelope_has_a_correlation_id(client, h):
    r = client.get(
        f"/blocks/{uuid.uuid4()}", headers=h("viewer", **{"X-Correlation-Id": "abc-123"})
    )
    assert r.json["error"]["correlation_id"] == "abc-123"
