import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { errorCode, errorDetails, errorMessage, http, setAuthHooks } from './client'

type Reply = { status: number; data?: unknown }

/** Replace the network: `script` decides the reply for each outgoing request. */
function fakeNetwork(script: (cfg: InternalAxiosRequestConfig, n: number) => Reply) {
  const seen: InternalAxiosRequestConfig[] = []
  const adapter: AxiosAdapter = async (config) => {
    seen.push(config)
    const { status, data } = script(config, seen.length)
    const response = { data: data ?? {}, status, statusText: '', headers: {}, config }
    if (status >= 400) throw new AxiosError('fail', 'ERR', config, null, response)
    return response
  }
  http.defaults.adapter = adapter
  return seen
}

describe('http client', () => {
  const hooks = {
    getToken: vi.fn<() => string | null>(() => 'old'),
    refresh: vi.fn<() => Promise<string>>(),
    onAuthLost: vi.fn(),
  }

  beforeEach(() => {
    vi.resetAllMocks()
    hooks.getToken.mockReturnValue('old')
    setAuthHooks(hooks)
  })
  afterEach(() => setAuthHooks(null))

  it('attaches the bearer token and a correlation id', async () => {
    const seen = fakeNetwork(() => ({ status: 200 }))
    await http.get('/things')
    expect(seen[0].headers.get('Authorization')).toBe('Bearer old')
    expect(seen[0].headers.get('X-Correlation-Id')).toBeTruthy()
  })

  it('skipAuth requests carry no token and are never retried', async () => {
    const seen = fakeNetwork(() => ({ status: 401 }))
    await expect(http.post('/auth/login', {}, { skipAuth: true })).rejects.toBeTruthy()
    expect(seen).toHaveLength(1)
    expect(seen[0].headers.get('Authorization')).toBeFalsy()
    expect(hooks.refresh).not.toHaveBeenCalled()
  })

  it('on 401: refreshes once and replays the request with the new token', async () => {
    hooks.refresh.mockResolvedValue('fresh')
    const seen = fakeNetwork((_c, n) => (n === 1 ? { status: 401 } : { status: 200, data: { ok: 1 } }))
    const res = await http.get('/reports')
    expect(res.data).toEqual({ ok: 1 })
    expect(hooks.refresh).toHaveBeenCalledTimes(1)
    expect(seen).toHaveLength(2)
    expect(seen[1].headers.get('Authorization')).toBe('Bearer fresh')
  })

  it('a second 401 after the retry is surfaced, not looped', async () => {
    hooks.refresh.mockResolvedValue('fresh')
    const seen = fakeNetwork(() => ({ status: 401 }))
    await expect(http.get('/reports')).rejects.toBeTruthy()
    expect(seen).toHaveLength(2)
    expect(hooks.refresh).toHaveBeenCalledTimes(1)
  })

  it('if the refresh fails the session is declared lost', async () => {
    hooks.refresh.mockRejectedValue(new Error('expired'))
    fakeNetwork(() => ({ status: 401 }))
    await expect(http.get('/reports')).rejects.toBeTruthy()
    expect(hooks.onAuthLost).toHaveBeenCalledTimes(1)
  })

  it('non-401 errors pass straight through', async () => {
    const seen = fakeNetwork(() => ({
      status: 409,
      data: { error: { code: 'LAST_ADMIN', message: 'nope', details: [], correlation_id: null } },
    }))
    const err = await http.patch('/users/1', {}).catch((e) => e)
    expect(seen).toHaveLength(1)
    expect(hooks.refresh).not.toHaveBeenCalled()
    expect(errorCode(err)).toBe('LAST_ADMIN')
    expect(errorMessage(err)).toBe('nope')
  })
})

describe('errorDetails', () => {
  const failure = (details: unknown[]) => {
    const config = { headers: {} } as InternalAxiosRequestConfig
    const response = {
      data: { error: { code: 'VALIDATION_FAILED', message: 'bad', details, correlation_id: null } },
      status: 422,
      statusText: '',
      headers: {},
      config,
    }
    return new AxiosError('fail', 'ERR', config, null, response)
  }

  it('turns path/message pairs and plain strings into lines', () => {
    expect(
      errorDetails(failure([{ path: 'tables.0.table', message: 'must be lowercase' }, 'plain', { message: 'no path' }, 42])),
    ).toEqual(['tables.0.table: must be lowercase', 'plain', 'no path'])
  })

  it('is empty for non-API errors', () => {
    expect(errorDetails(new Error('x'))).toEqual([])
  })
})
