"""Real Chromium smoke test against an isolated, disposable application instance."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='timeboard-browser-') as raw:
    work = Path(raw)
    config = work/'settings.yml'
    config.write_text(f'app:\n  host: 127.0.0.1\n  port: 8765\n  timezone: UTC\ndatabase:\n  path: {work / "browser.db"}\n')
    env = dict(os.environ, TIMEBOARDAPP_SETTINGS=str(config), TIMEBOARDAPP_BASE_URL='', PORT='8765')
    # Local logs are not artifacts: they could contain generated demo information.
    with (work/'server.log').open('w') as logs:
        process = subprocess.Popen(['python','-m','app.run'],cwd=ROOT,env=env,stdout=logs,stderr=subprocess.STDOUT)
        try:
            ready = False
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Isolated browser-test server exited before readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=1) as response:
                        ready = response.status == 200
                    if ready: break
                except OSError:
                    time.sleep(.2)
            assert ready, 'Server failed to become ready'
            password = (work/'initial-admin-password.txt').read_text().strip()
            with sync_playwright() as engine:
                browser = engine.chromium.launch()
                context = browser.new_context(base_url='http://127.0.0.1:8765')
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('/login')
                page.locator('input[name=username]').fill('admin')
                page.locator('input[name=password]').fill(password)
                page.get_by_role('button',name='Sign in').click()
                page.wait_for_url('**/dashboard')
                for path in ['/dashboard','/calendar','/archived','/profile','/profile/notifications','/admin/users','/admin/database','/admin/email','/admin/notifications','/admin/logs','/admin/validation','/help']:
                    result = page.goto(path)
                    assert result and result.status == 200, path
                    assert page.locator('meta[name=csrf-token]').get_attribute('content'), path
                page.goto('/tasks/new')
                page.locator('input[name=name]').fill('Browser smoke <task>')
                page.locator('input[name=task_type]').fill('Regression')
                page.get_by_role('button',name='Save',exact=True).click()
                assert 'Browser smoke <task>' in page.locator('body').inner_text()
                page.goto('/calendar')
                page.locator('.fc-view-harness').wait_for(state='visible')
                for name in ('week','day','month'):
                    page.locator(f'.fc-{ {"week":"timeGridWeek","day":"timeGridDay","month":"dayGridMonth"}[name] }-button').click()
                with page.expect_response(lambda response: '/ui/prefs/calendar' in response.url and response.request.method == 'POST') as saved:
                    page.locator('#tb-cal-completed').check()
                assert saved.value.status == 200, 'CSRF-protected calendar preferences did not save'
                page.reload()
                assert page.locator('#tb-cal-completed').is_checked()
                page.set_viewport_size({'width':390,'height':844})
                page.goto('/dashboard')
                assert page.locator('body').is_visible()
                page.set_viewport_size({'width':1280,'height':900})
                page.locator('form[action="/logout"] button').click()
                page.wait_for_url('**/login')
                assert not errors, '\n'.join(errors)
                browser.close()
            print('PASS: Chromium login, 12 authenticated pages including help, task creation, calendar views/preferences, mobile viewport, and CSRF-protected logout')
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
