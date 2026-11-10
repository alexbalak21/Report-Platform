<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import {
  CATEGORIES,
  createBlock,
  duplicateBlock,
  getBlock,
  getVersion,
  listVersions,
  PROP_TYPES,
  previewBlock,
  REGIONS,
  updateBlock,
  type BlockFull,
  type PreviewResult,
  type VersionItem,
} from '@/api/blocks'
import { errorCode, errorDetails, errorMessage } from '@/api/client'
import { listPlaceholders, type PlaceholderItem } from '@/api/ingestion'
import CodeEditor from '@/components/CodeEditor.vue'
import {
  buildSample,
  cloneDraft,
  draftFromBlock,
  draftToDefinition,
  draftToInput,
  isDirty,
  newDraft,
  parseSample,
  propsToRows,
  type BlockDraft,
  type DraftIssue,
} from '@/lib/blockDraft'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const blockId = computed(() => (typeof route.params.id === 'string' ? route.params.id : null))
const isNew = computed(() => blockId.value === null)

const block = ref<BlockFull | null>(null)
const draft = ref<BlockDraft>(newDraft())
const saved = ref<BlockDraft>(cloneDraft(draft.value))
const versions = ref<VersionItem[]>([])
const catalog = ref<PlaceholderItem[]>([])
const loadError = ref<string | null>(null)

const readOnly = computed(() => block.value?.kind === 'default')
const dirty = computed(() => !readOnly.value && isDirty(draft.value, saved.value))

// --- loading --------------------------------------------------------------------------------
function adopt(full: BlockFull) {
  block.value = full
  draft.value = draftFromBlock(full)
  saved.value = cloneDraft(draft.value)
  warnings.value = full.warnings
}

async function load() {
  loadError.value = null
  if (!blockId.value) {
    block.value = null
    draft.value = newDraft()
    saved.value = cloneDraft(draft.value)
    versions.value = []
    return
  }
  try {
    adopt(await getBlock(blockId.value))
    await refreshVersions()
  } catch (e) {
    loadError.value = errorMessage(e)
  }
}

/** The history list is secondary: if it fails to load, the editor itself keeps working. */
async function refreshVersions() {
  if (!block.value) return
  try {
    versions.value = await listVersions(block.value.id)
  } catch {
    versions.value = []
  }
}

onMounted(async () => {
  window.addEventListener('beforeunload', warnBeforeUnload)
  listPlaceholders()
    .then((items) => (catalog.value = items))
    .catch(() => undefined) // the picker is a convenience; the editor works without it
  await load()
  runPreview()
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', warnBeforeUnload)
  clearTimeout(previewTimer)
})
watch(blockId, async (id) => {
  // After "create" we navigate to the new block's URL; it is already loaded, don't fetch it again.
  if (id !== null && block.value?.id === id) return
  await load()
  runPreview()
})

// --- leaving with unsaved changes ------------------------------------------------------------
function warnBeforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value) e.preventDefault()
}
onBeforeRouteLeave(() => {
  if (!dirty.value) return true
  return window.confirm(t('blockEditor.leaveConfirm'))
})

// --- saving ---------------------------------------------------------------------------------
const saving = ref(false)
const saveError = ref<string | null>(null)
const saveDetails = ref<string[]>([])
const issues = ref<DraftIssue[]>([])
const warnings = ref<string[]>([])
const conflict = ref(false)
const justSaved = ref(false)

const issueText = (i: DraftIssue) => t(`blockEditor.issue.${i.code}`, { prop: i.prop ?? '' })

async function save() {
  saveError.value = null
  saveDetails.value = []
  conflict.value = false
  justSaved.value = false
  const built = draftToInput(draft.value)
  issues.value = built.issues
  if (!built.input) return
  saving.value = true
  try {
    if (block.value === null) {
      const created = await createBlock(built.input)
      adopt(created)
      await refreshVersions()
      justSaved.value = true
      // Same editor, now bound to the new id (the draft is clean, so no leave warning).
      await router.replace({ name: 'block-edit', params: { id: created.id } })
    } else {
      adopt(await updateBlock(block.value.id, block.value.current_version, built.input))
      await refreshVersions()
      justSaved.value = true
    }
  } catch (e) {
    if (errorCode(e) === 'CONFLICT') conflict.value = true
    else {
      saveError.value = errorMessage(e)
      saveDetails.value = errorDetails(e)
    }
  } finally {
    saving.value = false
  }
}

async function reloadAfterConflict() {
  conflict.value = false
  await load()
  runPreview()
}

async function duplicate() {
  if (!block.value) return
  try {
    const copy = await duplicateBlock(block.value.id, t('blocksPage.copyName', { name: block.value.name }))
    await router.push({ name: 'block-edit', params: { id: copy.id } })
  } catch (e) {
    saveError.value = errorMessage(e)
  }
}

async function loadVersion(v: VersionItem) {
  if (!block.value || readOnly.value) return
  try {
    const body = await getVersion(block.value.id, v.version)
    const d = body.definition
    draft.value = {
      ...draft.value,
      html: d.html,
      css: d.css ?? '',
      props: propsToRows(d.props_schema),
      w: d.default_size_mm?.w ?? draft.value.w,
      h: d.default_size_mm?.h ?? draft.value.h,
      resizable: d.resizable ?? true,
      region: d.repeatable_region ?? 'body',
    }
  } catch (e) {
    saveError.value = errorMessage(e)
  }
}

// --- settings table -------------------------------------------------------------------------
function addProp() {
  const taken = new Set(draft.value.props.map((p) => p.name))
  let n = draft.value.props.length + 1
  while (taken.has(`setting_${n}`)) n++
  draft.value.props.push({
    name: `setting_${n}`,
    type: 'string',
    label: '',
    default: '',
    options: '',
    required: false,
  })
}
const removeProp = (i: number) => draft.value.props.splice(i, 1)

// --- placeholder picker ---------------------------------------------------------------------
const htmlEditor = ref<InstanceType<typeof CodeEditor> | null>(null)
const pick = ref('')
function insertPlaceholder() {
  if (!pick.value) return
  htmlEditor.value?.insertAtCursor(`{{${pick.value}}}`)
  pick.value = ''
}

// --- live preview ---------------------------------------------------------------------------
const sampleText = ref('')
const sampleError = ref<string | null>(null)
const preview = ref<PreviewResult | null>(null)
const previewError = ref<string | null>(null)
const previewIssues = ref<DraftIssue[]>([])
const previewBusy = ref(false)
let previewSeq = 0
let previewTimer: ReturnType<typeof setTimeout> | undefined

function schedulePreview() {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(runPreview, 400)
}

async function runPreview() {
  clearTimeout(previewTimer)
  previewError.value = null
  const built = draftToDefinition(draft.value)
  const sample = parseSample(sampleText.value)
  sampleError.value = sample.error
  previewIssues.value = built.issues
  if (!built.definition || sample.error) return
  const seq = ++previewSeq // answers can arrive out of order; only the newest counts
  previewBusy.value = true
  try {
    const result = await previewBlock({ definition: built.definition, context: sample.value })
    if (seq === previewSeq) preview.value = result
  } catch (e) {
    if (seq === previewSeq) previewError.value = errorMessage(e)
  } finally {
    if (seq === previewSeq) previewBusy.value = false
  }
}

watch(
  () => [
    draft.value.html,
    draft.value.css,
    draft.value.props,
    draft.value.w,
    draft.value.h,
    draft.value.region,
    sampleText.value,
  ],
  schedulePreview,
  { deep: true },
)

function fillSample() {
  if (!preview.value) return
  const current = parseSample(sampleText.value)
  if (current.error) return
  sampleText.value = JSON.stringify(buildSample(current.value, preview.value), null, 2)
}

// vue-i18n reads `{` as the start of an interpolation, so literal braces travel as parameters.
const HTML_HELP = { value: '{{donnee}}', setting: '{{props.reglage}}', loop: '{{#each lignes}}…{{/each}}' }
const SAMPLE_EXAMPLE = '{"client": "ACME"}'
const SAMPLE_PLACEHOLDER = '{"client": "ACME", "rows": [{"lot": "L1"}]}'

const categoryLabel = (c: string) => t(`blockCategories.${c}`)
const when = (iso: string) => new Date(iso).toLocaleString()
const problems = computed(() => preview.value?.errors ?? [])
</script>

<template>
  <section class="space-y-4">
    <div class="flex flex-wrap items-center gap-3">
      <router-link :to="{ name: 'blocks' }" class="text-sm text-brand-600 hover:underline">
        ← {{ t('blockEditor.back') }}
      </router-link>
      <h1 class="text-lg font-semibold">
        {{ isNew ? t('blockEditor.titleNew') : t('blockEditor.title') }}
      </h1>
      <span v-if="block" class="text-xs text-slate-500">v{{ block.current_version }}</span>
      <span
        v-if="dirty"
        class="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800"
        data-testid="dirty"
      >
        {{ t('blockEditor.unsaved') }}
      </span>
      <div class="ml-auto flex gap-2">
        <button
          v-if="readOnly"
          class="btn-primary"
          type="button"
          data-testid="duplicate"
          @click="duplicate"
        >
          {{ t('blockEditor.duplicateToEdit') }}
        </button>
        <button
          v-else
          class="btn-primary"
          type="button"
          :disabled="saving || (!isNew && !dirty)"
          data-testid="save"
          @click="save"
        >
          {{ t('blockEditor.save') }}
        </button>
      </div>
    </div>

    <p v-if="loadError" class="card text-sm text-danger-600" role="alert">{{ loadError }}</p>

    <div v-else class="grid gap-4 lg:grid-cols-2">
      <!-- Left: what the author edits ------------------------------------------------------ -->
      <div class="space-y-4">
        <p
          v-if="readOnly"
          class="card bg-slate-50 text-sm text-slate-600"
          role="status"
          data-testid="readonly-note"
        >
          {{ t('blockEditor.readOnly') }}
        </p>

        <div class="card space-y-3">
          <div class="grid gap-3 sm:grid-cols-2">
            <div class="sm:col-span-2">
              <label class="label" for="be-name">{{ t('blockEditor.name') }}</label>
              <input
                id="be-name"
                v-model="draft.name"
                class="input"
                maxlength="120"
                :disabled="readOnly"
                data-testid="name"
              />
            </div>
            <div>
              <label class="label" for="be-cat">{{ t('blocksPage.category') }}</label>
              <select id="be-cat" v-model="draft.category" class="input" :disabled="readOnly">
                <option v-for="c in CATEGORIES" :key="c" :value="c">{{ categoryLabel(c) }}</option>
              </select>
            </div>
            <div>
              <label class="label" for="be-region">{{ t('blockEditor.region') }}</label>
              <select id="be-region" v-model="draft.region" class="input" :disabled="readOnly">
                <option v-for="r in REGIONS" :key="r" :value="r">
                  {{ t(`blockEditor.regions.${r}`) }}
                </option>
              </select>
            </div>
            <div class="sm:col-span-2">
              <label class="label" for="be-desc">{{ t('blockEditor.description') }}</label>
              <textarea
                id="be-desc"
                v-model="draft.description"
                class="input"
                rows="2"
                :disabled="readOnly"
              />
            </div>
            <div>
              <label class="label" for="be-w">{{ t('blockEditor.width') }}</label>
              <input id="be-w" v-model.number="draft.w" class="input" type="number" min="1" max="210" step="1" :disabled="readOnly" />
            </div>
            <div>
              <label class="label" for="be-h">{{ t('blockEditor.height') }}</label>
              <input id="be-h" v-model.number="draft.h" class="input" type="number" min="1" max="297" step="1" :disabled="readOnly" />
            </div>
            <label class="flex items-center gap-2 text-sm sm:col-span-2">
              <input v-model="draft.resizable" type="checkbox" class="rounded" :disabled="readOnly" />
              {{ t('blockEditor.resizable') }}
            </label>
          </div>
        </div>

        <div class="card space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <label class="label mb-0" for="be-html">HTML</label>
            <div v-if="!readOnly && catalog.length" class="flex items-center gap-2">
              <select
                v-model="pick"
                class="input w-auto py-1"
                :aria-label="t('blockEditor.insertPlaceholder')"
                data-testid="pick"
              >
                <option value="">{{ t('blockEditor.insertPlaceholder') }}</option>
                <option v-for="p in catalog" :key="p.qualified_key" :value="p.key">
                  {{ p.key }} ({{ p.table }})
                </option>
              </select>
              <button class="btn-secondary py-1" type="button" :disabled="!pick" @click="insertPlaceholder">
                {{ t('blockEditor.insert') }}
              </button>
            </div>
          </div>
          <CodeEditor
            id="be-html"
            ref="htmlEditor"
            v-model="draft.html"
            language="html"
            label="HTML"
            :readonly="readOnly"
          />
          <p class="text-xs text-slate-500">{{ t('blockEditor.htmlHelp', HTML_HELP) }}</p>
        </div>

        <div class="card space-y-2">
          <label class="label mb-0" for="be-css">CSS</label>
          <CodeEditor id="be-css" v-model="draft.css" language="css" label="CSS" :readonly="readOnly" />
          <p class="text-xs text-slate-500">{{ t('blockEditor.cssHelp') }}</p>
        </div>

        <div class="card space-y-3">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-medium">{{ t('blockEditor.settings') }}</h2>
            <button v-if="!readOnly" class="btn-secondary py-1" type="button" data-testid="add-prop" @click="addProp">
              {{ t('blockEditor.addSetting') }}
            </button>
          </div>
          <p class="text-xs text-slate-500">{{ t('blockEditor.settingsHelp') }}</p>
          <p v-if="!draft.props.length" class="text-sm text-slate-500">{{ t('blockEditor.noSettings') }}</p>
          <div
            v-for="(row, i) in draft.props"
            :key="i"
            class="grid gap-2 rounded-md border border-surface-border p-2 sm:grid-cols-6"
            data-testid="prop-row"
          >
            <input v-model="row.name" class="input sm:col-span-2" :aria-label="t('blockEditor.settingName')" :disabled="readOnly" spellcheck="false" />
            <select v-model="row.type" class="input sm:col-span-2" :aria-label="t('blockEditor.settingType')" :disabled="readOnly">
              <option v-for="pt in PROP_TYPES" :key="pt" :value="pt">{{ t(`blockEditor.propTypes.${pt}`) }}</option>
            </select>
            <input v-model="row.default" class="input sm:col-span-2" :aria-label="t('blockEditor.settingDefault')" :placeholder="t('blockEditor.settingDefault')" :disabled="readOnly" />
            <input v-model="row.label" class="input sm:col-span-3" :aria-label="t('blockEditor.settingLabel')" :placeholder="t('blockEditor.settingLabel')" :disabled="readOnly" />
            <label class="flex items-center gap-2 text-sm sm:col-span-2">
              <input v-model="row.required" type="checkbox" class="rounded" :disabled="readOnly" />
              {{ t('blockEditor.required') }}
            </label>
            <button v-if="!readOnly" class="btn-secondary py-1 text-danger-600" type="button" :aria-label="t('blockEditor.remove')" @click="removeProp(i)">
              ✕
            </button>
            <textarea
              v-if="row.type === 'select'"
              v-model="row.options"
              class="input sm:col-span-6"
              rows="3"
              :placeholder="t('blockEditor.optionsHint')"
              :aria-label="t('blockEditor.options')"
              :disabled="readOnly"
            />
          </div>
        </div>

        <div v-if="issues.length || saveError || conflict" class="card space-y-2" role="alert" data-testid="save-problems">
          <ul v-if="issues.length" class="list-inside list-disc text-sm text-danger-600">
            <li v-for="(i, n) in issues" :key="n">{{ issueText(i) }}</li>
          </ul>
          <p v-if="saveError" class="text-sm text-danger-600">{{ saveError }}</p>
          <ul v-if="saveDetails.length" class="list-inside list-disc text-sm text-danger-600">
            <li v-for="d in saveDetails" :key="d">{{ d }}</li>
          </ul>
          <div v-if="conflict" class="flex flex-wrap items-center gap-2 text-sm text-danger-600" data-testid="conflict">
            {{ t('blockEditor.conflict') }}
            <button class="btn-secondary py-1" type="button" @click="reloadAfterConflict">
              {{ t('blockEditor.reload') }}
            </button>
          </div>
        </div>
        <p v-if="justSaved && !dirty" class="text-sm text-green-700" role="status" data-testid="saved">
          {{ t('blockEditor.saved') }}
        </p>
        <ul v-if="warnings.length" class="card list-inside list-disc space-y-1 text-sm text-amber-800" data-testid="warnings">
          <li v-for="w in warnings" :key="w">{{ w }}</li>
        </ul>
      </div>

      <!-- Right: live preview + history ------------------------------------------------------ -->
      <div class="space-y-4">
        <div class="card space-y-3 lg:sticky lg:top-4">
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-medium">{{ t('blockEditor.preview') }}</h2>
            <span v-if="previewBusy" class="text-xs text-slate-500">{{ t('common.loading') }}</span>
          </div>

          <ul v-if="previewIssues.length" class="list-inside list-disc text-sm text-amber-800">
            <li v-for="(i, n) in previewIssues" :key="n">{{ issueText(i) }}</li>
          </ul>
          <ul v-if="problems.length" class="list-inside list-disc text-sm text-danger-600" data-testid="preview-errors">
            <li v-for="(p, n) in problems" :key="n">{{ p.message }}</li>
          </ul>
          <p v-if="previewError" class="text-sm text-danger-600" role="alert">{{ previewError }}</p>

          <!-- Fully sandboxed (no scripts, no same-origin): the block HTML is user content. -->
          <iframe
            v-if="preview?.ok"
            :srcdoc="preview.document"
            sandbox=""
            class="h-72 w-full rounded-md border border-surface-border bg-white"
            :title="t('blockEditor.preview')"
            data-testid="preview-frame"
          />
          <p v-else-if="!problems.length && !previewIssues.length" class="text-sm text-slate-500">
            {{ t('blockEditor.previewEmpty') }}
          </p>

          <p v-if="preview?.unresolved?.length" class="text-xs text-amber-800" data-testid="unresolved">
            {{ t('blockEditor.unresolved', { keys: preview.unresolved.join(', ') }) }}
          </p>

          <div class="space-y-1">
            <div class="flex items-center justify-between">
              <label class="label mb-0" for="be-sample">{{ t('blockEditor.sample') }}</label>
              <button class="btn-secondary py-1" type="button" :disabled="!preview?.ok" data-testid="fill-sample" @click="fillSample">
                {{ t('blockEditor.fillSample') }}
              </button>
            </div>
            <textarea
              id="be-sample"
              v-model="sampleText"
              class="input h-32 font-mono text-xs"
              spellcheck="false"
              :placeholder="SAMPLE_PLACEHOLDER"
              data-testid="sample"
            />
            <p v-if="sampleError" class="text-xs text-danger-600" role="alert">
              {{ sampleError === 'object' ? t('blockEditor.sampleNotObject', { example: SAMPLE_EXAMPLE }) : t('blockEditor.sampleBad', { message: sampleError }) }}
            </p>
            <p v-else class="text-xs text-slate-500">{{ t('blockEditor.sampleHelp') }}</p>
          </div>
        </div>

        <div v-if="versions.length" class="card space-y-2" data-testid="versions">
          <h2 class="text-sm font-medium">{{ t('blockEditor.history') }}</h2>
          <ul class="divide-y divide-surface-border text-sm">
            <li v-for="v in versions" :key="v.version" class="flex items-center justify-between gap-2 py-1.5">
              <span>
                v{{ v.version }}
                <span v-if="v.is_current" class="text-xs text-slate-500">({{ t('blockEditor.current') }})</span>
                <span class="ml-2 text-xs text-slate-500">{{ when(v.created_at) }}</span>
              </span>
              <button
                v-if="!v.is_current && !readOnly"
                class="btn-secondary py-1"
                type="button"
                data-testid="load-version"
                @click="loadVersion(v)"
              >
                {{ t('blockEditor.loadVersion') }}
              </button>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>
