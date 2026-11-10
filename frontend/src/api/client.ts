import axios, { type InternalAxiosRequestConfig } from 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    /** Auth endpoints: never attach the bearer token and never try a refresh-and-retry. */
    skipAuth?: boolean
    _retried?: boolean
  }
}

// All calls go through the gateway (proxied by Vite in dev, nginx in prod).
// withCredentials lets the browser send the HttpOnly refresh cookie to /api/auth.
export const http = axios.create({ baseURL: '/api', timeout: 15000, withCredentials: true })

export interface AuthHooks {
  getToken: () => string | null
  /** Resolves with a fresh access token; rejects when the session is gone. */
  refresh: () => Promise<string>
  onAuthLost: () => void
}

let hooks: AuthHooks | null = null

/** Registered once at start-up (avoids an import cycle between the client and the auth store). */
export function setAuthHooks(h: AuthHooks | null) {
  hooks = h
}

http.interceptors.request.use((config) => {
  config.headers.set('X-Correlation-Id', crypto.randomUUID())
  const token = hooks?.getToken()
  // Never overwrite an explicit header: the 401 retry below sets the fresh token itself.
  if (token && !config.skipAuth && !config.headers.has('Authorization')) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

http.interceptors.response.use(undefined, async (error: unknown) => {
  if (!axios.isAxiosError(error)) throw error
  const config = error.config as InternalAxiosRequestConfig | undefined
  if (error.response?.status !== 401 || !config || config.skipAuth || config._retried || !hooks) {
    throw error
  }
  config._retried = true
  try {
    const token = await hooks.refresh()
    config.headers.set('Authorization', `Bearer ${token}`)
  } catch {
    hooks.onAuthLost()
    throw error
  }
  return http(config)
})

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown[]; correlation_id: string | null }
}

export function errorCode(err: unknown): string | undefined {
  return axios.isAxiosError<ApiErrorBody>(err) ? err.response?.data?.error?.code : undefined
}

export function errorMessage(err: unknown): string {
  if (axios.isAxiosError<ApiErrorBody>(err)) {
    return err.response?.data?.error?.message ?? err.message
  }
  return err instanceof Error ? err.message : String(err)
}

/** Per-field problems from a 400/422 (`{path, message}` items) as readable lines. */
export function errorDetails(err: unknown): string[] {
  if (!axios.isAxiosError<ApiErrorBody>(err)) return []
  const out: string[] = []
  for (const d of err.response?.data?.error?.details ?? []) {
    if (typeof d === 'string') out.push(d)
    else if (d && typeof d === 'object') {
      const { path, message } = d as { path?: unknown; message?: unknown }
      if (typeof message === 'string') out.push(path ? `${String(path)}: ${message}` : message)
    }
  }
  return out
}
