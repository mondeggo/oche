import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import autodarts


class AutodartsConfigTests(unittest.TestCase):
    def test_boolean_selects_host_or_separate_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            host = Path(folder) / 'host'
            local = Path(folder) / 'local'
            host.mkdir()
            config = host / 'config.toml'
            config.write_text('existing configuration')
            for setting, expected in ((None, host), ('true', host), ('false', local)):
                with self.subTest(setting=setting):
                    link = MagicMock()
                    link.is_symlink.return_value = True
                    link.readlink.return_value = Path('/previous')
                    env = {} if setting is None else {'OCHE_REUSE_AUTODARTS_CONFIG': setting}
                    with patch.dict(os.environ, env, clear=True), \
                         patch.object(autodarts, 'HOST_CONFIG_DIR', host), \
                         patch.object(autodarts, 'AUTODARTS_DIR', local), \
                         patch.object(autodarts, 'CONFIG_LINK', link):
                        autodarts.configure_config_source()
                    link.symlink_to.assert_called_once_with(expected, target_is_directory=True)
                    self.assertEqual(config.read_text(), 'existing configuration')

    def test_missing_host_config_uses_local_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            link = MagicMock()
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(autodarts, 'HOST_CONFIG_DIR', Path(folder)), \
                 patch.object(autodarts, 'CONFIG_LINK', link):
                autodarts.configure_config_source()
            link.symlink_to.assert_called_once_with(autodarts.AUTODARTS_DIR, target_is_directory=True)

    def test_invalid_flag_fails_without_changing_config(self):
        with patch.dict(os.environ, {'OCHE_REUSE_AUTODARTS_CONFIG': 'invalid'}), \
             patch.object(autodarts, 'CONFIG_LINK') as link:
            with self.assertRaisesRegex(RuntimeError, 'must be true or false'):
                autodarts.configure_config_source()
            link.unlink.assert_not_called()

    def test_native_configuration_directory_is_untouched(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(autodarts, 'CONFIG_LINK') as link:
            link.is_symlink.return_value = False
            autodarts.configure_config_source()
            link.unlink.assert_not_called()
            link.symlink_to.assert_not_called()
