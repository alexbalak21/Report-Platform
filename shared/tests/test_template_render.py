import copy
import uuid

import pytest

from rp_shared.errors import ValidationFailed
from rp_shared.schemas.template_document import (
    TemplateDocument,
    empty_document,
    parse_template_document,
)
from rp_shared.template_render import (
    PAGE_TOKEN,
    PAGES_TOKEN,
    BlockSnapshot,
    datasource_table,
    has_page_tokens,
    pin_key,
    prop_values,
    render_document,
)

HDR, TBL, FTR, LOGO = (str(uuid.uuid4()) for _ in range(4))


def inst(block_id, iid, version=1, **props):
    return {"instance_id": iid, "block_id": block_id, "block_version": version, "props": props}


def snap(block_id, html, *, version=1, scope=None, **definition):
    return BlockSnapshot(
        block_id=block_id,
        version=version,
        name="b",
        html=html,
        css=f".{scope or 'blk-x'} p{{margin:0}}",
        scope_class=scope or f"blk-{block_id[:4]}",
        definition=definition,
    )


def pins(*snaps):
    return {pin_key(s.block_id, s.version): s for s in snaps}


@pytest.fixture
def blocks():
    return pins(
        snap(
            HDR,
            "<p>{{props.title}} - {{report_number}} - {{customer}}</p>",
            props_schema={"title": {"type": "string", "default": "Default title"}},
            placeholders=["customer"],
        ),
        snap(
            TBL,
            "<table>{{#each rows}}<tr><td>{{lot}}</td></tr>{{/each}}</table>",
            props_schema={"source": {"type": "datasource"}},
            loops=["rows"],
            loop_columns=["lot"],
        ),
        snap(FTR, "<p>Page {{page}} / {{pages}}</p>"),
        snap(LOGO, '<img src="{{props.src}}" alt="">', props_schema={"src": {"type": "string"}}),
    )


def doc_with(**parts) -> TemplateDocument:
    raw = copy.deepcopy(empty_document())
    raw["regions"]["header"]["items"] = parts.get("header", [])
    raw["regions"]["body"]["items"] = parts.get("body", [])
    raw["regions"]["footer"]["items"] = parts.get("footer", [])
    raw["overlays"] = parts.get("overlays", [])
    return parse_template_document(raw)


# ---------------------------------------------------------------- document schema
def test_empty_document_is_valid_and_has_default_regions():
    doc = parse_template_document(empty_document())
    assert doc.regions.header.height_mm == 25 and doc.regions.footer.height_mm == 15
    assert doc.page.width_mm == 210 and doc.page.height_mm == 297
    assert doc.instances() == []


def test_landscape_swaps_dimensions():
    raw = empty_document()
    raw["page"]["orientation"] = "landscape"
    page = parse_template_document(raw).page
    assert (page.width_mm, page.height_mm) == (297, 210)


def test_duplicate_instance_ids_are_rejected_across_regions():
    raw = empty_document()
    raw["regions"]["body"]["items"] = [inst(HDR, "a")]
    raw["regions"]["footer"]["items"] = [inst(FTR, "a")]
    with pytest.raises(ValidationFailed) as exc:
        parse_template_document(raw)
    assert "used twice" in exc.value.details[0]["message"]


@pytest.mark.parametrize("bad", ["", "has space", "x" * 65, "a/b"])
def test_bad_instance_ids(bad):
    raw = empty_document()
    raw["regions"]["body"]["items"] = [inst(HDR, bad)]
    with pytest.raises(ValidationFailed):
        parse_template_document(raw)


def test_unknown_keys_and_bad_values_are_reported_with_a_path():
    raw = empty_document()
    raw["page"]["margins_mm"]["t"] = 999
    raw["surprise"] = 1
    with pytest.raises(ValidationFailed) as exc:
        parse_template_document(raw)
    paths = {d["path"] for d in exc.value.details}
    assert "page.margins_mm.t" in paths and "surprise" in paths


def test_overlay_needs_geometry_and_pins_report_highest_version():
    raw = empty_document()
    raw["overlays"] = [{**inst(LOGO, "o1", 2), "x_mm": 10, "y_mm": 10, "w_mm": 30, "h_mm": 20}]
    raw["regions"]["body"]["items"] = [inst(LOGO, "b1", 3)]
    doc = parse_template_document(raw)
    assert doc.block_pins() == {uuid.UUID(LOGO): 3}
    raw["overlays"] = [inst(LOGO, "o2")]
    with pytest.raises(ValidationFailed):
        parse_template_document(raw)


def test_block_version_must_be_positive():
    raw = empty_document()
    raw["regions"]["body"]["items"] = [inst(HDR, "a", 0)]
    with pytest.raises(ValidationFailed):
        parse_template_document(raw)


# ---------------------------------------------------------------- props helpers
def test_prop_values_fall_back_to_defaults_but_keep_deliberate_blanks(blocks):
    s = blocks[pin_key(HDR, 1)]
    i = parse_template_document(doc_with(body=[inst(HDR, "a")]).model_dump()).instances()[0]
    assert prop_values(s, i) == {"title": "Default title"}
    blank = doc_with(body=[inst(HDR, "a", title="")]).instances()[0]
    assert prop_values(s, blank) == {"title": ""}
    unset = doc_with(body=[inst(HDR, "a", title=None)]).instances()[0]
    assert prop_values(s, unset) == {"title": "Default title"}


def test_datasource_table_reads_the_datasource_prop(blocks):
    s = blocks[pin_key(TBL, 1)]
    assert datasource_table(s, {"source": "samples"}) == "samples"
    assert datasource_table(s, {}) is None


# ---------------------------------------------------------------- rendering: sample mode
def test_sample_mode_shows_keywords_literally_and_one_example_row(blocks):
    doc = doc_with(header=[inst(HDR, "h")], body=[inst(TBL, "t", source="samples")])
    out = render_document(doc, blocks)
    assert "Default title - {{report_number}} - {{customer}}" in out.html
    assert "<td>{{lot}}</td>" in out.html
    assert out.ok


def test_sample_mode_nested_keys():
    b = snap(HDR, "<p>{{sample.lot}}</p>", placeholders=["sample.lot"])
    out = render_document(doc_with(body=[inst(HDR, "a")]), pins(b))
    assert "{{sample.lot}}" in out.html and out.ok


# ---------------------------------------------------------------- rendering: data mode
def test_data_mode_fills_columns_props_and_loops(blocks):
    doc = doc_with(header=[inst(HDR, "h", title="My report")], body=[inst(TBL, "t", source="s")])
    out = render_document(
        doc,
        blocks,
        context={"customer": "ACME"},
        tables={"s": [{"lot": "L1"}, {"lot": "L2"}]},
        report_number="261109-1",
    )
    assert "My report - 261109-1 - ACME" in out.html
    assert "<td>L1</td></tr><tr><td>L2</td>" in out.html
    assert out.ok


def test_data_mode_reports_unresolved_keys_once_and_marks_them(blocks):
    doc = doc_with(header=[inst(HDR, "h")], body=[inst(TBL, "t", source="nope")])
    out = render_document(doc, blocks, context={}, tables={"s": []}, report_number="1")
    assert out.unresolved == ["customer", "rows"]
    assert "[[customer?]]" in out.html and not out.ok


def test_blank_policy_hides_markers(blocks):
    out = render_document(
        doc_with(header=[inst(HDR, "h")]), blocks, context={}, report_number="1", missing="blank"
    )
    assert "[[" not in out.html and out.unresolved == ["customer"]


def test_values_are_escaped(blocks):
    out = render_document(
        doc_with(header=[inst(HDR, "h")]),
        blocks,
        context={"customer": "<script>alert(1)</script>"},
        report_number="1",
    )
    assert "<script>" not in out.html and "&lt;script&gt;" in out.html


def test_hydrated_props_are_sanitized_again(blocks):
    doc = doc_with(body=[inst(LOGO, "l", src="javascript:alert(1)")])
    out = render_document(doc, blocks, context={})
    assert "javascript:" not in out.html


# ---------------------------------------------------------------- rendering: layout
def test_regions_use_repeating_thead_tfoot_and_instances_are_tagged(blocks):
    doc = doc_with(header=[inst(HDR, "h")], body=[inst(TBL, "t")], footer=[inst(FTR, "f")])
    doc.regions.body.items[0].locked = True
    html = render_document(doc, blocks).html
    assert html.index("<thead>") < html.index("<tbody>") < html.index("<tfoot>")
    assert 'data-instance="t" data-locked="true"' in html
    assert 'data-instance="h" data-locked="false"' in html
    assert "height:25mm" in html and "height:15mm" in html


def test_empty_header_and_footer_take_no_height(blocks):
    html = render_document(doc_with(body=[inst(TBL, "t")]), blocks).html
    assert "height:25mm" not in html


def test_css_is_included_once_per_block_version(blocks):
    doc = doc_with(body=[inst(HDR, "a"), inst(HDR, "b")])
    html = render_document(doc, blocks).html
    assert html.count("margin:0}") == 1


def test_overlay_position_preview_vs_print(blocks):
    ov = {**inst(LOGO, "o", src="https://x/y.png"), "x_mm": 100, "y_mm": 50, "w_mm": 30}
    ov |= {"h_mm": 20, "z": 4}
    doc = doc_with(overlays=[ov])
    preview = render_document(doc, blocks, context={}, mode="preview").html
    assert "left:100mm;top:50mm;width:30mm;height:20mm;z-index:4" in preview
    printed = render_document(doc, blocks, context={}, mode="print").html
    # default margins are 15mm left / 20mm top: paper coordinates minus the @page margin
    assert "left:85mm;top:30mm" in printed
    assert "@page{size:A4 portrait;margin:20mm 15mm 20mm 15mm}" in printed
    assert "box-shadow" not in printed and "box-shadow" in preview


def test_overlays_render_in_z_order(blocks):
    base = {"x_mm": 0, "y_mm": 0, "w_mm": 10, "h_mm": 10}
    doc = doc_with(
        overlays=[
            {**inst(LOGO, "top", src="a"), **base, "z": 5},
            {**inst(LOGO, "bottom", src="b"), **base, "z": 1},
        ]
    )
    html = render_document(doc, blocks, context={}).html
    assert html.index('data-instance="bottom"') < html.index('data-instance="top"')


def test_landscape_page_css(blocks):
    raw = empty_document()
    raw["page"]["orientation"] = "landscape"
    html = render_document(parse_template_document(raw), blocks).html
    assert "size:A4 landscape" in html and "width:297mm" in html


# ---------------------------------------------------------------- page numbers, missing blocks
def test_page_numbers_preview_print_and_disabled(blocks):
    doc = doc_with(footer=[inst(FTR, "f")])
    assert "Page 1 / 1" in render_document(doc, blocks, mode="preview").html
    printed = render_document(doc, blocks, mode="print").html
    assert f"Page {PAGE_TOKEN} / {PAGES_TOKEN}" in printed and has_page_tokens(printed)
    doc.settings.page_numbering = False
    assert "Page  / " in render_document(doc, blocks, mode="print").html


def test_missing_block_is_reported_not_fatal(blocks):
    other = str(uuid.uuid4())
    doc = doc_with(body=[inst(other, "ghost"), inst(HDR, "ok")])
    out = render_document(doc, blocks)
    assert out.missing_blocks == [{"instance_id": "ghost", "block_id": other, "block_version": 1}]
    assert "Block unavailable" in out.html and not out.ok


def test_pinned_version_is_what_renders(blocks):
    v2 = snap(HDR, "<p>NEW {{props.title}}</p>", version=2, scope="blk-v2")
    both = {**blocks, **pins(v2)}
    old = render_document(doc_with(body=[inst(HDR, "a", 1)]), both).html
    new = render_document(doc_with(body=[inst(HDR, "a", 2)]), both).html
    assert "NEW" not in old and "NEW" in new


def test_block_snapshot_from_api_body():
    s = BlockSnapshot.from_api(
        {
            "block_id": HDR,
            "version": 3,
            "name": "N",
            "html_sanitized": "<p>x</p>",
            "css_sanitized": ".a{}",
            "scope_class": "blk-a",
            "definition": {"loops": []},
        }
    )
    assert (s.version, s.html, s.scope_class) == (3, "<p>x</p>", "blk-a")
