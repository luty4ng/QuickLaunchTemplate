import { useState } from 'react'

import type { Todo } from './api'

export function TodoList({
  todos,
  remaining,
  onCreate,
  onPatch,
  onDelete,
}: {
  todos: Todo[]
  remaining: number
  onCreate: (title: string) => Promise<void>
  onPatch: (id: string, patch: { title?: string; done?: boolean }) => Promise<void>
  onDelete: (id: string) => Promise<void>
}) {
  const [draft, setDraft] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [busy, setBusy] = useState(false)

  const submitNew = async (event: React.FormEvent) => {
    event.preventDefault()
    const title = draft.trim()
    if (!title) return
    setBusy(true)
    try {
      await onCreate(title)
      setDraft('')
    } finally {
      setBusy(false)
    }
  }

  const commitEdit = async (todo: Todo) => {
    const title = editingTitle.trim()
    setEditingId(null)
    if (title && title !== todo.title) await onPatch(todo.id, { title })
  }

  return (
    <div className="stack">
      <form className="row" onSubmit={submitNew}>
        <input
          type="text"
          placeholder="What needs doing?"
          aria-label="New todo title"
          maxLength={200}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" disabled={busy || !draft.trim()}>
          Add
        </button>
      </form>

      <p className="muted" style={{ margin: 0 }}>
        {todos.length === 0
          ? 'Nothing yet.'
          : `${remaining} open of ${todos.length} total`}
      </p>

      {todos.length === 0 ? (
        <p className="empty">Add your first todo above.</p>
      ) : (
        <ul className="todos">
          {todos.map((todo) => (
            <li key={todo.id} className={`todo${todo.done ? ' done' : ''}`}>
              <input
                type="checkbox"
                checked={todo.done}
                aria-label={`Mark "${todo.title}" as ${todo.done ? 'open' : 'done'}`}
                onChange={(event) => void onPatch(todo.id, { done: event.target.checked })}
              />

              {editingId === todo.id ? (
                <input
                  type="text"
                  autoFocus
                  maxLength={200}
                  value={editingTitle}
                  aria-label="Edit todo title"
                  onChange={(event) => setEditingTitle(event.target.value)}
                  onBlur={() => void commitEdit(todo)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void commitEdit(todo)
                    if (event.key === 'Escape') setEditingId(null)
                  }}
                />
              ) : (
                <span
                  className="title"
                  onDoubleClick={() => {
                    setEditingId(todo.id)
                    setEditingTitle(todo.title)
                  }}
                >
                  {todo.title}
                </span>
              )}

              <button
                className="ghost"
                aria-label={`Edit "${todo.title}"`}
                onClick={() => {
                  setEditingId(todo.id)
                  setEditingTitle(todo.title)
                }}
              >
                Edit
              </button>
              <button
                className="ghost"
                aria-label={`Delete "${todo.title}"`}
                onClick={() => void onDelete(todo.id)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
