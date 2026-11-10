<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { errorCode, errorMessage } from '@/api/client'
import { safeRedirect } from '@/router'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = null
  try {
    await auth.login(email.value, password.value)
    await router.replace(safeRedirect(route.query.redirect))
  } catch (e) {
    const code = errorCode(e)
    error.value =
      code === 'INVALID_CREDENTIALS'
        ? t('login.invalid')
        : code === 'RATE_LIMITED'
          ? t('login.rateLimited')
          : errorMessage(e)
    password.value = ''
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main class="flex min-h-screen items-center justify-center bg-surface-muted px-4">
    <form class="card w-full max-w-sm space-y-4" data-testid="login-form" @submit.prevent="submit">
      <h1 class="text-lg font-semibold text-brand-700">{{ t('app.title') }}</h1>
      <div>
        <label class="label" for="email">{{ t('login.email') }}</label>
        <input
          id="email"
          v-model="email"
          class="input"
          type="email"
          autocomplete="username"
          required
          autofocus
        />
      </div>
      <div>
        <label class="label" for="password">{{ t('login.password') }}</label>
        <input
          id="password"
          v-model="password"
          class="input"
          type="password"
          autocomplete="current-password"
          required
        />
      </div>
      <p v-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>
      <button class="btn-primary w-full" type="submit" :disabled="busy">
        {{ t('login.submit') }}
      </button>
    </form>
  </main>
</template>
