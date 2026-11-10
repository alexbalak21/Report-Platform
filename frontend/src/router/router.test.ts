import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/api/auth'
import type { Role } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { createAppRouter, safeRedirect } from './index'

function session(role: Role | null) {
  vi.restoreAllMocks()
  if (role) {
    vi.spyOn(api, 'refresh').mockResolvedValue({
      access_token: 't',
      token_type: 'bearer',
      expires_in: 900,
      user: {
        id: '1',
        email: 'a@example.com',
        display_name: 'A',
        role,
        is_active: true,
        created_at: '',
        last_login: null,
      },
    })
  } else {
    vi.spyOn(api, 'refresh').mockRejectedValue(new Error('401'))
  }
}

async function go(path: string) {
  const router = createAppRouter(createMemoryHistory())
  await router.push(path)
  return router.currentRoute.value
}

describe('route guards', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('anonymous users are sent to login, remembering where they wanted to go', async () => {
    session(null)
    const r = await go('/reports?tab=2')
    expect(r.name).toBe('login')
    expect(r.query.redirect).toBe('/reports?tab=2')
  })

  it('signed-in users skip the login page', async () => {
    session('viewer')
    expect((await go('/login')).name).toBe('home')
  })

  it.each([
    ['viewer', '/reports', 'reports'],
    ['viewer', '/data/upload', 'home'],
    ['operator', '/data/upload', 'upload'],
    ['operator', '/blocks', 'home'],
    ['designer', '/templates', 'templates'],
    ['designer', '/admin/users', 'home'],
    ['admin', '/admin/users', 'users'],
  ] as [Role, string, string][])('%s opening %s lands on %s', async (role, path, expected) => {
    session(role)
    expect((await go(path)).name).toBe(expected)
  })

  it('unknown paths fall back to home', async () => {
    session('viewer')
    expect((await go('/nope/nothing')).name).toBe('home')
  })

  it('restores the session once, not on every navigation', async () => {
    session('admin')
    const router = createAppRouter(createMemoryHistory())
    await router.push('/reports')
    await router.push('/blocks')
    await router.push('/admin/users')
    expect(api.refresh).toHaveBeenCalledTimes(1)
    expect(useAuthStore().isAuthenticated).toBe(true)
  })
})

describe('safeRedirect', () => {
  it.each([
    ['/reports', '/reports'],
    ['/a?b=1#c', '/a?b=1#c'],
    ['//evil.com', '/'],
    ['https://evil.com', '/'],
    ['javascript:alert(1)', '/'],
    ['', '/'],
    [undefined, '/'],
    [['/x'], '/'],
  ])('%j -> %s', (input, expected) => {
    expect(safeRedirect(input)).toBe(expected)
  })
})
