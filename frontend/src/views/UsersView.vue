<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ROLES, type Role, type User } from '@/api/auth'
import { errorMessage } from '@/api/client'
import { createUser, listUsers, updateUser, type UserPatch } from '@/api/users'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()
const auth = useAuthStore()

const users = ref<User[]>([])
const error = ref<string | null>(null)
const busy = ref(false)
const form = reactive({ email: '', display_name: '', password: '', role: 'operator' as Role })

async function load() {
  try {
    users.value = await listUsers()
  } catch (e) {
    error.value = errorMessage(e)
  }
}

async function run(action: () => Promise<unknown>) {
  busy.value = true
  error.value = null
  try {
    await action()
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    await load() // also resets any <select> the server refused to change
    busy.value = false
  }
}

const create = () =>
  run(async () => {
    await createUser({ ...form })
    Object.assign(form, { email: '', display_name: '', password: '', role: 'operator' })
  })

const patch = (u: User, p: UserPatch) => run(() => updateUser(u.id, p))

onMounted(load)
</script>

<template>
  <section class="space-y-6">
    <h1 class="text-lg font-semibold">{{ t('users.title') }}</h1>
    <p v-if="error" class="text-sm text-danger-600" role="alert">{{ error }}</p>

    <div class="card overflow-x-auto p-0">
      <table class="w-full text-sm" data-testid="users-table">
        <thead class="bg-surface-muted text-left text-slate-600">
          <tr>
            <th class="px-4 py-2">{{ t('users.name') }}</th>
            <th class="px-4 py-2">{{ t('login.email') }}</th>
            <th class="px-4 py-2">{{ t('users.role') }}</th>
            <th class="px-4 py-2">{{ t('users.active') }}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-surface-border">
          <tr v-for="u in users" :key="u.id" :class="{ 'text-slate-400': !u.is_active }">
            <td class="px-4 py-2 font-medium">{{ u.display_name }}</td>
            <td class="px-4 py-2">{{ u.email }}</td>
            <td class="px-4 py-2">
              <select
                class="input w-44 py-1"
                :aria-label="t('users.role')"
                :value="u.role"
                :disabled="busy"
                @change="patch(u, { role: ($event.target as HTMLSelectElement).value as Role })"
              >
                <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
              </select>
            </td>
            <td class="px-4 py-2">
              <input
                type="checkbox"
                class="rounded border-surface-border text-brand-500"
                :aria-label="t('users.active')"
                :checked="u.is_active"
                :disabled="busy || u.id === auth.user?.id"
                @change="patch(u, { is_active: ($event.target as HTMLInputElement).checked })"
              />
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <form class="card grid gap-3 sm:grid-cols-2" data-testid="user-form" @submit.prevent="create">
      <h2 class="text-base font-semibold sm:col-span-2">{{ t('users.add') }}</h2>
      <div>
        <label class="label" for="u-name">{{ t('users.name') }}</label>
        <input id="u-name" v-model="form.display_name" class="input" required maxlength="100" />
      </div>
      <div>
        <label class="label" for="u-email">{{ t('login.email') }}</label>
        <input id="u-email" v-model="form.email" class="input" type="email" required />
      </div>
      <div>
        <label class="label" for="u-pass">{{ t('users.password') }}</label>
        <input
          id="u-pass"
          v-model="form.password"
          class="input"
          type="password"
          autocomplete="new-password"
          minlength="10"
          required
        />
        <p class="mt-1 text-xs text-slate-500">{{ t('users.passwordHint') }}</p>
      </div>
      <div>
        <label class="label" for="u-role">{{ t('users.role') }}</label>
        <select id="u-role" v-model="form.role" class="input">
          <option v-for="r in ROLES" :key="r" :value="r">{{ t(`roles.${r}`) }}</option>
        </select>
      </div>
      <div class="sm:col-span-2">
        <button class="btn-primary" type="submit" :disabled="busy">{{ t('users.create') }}</button>
      </div>
    </form>
  </section>
</template>
