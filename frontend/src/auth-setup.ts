import type { Router } from 'vue-router'
import { setAuthHooks } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

/** Connect the HTTP client to the auth store. Call once, after Pinia is installed. */
export function installAuth(router: Router) {
  const auth = useAuthStore()
  setAuthHooks({
    getToken: () => auth.accessToken,
    refresh: () => auth.refresh(),
    onAuthLost: () => {
      auth.clear()
      const here = router.currentRoute.value
      if (here.name !== 'login') {
        void router.push({ name: 'login', query: { redirect: here.fullPath } })
      }
    },
  })
}
