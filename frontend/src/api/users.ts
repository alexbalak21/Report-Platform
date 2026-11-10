import { http } from './client'
import type { Role, User } from './auth'

export interface NewUser {
  email: string
  display_name: string
  password: string
  role: Role
}

export type UserPatch = Partial<Pick<User, 'display_name' | 'role' | 'is_active'>> & {
  password?: string
}

export async function listUsers(): Promise<User[]> {
  const { data } = await http.get<{ items: User[] }>('/users')
  return data.items
}

export async function createUser(body: NewUser): Promise<User> {
  return (await http.post<User>('/users', body)).data
}

export async function updateUser(id: string, patch: UserPatch): Promise<User> {
  return (await http.patch<User>(`/users/${id}`, patch)).data
}
