"""Crash recovery test. Run with: python test_crash_recovery.py

Starts its own server in a temp directory (so your real wal.log is untouched),
writes keys, kills the process hard, restarts it, and checks every write that
was acknowledged is still there. Port 8080 must be free.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import requests

BASE = "http://127.0.0.1:8080"
SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.py")


def start_server(workdir):
    proc = subprocess.Popen([sys.executable, SERVER], cwd=workdir,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            requests.get(f"{BASE}/ping", timeout=0.5)
            return proc
        except requests.ConnectionError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server did not start")


def hard_kill(proc):
    # Popen.kill() is SIGKILL on Linux/Mac and TerminateProcess on Windows.
    # Either way no cleanup code runs in the server.
    proc.kill()
    proc.wait()


def main():
    workdir = tempfile.mkdtemp()
    proc = start_server(workdir)
    try:
        acked = {}
        for i in range(200):
            r = requests.put(f"{BASE}/crash_{i}", json={"value": f"v{i}"})
            assert r.status_code == 201, r.status_code
            acked[f"crash_{i}"] = f"v{i}"
        for i in range(0, 200, 10):  # overwrite some
            requests.put(f"{BASE}/crash_{i}", json={"value": f"new{i}"})
            acked[f"crash_{i}"] = f"new{i}"
        for i in range(5, 200, 20):  # delete some
            requests.delete(f"{BASE}/crash_{i}")
            del acked[f"crash_{i}"]

        hard_kill(proc)
        proc = start_server(workdir)

        for k, v in acked.items():
            r = requests.get(f"{BASE}/{k}")
            assert r.status_code == 200 and r.json() == {"value": v}, (k, r.status_code, r.text)
        for i in range(5, 200, 20):
            assert requests.get(f"{BASE}/crash_{i}").status_code == 404, f"crash_{i} came back"
        print(f"PASS: {len(acked)} acknowledged keys survived, deleted keys stayed deleted")
    finally:
        hard_kill(proc)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
