import { http } from './client'
import type { Page } from './ingestion'

export const CATEGORIES = [
  'header',
  'footer',
  'text',
  'table',
  'data',
  'media',
  'layout',
  'other',
] as const
export type Category = (typeof CATEGORIES)[number]

export const REGIONS = ['body', 'header', 'footer', 'any'] as const
export type Region = (typeof REGIONS)[number]

export const PROP_TYPES = ['string', 'number', 'boolean', 'select', 'datasource'] as const
export type PropType = (typeof PROP_TYPES)[number]

export interface PropSpec {
  type: PropType
  label?: string | null
  default?: string | number | boolean | null
  options?: string[]
  required?: boolean
}

export interface BlockDefinition {
  html: string
  css: string
  props_schema: Record<string, PropSpec>
  default_size_mm: { w: number; h: number }
  resizable: boolean
  repeatable_region: Region
  // Computed by the server (placeholders, loops, ...); ignored when sent back.
  [computed: string]: unknown
}

export interface BlockSummary {
  id: string
  kind: 'default' | 'custom'
  slug: string | null
  name: string
  category: Category
  description: string
  is_archived: boolean
  current_version: number
  default_size_mm: { w: number; h: number } | null
  repeatable_region: Region
  resizable: boolean
  has_loop: boolean
  updated_at: string
}

export interface BlockVersionBody {
  version: number
  definition: BlockDefinition
  html_sanitized: string
  css_sanitized: string
  scope_class: string
  created_by: string | null
  created_at: string
}

export interface BlockFull extends BlockSummary {
  owner_id: string | null
  created_at: string
  current: BlockVersionBody
  warnings: string[]
}

export interface VersionItem {
  version: number
  created_by: string | null
  created_at: string
  content_hash: string
  is_current: boolean
}

export interface PreviewIssue {
  path?: string
  message: string
  [extra: string]: unknown
}

export interface PreviewResult {
  ok: boolean
  html: string
  css: string
  /** A complete, self-contained document for a sandboxed iframe's srcdoc. */
  document: string
  /** Top-level data keys the block uses; loop names such as `rows` are included. */
  placeholders?: string[]
  loops?: string[]
  /** Column names used inside {{#each}} loops (all loops together). */
  loop_columns?: string[]
  unresolved?: string[]
  errors: PreviewIssue[]
  warnings: string[]
}

export interface BlockListQuery {
  kind?: 'default' | 'custom'
  category?: string
  q?: string
  include_archived?: boolean
  page?: number
  page_size?: number
}

export interface BlockExport {
  format: string
  format_version: number
  name: string
  category: string
  description: string
  definition: BlockDefinition
  [extra: string]: unknown
}

export async function listBlocks(query: BlockListQuery = {}): Promise<Page<BlockSummary>> {
  const params: Record<string, string | number> = {}
  if (query.kind) params.kind = query.kind
  if (query.category) params.category = query.category
  if (query.q) params.q = query.q
  if (query.include_archived) params.include_archived = 'true'
  if (query.page) params.page = query.page
  if (query.page_size) params.page_size = query.page_size
  return (await http.get<Page<BlockSummary>>('/blocks', { params })).data
}

export async function getBlock(id: string): Promise<BlockFull> {
  return (await http.get<BlockFull>(`/blocks/${id}`)).data
}

export async function listVersions(id: string): Promise<VersionItem[]> {
  return (await http.get<{ items: VersionItem[] }>(`/blocks/${id}/versions`)).data.items
}

export async function getVersion(id: string, version: number): Promise<BlockVersionBody> {
  return (await http.get<BlockVersionBody>(`/blocks/${id}/versions/${version}`)).data
}

export interface BlockInput {
  name: string
  category: string
  description: string
  definition: BlockDefinition
}

export async function createBlock(input: BlockInput): Promise<BlockFull> {
  return (await http.post<BlockFull>('/blocks', input)).data
}

/** `expectedVersion` goes in If-Match: the server answers 409 when someone else saved first. */
export async function updateBlock(
  id: string,
  expectedVersion: number,
  input: Partial<BlockInput>,
): Promise<BlockFull> {
  return (
    await http.put<BlockFull>(`/blocks/${id}`, input, {
      headers: { 'If-Match': String(expectedVersion) },
    })
  ).data
}

export async function duplicateBlock(id: string, name?: string): Promise<BlockFull> {
  return (await http.post<BlockFull>(`/blocks/${id}/duplicate`, name ? { name } : {})).data
}

export async function archiveBlock(id: string): Promise<BlockSummary> {
  return (await http.post<BlockSummary>(`/blocks/${id}/archive`)).data
}

export async function restoreBlock(id: string): Promise<BlockSummary> {
  return (await http.post<BlockSummary>(`/blocks/${id}/restore`)).data
}

export async function previewBlock(body: {
  definition: BlockDefinition
  props?: Record<string, unknown>
  context?: Record<string, unknown>
}): Promise<PreviewResult> {
  return (await http.post<PreviewResult>('/blocks/preview', body)).data
}

export async function exportBlock(id: string): Promise<BlockExport> {
  return (await http.get<BlockExport>(`/blocks/${id}/export`)).data
}

export async function importBlock(payload: unknown): Promise<BlockFull> {
  return (await http.post<BlockFull>('/blocks/import', payload)).data
}
