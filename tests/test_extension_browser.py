"""Run with BETTERASSPEN_EXTENSION_TESTS=1; uses Chromium and local fixtures."""
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from flask import redirect, session
import requests
from werkzeug.serving import make_server

from app import create_app
from aspen import demo_snapshot


@unittest.skipUnless(os.environ.get('BETTERASSPEN_EXTENSION_TESTS') == '1', 'Set BETTERASSPEN_EXTENSION_TESTS=1 to run the actual extension in Chromium.')
class ExtensionBrowserTests(unittest.TestCase):
    def test_google_login_connects_existing_aspen_session_automatically(self):
        self.run_flow(existing_session=True)

    def test_missing_aspen_session_opens_school_sign_in_and_connects_afterwards(self):
        self.run_flow(existing_session=False)

    def run_flow(self, existing_session):
        from playwright.sync_api import expect, sync_playwright
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(Path(directory) / 'state', {'TESTING': True, 'START_REFRESH': False, 'GOOGLE_CLIENT_ID': 'fixture', 'GOOGLE_CLIENT_SECRET': 'fixture'})
            def fixture_google_login():
                token = app.extensions['accounts'].login({'sub': 'alice', 'email': 'alice@school.example',
                                                        'name': 'Alice', 'email_verified': True})
                session.clear()
                session['login'] = token
                session.permanent = True
                return redirect('/')
            app.view_functions['google_login'] = fixture_google_login
            snapshot = demo_snapshot()
            snapshot.update(mode='live', student={'name': 'Alice', 'studentOid': 'alice-student'})
            class FixtureAspenClient:
                def __init__(self, cookies):
                    self.session = requests.Session()
                    self.session.cookies = cookies
                def api(self, path, params=None):
                    if path == '/users/current':
                        return {'personOid': 'alice-person', 'email': 'alice@school.example'}
                    return {'studentOid': 'alice-student'}
                def sync(self):
                    return snapshot
            logging.getLogger('werkzeug').setLevel(logging.ERROR)
            server = make_server('127.0.0.1', 0, app, threaded=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'http://127.0.0.1:{server.server_port}'
            extension = Path(directory) / 'extension'
            shutil.copytree(Path(__file__).resolve().parents[1] / 'extension/chromium', extension)
            manifest = json.loads((extension / 'manifest.json').read_text())
            # The fixture grants this one origin instead of showing a permission prompt.
            manifest['host_permissions'].append(origin + '/*')
            (extension / 'manifest.json').write_text(json.dumps(manifest))
            try:
                with patch('app.AspenClient', FixtureAspenClient), sync_playwright() as playwright:
                    context = playwright.chromium.launch_persistent_context(
                        str(Path(directory) / 'browser'), channel='chromium', headless=True,
                        args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
                    try:
                        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                        extension_id = worker.url.split('/')[2]
                        options = context.new_page()
                        options.goto(f'chrome-extension://{extension_id}/options.html')
                        result = options.evaluate('(origin) => chrome.runtime.sendMessage({type:"configure",origin})', origin)
                        self.assertTrue(result.get('ok'), result)
                        options.close()
                        fixture_cookies = [
                            {'name': 'JSESSIONID', 'value': 'fixture-private-session', 'domain': 'aspen.darienps.org', 'path': '/app', 'secure': True, 'httpOnly': True},
                            {'name': 'VITHAR_CSRF', 'value': 'fixture-private-csrf', 'domain': 'aspen.darienps.org', 'path': '/', 'secure': True},
                            {'name': 'google-secret', 'value': 'never-transfer', 'domain': 'accounts.google.com', 'path': '/', 'secure': True},
                        ]
                        context.route('https://aspen.darienps.org/**', lambda route: route.fulfill(status=200, body='<h1>School sign-in fixture</h1>'))
                        if existing_session:
                            context.add_cookies(fixture_cookies)
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(origin)
                        page.get_by_role('button', name='Open Aspen connection settings').click()
                        self.assertEqual(page.locator('#cookie-text').count(), 0)
                        if existing_session:
                            page.get_by_role('link', name='Sign in with Google', exact=True).click()
                        else:
                            with context.expect_page(timeout=10000) as opened:
                                page.get_by_role('link', name='Sign in with Google', exact=True).click()
                            aspen = opened.value
                            aspen.wait_for_load_state()
                            self.assertTrue(aspen.url.startswith('https://aspen.darienps.org/aspen-login/'))
                            context.add_cookies(fixture_cookies)
                        page.locator('#data-label').get_by_text('Connected', exact=True).wait_for(timeout=15000)
                        self.assertTrue(page.evaluate('currentState.signedIn'))
                        self.assertEqual(page.evaluate('currentState.snapshot.student.name'), 'Alice')
                        self.assertEqual(errors, [])
                        store = app.extensions['account_store']('alice')
                        self.assertTrue(store.session_path.exists())
                        self.assertEqual({c.name for c in store.client.session.cookies}, {'JSESSIONID', 'VITHAR_CSRF'})
                        page.get_by_role('button', name='Open Aspen connection settings').click()
                        with page.expect_navigation(wait_until='load'):
                            page.get_by_role('button', name='Sign out of Google').click()
                        expect(page.locator('#google-sign-in')).to_have_text('Sign in with Google')
                        self.assertFalse(page.request.get(origin + '/api/state').json()['signedIn'])
                        self.assertTrue(page.locator('#dashboard').is_hidden())
                    finally:
                        context.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                app.extensions['refresh_runtime'].stop()


if __name__ == '__main__':
    unittest.main()
