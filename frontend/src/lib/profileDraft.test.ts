import { describe, expect, it } from 'vitest'
import type { SheetPreview } from '@/api/ingestion'
import { draftFromPreview, draftProblems, snake, toDefinition } from './profileDraft'

const preview: SheetPreview = {
  name: 'Rapport',
  header_row: 1,
  headers: [],
  duplicate_headers: [],
  rows: [],
  suggested_columns: [
    { source: 'numero echantillon', target: 'numero_echantillon', type: 'integer' },
    { source: 'date rapport', target: 'date_rapport', type: 'date' },
    { source: 'lot', target: 'lot', type: 'string' },
  ],
}

describe('snake', () => {
  it('strips accents and punctuation', () => {
    expect(snake('Nom entreprise client')).toBe('nom_entreprise_client')
    expect(snake('Espèce (code)')).toBe('espece_code')
  })
  it('never starts with a digit and can be empty', () => {
    expect(snake('2026 results')).toBe('c_2026_results')
    expect(snake('---')).toBe('')
  })
})

describe('profile draft', () => {
  it('starts from the inferred columns, named after the file and sheet', () => {
    const d = draftFromPreview(preview, 'Rapport_data.xlsx')
    expect(d.name).toBe('Rapport_data')
    expect(d.table).toBe('rapport')
    expect(d.columns).toHaveLength(3)
    expect(draftProblems(d)).toEqual([])
  })

  it('builds a definition from the included columns only', () => {
    const d = draftFromPreview(preview)
    d.columns[1].include = false
    d.columns[2].required = true
    d.key = ['numero_echantillon', 'date_rapport'] // the excluded one is dropped from the key
    const def = toDefinition(d)
    expect(def.match).toEqual({ sheet: 'Rapport', required_headers: ['numero echantillon', 'lot'] })
    expect(def.tables[0].key).toEqual(['numero_echantillon'])
    expect(def.tables[0].columns.map((c) => c.target)).toEqual(['numero_echantillon', 'lot'])
    expect(def.tables[0].columns[1]).toMatchObject({ target: 'lot', required: true })
    expect(def.tables[0].columns[0]).not.toHaveProperty('required')
  })

  it('reports what the server would reject', () => {
    const d = draftFromPreview(preview)
    d.name = ' '
    d.table = 'Bad Name'
    d.columns[0].target = 'Not Valid'
    d.columns[2].target = 'date_rapport' // duplicate of column 1
    d.key = ['ghost']
    expect(draftProblems(d)).toEqual(
      expect.arrayContaining(['name', 'table', 'target:numero echantillon', 'duplicate:date_rapport', 'key:ghost']),
    )
  })

  it('needs at least one column', () => {
    const d = draftFromPreview(preview)
    d.columns.forEach((c) => (c.include = false))
    expect(draftProblems(d)).toContain('columns')
  })
})
