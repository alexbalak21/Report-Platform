<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { listRows, listTables, type DataTableInfo, type Page, type Row } from '@/api/ingestion'
import { errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const auth = useAuthStore()

const tables = ref<DataTableInfo[]>([])
const selected = ref<string>('')
const result = ref<Page<Row> | null>(null)
const page = ref(1)
const sort = ref('')
const search = ref('')
const loading = ref(false)
const error = ref<string | null>(null)
const PAGE_SIZE = 25

const table = computed(() => tables.value.find((x) => x.name === selected.value) ?? null)
const pages = computed(() =>
  result.value ? Math.max(1, Math.ceil(result.value.total / result.value.page_size)) : 1,
)
const columns = computed(() => [
  ...(table.value?.columns.map((c) => ({ name: c.name, label: c.label || c.name })) ?? []),
  { name: '_report_number', label: t('tablesPage.reportNumber') },
])

// Several requests can overlap (typing in the search box); only the latest may update the grid.
let seq = 0
async function load() {
  if (!selected.value) return
  const mine = ++seq
  loading.value = true
  error.value = null
  try {
    const data = await listRows(selected.value, {
      page: page.value,
      pageSize: PAGE_SIZE,
      sort: sort.value || undefined,
      q: search.value.trim() || undefined,
    })
    if (mine === seq) result.value = data
  } catch (e) {
    if (mine === seq) error.value = errorMessage(e)
  } finally {
    if (mine === seq) loading.value = false
  }
}

async function init() {
  loading.value = true
  try {
    tables.value = await listTables()
    selected.value = tables.value[0]?.name ?? ''
    if (!selected.value) loading.value = false
  } catch (e) {
    error.value = errorMessage(e)
    loading.value = false
  }
}

function toggleSort(col: string) {
  sort.value = sort.value === col ? `-${col}` : col
  page.value = 1
}

const sortState = (col: string): 'ascending' | 'descending' | 'none' =>
  sort.value === col ? 'ascending' : sort.value === `-${col}` ? 'descending' : 'none'

const show = (v: unknown) => (v === null || v === undefined || v === '' ? '—' : String(v))

watch(selected, () => {
  page.value = 1
  sort.value = ''
  search.value = ''
  result.value = null
  void load()
})
watch([page, sort], load)

let timer: ReturnType<typeof setTimeout> | undefined
watch(search, () => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    page.value = 1
    void load()
  }, 300)
})
onBeforeUnmount(() => clearTimeout(timer))
onMounted(init)
</script>

<template>
  <section class="space-y-4">
    <h1 class="text-lg font-semibold">{{ t('tablesPage.title') }}</h1>
    <p v-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>

    <div v-if="!tables.length && !loading" class="card text-center" data-testid="empty">
      <p class="font-medium">{{ t('tablesPage.empty') }}</p>
      <p class="mt-1 text-sm text-slate-500">{{ t('tablesPage.emptyHint') }}</p>
      <RouterLink v-if="auth.hasRole('operator')" class="btn-primary mt-3" :to="{ name: 'upload' }">
        {{ t('nav.upload') }}
      </RouterLink>
    </div>

    <template v-else-if="table">
      <div class="flex flex-wrap items-end gap-3">
        <div>
          <label class="label" for="table">Table</label>
          <select id="table" v-model="selected" class="input w-56 py-1">
            <option v-for="x in tables" :key="x.name" :value="x.name">
              {{ x.label || x.name }}
            </option>
          </select>
        </div>
        <div class="flex-1">
          <label class="label" for="q">{{ t('tablesPage.search') }}</label>
          <input id="q" v-model="search" class="input max-w-sm py-1" type="search" />
        </div>
        <p class="pb-1.5 text-sm text-slate-500" data-testid="count">
          {{ t('tablesPage.rows', { n: result?.total ?? table.row_count }) }}
        </p>
      </div>

      <div class="card overflow-auto p-0" :class="{ 'opacity-60': loading }">
        <table class="w-full text-sm" data-testid="rows-table">
          <thead class="sticky top-0 bg-surface-muted text-left text-slate-600">
            <tr>
              <th
                v-for="c in columns"
                :key="c.name"
                class="whitespace-nowrap px-3 py-2"
                :aria-sort="sortState(c.name)"
              >
                <button type="button" class="font-medium hover:text-brand-700" @click="toggleSort(c.name)">
                  {{ c.label }}
                  <span aria-hidden="true">{{
                    sortState(c.name) === 'ascending' ? '▲' : sortState(c.name) === 'descending' ? '▼' : ''
                  }}</span>
                </button>
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-surface-border">
            <tr v-for="row in result?.items ?? []" :key="row._id">
              <td v-for="c in columns" :key="c.name" class="whitespace-nowrap px-3 py-1.5">
                {{ show(row[c.name]) }}
              </td>
            </tr>
            <tr v-if="result && !result.items.length">
              <td class="px-3 py-6 text-center text-slate-500" :colspan="columns.length">
                {{ t('tablesPage.noRows') }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex items-center justify-between text-sm">
        <button class="btn-secondary" type="button" :disabled="page <= 1 || loading" @click="page--">
          {{ t('tablesPage.prev') }}
        </button>
        <span data-testid="page-info">{{ t('tablesPage.page', { page, pages }) }}</span>
        <button class="btn-secondary" type="button" :disabled="page >= pages || loading" @click="page++">
          {{ t('tablesPage.next') }}
        </button>
      </div>
    </template>
  </section>
</template>
