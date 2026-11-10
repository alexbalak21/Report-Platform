<script setup lang="ts">
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useHealthStore } from '@/stores/health'

const { t } = useI18n()
const health = useHealthStore()

onMounted(health.refresh)

const dot: Record<string, string> = {
  ready: 'bg-ok-500',
  degraded: 'bg-amber-500',
  down: 'bg-danger-500',
  unknown: 'bg-slate-400',
}
</script>

<template>
  <section class="card" data-testid="status-card">
    <div class="mb-3 flex items-center justify-between">
      <h1 class="text-base font-semibold">{{ t('status.title') }}</h1>
      <button class="btn-secondary" type="button" :disabled="health.loading" @click="health.refresh">
        {{ t('status.refresh') }}
      </button>
    </div>
    <p v-if="health.error" class="text-sm text-danger-600" role="alert">{{ health.error }}</p>
    <ul class="divide-y divide-surface-border">
      <li
        v-for="s in health.services"
        :key="s.service"
        class="flex items-center justify-between py-2 text-sm"
      >
        <span class="font-medium capitalize">{{ s.service }}</span>
        <span class="flex items-center gap-2">
          <span class="h-2.5 w-2.5 rounded-full" :class="dot[s.status] ?? dot.unknown" />
          {{ t(`status.${s.status}`) }}
        </span>
      </li>
    </ul>
  </section>
</template>
