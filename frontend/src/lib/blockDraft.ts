import type {
  BlockDefinition,
  BlockFull,
  BlockInput,
  Category,
  PropSpec,
  PropType,
  Region,
} from '@/api/blocks'

/** Same rule as the server (rp_shared.schemas.block.PROP_NAME_RE). */
export const PROP_NAME_RE = /^[a-z][a-z0-9_]{0,39}$/

/** One line of the "settings" table in the editor; everything is text until it is validated. */
export interface PropRow {
  name: string
  type: PropType
  label: string
  default: string
  /** One option per line (select only). */
  options: string
  required: boolean
}

export interface BlockDraft {
  name: string
  category: Category
  description: string
  html: string
  css: string
  props: PropRow[]
  w: number
  h: number
  resizable: boolean
  region: Region
}

/** Problems keyed by a stable code so the view can translate them. */
export interface DraftIssue {
  code:
    | 'name_required'
    | 'html_required'
    | 'size_invalid'
    | 'prop_name_invalid'
    | 'prop_name_duplicate'
    | 'prop_options_required'
    | 'prop_default_invalid'
    | 'prop_default_not_option'
  /** The property name the issue is about, when there is one. */
  prop?: string
}

export function newDraft(): BlockDraft {
  return {
    name: '',
    category: 'text',
    description: '',
    html: '<p>{{props.text}}</p>',
    css: 'p { margin: 0; }',
    props: [
      { name: 'text', type: 'string', label: 'Text', default: 'Hello', options: '', required: false },
    ],
    w: 180,
    h: 20,
    resizable: true,
    region: 'body',
  }
}

export function propsToRows(schema: Record<string, PropSpec> | undefined): PropRow[] {
  return Object.entries(schema ?? {}).map(([name, spec]) => ({
    name,
    type: spec.type,
    label: spec.label ?? '',
    default: spec.default == null ? '' : String(spec.default),
    options: (spec.options ?? []).join('\n'),
    required: Boolean(spec.required),
  }))
}

export function draftFromBlock(block: BlockFull): BlockDraft {
  const d = block.current.definition
  return {
    name: block.name,
    category: block.category,
    description: block.description,
    html: d.html,
    css: d.css ?? '',
    props: propsToRows(d.props_schema),
    w: d.default_size_mm?.w ?? 180,
    h: d.default_size_mm?.h ?? 20,
    resizable: d.resizable ?? true,
    region: d.repeatable_region ?? 'body',
  }
}

function parseDefault(row: PropRow): { value?: PropSpec['default']; bad?: boolean } {
  const raw = row.default.trim()
  if (raw === '') return {}
  if (row.type === 'number') {
    const n = Number(raw.replace(',', '.'))
    return Number.isFinite(n) ? { value: n } : { bad: true }
  }
  if (row.type === 'boolean') {
    if (['true', 'yes', 'oui', '1'].includes(raw.toLowerCase())) return { value: true }
    if (['false', 'no', 'non', '0'].includes(raw.toLowerCase())) return { value: false }
    return { bad: true }
  }
  return { value: row.default } // string / select / datasource keep the text as typed
}

export function rowsToSchema(rows: PropRow[]): {
  schema: Record<string, PropSpec>
  issues: DraftIssue[]
} {
  const schema: Record<string, PropSpec> = {}
  const issues: DraftIssue[] = []
  for (const row of rows) {
    const name = row.name.trim()
    if (!PROP_NAME_RE.test(name)) {
      issues.push({ code: 'prop_name_invalid', prop: name })
      continue
    }
    if (name in schema) {
      issues.push({ code: 'prop_name_duplicate', prop: name })
      continue
    }
    const spec: PropSpec = { type: row.type }
    if (row.label.trim()) spec.label = row.label.trim()
    if (row.required) spec.required = true
    if (row.type === 'select') {
      const options = row.options
        .split('\n')
        .map((o) => o.trim())
        .filter(Boolean)
      if (options.length === 0) {
        issues.push({ code: 'prop_options_required', prop: name })
        continue
      }
      spec.options = options
    }
    const def = parseDefault(row)
    if (def.bad) {
      issues.push({ code: 'prop_default_invalid', prop: name })
      continue
    }
    if (def.value !== undefined) {
      if (row.type === 'select' && !spec.options?.includes(String(def.value))) {
        issues.push({ code: 'prop_default_not_option', prop: name })
        continue
      }
      spec.default = def.value
    }
    schema[name] = spec
  }
  return { schema, issues }
}

/** The definition part alone: enough for the live preview even while the name is still empty. */
export function draftToDefinition(
  draft: BlockDraft,
): { definition: BlockDefinition; issues: [] } | { definition: null; issues: DraftIssue[] } {
  const issues: DraftIssue[] = []
  if (!draft.html.trim()) issues.push({ code: 'html_required' })
  const w = Number(draft.w)
  const h = Number(draft.h)
  if (!(w > 0 && w <= 210 && h > 0 && h <= 297)) issues.push({ code: 'size_invalid' })
  const { schema, issues: propIssues } = rowsToSchema(draft.props)
  issues.push(...propIssues)
  if (issues.length) return { definition: null, issues }
  return {
    definition: {
      html: draft.html,
      css: draft.css,
      props_schema: schema,
      default_size_mm: { w, h },
      resizable: draft.resizable,
      repeatable_region: draft.region,
    },
    issues: [],
  }
}

/** Builds the request body, or the list of problems the author has to fix first. */
export function draftToInput(
  draft: BlockDraft,
): { input: BlockInput; issues: [] } | { input: null; issues: DraftIssue[] } {
  const issues: DraftIssue[] = []
  if (!draft.name.trim()) issues.push({ code: 'name_required' })
  const built = draftToDefinition(draft)
  if (built.definition === null) issues.push(...built.issues)
  if (issues.length || built.definition === null) return { input: null, issues }
  return {
    input: {
      name: draft.name.trim(),
      category: draft.category,
      description: draft.description.trim(),
      definition: built.definition,
    },
    issues: [],
  }
}

/** A draft is plain data; this copies it even when it is wrapped in a Vue reactive proxy. */
export function cloneDraft(draft: BlockDraft): BlockDraft {
  return JSON.parse(JSON.stringify(draft)) as BlockDraft
}

export function isDirty(a: BlockDraft, b: BlockDraft): boolean {
  return JSON.stringify(a) !== JSON.stringify(b)
}

/** Sample data for the preview: a JSON object, e.g. {"lot": "L1", "rows": [{"lot": "L1"}]}. */
export function parseSample(text: string): { value: Record<string, unknown>; error: string | null } {
  const trimmed = text.trim()
  if (!trimmed) return { value: {}, error: null }
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { value: {}, error: 'object' }
    }
    return { value: parsed as Record<string, unknown>, error: null }
  } catch (e) {
    return { value: {}, error: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * Sample data for the keys a block uses, merged under what the author already typed: plain keys
 * become text, loops become two rows. Existing keys are never overwritten.
 */
export function buildSample(
  existing: Record<string, unknown>,
  result: { placeholders?: string[]; loops?: string[]; loop_columns?: string[] },
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...existing }
  const loops = new Set(result.loops ?? [])
  const columns = result.loop_columns ?? []
  for (const key of result.placeholders ?? []) {
    if (key in out) continue
    if (loops.has(key)) {
      out[key] = [1, 2].map((n) =>
        Object.fromEntries(columns.map((c) => [c, `${c} ${n}`])),
      )
    } else {
      out[key] = key
    }
  }
  return out
}
