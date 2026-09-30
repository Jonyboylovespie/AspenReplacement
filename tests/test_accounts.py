import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from joserfc import jwt
from joserfc.jwk import RSAKey
import requests

from app import create_app
from aspen import AspenError, demo_snapshot, parse_cookies
from runtime import RefreshRuntime

COOKIES = json.dumps([
    {"name": "JSESSIONID", "value": "private-aspen-session", "domain": "aspen.darienps.org", "path": "/app"},
    {"name": "VITHAR_CSRF", "value": "private-aspen-csrf", "domain": "aspen.darienps.org", "path": "/"},
])


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "START_REFRESH": False,
                       "GOOGLE_CLIENT_ID": "fixture-client", "GOOGLE_CLIENT_SECRET": "fixture-secret"}
        self.app = create_app(self.directory.name, self.config)
        self.accounts = self.app.extensions["accounts"]

    def tearDown(self):
        self.app.extensions["refresh_runtime"].stop()
        self.directory.cleanup()

    def login(self, subject="alice"):
        client = self.app.test_client()
        token = self.accounts.login({"sub": subject, "email": subject + "@school.example",
                                     "email_verified": True, "name": subject})
        with client.session_transaction() as state:
            state["login"] = token
            state.permanent = True
        store = self.app.extensions["account_store"](subject)
        return client, store, {"X-CSRF-Token": store.csrf}

    def aspen_client(self, email="alice@school.example", student="alice-student"):
        client = Mock()
        client.session = requests.Session()
        client.session.cookies = parse_cookies(COOKIES)
        client.api.side_effect = [{"personOid": "person", "email": email}, {"studentOid": student}]
        return client

    def test_anonymous_requests_never_reveal_cached_grades(self):
        alice, store, _ = self.login()
        store.save({"student": {"studentOid": "alice"}, "classes": ["alice-private-grades"]})
        anonymous = self.app.test_client()
        state = anonymous.get('/api/state', base_url='http://192.168.1.50:5173').json
        self.assertFalse(state['signedIn'])
        self.assertIsNone(state['snapshot'])
        self.assertNotIn('csrfToken', state)
        for route in ['/api/session', '/api/refresh', '/api/clear', '/api/demo', '/api/sign-in', '/auth/logout']:
            self.assertEqual(anonymous.post(route).status_code, 401)
        self.assertIn('alice-private-grades', alice.get('/api/state').json['snapshot']['classes'])

    def test_accounts_cannot_read_or_modify_each_others_store(self):
        alice, first, first_headers = self.login('alice')
        bob, second, second_headers = self.login('bob')
        first.save({'student': {'studentOid': 'alice'}, 'classes': ['alice-grades']})
        second.save({'student': {'studentOid': 'bob'}, 'classes': ['bob-grades']})
        self.assertEqual(alice.get('/api/state').json['snapshot']['classes'], ['alice-grades'])
        self.assertEqual(bob.get('/api/state').json['snapshot']['classes'], ['bob-grades'])
        self.assertEqual(bob.post('/api/clear', headers=first_headers).status_code, 403)
        self.assertEqual(alice.post('/api/clear', headers=first_headers).status_code, 200)
        self.assertEqual(bob.get('/api/state').json['snapshot']['classes'], ['bob-grades'])
        self.assertNotEqual(first.directory, second.directory)

    def test_authentication_is_required_before_getting_csrf_token(self):
        client, _, headers = self.login()
        self.assertEqual(client.post('/api/demo').status_code, 403)
        self.assertEqual(client.post('/api/demo', headers={**headers, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(client.post('/api/demo', headers=headers, base_url='http://localhost').status_code, 200)

    def test_logout_revokes_a_copied_browser_session(self):
        client, _, headers = self.login()
        cookie = client.get_cookie('betterasspen_session').value
        self.assertEqual(client.post('/auth/logout', headers=headers).status_code, 200)
        replay = self.app.test_client()
        replay.set_cookie('betterasspen_session', cookie)
        self.assertFalse(replay.get('/api/state').json['signedIn'])

    def test_unverified_google_email_cannot_create_account(self):
        for verified in (False, 'true', None):
            with self.assertRaises(ValueError):
                self.accounts.login({'sub': 'fake', 'email': 'fake@school.example', 'email_verified': verified})

    def test_expired_session_cannot_access_grades(self):
        client, _, _ = self.login()
        from contextlib import closing
        with closing(self.accounts.connect()) as db, db:
            db.execute('UPDATE sessions SET expires=1')
        self.assertFalse(client.get('/api/state').json['signedIn'])
        self.assertEqual(client.post('/api/refresh').status_code, 401)

    def test_extension_connection_requires_current_attempt_and_matching_identity(self):
        client, store, headers = self.login()
        self.assertEqual(client.post('/api/session', json={'cookies': COOKIES}, headers=headers).status_code, 409)
        state = client.post('/api/sign-in', headers=headers).json
        body = {'cookies': COOKIES, 'connectionAttempt': state['signIn']['attempt']}
        with patch('app.AspenClient', return_value=self.aspen_client(email='bob@school.example')):
            self.assertEqual(client.post('/api/session', json=body, headers=headers).status_code, 401)
        self.assertIsNone(store.client)
        self.assertFalse(store.session_path.exists())
        with patch('app.AspenClient', return_value=self.aspen_client()), patch.object(store, 'start_refresh'):
            self.assertEqual(client.post('/api/session', json=body, headers=headers).status_code, 202)
        self.assertEqual(client.get('/api/state').json['signIn']['phase'], 'connected')
        self.assertEqual(client.post('/api/session', json=body, headers=headers).status_code, 409)
        self.assertNotIn('private-aspen-session', json.dumps(client.get('/api/state').json))

    def test_absent_aspen_identity_fails_closed(self):
        _, store, _ = self.login()
        fixture = self.aspen_client(email=None)
        with patch('app.AspenClient', return_value=fixture), self.assertRaises(AspenError):
            store.verify_session(COOKIES)

    def test_student_binding_cannot_be_taken_by_another_google_account(self):
        self.login('alice')
        self.login('bob')
        self.accounts.claim_student('alice', 'student-one')
        with self.assertRaises(ValueError):
            self.accounts.claim_student('bob', 'student-one')
        with self.assertRaises(ValueError):
            self.accounts.claim_student('alice', 'student-two')

    def test_sync_cannot_publish_another_students_records(self):
        client, store, _ = self.login()
        self.accounts.claim_student('alice', 'alice-student')
        saved = {'mode': 'live', 'student': {'studentOid': 'alice-student'}, 'classes': ['alice-grades']}
        store.save(saved)
        fixture = self.aspen_client()
        fixture.sync.return_value = {'mode': 'live', 'student': {'studentOid': 'bob-student'}, 'classes': ['bob-grades']}
        store.client = fixture
        store.needs_auth = False
        store.refresh()
        self.assertEqual(client.get('/api/state').json['snapshot'], saved)
        self.assertTrue(store.needs_auth)
        self.assertFalse(store.session_path.exists())

    def test_cancel_during_verification_cannot_save_a_session(self):
        client, store, headers = self.login()
        attempt = client.post('/api/sign-in', headers=headers).json['signIn']['attempt']
        entered = threading.Event()
        finish = threading.Event()
        fixture = self.aspen_client()
        def verify(raw):
            entered.set()
            self.assertTrue(finish.wait(2))
            return fixture, {'studentOid': 'alice-student'}
        result = []
        def connect():
            worker_client = self.app.test_client()
            worker_client.set_cookie('betterasspen_session', client.get_cookie('betterasspen_session').value)
            result.append(worker_client.post('/api/session', json={'cookies': COOKIES, 'connectionAttempt': attempt}, headers=headers).status_code)
        with patch.object(store, 'verify_session', side_effect=verify):
            thread = threading.Thread(target=connect)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(client.post('/api/sign-in/cancel', headers=headers).status_code, 200)
            finally:
                finish.set()
                thread.join(timeout=3)
        self.assertEqual(result, [409])
        self.assertIsNone(store.client)
        self.assertFalse(store.session_path.exists())

    def test_encrypted_aspen_session_and_google_login_survive_restart(self):
        client, store, headers = self.login()
        state = client.post('/api/sign-in', headers=headers).json
        with patch('app.AspenClient', return_value=self.aspen_client()), patch.object(store, 'start_refresh'):
            response = client.post('/api/session', json={'cookies': COOKIES, 'connectionAttempt': state['signIn']['attempt']}, headers=headers)
        self.assertEqual(response.status_code, 202)
        saved = demo_snapshot()
        saved.update(mode='live', student={'studentOid': 'alice-student', 'name': 'alice'})
        store.save(saved)
        self.assertNotIn(b'private-aspen-session', store.session_path.read_bytes())
        self.assertEqual(store.session_path.stat().st_mode & 0o777, 0o600)
        restarted = create_app(self.directory.name, self.config)
        try:
            fresh_client = restarted.test_client()
            fresh_client.set_cookie('betterasspen_session', client.get_cookie('betterasspen_session').value)
            self.assertTrue(fresh_client.get('/api/state').json['signedIn'])
            fresh_store = restarted.extensions['account_store']('alice')
            with patch('app.AspenClient', return_value=self.aspen_client()):
                self.assertTrue(fresh_store.resume_session(refresh=False))
            self.assertTrue(fresh_client.get('/api/state').json['connected'])
            self.assertEqual(fresh_store.snapshot, saved)
            token = fresh_store.csrf
            self.assertEqual(fresh_client.post('/api/disconnect', headers={'X-CSRF-Token': token}).status_code, 200)
            self.assertFalse(fresh_store.session_path.exists())
        finally:
            restarted.extensions['refresh_runtime'].stop()

    def test_refresh_coordinator_is_started_in_factory_and_handles_each_account(self):
        runtime = RefreshRuntime(300, auto_resume=False)
        try:
            _, alice, _ = self.login('alice')
            _, bob, _ = self.login('bob')
            alice.client = bob.client = object()
            alice.needs_auth = bob.needs_auth = False
            runtime.stores = {'alice': alice, 'bob': bob}
            with patch.object(alice, 'start_refresh') as first, patch.object(bob, 'start_refresh') as second:
                runtime.tick()
                first.assert_called_once()
                second.assert_called_once()
            with patch('app.RefreshRuntime.start') as start:
                app = create_app(self.directory.name, {**self.config, 'START_REFRESH': True})
                start.assert_called_once()
                app.extensions['refresh_runtime'].stop()
        finally:
            runtime.stop()


class GoogleOIDCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = RSAKey.generate_key(2048)
        cls.key_dict = cls.key.as_dict(is_private=False)
        cls.key_dict['kid'] = 'fixture'

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app(self.directory.name, {'TESTING': True, 'START_REFRESH': False,
                                'GOOGLE_CLIENT_ID': 'fixture-client', 'GOOGLE_CLIENT_SECRET': 'fixture-secret'})
        self.google = self.app.extensions['authlib.integrations.flask_client'].create_client('google')
        self.metadata = {'issuer': 'https://accounts.google.com',
                         'authorization_endpoint': 'https://accounts.google.com/o/oauth2/v2/auth',
                         'token_endpoint': 'https://oauth2.googleapis.com/token',
                         'jwks_uri': 'https://www.googleapis.com/oauth2/v3/certs',
                         'id_token_signing_alg_values_supported': ['RS256']}
        self.metadata_patch = patch.object(self.google, 'load_server_metadata', return_value=self.metadata)
        self.metadata_patch.start()
        self.keys_patch = patch.object(self.google, 'fetch_jwk_set', return_value={'keys': [self.key_dict]})
        self.keys_patch.start()

    def tearDown(self):
        self.keys_patch.stop()
        self.metadata_patch.stop()
        self.directory.cleanup()

    def flow(self, overrides=None):
        client = self.app.test_client()
        response = client.get('/auth/google')
        params = parse_qs(urlsplit(response.location).query)
        self.assertEqual(params['code_challenge_method'], ['S256'])
        now = int(time.time())
        claims = {'iss': self.metadata['issuer'], 'aud': 'fixture-client', 'sub': 'verified-user',
                  'iat': now, 'exp': now + 600, 'nonce': params['nonce'][0],
                  'email': 'verified@school.example', 'email_verified': True}
        claims.update(overrides or {})
        encoded = jwt.encode({'alg': 'RS256', 'kid': 'fixture'}, claims, self.key)
        with patch.object(self.google, 'fetch_access_token', return_value={'id_token': encoded, 'access_token': 'fixture-token', 'token_type': 'Bearer'}):
            response = client.get('/auth/google/callback', query_string={'code': 'fixture-code', 'state': params['state'][0]})
        return client, response

    def test_valid_signed_google_token_creates_a_private_account(self):
        client, response = self.flow()
        self.assertEqual(response.location, '/')
        state = client.get('/api/state').json
        self.assertTrue(state['signedIn'])
        self.assertEqual(state['account']['email'], 'verified@school.example')
        self.assertIsNone(state['snapshot'])

    def test_wrong_nonce_issuer_audience_and_expiry_are_rejected(self):
        for claims in [{'nonce': 'wrong'}, {'iss': 'https://evil.example'}, {'aud': 'other-client'},
                       {'exp': int(time.time()) - 600}, {'email_verified': False}]:
            with self.subTest(claims=claims):
                client, response = self.flow(claims)
                self.assertEqual(response.location, '/?login=failed')
                self.assertFalse(client.get('/api/state').json['signedIn'])

    def test_unsolicited_oauth_callback_is_rejected_without_exchanging_code(self):
        with patch.object(self.google, 'fetch_access_token') as exchange:
            response = self.app.test_client().get('/auth/google/callback?code=fake&state=fake')
            self.assertEqual(response.location, '/?login=failed')
            exchange.assert_not_called()


if __name__ == '__main__':
    unittest.main()
