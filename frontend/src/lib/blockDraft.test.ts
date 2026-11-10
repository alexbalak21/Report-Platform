import { reactive } from 'vue'
import { describe, expect, it } from 'vitest'
import type { BlockFull } from '@/api/blocks'
import {
  draftFromBlock,
  draftToInput,
  buildSample,
  cloneDraft,
  isDirty,
  newDraft,
  parseSample,
  propsToRows,
  rowsToSchema,
  type PropRow,
} from './blockDraft'

const row = (over: Partial<PropRow>): PropRow => ({
  name: 'title',
  type: 'string',
  label: '',
  default: '',
  options: '',
  required: false,
  ...over,
})

describe('rowsToSchema', () => {
  it('converts typed defaults', () => {
    const { schema, issues } = rowsToSchema([
      row({ name: 'title', default: 'Results', label: 'Title', required: true }),
      row({ name: 'rows_max', type: 'number', default: '12,5' }),
      row({ name: 'show', type: 'boolean', default: 'oui' }),
      row({ name: 'align', type: 'select', options: 'left\ncenter\n', default: 'center' }),
      row({ name: 'source', type: 'datasource' }),
    ])
    expect(issues).toEqual([])
    expect(schema.title).toEqual({ type: 'string', label: 'Title', required: true, default: 'Results' })
    expect(schema.rows_max.default).toBe(12.5)
    expect(schema.show.default).toBe(true)
    expect(schema.align).toEqual({ type: 'select', options: ['left', 'center'], default: 'center' })
    expect(schema.source).toEqual({ type: 'datasource' })
  })

  it('keeps a string default exactly as typed', () => {
    const { schema } = rowsToSchema([row({ default: '  padded ' })])
    expect(schema.title.default).toBe('  padded ')
  })

  it('reports each kind of problem with the property it concerns', () => {
    const { schema, issues } = rowsToSchema([
      row({ name: 'Bad Name' }),
      row({ name: 'dup' }),
      row({ name: 'dup' }),
      row({ name: 'pick', type: 'select' }),
      row({ name: 'n', type: 'number', default: 'abc' }),
      row({ name: 'flag', type: 'boolean', default: 'maybe' }),
      row({ name: 'side', type: 'select', options: 'a\nb', default: 'c' }),
    ])
    expect(issues).toEqual([
      { code: 'prop_name_invalid', prop: 'Bad Name' },
      { code: 'prop_name_duplicate', prop: 'dup' },
      { code: 'prop_options_required', prop: 'pick' },
      { code: 'prop_default_invalid', prop: 'n' },
      { code: 'prop_default_invalid', prop: 'flag' },
      { code: 'prop_default_not_option', prop: 'side' },
    ])
    expect(Object.keys(schema)).toEqual(['dup'])
  })

  it('round-trips through propsToRows', () => {
    const original = {
      a: { type: 'number' as const, default: 3 },
      b: { type: 'select' as const, options: ['x', 'y'], default: 'y', label: 'B' },
      c: { type: 'boolean' as const, default: false, required: true },
    }
    expect(rowsToSchema(propsToRows(original)).schema).toEqual(original)
  })
})

describe('draftToInput', () => {
  it('builds the request body from a valid draft', () => {
    const draft = { ...newDraft(), name: '  Title block ', description: ' d ' }
    const { input, issues } = draftToInput(draft)
    expect(issues).toEqual([])
    expect(input?.name).toBe('Title block')
    expect(input?.description).toBe('d')
    expect(input?.definition.default_size_mm).toEqual({ w: 180, h: 20 })
    expect(input?.definition.repeatable_region).toBe('body')
    expect(input?.definition.props_schema.text.default).toBe('Hello')
  })

  it('refuses an empty name, empty html and an impossible size', () => {
    const { input, issues } = draftToInput({ ...newDraft(), name: ' ', html: '  ', w: 300, h: 0 })
    expect(input).toBeNull()
    expect(issues.map((i) => i.code)).toEqual(['name_required', 'html_required', 'size_invalid'])
  })

  it('refuses a size that is not a number', () => {
    const { issues } = draftToInput({ ...newDraft(), name: 'x', w: Number.NaN })
    expect(issues.map((i) => i.code)).toEqual(['size_invalid'])
  })
})

describe('draftFromBlock / isDirty', () => {
  const block = {
    name: 'Results',
    category: 'table',
    description: 'desc',
    current: {
      definition: {
        html: '<table></table>',
        css: 'td{}',
        props_schema: { title: { type: 'string', default: 'R' } },
        default_size_mm: { w: 100, h: 40 },
        resizable: false,
        repeatable_region: 'footer',
      },
    },
  } as unknown as BlockFull

  it('maps a stored block into the draft', () => {
    const d = draftFromBlock(block)
    expect(d).toMatchObject({ name: 'Results', category: 'table', w: 100, h: 40, region: 'footer' })
    expect(d.resizable).toBe(false)
    expect(d.props).toHaveLength(1)
  })

  it('detects changes', () => {
    const a = draftFromBlock(block)
    expect(isDirty(a, draftFromBlock(block))).toBe(false)
    expect(isDirty(a, { ...a, css: 'td{color:red}' })).toBe(true)
  })
})

describe('parseSample', () => {
  it('accepts empty input and objects', () => {
    expect(parseSample('  ')).toEqual({ value: {}, error: null })
    expect(parseSample('{"lot":"L1"}').value).toEqual({ lot: 'L1' })
  })
  it('rejects arrays, scalars and broken JSON', () => {
    expect(parseSample('[1]').error).toBe('object')
    expect(parseSample('3').error).toBe('object')
    expect(parseSample('{oops').error).toBeTruthy()
  })
})

describe('buildSample', () => {
  it('fills plain keys and loops without overwriting what the author typed', () => {
    const out = buildSample(
      { customer: 'ACME' },
      { placeholders: ['customer', 'lot', 'rows'], loops: ['rows'], loop_columns: ['species', 'imp'] },
    )
    expect(out).toEqual({
      customer: 'ACME',
      lot: 'lot',
      rows: [
        { species: 'species 1', imp: 'imp 1' },
        { species: 'species 2', imp: 'imp 2' },
      ],
    })
  })
  it('copes with a result that has nothing in it', () => {
    expect(buildSample({ a: 1 }, {})).toEqual({ a: 1 })
  })
})

describe('cloneDraft', () => {
  it('copies a reactive draft (structuredClone throws on Vue proxies) without sharing rows', () => {
    const live = reactive(newDraft())
    const copy = cloneDraft(live)
    expect(copy).toEqual(newDraft())
    live.props[0].default = 'changed'
    expect(copy.props[0].default).toBe('Hello')
  })
})
