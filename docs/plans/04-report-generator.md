# 04 - Report Generator Service

## Purpose
Take a published template + a data record and produce the finished report by replacing `{{keywords}}`, expanding table loops, and exporting.

## Pipeline
```
request {template_id, record selector}
  1. Fetch template (Template svc) + pinned blocks (Block svc)   [cache by version]
  2. Fetch record(s) (Ingestion svc)
  3. Resolve context: apply per-field operations (date_format, round, suffix, lookup, concat...)
  4. Compute generated fields: report number, today, page numbers
  5. Render HTML: template renderer + placeholder engine
  6. Store ReportDocument (HTML) -> status "draft"
  7. Export on demand: PDF (Playwright), optional DOCX
  8. Emit report.generated
```

## Placeholder engine
- Jinja-like but restricted (no code execution): `{{key}}`, `{{key | op:arg}}`, `{{#rows}}...{{/rows}}`, `{{#if key}}...{{/if}}`.
- Missing key -> configurable: error (default), blank, or visible marker. Always return a **list of unresolved keywords** to the user.
- Escape values by default (HTML-escape), explicit `|raw` only for trusted rich text.
- Port logic from `processors.py` and `word_processor.py` (note: in Word, runs split placeholders; in HTML this problem disappears).

## Report numbering
Format `yymmdd-N`, N = count of reports for that date. Implement with a transactional counter table `report_counters(date, last_n)` using `SELECT ... FOR UPDATE` (no race when two users generate at once). Optionally write the number back to the source row via Ingestion API (`PATCH /tables/samples/rows/{id}`), as the desktop app wrote it to Excel.

## Data model (schema `reports`)
- `reports(id, template_id, template_version, record_ref, report_number, status[draft|final], html_storage_key, pdf_storage_key, created_by, created_at)`
- `report_counters(date, last_n)`
- `generation_jobs(id, report_id, status, error)`

## API
- `POST /reports` `{template_id, record_id}` -> report id (sync for one, job for batch)
- `POST /reports/batch` `{template_id, record_ids[]}` -> job
- `GET /reports/{id}` metadata, `GET /reports/{id}/html`, `GET /reports/{id}/pdf`, `GET /reports/{id}/docx`
- `POST /reports/{id}/finalize` -> locks content, produces PDF
- File naming rule reused from mapping: `{name} {report_number}.pdf`

## Events
Consumes `template.published`, `report.revised`. Publishes `report.generated`, `report.finalized`.

## Frontend
Report list + "Generate": pick template, pick row(s) from a data grid, run, show unresolved-keywords warnings, open in Editor, download.

## Implementation steps
1. Placeholder engine + operations (port + unit tests from the existing app's cases).
2. Template renderer integration (shared contract).
3. Single-report generation + numbering.
4. HTML -> PDF via Playwright with A4 CSS (`@page`, page breaks, repeating header/footer).
5. Batch generation as background jobs.
6. DOCX export if required (html-to-docx or docxtpl on a parallel Word path).
7. Events + status updates (SSE).

## Gotchas (from the desktop app)
- Operator precedence and CRLF in templates caused bugs: normalise line endings; test chained operations.
- Lookups must fail loudly when the key is missing.
- Mapping completeness: validate template keywords vs. catalog before generating.

## Done when
Selecting a row and a template yields a numbered A4 PDF identical in content to what the desktop app produced.
