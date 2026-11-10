import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/auth'
import { ROLE_RANK, type Role, type User } from '@/api/auth'

/**
 * The access token lives in memory only (never localStorage): a page reload restores the
 * session through the HttpOnly refresh cookie via `init()`.
 */
export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const user = ref<User | null>(null)
  const ready = ref(false) // initial session restore finished (successfully or not)

  let refreshing: Promise<string> | null = null
  let initializing: Promise<void> | null = null

  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)

  function hasRole(min: Role): boolean {
    return !!user.value && ROLE_RANK[user.value.role] >= ROLE_RANK[min]
  }

  function apply(res: api.TokenResponse) {
    accessToken.value = res.access_token
    user.value = res.user
  }

  function clear() {
    accessToken.value = null
    user.value = null
  }

  async function login(email: string, password: string) {
    apply(await api.login(email, password))
  }

  /**
   * Refresh tokens are single-use and rotate, so concurrent callers MUST share one request:
   * a second parallel refresh would replay the old token and revoke the whole session.
   */
  function refresh(): Promise<string> {
    refreshing ??= api
      .refresh()
      .then((res) => {
        apply(res)
        return res.access_token
      })
      .catch((e) => {
        clear()
        throw e
      })
      .finally(() => {
        refreshing = null
      })
    return refreshing
  }

  function init(): Promise<void> {
    initializing ??= refresh()
      .then(() => undefined)
      .catch(() => undefined) // not logged in: fine
      .finally(() => {
        ready.value = true
      })
    return initializing
  }

  async function logout() {
    try {
      await api.logout()
    } finally {
      clear()
    }
  }

  return { accessToken, user, ready, isAuthenticated, hasRole, login, refresh, init, logout, clear }
})
