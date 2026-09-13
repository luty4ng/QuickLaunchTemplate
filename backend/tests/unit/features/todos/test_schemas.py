"""Example business: the todo request contracts.

Same story as the rest of `tests/*/features/`: this is the part that migrates.
"""

from __future__ import annotations

import pytest
from app.features.todos.schemas import TodoCreate, TodoUpdate
from pydantic import ValidationError

pytestmark = pytest.mark.unit


class TestTodoSchemas:
    @pytest.mark.parametrize("title", ["", " ", "x" * 201])
    def test_bad_titles_are_rejected(self, title: str) -> None:
        with pytest.raises(ValidationError):
            TodoCreate(title=title)

    def test_title_bounds_are_inclusive(self) -> None:
        assert TodoCreate(title="x").title == "x"
        assert len(TodoCreate(title="x" * 200).title) == 200

    def test_empty_update_is_representable_but_flagged_by_the_route(self) -> None:
        assert TodoUpdate().model_dump(exclude_unset=True) == {}

    def test_partial_update_keeps_only_provided_fields(self) -> None:
        assert TodoUpdate(done=True).model_dump(exclude_unset=True) == {"done": True}
