"""Assemble a TemplateDocument + pinned block snapshots into one A4 HTML document.

Used by the Template service (builder preview) and, in Phase 5, by the Report service, so a
preview and a generated report can never drift apart.

Two data modes
    sample   context=None  -> every keyword shows as its own `{{name}}` and each loop renders one
                              example row: the designer sees the layout, not an error.
    data     context={...} -> keywords are filled from the context; `tables` supplies the rows
                              of loop blocks, chosen by the block's `datasource` property.

Two output modes
    preview  an on-screen A4 sheet on a grey desk (the builder shows it in a sandboxed iframe)
    print    margins come from @page; {{page}} / {{pages}} become the sentinels PAGE_TOKEN /
             PAGES_TOKEN so the PDF step can replace them (Chromium footer spans)

Every hydrated block is sanitized again: prop values come from template designers.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from . import placeholders
from .sanitizer import sanitize_html
from .schemas.template_document import BlockInstance, Overlay, TemplateDocument

PAGE_TOKEN = "@@RP_PAGE@@"
PAGES_TOKEN = "@@RP_PAGES@@"
CSP = "default-src 'none'; img-src 'self' data: https:; style-src 'unsafe-inline'"

Mode = Literal["preview", "print"]


@dataclass(frozen=True)
class BlockSnapshot:
    """One immutable block version, as the Block service returns it."""

    block_id: str
    version: int
    name: str
    html: str  # sanitized, placeholders intact
    css: str  # sanitized and already scoped to `scope_class`
    scope_class: str
    definition: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> BlockSnapshot:
        """Build from GET /blocks/<id>/versions/<n>."""
        return cls(
            block_id=str(body["block_id"]),
            version=int(body["version"]),
            name=str(body.get("name", "")),
            html=body["html_sanitized"],
            css=body.get("css_sanitized", ""),
            scope_class=body["scope_class"],
            definition=body.get("definition", {}),
        )


@dataclass
class RenderOutput:
    html: str
    unresolved: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    missing_blocks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unresolved or self.errors or self.missing_blocks)


Pins = dict[tuple[str, int], BlockSnapshot]


def pin_key(block_id: Any, version: int) -> tuple[str, int]:
    return (str(block_id), int(version))


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------
def _set_path(target: dict[str, Any], key: str, value: Any) -> None:
    """target['a.b'] -> target['a']['b'] (skips a key that would overwrite a scalar)."""
    *parents, last = key.split(".")
    cur = target
    for part in parents:
        nxt = cur.setdefault(part, {})
        if not isinstance(nxt, dict):
            return
        cur = nxt
    if not isinstance(cur.get(last), dict):
        cur[last] = value


def _literal(key: str) -> str:
    return "{{" + key + "}}"


def prop_values(snap: BlockSnapshot, instance: BlockInstance) -> dict[str, Any]:
    """Defaults from the block's props_schema, overridden by what the designer set.

    A prop set to None means "not set" and falls back to the default; "" is a deliberate blank.
    """
    values = {
        name: spec["default"]
        for name, spec in (snap.definition.get("props_schema") or {}).items()
        if spec.get("default") is not None
    }
    values.update({k: v for k, v in instance.props.items() if v is not None})
    return values


def datasource_table(snap: BlockSnapshot, props: dict[str, Any]) -> str | None:
    for name, spec in (snap.definition.get("props_schema") or {}).items():
        if spec.get("type") == "datasource":
            value = props.get(name)
            return str(value) if value else None
    return None


def _instance_context(
    snap: BlockSnapshot,
    instance: BlockInstance,
    base: dict[str, Any],
    context: dict[str, Any] | None,
    tables: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    props = prop_values(snap, instance)
    ctx = dict(base)
    ctx["props"] = props
    loops = snap.definition.get("loops") or []
    if context is None:  # sample mode
        for key in snap.definition.get("placeholders") or []:
            _set_path(ctx, key, _literal(key))
        columns = snap.definition.get("loop_columns") or []
        for loop in loops:
            ctx[loop] = [{c: _literal(c) for c in columns}]
    else:
        ctx.update(context)
        ctx["props"] = props
        table = datasource_table(snap, props)
        if table is not None and tables is not None and table in tables:
            for loop in loops:
                ctx[loop] = tables[table]
    return ctx


# ---------------------------------------------------------------------------
# HTML assembly
# ---------------------------------------------------------------------------
def _mm(value: float) -> str:
    return f"{value:g}mm"


def _base_css(doc: TemplateDocument, mode: Mode) -> str:
    m = doc.page.margins_mm
    page = doc.page
    css = [
        f"@page{{size:A4 {page.orientation};margin:{_mm(m.t)} {_mm(m.r)} {_mm(m.b)} {_mm(m.l)}}}",
        "html,body{margin:0;padding:0}",
        'body{font:10pt/1.4 "Helvetica Neue",Arial,sans-serif;color:#111;background:#fff}',
        ".rp-sheet{width:100%;border-collapse:collapse;table-layout:fixed}",
        ".rp-sheet>thead{display:table-header-group}.rp-sheet>tfoot{display:table-footer-group}",
        ".rp-sheet td{padding:0;vertical-align:top}",
        ".blk{box-sizing:border-box}",
        ".blk table{border-collapse:collapse;width:100%}.blk img{max-width:100%}",
        ".rp-overlay{position:absolute;overflow:hidden;box-sizing:border-box}",
        ".rp-missing{border:1px dashed #b91c1c;color:#b91c1c;font-size:8pt;padding:1mm}",
    ]
    if mode == "preview":
        css += [
            "body{background:#d1d5db;padding:8mm 0}",
            f".rp-page{{position:relative;box-sizing:border-box;width:{_mm(page.width_mm)};"
            f"min-height:{_mm(page.height_mm)};margin:0 auto;background:#fff;"
            f"padding:{_mm(m.t)} {_mm(m.r)} {_mm(m.b)} {_mm(m.l)};"
            "box-shadow:0 1px 4px rgba(0,0,0,.35)}",
        ]
    else:
        css.append(".rp-page{position:relative}")
    return "".join(css)


def render_document(
    doc: TemplateDocument,
    blocks: Pins,
    *,
    context: dict[str, Any] | None = None,
    tables: dict[str, list[dict[str, Any]]] | None = None,
    mode: Mode = "preview",
    missing: str = "marker",
    report_number: str | None = None,
    title: str = "",
) -> RenderOutput:
    """Render the whole document. `missing` is "marker" or "blank"; "error" behaves as "marker"
    here and the caller decides what to do with `unresolved`."""
    policy = "blank" if missing == "blank" else "marker"
    out = RenderOutput(html="")
    sample = context is None

    base: dict[str, Any] = {}
    if report_number is not None:
        base["report_number"] = report_number
    elif sample:
        base["report_number"] = _literal("report_number")
    if doc.settings.page_numbering:
        base["page"], base["pages"] = (PAGE_TOKEN, PAGES_TOKEN) if mode == "print" else ("1", "1")
    else:
        base["page"] = base["pages"] = ""

    css_seen: dict[str, str] = {}

    def block_html(instance: BlockInstance) -> str:
        snap = blocks.get(pin_key(instance.block_id, instance.block_version))
        if snap is None:
            out.missing_blocks.append(
                {
                    "instance_id": instance.instance_id,
                    "block_id": str(instance.block_id),
                    "block_version": instance.block_version,
                }
            )
            return '<div class="rp-missing">Block unavailable</div>'
        ctx = _instance_context(snap, instance, base, context, tables)
        result = placeholders.render(snap.html, ctx, missing=policy)
        for key in result.unresolved:
            if key not in out.unresolved:
                out.unresolved.append(key)
        out.errors.extend({**e, "instance_id": instance.instance_id} for e in result.errors)
        css_seen.setdefault(snap.scope_class, snap.css)
        body = sanitize_html(result.text)
        return (
            f'<section class="blk {snap.scope_class}" data-instance="{instance.instance_id}" '
            f'data-locked="{"true" if instance.locked else "false"}">{body}</section>'
        )

    def region_html(items: list[BlockInstance]) -> str:
        return "".join(block_html(i) for i in items)

    def overlay_html(o: Overlay) -> str:
        m = doc.page.margins_mm
        # In print mode .rp-page starts inside the @page margins, so shift to paper coordinates.
        dx, dy = (m.l, m.t) if mode == "print" else (0.0, 0.0)
        style = (
            f"left:{_mm(o.x_mm - dx)};top:{_mm(o.y_mm - dy)};"
            f"width:{_mm(o.w_mm)};height:{_mm(o.h_mm)};z-index:{o.z}"
        )
        return f'<div class="rp-overlay" style="{style}">{block_html(o)}</div>'

    r = doc.regions
    head = region_html(r.header.items)
    body = region_html(r.body.items)
    foot = region_html(r.footer.items)
    overlays = "".join(overlay_html(o) for o in sorted(doc.overlays, key=lambda o: o.z))

    def cell(cls: str, height: float | None, inner: str) -> str:
        style = f' style="height:{_mm(height)}"' if height else ""
        return f'<td class="{cls}"{style}>{inner}</td>'

    sheet = (
        '<table class="rp-sheet">'
        f"<thead><tr>{cell('rp-head', r.header.height_mm if r.header.items else None, head)}</tr>"
        "</thead>"
        f"<tbody><tr>{cell('rp-body', None, body)}</tr></tbody>"
        f"<tfoot><tr>{cell('rp-foot', r.footer.height_mm if r.footer.items else None, foot)}</tr>"
        "</tfoot></table>"
    )
    block_css = "".join(css_seen.values())
    out.html = (
        '<!doctype html><html lang="fr"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{html_lib.escape(CSP)}">'
        f"<title>{html_lib.escape(title or 'Report')}</title>"
        f"<style>{_base_css(doc, mode)}</style><style>{block_css}</style></head>"
        f'<body class="rp-{mode}"><div class="rp-page">{sheet}{overlays}</div></body></html>'
    )
    return out


def collect_pins(doc: TemplateDocument, directory: Any) -> Pins:
    """Every pinned block version the document uses (missing ones are simply absent).

    `directory` is anything with `snapshot(block_id, version) -> BlockSnapshot | None`.
    """
    pins: Pins = {}
    for inst in doc.instances():
        key = pin_key(inst.block_id, inst.block_version)
        if key not in pins and (snap := directory.snapshot(inst.block_id, inst.block_version)):
            pins[key] = snap
    return pins


def datasources(doc: TemplateDocument, pins: Pins) -> set[str]:
    """Tables that the loop blocks of this document read from."""
    tables: set[str] = set()
    for inst in doc.instances():
        snap = pins.get(pin_key(inst.block_id, inst.block_version))
        if snap is None or not snap.definition.get("loops"):
            continue
        if table := datasource_table(snap, prop_values(snap, inst)):
            tables.add(table)
    return tables


_TOKEN_RE = re.compile(re.escape(PAGE_TOKEN) + "|" + re.escape(PAGES_TOKEN))


def has_page_tokens(html: str) -> bool:
    return bool(_TOKEN_RE.search(html))
