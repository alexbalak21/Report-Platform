import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import * as api from '@/api/ingestion'
import type { Role } from '@/api/auth'
import { i18n } from '@/i18n'
import { pollDelay } from '@/stores/ingestion'
import { useAuthStore } from '@/stores/auth'
import ProfilesView from './ProfilesView.vue'
import TablesView from './TablesView.vue'
import UploadView from './UploadView.vue'

const stub = { template: '<div />' }
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'home', component: stub },
    { path: '/data/upload', name: 'upload', component: stub },
    { path: '/data/tables', name: 'tables', component: stub },
  ],
})

function signIn(role: Role) {
  const auth = useAuthStore()
  auth.user = {
    id: '1',
    email: 'a@example.com',
    display_name: 'A',
    role,
    is_active: true,
    created_at: '',
    last_login: null,
  }
  auth.accessToken = 't'
}

const mountView = (view: object) =>
  mount(view, { global: { plugins: [i18n, router] } })

const upload: api.UploadInfo = {
  id: 'u1',
  filename: 'rapport.xlsx',
  size_bytes: 10,
  status: 'uploaded',
  created_at: '2026-11-08T10:00:00Z',
}

const sheet: api.SheetPreview = {
  name: 'Rapport',
  header_row: 1,
  headers: ['numero echantillon', 'lot'],
  duplicate_headers: [],
  rows: [
    [1001, 'L240501'],
    [1002, null],
  ],
  suggested_columns: [
    { source: 'numero echantillon', target: 'numero_echantillon', type: 'integer' },
    { source: 'lot', target: 'lot', type: 'string' },
  ],
}

const suggestion: api.ProfileSuggestion = {
  id: 'p1',
  name: 'Lab results',
  version: 2,
  score: 1,
  sheet: 'Rapport',
  matched: [],
  missing: [],
  suggested: true,
  unmapped_headers: ['colonne ignoree'],
}

const previewWith = (suggested: api.ProfileSuggestion[]): api.PreviewResponse => ({
  upload,
  sheets: [{ name: 'Rapport', headers: sheet.headers }],
  preview: sheet,
  suggested_profiles: suggested,
})

const doneRun: api.ImportRun = {
  id: 'r1',
  upload_id: 'u1',
  profile_id: 'p1',
  profile_version: 2,
  dry_run: false,
  status: 'done',
  rows_total: 3,
  rows_ok: 2,
  rows_failed: 1,
  tables: { samples: { rows: 2 } },
  error_message: null,
  errors_truncated: false,
  errors: [{ level: 'error', row: 4, column: 'lot', message: 'Required value is missing', value: null }],
  created_at: '',
  started_at: null,
  finished_at: null,
}

async function chooseFile(wrapper: ReturnType<typeof mountView>) {
  const input = wrapper.get('[data-testid="file-input"]')
  const file = new File(['x'], 'rapport.xlsx')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
  pollDelay.ms = 0
  vi.restoreAllMocks()
})

describe('UploadView', () => {
  it('walks upload -> profile -> result for an operator', async () => {
    signIn('operator')
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(previewWith([suggestion]))
    const start = vi.spyOn(api, 'startImport').mockResolvedValue(doneRun)
    const w = mountView(UploadView)

    await chooseFile(w)
    expect(w.text()).toContain('rapport.xlsx')
    expect(w.get('[data-testid="preview-table"]').text()).toContain('L240501')
    expect(w.text()).toContain('Lab results')
    expect(w.text()).toContain('100% match')
    expect(w.text()).toContain('colonne ignoree') // unmapped columns are called out
    expect((w.get('input[type="radio"]').element as HTMLInputElement).checked).toBe(true)
    expect(w.find('[data-testid="toggle-mapper"]').exists()).toBe(false) // operators can't design

    await w.get('[data-testid="import"]').trigger('click')
    await flushPromises()
    expect(start).toHaveBeenCalledWith('u1', 'p1', false)
    expect(w.get('[data-testid="rows-ok"]').text()).toBe('2')
    expect(w.get('[data-testid="rows-failed"]').text()).toBe('1')
    expect(w.get('[data-testid="errors-table"]').text()).toContain('Required value is missing')
    expect(w.text()).toContain('View the data')
  })

  it('offers no way to import when no profile fits, and tells operators what to do', async () => {
    signIn('operator')
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(previewWith([]))
    const w = mountView(UploadView)
    await chooseFile(w)
    expect(w.get('[data-testid="no-profile"]').text()).toContain('Ask a designer')
    expect(w.get('[data-testid="import"]').attributes('disabled')).toBeDefined()
  })

  it('lets a designer build a profile from the sheet and then use it', async () => {
    signIn('designer')
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    const getPreview = vi
      .spyOn(api, 'getPreview')
      .mockResolvedValueOnce(previewWith([]))
      .mockResolvedValueOnce(previewWith([{ ...suggestion, id: 'new', name: 'rapport' }]))
    const create = vi.spyOn(api, 'createProfile').mockResolvedValue({
      id: 'new',
      name: 'rapport',
      version: 1,
      definition: {} as api.ProfileDefinition,
      created_at: '',
      updated_at: '',
    })
    const w = mountView(UploadView)
    await chooseFile(w)

    await w.get('[data-testid="toggle-mapper"]').trigger('click')
    const mapper = w.get('[data-testid="profile-mapper"]')
    await mapper.get('input[aria-label="Key numero echantillon"]').setValue(true)
    await mapper.get('input[aria-label="Required lot"]').setValue(true)
    await mapper.trigger('submit')
    await flushPromises()

    expect(create).toHaveBeenCalledOnce()
    const [name, def] = create.mock.calls[0]
    expect(name).toBe('rapport')
    expect(def.tables[0].key).toEqual(['numero_echantillon'])
    expect(def.tables[0].columns[1]).toMatchObject({ target: 'lot', required: true })
    expect(getPreview).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="profile-mapper"]').exists()).toBe(false) // closed after saving
    expect((w.get('input[type="radio"]').element as HTMLInputElement).checked).toBe(true)
  })

  it('blocks saving a profile with an invalid table name', async () => {
    signIn('designer')
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(previewWith([]))
    const create = vi.spyOn(api, 'createProfile')
    const w = mountView(UploadView)
    await chooseFile(w)
    await w.get('[data-testid="toggle-mapper"]').trigger('click')
    const mapper = w.get('[data-testid="profile-mapper"]')
    await mapper.get('#pm-table').setValue('Not Valid!')
    expect(mapper.get('button[type="submit"]').attributes('disabled')).toBeDefined()
    await mapper.trigger('submit')
    expect(create).not.toHaveBeenCalled()
  })

  it('shows the server message when the upload is refused', async () => {
    signIn('operator')
    vi.spyOn(api, 'uploadWorkbook').mockRejectedValue(new Error('Only .xlsx files are accepted'))
    const w = mountView(UploadView)
    await chooseFile(w)
    expect(w.get('[data-testid="wizard-error"]').text()).toBe('Only .xlsx files are accepted')
    expect(w.find('[data-testid="dropzone"]').exists()).toBe(true)
  })

  it('downloads the error CSV through the authenticated client', async () => {
    signIn('operator')
    vi.spyOn(api, 'uploadWorkbook').mockResolvedValue(upload)
    vi.spyOn(api, 'getPreview').mockResolvedValue(previewWith([suggestion]))
    vi.spyOn(api, 'startImport').mockResolvedValue(doneRun)
    const csv = vi.spyOn(api, 'downloadErrorsCsv').mockResolvedValue(new Blob(['a']))
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const w = mountView(UploadView)
    await chooseFile(w)
    await w.get('[data-testid="import"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="download-csv"]').trigger('click')
    await flushPromises()
    expect(csv).toHaveBeenCalledWith('r1')
    expect(click).toHaveBeenCalledOnce()
  })
})

describe('TablesView', () => {
  const info: api.DataTableInfo = {
    name: 'samples',
    label: 'Samples',
    key_columns: ['sample_number'],
    row_count: 60,
    columns: [
      { name: 'sample_number', label: 'N°', type: 'integer' },
      { name: 'customer_name', label: 'Client', type: 'string' },
    ],
  }
  const rows = (n: number, page = 1): api.Page<api.Row> => ({
    items: Array.from({ length: n }, (_, i) => ({
      _id: `id${page}-${i}`,
      sample_number: i + 1,
      customer_name: i === 0 ? null : 'ACME',
    })),
    total: 60,
    page,
    page_size: 25,
  })

  it('shows an empty state with a link to the import when there is no data', async () => {
    signIn('operator')
    vi.spyOn(api, 'listTables').mockResolvedValue([])
    const w = mountView(TablesView)
    await flushPromises()
    expect(w.get('[data-testid="empty"]').text()).toContain('No data yet')
  })

  it('lists rows, shows a dash for empty cells, and pages and sorts server-side', async () => {
    signIn('viewer')
    vi.spyOn(api, 'listTables').mockResolvedValue([info])
    const list = vi.spyOn(api, 'listRows').mockImplementation(async (_t, q) => rows(25, q?.page))
    const w = mountView(TablesView)
    await flushPromises()

    expect(w.get('[data-testid="rows-table"]').text()).toContain('ACME')
    expect(w.get('[data-testid="rows-table"] tbody tr').text()).toContain('—')
    expect(w.get('[data-testid="page-info"]').text()).toBe('Page 1 of 3')
    expect(w.get('[data-testid="count"]').text()).toBe('60 rows')

    const buttons = w.findAll('.btn-secondary')
    await buttons[buttons.length - 1].trigger('click') // next
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith('samples', expect.objectContaining({ page: 2 }))

    await w.get('th button').trigger('click') // sort by first column
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith(
      'samples',
      expect.objectContaining({ page: 1, sort: 'sample_number' }),
    )
    await w.get('th button').trigger('click') // again: descending
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith('samples', expect.objectContaining({ sort: '-sample_number' }))
  })

  it('searches after a pause and returns to page 1', async () => {
    vi.useFakeTimers()
    try {
      signIn('viewer')
      vi.spyOn(api, 'listTables').mockResolvedValue([info])
      const list = vi.spyOn(api, 'listRows').mockResolvedValue(rows(3))
      const w = mountView(TablesView)
      await flushPromises()
      list.mockClear()
      await w.get('#q').setValue('acme')
      expect(list).not.toHaveBeenCalled() // debounced
      await vi.advanceTimersByTimeAsync(350)
      expect(list).toHaveBeenCalledTimes(1)
      expect(list).toHaveBeenCalledWith('samples', expect.objectContaining({ q: 'acme', page: 1 }))
    } finally {
      vi.useRealTimers()
    }
  })

  it('ignores a slow earlier response that arrives after a newer one', async () => {
    signIn('viewer')
    vi.spyOn(api, 'listTables').mockResolvedValue([info])
    let releaseFirst: (p: api.Page<api.Row>) => void = () => {}
    const first = new Promise<api.Page<api.Row>>((r) => (releaseFirst = r))
    vi.spyOn(api, 'listRows')
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce({ ...rows(1), items: [{ _id: 'new', sample_number: 99, customer_name: 'NEWEST' }] })
    const w = mountView(TablesView)
    await flushPromises() // first request is pending
    await w.get('th button').trigger('click') // second request resolves immediately
    await flushPromises()
    releaseFirst({ ...rows(1), items: [{ _id: 'old', sample_number: 1, customer_name: 'STALE' }] })
    await flushPromises()
    expect(w.text()).toContain('NEWEST')
    expect(w.text()).not.toContain('STALE')
  })
})

describe('ProfilesView', () => {
  const profile: api.ImportProfile = {
    id: 'p1',
    name: 'Lab results',
    version: 3,
    definition: {
      match: { required_headers: [] },
      header_row: 1,
      tables: [{ table: 'samples', key: [], columns: [{ source: 'lot', target: 'lot', type: 'string' }] }],
    },
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-02T10:00:00Z',
  }

  it('lists profiles and saves an edit with the version it was loaded at', async () => {
    signIn('designer')
    vi.spyOn(api, 'listProfiles').mockResolvedValue([profile])
    const update = vi.spyOn(api, 'updateProfile').mockResolvedValue({ ...profile, version: 4 })
    const w = mountView(ProfilesView)
    await flushPromises()
    expect(w.get('[data-testid="profiles-table"]').text()).toContain('samples')

    await w.get('tbody button').trigger('click') // Edit
    const box = w.get('[data-testid="json"]')
    expect((box.element as HTMLTextAreaElement).value).toContain('"table": "samples"')
    await w.get('#p-name').setValue('Renamed')
    const buttons = w.findAll('[data-testid="profile-editor"] button')
    await buttons[1].trigger('click') // Save
    await flushPromises()
    expect(update).toHaveBeenCalledWith('p1', 3, 'Renamed', profile.definition)
    expect(w.find('[data-testid="profile-editor"]').exists()).toBe(false)
  })

  it('refuses invalid JSON without calling the server', async () => {
    signIn('designer')
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    const validate = vi.spyOn(api, 'validateProfile')
    const w = mountView(ProfilesView)
    await flushPromises()
    await w.get('[data-testid="new-profile"]').trigger('click')
    await w.get('[data-testid="json"]').setValue('{ nope')
    await w.findAll('[data-testid="profile-editor"] button')[0].trigger('click') // Validate
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('Invalid JSON')
    expect(validate).not.toHaveBeenCalled()
  })

  it('shows the server validation details and a conflict message on a stale save', async () => {
    signIn('designer')
    vi.spyOn(api, 'listProfiles').mockResolvedValue([profile])
    const failure = (code: string, details: unknown[]) => {
      const config = { headers: {} } as InternalAxiosRequestConfig
      return new AxiosError('x', 'ERR', config, null, {
        data: { error: { code, message: 'Server said no', details, correlation_id: null } },
        status: 422,
        statusText: '',
        headers: {},
        config,
      })
    }
    vi.spyOn(api, 'validateProfile').mockRejectedValue(
      failure('VALIDATION_FAILED', [{ path: 'tables.0.table', message: 'must be lowercase' }]),
    )
    vi.spyOn(api, 'updateProfile').mockRejectedValue(failure('CONFLICT', []))
    const w = mountView(ProfilesView)
    await flushPromises()
    await w.get('tbody button').trigger('click')
    const buttons = w.findAll('[data-testid="profile-editor"] button')
    await buttons[0].trigger('click')
    await flushPromises()
    expect(w.text()).toContain('tables.0.table: must be lowercase')
    await buttons[1].trigger('click')
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('Someone else changed this profile')
    expect(w.find('[data-testid="profile-editor"]').exists()).toBe(true) // edit not lost
  })
})
