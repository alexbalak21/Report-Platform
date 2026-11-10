<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink } from 'vue-router'
import { downloadErrorsCsv } from '@/api/ingestion'
import { errorMessage } from '@/api/client'
import ProfileMapper from '@/components/ProfileMapper.vue'
import { useAuthStore } from '@/stores/auth'
import { useIngestionStore } from '@/stores/ingestion'

const { t } = useI18n()
const auth = useAuthStore()
const wiz = useIngestionStore()

const dragging = ref(false)
const showMapper = ref(false)
const headerRow = ref(1)
const csvError = ref<string | null>(null)

const steps = ['upload', 'profile', 'result'] as const
const stepIndex = computed(() => steps.indexOf(wiz.step))
const canDesign = computed(() => auth.hasRole('designer'))
const running = computed(() => !!wiz.run && !['done', 'failed'].includes(wiz.run.status))
const currentSheet = computed(() => wiz.preview?.preview.name ?? '')

function pick(files: FileList | File[] | null | undefined) {
  const file = files?.[0]
  if (file) void wiz.submitFile(file)
}

function onDrop(e: DragEvent) {
  dragging.value = false
  pick(e.dataTransfer?.files)
}

function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  pick(input.files)
  input.value = '' // lets the same file be chosen again after an error
}

async function changeSheet(e: Event) {
  headerRow.value = 1
  await wiz.loadPreview({ sheet: (e.target as HTMLSelectElement).value })
}

async function changeHeaderRow() {
  const n = Math.max(1, Math.floor(Number(headerRow.value) || 1))
  headerRow.value = n
  await wiz.loadPreview({ sheet: currentSheet.value, headerRow: n })
}

async function saveProfile(draft: Parameters<typeof wiz.saveProfile>[0]) {
  const created = await wiz.saveProfile(draft)
  if (created) showMapper.value = false
}

async function downloadCsv() {
  if (!wiz.run) return
  csvError.value = null
  try {
    const url = URL.createObjectURL(await downloadErrorsCsv(wiz.run.id))
    const a = document.createElement('a')
    a.href = url
    a.download = `import-${wiz.run.id}-errors.csv`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    csvError.value = errorMessage(e)
  }
}

function again() {
  showMapper.value = false
  headerRow.value = 1
  wiz.reset()
}

onBeforeUnmount(() => wiz.reset()) // also stops any polling loop

const cell = (v: unknown) => (v === null || v === undefined ? '' : String(v))
const names = (list: string[]) => list.slice(0, 8).join(', ') + (list.length > 8 ? '…' : '')
const pct = (score: number) => Math.round(score * 100)
</script>

<template>
  <section class="space-y-6">
    <h1 class="text-lg font-semibold">{{ t('upload.title') }}</h1>

    <ol class="flex gap-2 text-sm" aria-label="Steps">
      <li
        v-for="(s, i) in steps"
        :key="s"
        class="flex items-center gap-2 rounded-full px-3 py-1"
        :class="
          i === stepIndex
            ? 'bg-brand-500 text-white'
            : i < stepIndex
              ? 'bg-brand-50 text-brand-700'
              : 'bg-surface-muted text-slate-500'
        "
        :aria-current="i === stepIndex ? 'step' : undefined"
      >
        <span class="font-semibold">{{ i + 1 }}</span> {{ t(`upload.steps.${s}`) }}
      </li>
    </ol>

    <p v-if="wiz.error" class="text-sm text-danger-600" role="alert" data-testid="wizard-error">
      {{ wiz.error }}
    </p>

    <!-- 1. File ------------------------------------------------------------------------- -->
    <div v-if="wiz.step === 'upload'" class="card">
      <label
        class="flex cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-dashed px-6 py-12 text-center transition-colors focus-within:ring-2 focus-within:ring-brand-500"
        :class="dragging ? 'border-brand-500 bg-brand-50' : 'border-surface-border'"
        data-testid="dropzone"
        @dragover.prevent="dragging = true"
        @dragleave.prevent="dragging = false"
        @drop.prevent="onDrop"
      >
        <span class="text-base font-medium">{{ t('upload.drop') }}</span>
        <span class="text-sm text-slate-500">{{ t('upload.browse') }}</span>
        <input
          class="sr-only"
          type="file"
          data-testid="file-input"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          :disabled="wiz.busy"
          @change="onFileInput"
        />
      </label>
      <div v-if="wiz.busy" class="mt-4" role="status">
        <p class="mb-1 text-sm text-slate-600">{{ t('upload.uploading') }}</p>
        <div class="h-2 overflow-hidden rounded bg-surface-muted">
          <div
            class="h-full bg-brand-500 transition-all"
            :style="{ width: `${Math.round(wiz.progress * 100)}%` }"
          />
        </div>
      </div>
    </div>

    <!-- 2. Profile ---------------------------------------------------------------------- -->
    <template v-else-if="wiz.step === 'profile' && wiz.preview">
      <div class="card space-y-3">
        <div class="flex flex-wrap items-end gap-4">
          <div>
            <p class="text-sm font-medium">{{ wiz.upload?.filename }}</p>
          </div>
          <div>
            <label class="label" for="sheet">{{ t('upload.sheet') }}</label>
            <select
              id="sheet"
              class="input w-48 py-1"
              :value="currentSheet"
              :disabled="wiz.busy"
              @change="changeSheet"
            >
              <option v-for="s in wiz.preview.sheets" :key="s.name" :value="s.name">
                {{ s.name }}
              </option>
            </select>
          </div>
          <div>
            <label class="label" for="header-row">{{ t('upload.headerRow') }}</label>
            <input
              id="header-row"
              v-model.number="headerRow"
              class="input w-24 py-1"
              type="number"
              min="1"
              :disabled="wiz.busy"
              @change="changeHeaderRow"
            />
          </div>
        </div>

        <p
          v-if="wiz.preview.preview.duplicate_headers.length"
          class="text-sm text-amber-700"
          role="alert"
        >
          {{
            t('upload.duplicateHeaders', { names: names(wiz.preview.preview.duplicate_headers) })
          }}
        </p>

        <h2 class="text-sm font-semibold">{{ t('upload.previewTitle') }}</h2>
        <div class="max-h-72 overflow-auto rounded-md border border-surface-border">
          <table class="w-full text-sm" data-testid="preview-table">
            <thead class="sticky top-0 bg-surface-muted text-left text-slate-600">
              <tr>
                <th v-for="(h, i) in wiz.preview.preview.headers" :key="i" class="px-3 py-1.5">
                  {{ h }}
                </th>
              </tr>
            </thead>
            <tbody class="divide-y divide-surface-border">
              <tr v-for="(row, r) in wiz.preview.preview.rows" :key="r">
                <td v-for="(v, c) in row" :key="c" class="whitespace-nowrap px-3 py-1">
                  {{ cell(v) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="card space-y-3">
        <h2 class="text-sm font-semibold">{{ t('upload.profiles') }}</h2>

        <fieldset v-if="wiz.suggestions.length" class="space-y-2">
          <legend class="sr-only">{{ t('upload.profiles') }}</legend>
          <label
            v-for="p in wiz.suggestions"
            :key="p.id"
            class="flex cursor-pointer items-start gap-3 rounded-md border p-3"
            :class="
              wiz.selectedProfileId === p.id
                ? 'border-brand-500 bg-brand-50'
                : 'border-surface-border'
            "
          >
            <input
              v-model="wiz.selectedProfileId"
              class="mt-1"
              type="radio"
              name="profile"
              :value="p.id"
            />
            <span class="text-sm">
              <span class="font-medium">{{ p.name }}</span>
              <span class="ml-2 text-xs text-slate-500">v{{ p.version }}</span>
              <span
                class="ml-2 rounded px-1.5 py-0.5 text-xs"
                :class="p.suggested ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'"
              >
                {{ t('upload.match', { pct: pct(p.score) }) }}
              </span>
              <span v-if="p.missing.length" class="mt-1 block text-xs text-danger-600">
                {{ t('upload.missing', { names: names(p.missing) }) }}
              </span>
              <span v-if="p.unmapped_headers.length" class="mt-1 block text-xs text-amber-700">
                {{ t('upload.unmapped', { names: names(p.unmapped_headers) }) }}
              </span>
            </span>
          </label>
        </fieldset>
        <p v-else class="text-sm text-slate-600" data-testid="no-profile">
          {{ t('upload.noProfile') }}
          <span v-if="!canDesign">{{ t('upload.noProfileOperator') }}</span>
        </p>

        <div class="flex flex-wrap gap-2">
          <button
            class="btn-primary"
            type="button"
            data-testid="import"
            :disabled="!wiz.selectedProfileId || wiz.busy"
            @click="wiz.runImport(false)"
          >
            {{ t('upload.import') }}
          </button>
          <button
            class="btn-secondary"
            type="button"
            :disabled="!wiz.selectedProfileId || wiz.busy"
            @click="wiz.runImport(true)"
          >
            {{ t('upload.dryRun') }}
          </button>
          <button
            v-if="canDesign"
            class="btn-secondary"
            type="button"
            data-testid="toggle-mapper"
            @click="showMapper = !showMapper"
          >
            {{ t('upload.createProfile') }}
          </button>
          <button class="btn-secondary" type="button" @click="again">
            {{ t('upload.another') }}
          </button>
        </div>

        <ProfileMapper
          v-if="canDesign && showMapper"
          :preview="wiz.preview.preview"
          :filename="wiz.upload?.filename ?? ''"
          :busy="wiz.busy"
          @save="saveProfile"
        />
      </div>
    </template>

    <!-- 3. Result ----------------------------------------------------------------------- -->
    <div v-else-if="wiz.step === 'result' && wiz.run" class="card space-y-4" data-testid="result">
      <div role="status" aria-live="polite" class="flex items-center gap-2 text-base font-semibold">
        <span
          v-if="running"
          class="h-3 w-3 animate-pulse rounded-full bg-brand-500"
          aria-hidden="true"
        />
        <template v-if="running">{{ t('upload.running') }}</template>
        <template v-else-if="wiz.run.status === 'failed'">
          <span class="text-danger-600">{{ t('upload.failed') }}</span>
        </template>
        <template v-else>
          {{ wiz.run.dry_run ? t('upload.doneDry') : t('upload.done') }}
        </template>
      </div>

      <p v-if="wiz.run.error_message" class="text-sm text-danger-600" role="alert">
        {{ wiz.run.error_message }}
      </p>

      <dl v-if="!running" class="grid gap-3 sm:grid-cols-3">
        <div class="rounded-md bg-surface-muted p-3">
          <dt class="text-xs text-slate-500">{{ t('upload.rowsTotal') }}</dt>
          <dd class="text-xl font-semibold" data-testid="rows-total">{{ wiz.run.rows_total }}</dd>
        </div>
        <div class="rounded-md bg-surface-muted p-3">
          <dt class="text-xs text-slate-500">{{ t('upload.rowsOk') }}</dt>
          <dd class="text-xl font-semibold text-ok-500" data-testid="rows-ok">
            {{ wiz.run.rows_ok }}
          </dd>
        </div>
        <div class="rounded-md bg-surface-muted p-3">
          <dt class="text-xs text-slate-500">{{ t('upload.rowsFailed') }}</dt>
          <dd
            class="text-xl font-semibold"
            :class="wiz.run.rows_failed ? 'text-danger-600' : ''"
            data-testid="rows-failed"
          >
            {{ wiz.run.rows_failed }}
          </dd>
        </div>
      </dl>

      <p v-if="!running && Object.keys(wiz.run.tables).length" class="text-sm text-slate-600">
        {{ t('upload.tablesWritten') }} :
        <span v-for="(info, name) in wiz.run.tables" :key="name" class="mr-3 font-mono">
          {{ name }} ({{ info.rows }})
        </span>
      </p>

      <div v-if="wiz.run.errors?.length" class="space-y-2">
        <h2 class="text-sm font-semibold">{{ t('upload.errors') }}</h2>
        <div class="max-h-72 overflow-auto rounded-md border border-surface-border">
          <table class="w-full text-sm" data-testid="errors-table">
            <thead class="sticky top-0 bg-surface-muted text-left text-slate-600">
              <tr>
                <th class="px-3 py-1.5">{{ t('upload.row') }}</th>
                <th class="px-3 py-1.5">{{ t('upload.column') }}</th>
                <th class="px-3 py-1.5">{{ t('upload.message') }}</th>
                <th class="px-3 py-1.5">{{ t('upload.value') }}</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-surface-border">
              <tr
                v-for="(e, i) in wiz.run.errors.slice(0, 200)"
                :key="i"
                :class="e.level === 'warning' ? 'text-amber-700' : ''"
              >
                <td class="px-3 py-1">{{ e.row ?? '' }}</td>
                <td class="px-3 py-1">{{ e.column ?? '' }}</td>
                <td class="px-3 py-1">{{ e.message }}</td>
                <td class="px-3 py-1 font-mono text-xs">{{ e.value ?? '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="wiz.run.errors_truncated" class="text-xs text-slate-500">
          {{ t('upload.errorsTruncated') }}
        </p>
        <p v-if="csvError" class="text-sm text-danger-600" role="alert">{{ csvError }}</p>
        <button class="btn-secondary" type="button" data-testid="download-csv" @click="downloadCsv">
          {{ t('upload.downloadCsv') }}
        </button>
      </div>

      <div v-if="!running" class="flex flex-wrap gap-2">
        <RouterLink
          v-if="wiz.run.status === 'done' && !wiz.run.dry_run"
          class="btn-primary"
          :to="{ name: 'tables' }"
        >
          {{ t('upload.viewData') }}
        </RouterLink>
        <button class="btn-secondary" type="button" @click="again">{{ t('upload.another') }}</button>
      </div>
    </div>
  </section>
</template>
