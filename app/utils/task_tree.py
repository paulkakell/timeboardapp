"""Presentation ordering for already-authorized task descendants."""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Iterable, TypedDict

if TYPE_CHECKING:
    from ..models import Task


class TaskTreeRow(TypedDict):
    task: Task
    depth: int


def build_descendant_rows(tasks: Iterable[Task], *, root_task_id: int) -> list[TaskTreeRow]:
    """Return depth-first rows, keeping each branch contiguous.

    Direct children have depth zero. Siblings retain creation/ID order. The
    iterative traversal handles deep trees without recursion and skips cycles,
    duplicate IDs, and nodes not connected to the requested root.
    """
    children: dict[int, list[Task]] = defaultdict(list)
    for task in tasks:
        if task.parent_task_id is not None:
            children[int(task.parent_task_id)].append(task)
    for siblings in children.values():
        siblings.sort(key=lambda task: int(task.id))

    root_id = int(root_task_id)
    seen = {root_id}
    stack = [(task, 0) for task in reversed(children.get(root_id, []))]
    rows: list[TaskTreeRow] = []
    while stack:
        task, depth = stack.pop()
        task_id = int(task.id)
        if task_id in seen:
            continue
        seen.add(task_id)
        rows.append({"task": task, "depth": depth})
        stack.extend((child, depth + 1) for child in reversed(children.get(task_id, [])))
    return rows
