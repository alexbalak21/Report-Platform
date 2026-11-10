<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  createProfile,
  deleteProfile,
  listProfiles,
  updateProfile,
  validateProfile,
  type ImportProfile,
  type ProfileDefinition,
} from '@/api/ingestion'
import { errorCode, errorDetails, errorMessage } from '@/api/client'

const { t } = useI18n()

const profiles = ref<ImportProfile[]>([])
const editing = ref<ImportProfile | 'new' | null>(null)
const name = ref('')
const json = ref('')
const error = ref<string | null>(null)
const details = ref<string[]>([])
const ok = ref(false)
const busy = ref(false)

const TEMPLATE: ProfileDefinition = {
  match: { sheet: 'Rapport', required_headers: ['lot'] },
  header_row: 1,
  tables: [
    {
      table: 'samples',
      key: [],
      columns: [{ source: 'lot', target: 'lot', type: 'string' }],
    },
  ],
}

const isNew = computed(() => editing.value === 'new')

function clearFeedback() {
  error.value = null
  details.value = []
  ok.value = false
}

async function load() {
  try {
    profiles.value = await listProfiles()
  } catch (e) {
    error.value = errorMessage(e)
  }
}

function edit(p: ImportProfile | 'new') {
  clearFeedback()
  editing.value = p
  name.value = p === 'new' ? '' : p.name
  json.value = JSON.stringify(p === 'new' ? TEMPLATE : p.definition, null, 2)
}

function parse(): ProfileDefinition | null {
  try {
    return JSON.parse(json.value) as ProfileDefinition
  } catch (e) {
    error.value = t('profilesPage.badJson', { message: e instanceof Error ? e.message : String(e) })
    return null
  }
}

async function run(action: (def: ProfileDefinition) => Promise<void>) {
  clearFeedback()
  const def = parse()
  if (!def) return
  busy.value = true
  try {
    await action(def)
  } catch (e) {
    error.value = errorCode(e) === 'CONFLICT' ? t('profilesPage.conflict') : errorMessage(e)
    details.value = errorDetails(e)
  } finally {
    busy.value = false
  }
}

const validate = () =>
  run(async (def) => {
    await validateProfile(def)
    ok.value = true
  })

const save = () =>
  run(async (def) => {
    const current = editing.value
    if (current === 'new') await createProfile(name.value.trim(), def)
    else if (current) await updateProfile(current.id, current.version, name.value.trim(), def)
    editing.value = null
    await load()
  })

async function remove(p: ImportProfile) {
  if (!window.confirm(t('profilesPage.confirmDelete', { name: p.name }))) return
  clearFeedback()
  try {
    await deleteProfile(p.id)
    if (editing.value !== 'new' && editing.value?.id === p.id) editing.value = null
    await load()
  } catch (e) {
    error.value = errorMessage(e)
  }
}

const when = (iso: string) => new Date(iso).toLocaleString()
onMounted(load)
</script>

<template>
  <section class="space-y-4">
    <div class="flex items-center justify-between">
      <h1 class="text-lg font-semibold">{{ t('profilesPage.title') }}</h1>
      <button class="btn-primary" type="button" data-testid="new-profile" @click="edit('new')">
        {{ t('profilesPage.new') }}
      </button>
    </div>
    <p class="text-sm text-slate-500">{{ t('profilesPage.help') }}</p>

    <div v-if="editing" class="card space-y-3" data-testid="profile-editor">
      <div>
        <label class="label" for="p-name">{{ t('profilesPage.name') }}</label>
        <input id="p-name" v-model="name" class="input max-w-md" maxlength="120" required />
      </div>
      <div>
        <label class="label" for="p-json">{{ t('profilesPage.definition') }}</label>
        <textarea
          id="p-json"
          v-model="json"
          class="input h-96 font-mono text-xs"
          spellcheck="false"
          data-testid="json"
        />
      </div>
      <p v-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>
      <ul v-if="details.length" class="list-inside list-disc text-sm text-danger-600">
        <li v-for="d in details" :key="d">{{ d }}</li>
      </ul>
      <p v-if="ok" class="text-sm text-green-700" role="status">{{ t('profilesPage.valid') }}</p>
      <div class="flex gap-2">
        <button class="btn-secondary" type="button" :disabled="busy" @click="validate">
          {{ t('profilesPage.validate') }}
        </button>
        <button class="btn-primary" type="button" :disabled="busy || !name.trim()" @click="save">
          {{ t('profilesPage.save') }}
        </button>
        <button class="btn-secondary" type="button" @click="editing = null">
          {{ t('profilesPage.cancel') }}
        </button>
      </div>
      <p v-if="!isNew" class="text-xs text-slate-500">
        v{{ (editing as ImportProfile).version }}
      </p>
    </div>
    <p v-else-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>

    <div class="card overflow-x-auto p-0">
      <table class="w-full text-sm" data-testid="profiles-table">
        <thead class="bg-surface-muted text-left text-slate-600">
          <tr>
            <th class="px-4 py-2">{{ t('profilesPage.name') }}</th>
            <th class="px-4 py-2">{{ t('profilesPage.version') }}</th>
            <th class="px-4 py-2">{{ t('profilesPage.tables') }}</th>
            <th class="px-4 py-2">{{ t('profilesPage.updated') }}</th>
            <th class="px-4 py-2" />
          </tr>
        </thead>
        <tbody class="divide-y divide-surface-border">
          <tr v-for="p in profiles" :key="p.id">
            <td class="px-4 py-2 font-medium">{{ p.name }}</td>
            <td class="px-4 py-2">{{ p.version }}</td>
            <td class="px-4 py-2 font-mono text-xs">
              {{ p.definition.tables.map((x) => x.table).join(', ') }}
            </td>
            <td class="px-4 py-2">{{ when(p.updated_at) }}</td>
            <td class="space-x-2 whitespace-nowrap px-4 py-2 text-right">
              <button class="btn-secondary py-1" type="button" @click="edit(p)">
                {{ t('profilesPage.edit') }}
              </button>
              <button class="btn-secondary py-1 text-danger-600" type="button" @click="remove(p)">
                {{ t('profilesPage.delete') }}
              </button>
            </td>
          </tr>
          <tr v-if="!profiles.length">
            <td colspan="5" class="px-4 py-6 text-center text-slate-500">
              {{ t('profilesPage.empty') }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
