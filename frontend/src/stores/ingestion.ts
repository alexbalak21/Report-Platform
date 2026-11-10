import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/ingestion'
import { errorMessage } from '@/api/client'
import { toDefinition, type ProfileDraft } from '@/lib/profileDraft'

export type WizardStep = 'upload' | 'profile' | 'result'

/** Tests set this to 0 so polling doesn't wait. */
export const pollDelay = { ms: 1000, maxPolls: 600 }

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export const useIngestionStore = defineStore('ingestion', () => {
  const step = ref<WizardStep>('upload')
  const upload = ref<api.UploadInfo | null>(null)
  const progress = ref(0)
  const preview = ref<api.PreviewResponse | null>(null)
  const selectedProfileId = ref<string | null>(null)
  const run = ref<api.ImportRun | null>(null)
  const busy = ref(false)
  const error = ref<string | null>(null)
  // Bumped on reset so an old polling loop notices it is stale and stops.
  let generation = 0

  const suggestions = computed(() => preview.value?.suggested_profiles ?? [])
  const selectedProfile = computed(
    () => suggestions.value.find((p) => p.id === selectedProfileId.value) ?? null,
  )

  function reset() {
    generation++
    step.value = 'upload'
    upload.value = null
    progress.value = 0
    preview.value = null
    selectedProfileId.value = null
    run.value = null
    busy.value = false
    error.value = null
  }

  async function guarded<T>(fn: () => Promise<T>): Promise<T | undefined> {
    busy.value = true
    error.value = null
    try {
      return await fn()
    } catch (e) {
      error.value = errorMessage(e)
      return undefined
    } finally {
      busy.value = false
    }
  }

  function preselect() {
    const best = suggestions.value.find((p) => p.suggested)
    selectedProfileId.value = best?.id ?? selectedProfileId.value ?? null
    if (selectedProfileId.value && !selectedProfile.value) selectedProfileId.value = best?.id ?? null
  }

  async function submitFile(file: File) {
    reset()
    await guarded(async () => {
      upload.value = await api.uploadWorkbook(file, (f) => (progress.value = f))
      preview.value = await api.getPreview(upload.value.id)
      preselect()
      step.value = 'profile'
    })
  }

  /** Re-read the preview for another sheet or header row (also re-scores the profiles). */
  async function loadPreview(opts: { sheet?: string; headerRow?: number } = {}) {
    if (!upload.value) return
    await guarded(async () => {
      preview.value = await api.getPreview(upload.value!.id, opts)
      preselect()
    })
  }

  async function saveProfile(draft: ProfileDraft): Promise<api.ImportProfile | undefined> {
    if (!upload.value) return undefined
    return guarded(async () => {
      const created = await api.createProfile(draft.name.trim(), toDefinition(draft))
      preview.value = await api.getPreview(upload.value!.id, {
        sheet: draft.sheet,
        headerRow: draft.headerRow,
      })
      selectedProfileId.value = created.id
      return created
    })
  }

  async function runImport(dryRun = false) {
    if (!upload.value || !selectedProfileId.value) return
    const mine = ++generation
    const started = await guarded(() =>
      api.startImport(upload.value!.id, selectedProfileId.value!, dryRun),
    )
    if (!started) return
    run.value = started
    step.value = 'result'
    busy.value = true
    try {
      for (let i = 0; !api.isFinished(run.value!) && i < pollDelay.maxPolls; i++) {
        await sleep(pollDelay.ms)
        if (mine !== generation) return // reset or a newer import took over
        run.value = await api.getRun(run.value!.id)
      }
    } catch (e) {
      error.value = errorMessage(e)
    } finally {
      if (mine === generation) busy.value = false
    }
  }

  return {
    step,
    upload,
    progress,
    preview,
    selectedProfileId,
    selectedProfile,
    suggestions,
    run,
    busy,
    error,
    reset,
    submitFile,
    loadPreview,
    saveProfile,
    runImport,
  }
})
