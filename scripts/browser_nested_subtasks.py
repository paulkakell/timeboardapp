"""Nested-task browser regression using only the smoke test's disposable data."""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Page


def exercise_nested_subtasks(page: Page) -> None:
    evidence = Path('audit-evidence')
    evidence.mkdir(exist_ok=True)
    page.set_viewport_size({'width': 1280, 'height': 900})
    page.goto('/tasks/new')
    page.locator('input[name=name]').fill('Nested browser root')
    page.locator('input[name=task_type]').fill('Nested regression')
    page.get_by_role('button', name='Save', exact=True).click()
    page.get_by_role('link', name='Nested browser root', exact=True).click()
    match = re.search(r'/tasks/(\d+)/edit', page.url)
    assert match, page.url
    root_id = int(match[1])
    root_url = f'/tasks/{root_id}/edit?next=/dashboard'

    def add(name: str, parent_id: int = root_id) -> int:
        page.goto(root_url)
        if parent_id == root_id:
            page.get_by_role('link', name='Add subtask', exact=True).first.click()
        else:
            page.locator(f'[data-subtask-id="{parent_id}"]').get_by_role(
                'link', name='Add subtask', exact=True
            ).click()
        assert int(page.locator('input[name=parent_task_id]').input_value()) == parent_id
        page.locator('input[name=name]').fill(name)
        page.locator('input[name=task_type]').fill('Nested regression')
        page.get_by_role('button', name='Save', exact=True).click()
        page.wait_for_url(re.compile(rf'/tasks/{root_id}/edit(?:\?|$)'))
        row = page.locator('[data-subtask-id]').filter(
            has=page.get_by_role('link', name=name, exact=True)
        )
        row.wait_for()
        return int(row.get_attribute('data-subtask-id'))

    a = add('Branch A')
    b = add('Branch B')
    aa = add('A child', a)
    bb = add('B child', b)
    aaa = add('A grandchild', aa)
    expected = [str(a), str(aa), str(aaa), str(b), str(bb)]

    def check_order() -> None:
        assert page.locator('[data-subtask-id]').evaluate_all(
            '(rows) => rows.map(row => row.dataset.subtaskId)'
        ) == expected
        assert page.locator('[data-subtask-id]').evaluate_all(
            '(rows) => rows.map(row => row.dataset.depth)'
        ) == ['0', '1', '2', '0', '1']

    page.reload()
    check_order()
    page.get_by_role('link', name='A child', exact=True).click()
    page.locator('textarea[name=description]').fill('Saved from the nested tree')
    page.get_by_role('button', name='Save', exact=True).click()
    page.wait_for_url(re.compile(rf'/tasks/{root_id}/edit(?:\?|$)'))
    check_order()
    page.locator(f'[data-subtask-id="{bb}"]').get_by_role(
        'button', name='Complete B child', exact=True
    ).click()
    page.wait_for_url(re.compile(rf'/tasks/{root_id}/edit(?:\?|$)'))
    assert 'completed' in page.locator(f'[data-subtask-id="{bb}"]').inner_text()
    check_order()
    page.screenshot(path=str(evidence / 'nested-subtasks-desktop.png'), full_page=True)

    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto('/site/mobile?next=/dashboard')
    assert page.locator('.task-parent').filter(has_text='Nested browser root').count() == 2
    page.get_by_role('link', name='Nested browser root', exact=True).first.click()
    check_order()
    page.screenshot(path=str(evidence / 'nested-subtasks-mobile.png'), full_page=True)

    with page.expect_response(lambda response: response.url.endswith(f'/tasks/{a}/complete')) as pending:
        page.locator(f'[data-subtask-id="{a}"]').get_by_role(
            'button', name='Complete Branch A', exact=True
        ).click()
    assert pending.value.status == 409
    page.get_by_role('button', name='Close subtasks and complete', exact=True).click()
    page.wait_for_url(re.compile(rf'/tasks/{root_id}/edit(?:\?|$)'))
    for task_id in (a, aa, aaa):
        assert 'completed' in page.locator(f'[data-subtask-id="{task_id}"]').inner_text()
    check_order()
    page.goto('/site/desktop?next=/dashboard')
    page.set_viewport_size({'width': 1280, 'height': 900})
    print('PASS: nested browser creation, branch ordering, reload, edit return, statuses, mobile mode, and confirmed cascade')
