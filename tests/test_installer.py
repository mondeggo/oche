import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
BASH = (r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else shutil.which("bash"))
SCRIPT = (ROOT / "scripts/install.sh").read_text()


@unittest.skipUnless(BASH and Path(BASH).exists(), "Bash is required")
class InstallerTests(unittest.TestCase):
    def run_bash(self, source, cwd=None, args=(), env=None):
        result = subprocess.run(
            [BASH, "-c", "set -euo pipefail\n" + source, "test", *args],
            cwd=cwd, env=env, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout.decode()

    def discover(self, folder, probe=None):
        function = SCRIPT[SCRIPT.index("discover_cameras() ("):SCRIPT.index("configure_cameras() (")]
        if probe is None:
            probe = "v4l2-ctl() { printf 'Device Caps : 0x04200001\\n'; }\nsudo_cmd=()\n"
        return self.run_bash(function + '\n' + probe + '\ndiscover_cameras dev sys\n', cwd=folder)

    def test_capture_nodes_in_numeric_order_with_kernel_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dev").mkdir()
            for node in ("video10", "video2", "video0", "video0-extra"):
                (root / "dev" / node).touch()
            for node in ("video0", "video2"):
                location = root / "sys" / node
                location.mkdir(parents=True)
                (location / "name").write_text("USB Camera\n")
            self.assertEqual(self.discover(folder).splitlines(), [
                "dev/video0\tUSB Camera", "dev/video2\tUSB Camera",
                "dev/video10\tVideo device (video10)",
            ])

    def test_no_devices_produces_no_phantom_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "dev").mkdir()
            self.assertEqual(self.discover(folder), "")

    def test_camera_pairs_shared_serial_and_pi_processors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dev").mkdir()
            for number in (0, 1, 2, 3, 4, 5, 10, 13):
                (root / "dev" / f"video{number}").touch()
            probe = '''
sudo_cmd=(as_root)
as_root() { ROOT_PROBE=1 "$@"; }
v4l2-ctl() {
    [[ "${ROOT_PROBE:-}" == 1 ]] || { echo 'Permission denied' >&2; return 1; }
    driver=uvcvideo
    case "$1" in
        *video1|*video3|*video5) caps=0x04a00000 ;;
        *video10) caps=0x04004001; driver=bcm2835-codec ;;
        *video13) caps=0x04201000; driver=bcm2835-isp ;;
        *) caps=0x04200001 ;;
    esac
    printf 'Driver name : %s\\nCard type : Autodarts DIY Cam\\nCapabilities : 0x84a00001\\nDevice Caps : %s\\nSerial : SAME_SERIAL\\n' "$driver" "$caps"
}
'''
            self.assertEqual(self.discover(folder, probe).splitlines(), [
                'dev/video0\tAutodarts DIY Cam',
                'dev/video2\tAutodarts DIY Cam',
                'dev/video4\tAutodarts DIY Cam',
            ])

    def test_failed_probe_is_not_recommended_as_a_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dev").mkdir()
            (root / "dev/video0").touch()
            self.assertEqual(self.discover(folder, 'sudo_cmd=()\nv4l2-ctl() { echo "Permission denied" >&2; return 1; }'), '')

    def test_old_driver_without_device_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "dev").mkdir()
            (root / "dev/video0").touch()
            output = self.discover(folder, "sudo_cmd=()\nv4l2-ctl() { printf 'Capabilities : 0x04000001\\n'; }")
            self.assertIn('dev/video0\t', output)

    def test_repository_default_and_overrides(self):
        assignment = next(line.strip() for line in SCRIPT.splitlines() if line.strip().startswith('repo='))
        env = dict(os.environ)
        env.pop("OCHE_REPOSITORY", None)
        source = assignment + '\nprintf "%s" "$repo"'
        self.assertEqual(self.run_bash(source, env=env), "mondeggo/oche")
        env["OCHE_REPOSITORY"] = "example/environment"
        self.assertEqual(self.run_bash(source, env=env), "example/environment")
        self.assertEqual(self.run_bash(source, env=env, args=("example/argument",)), "example/argument")

    def test_shell_syntax(self):
        result = subprocess.run([BASH, "-n", "scripts/install.sh"], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_install_mode_default_detailed_and_invalid_retry(self):
        helpers = SCRIPT[:SCRIPT.index('install_docker() (')]
        for answer, expected in (('', 'easy'), ('1', 'easy'), ('2', 'detailed'),
                                 ('wrong\\n2', 'detailed')):
            with self.subTest(answer=answer):
                output = self.run_bash(helpers +
                    f"\nchoose_install_mode <<< $'{answer}'\nprintf 'MODE=%s' \"$install_mode\"")
                self.assertTrue(output.endswith('MODE=' + expected), output)

    def test_easy_defaults_and_full_device_access_without_prompts_or_probes(self):
        helpers = SCRIPT[SCRIPT.index('autodarts_config_mount() ('):SCRIPT.index('main() (')]
        folder_block = SCRIPT[SCRIPT.index('section "Installation folder"'):
                              SCRIPT.index('mkdir -p -- "$install_dir"')]
        startup_block = SCRIPT[SCRIPT.index('section "Startup"'):
                               SCRIPT.index('section "Docker setup"')]
        with tempfile.TemporaryDirectory() as folder:
            source = helpers + '''
section() { printf '\\n%s\\n\\n' "$1"; }
install_mode=easy
install_home="$PWD"
read() {
    if [[ " $* " == *' -p '* ]]; then echo UNEXPECTED_PROMPT >&2; return 1; fi
    builtin read "$@"
}
discover_cameras() { echo UNEXPECTED_PROBE >&2; }
find() { printf '44\\n20\\n'; }
getent() { printf 'video:x:44:\\ndialout:x:20:\\n'; }
''' + folder_block + startup_block + '''
[[ "$install_dir" == "$install_home/oche" ]]
[[ "$restart_policy" == unless-stopped ]]
configure_cameras
'''
            self.run_bash(source, cwd=folder)
            override = (Path(folder) / 'docker-compose.override.yml').read_text()
            self.assertIn('"/dev:/dev"', override)
            self.assertIn("'a *:* rwm'", override)
            self.assertIn('      - "20"\n      - "44"', override)

    def test_detailed_camera_selection_still_accepts_none(self):
        helpers = SCRIPT[SCRIPT.index('autodarts_config_mount() ('):SCRIPT.index('main() (')]
        with tempfile.TemporaryDirectory() as folder:
            self.run_bash(helpers + '''
install_mode=detailed
install_home="$PWD"
discover_cameras() { :; }
configure_cameras <<< none
''', cwd=folder)
            override = (Path(folder) / 'docker-compose.override.yml').read_text()
            self.assertIn('devices: []', override)
            self.assertNotIn('device_cgroup_rules', override)

    def test_existing_autodarts_configuration_mount(self):
        function = SCRIPT[SCRIPT.index('autodarts_config_mount() ('):SCRIPT.index('configure_cameras() (')]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config_dir = root / "user's $home" / '.config' / 'autodarts'
            config_dir.mkdir(parents=True)
            config = config_dir / 'config.toml'
            config.write_text('[cam]\ncams = []\n')
            source = function + '\ninstall_home="$PWD/user\'s \\$home"\nautodarts_config_mount\n'
            output = self.run_bash(source, cwd=folder)
            self.assertIn("user''s $$home/.config/autodarts'", output)
            self.assertIn('target: /app/host-autodarts', output)
            self.assertIn('create_host_path: false', output)
            self.assertNotIn('[cam]', output)
            self.assertEqual(config.read_text(), '[cam]\ncams = []\n')

    def test_missing_autodarts_configuration_uses_data_mount(self):
        function = SCRIPT[SCRIPT.index('autodarts_config_mount() ('):SCRIPT.index('configure_cameras() (')]
        with tempfile.TemporaryDirectory() as folder:
            for directory_exists in (False, True):
                if directory_exists:
                    (Path(folder) / '.config' / 'autodarts').mkdir(parents=True)
                self.assertEqual(self.run_bash(function + '\ninstall_home="$PWD"\nautodarts_config_mount\n', cwd=folder), '')

    def test_installer_places_helper_for_local_and_remote_installs(self):
        start = SCRIPT.index('# Ship the helper')
        block = SCRIPT[start:SCRIPT.index('cd -- "$install_dir"', start)]
        for local in (True, False):
            with self.subTest(local=local), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'source').mkdir()
                (root / 'install').mkdir()
                helper = '#!/usr/bin/env bash\necho helper\n'
                (root / 'source/oche.sh').write_text(helper, newline='\n')
                setup = '''
install_dir="$PWD/install"
repo=mondeggo/oche
curl() { cp source/oche.sh "${@: -1}"; }
'''
                setup += 'source_dir="$PWD/source"\n' if local else 'source_dir=""\n'
                self.run_bash(setup + block, cwd=folder)
                self.assertEqual((root / 'install/oche.sh').read_text(), helper)

    def test_piped_entrypoint_restores_input_after_terminal_prompts(self):
        # Exercise the real entrypoint wrapper and its stdin redirect without
        # running installation. A file stands in for terminal input: the parent
        # must continue reading the pipe, never execute the terminal's contents.
        main = SCRIPT[SCRIPT.index('main() '):]
        declaration = main.splitlines()[0]
        closing_and_call = main[main.rindex('\n)'):].splitlines()[1:]
        redirect = next(line for line in main.splitlines() if line.strip() == 'exec 0<&3')
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'terminal-input').write_text('echo WRONG_INPUT\n')
            source = '\n'.join([
                declaration, 'exec 3<terminal-input', redirect,
                'echo INSTALL_DONE', *closing_and_call, 'echo PIPE_FINISHED', '',
            ])
            result = subprocess.run(
                [BASH], input=source.encode(), cwd=folder,
                capture_output=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(result.stdout.decode().splitlines(), ['INSTALL_DONE', 'PIPE_FINISHED'])
