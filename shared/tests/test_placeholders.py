from datetime import date

import pytest

from rp_shared.placeholders import (
    TemplateSyntaxError,
    extract_keys,
    parse,
    render,
    validate,
)


def r(text, ctx, **kw):
    return render(text, ctx, **kw)


# --- basics ---------------------------------------------------------------
def test_simple_value():
    assert r("Client: {{ client }}", {"client": "ACME"}).text == "Client: ACME"


def test_whitespace_inside_braces_is_ignored():
    assert r("{{client}}|{{  client  }}", {"client": "x"}).text == "x|x"


def test_html_is_escaped_by_default_and_raw_opts_out():
    ctx = {"v": "<b>&"}
    assert r("{{v}}", ctx).text == "&lt;b&gt;&amp;"
    assert r("{{v | raw}}", ctx).text == "<b>&"


def test_dotted_keys():
    assert r("{{sample.lot}}", {"sample": {"lot": "L1"}}).text == "L1"


# --- regression tests from the desktop app --------------------------------
def test_crlf_input_is_normalised():
    res = r("a\r\n{{x}}\r\nb", {"x": "1"})
    assert res.text == "a\n1\nb"


def test_placeholder_wrapped_in_inline_tags():
    # In Word, runs split placeholders; in HTML tags may wrap them.
    assert r("<b>{{x}}</b>", {"x": "ok"}).text == "<b>ok</b>"


def test_chained_ops_apply_left_to_right():
    ctx = {"imp": "0.126"}
    assert r("{{imp | multiply:100 | round:0 | suffix:%}}", ctx).text == "13%"
    # order matters: round first gives 0 -> 0%
    assert r("{{imp | round:0 | multiply:100 | suffix:%}}", ctx).text == "0%"


def test_round_is_half_up_like_excel():
    assert r("{{v | round:0}}", {"v": "2.5"}).text == "3"
    assert r("{{v | round:0}}", {"v": "0.5"}).text == "1"
    assert r("{{v | round:1}}", {"v": "1.25"}).text == "1.3"


def test_comma_decimal_input():
    assert r("{{v | multiply:2}}", {"v": "12,5"}).text == "25"


def test_missing_key_is_reported_not_silently_blank():
    res = r("{{nope}} {{ok}}", {"ok": "1"})
    assert res.text == "[[nope?]] 1"
    assert res.unresolved == ["nope"]
    assert not res.ok


def test_present_but_empty_is_not_unresolved():
    res = r("[{{x}}]", {"x": None})
    assert res.text == "[]"
    assert res.unresolved == []


def test_missing_policies():
    assert r("{{nope}}", {}, missing="blank").text == ""
    with pytest.raises(KeyError):
        r("{{nope}}", {}, missing="error")


# --- operations -----------------------------------------------------------
def test_date_and_builtins():
    assert r("{{d | date:%d/%m/%Y}}", {"d": date(2026, 6, 21)}).text == "21/06/2026"
    assert r("{{d | date:%d/%m/%Y}}", {"d": "2026-06-21"}).text == "21/06/2026"
    assert r("{{today | date:%Y}}", {}).text.isdigit()


def test_date_format_may_contain_colon():
    from datetime import datetime

    assert r("{{d | date:%H:%M}}", {"d": datetime(2026, 1, 1, 9, 5)}).text == "09:05"


def test_upper_lower_prefix_suffix_default():
    assert r("{{v | upper}}", {"v": "ab"}).text == "AB"
    assert r("{{v | lower}}", {"v": "AB"}).text == "ab"
    assert r("{{v | prefix:N°}}", {"v": "5"}).text == "N°5"
    assert r("{{v | suffix: kg}}", {"v": "5"}).text == "5 kg"  # leading space in arg is kept
    assert r("{{v | default:n/a}}", {"v": ""}).text == "n/a"
    assert r("{{v | default:n/a}}", {}).text == "n/a"  # absent but defaulted


def test_number_op_french_format():
    assert r("{{v | number}}", {"v": "1234567.5"}).text == "1 234 567,5"


def test_bad_number_reports_error_without_crashing():
    res = r("{{v | multiply:2}}", {"v": "abc"})
    assert res.text == "[[v!]]"
    assert res.errors and res.errors[0]["key"] == "v"


# --- control flow ---------------------------------------------------------
def test_each_loop_with_this_and_columns():
    tpl = "{{#each rows}}<tr><td>{{lot}}</td><td>{{this.imp}}</td></tr>{{/each}}"
    ctx = {"rows": [{"lot": "A", "imp": 1}, {"lot": "B", "imp": 2}]}
    assert r(tpl, ctx).text == "<tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr>"


def test_each_missing_list_is_unresolved():
    res = r("{{#each rows}}x{{/each}}", {})
    assert res.unresolved == ["rows"]


def test_if_else():
    tpl = "{{#if ok}}yes{{else}}no{{/if}}"
    assert r(tpl, {"ok": "Conforme"}).text == "yes"
    assert r(tpl, {"ok": ""}).text == "no"
    assert r(tpl, {}).text == "no"


def test_nested_blocks():
    tpl = "{{#each rows}}{{#if flag}}[{{n}}]{{/if}}{{/each}}"
    ctx = {"rows": [{"n": 1, "flag": True}, {"n": 2, "flag": False}, {"n": 3, "flag": True}]}
    assert r(tpl, ctx).text == "[1][3]"


# --- syntax errors & validation -------------------------------------------
@pytest.mark.parametrize(
    "bad",
    ["{{#each rows}}", "{{/if}}", "{{else}}", "{{Bad Key}}", "{{x | nope}}", "{{x | round}}"],
)
def test_syntax_errors_raise_with_position(bad):
    with pytest.raises(TemplateSyntaxError):
        parse(bad)


def test_validate_reports_line_and_col():
    issues = validate("ok\n  {{ nope | }}", None)
    assert issues and issues[0]["code"] == "SYNTAX"
    assert issues[0]["line"] == 2


def test_validate_unknown_keys_against_catalog():
    issues = validate("{{a}} {{b}}", known_keys={"a"})
    assert [i["key"] for i in issues] == ["b"]
    assert validate("{{a}} {{today}}", known_keys={"a"}) == []


def test_extract_keys_ignores_loop_scope_and_builtins():
    tpl = "{{client}} {{#each rows}}{{lot}}{{/each}} {{today}} {{#if flag}}x{{/if}}"
    assert extract_keys(tpl) == {"client", "rows", "flag"}


def test_each_nested_loop_variable_not_validated_as_catalog_key():
    assert validate("{{#each rows}}{{lot}}{{/each}}", known_keys={"rows"}) == []
