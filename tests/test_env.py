import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app


class EnvironmentTests(unittest.TestCase):
    def test_dotenv_loads_automatically_without_overriding_exported_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text(
                'BETTERASSPEN_PUBLIC_URL=https://aspen.jonyserver.ddnsfree.com\n'
                'GOOGLE_CLIENT_ID=fixture-client\n'
                'GOOGLE_CLIENT_SECRET=fixture$literal-secret\n')
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(('GOOGLE_', 'BETTERASSPEN_'))}
            environment['GOOGLE_CLIENT_SECRET'] = 'exported-fixture-secret'
            with patch.dict(os.environ, environment, clear=True), patch('app.ROOT', root):
                app = create_app(root / 'state', {'START_REFRESH': False})
                self.assertEqual(app.config['PUBLIC_URL'], 'https://aspen.jonyserver.ddnsfree.com')
                self.assertEqual(app.config['GOOGLE_CLIENT_ID'], 'fixture-client')
                self.assertEqual(app.config['GOOGLE_CLIENT_SECRET'], 'exported-fixture-secret')
                self.assertTrue(app.config['SESSION_COOKIE_SECURE'])
                app.extensions['refresh_runtime'].stop()

    def test_dotenv_secret_values_are_not_interpolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('GOOGLE_CLIENT_SECRET=fixture-${MISSING_VALUE}\n')
            environment = {key: value for key, value in os.environ.items()
                           if not key.startswith(('GOOGLE_', 'BETTERASSPEN_'))}
            with patch.dict(os.environ, environment, clear=True), patch('app.ROOT', root):
                app = create_app(root / 'state', {'START_REFRESH': False})
                self.assertEqual(app.config['GOOGLE_CLIENT_SECRET'], 'fixture-${MISSING_VALUE}')
                app.extensions['refresh_runtime'].stop()


if __name__ == '__main__':
    unittest.main()
