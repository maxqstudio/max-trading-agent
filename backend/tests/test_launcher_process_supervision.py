from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
POWERSHELL_TEST = ROOT / "scripts" / "test_launcher_process_supervision.ps1"


@pytest.mark.skipif(os.name != "nt", reason="MAX launcher is Windows-only")
def test_windows_launcher_captures_early_exit_and_timeout_diagnostics() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell is not None, "Windows PowerShell is required for launcher process regression coverage"

    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(POWERSHELL_TEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, f"launcher PowerShell test failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "LAUNCHER_PROCESS_SUPERVISION=PASS" in result.stdout
    assert "EARLY_EXIT=PASS; EXIT_CODE=37; STDERR_CAPTURED=YES" in result.stdout
    assert "TIMEOUT=PASS; PROCESS_REMAINS_RUNNING_UNTIL_VERIFIED_STOP=YES" in result.stdout
