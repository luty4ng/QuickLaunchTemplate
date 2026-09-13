import { request } from '../../api/core'

export type Todo = {
  id: string
  title: string
  done: boolean
  created_at: string
  updated_at: string
}

export const todosApi = {
  list: () => request<Todo[]>('/api/todos'),
  create: (title: string) => request<Todo>('/api/todos', { method: 'POST', body: JSON.stringify({ title }) }),
  update: (id: string, patch: { title?: string; done?: boolean }) =>
    request<Todo>(`/api/todos/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  remove: (id: string) => request<void>(`/api/todos/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
