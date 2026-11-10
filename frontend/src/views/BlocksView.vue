<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import {
  archiveBlock,
  CATEGORIES,
  duplicateBlock,
  exportBlock,
  importBlock,
  listBlocks,
  restoreBlock,
  type BlockSummary,
} from '@/api/blocks'
import { errorDetails, errorMessage } from '@/api/client'

const { t } = useI18n()
const router = useRouter()

const blocks = ref<BlockSummary[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const details = ref<string[]>([])
const notice = ref<string | null>(null)

const q = ref('')
const kind = ref<'' | 'default' | 'custom'>('')
const category = ref('')
const showArchived = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

let loadSeq = 0
async function load() {
  const seq = ++loadSeq // a slow answer to an old filter must not overwrite a newer one
  loading.value = true
  error.value = null
  try {
    const page = await listBlocks({
      q: q.value.trim() || undefined,
      kind: kind.value || undefined,
      category: category.value || undefined,
      include_archived: showArchived.value,
      page_size: 100,
    })
    if (seq !== loadSeq) return
    blocks.value = page.items
    total.value = page.total
  } catch (e) {
    if (seq === loadSeq) error.value = errorMessage(e)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

let timer: ReturnType<typeof setTimeout> | undefined
watch(q, () => {
  clearTimeout(timer)
  timer = setTimeout(load, 300)
})
watch([kind, category, showArchived], load)
onMounted(load)

const edit = (b: BlockSummary) => router.push({ name: 'block-edit', params: { id: b.id } })

async function act(action: () => Promise<void>) {
  error.value = null
  details.value = []
  notice.value = null
  try {
    await action()
  } catch (e) {
    error.value = errorMessage(e)
    details.value = errorDetails(e)
  }
}

const duplicate = (b: BlockSummary) =>
  act(async () => {
    const copy = await duplicateBlock(b.id, t('blocksPage.copyName', { name: b.name }))
    await router.push({ name: 'block-edit', params: { id: copy.id } })
  })

const setArchived = (b: BlockSummary, archived: boolean) =>
  act(async () => {
    await (archived ? archiveBlock(b.id) : restoreBlock(b.id))
    await load()
  })

const download = (b: BlockSummary) =>
  act(async () => {
    const data = await exportBlock(b.id)
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    )
    const a = document.createElement('a')
    a.href = url
    a.download = `${b.name.replace(/[^A-Za-z0-9._-]+/g, '-') || 'block'}.block.json`
    a.click()
    URL.revokeObjectURL(url)
  })

async function onFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // so choosing the same file again still fires
  if (!file) return
  await act(async () => {
    let payload: unknown
    try {
      payload = JSON.parse(await file.text())
    } catch {
      throw new Error(t('blocksPage.badFile'))
    }
    const created = await importBlock(payload)
    notice.value = t('blocksPage.imported', { name: created.name })
    await load()
  })
}

const categoryLabel = (c: string) => t(`blockCategories.${c}`)
const size = (b: BlockSummary) =>
  b.default_size_mm ? `${b.default_size_mm.w} × ${b.default_size_mm.h} mm` : ''
const hasBlocks = computed(() => blocks.value.length > 0)
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h1 class="text-lg font-semibold">{{ t('blocksPage.title') }}</h1>
      <div class="flex gap-2">
        <button class="btn-secondary" type="button" data-testid="import" @click="fileInput?.click()">
          {{ t('blocksPage.import') }}
        </button>
        <input
          ref="fileInput"
          type="file"
          accept=".json,application/json"
          class="hidden"
          data-testid="import-file"
          @change="onFile"
        />
        <button
          class="btn-primary"
          type="button"
          data-testid="new-block"
          @click="router.push({ name: 'block-new' })"
        >
          {{ t('blocksPage.new') }}
        </button>
      </div>
    </div>
    <p class="text-sm text-slate-500">{{ t('blocksPage.help') }}</p>

    <div class="card flex flex-wrap items-end gap-3">
      <div class="min-w-[12rem] flex-1">
        <label class="label" for="b-q">{{ t('blocksPage.search') }}</label>
        <input id="b-q" v-model="q" class="input" type="search" data-testid="search" />
      </div>
      <div>
        <label class="label" for="b-kind">{{ t('blocksPage.kind') }}</label>
        <select id="b-kind" v-model="kind" class="input" data-testid="kind">
          <option value="">{{ t('blocksPage.all') }}</option>
          <option value="default">{{ t('blocksPage.default') }}</option>
          <option value="custom">{{ t('blocksPage.custom') }}</option>
        </select>
      </div>
      <div>
        <label class="label" for="b-cat">{{ t('blocksPage.category') }}</label>
        <select id="b-cat" v-model="category" class="input" data-testid="category">
          <option value="">{{ t('blocksPage.all') }}</option>
          <option v-for="c in CATEGORIES" :key="c" :value="c">{{ categoryLabel(c) }}</option>
        </select>
      </div>
      <label class="flex items-center gap-2 pb-2 text-sm">
        <input v-model="showArchived" type="checkbox" class="rounded" data-testid="archived" />
        {{ t('blocksPage.showArchived') }}
      </label>
    </div>

    <p v-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>
    <ul v-if="details.length" class="list-inside list-disc text-sm text-danger-600">
      <li v-for="d in details" :key="d">{{ d }}</li>
    </ul>
    <p v-if="notice" class="text-sm text-green-700" role="status">{{ notice }}</p>
    <p v-if="loading && !hasBlocks" class="text-sm text-slate-500">{{ t('common.loading') }}</p>

    <ul class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="block-list">
      <li
        v-for="b in blocks"
        :key="b.id"
        class="card flex flex-col gap-2"
        :class="{ 'opacity-60': b.is_archived }"
        data-testid="block-card"
      >
        <div class="flex items-start justify-between gap-2">
          <h2 class="font-medium">{{ b.name }}</h2>
          <span
            class="shrink-0 rounded-full px-2 py-0.5 text-xs"
            :class="b.kind === 'default' ? 'bg-slate-100 text-slate-600' : 'bg-brand-50 text-brand-700'"
          >
            {{ t(b.kind === 'default' ? 'blocksPage.default' : 'blocksPage.custom') }}
          </span>
        </div>
        <p class="line-clamp-2 min-h-[2.5rem] text-sm text-slate-500">{{ b.description }}</p>
        <p class="text-xs text-slate-500">
          {{ categoryLabel(b.category) }} · {{ size(b) }} · v{{ b.current_version }}
          <span v-if="b.has_loop"> · {{ t('blocksPage.loop') }}</span>
          <span v-if="b.is_archived"> · {{ t('blocksPage.archived') }}</span>
        </p>
        <div class="mt-auto flex flex-wrap gap-2 pt-1">
          <button class="btn-secondary py-1" type="button" data-testid="edit" @click="edit(b)">
            {{ t(b.kind === 'default' ? 'blocksPage.view' : 'blocksPage.edit') }}
          </button>
          <button class="btn-secondary py-1" type="button" data-testid="duplicate" @click="duplicate(b)">
            {{ t('blocksPage.duplicate') }}
          </button>
          <button class="btn-secondary py-1" type="button" data-testid="export" @click="download(b)">
            {{ t('blocksPage.export') }}
          </button>
          <template v-if="b.kind === 'custom'">
            <button
              v-if="!b.is_archived"
              class="btn-secondary py-1 text-danger-600"
              type="button"
              data-testid="archive"
              @click="setArchived(b, true)"
            >
              {{ t('blocksPage.archive') }}
            </button>
            <button
              v-else
              class="btn-secondary py-1"
              type="button"
              data-testid="restore"
              @click="setArchived(b, false)"
            >
              {{ t('blocksPage.restore') }}
            </button>
          </template>
        </div>
      </li>
    </ul>
    <p v-if="!loading && !hasBlocks && !error" class="card text-center text-sm text-slate-500">
      {{ t('blocksPage.empty') }}
    </p>
  </section>
</template>
