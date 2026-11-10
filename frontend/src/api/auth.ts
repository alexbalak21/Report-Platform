import { http } from './client'

export type Role = 'viewer' | 'operator' | 'designer' | 'admin'

/** viewer < operator < designer < admin (same hierarchy as the gateway policy). */
export const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, designer: 2, admin: 3 }
export const ROLES = Object.keys(ROLE_RANK) as Role[]

export interface User {
  id: string
  email: string
  display_name: string
  role: Role
  is_active: boolean
  created_at: string
  last_login: string | null
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  expires_in: number
  user: User
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>('/auth/login', { email, password }, { skipAuth: true })
  return data
}

export async function refresh(): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>('/auth/refresh', null, { skipAuth: true })
  return data
}

export async function logout(): Promise<void> {
  await http.post('/auth/logout', null, { skipAuth: true })
}
