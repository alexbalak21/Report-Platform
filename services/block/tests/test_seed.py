import uuid

from app.defaults import DEFAULTS, NAMESPACE, default_id
from app.seed import seed_defaults
from app.service import build
from rp_shared.schemas.block import parse_block_definition


def test_all_default_blocks_are_seeded_with_stable_ids(client, h):
    r = client.get("/blocks?kind=default&page_size=200", headers=h("viewer"))
    assert r.status_code == 200
    assert r.json["total"] == len(DEFAULTS)
    by_slug = {b["slug"]: b for b in r.json["items"]}
    for spec in DEFAULTS:
        assert by_slug[spec["slug"]]["id"] == str(uuid.uuid5(NAMESPACE, spec["slug"]))
        assert by_slug[spec["slug"]]["id"] == str(default_id(spec["slug"]))
        assert by_slug[spec["slug"]]["current_version"] == 1


def test_seeding_twice_changes_nothing(app):
    assert seed_defaults(app.extensions["db_sessionmaker"]) == 0


def test_changed_default_definition_adds_a_new_version(app, client, h, monkeypatch):
    import app.seed as seed_module

    edited = [dict(d) for d in DEFAULTS]
    edited[0] = {**edited[0], "definition": {**edited[0]["definition"], "css": ".x { color: red }"}}
    monkeypatch.setattr(seed_module, "DEFAULTS", edited)
    assert seed_defaults(app.extensions["db_sessionmaker"]) == 1
    block = client.get(f"/blocks/{default_id(edited[0]['slug'])}", headers=h("viewer")).json
    assert block["current_version"] == 2
    # the old version is still there for templates pinned to it
    old = client.get(f"/blocks/{block['id']}/versions/1", headers=h("viewer"))
    assert old.status_code == 200


def test_every_default_block_is_clean():
    for spec in DEFAULTS:
        built = build(parse_block_definition(spec["definition"]), "blk-test")
        assert built.warnings == [], (spec["slug"], built.warnings)


def test_default_results_table_keeps_its_loop_inside_the_table():
    spec = next(d for d in DEFAULTS if d["slug"] == "results_table")
    built = build(parse_block_definition(spec["definition"]), "blk-test")
    assert "<tbody>{{#each rows}}<tr>" in built.html
    assert "{{/each}}</tbody>" in built.html
    assert built.loops == ["rows"]
    assert built.loop_columns == ["imp_pct", "lot", "sample_number", "species_name"]
