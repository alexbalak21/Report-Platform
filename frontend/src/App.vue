<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { routes } from '@/router'
import { useAuthStore } from '@/stores/auth'

const { t, locale } = useI18n()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// Role-based menu: only entries the signed-in user may open.
const menu = computed(() =>
  routes.filter((r) => r.meta?.nav && (!r.meta.minRole || auth.hasRole(r.meta.minRole))),
)

async function signOut() {
  await auth.logout()
  await router.push({ name: 'login' })
}
</script>

<template>
  <RouterView v-if="route.meta.public" />
  <div v-else class="flex min-h-screen flex-col">
    <header class="border-b border-surface-border bg-white">
      <div class="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
        <span class="text-lg font-semibold text-brand-700">{{ t('app.title') }}</span>
        <nav class="flex flex-wrap gap-1" aria-label="Main">
          <RouterLink
            v-for="r in menu"
            :key="String(r.name)"
            :to="r.path"
            class="rounded-md px-3 py-1.5 text-sm text-slate-600 hover:bg-surface-muted"
            active-class="!bg-brand-50 !text-brand-700"
          >
            {{ t(r.meta!.nav!) }}
          </RouterLink>
        </nav>
        <div class="ml-auto flex items-center gap-2">
          <span v-if="auth.user" class="text-sm text-slate-600" data-testid="whoami">
            {{ auth.user.display_name }}
            <span class="ml-1 rounded bg-surface-muted px-1.5 py-0.5 text-xs">
              {{ t(`roles.${auth.user.role}`) }}
            </span>
          </span>
          <button
            class="btn-secondary"
            type="button"
            @click="locale = locale === 'fr' ? 'en' : 'fr'"
          >
            {{ locale.toUpperCase() }}
          </button>
          <button class="btn-secondary" type="button" data-testid="logout" @click="signOut">
            {{ t('nav.logout') }}
          </button>
        </div>
      </div>
    </header>
    <main class="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
      <RouterView />
    </main>
  </div>
</template>
