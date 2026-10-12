"""Runs the dashboard's Node tests (dashboard/tests/*.test.js, node --test) when Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node") or shutil.which("node.exe")
TESTS = sorted((Path(__file__).resolve().parents[1] / "dashboard" / "tests").glob("*.test.js"))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_dashboard_data_layer():
    assert TESTS, "no dashboard tests found"
    files = [str(p) for p in TESTS]
    if NODE.startswith("/mnt/"):  # WSL calling the Windows Node: give it Windows paths
        files = [
            subprocess.run(["wslpath", "-w", f], capture_output=True, text=True).stdout.strip() for f in files
        ]
    r = subprocess.run([NODE, "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
