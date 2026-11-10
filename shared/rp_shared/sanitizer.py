"""HTML allow-list sanitizer for user-authored content (blocks, editor revisions).

Applied on write and again before PDF rendering. No scripts, no event handlers,
no external resources except data: images and our own asset URLs.
"""

from __future__ import annotations

import re

import nh3

ALLOWED_TAGS = {
    "a",
    "b",
    "i",
    "u",
    "s",
    "em",
    "strong",
    "sub",
    "sup",
    "span",
    "div",
    "p",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "caption",
    "colgroup",
    "col",
    "img",
    "figure",
    "figcaption",
    "section",
    "header",
    "footer",
    "small",
}

ALLOWED_ATTRIBUTES = {
    "*": {"class", "style", "id", "data-instance", "data-locked", "data-block", "title", "lang"},
    "a": {"href", "target"},  # rel is set by nh3 via link_rel
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "col": {"span", "width"},
    "table": {"border", "cellpadding", "cellspacing"},
}

URL_SCHEMES = {"http", "https", "mailto", "data"}

# Inline styles are filtered property by property: layout-escaping properties (position, top,
# z-index, ...) and anything that can fetch a resource (background-image, content, ...) are out.
ALLOWED_STYLE_PROPERTIES = {
    "font-family", "font-size", "font-weight", "font-style", "font-variant", "line-height",
    "letter-spacing", "word-spacing", "text-align", "text-decoration", "text-indent",
    "text-transform", "vertical-align", "white-space", "color", "background-color",
    "width", "height", "min-width", "max-width", "min-height", "max-height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-width", "border-style", "border-color", "border-collapse", "border-spacing",
    "border-radius", "table-layout", "display", "float", "clear", "list-style-type",
    "aspect-ratio", "object-fit",
    "page-break-before", "page-break-after", "page-break-inside",
    "break-before", "break-after", "break-inside",
}  # fmt: skip


def sanitize_html(html: str) -> str:
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        filter_style_properties=ALLOWED_STYLE_PROPERTIES,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


# {{#each rows}} / {{/each}} / {{#if x}} / {{else}} / {{/if}} usually sit *between* <tr> or <li>
# elements. An HTML parser moves bare text out of a <table>, which would leave every loop empty,
# so those tags travel through the sanitizer disguised as comments.
_STRUCTURAL_TAG = re.compile(r"\{\{\s*(?:#each\b[^}]*|/each|#if\b[^}]*|else|/if)\s*\}\}")
_DISGUISE = re.compile(r"(?:<|&lt;)!--rp:(\d+)--(?:>|&gt;)")
_ANY_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def sanitize_template_html(html: str) -> str:
    """`sanitize_html` for markup that contains placeholder loops and conditionals.

    The result keeps `{{#each}}`/`{{#if}}` exactly where the author put them, including
    directly inside <table>/<tbody>/<ul>. Other comments are dropped.
    """
    saved: list[str] = []

    def disguise(match: re.Match) -> str:
        saved.append(match.group(0))
        return f"<!--rp:{len(saved) - 1}-->"

    def restore(match: re.Match) -> str:
        index = int(match.group(1))
        return saved[index] if index < len(saved) else ""

    protected = _STRUCTURAL_TAG.sub(disguise, html)
    cleaned = nh3.clean(
        protected,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        filter_style_properties=ALLOWED_STYLE_PROPERTIES,
        link_rel="noopener noreferrer",
        strip_comments=False,
    )
    return _ANY_COMMENT.sub("", _DISGUISE.sub(restore, cleaned))
