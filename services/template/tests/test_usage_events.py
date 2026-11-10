import json

from app.consumer import GROUP, handle_block_updated
from conftest import inst, make_doc
from rp_shared.events import STREAM, build_event, consume_once, publish


def get(client, h, t):
    return client.get(f"/templates/{t['id']}", headers=h("viewer")).json


# ------------------------------------------------------------------ usage
def test_usage_lists_draft_and_published_pins_with_latest_versions(
    client, h, make_template, save, fb
):
    t = make_template(make_doc(body=[inst(fb.title, "a"), inst(fb.kv, "kv")]))
    client.post(f"/templates/{t['id']}/publish", headers=h())
    v2 = fb.bump(fb.title)
    t = get(client, h, t)
    save(t, make_doc(body=[inst(fb.title, "a", version=v2)]))
    items = client.get(f"/templates/{t['id']}/usage", headers=h("viewer")).json["items"]
    rows = {(i["source"], i["name"]): i for i in items}
    assert (
        rows[("draft", "Title")]["pinned_version"] == 2 and not rows[("draft", "Title")]["outdated"]
    )
    assert rows[("published", "Title")]["pinned_version"] == 1
    assert rows[("published", "Title")]["outdated"] is True
    assert ("draft", "Key/value") not in rows  # removed from the draft, still in published
    assert ("published", "Key/value") in rows


def test_usage_tracks_the_highest_pinned_version_per_block(client, h, make_template, fb):
    v2 = fb.bump(fb.title)
    t = make_template(
        make_doc(body=[inst(fb.title, "a", version=1), inst(fb.title, "b", version=v2)])
    )
    items = client.get(f"/templates/{t['id']}/usage", headers=h("viewer")).json["items"]
    assert [(i["source"], i["pinned_version"]) for i in items] == [("draft", 2)]


# ------------------------------------------------------------------ upgrade blocks
def test_upgrade_blocks_moves_pins_and_keeps_props(client, h, make_template, fb):
    v2 = fb.bump(fb.title)
    t = make_template(make_doc(body=[inst(fb.title, "a", text="Keep me"), inst(fb.kv, "kv")]))
    r = client.post(f"/templates/{t['id']}/upgrade-blocks", headers=h(**{"If-Match": "1"}), json={})
    assert r.status_code == 200
    assert r.json["changes"] == [
        {"instance_id": "a", "block_id": fb.title, "name": "Title", "from": 1, "to": v2}
    ]
    item = r.json["template"]["document"]["regions"]["body"]["items"][0]
    assert item["block_version"] == v2 and item["props"] == {"text": "Keep me"}
    assert r.json["template"]["draft_revision"] == 2
    assert r.json["validation"]["ok"]


def test_upgrade_blocks_can_target_blocks_and_skips_archived(client, h, make_template, fb):
    fb.bump(fb.title)
    fb.bump(fb.kv)
    fb.info[fb.kv]["is_archived"] = True
    t = make_template(make_doc(body=[inst(fb.title, "a"), inst(fb.kv, "kv")]))
    r = client.post(
        f"/templates/{t['id']}/upgrade-blocks",
        headers=h(**{"If-Match": "1"}),
        json={"block_ids": [fb.kv]},
    )
    assert r.json["changes"] == []  # archived blocks are never auto-upgraded
    r = client.post(f"/templates/{t['id']}/upgrade-blocks", headers=h(**{"If-Match": "1"}), json={})
    assert [c["instance_id"] for c in r.json["changes"]] == ["a"]


def test_upgrade_with_nothing_to_do_does_not_bump_the_revision(client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    r = client.post(f"/templates/{t['id']}/upgrade-blocks", headers=h(**{"If-Match": "1"}), json={})
    assert r.json["changes"] == [] and r.json["template"]["draft_revision"] == 1


def test_upgrade_needs_if_match_and_a_designer(client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    url = f"/templates/{t['id']}/upgrade-blocks"
    assert client.post(url, headers=h(), json={}).status_code == 428
    assert client.post(url, headers=h("operator", **{"If-Match": "1"})).status_code == 403
    assert client.post(url, headers=h(**{"If-Match": "9"}), json={}).status_code == 409


# ------------------------------------------------------------------ block.updated consumer
def test_block_update_flags_templates_pinning_an_older_version(app, client, h, make_template, fb):
    uses = make_template(make_doc(body=[inst(fb.title, "a")]), name="Uses title")
    other = make_template(make_doc(body=[inst(fb.kv, "kv")]), name="Other")
    v2 = fb.bump(fb.title)
    event = build_event("block.updated", {"block_id": fb.title, "version": v2, "name": "Title"})
    flagged = handle_block_updated(app.extensions["db_sessionmaker"], event)
    assert flagged == 1
    got = get(client, h, uses)
    assert got["outdated_blocks"] == 1
    assert got["outdated_block_list"] == [
        {"block_id": fb.title, "name": "Title", "pinned": 1, "latest": v2}
    ]
    assert get(client, h, other)["outdated_blocks"] == 0
    # idempotent: handling the same event again does not duplicate the entry
    handle_block_updated(app.extensions["db_sessionmaker"], event)
    assert get(client, h, uses)["outdated_blocks"] == 1


def test_flag_clears_when_the_draft_is_upgraded(app, client, h, make_template, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    v2 = fb.bump(fb.title)
    handle_block_updated(
        app.extensions["db_sessionmaker"],
        build_event("block.updated", {"block_id": fb.title, "version": v2, "name": "Title"}),
    )
    assert get(client, h, t)["outdated_blocks"] == 1
    client.post(f"/templates/{t['id']}/upgrade-blocks", headers=h(**{"If-Match": "1"}), json={})
    assert get(client, h, t)["outdated_blocks"] == 0


def test_flag_clears_when_the_block_is_removed_from_the_draft(
    app, client, h, make_template, save, fb
):
    t = make_template(make_doc(body=[inst(fb.title, "a"), inst(fb.kv, "kv")]))
    v2 = fb.bump(fb.title)
    handle_block_updated(
        app.extensions["db_sessionmaker"],
        build_event("block.updated", {"block_id": fb.title, "version": v2, "name": "Title"}),
    )
    save(get(client, h, t), make_doc(body=[inst(fb.kv, "kv")]))
    assert get(client, h, t)["outdated_blocks"] == 0


def test_published_only_pins_are_not_flagged(app, client, h, make_template, save, fb):
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    client.post(f"/templates/{t['id']}/publish", headers=h())
    save(get(client, h, t), make_doc(body=[inst(fb.kv, "kv")]))  # draft no longer uses Title
    v2 = fb.bump(fb.title)
    n = handle_block_updated(
        app.extensions["db_sessionmaker"],
        build_event("block.updated", {"block_id": fb.title, "version": v2, "name": "Title"}),
    )
    assert n == 0


def test_consumer_reads_block_updated_from_the_stream(app, client, h, make_template, fb):
    redis = app.extensions["redis"]
    consume_once(redis, GROUP, "t-1", {}, block_ms=1)  # create the group at the stream's end
    t = make_template(make_doc(body=[inst(fb.title, "a")]))
    v2 = fb.bump(fb.title)
    publish(redis, "block.updated", {"block_id": fb.title, "version": v2, "name": "Title"})
    publish(redis, "something.else", {"x": 1})  # ignored, but acked
    factory = app.extensions["db_sessionmaker"]
    handled = consume_once(
        redis,
        GROUP,
        "t-1",
        {"block.updated": lambda e: handle_block_updated(factory, e)},
        block_ms=1,
    )
    assert handled == 1
    assert get(client, h, t)["outdated_blocks"] == 1
    # everything was acked
    assert redis.xpending(STREAM, GROUP)["pending"] == 0
    assert json.loads(next(iter(redis.xrange(STREAM)))[1]["event"])["type"] == "block.updated"
