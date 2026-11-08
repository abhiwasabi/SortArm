import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.envfile import load_local_env


class EnvFileTests(unittest.TestCase):
    def test_fills_unset_values_and_keeps_the_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text("export TIGER_DATABASE_URL='from-file'\n# comment\nGEMINI_MODEL=file-model\n")
            with patch.dict(os.environ, {'GEMINI_MODEL': 'shell-model'}, clear=True):
                load_local_env(path)
                self.assertEqual(os.environ['TIGER_DATABASE_URL'], 'from-file')
                self.assertEqual(os.environ['GEMINI_MODEL'], 'shell-model')

    def test_missing_file_is_ignored(self):
        with patch.dict(os.environ, {}, clear=True):
            load_local_env(Path('/tmp/sharkbyte-missing-env-file'))
            self.assertNotIn('TIGER_DATABASE_URL', os.environ)
