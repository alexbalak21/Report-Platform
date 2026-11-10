import { http } from './client'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type ColumnType = 'string' | 'number' | 'integer' | 'date' | 'datetime' | 'boolean'
export const COLUMN_TYPES: ColumnType[] = ['string', 'number', 'integer', 'date', 'datetime', 'boolean']

export type UploadStatus = 'uploaded' | 'importing' | 'done' | 'failed'

export interface UploadInfo {
  id: string
  filename: string
  size_bytes: number
  status: UploadStatus
  created_at: string
  sheets?: string[]
}

export interface SuggestedColumn {
  source: string
  target: string
  type: ColumnType
}

export interface SheetPreview {
  name: string
  header_row: number
  headers: string[]
  duplicate_headers: string[]
  rows: unknown[][]
  suggested_columns: SuggestedColumn[]
}

export interface ProfileSuggestion {
  id: string
  name: string
  version: number
  score: number
  sheet: string | null
  matched: string[]
  missing: string[]
  suggested: boolean
  unmapped_headers: string[]
}

export interface PreviewResponse {
  upload: UploadInfo
  sheets: { name: string; headers: string[] }[]
  preview: SheetPreview
  suggested_profiles: ProfileSuggestion[]
}

// --- profiles -------------------------------------------------------------------------------

export interface ColumnRule {
  source: string
  target: string
  type: ColumnType
  required?: boolean
  format?: string | null
  label?: string | null
  [extra: string]: unknown // lookup / operations / default are edited as JSON
}

export interface TableRule {
  table: string
  label?: string | null
  sheet?: string | null
  key: string[]
  columns: ColumnRule[]
}

export interface ProfileDefinition {
  match: { sheet?: string | null; required_headers: string[] }
  header_row: number
  first_data_row?: number | null
  stop_on_blank_rows?: number
  strict?: boolean
  tables: TableRule[]
}

export interface ImportProfile {
  id: string
  name: string
  version: number
  definition: ProfileDefinition
  created_at: string
  updated_at: string
}

// --- runs -----------------------------------------------------------------------------------

export type RunStatus = 'queued' | 'running' | 'done' | 'failed'

export interface RunError {
  level: 'error' | 'warning'
  row: number | null
  column: string | null
  message: string
  value: string | null
}

export interface ImportRun {
  id: string
  upload_id: string
  profile_id: string | null
  profile_version: number
  dry_run: boolean
  status: RunStatus
  rows_total: number
  rows_ok: number
  rows_failed: number
  tables: Record<string, { rows: number; dry_run?: boolean; [k: string]: unknown }>
  error_message: string | null
  errors_truncated: boolean
  errors?: RunError[]
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export const isFinished = (r: Pick<ImportRun, 'status'>) => r.status === 'done' || r.status === 'failed'

// --- tables ---------------------------------------------------------------------------------

export interface TableColumn {
  name: string
  label: string
  type: ColumnType
}

export interface DataTableInfo {
  name: string
  label: string | null
  key_columns: string[]
  row_count: number
  columns: TableColumn[]
}

export type Row = Record<string, unknown> & { _id: string }

export interface PlaceholderItem {
  key: string
  qualified_key: string
  table: string
  column: string
  label: string
  type: ColumnType
}

// --- calls ----------------------------------------------------------------------------------

export async function uploadWorkbook(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<UploadInfo> {
  const body = new FormData()
  body.append('file', file)
  const { data } = await http.post<UploadInfo>('/ingestion/uploads', body, {
    timeout: 120_000,
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(e.loaded / e.total)
    },
  })
  return data
}

export async function getPreview(
  uploadId: string,
  opts: { sheet?: string; headerRow?: number } = {},
): Promise<PreviewResponse> {
  const params: Record<string, string | number> = {}
  if (opts.sheet) params.sheet = opts.sheet
  if (opts.headerRow && opts.headerRow > 1) params.header_row = opts.headerRow
  const { data } = await http.get<PreviewResponse>(`/ingestion/uploads/${uploadId}/preview`, { params })
  return data
}

export async function startImport(
  uploadId: string,
  profileId: string,
  dryRun = false,
): Promise<ImportRun> {
  const { data } = await http.post<ImportRun>(`/ingestion/uploads/${uploadId}/import`, {
    profile_id: profileId,
    dry_run: dryRun,
  })
  return data
}

export async function getRun(runId: string): Promise<ImportRun> {
  return (await http.get<ImportRun>(`/ingestion/runs/${runId}`)).data
}

/** The CSV is behind the bearer token, so it is fetched as a blob rather than linked to. */
export async function downloadErrorsCsv(runId: string): Promise<Blob> {
  const { data } = await http.get<Blob>(`/ingestion/runs/${runId}/errors.csv`, {
    responseType: 'blob',
  })
  return data
}

export async function listProfiles(): Promise<ImportProfile[]> {
  const { data } = await http.get<Page<ImportProfile>>('/ingestion/profiles', {
    params: { page_size: 200 },
  })
  return data.items
}

export async function createProfile(name: string, definition: ProfileDefinition): Promise<ImportProfile> {
  return (await http.post<ImportProfile>('/ingestion/profiles', { name, definition })).data
}

export async function updateProfile(
  id: string,
  version: number,
  name: string,
  definition: ProfileDefinition,
): Promise<ImportProfile> {
  const { data } = await http.put<ImportProfile>(
    `/ingestion/profiles/${id}`,
    { name, definition },
    { headers: { 'If-Match': String(version) } },
  )
  return data
}

export async function deleteProfile(id: string): Promise<void> {
  await http.delete(`/ingestion/profiles/${id}`)
}

export interface ValidationResult {
  valid: boolean
  message?: string
  rows_total?: number
  rows_ok?: number
  rows_failed?: number
  errors?: RunError[]
  errors_total?: number
}

export async function validateProfile(
  definition: ProfileDefinition,
  uploadId?: string,
): Promise<ValidationResult> {
  const { data } = await http.post<ValidationResult>('/ingestion/profiles/validate', {
    definition,
    upload_id: uploadId,
  })
  return data
}

export async function listTables(): Promise<DataTableInfo[]> {
  return (await http.get<{ items: DataTableInfo[] }>('/ingestion/tables')).data.items
}

export interface RowQuery {
  page?: number
  pageSize?: number
  sort?: string
  q?: string
}

export async function listRows(table: string, query: RowQuery = {}): Promise<Page<Row>> {
  const params: Record<string, string | number> = {
    page: query.page ?? 1,
    page_size: query.pageSize ?? 50,
  }
  if (query.sort) params.sort = query.sort
  if (query.q) params.q = query.q
  const { data } = await http.get<Page<Row>>(`/ingestion/tables/${encodeURIComponent(table)}/rows`, {
    params,
  })
  return data
}

export async function listPlaceholders(): Promise<PlaceholderItem[]> {
  return (await http.get<{ items: PlaceholderItem[] }>('/ingestion/placeholders')).data.items
}
