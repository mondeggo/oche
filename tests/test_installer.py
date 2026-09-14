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
