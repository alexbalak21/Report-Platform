import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/api/ingestion'
import { draftFromPreview } from '@/lib/profileDraft'
import { pollDelay, useIngestionStore } from './ingestion'

const upload: api.UploadInfo = {
  id: 'u1',
  filename: 'rapport.xlsx',
  size_bytes: 10,
  status: 'uploaded',
  created_at: '2026-11-08T10:00:00Z',
}

const sheetPreview: api.SheetPreview = {
  name: 'Rapport',
  header_row: 1,
  headers: ['numero echantillon', 'lot'],
  duplicate_headers: [],
  rows: [[1001, 'L1']],
  suggested_columns: [
    { source: 'numero echantillon', target: 'numero_echantillon', type: 'integer' },
    { source: 'lot', target: 'lot', type: 'string' },
  ],
}

const suggestion = (over: Partial<api.ProfileSuggestion> = {}): api.ProfileSuggestion => ({
  id: 'p1',
  name: 'Lab',
  version: 1,
  score: 1,
  sheet: 'Rapport',
  matched: [],
  missing: [],
  suggested: true,
  unmapped_headers: [],
  ...over,
})

const preview = (suggested: api.ProfileSuggestion[] = []): api.PreviewResponse => ({
  upload,
  sheets: [{ name: 'Rapport', headers: sheetPreview.headers }],
  preview: sheetPreview,
  suggested_profiles: suggested,
})

const run = (over: Partial<api.ImportRun> = {}): api.ImportRun => ({
  id: 'r1',
  upload_id: 'u1',
  profile_id: 'p1',
  profile_version: 1,
  dry_run: false,
  status: 'queued',
  rows_total: 0,
  rows_ok: 0,
  rows_failed: 0,
  tables: {},
  error_message: null,
  errors_truncated: false,
  created_at: '',
  started_at: null,
  finished_at: null,
  ...over,
})

const file = () => new File(['x'], 'rapport.xlsx')

describe('ingestion wizard store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    pollDelay.ms = 0
    vi.restoreAllMocks()
  })

  it('uploads, previews and preselects the best-fitting profile', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockImplementation(async (_f, onProgress) => {
      onProgress?.(0.5)
      return upload
    })
    vi.spyOn(api, 'getPreview').mockResolvedValue(
      preview([suggestion({ id: 'weak', suggested: false, score: 0.4 }), suggestion()]),
    )
    const s = useIngestionStore()
    await s.submitFile(file())
    expect(s.step).toBe('profile')
    expect(s.selectedProfileId).toBe('p1')
    expect(s.progress).toBe(0.5)
    expect(s.busy).toBe(false)
  })

  it('selects nothing when no profile fits', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(preview([suggestion({ suggested: false })]))
    const s = useIngestionStore()
    await s.submitFile(file())
    expect(s.step).toBe('profile')
    expect(s.selectedProfileId).toBeNull()
  })

  it('stays on the first step and shows the server message when the upload fails', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockRejectedValue(new Error('Only .xlsx files are accepted'))
    const s = useIngestionStore()
    await s.submitFile(file())
    expect(s.step).toBe('upload')
    expect(s.error).toBe('Only .xlsx files are accepted')
    expect(s.busy).toBe(false)
  })

  it('imports and polls until the run finishes', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(preview([suggestion()]))
    vi.spyOn(api, 'startImport').mockResolvedValue(run())
    const poll = vi
      .spyOn(api, 'getRun')
      .mockResolvedValueOnce(run({ status: 'running' }))
      .mockResolvedValueOnce(run({ status: 'done', rows_ok: 3, rows_total: 3 }))
    const s = useIngestionStore()
    await s.submitFile(file())
    await s.runImport()
    expect(poll).toHaveBeenCalledTimes(2)
    expect(s.step).toBe('result')
    expect(s.run?.status).toBe('done')
    expect(s.run?.rows_ok).toBe(3)
    expect(s.busy).toBe(false)
  })

  it('passes the dry-run flag through', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(preview([suggestion()]))
    const start = vi.spyOn(api, 'startImport').mockResolvedValue(run({ status: 'done' }))
    const s = useIngestionStore()
    await s.submitFile(file())
    await s.runImport(true)
    expect(start).toHaveBeenCalledWith('u1', 'p1', true)
  })

  it('does not start without an upload and a profile', async () => {
    const start = vi.spyOn(api, 'startImport')
    await useIngestionStore().runImport()
    expect(start).not.toHaveBeenCalled()
  })

  it('stops polling when the wizard is reset', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(preview([suggestion()]))
    vi.spyOn(api, 'startImport').mockResolvedValue(run())
    const poll = vi.spyOn(api, 'getRun').mockResolvedValue(run({ status: 'running' }))
    const s = useIngestionStore()
    await s.submitFile(file())
    pollDelay.ms = 5
    const pending = s.runImport()
    await new Promise((r) => setTimeout(r, 12))
    s.reset()
    await pending
    const calls = poll.mock.calls.length
    await new Promise((r) => setTimeout(r, 30))
    expect(poll.mock.calls.length).toBe(calls) // nothing keeps polling after reset
    expect(s.step).toBe('upload')
    expect(s.run).toBeNull()
  })

  it('keeps the last known state and reports an error if polling fails', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(preview([suggestion()]))
    vi.spyOn(api, 'startImport').mockResolvedValue(run())
    vi.spyOn(api, 'getRun').mockRejectedValue(new Error('gateway down'))
    const s = useIngestionStore()
    await s.submitFile(file())
    await s.runImport()
    expect(s.error).toBe('gateway down')
    expect(s.run?.status).toBe('queued')
    expect(s.busy).toBe(false)
  })

  it('saves a drafted profile, re-reads the preview and selects the new profile', async () => {
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    const getPreview = vi
      .spyOn(api, 'getPreview')
      .mockResolvedValueOnce(preview([]))
      .mockResolvedValueOnce(preview([suggestion({ id: 'new' })]))
    const create = vi.spyOn(api, 'createProfile').mockResolvedValue({
      id: 'new',
      name: 'My profile',
      version: 1,
      definition: {} as api.ProfileDefinition,
      created_at: '',
      updated_at: '',
    })
    const s = useIngestionStore()
    await s.submitFile(file())
    const draft = draftFromPreview(sheetPreview, 'rapport.xlsx')
    draft.name = ' My profile '
    draft.key = ['numero_echantillon']
    await s.saveProfile(draft)
    expect(create).toHaveBeenCalledOnce()
    expect(create.mock.calls[0][0]).toBe('My profile') // trimmed
    expect(getPreview).toHaveBeenCalledTimes(2)
    expect(s.selectedProfileId).toBe('new')
  })
})
