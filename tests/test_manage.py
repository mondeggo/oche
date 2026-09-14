import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash")


@unittest.skipUnless(BASH and Path(BASH).exists(), "Bash is required")
class ManageTests(unittest.TestCase):
    def run_helper(self, action, *, local=True, pull=True, sources=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copyfile(ROOT / 'oche.sh', root / 'oche.sh')
            (root / 'docker-compose.override.yml').touch()
            if sources:
                (root / 'Dockerfile').touch()
                (root / 'requirements.txt').touch()
                (root / 'app').mkdir()
            mock = '''
docker() {
    printf '%s\\n' "$*" >> calls
    case "$1 ${2:-}" in
        'image inspect') return LOCAL_RESULT ;;
        'pull registry.example/oche:custom') return PULL_RESULT ;;
        compose*)
            if [[ " $* " == *' config --images oche '* ]]; then
                echo registry.example/oche:custom
            fi ;;
    esac
    return 0
}
source ./oche.sh "$1"
'''.replace('LOCAL_RESULT', '0' if local else '1').replace('PULL_RESULT', '0' if pull else '1')
            result = subprocess.run([BASH, '-c', mock, 'test', action], cwd=root, capture_output=True)
            calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
            return result, calls

    def test_start_and_restart_pull_before_starting_even_with_local_image(self):
        for action in ('start', 'restart'):
            with self.subTest(action=action):
                result, calls = self.run_helper(action)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn('docker-compose.override.yml', calls)
                self.assertIn('pull registry.example/oche:custom', calls)
                self.assertIn('up -d --no-build --pull never', calls)
                self.assertLess(calls.index('pull registry.example/oche:custom'), calls.index('up -d'))

    def test_start_and_restart_use_local_image_when_registry_unavailable(self):
        for action in ('start', 'restart'):
            with self.subTest(action=action):
                result, calls = self.run_helper(action, pull=False)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn('image inspect registry.example/oche:custom', calls)
                self.assertIn('up -d', calls)
                self.assertNotIn('\nbuild ', calls)

    def test_failed_pull_without_image_or_sources_reports_error(self):
        result, calls = self.run_helper('pull', local=False, pull=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('source checkout', result.stderr.decode())
        self.assertNotIn('\nbuild ', calls)

    def test_failed_pull_preserves_local_image(self):
        result, calls = self.run_helper('pull', pull=False)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('up -d', calls)

    def test_missing_image_falls_back_to_source_build(self):
        result, calls = self.run_helper('restart', local=False, pull=False, sources=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn('build -t registry.example/oche:custom .', calls)
        self.assertIn('--force-recreate oche', calls)

    def test_stop_keeps_volumes_and_push_uses_configured_image(self):
        result, calls = self.run_helper('stop')
        self.assertEqual(result.returncode, 0)
        self.assertIn(' down\n', calls)
        self.assertNotIn('--volumes', calls)
        result, calls = self.run_helper('push')
        self.assertEqual(result.returncode, 0)
        self.assertIn('push registry.example/oche:custom', calls)

    def test_invalid_action_does_not_call_docker(self):
        result, calls = self.run_helper('unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, '')
