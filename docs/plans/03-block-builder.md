# 03 - Block Builder (Block Service + UI)

## Purpose
A separate page where users create **custom blocks** that then appear in the Template Builder palette. The service also ships the **default blocks**.

## Block types
- **Static content**: header, footer, logo, text, signature area.
- **Data table**: columns bound to data fields, repeated per row (`{{#rows}}`).
- **Key/value**: label + `{{keyword}}` pairs (e.g. client info).
- **Chart** (later): bar/line from a data query.
- **Composite**: group of other blocks.

## Block definition (JSON)
```json
{
  "id": "blk_table_results", "name": "Results table", "kind": "default|custom",
  "category": "table", "version": 2,
  "html": "<table>...{{#rows}}<tr><td>{{lot}}</td>...</tr>{{/rows}}</table>",
  "css": ".results td { ... }",
  "props_schema": {
    "title": {"type":"string","default":"Results"},
    "data_source": {"type":"datasource"}
  },
  "placeholders": ["lot", "imp_pct"],
  "default_size_mm": {"w": 180, "h": 40},
  "resizable": true
}
```
HTML is **sanitized** server-side (allow-list tags/attributes, no scripts) since custom blocks are user content.

## Data model (schema `blocks`)
- `blocks(id, kind, name, category, owner, org_id, current_version)`
- `block_versions(id, block_id, version, definition_json, created_at)`
- Default blocks seeded by migrations (read-only, kind=default).

## API
- `GET /blocks?kind=&category=&q=`
- `POST /blocks`, `PUT /blocks/{id}` (creates new version), `DELETE`
- `POST /blocks/{id}/duplicate` (clone a default into a custom one)
- `POST /blocks/{id}/preview` with sample data -> sanitized rendered HTML
- `POST /blocks/import` / `GET /blocks/{id}/export` (JSON, to share between orgs)

## Events
Publishes `block.updated {block_id, version}`.

## Frontend (Block Builder page)
- Left: block list + new/duplicate.
- Centre: editing surface. Start with a **form + code view** (HTML/CSS editors with live preview) for v1; upgrade to a visual editor (reuse CKEditor for rich content blocks) later.
- Right: props schema editor (what the Template Builder inspector will show), placeholder picker, size defaults.
- "Test with data" preview using a sample row from Ingestion.
- Versioning: edits never break templates; templates pin `block_version` and are offered an upgrade.

## Implementation steps
1. Block schema + sanitizer (shared in `contracts/`).
2. CRUD + versions + seed default blocks (Header, Footer, Title, Text, Key/Value, Results table, Signature, Image).
3. Preview endpoint.
4. Builder UI v1 (code + live preview + props schema).
5. Import/export.
6. Visual editing, chart block (later).

## Done when
User duplicates "Results table", edits columns, saves as custom, and sees it in the Template Builder palette.
