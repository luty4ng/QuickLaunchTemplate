"""Todo CRUD.

Isolation rule, enforced on every statement below: the query itself carries
`user_id = <caller>`. We never load a row by id and then check ownership,
because that pattern is one forgotten `if` away from a data leak. A row that
belongs to somebody else is indistinguishable from a row that does not exist:
both answer 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import delete, select, update

from app.db.base import utcnow
from app.db.models import Todo
from app.deps import CurrentUser, SessionDep, api_error
from app.schemas import TodoCreate, TodoOut, TodoUpdate

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("", response_model=list[TodoOut])
async def list_todos(user: CurrentUser, session: SessionDep) -> list[Todo]:
    rows = await session.scalars(
        select(Todo).where(Todo.user_id == user.id).order_by(Todo.created_at.desc(), Todo.id.desc())
    )
    return list(rows)


@router.post("", response_model=TodoOut, status_code=status.HTTP_201_CREATED)
async def create_todo(payload: TodoCreate, user: CurrentUser, session: SessionDep) -> Todo:
    # The schema already trimmed the title; store exactly what was validated.
    todo = Todo(user_id=user.id, title=payload.title)
    session.add(todo)
    await session.commit()
    await session.refresh(todo)
    return todo


@router.patch("/{todo_id}", response_model=TodoOut)
async def update_todo(todo_id: str, payload: TodoUpdate, user: CurrentUser, session: SessionDep) -> Todo:
    values = payload.model_dump(exclude_unset=True)
    if not values or all(v is None for v in values.values()):
        raise api_error("empty_update", "Provide a title or a done flag.", status.HTTP_400_BAD_REQUEST)
    values["updated_at"] = utcnow()

    result = await session.execute(
        update(Todo).where(Todo.id == todo_id, Todo.user_id == user.id).values(**values)
    )
    if result.rowcount == 0:
        await session.rollback()
        raise api_error("not_found", "Todo not found.", status.HTTP_404_NOT_FOUND)
    await session.commit()
    todo = await session.scalar(select(Todo).where(Todo.id == todo_id, Todo.user_id == user.id))
    assert todo is not None  # rowcount > 0 and we just committed it
    return todo


@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: str, user: CurrentUser, session: SessionDep) -> Response:
    result = await session.execute(delete(Todo).where(Todo.id == todo_id, Todo.user_id == user.id))
    if result.rowcount == 0:
        await session.rollback()
        raise api_error("not_found", "Todo not found.", status.HTTP_404_NOT_FOUND)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
