import type { ColumnType, ProfileDefinition, SheetPreview } from '@/api/ingestion'

export interface DraftColumn {
  include: boolean
  source: string
  target: string
  type: ColumnType
  required: boolean
}

export interface ProfileDraft {
  name: string
  table: string
  sheet: string
  headerRow: number
  /** Targets that identify a row; re-importing the same key updates instead of duplicating. */
  key: string[]
  columns: DraftColumn[]
}

const IDENT = /^[a-z][a-z0-9_]{0,62}$/

/** Lowercase, accent-free, `_`-separated; same idea as the server's `snake_key`. */
export function snake(value: string): string {
  const s = value
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 62)
  return /^[a-z]/.test(s) ? s : s ? `c_${s}` : ''
}

/** Start from what the server inferred for the sheet; the designer then adjusts it. */
export function draftFromPreview(preview: SheetPreview, filename = ''): ProfileDraft {
  const base = filename.replace(/\.xlsx$/i, '')
  return {
    name: base,
    table: snake(preview.name) || 'records',
    sheet: preview.name,
    headerRow: preview.header_row,
    key: [],
    columns: preview.suggested_columns.map((c) => ({
      include: true,
      source: c.source,
      target: c.target,
      type: c.type,
      required: false,
    })),
  }
}

/** Human-readable problems that would make the server reject the draft (empty = fine). */
export function draftProblems(d: ProfileDraft): string[] {
  const out: string[] = []
  if (!d.name.trim()) out.push('name')
  if (!IDENT.test(d.table)) out.push('table')
  const used = d.columns.filter((c) => c.include)
  if (!used.length) out.push('columns')
  const seen = new Set<string>()
  for (const c of used) {
    if (!IDENT.test(c.target)) out.push(`target:${c.source}`)
    else if (seen.has(c.target)) out.push(`duplicate:${c.target}`)
    seen.add(c.target)
  }
  for (const k of d.key) if (!seen.has(k)) out.push(`key:${k}`)
  return out
}

export function toDefinition(d: ProfileDraft): ProfileDefinition {
  const used = d.columns.filter((c) => c.include)
  const targets = new Set(used.map((c) => c.target))
  return {
    match: { sheet: d.sheet, required_headers: used.map((c) => c.source) },
    header_row: d.headerRow,
    tables: [
      {
        table: d.table,
        key: d.key.filter((k) => targets.has(k)),
        columns: used.map((c) => ({
          source: c.source,
          target: c.target,
          type: c.type,
          ...(c.required ? { required: true } : {}),
        })),
      },
    ],
  }
}
