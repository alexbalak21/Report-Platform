# 01 - Upload & Data Entry (Ingestion Service)

## Purpose
User uploads one Excel file. The server uses an **import profile** (your "template" for identifying data; named differently here to avoid confusion with report templates) to locate columns/rows and insert them into the right DB tables.

## Responsibilities
- Accept and store the uploaded `.xlsx`.
- Parse sheets, detect header row, preview data.
- Match file to an import profile (auto-detect by headers, or user picks).
- Validate + transform values (dates, numbers, formulas) and upsert into tables.
- Expose the data and the **placeholder catalog** (available keywords) to other services.

## Data model (schema `ingestion` + `data`)
- `import_profiles(id, name, version, definition_json)`
- `uploads(id, filename, storage_key, status, profile_id, uploaded_by, created_at)`
- `import_runs(id, upload_id, rows_ok, rows_failed, errors_json)`
- Business tables in `data`, e.g. `samples`, `customers`, `products` (created from the profile; start with one generic `records` table + JSONB if schema is still moving, then promote to real columns).

## Import profile (JSON)
```json
{
  "name": "Lab results v1",
  "match": { "sheet": "Rapport", "required_headers": ["date rapport", "lot"] },
  "header_row": 1,
  "tables": [{
    "table": "samples",
    "key": ["numero echantillon"],
    "columns": [
      { "source": "date rapport", "target": "report_date", "type": "date", "format": "%d/%m/%Y" },
      { "source": "lot", "target": "lot", "type": "string" },
      { "source": "imp", "target": "imp_pct", "type": "number", "operations": [{"type":"multiply","value":100}] }
    ]
  }]
}
```
Reuse `processors.py` operations. Lookups across sheets become joins at import time.

## API
- `POST /uploads` (multipart) -> upload id
- `GET /uploads/{id}/preview` -> sheets, headers, first 50 rows, suggested profile
- `POST /uploads/{id}/import` `{profile_id}` -> starts job
- `GET /imports/{run_id}` -> status, errors
- `CRUD /profiles`
- `GET /tables`, `GET /tables/{name}/rows?filter=&page=` -> browse data
- `GET /placeholders` -> catalog: `[{key:"customer_number", table:"samples", column:"customer_number", type:"string"}]`

## Events
Publishes: `data.imported {upload_id, table, row_count}`.

## Frontend (Upload area)
Drag-and-drop zone -> preview grid -> profile picker/mapper (column <-> field) -> import -> result report with row errors downloadable as CSV.

## Implementation steps
1. Project skeleton + DB migrations (Alembic).
2. Excel reader (port `excel_reader.py`; use openpyxl, keep formula-cached values option).
3. Profile schema + validator.
4. Import engine: parse -> transform -> validate -> upsert, in a transaction, async job.
5. Preview + profile matching.
6. Placeholder catalog endpoint.
7. Tests with the existing `Rapport_data.xlsx`.

## Gotchas (from the desktop app)
- Mapping must be complete: unmapped columns should be flagged, not silently dropped.
- Excel formulas: read cached values; warn if none cached.
- Idempotent re-upload: use key columns to upsert, never duplicate.
- The old `excel_day_counter` (`yymmdd-N`) report numbering must be atomic in a DB (sequence/row lock) - not computed from counting Excel rows. Lives in Report Generator.

## Done when
Upload `Rapport_data.xlsx` -> rows visible in DB -> `/placeholders` lists every keyword.
