import { useEffect, useState } from 'react'

import { ApiError, describeError } from '../../api/core'
import { publishDataChanged } from '../bus'
import type { FeatureProps } from '../types'
import { TodoList } from './TodoList'
import { todosApi, type Todo } from './api'

/**
 * The todos panel owns its data end to end: loading, mutating, and deciding what
 * a failure means. The shell only hears about errors and dead sessions.
 */
export function TodosPanel({ onError, onUnauthorized }: FeatureProps) {
  const [todos, setTodos] = useState<Todo[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const items = await todosApi.list()
        if (!cancelled) setTodos(items)
      } catch (cause) {
        if (cancelled) return
        if (cause instanceof ApiError && cause.status === 401) {
          onUnauthorized()
          return
        }
        onError(describeError(cause))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [onError, onUnauthorized])

  const create = async (title: string) => {
    onError(null)
    try {
      const created = await todosApi.create(title)
      setTodos((current) => [created, ...current])
      // The quota moved, so anything showing it must re-read.
      publishDataChanged()
    } catch (cause) {
      onError(describeError(cause))
      if (cause instanceof ApiError && cause.status === 402) publishDataChanged()
    }
  }

  const patch = async (id: string, changes: { title?: string; done?: boolean }) => {
    onError(null)
    try {
      const updated = await todosApi.update(id, changes)
      setTodos((current) => current.map((todo) => (todo.id === id ? updated : todo)))
    } catch (cause) {
      onError(describeError(cause))
    }
  }

  const remove = async (id: string) => {
    onError(null)
    try {
      await todosApi.remove(id)
      setTodos((current) => current.filter((todo) => todo.id !== id))
      publishDataChanged()
    } catch (cause) {
      onError(describeError(cause))
    }
  }

  // The shell stops showing its own spinner as soon as the session resolves, so
  // the list keeps the old "Loading..." placeholder while the first read is out.
  if (loading) return <div className="card empty">Loading...</div>

  return (
    <div className="card">
      <TodoList
        todos={todos}
        remaining={todos.filter((todo) => !todo.done).length}
        onCreate={create}
        onPatch={patch}
        onDelete={remove}
      />
    </div>
  )
}
