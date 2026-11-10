import json

from conftest import TOKEN, inst, make_doc
from rp_shared.events import STREAM


def events_of(app, type_):
    r = app.extensions["redis"]
    out = [json.loads(f["event"]) for _id, f in r.xrange(STREAM)]
    return [e for e in out if e["type"] == type_]


# ------------------------------------------------------------------ create / read
def test_create_returns_default_document_and_draft_state(client, h):
    r = client.post("/templates", json={"name": " Lab report ", "description": "d"}, headers=h())
    assert r.status_code == 201
    t = r.json
    assert t["name"] == "Lab report" and t["status"] == "draft"
    assert t["draft_revision"] == 1 and t["published_version"] is None
    assert t["has_unpublished_changes"] is True
    assert t["document"]["page"]["size"] == "A4"
    assert t["document"]["regions"]["header"]["height_mm"] == 25


def test_create_with_a_document_and_get_it_back(client, h, make_template, fb):
    doc = make_doc(body=[inst(fb.title, "t1", text="Hello")])
    t = make_template(doc)
    got = client.get(f"/templates/{t['id']}", headers=h("viewer")).json
    assert got["document"]["regions"]["body"]["items"][0]["props"] == {"text": "Hello"}


def test_create_validates_name_and_document(client, h, fb):
    assert client.post("/templates", json={"name": ""}, headers=h()).status_code == 400
    assert client.post("/templates", json={"name": "x" * 121}, headers=h()).status_code == 400
    bad = make_doc(body=[inst(fb.title, "same"), inst(fb.title, "same")])
    r = client.post("/templates", json={"name": "x", "document": bad}, headers=h())
    assert r.status_code == 400 and r.json["error"]["code"] == "VALIDATION_ERROR"
    assert "used twice" in r.json["error"]["details"][0]["message"]
    r = client.post("/templates", json={"name": "x", "document": {"surprise": 1}}, headers=h())
    assert r.json["error"]["details"][0]["path"] == "surprise"
    assert client.post("/templates", data="nope", headers=h()).status_code == 400


def test_unknown_template_is_404(client, h):
    r = client.get("/templates/11111111-1111-1111-1111-111111111111", headers=h("viewer"))
    assert r.status_code == 404 and r.json["error"]["code"] == "NOT_FOUND"


# ------------------------------------------------------------------ access control
def test_roles_and_service_token(client, h, make_template):
    t = make_template()
    # no service token -> 401, whatever the role header says
    assert client.get("/templates", headers={"X-User-Role": "admin"}).status_code == 401
    # no user at all on a browser-facing route -> 401
    assert client.get("/templates", headers=h(None)).status_code == 401
    # viewers read but cannot write
    assert client.get("/templates", headers=h("viewer")).status_code == 200
    assert client.post("/templates", json={"name": "x"}, headers=h("viewer")).status_code == 403
    assert client.post("/templates", json={"name": "x"}, headers=h("operator")).status_code == 403
    assert (
        client.patch(f"/templates/{t['id']}", json={"name": "y"}, headers=h("operator")).status_code
        == 403
    )
    assert client.post(f"/templates/{t['id']}/publish", headers=h("operator")).status_code == 403
    assert client.post(f"/templates/{t['id']}/archive", headers=h("operator")).status_code == 403
    # operators may validate and render (nothing is stored)
    assert client.post(f"/templates/{t['id']}/validate", headers=h("operator")).status_code == 200
    assert client.post(f"/templates/{t['id']}/render", headers=h("operator")).status_code == 200
    assert client.post(f"/templates/{t['id']}/validate", headers=h("viewer")).status_code == 403
    assert client.post("/templates", json={"name": "x"}, headers=h("admin")).status_code == 201


def test_internal_call_can_read_a_published_version_but_nothing_else(client, h, make_template):
    t = make_template()
    client.post(f"/templates/{t['id']}/publish", headers=h())
    assert client.get(f"/templates/{t['id']}/versions/1", headers=h(None)).status_code == 200
    assert client.get(f"/templates/{t['id']}", headers=h(None)).status_code == 401
    assert client.get(f"/templates/{t['id']}/versions/1", headers={}).status_code == 401
    bad = {"X-Service-Token": "wrong"}
    assert client.get(f"/templates/{t['id']}/versions/1", headers=bad).status_code == 401
    assert TOKEN != "wrong"


# ------------------------------------------------------------------ list
def test_list_filters_search_and_hides_archived(client, h, make_template):
    a = make_template(name="Fish report", description="seafood")
    b = make_template(name="Yeast certificate")
    client.post(f"/templates/{b['id']}/archive", headers=h())
    names = lambda r: sorted(i["name"] for i in r.json["items"])  # noqa: E731
    assert names(client.get("/templates", headers=h("viewer"))) == ["Fish report"]
    assert names(client.get("/templates?status=all", headers=h("viewer"))) == [
        "Fish report",
        "Yeast certificate",
    ]
    assert names(client.get("/templates?status=archived", headers=h("viewer"))) == [
        "Yeast certificate"
    ]
    assert names(client.get("/templates?q=SEAFOOD", headers=h("viewer"))) == ["Fish report"]
    assert names(client.get("/templates?q=%25", headers=h("viewer"))) == []  # '%' is literal
    page = client.get("/templates?page=1&page_size=1&status=all", headers=h("viewer")).json
    assert page["total"] == 2 and len(page["items"]) == 1 and a["id"]


def test_list_rejects_bad_paging(client, h):
    assert client.get("/templates?page=abc", headers=h("viewer")).status_code == 400


# ------------------------------------------------------------------ draft autosave
def test_save_requires_if_match(client, h, make_template):
    t = make_template()
    r = client.put(f"/templates/{t['id']}/draft", json={"document": t["document"]}, headers=h())
    assert r.status_code == 428 and r.json["error"]["code"] == "PRECONDITION_REQUIRED"
    r = client.put(
        f"/templates/{t['id']}/draft",
        json={"document": t["document"]},
        headers=h(**{"If-Match": "abc"}),
    )
    assert r.status_code == 400


def test_save_bumps_revision_only_when_the_document_changes(save, make_template, fb):
    t = make_template()
    same = save(t, t["document"])
    assert same["draft_revision"] == 1
    changed = save(same, make_doc(body=[inst(fb.title, "a")]))
    assert changed["draft_revision"] == 2
    again = save(changed, changed["document"])
    assert again["draft_revision"] == 2


def test_rename_does_not_bump_the_revision(save, make_template):
    t = make_template()
    r = save(t, t["document"], name="New name", description="  hi ")
    assert (r["name"], r["description"], r["draft_revision"]) == ("New name", "hi", 1)


def test_stale_save_is_a_conflict_that_returns_the_latest(client, h, save, make_template, fb):
    t = make_template()
    save(t, make_doc(body=[inst(fb.title, "a")]))  # someone else saved revision 2
    r = client.put(
        f"/templates/{t['id']}/draft",
        json={"document": make_doc(body=[inst(fb.title, "b")])},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 409 and r.json["error"]["code"] == "CONFLICT"
    latest = r.json["error"]["details"][0]
    assert latest["draft_revision"] == 2
    assert latest["document"]["regions"]["body"]["items"][0]["instance_id"] == "a"


def test_invalid_document_is_not_saved(client, h, make_template, fb):
    t = make_template()
    bad = make_doc(body=[inst(fb.title, "bad id")])
    r = client.put(
        f"/templates/{t['id']}/draft", json={"document": bad}, headers=h(**{"If-Match": "1"})
    )
    assert r.status_code == 400
    assert client.get(f"/templates/{t['id']}", headers=h("viewer")).json["draft_revision"] == 1
    r = client.put(f"/templates/{t['id']}/draft", json={}, headers=h(**{"If-Match": "1"}))
    assert r.status_code == 400


def test_patch_metadata(client, h, make_template):
    t = make_template()
    r = client.patch(f"/templates/{t['id']}", json={"name": "Renamed"}, headers=h())
    assert r.status_code == 200 and r.json["name"] == "Renamed"
    assert client.patch(f"/templates/{t['id']}", json={"name": ""}, headers=h()).status_code == 400


# ------------------------------------------------------------------ duplicate / archive / restore
def test_duplicate_copies_the_draft_but_not_the_publication(client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    client.post(f"/templates/{t['id']}/publish", headers=h())
    r = client.post(f"/templates/{t['id']}/duplicate", json={}, headers=h())
    assert r.status_code == 201
    copy = r.json
    assert copy["id"] != t["id"] and copy["name"] == "Lab report (copy)"
    assert copy["status"] == "draft" and copy["published_version"] is None
    assert copy["document"] == t["document"]
    named = client.post(f"/templates/{t['id']}/duplicate", json={"name": "Mine"}, headers=h())
    assert named.json["name"] == "Mine"


def test_archive_restore_and_editing_an_archived_template(client, h, make_template, save):
    t = make_template()
    assert client.post(f"/templates/{t['id']}/archive", headers=h()).json["status"] == "archived"
    r = client.put(
        f"/templates/{t['id']}/draft",
        json={"document": t["document"]},
        headers=h(**{"If-Match": "1"}),
    )
    assert r.status_code == 409 and r.json["error"]["code"] == "TEMPLATE_ARCHIVED"
    r = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert r.status_code == 409 and r.json["error"]["code"] == "TEMPLATE_ARCHIVED"
    assert client.post(f"/templates/{t['id']}/restore", headers=h()).json["status"] == "draft"
    client.post(f"/templates/{t['id']}/publish", headers=h())
    client.post(f"/templates/{t['id']}/archive", headers=h())
    assert client.post(f"/templates/{t['id']}/restore", headers=h()).json["status"] == "published"


# ------------------------------------------------------------------ publish
def test_publish_snapshots_the_draft_and_emits_an_event(app, client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a", text="v1")]))
    r = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert r.status_code == 200
    assert r.json["status"] == "published" and r.json["published_version"] == 1
    assert r.json["has_unpublished_changes"] is False and r.json["unchanged"] is False
    evs = events_of(app, "template.published")
    assert len(evs) == 1
    assert evs[0]["data"] == {"template_id": t["id"], "version": 1, "name": "Lab report"}

    v = client.get(f"/templates/{t['id']}/versions/1", headers=h("viewer")).json
    assert v["version"] == 1 and v["document"] == t["document"]


def test_publishing_twice_without_changes_is_a_noop(app, client, h, make_template):
    t = make_template()
    client.post(f"/templates/{t['id']}/publish", headers=h())
    again = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert again.status_code == 200 and again.json["unchanged"] is True
    assert again.json["published_version"] == 1
    assert len(events_of(app, "template.published")) == 1


def test_published_versions_are_immutable_snapshots(client, h, make_template, save, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a", text="one")]))
    client.post(f"/templates/{t['id']}/publish", headers=h())
    t = client.get(f"/templates/{t['id']}", headers=h("viewer")).json
    t = save(t, make_doc(body=[inst(fb.title, "a", text="two")]))
    assert t["has_unpublished_changes"] is True and t["published_version"] == 1
    r = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert r.json["published_version"] == 2

    v1 = client.get(f"/templates/{t['id']}/versions/1", headers=h("viewer")).json
    v2 = client.get(f"/templates/{t['id']}/versions/2", headers=h("viewer")).json
    assert v1["document"]["regions"]["body"]["items"][0]["props"]["text"] == "one"
    assert v2["document"]["regions"]["body"]["items"][0]["props"]["text"] == "two"
    items = client.get(f"/templates/{t['id']}/versions", headers=h("viewer")).json["items"]
    assert [(i["version"], i["is_current"]) for i in items] == [(2, True), (1, False)]
    assert client.get(f"/templates/{t['id']}/versions/3", headers=h("viewer")).status_code == 404


def test_publish_is_blocked_by_validation_errors(app, client, h, make_template, fb):
    # the image block requires 'src'
    t = make_template(make_doc(body=[inst(fb.logo, "img")]))
    r = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert r.status_code == 422 and r.json["error"]["code"] == "TEMPLATE_INVALID"
    codes = {d["code"] for d in r.json["error"]["details"]}
    assert "PROP_REQUIRED" in codes
    got = client.get(f"/templates/{t['id']}", headers=h("viewer")).json
    assert got["published_version"] is None and got["status"] == "draft"
    assert events_of(app, "template.published") == []


def test_publish_succeeds_with_only_warnings(client, h, make_template, catalog):
    catalog.catalog = None  # data service down -> warning, not an error
    t = make_template()
    r = client.post(f"/templates/{t['id']}/publish", headers=h())
    assert r.status_code == 200
    assert {i["code"] for i in r.json["validation"]["issues"]} >= {"CATALOG_UNAVAILABLE"}


def test_unknown_version_in_render_is_404(client, h, make_template):
    t = make_template()
    r = client.post(f"/templates/{t['id']}/render", json={"version": 9}, headers=h("operator"))
    assert r.status_code == 404
    r = client.post(f"/templates/{t['id']}/render", json={"version": "x"}, headers=h("operator"))
    assert r.status_code == 400
