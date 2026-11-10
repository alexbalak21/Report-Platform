<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { COLUMN_TYPES, type ColumnType, type SheetPreview } from '@/api/ingestion'
import { draftFromPreview, draftProblems, snake, type ProfileDraft } from '@/lib/profileDraft'

const props = defineProps<{ preview: SheetPreview; filename: string; busy?: boolean }>()
const emit = defineEmits<{ save: [draft: ProfileDraft] }>()

const { t } = useI18n()

const draft = reactive<ProfileDraft>(draftFromPreview(props.preview, props.filename))
// The key is tracked by Excel column so renaming a target never leaves a stale key behind.
const keySources = ref(new Set<string>())

watch(
  () => [props.preview.name, props.preview.header_row],
  () => {
    Object.assign(draft, draftFromPreview(props.preview, props.filename))
    keySources.value = new Set()
  },
)

const current = computed<ProfileDraft>(() => ({
  ...draft,
  key: draft.columns
    .filter((c) => c.include && keySources.value.has(c.source))
    .map((c) => c.target),
}))
const problems = computed(() => draftProblems(current.value))
const bad = (code: string) => problems.value.includes(code)
const dupTarget = (target: string) => problems.value.includes(`duplicate:${target}`)

function toggleKey(source: string, on: boolean) {
  const next = new Set(keySources.value)
  if (on) next.add(source)
  else next.delete(source)
  keySources.value = next
}

function normaliseTarget(c: { target: string }) {
  c.target = snake(c.target) || c.target
}

function normaliseTable() {
  draft.table = snake(draft.table) || draft.table
}
</script>

<template>
  <form
    class="space-y-4 rounded-lg border border-brand-100 bg-brand-50/40 p-4"
    data-testid="profile-mapper"
    @submit.prevent="!problems.length && emit('save', current)"
  >
    <h3 class="text-sm font-semibold">{{ t('mapper.title') }}</h3>

    <div class="grid gap-3 sm:grid-cols-2">
      <div>
        <label class="label" for="pm-name">{{ t('mapper.name') }}</label>
        <input
          id="pm-name"
          v-model="draft.name"
          class="input"
          :class="{ 'border-danger-500': bad('name') }"
          maxlength="120"
          required
        />
      </div>
      <div>
        <label class="label" for="pm-table">{{ t('mapper.table') }}</label>
        <input
          id="pm-table"
          v-model="draft.table"
          class="input font-mono"
          :class="{ 'border-danger-500': bad('table') }"
          maxlength="63"
          required
          @blur="normaliseTable"
        />
        <p class="mt-1 text-xs text-slate-500">{{ t('mapper.tableHint') }}</p>
      </div>
    </div>

    <div class="overflow-x-auto rounded-md border border-surface-border bg-white">
      <table class="w-full text-sm">
        <thead class="bg-surface-muted text-left text-slate-600">
          <tr>
            <th class="px-3 py-2">{{ t('mapper.include') }}</th>
            <th class="px-3 py-2">{{ t('mapper.source') }}</th>
            <th class="px-3 py-2">{{ t('mapper.target') }}</th>
            <th class="px-3 py-2">{{ t('mapper.type') }}</th>
            <th class="px-3 py-2">{{ t('mapper.required') }}</th>
            <th class="px-3 py-2" :title="t('mapper.keyHint')">{{ t('mapper.key') }}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-surface-border">
          <tr v-for="c in draft.columns" :key="c.source" :class="{ 'text-slate-400': !c.include }">
            <td class="px-3 py-1.5">
              <input
                v-model="c.include"
                type="checkbox"
                class="rounded border-surface-border text-brand-500"
                :aria-label="`${t('mapper.include')} ${c.source}`"
              />
            </td>
            <td class="px-3 py-1.5">{{ c.source }}</td>
            <td class="px-3 py-1.5">
              <input
                v-model="c.target"
                class="input w-48 py-1 font-mono"
                :class="{
                  'border-danger-500': c.include && (bad(`target:${c.source}`) || dupTarget(c.target)),
                }"
                :disabled="!c.include"
                :aria-label="`${t('mapper.target')} ${c.source}`"
                maxlength="63"
                @blur="normaliseTarget(c)"
              />
            </td>
            <td class="px-3 py-1.5">
              <select
                v-model="c.type"
                class="input w-36 py-1"
                :disabled="!c.include"
                :aria-label="`${t('mapper.type')} ${c.source}`"
              >
                <option v-for="ty in COLUMN_TYPES" :key="ty" :value="ty as ColumnType">
                  {{ t(`mapper.types.${ty}`) }}
                </option>
              </select>
            </td>
            <td class="px-3 py-1.5">
              <input
                v-model="c.required"
                type="checkbox"
                class="rounded border-surface-border text-brand-500"
                :disabled="!c.include"
                :aria-label="`${t('mapper.required')} ${c.source}`"
              />
            </td>
            <td class="px-3 py-1.5">
              <input
                type="checkbox"
                class="rounded border-surface-border text-brand-500"
                :checked="keySources.has(c.source)"
                :disabled="!c.include"
                :aria-label="`${t('mapper.key')} ${c.source}`"
                @change="toggleKey(c.source, ($event.target as HTMLInputElement).checked)"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="problems.length" class="text-sm text-danger-600" role="alert">
      {{ t('mapper.invalid') }}
    </p>
    <button class="btn-primary" type="submit" :disabled="busy || problems.length > 0">
      {{ t('mapper.save') }}
    </button>
  </form>
</template>
