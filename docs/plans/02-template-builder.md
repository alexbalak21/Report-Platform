# 02 - Template Builder (Template Service + UI)

## Purpose
Visual designer: an A4 page on the right, a block palette on the left. User drags blocks onto the page, moves, resizes and locks them.

## Responsibilities
- Store templates and versions as a structured **TemplateDocument** (not raw HTML).
- Render a template to HTML (shared renderer used by the builder preview and Report Generator).
- Validate that every `{{keyword}}` used exists in the placeholder catalog.
- Track block usage (which templates use which block version).

## TemplateDocument (JSON)
```json
{
  "id": "tpl_123", "name": "Test report", "version": 3,
  "page": { "size": "A4", "orientation": "portrait", "margins_mm": [15,15,15,15] },
  "regions": {
    "header": [ /* block instances repeated on every page */ ],
    "body":   [ /* positioned block instances */ ],
    "footer": [ /* repeated, supports {{page}} / {{pages}} */ ]
  },
  "blocks": [{
    "instance_id": "b1", "block_id": "blk_table_results", "block_version": 2,
    "x_mm": 15, "y_mm": 60, "w_mm": 180, "h_mm": 40,
    "locked": false, "z": 1,
    "props": { "title": "Results", "data_source": "samples" }
  }]
}
```
Layout choice to settle early: **absolute positioning (mm)** is simplest for drag/drop but breaks when tables grow. Recommended: **flow layout in the body** (blocks stack vertically, reorder by drag, optional free "overlay" blocks like logos/stamps) so dynamic tables can push content and paginate. Header/footer are fixed regions.

## Data model (schema `templates`)
- `templates(id, name, owner, status[draft|published], current_version)`
- `template_versions(id, template_id, version, document_json, created_at)`
- `template_block_usage(template_id, block_id, block_version)`

## API
- `CRUD /templates`, `POST /templates/{id}/versions`, `POST /templates/{id}/publish`
- `GET /templates/{id}/render?mode=preview&sample=...` -> HTML
- `POST /templates/{id}/validate` -> missing/unknown keywords, overflow warnings
- `GET /templates/{id}/usage`

## Events
Publishes `template.published`. Consumes `block.updated` (mark templates "outdated block", offer upgrade).

## Frontend
- Left: palette (Default blocks / Custom blocks tabs, search), fetched from Block service.
- Right: A4 canvas (210x297mm, zoomable), snap grid, rulers.
- Interactions: drag from palette, move, resize, lock/unlock, duplicate, delete, z-order, undo/redo, multi-page preview.
- Right-side inspector panel: block props, placeholder picker (autocomplete from catalog).
- Autosave drafts, explicit Publish.
- Preview with real sample row.

## Implementation steps
1. TemplateDocument schema in `contracts/` + renderer (TS and/or server-side).
2. CRUD + versioning API.
3. Canvas with dnd-kit: drop, move, resize, lock.
4. Header/footer regions + page numbering.
5. Palette wired to Block service.
6. Inspector + placeholder picker.
7. Validation + preview with real data.
8. Undo/redo, autosave, polish.

## Done when
User builds a 2-page-capable template with header, footer, a text block and a results table, locks the header, publishes it, and preview shows real data.
