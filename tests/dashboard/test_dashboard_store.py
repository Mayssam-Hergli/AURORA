"""Runs the dashboard's Node test (tests/dashboard/store_test.js) when Node is installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node") or shutil.which("node.exe")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_dashboard_store_logic():
    script = str(Path(__file__).with_name("store_test.js"))
    if NODE.startswith("/mnt/"):  # WSL calling the Windows Node: give it a Windows path
        script = subprocess.run(["wslpath", "-w", script], capture_output=True, text=True).stdout.strip()
    r = subprocess.run([NODE, script], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all checks passed" in r.stdout
