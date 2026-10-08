"""Non-replaceable container entry point with self-update health rollback."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

import oche_runtime as store


def main():
    store.initialize()
    stopping = False
    child = None

    def stop(signum, frame):
        nonlocal stopping
        stopping = True
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        source = store.location("oche", "/app")
        env = {**os.environ, "OCHE_LAUNCHER": "1", "OCHE_DATA_DIR": str(store.DATA),
               "PYTHONPATH": "/app", "OCHE_BOOT_ID": os.urandom(16).hex()}
        child = subprocess.Popen([sys.executable, "-m", "app.server"], cwd=source, env=env,
                                 start_new_session=os.name == "posix")
        deadline = time.monotonic() + 90
        healthy = False
        while child.poll() is None and not stopping:
            state = store.read_state("oche")
            request = store.DATA / "state" / "restart-oche"
            if request.exists():
                request.unlink()
                child.terminate()
                break
            if not healthy:
                try:
                    import json
                    with urlopen(f"http://127.0.0.1:{os.environ.get('OCHE_PORT', '8180')}/healthz", timeout=2) as response:
                        healthy = json.load(response).get("boot_id") == env["OCHE_BOOT_ID"]
                except Exception:
                    pass
                if healthy and state.get("pending"):
                    state.update(pending=False, error=None)
                    store.write_state("oche", state)
                elif time.monotonic() > deadline:
                    child.terminate()
                    break
            time.sleep(1)
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        if os.name == "posix":
            # A crashed/killed supervisor cannot clean up its module children.
            # Reap its private process group before starting a replacement.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        state = store.read_state("oche")
        # A restart request starts the candidate on the next loop; failure of
        # that candidate restores the last healthy release.
        if state.get("pending") and source == store.location("oche", "/app"):
            state.update(active=state.get("previous"), pending=False,
                         error="Oche failed its startup health check; restored previous version")
            store.write_state("oche", state)
        if not stopping:
            time.sleep(1)


if __name__ == "__main__":
    main()
