import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/api/auth'
import type { TokenResponse, User } from '@/api/auth'
import { useAuthStore } from './auth'

const user = (role: User['role'] = 'operator'): User => ({
  id: '1',
  email: 'a@example.com',
  display_name: 'A',
  role,
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login: null,
})
const tokens = (access = 'tok', role: User['role'] = 'operator'): TokenResponse => ({
  access_token: access,
  token_type: 'bearer',
  expires_in: 900,
  user: user(role),
})

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('login stores the token and user in memory', async () => {
    vi.spyOn(api, 'login').mockResolvedValue(tokens())
    const s = useAuthStore()
    await s.login('a@example.com', 'pw')
    expect(s.isAuthenticated).toBe(true)
    expect(s.accessToken).toBe('tok')
    expect(localStorage.length).toBe(0) // never persisted
  })

  it('a failed login leaves the store signed out', async () => {
    vi.spyOn(api, 'login').mockRejectedValue(new Error('401'))
    const s = useAuthStore()
    await expect(s.login('a@example.com', 'bad')).rejects.toThrow()
    expect(s.isAuthenticated).toBe(false)
  })

  it('concurrent refreshes share ONE request (tokens rotate: a second call would revoke the session)', async () => {
    let resolve!: (t: TokenResponse) => void
    const spy = vi.spyOn(api, 'refresh').mockReturnValue(new Promise((r) => (resolve = r)))
    const s = useAuthStore()
    const [a, b, c] = [s.refresh(), s.refresh(), s.refresh()]
    resolve(tokens('new'))
    expect(await Promise.all([a, b, c])).toEqual(['new', 'new', 'new'])
    expect(spy).toHaveBeenCalledTimes(1)
    spy.mockResolvedValue(tokens('newer'))
    expect(await s.refresh()).toBe('newer') // a later refresh is a fresh request
    expect(spy).toHaveBeenCalledTimes(2)
  })

  it('a failed refresh clears the session', async () => {
    vi.spyOn(api, 'login').mockResolvedValue(tokens())
    vi.spyOn(api, 'refresh').mockRejectedValue(new Error('401'))
    const s = useAuthStore()
    await s.login('a@example.com', 'pw')
    await expect(s.refresh()).rejects.toThrow()
    expect(s.isAuthenticated).toBe(false)
  })

  it('init restores a session once, and swallows "not logged in"', async () => {
    const spy = vi.spyOn(api, 'refresh').mockResolvedValue(tokens())
    const s = useAuthStore()
    await Promise.all([s.init(), s.init()])
    await s.init()
    expect(spy).toHaveBeenCalledTimes(1)
    expect(s.ready && s.isAuthenticated).toBe(true)

    setActivePinia(createPinia())
    vi.spyOn(api, 'refresh').mockRejectedValue(new Error('401'))
    const fresh = useAuthStore()
    await expect(fresh.init()).resolves.toBeUndefined()
    expect(fresh.ready).toBe(true)
    expect(fresh.isAuthenticated).toBe(false)
  })

  it('logout clears local state even if the request fails', async () => {
    vi.spyOn(api, 'login').mockResolvedValue(tokens())
    vi.spyOn(api, 'logout').mockRejectedValue(new Error('network'))
    const s = useAuthStore()
    await s.login('a@example.com', 'pw')
    await expect(s.logout()).rejects.toThrow()
    expect(s.isAuthenticated).toBe(false)
  })

  it('role hierarchy', async () => {
    vi.spyOn(api, 'login').mockResolvedValue(tokens('t', 'designer'))
    const s = useAuthStore()
    expect(s.hasRole('viewer')).toBe(false) // signed out: nothing
    await s.login('a@example.com', 'pw')
    expect(s.hasRole('operator')).toBe(true)
    expect(s.hasRole('designer')).toBe(true)
    expect(s.hasRole('admin')).toBe(false)
  })
})
