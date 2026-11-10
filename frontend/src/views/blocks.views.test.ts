import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import * as api from '@/api/blocks'
import * as ingestion from '@/api/ingestion'
import { i18n } from '@/i18n'
import BlockEditorView from './BlockEditorView.vue'
import BlocksView from './BlocksView.vue'

// CodeMirror needs real layout; a textarea with the same v-model contract is enough here.
vi.mock('@/components/CodeEditor.vue', () => ({
  default: defineComponent({
    props: { modelValue: String, language: String, label: String, readonly: Boolean },
    emits: ['update:modelValue'],
    setup(props, { emit, expose }) {
      expose({ insertAtCursor: (text: string) => emit('update:modelValue', props.modelValue + text) })
      return () =>
        h('textarea', {
          'data-testid': `code-${props.language}`,
          value: props.modelValue,
          disabled: props.readonly,
          onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLTextAreaElement).value),
        })
    },
  }),
}))

const stub = { template: '<div />' }
const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'home', component: stub },
    { path: '/blocks', name: 'blocks', component: stub },
    { path: '/blocks/new', name: 'block-new', component: BlockEditorView },
    { path: '/blocks/:id/edit', name: 'block-edit', component: BlockEditorView },
  ],
})

const def = (over: Partial<api.BlockDefinition> = {}): api.BlockDefinition => ({
  html: '<p>{{props.text}}</p>',
  css: 'p { margin: 0 }',
  props_schema: { text: { type: 'string', default: 'Hello' } },
  default_size_mm: { w: 180, h: 20 },
  resizable: true,
  repeatable_region: 'body',
  ...over,
})

const summary = (over: Partial<api.BlockSummary> = {}): api.BlockSummary => ({
  id: 'b1',
  kind: 'custom',
  slug: null,
  name: 'Title',
  category: 'text',
  description: 'A title',
  is_archived: false,
  current_version: 1,
  default_size_mm: { w: 180, h: 20 },
  repeatable_region: 'body',
  resizable: true,
  has_loop: false,
  updated_at: '2026-11-09T10:00:00Z',
  ...over,
})

const full = (over: Partial<api.BlockFull> = {}): api.BlockFull => ({
  ...summary(),
  owner_id: 'u1',
  created_at: '2026-11-09T10:00:00Z',
  warnings: [],
  current: {
    version: 1,
    definition: def(),
    html_sanitized: '',
    css_sanitized: '',
    scope_class: 'blk-b1',
    created_by: 'u1',
    created_at: '2026-11-09T10:00:00Z',
  },
  ...over,
})

const page = (items: api.BlockSummary[]) => ({ items, total: items.length, page: 1, page_size: 100 })

function apiError(status: number, code: string, message = 'Server said no', details: unknown[] = []) {
  const config = { headers: {} } as InternalAxiosRequestConfig
  return new AxiosError('x', 'ERR', config, null, {
    data: { error: { code, message, details, correlation_id: null } },
    status,
    statusText: '',
    headers: {},
    config,
  })
}

const previewOk = (over: Partial<api.PreviewResult> = {}): api.PreviewResult => ({
  ok: true,
  html: '<p>Hello</p>',
  css: '',
  document: '<!doctype html><p>Hello</p>',
  placeholders: [],
  loops: [],
  loop_columns: [],
  unresolved: [],
  errors: [],
  warnings: [],
  ...over,
})

const mountView = (view: object) => mount(view, { global: { plugins: [i18n, router] } })
/** The editor lives in a <router-view> in the app; mounting it there avoids router warnings. */
const mountRouted = () => mount(RouterView, { global: { plugins: [i18n, router] } })

beforeEach(async () => {
  setActivePinia(createPinia())
  vi.restoreAllMocks()
  vi.spyOn(ingestion, 'listPlaceholders').mockResolvedValue([])
  vi.spyOn(api, 'previewBlock').mockResolvedValue(previewOk())
  await router.push('/')
  await router.isReady()
})
// Unmounting clears the editor's debounce timer; otherwise it fires during a later test.
enableAutoUnmount(afterEach)
afterEach(() => vi.useRealTimers())

describe('BlocksView', () => {
  it('lists blocks, and only custom ones can be archived', async () => {
    vi.spyOn(api, 'listBlocks').mockResolvedValue(
      page([
        summary({ id: 'd1', kind: 'default', name: 'Header' }),
        summary({ id: 'c1', kind: 'custom', name: 'My table', has_loop: true }),
      ]),
    )
    const w = mountView(BlocksView)
    await flushPromises()
    const cards = w.findAll('[data-testid="block-card"]')
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain('Header')
    expect(cards[0].find('[data-testid="archive"]').exists()).toBe(false)
    expect(cards[1].find('[data-testid="archive"]').exists()).toBe(true)
    expect(cards[1].text()).toContain('boucle')
  })

  it('passes the filters to the API', async () => {
    const list = vi.spyOn(api, 'listBlocks').mockResolvedValue(page([]))
    const w = mountView(BlocksView)
    await flushPromises()
    await w.get('[data-testid="kind"]').setValue('custom')
    await w.get('[data-testid="category"]').setValue('table')
    await w.get('[data-testid="archived"]').setValue(true)
    await flushPromises()
    expect(list).toHaveBeenLastCalledWith({
      q: undefined,
      kind: 'custom',
      category: 'table',
      include_archived: true,
      page_size: 100,
    })
    expect(w.text()).toContain('Aucun bloc')
  })

  it('shows an error when loading fails', async () => {
    vi.spyOn(api, 'listBlocks').mockRejectedValue(apiError(500, 'INTERNAL', 'Boom'))
    const w = mountView(BlocksView)
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toBe('Boom')
  })

  it('duplicates a block and opens the copy', async () => {
    vi.spyOn(api, 'listBlocks').mockResolvedValue(page([summary({ kind: 'default', name: 'Header' })]))
    const dup = vi.spyOn(api, 'duplicateBlock').mockResolvedValue(full({ id: 'copy-1' }))
    const w = mountView(BlocksView)
    await flushPromises()
    await w.get('[data-testid="duplicate"]').trigger('click')
    await flushPromises()
    expect(dup).toHaveBeenCalledWith('b1', 'Header (copie)')
    expect(router.currentRoute.value.fullPath).toBe('/blocks/copy-1/edit')
  })

  it('archives a custom block and reloads the list', async () => {
    const list = vi.spyOn(api, 'listBlocks').mockResolvedValue(page([summary()]))
    const archive = vi.spyOn(api, 'archiveBlock').mockResolvedValue(summary({ is_archived: true }))
    const w = mountView(BlocksView)
    await flushPromises()
    await w.get('[data-testid="archive"]').trigger('click')
    await flushPromises()
    expect(archive).toHaveBeenCalledWith('b1')
    expect(list).toHaveBeenCalledTimes(2)
  })

  async function pickFile(w: ReturnType<typeof mountView>, text: string) {
    const input = w.get('[data-testid="import-file"]')
    const file = new File([text], 'x.block.json', { type: 'application/json' })
    // jsdom has no Blob.text(); real browsers do, and that is what the view uses.
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(text) })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
  }

  it('imports an exported block file', async () => {
    vi.spyOn(api, 'listBlocks').mockResolvedValue(page([]))
    const imp = vi.spyOn(api, 'importBlock').mockResolvedValue(full({ name: 'Imported' }))
    const w = mountView(BlocksView)
    await flushPromises()
    await pickFile(w, '{"format":"x","definition":{}}')
    expect(imp).toHaveBeenCalledWith({ format: 'x', definition: {} })
    expect(w.get('[role="status"]').text()).toContain('Imported')
  })

  it('rejects a file that is not JSON without calling the API', async () => {
    vi.spyOn(api, 'listBlocks').mockResolvedValue(page([]))
    const imp = vi.spyOn(api, 'importBlock')
    const w = mountView(BlocksView)
    await flushPromises()
    await pickFile(w, 'not json')
    expect(imp).not.toHaveBeenCalled()
    expect(w.get('[role="alert"]').text()).toContain('JSON')
  })
})

describe('BlockEditorView', () => {
  async function openExisting(block: api.BlockFull = full()) {
    vi.spyOn(api, 'getBlock').mockResolvedValue(block)
    vi.spyOn(api, 'listVersions').mockResolvedValue([
      { version: block.current_version, created_by: null, created_at: block.created_at, content_hash: 'a', is_current: true },
    ])
    await router.push(`/blocks/${block.id}/edit`)
    const w = mountRouted()
    await flushPromises()
    return w
  }

  it('refuses to save a new block without a name and says why', async () => {
    const create = vi.spyOn(api, 'createBlock')
    await router.push('/blocks/new')
    const w = mountRouted()
    await flushPromises()
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(create).not.toHaveBeenCalled()
    expect(w.get('[data-testid="save-problems"]').text()).toContain('nom')
  })

  it('creates a new block from the form and moves to its edit page', async () => {
    const create = vi.spyOn(api, 'createBlock').mockResolvedValue(full({ id: 'new-1', name: 'Mine' }))
    vi.spyOn(api, 'listVersions').mockResolvedValue([])
    await router.push('/blocks/new')
    const w = mountRouted()
    await flushPromises()
    await w.get('[data-testid="name"]').setValue('  Mine ')
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    const sent = create.mock.calls[0][0]
    expect(sent.name).toBe('Mine')
    expect(sent.definition.props_schema.text).toEqual({ type: 'string', label: 'Text', default: 'Hello' })
    expect(router.currentRoute.value.fullPath).toBe('/blocks/new-1/edit')
  })

  it('marks edits as unsaved and saves with the version it opened (If-Match)', async () => {
    const update = vi
      .spyOn(api, 'updateBlock')
      .mockResolvedValue(full({ current_version: 2, current: { ...full().current, version: 2 } }))
    const w = await openExisting(full({ current_version: 1 }))
    expect(w.find('[data-testid="dirty"]').exists()).toBe(false)
    expect(w.get<HTMLButtonElement>('[data-testid="save"]').element.disabled).toBe(true)

    await w.get('[data-testid="code-css"]').setValue('p { color: red }')
    expect(w.find('[data-testid="dirty"]').exists()).toBe(true)

    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(update).toHaveBeenCalledTimes(1)
    const [id, version, body] = update.mock.calls[0]
    expect([id, version]).toEqual(['b1', 1])
    expect(body.definition?.css).toBe('p { color: red }')
    expect(w.find('[data-testid="dirty"]').exists()).toBe(false)
    expect(w.find('[data-testid="saved"]').exists()).toBe(true)
  })

  it('offers to reload when someone else saved first, and keeps the draft until then', async () => {
    const getBlock = vi.spyOn(api, 'getBlock')
    getBlock.mockResolvedValue(full())
    vi.spyOn(api, 'listVersions').mockResolvedValue([])
    vi.spyOn(api, 'updateBlock').mockRejectedValue(apiError(409, 'CONFLICT'))
    await router.push('/blocks/b1/edit')
    const w = mountRouted()
    await flushPromises()
    await w.get('[data-testid="code-css"]').setValue('p { color: blue }')
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="conflict"]').exists()).toBe(true)
    expect(w.get<HTMLTextAreaElement>('[data-testid="code-css"]').element.value).toBe('p { color: blue }')

    getBlock.mockResolvedValue(full({ current_version: 2 }))
    await w.get('[data-testid="conflict"] button').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="conflict"]').exists()).toBe(false)
    expect(w.get<HTMLTextAreaElement>('[data-testid="code-css"]').element.value).toBe('p { margin: 0 }')
  })

  it('shows server-side validation details', async () => {
    vi.spyOn(api, 'updateBlock').mockRejectedValue(
      apiError(422, 'VALIDATION_FAILED', 'Invalid block definition', [
        { path: 'definition.html', message: 'unclosed placeholder' },
      ]),
    )
    const w = await openExisting()
    await w.get('[data-testid="code-html"]').setValue('<p>{{oops</p>')
    await w.get('[data-testid="save"]').trigger('click')
    await flushPromises()
    const box = w.get('[data-testid="save-problems"]')
    expect(box.text()).toContain('Invalid block definition')
    expect(box.text()).toContain('definition.html: unclosed placeholder')
  })

  it('shows a default block read-only, with only a duplicate button', async () => {
    const w = await openExisting(full({ kind: 'default', name: 'Header' }))
    expect(w.find('[data-testid="readonly-note"]').exists()).toBe(true)
    expect(w.find('[data-testid="save"]').exists()).toBe(false)
    expect(w.get<HTMLInputElement>('[data-testid="name"]').element.disabled).toBe(true)
    expect(w.get<HTMLTextAreaElement>('[data-testid="code-html"]').element.disabled).toBe(true)

    const dup = vi.spyOn(api, 'duplicateBlock').mockResolvedValue(full({ id: 'copy-9' }))
    await w.get('[data-testid="duplicate"]').trigger('click')
    await flushPromises()
    expect(dup).toHaveBeenCalledWith('b1', 'Header (copie)')
    expect(router.currentRoute.value.fullPath).toBe('/blocks/copy-9/edit')
  })

  it('loads an old version into the editor as an unsaved change', async () => {
    vi.spyOn(api, 'getBlock').mockResolvedValue(full({ current_version: 2 }))
    vi.spyOn(api, 'listVersions').mockResolvedValue([
      { version: 2, created_by: null, created_at: '2026-11-09T10:00:00Z', content_hash: 'b', is_current: true },
      { version: 1, created_by: null, created_at: '2026-11-08T10:00:00Z', content_hash: 'a', is_current: false },
    ])
    vi.spyOn(api, 'getVersion').mockResolvedValue({
      ...full().current,
      version: 1,
      definition: def({ html: '<h1>old</h1>' }),
    })
    await router.push('/blocks/b1/edit')
    const w = mountRouted()
    await flushPromises()
    await w.get('[data-testid="load-version"]').trigger('click')
    await flushPromises()
    expect(w.get<HTMLTextAreaElement>('[data-testid="code-html"]').element.value).toBe('<h1>old</h1>')
    expect(w.find('[data-testid="dirty"]').exists()).toBe(true)
  })

  it('adds and removes settings rows', async () => {
    const w = await openExisting()
    expect(w.findAll('[data-testid="prop-row"]')).toHaveLength(1)
    await w.get('[data-testid="add-prop"]').trigger('click')
    expect(w.findAll('[data-testid="prop-row"]')).toHaveLength(2)
    await w.findAll('[data-testid="prop-row"]')[1].get('button').trigger('click')
    expect(w.findAll('[data-testid="prop-row"]')).toHaveLength(1)
  })

  it('inserts a catalog field into the HTML', async () => {
    vi.spyOn(ingestion, 'listPlaceholders').mockResolvedValue([
      { key: 'lot', qualified_key: 'samples.lot', table: 'samples', column: 'lot', label: 'Lot', type: 'string' },
    ])
    const w = await openExisting()
    await w.get('[data-testid="pick"]').setValue('lot')
    await w.get('[data-testid="pick"]').element.parentElement!.querySelector('button')!.click()
    await flushPromises()
    expect(w.get<HTMLTextAreaElement>('[data-testid="code-html"]').element.value).toBe(
      '<p>{{props.text}}</p>{{lot}}',
    )
  })
})

describe('BlockEditorView live preview', () => {
  async function open() {
    vi.spyOn(api, 'getBlock').mockResolvedValue(full())
    vi.spyOn(api, 'listVersions').mockResolvedValue([])
    await router.push('/blocks/b1/edit')
    const w = mountRouted()
    await flushPromises()
    return w
  }

  it('renders the preview in a fully sandboxed iframe', async () => {
    const w = await open()
    const frame = w.get('[data-testid="preview-frame"]')
    expect(frame.attributes('sandbox')).toBe('')
    expect(frame.attributes('srcdoc')).toContain('Hello')
  })

  it('re-renders after typing, once, and with the typed sample data as context', async () => {
    const w = await open()
    const preview = vi.mocked(api.previewBlock)
    preview.mockClear()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await w.get('[data-testid="sample"]').setValue('{"client":"ACME"}')
    await w.get('[data-testid="code-html"]').setValue('<p>{{client}}</p>')
    expect(preview).not.toHaveBeenCalled() // still debouncing
    await vi.advanceTimersByTimeAsync(500)
    expect(preview).toHaveBeenCalledTimes(1)
    const body = preview.mock.calls[0][0]
    expect(body.definition.html).toBe('<p>{{client}}</p>')
    expect(body.context).toEqual({ client: 'ACME' })
  })

  it('ignores a slow answer that arrives after a newer one', async () => {
    const w = await open()
    const preview = vi.mocked(api.previewBlock)
    let resolveSlow!: (r: api.PreviewResult) => void
    preview.mockReset()
    preview.mockImplementationOnce(() => new Promise((r) => (resolveSlow = r)))
    preview.mockResolvedValueOnce(previewOk({ document: '<!doctype html>NEWER' }))
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await w.get('[data-testid="code-html"]').setValue('<p>one</p>')
    await vi.advanceTimersByTimeAsync(500)
    await w.get('[data-testid="code-html"]').setValue('<p>two</p>')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    resolveSlow(previewOk({ document: '<!doctype html>OLDER' }))
    await flushPromises()
    expect(w.get('[data-testid="preview-frame"]').attributes('srcdoc')).toContain('NEWER')
  })

  it('lists template problems returned by the server instead of the frame', async () => {
    vi.spyOn(api, 'getBlock').mockResolvedValue(full())
    vi.spyOn(api, 'listVersions').mockResolvedValue([])
    vi.mocked(api.previewBlock).mockResolvedValue({
      ...previewOk({ ok: false, document: '' }),
      errors: [{ message: 'Unclosed {{ at line 1' }],
    })
    await router.push('/blocks/b1/edit')
    const w = mountRouted()
    await flushPromises()
    expect(w.find('[data-testid="preview-frame"]').exists()).toBe(false)
    expect(w.get('[data-testid="preview-errors"]').text()).toContain('Unclosed')
  })

  it('flags unreadable sample JSON and does not call the server', async () => {
    const w = await open()
    const preview = vi.mocked(api.previewBlock)
    preview.mockClear()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await w.get('[data-testid="sample"]').setValue('{oops')
    await vi.advanceTimersByTimeAsync(500)
    expect(preview).not.toHaveBeenCalled()
    expect(w.text()).toContain('JSON invalide')
  })

  it('fills the sample from what the block uses, keeping what was typed', async () => {
    vi.mocked(api.previewBlock).mockResolvedValue(
      previewOk({ placeholders: ['client', 'rows'], loops: ['rows'], loop_columns: ['lot'], unresolved: ['client'] }),
    )
    const w = await open()
    expect(w.get('[data-testid="unresolved"]').text()).toContain('client')
    await w.get('[data-testid="fill-sample"]').trigger('click')
    const sample = JSON.parse(w.get<HTMLTextAreaElement>('[data-testid="sample"]').element.value)
    expect(sample).toEqual({
      client: 'client',
      rows: [{ lot: 'lot 1' }, { lot: 'lot 2' }],
    })
  })
})
