from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_max_rebuild_local_appdata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep backend tests independent from the Owner's persisted local settings."""
    monkeypatch.setenv(
        "LOCALAPPDATA",
        str(tmp_path / "LocalAppData"),
    )
