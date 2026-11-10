import pytest
from flask import Flask, g

from rp_shared.auth import require_min_role_or_internal
from rp_shared.errors import Forbidden, ValidationFailed
from rp_shared.placeholders import validate
from rp_shared.sanitizer import sanitize_html, sanitize_template_html
from rp_shared.schemas.block import parse_block_definition

TABLE = (
    "<table><thead><tr><th>Lot</th></tr></thead>"
    "<tbody>{{#each rows}}<tr><td>{{lot}}</td></tr>{{/each}}</tbody></table>"
)


def test_plain_sanitizer_would_break_a_table_loop():
    # Documents why sanitize_template_html exists.
    assert sanitize_html(TABLE) != TABLE


def test_template_sanitizer_keeps_loops_inside_tables():
    assert sanitize_template_html(TABLE) == TABLE


def test_template_sanitizer_keeps_conditionals_inside_lists_and_attributes():
    html = '<ul>{{#if a}}<li>x</li>{{else}}<li>y</li>{{/if}}</ul><p title="{{#if b}}t{{/if}}">z</p>'
    assert sanitize_template_html(html) == html


def test_template_sanitizer_result_still_parses_as_a_template():
    assert validate(sanitize_template_html(TABLE)) == []


def test_template_sanitizer_removes_scripts_handlers_comments_and_forged_markers():
    out = sanitize_template_html(
        '<p onclick="x()">a</p><script>alert(1)</script><!-- note --><!--rp:7-->'
        '<iframe src="x"></iframe>'
    )
    assert out == "<p>a</p>"


def test_inline_style_is_filtered_property_by_property():
    out = sanitize_html(
        '<p style="color:red; position:fixed; top:0; '
        'background-image:url(http://x/y); width:50%">a</p>'
    )
    assert "color:red" in out.replace(" ", "")
    assert "width:50%" in out.replace(" ", "")
    for bad in ("position", "top", "url", "background-image"):
        assert bad not in out


def test_placeholders_survive_in_attributes():
    assert sanitize_template_html('<img src="{{props.logo}}" alt="">') == (
        '<img src="{{props.logo}}" alt="">'
    )


def test_definition_defaults():
    d = parse_block_definition({"html": "<p>x</p>"})
    assert d.repeatable_region == "body" and d.resizable and d.default_size_mm.w == 180


def test_definition_ignores_computed_fields_so_exports_round_trip():
    d = parse_block_definition({"html": "<p>x</p>", "placeholders": ["a"], "loops": []})
    assert not hasattr(d, "placeholders")


@pytest.mark.parametrize(
    "raw, path",
    [
        ({"html": ""}, "html"),
        ({"html": "x", "default_size_mm": {"w": 500, "h": 10}}, "default_size_mm.w"),
        ({"html": "x", "repeatable_region": "middle"}, "repeatable_region"),
        ({"html": "x", "props_schema": {"Bad Name": {"type": "string"}}}, "props_schema"),
        ({"html": "x", "props_schema": {"a": {"type": "select"}}}, "props_schema.a"),
        (
            {"html": "x", "props_schema": {"a": {"type": "number", "default": "x"}}},
            "props_schema.a",
        ),
        (
            {"html": "x", "props_schema": {"a": {"type": "string", "oops": 1}}},
            "props_schema.a.oops",
        ),
    ],
)
def test_definition_rejects_bad_input_with_a_path(raw, path):
    with pytest.raises(ValidationFailed) as exc:
        parse_block_definition(raw)
    assert any(d["path"].startswith(path) for d in exc.value.details), exc.value.details


def _guarded(minimum, role):
    app = Flask(__name__)

    @require_min_role_or_internal(minimum)
    def view():
        return "ok"

    with app.test_request_context():
        g.user_role = role
        return view()


def test_internal_callers_without_a_role_are_allowed():
    assert _guarded("viewer", None) == "ok"


def test_users_still_need_the_minimum_role():
    assert _guarded("operator", "operator") == "ok"
    assert _guarded("operator", "designer") == "ok"
    with pytest.raises(Forbidden):
        _guarded("designer", "viewer")
