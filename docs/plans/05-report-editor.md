# 05 - Report Editor (Editor Service + UI)

## Purpose
After generation, the user fine-tunes the A4 HTML report in a rich text editor: add text, images, a signature, comments, etc., then saves and finalizes.

## Why a separate service
It owns **revisions** and editing sessions, independent from generation. The Report Generator stays stateless about manual edits.

## Editor choice
CKEditor 5 (decoupled/document editor build) with A4 page styling.
- Plugins: Essentials, Paragraph, Heading, Bold/Italic/Underline, Font, Alignment, List, Table (+ properties), Image (upload), Link, Page Break, Source Editing, Undo, Remove Format, Autosave.
- Signature: custom plugin or image-upload with a "signature" preset (store user's signature image in their profile and insert in one click). Optional drawn signature via canvas.
- Locked regions: mark generated blocks `contenteditable="false"` (CKEditor "restricted editing" / non-editable `data-locked`) so header/footer or data tables can't be corrupted, while free-text zones stay editable.
- **Licensing:** CKEditor 5 is GPL-2.0-or-later (or commercial). Confirm this fits your distribution model; TipTap (MIT) is the fallback.
- Pagination: CKEditor does not paginate natively; use A4-width content styling with CSS print rules, and a visual page-break overlay. True WYSIWYG pagination is a paid CKEditor feature - for v1 accept approximate page guides, and rely on PDF render for the exact result.

## Data model (schema `editor`)
- `documents(id, report_id, current_revision, locked_by, updated_at)`
- `revisions(id, document_id, number, html_storage_key, author, created_at, note)`
- `assets(id, document_id, type[image|signature], storage_key)`

## API
- `POST /documents` `{report_id}` (fetches generated HTML from Report svc)
- `GET /documents/{id}` -> latest HTML
- `PUT /documents/{id}` `{html, base_revision}` -> new revision (conflict check on `base_revision`)
- `GET /documents/{id}/revisions`, `POST .../revisions/{n}/restore`
- `POST /assets` (image/signature upload)
- `POST /documents/{id}/lock` / heartbeat (single editor at a time in v1)

## Security
Sanitize HTML on every save (allow-list, strip scripts/event handlers); the same sanitizer as Block service, from `contracts/`.

## Events
Consumes `report.generated`. Publishes `report.revised {report_id, revision}` -> Report Generator re-renders PDF.

## Frontend
A4 canvas, toolbar, side panel (revision history, signature, inserted-assets), status bar (saved/unsaved), "Finalize & export PDF" button, read-only mode once finalized.

## Implementation steps
1. CKEditor 5 integration in the SPA with A4 CSS.
2. Documents/revisions API + autosave.
3. Image + signature upload.
4. Non-editable regions for generated content.
5. Revision history + restore.
6. Finalize -> hand off to Report Generator for PDF.
7. (Later) real-time collaboration, comments.

## Done when
User opens a generated report, adds a paragraph and a signature, saves (revision 2), finalizes, and downloads a PDF showing the edits.
