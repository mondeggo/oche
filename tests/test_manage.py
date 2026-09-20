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
    def run_helper(self, action, *, local=True, pull=True, sources=False, camera_setup=None, checkout=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'scripts').mkdir()
            helper_path = 'scripts/oche.sh' if checkout else 'oche.sh'
            shutil.copyfile(ROOT / 'scripts/oche.sh', root / helper_path)
            (root / 'docker-compose.yml').touch()
            (root / 'docker-compose.override.yml').touch()
            if camera_setup is not None:
                (root / 'scripts/install.sh').write_text(camera_setup, newline='\n')
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
source "$2" "$1"
'''.replace('LOCAL_RESULT', '0' if local else '1').replace('PULL_RESULT', '0' if pull else '1')
            result = subprocess.run([BASH, '-c', mock, 'test', action, str(root / helper_path)], cwd=root / 'scripts', capture_output=True)
            calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
            return result, calls

    def test_checkout_helper_resolves_compose_and_camera_setup(self):
        result, calls = self.run_helper('cameras', checkout=True, camera_setup='''
reconfigure_cameras() { echo camera-setup >> calls; }
''')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn('camera-setup', calls)
        self.assertIn('docker-compose.override.yml', calls)
        self.assertIn('up -d', calls)

    def test_start_and_restart_pull_before_starting_even_with_local_image(self):
        for action in ('start', 'restart', 'update'):
            with self.subTest(action=action):
                result, calls = self.run_helper(action)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn('docker-compose.override.yml', calls)
                self.assertIn('pull registry.example/oche:custom', calls)
                self.assertIn('up -d --no-build --pull never', calls)
                self.assertLess(calls.index('pull registry.example/oche:custom'), calls.index('up -d'))

    def test_start_and_restart_use_local_image_when_registry_unavailable(self):
        for action in ('start', 'restart', 'update'):
            with self.subTest(action=action):
                result, calls = self.run_helper(action, pull=False)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertIn('image inspect registry.example/oche:custom', calls)
                self.assertIn('up -d', calls)
                self.assertNotIn('\nbuild ', calls)

    def test_failed_pull_without_image_reports_error(self):
        result, calls = self.run_helper('pull', local=False, pull=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No local image available', result.stderr.decode())
        self.assertNotIn('\nbuild ', calls)

    def test_failed_pull_preserves_local_image(self):
        result, calls = self.run_helper('pull', pull=False)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('up -d', calls)

    def test_missing_image_never_builds_even_with_sources(self):
        result, calls = self.run_helper('restart', local=False, pull=False, sources=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('build -t', calls)
        self.assertNotIn('up -d', calls)

    def test_stop_keeps_volumes(self):
        result, calls = self.run_helper('stop')
        self.assertEqual(result.returncode, 0)
        self.assertIn(' down\n', calls)
        self.assertNotIn('--volumes', calls)

    def test_invalid_action_does_not_call_docker(self):
        for action in ('unknown', 'build', 'push'):
            result, calls = self.run_helper(action)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, '')

    def test_help_explains_commands_without_calling_docker(self):
        for action in ('-h', '--help'):
            result, calls = self.run_helper(action)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(calls, '')
            for command in ('start', 'stop', 'restart', 'update', 'pull', 'cameras'):
                self.assertIn(command, result.stdout.decode())
            self.assertNotIn('build', result.stdout.decode())
            self.assertNotIn('push', result.stdout.decode())

    def test_update_recreates_container(self):
        result, calls = self.run_helper('update')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn('--force-recreate oche', calls)

    def test_camera_setup_applies_without_pulling(self):
        result, calls = self.run_helper('cameras', camera_setup='''
reconfigure_cameras() { echo camera-setup >> calls; }
''')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn('camera-setup', calls)
        self.assertIn('config --quiet', calls)
        self.assertIn('up -d --no-build --pull never --force-recreate oche', calls)
        self.assertNotIn('\npull ', calls)

    def test_camera_setup_failure_does_not_recreate_container(self):
        for setup in (None, 'reconfigure_cameras() { return 1; }\n'):
            result, calls = self.run_helper('cameras', camera_setup=setup)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('up -d', calls)
