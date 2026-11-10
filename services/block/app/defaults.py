"""Blocks shipped with the platform. They are read-only; users duplicate one to customise it.

`seed_defaults` stores each as a version of a block with a stable id (uuid5 of the slug), so
templates keep working across installs. Changing a definition here adds a new version at the
next start-up; templates stay pinned to the version they use.

Conventions: `{{props.x}}` are settings the template designer edits on the block instance;
`{{customer_name}}`, `{{lot}}`... are data columns. The data columns used below follow the
sample import profile (`Rapport_data.xlsx`); duplicate a block to point it at your own columns.
"""

from __future__ import annotations

import uuid

NAMESPACE = uuid.UUID("6f3c1f0e-5b57-4d5e-9d57-0b2f1c7a9a10")


def default_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, slug)


DEFAULTS: list[dict] = [
    {
        "slug": "header_standard",
        "name": "Header",
        "category": "header",
        "description": "Logo, organisation name, report title, report number and date.",
        "definition": {
            "html": (
                '<div class="hdr">'
                '<div class="hdr-main">{{#if props.logo}}'
                '<img class="logo" src="{{props.logo}}" alt="">'
                '{{/if}}<span class="org">{{props.organisation}}</span></div>'
                '<div class="hdr-meta"><strong>{{props.title}}</strong><br>'
                "N° {{report_number}}<br>{{today | date:%d/%m/%Y}}</div></div>"
            ),
            "css": (
                ".hdr { display: flex; justify-content: space-between; align-items: flex-end;"
                " border-bottom: 1px solid #444; padding-bottom: 3mm; }"
                ".logo { height: 14mm; margin-right: 4mm; vertical-align: middle; }"
                ".org { font-size: 14pt; font-weight: bold; }"
                ".hdr-meta { text-align: right; font-size: 9pt; line-height: 1.5; }"
            ),
            "props_schema": {
                "logo": {"type": "string", "label": "Logo URL"},
                "organisation": {
                    "type": "string",
                    "label": "Organisation",
                    "default": "Your organisation",
                },
                "title": {"type": "string", "label": "Report title", "default": "Test report"},
            },
            "default_size_mm": {"w": 180, "h": 25},
            "repeatable_region": "header",
        },
    },
    {
        "slug": "footer_standard",
        "name": "Footer",
        "category": "footer",
        "description": "Free text on the left, page x / y on the right.",
        "definition": {
            "html": (
                '<div class="ftr"><span>{{props.text}}</span>'
                "<span>Page {{page}} / {{pages}}</span></div>"
            ),
            "css": (
                ".ftr { display: flex; justify-content: space-between;"
                " border-top: 1px solid #999; padding-top: 2mm; font-size: 8pt; color: #555; }"
            ),
            "props_schema": {"text": {"type": "string", "label": "Footer text", "default": ""}},
            "default_size_mm": {"w": 180, "h": 12},
            "repeatable_region": "footer",
        },
    },
    {
        "slug": "title",
        "name": "Title",
        "category": "text",
        "description": "A large heading.",
        "definition": {
            "html": '<h1 class="title">{{props.text}}</h1>',
            "css": ".title { font-size: 18pt; margin: 0 0 2mm; }",
            "props_schema": {"text": {"type": "string", "label": "Text", "default": "Report"}},
            "default_size_mm": {"w": 180, "h": 14},
        },
    },
    {
        "slug": "text",
        "name": "Text",
        "category": "text",
        "description": "A paragraph of fixed text.",
        "definition": {
            "html": '<p class="text">{{props.content}}</p>',
            "css": ".text { margin: 0 0 2mm; text-align: justify; }",
            "props_schema": {
                "content": {"type": "string", "label": "Text", "default": "Write your text here."}
            },
            "default_size_mm": {"w": 180, "h": 20},
        },
    },
    {
        "slug": "key_value",
        "name": "Key / value list",
        "category": "data",
        "description": "Labels and the matching values of one record (customer, date, lot...).",
        "definition": {
            "html": (
                '<table class="kv"><tbody>'
                "<tr><th>Customer</th><td>{{customer_name}}</td>"
                "<th>Report date</th><td>{{report_date | date:%d/%m/%Y}}</td></tr>"
                "<tr><th>Lot</th><td>{{lot}}</td><th>Species</th><td>{{species_name}}</td></tr>"
                "</tbody></table>"
            ),
            "css": (
                ".kv th { text-align: left; width: 22%; color: #444; font-weight: 600; }"
                ".kv th, .kv td { padding: 1mm 2mm; vertical-align: top; }"
            ),
            "default_size_mm": {"w": 180, "h": 18},
        },
    },
    {
        "slug": "results_table",
        "name": "Results table",
        "category": "table",
        "description": "One row per record of the chosen data source.",
        "definition": {
            "html": (
                '{{#if props.title}}<h2 class="tbl-title">{{props.title}}</h2>{{/if}}'
                '<table class="res"><thead><tr><th>Sample</th><th>Lot</th><th>Species</th>'
                '<th class="num">Impurities</th></tr></thead><tbody>'
                "{{#each rows}}<tr><td>{{sample_number}}</td><td>{{lot}}</td>"
                '<td>{{species_name}}</td><td class="num">{{imp_pct | suffix:%}}</td></tr>'
                "{{/each}}</tbody></table>"
            ),
            "css": (
                ".tbl-title { font-size: 12pt; margin: 0 0 2mm; }"
                ".res th, .res td { border: 1px solid #999; padding: 1mm 2mm; }"
                ".res th { background: #eee; text-align: left; }"
                ".res .num { text-align: right; }"
                ".res tr { break-inside: avoid; }"
            ),
            "props_schema": {
                "title": {"type": "string", "label": "Table title", "default": "Results"},
                "data_source": {"type": "datasource", "label": "Data source"},
            },
            "default_size_mm": {"w": 180, "h": 40},
        },
    },
    {
        "slug": "signature",
        "name": "Signature",
        "category": "other",
        "description": "Place and date, an empty box to sign in, then name and role.",
        "definition": {
            "html": (
                '<div class="sig"><div>{{#if props.place}}{{props.place}}, {{/if}}'
                '{{today | date:%d/%m/%Y}}</div><div class="sig-box"></div>'
                '<div class="sig-name">{{props.name}}</div><div>{{props.role}}</div></div>'
            ),
            "css": (
                ".sig { width: 70mm; font-size: 9pt; }"
                ".sig-box { height: 18mm; border-bottom: 1px solid #444; margin: 2mm 0; }"
                ".sig-name { font-weight: 600; }"
            ),
            "props_schema": {
                "place": {"type": "string", "label": "Place"},
                "name": {"type": "string", "label": "Name"},
                "role": {"type": "string", "label": "Role"},
            },
            "default_size_mm": {"w": 70, "h": 32},
        },
    },
    {
        "slug": "image",
        "name": "Image",
        "category": "media",
        "description": "A picture from a URL.",
        "definition": {
            "html": (
                '{{#if props.src}}<img class="pic" src="{{props.src}}" alt="{{props.alt}}">{{/if}}'
            ),
            "css": ".pic { max-width: 100%; height: auto; }",
            "props_schema": {
                "src": {"type": "string", "label": "Image URL", "required": True},
                "alt": {"type": "string", "label": "Alternative text", "default": ""},
            },
            "default_size_mm": {"w": 60, "h": 40},
        },
    },
    {
        "slug": "spacer",
        "name": "Spacer",
        "category": "layout",
        "description": "Empty vertical space (resize it on the page).",
        "definition": {
            "html": '<div class="spacer"></div>',
            "css": ".spacer { height: 100%; min-height: 4mm; }",
            "default_size_mm": {"w": 180, "h": 10},
            "repeatable_region": "any",
        },
    },
    {
        "slug": "page_break",
        "name": "Page break",
        "category": "layout",
        "description": "Starts a new page.",
        "definition": {
            "html": '<div class="pb"></div>',
            "css": ".pb { height: 0; break-after: page; page-break-after: always; }",
            "default_size_mm": {"w": 180, "h": 4},
            "resizable": False,
        },
    },
]
