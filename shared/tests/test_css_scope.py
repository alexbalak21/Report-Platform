import uuid

from rp_shared.css_scope import PREVIEW_SCOPE, scope_class, scope_css

S = "blk-abc"


def test_scope_class_is_stable_and_versioned():
    bid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert scope_class(bid) == "blk-12345678123456781234567812345678"
    assert scope_class(bid, 3) == "blk-12345678123456781234567812345678-v3"
    assert PREVIEW_SCOPE == "blk-preview"


def test_every_selector_in_a_list_is_prefixed():
    out = scope_css("h1, .a > td { color: red }", S)
    assert ".blk-abc h1" in out.css
    assert ".blk-abc .a > td" in out.css
    assert out.warnings == []


def test_page_level_selectors_become_the_block_itself():
    css = scope_css("body { margin: 0 } html table { width: 100% } :root { color: red }", S).css
    assert ".blk-abc {" in css
    assert ".blk-abc table" in css
    assert "body" not in css and "html" not in css and ":root" not in css


def test_media_queries_are_scoped_recursively():
    out = scope_css("@media print { td { padding: 2px } }", S).css
    assert out.startswith("@media print")
    assert ".blk-abc td" in out


def test_dangerous_at_rules_are_removed_with_a_warning():
    css = (
        '@import url("https://evil/x.css"); @font-face { font-family: x } '
        "@page { margin: 0 } p{color:red}"
    )
    out = scope_css(css, S)
    assert "@" not in out.css.replace("@media", "")
    assert ".blk-abc p" in out.css
    assert {w.split()[0] for w in out.warnings} == {"@import", "@font-face", "@page"}


def test_remote_urls_are_dropped_but_data_images_and_assets_stay():
    css = """
      a { background: url(https://evil/x.png) }
      b { background: url("http://evil/x.png"); color: red }
      c { background-image: url(data:image/png;base64,AAAA) }
      d { background-image: url("/api/editor/assets/1") }
    """
    out = scope_css(css, S)
    assert "evil" not in out.css
    assert "color: red" in out.css  # the other declaration of the same rule survives
    assert "data:image/png" in out.css
    assert "/api/editor/assets/1" in out.css
    assert len(out.warnings) == 2


def test_fixed_and_sticky_positioning_is_removed_absolute_is_kept():
    css = ".a{position:fixed} .b{position:sticky} .c{position:absolute;top:1mm}"
    out = scope_css(css, S).css
    assert "fixed" not in out and "sticky" not in out
    assert "position: absolute" in out


def test_legacy_script_hooks_are_removed():
    css = (
        "a{width:expression(alert(1))} b{behavior:url(x.htc)} "
        "c{-moz-binding:url(x)} d{image-set(x)}"
    )
    assert scope_css(css, S).css.strip() == ""


def test_style_tag_breakout_is_neutralised():
    out = scope_css('p::after { content: "</style><script>alert(1)</script>" }', S).css
    assert "<" not in out


def test_garbage_does_not_raise():
    out = scope_css("{{{ ;;; a{ b{ c } } } @@", S)
    assert isinstance(out.css, str)


def test_empty_css():
    assert scope_css("", S).css == ""
