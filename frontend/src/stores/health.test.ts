import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '@/api/health'
import { useHealthStore } from './health'

describe('health store', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('loads services', async () => {
    vi.spyOn(api, 'fetchHealth').mockResolvedValue({
      status: 'ok',
      services: [{ service: 'block', status: 'ready' }],
    })
    const s = useHealthStore()
    await s.refresh()
    expect(s.services).toHaveLength(1)
    expect(s.error).toBeNull()
    expect(s.loading).toBe(false)
  })

  it('records an error and clears services on failure', async () => {
    vi.spyOn(api, 'fetchHealth').mockRejectedValue(new Error('boom'))
    const s = useHealthStore()
    await s.refresh()
    expect(s.services).toEqual([])
    expect(s.error).toBe('boom')
  })
})
