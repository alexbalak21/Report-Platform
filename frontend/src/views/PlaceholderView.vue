<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

const { t } = useI18n()
const route = useRoute()
</script>

<template>
  <section class="card text-center">
    <h1 class="text-base font-semibold">{{ t(String(route.meta.nav)) }}</h1>
    <p class="mt-1 text-sm text-slate-500">{{ t('soon') }}</p>
  </section>
</template>
