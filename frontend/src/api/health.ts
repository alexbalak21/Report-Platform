import { http } from './client'

export type ServiceState = 'ready' | 'degraded' | 'down' | 'unknown'

export interface ServiceHealth {
  service: string
  status: ServiceState
  checks?: Record<string, string>
  error?: string
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  services: ServiceHealth[]
}

export async function fetchHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>('/health')
  return data
}
